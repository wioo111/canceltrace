# canceltrace

Offline cancellation / retry evidence checks.

**Experimental source-visible prototype; distribution license not yet selected.** Python 3.11+; standard library only. No installation, network, credentials, server, telemetry, SDK execution, or third-party dependencies. Public source visibility is not a declaration of an open-source license; see [SOURCE_NOTES.md](SOURCE_NOTES.md).

When an agent stops waiting for a tool, did the operation stop — or did its response merely disappear? `canceltrace` checks **supplied, synthetic or application-produced JSONL evidence** without executing tools. It separates:

1. a cancellation request;
2. cancellation of the local wait;
3. the observed external outcome;
4. the recorded write authorization generation.

It flags re-dispatch while an earlier write is unresolved, writes after a recorded revocation, and distinct recorded effect IDs for a single logical operation. **Missing evidence is uncertainty, not proof of a duplicate effect.** A new grant does not resolve an old attempt. Read-only status queries using the same operation key are not write retries.

## Run

```sh
python3 canceltrace.py examples/reconciled_read.jsonl
python3 canceltrace.py examples/ambiguous_retry.jsonl --format json
python3 canceltrace.py examples/duplicate_effects.jsonl
python3 canceltrace.py examples/revoked_retry.jsonl
python3 canceltrace.py examples/task_cancel_ack_unknown.jsonl --format json
python3 mcp_boundary.py tasks/get examples/mcp_task_completed_error.synthetic.json \
  --request-id-json '"synthetic-poll-1"' --task-id synthetic-task
python3 -m unittest discover -v
python3 -m unittest test_state_space -v
python3 canceltrace.py examples/receipt_before_retry.synthetic.jsonl --format json
python3 canceltrace.py examples/receipt_after_retry.synthetic.jsonl --format json
```

`-` reads stdin. Exit status: **0** = no risk findings in the supplied trace, **1** = advisory risk findings, **2** = invalid / unreadable data. Exit 0 does **not** mean the operation is safe, fully logged, authorized, or complete. JSON output includes each attempt's outcome, cancellation flags and evidence IDs, plus an explicit `untracked` authorization state when grants were not logged.

The `reconciled_read` example records a revoked write whose local wait was cancelled. A separate read observes the original write's receipt; there is no second write. The ambiguous example contains no receipts and therefore cannot establish duplicate effects. The duplicate example supplies two distinct effect IDs; this is stronger recorded evidence but is still not independently verified by the tool.

## Trace contract (version 2)

Version 2 retains all version 1 input events and adds passive protocol observations. JSON reports now declare `schema_version: 2` and add a `protocol_observations` list per attempt. Consumers that check the output version must opt in to the new shape.

One JSON object per line, in **observation order**. Blank lines are allowed. All events require `event_id`, `operation_key`, and `type`.

| Type | Additional fields | Meaning |
| --- | --- | --- |
| `authorization_granted` | `epoch` | Record a newer grant for this operation. |
| `authorization_revoked` | `epoch` | Revoke the current recorded grant. |
| `dispatch` | `attempt_id`, `access` (`read` or `write`), optional `authorization_epoch` for writes | Record one invocation; never execute it. |
| `cancel_requested` | `attempt_id` | Cancellation was requested, not necessarily honored. |
| `wait_cancelled` | `attempt_id` | The caller stopped waiting. Does not imply a cancellation request was sent. |
| `outcome_observed` | `attempt_id`, `outcome` | Evidence concerning the **original write attempt**, even when retrieved by a separate query. |
| `read_completed` | `attempt_id` | A read-only query finished; says nothing by itself about write outcomes. |
| `protocol_observed` | `attempt_id`, `observation`, `evidence_id` | Preserve a protocol/API fact without changing business outcomes, cancellation flags or authorization. |

For `outcome_observed`:

- `unknown` supplies neither `evidence_id` nor `effect_id`; it never erases a previously recorded outcome.
- `committed` requires `evidence_id` and `effect_id`.
- `no_effect` requires `evidence_id` and no `effect_id`. Use only when a trusted external source establishes that this attempt caused no effect and cannot still commit. A cancelled coroutine, closed transport, failed status, elapsed timeout, or empty response is **not** such evidence.

