# Public-source provenance and license notes

Inspected through the GitHub connector on **2026-10-29**. No upstream checkout, package installation, code execution or issue/PR submission was performed.

## MCP Python SDK

Repository: [modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk). At review: public, not archived; the connection returned pull permission but no push permission. `main` was pinned at `d7845c073a2b9ba5ab2817ecf93d3e45ef24cd14`, committed 2026-10-27 11:15:09 UTC. This establishes recent maintenance, not future support.

- [Dispatcher, lines 443–456](https://github.com/modelcontextprotocol/python-sdk/blob/d7845c073a2b9ba5ab2817ecf93d3e45ef24cd14/src/mcp/shared/jsonrpc_dispatcher.py#L443-L456): courtesy cancellation and removal of a pending waiter.
- [Dispatcher, lines 640–645](https://github.com/modelcontextprotocol/python-sdk/blob/d7845c073a2b9ba5ab2817ecf93d3e45ef24cd14/src/mcp/shared/jsonrpc_dispatcher.py#L640-L645): cancellation applied to an in-flight handler.
- [Dispatcher, lines 740–756](https://github.com/modelcontextprotocol/python-sdk/blob/d7845c073a2b9ba5ab2817ecf93d3e45ef24cd14/src/mcp/shared/jsonrpc_dispatcher.py#L740-L756): result suppressed when cancellation was requested.
- [Documentation tests, lines 151–168](https://github.com/modelcontextprotocol/python-sdk/blob/d7845c073a2b9ba5ab2817ecf93d3e45ef24cd14/tests/docs_src/test_cancellation.py#L151-L168): a synchronous tool without cooperative checks finishes after cancellation.
- [Documentation tests, line 206 onward](https://github.com/modelcontextprotocol/python-sdk/blob/d7845c073a2b9ba5ab2817ecf93d3e45ef24cd14/tests/docs_src/test_cancellation.py#L206): two transport/configuration combinations where cancellation does not reach the handler.
- [Low-level interaction tests](https://github.com/modelcontextprotocol/python-sdk/blob/d7845c073a2b9ba5ab2817ecf93d3e45ef24cd14/tests/interaction/lowlevel/test_cancellation.py): late-response dropping, caller abandonment and concurrent-call isolation.
- [Cancellation guide](https://github.com/modelcontextprotocol/python-sdk/blob/d7845c073a2b9ba5ab2817ecf93d3e45ef24cd14/docs/handlers/cancellation.md): existing cleanup/cooperation guidance, not a new gap discovered by this project.
- [Tasks status in this snapshot](https://github.com/modelcontextprotocol/python-sdk/blob/d7845c073a2b9ba5ab2817ecf93d3e45ef24cd14/examples/stories/tasks/README.md): runtime implementation is explicitly deferred here. Do not generalize to other SDKs or future versions.

## Protocol and existing solution

The spec repository was pinned at `84d845729393f79b3fd3d392aa17cb86f5b6f95c`, committed 2026-10-29 11:37:15 UTC.

- [Cancellation requirements and races](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/84d845729393f79b3fd3d392aa17cb86f5b6f95c/docs/specification/2026-07-28/basic/patterns/cancellation.mdx#L66-L87): completion can race cancellation; noncancellable work may continue and late responses may be ignored.
- [Current Tasks overview](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/84d845729393f79b3fd3d392aa17cb86f5b6f95c/docs/extensions/tasks/overview.mdx): durable handles, reconnect/poll/result retrieval, cooperative cancellation. `canceltrace` complements rather than reimplements these features.
- [SEP-2663 historical design](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/84d845729393f79b3fd3d392aa17cb86f5b6f95c/docs/seps/2663-tasks-extension.mdx): Final, explicitly not the source of later changes.

The SDK README linked to `docs/seps/2663-tasks-extension.md`, which returned 404. A fresh repository tree resolved the `.mdx` path above. This was a stale filename, not an access denial.

## Licensing

The Python SDK's [LICENSE](https://github.com/modelcontextprotocol/python-sdk/blob/d7845c073a2b9ba5ab2817ecf93d3e45ef24cd14/LICENSE) was read and identifies **MIT**, copyright 2024 Anthropic, PBC. It requires retaining the copyright and permission notice when copying substantial portions. This prototype's implementation, tests and examples are original; no upstream source is vendored or imported. Public sources informed the review and are linked rather than copied.

The new project's own distribution license is **not yet selected**. This is a source-visible experimental prototype, not an open-source-licensed release. No distribution-license grant or contributor copyright assignment is declared here. Select and review the project's own license before representing it as an open-source-licensed release, and review attribution obligations before adding dependencies or copied code. No private user-project source, logs, credentials or research text was incorporated.

## Maintenance review: protocol observation boundary (2026-10-29)

The protocol and Python SDK heads were re-read and remained at the commits above. A separate official repository, [modelcontextprotocol/ext-tasks](https://github.com/modelcontextprotocol/ext-tasks), was read at `b3d28aceaa6d49f5f92258e3d06b61ad6f111e84` (2026-10-20 23:40:57 UTC). Its specification and actual public tests distinguish local observation cancellation, remote task cancellation, polling retries and task resumption. They already solve substantial lifecycle machinery; this project does not reimplement it.

[MCP_BOUNDARY_REVIEW.md](MCP_BOUNDARY_REVIEW.md) contains the source-linked counterexample analysis. [PUBLIC_EXAMPLE_VALIDATION.json](PUBLIC_EXAMPLE_VALIDATION.json) records ten fetched official illustrative JSON examples and their classification results, including the two examples with a missing nested result discriminator. These are not captured production traces. Raw upstream examples and implementation source are **not vendored**; only provenance, hashes and classification results are retained. All runnable fixtures and tests remain original synthetic data.

Do not generalize the Python SDK's MIT license to the whole ecosystem. The [main protocol repository's LICENSE](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/84d845729393f79b3fd3d392aa17cb86f5b6f95c/LICENSE) describes a transition from MIT to Apache-2.0 and separate CC-BY-4.0 documentation terms. The [Tasks repository LICENSE](https://github.com/modelcontextprotocol/ext-tasks/blob/b3d28aceaa6d49f5f92258e3d06b61ad6f111e84/LICENSE) is Apache-2.0. Both notices were inspected. Future copying or distribution must review the applicable file's terms; this maintenance change copies no upstream implementation or example payload into the archive.

## Later maintenance: bounded synthetic state space

The new `test_state_space.py`, paired receipt-order examples and
`STATE_SPACE_REVIEW.md` are original, synthetic local work derived from this
project's documented contract, not copied upstream code or captured real traces.
Existing public-source records remain pinned and were not re-fetched this round.
`STATE_SPACE_VALIDATION.json` records the exact tested module hashes and confirms
runtime equality with the prior archive. The application-contract checklist does
not assert that any Stripe/MCP integration satisfies it. No adapter was added.

