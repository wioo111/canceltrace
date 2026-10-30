# MCP evidence boundaries: counterexamples before normalization

Reviewed 2026-10-29 against current public repository heads, which matched the earlier pinned protocol and Python SDK commits. The Tasks extension implementation is a separate active repository; its actual tests were read, not executed. This is a source-supported design review, not a vulnerability report or production-trace study.

## What the previous schema could and could not represent

Version 1 correctly kept unknown business outcomes separate from local cancellation. However, its only result-like field was `outcome_observed`: a producer could record `unknown`, but could not preserve *which protocol result arrived* without abusing `committed` or `no_effect`.

Version 2 adds a passive `protocol_observed` evidence list. It neither clears unresolved attempts nor changes cancel/wait flags, effect IDs, access classification or authorization epochs. The optional offline classifier recognizes selected public MCP shapes but emits only observation labels. Application-level identity and effect decisions remain external.

## Counterexample matrix

| Observable fact | Unsafe conversion | Why it fails | Current boundary |
| --- | --- | --- | --- |
| `tasks/cancel` returns an empty complete result | `no_effect` or remote cancelled | Acknowledgment is eventually consistent; the server may keep working and finish normally. | `task_cancel_ack`; outcome unchanged. |
| `tasks/get` returns `completed` with nested `isError: true` | business success or protocol task failure | Protocol completion and tool failure coexist. Neither tells us whether an external effect occurred. | Preserve `task_completed_tool_error`, `task_status=completed`, `tool_is_error=true`. |
| An observation iterator or settlement wait aborts | remote cancellation | Official client tests assert zero `tasks/cancel` calls for these local-abort cases. | Keep `wait_cancelled` separate; no flag is synthesized from protocol labels. |
| `tasks/get` is retried after a transient observation error | another business write | It is polling the same task, not resending the initiating tool invocation. | Classify `task_poll_request`; do not create a write dispatch. |
| A legal multi-round-trip `tools/call` continuation has a new RPC ID | a duplicate business attempt | The protocol explicitly requires a different RPC ID for the continuation. | Retain request/input-required labels; require application-specific attempt mapping. |
| `readOnlyHint` or `idempotentHint` is present | trusted read access or deduplication proof | The official schema says annotations are hints, not guaranteed descriptions. | No access or effect inference; `tools/list` is outside the classifier. |
| A task is expired or not found | no effect happened | Task-handle availability does not establish past external effects. | `rpc_error`; outcome remains unknown. |
| Task state is `cancelled` | rollback succeeded | Ending task execution does not establish reversal of prior effects. | `task_cancelled`; outcome unchanged. |
| Success response arrives after local cancellation/revocation | revive authorization or silently complete business state | Protocol evidence and authorization are independent; source logs can be incomplete. | Preserve the observation without changing either. |

### Deliberately unresolved false positives

The core still warns if an adapter explicitly logs two unresolved **write dispatches** for one logical operation. If those two rows were actually one MRTR conversation or two task polls, the adapter's mapping was wrong. The classifier does not hide this by inventing `no_effect`, dropping a row or merging IDs. A production adapter requires a trusted per-application operation model; protocol-only logs cannot supply it.

An idempotent service may safely receive a repeated write. `REDISPATCH_AFTER_COMMIT` remains advisory, not a duplicate-effect verdict. There is no automatic suppression based on an untrusted idempotency hint.

### Deliberately unresolved false negatives

Missing or forged events, mislabeled writes, reused logical-operation keys, wrong endpoint/session context, incomplete provider receipts and multiple intended effects under one key remain outside the checker. Equal RPC IDs do not prove messages came from the same endpoint/session. A `no_findings` report never establishes complete telemetry or correct real-world behavior.

## Primary-source evidence

- [Tasks cancellation, current extension](https://github.com/modelcontextprotocol/ext-tasks/blob/b3d28aceaa6d49f5f92258e3d06b61ad6f111e84/specification/2026-07-28/tasks.md#task-cancellation): intent acknowledgment, eventual consistency and cooperative cancellation.
- [Tasks error semantics](https://github.com/modelcontextprotocol/ext-tasks/blob/b3d28aceaa6d49f5f92258e3d06b61ad6f111e84/specification/2026-07-28/tasks.md#task-execution-errors): completed can contain tool errors; failed denotes a JSON-RPC execution error.
- [Actual public lifecycle tests](https://github.com/modelcontextprotocol/ext-tasks/blob/b3d28aceaa6d49f5f92258e3d06b61ad6f111e84/packages/ext-tasks/src/client/task-lifecycle-races.test.ts#L367-L485): separate observation/cancellation retry tests. Lines 708–793 and 1255–1302 cover local observers/settlement and zero remote cancel calls.
- [Actual public resumption tests](https://github.com/modelcontextprotocol/ext-tasks/blob/b3d28aceaa6d49f5f92258e3d06b61ad6f111e84/packages/ext-tasks/src/client/task-resumption.test.ts#L85-L113): reject endpoint, generation and original-operation mismatches before dispatch.
- [MRTR client requirements](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/84d845729393f79b3fd3d392aa17cb86f5b6f95c/docs/specification/2026-07-28/basic/patterns/mrtr.mdx#L249-L257): opaque state, scoped input and a new RPC ID per continuation.
- [Tool annotations](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/84d845729393f79b3fd3d392aa17cb86f5b6f95c/schema/2026-07-28/schema.ts#L1900-L1943): hints are not guaranteed tool behavior.
- [Python SDK error tests](https://github.com/modelcontextprotocol/python-sdk/blob/d7845c073a2b9ba5ab2817ecf93d3e45ef24cd14/tests/docs_src/test_handling_errors.py#L15-L50): pre-function argument rejection and handler-level errors use the same tool-error category. Generic flags alone cannot identify the application-effect boundary.

## Actual example-data validation, not invented traces

Ten official illustrative samples were fetched via the GitHub connector and parsed as JSON by this project's original classifier: three stand-alone schema examples and seven JSON code blocks from the current Tasks specification. The result manifest records source commit/blob SHA, code-block index where relevant, sample SHA-256 and observed labels. Raw examples are not vendored; no TypeScript/Python upstream code was executed.

Two official completed-task examples omit `resultType` inside their embedded tool result. The classifier records `nested_result_type_present=false`, while retaining the visible outer task state and tool error flag. This is neither a repaired sample nor a conformance claim. Top-level unknown/missing result discriminators remain rejected. Isolated-example expected IDs are supplied as classification context, not evidence of a real captured exchange.

All runnable regression fixtures in this project are separately labeled synthetic. Their correct results demonstrate local semantics under the stated assumptions, not interoperability with a running MCP server.

## Next useful test and stopping conditions

Before broadening parsing, obtain a public, reproducible application contract that defines one business operation, its effect identity and trustworthy outcome receipt. Then test whether that contract can map a small complete cancellation/retry trace without assumptions about opaque state. Keep it offline first.

Abandon the automatic adapter direction if that contract is unavailable, the same logical operation cannot be distinguished from independent repeats, or outcomes can only be inferred from transport success/error flags. Do not compensate with heuristic hashes or annotation trust. Do not reimplement Tasks' already tested polling, cancellation ownership or resumption machinery. Further work should have a distinct evidence-quality benefit, not just more protocol branches.