Identifier strings are nonblank, at most 128 characters, and cannot contain control characters or surrogate code points. Epochs are integers in 0 through 2^53−1 and grants strictly increase. Unknown fields are errors, not ignored extensions. An identical replay of `event_id` is ignored; conflicting content is rejected. Attempt IDs are unique within an operation. Event IDs are unique across the entire file.

### Identity and evidence assumptions

- `operation_key` must identify **one intended logical effect**. Two legitimate purchases, multiple intended effects, or different tenants need distinct keys. The checker does not infer identity by hashing arguments.
- `effect_id` must consistently identify the actual effect within that operation, **not a request ID or receipt-observation ID**. Re-observing the same effect, including through an idempotent provider's retry response, does not prove a duplicate. Multiple distinct IDs only indicate duplicate effects if the adapter's identity mapping is correct.
- `evidence_id` is a reference to evidence the producer claims to have; the checker does not authenticate receipts or query providers.
- Authorization events are observations, not real capabilities, signatures, permissions, or an enforcement mechanism. The checker neither grants nor revokes real access. Grants apply only to the named operation. A missing authorization log is `untracked`; it is not inferred permission.
- Read queries require their own real-world authorization. The checker only avoids confusing a read with repeating a write; it does not assert that reads after revocation are permitted by a service's access policy.
- Input is bounded to 1 MiB, 16 KiB per physical line, and 10,000 events. Malformed JSON, duplicate JSON object keys, non-finite numbers, unsupported events, contradictory confirmed outcomes, and references to not-yet-dispatched attempts fail as data errors. No partial “pass” is returned.
- Repeated-risk findings include a total count and at most five example IDs, preventing quadratic report growth. Complete final per-operation evidence appears once in the JSON summary. Text output escapes untrusted identifiers and field names.

## Findings

| Code | Interpretation |
| --- | --- |
| `CANCELLED_OUTCOME_UNRESOLVED` | A write was cancelled / abandoned but its external outcome remains unknown. |
| `REDISPATCH_WITH_UNKNOWN_OUTCOME` | Another write was dispatched before an earlier write was reconciled. Risk, not duplicate-effect proof. |
| `REDISPATCH_AFTER_COMMIT` | A write followed a recorded effect; it might be safe under real provider idempotency, which this tool cannot establish. |
| `MULTIPLE_RECORDED_EFFECTS` | Distinct effect IDs were recorded for one logical operation; verify the evidence and identity mapping. |
| `DISPATCH_AFTER_REVOCATION` | A write occurred while its operation's recorded grant was revoked. |
| `STALE_OR_MISSING_AUTHORIZATION` | A write did not reference the current logged grant. |

Historical risk findings remain even if later evidence reconciles the outcome: the later fact does not erase the earlier decision under uncertainty. An unfinished write without cancellation appears as `outcome: unknown` in JSON but is not automatically a retry/cancellation finding.

The two `receipt_*_retry.synthetic.jsonl` examples finish with identical business
state but different historical risk: a claimed no-effect receipt available
before the next write permits that dispatch without an unknown-outcome finding;
the same receipt arriving afterward does not erase the earlier risk. Do not sort
observations by remote-event timestamps and thereby pretend the caller knew a
late receipt earlier. Both examples assume genuine final no-effect evidence;
neither obtains or authenticates such evidence.

## MCP evidence boundary (offline subset)

`mcp_boundary.py` classifies a **single supplied JSON message or result**, using the MCP 2026-07-28 / Tasks extension profile. Supported method contexts are `tools/call`, `tasks/get`, `tasks/update`, `tasks/cancel` and `notifications/cancelled`. It never calls a server or generates `dispatch`, `outcome_observed`, cancellation or authorization events.

- Response envelopes require `--request-id-json` with an exact, type-sensitive match. Numeric `1` is not string `"1"`.
- `tasks/get` results also require an exact `--task-id` match. The caller must supply context from the correct endpoint/session and original request; matching IDs alone do not establish that context.
- `--form result` explicitly inspects a bare result; no RPC-ID correlation is possible. It cannot be combined with `--request-id-json`.
- Exit 0 means **a protocol observation was classified**, not a clean business trace or confirmed effect. The output always says `business_outcome: unknown`.
- The parser bounds input to 1 MiB and nesting to 64. Selected malformed/unsupported shapes are rejected, but this is **not a complete MCP schema/conformance validator**. It does not validate every content block, timestamp, extension or transport rule.
- Opaque state, request arguments, tool output text, task handles and provider error details are not copied into reports. The classifier does not parse `requestState`, trust `readOnlyHint`/`idempotentHint`, or infer identity from RPC IDs.

