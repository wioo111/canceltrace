# Local verification

Date: 2026-10-29 UTC. Interpreter: Python 3.12.14. Run from this new project directory only.

```sh
python3 -m unittest discover -v
python3 -m py_compile canceltrace.py test_canceltrace.py
```

Initial result: **39 tests passed**, run 0.400 seconds; compilation passed. Tests include original code only. No SDK or third-party package was installed or executed. No network-based test was run.

| Example | CLI exit | Verified result |
| --- | --- | --- |
| `ambiguous_retry.jsonl` | 1 | Unresolved cancellation and risky re-dispatch; no claim of proven duplicate effects. |
| `duplicate_effects.jsonl` | 1 | Historical risky re-dispatch and two recorded effect IDs. |
| `reconciled_read.jsonl` | 0 | Same-key read-only reconciliation after recorded revocation; original effect retained. |
| `revoked_retry.jsonl` | 1 | Unresolved cancellation, re-dispatch risk, and a write after revocation. |

An independent local review found quadratic report growth and terminal-escape handling issues. The implementation was changed to maintain unresolved/effect indices, limit each finding to five sample IDs plus counts, and escape untrusted text. Four additional regression tests cover these fixes; the final 39-test suite includes them.

Not verified: real MCP adapter interoperability, remote provider outcomes, authentication of logged evidence, production concurrency, durability, CI, publication, or distribution-license selection. Passing local tests does not establish those properties.

## Offline protocol-boundary maintenance (2026-10-29)

```sh
python3 -m unittest discover -v
python3 -m py_compile canceltrace.py mcp_boundary.py test_canceltrace.py test_mcp_boundary.py
python3 canceltrace.py examples/task_cancel_ack_unknown.jsonl --format json
python3 mcp_boundary.py tasks/get examples/mcp_task_completed_error.synthetic.json \
  --request-id-json '"synthetic-poll-1"' --task-id synthetic-task
```

Latest verification: **69 tests passed in 0.510 seconds**; all four modules compiled.
The new trace example exited 1 with exactly one `CANCELLED_OUTCOME_UNRESOLVED`
finding. Its explicit read-only poll did not create a write attempt or resolve the
unknown write. The protocol example exited 0 with `task_completed_tool_error`,
`task_status: completed`, and `business_outcome: unknown`. Exit 0 from this helper
means selected-shape classification succeeded, not that a remote action was safe.

Ten actual official illustrative JSON examples were separately fetched and used
as data, including public schema examples and task specification code blocks.
All ten matched their expected protocol observation while retaining unknown
business effects. These are **not production traces**; request/task context was
supplied for isolated samples, not captured from a live exchange. Raw public
payloads are not vendored. `PUBLIC_EXAMPLE_VALIDATION.json` records exact source
commits, file/blob identities, code-block positions where needed, sample hashes,
and classification results. Official task examples omitting a nested tool-result
discriminator are reported as missing, not silently repaired or called conformant.

Independent read-only review re-ran the suite and found no remaining blocker for
this limited offline scope. Review caught numeric overflow (`1e999`/`-1e999`), now
rejected alongside duplicate fields, deep nesting, and non-finite constants.
Regression tests also cover type-sensitive request correlation, mismatched task
IDs, no payload echo, passive protocol observations, recorded receipt retention,
and read-only/revoked-result cases. All local fixtures and tests are original and
synthetic. No upstream test suite or third-party code was executed or installed.

See `MCP_BOUNDARY_REVIEW.md` for false positives/negatives and the explicit stop
condition for automatic adapters. The unverified properties above remain so.

## Bounded state-space maintenance (2026-10-29, later round)

```sh
python3 -m unittest discover -v
python3 -m unittest test_state_space -v
python3 -m py_compile canceltrace.py mcp_boundary.py test_canceltrace.py test_mcp_boundary.py test_state_space.py
python3 canceltrace.py examples/receipt_before_retry.synthetic.jsonl --format json
python3 canceltrace.py examples/receipt_after_retry.synthetic.jsonl --format json
```