The accepted `protocol_observed.observation` labels are:

`tool_call_request`, `tool_result`, `tool_error`, `rpc_error`, `input_required`, `request_cancel_intent`, `task_cancel_intent`, `task_cancel_ack`, `task_poll_request`, `task_update_request`, `task_update_ack`, `task_created`, `task_working`, `task_input_required`, `task_completed`, `task_completed_tool_error`, `task_failed`, `task_cancelled`.

An explicit application mapping may attach one of these labels plus an evidence reference to an existing attempt. **None of the labels automatically changes business state.** Even `task_cancelled` is not proof of rollback; a cancel acknowledgment may be followed by `working` or `completed`; and task `completed` can contain a tool-level error.

The official Tasks documentation has completed-result examples whose nested tool result omits `resultType`. The classifier retains this fact as `nested_result_type_present: false`; it does not repair the source or pronounce it conformant. A top-level missing/unknown discriminator remains outside this supported profile.

For protocol false positives and false negatives, see [MCP_BOUNDARY_REVIEW.md](MCP_BOUNDARY_REVIEW.md). [PUBLIC_EXAMPLE_VALIDATION.json](PUBLIC_EXAMPLE_VALIDATION.json) records classification of **10 fetched official illustrative examples** with commit/blob/sample hashes; they are not production traces and their raw payloads are not vendored. All runnable files under `examples/` and all test-generated messages are original synthetic data.

## Why this small tool exists

The MCP Python SDK already implements cancellation propagation, scoped handling, cleanup and response suppression, and has explicit tests for synchronous work that outlives cancellation. MCP Tasks already provide durable handles and result polling. This tool does not claim those ideas are new and does not treat protocol-compliant dropped late responses as bugs.

The separate application-level question is whether a caller re-dispatches an uncertain, potentially side-effectful operation. The inspected SDK cancellation tests exercise transport/handler lifecycle; the inspected files do not test that business-level retry scenario. This is a **bounded source-review observation**, not a claim about every upstream test or a confirmed SDK defect. See [SOURCE_NOTES.md](SOURCE_NOTES.md) and [SOURCES.json](SOURCES.json) for pinned evidence.

## Limitations and next work

This is a custom offline event format with a limited observational MCP classifier, **not** a full wire decoder, protocol conformance suite, durable ledger, retry executor, exactly-once guarantee, security control, or production-ready adapter. Input could omit events, mislabel writes as reads, reuse keys incorrectly, or invent receipts. No external effect has been validated. No real MCP integration tests were run.

1. Do **not** add an automatic wire-to-business-event adapter until an explicit trusted application contract defines operation identity, effect receipts, read/write classification and MRTR continuation boundaries. If those facts remain unavailable, stop at observational labels rather than guess.
2. Maintain the bounded deterministic state-space suite in `test_state_space.py`: 5,856 generated case executions plus a paired-history counterexample. It covers two-write interleavings, replay, independent operations, same-key reads and passive protocol labels, not all possible histories. See [STATE_SPACE_REVIEW.md](STATE_SPACE_REVIEW.md) for the application-contract gate, actual envelope and limitations; extend it only for a concrete new semantic change or failing history.
3. Define stream/session boundaries, principal scoping and retention semantics before accepting production logs.
4. Recheck the pinned SDK paths and current MCP Tasks documentation for changes; do not automatically install or run upstream updates.
5. Choose the project's own distribution license before describing it as an open-source-licensed release. The Python SDK's MIT license, the Tasks repository's Apache-2.0 license, and the main protocol repository's licensing-transition notice were inspected; none automatically licenses this project. No upstream code or example payload is vendored; see [SOURCE_NOTES.md](SOURCE_NOTES.md).

All included examples are synthetic. Do not commit real credentials, prompts, personal data or service receipts.