The suite now has **76 passing test methods**. Seven new methods execute 5,856
bounded generated cases plus one paired-history counterexample. They do not
represent thousands of real traces or exhaustive verification. Final run time,
source SHA-256 values and exact checks are in `STATE_SPACE_VALIDATION.json`.
Compilation of all five Python modules passed.

The two new synthetic examples have identical final business state. The
before-retry receipt example exits 0 with no findings; the after-retry example
exits 1 with exactly `REDISPATCH_WITH_UNKNOWN_OUTCOME`. This verifies that later
reconciliation does not erase a decision made under uncertainty.

No runtime module, existing test module or schema changed: both runtime modules
and the two earlier test modules were compared byte-for-byte against archive
version 1. Existing pinned public sources were retained, not refreshed. No live
Stripe/MCP integration or new public application contract was validated. See
`STATE_SPACE_REVIEW.md` for scope, review findings and the still-closed adapter
gate. Independent review was read-only; its in-memory mutation checks did not
modify source files.

## Repository import verification (2026-10-30 UTC)

The unchanged five Python modules were rechecked with Python 3.12.14 before
repository import. `python3 -m unittest discover -v` passed all **76 test
methods in 1.374 seconds**; `python3 -m py_compile canceltrace.py mcp_boundary.py
test_canceltrace.py test_mcp_boundary.py test_state_space.py` passed. This includes
the existing 5,856 bounded generated case executions; no cases were added for
publication. Module hashes still match `STATE_SPACE_VALIDATION.json`.

Only publication-status/provenance documentation and this verification record
changed relative to the prior local source archive. The initialized repository's
`# canceltrace` heading is retained and expanded, and its initial commit remains
in history. The import contains only the 22 explicitly reviewed source, test,
documentation and synthetic-example files: no local ZIP, cache, credentials,
private logs, internal research notes or third-party source payloads.

This is a local offline verification result, not a remote CI pass or real-service
integration result. No CI workflow is supplied by this import. Distribution
license selection remains pending; public visibility alone does not establish an
open-source license.

## Identifier control-range correction (2026-10-30 UTC)

A focused check at commit `d52943fe9f070a55d86020103808f1dac48959c6`
confirmed that the documented ban on control characters was incomplete:
identifiers containing DEL or C1 characters were accepted. For example,
`left\u0085right` was accepted, although U+0085 alone was already rejected by
the separate nonblank check. C0 controls and surrogate code points were already
rejected. This is an input-contract defect; the existing text/JSON escaping
remains in place, and no terminal-injection exploit is claimed.

The correction rejects U+007F–U+009F in all five identifier fields. It intentionally
tightens validation of previously accepted, out-of-contract inputs; valid Unicode
identifiers are not restricted to ASCII or normalized. Business state transitions,
report schema version 2 and protocol classification are unchanged.

Three new test methods exercise all 65 Unicode `Cc` code points across five
identifier fields with JSON `ensure_ascii` enabled and disabled (650 parser
subcases; C0 still requires JSON escaping in either mode),
60 valid Unicode/boundary subcases, and six DEL/C1 CLI cases. Parser checks call
`parse_trace` so a later unknown-attempt error cannot falsely satisfy a validation
test. Before the fix these tests produced 336 expected failures and zero errors
(330 parser cases and six CLI cases); after it they all pass. CLI rejection
returns exit 2 and `data_error` without a partial result.

Full verification on Python 3.12.14:

```sh
python3 -m unittest discover -v
python3 -m py_compile canceltrace.py mcp_boundary.py test_canceltrace.py test_mcp_boundary.py test_state_space.py
```

Result: **79 test methods passed in 1.697 seconds**; compilation of all five
modules passed. The previous 5,856 bounded state-space cases are included and
unchanged. `STATE_SPACE_VALIDATION.json` remains the dated, historical pre-fix
validation record, not a claim about the new runtime hash.

The separate trace with an untracked write, a final no-effect receipt, a grant,
and a tracked write was also reproduced: it reports `no_findings` with final
authorization state `active`. This is not proof of the earlier write's authority.
README now explicitly documents that the operation summary does not retain
per-attempt authorization history; no authorization semantics or report fields
were changed. These are synthetic local checks, not live-service or CI results.
