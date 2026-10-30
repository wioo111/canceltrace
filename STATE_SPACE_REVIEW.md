# Bounded state-space review: observation order is evidence

## Question and decision

Can the current offline checker distinguish a repeated observation from a new
write, retain uncertainty across cancellation, and preserve historical risk
without being confused by unrelated interleavings?

This is a useful question even while the application adapter remains blocked.
The previous public-contract review did not establish a complete trusted mapping
from business operations to attempts and effect receipts. Adding more protocol
branches would not fix that gap. This round therefore changes **tests and
documentation only**, not runtime behavior or input/output schema.

## Counterexamples considered before testing

| Tempting assertion | Counterexample | Tested expectation |
| --- | --- | --- |
| Same final state means same retry risk. | A trusted no-effect observation arrives just before versus just after a second dispatch. Both histories end identically. | Only the latter retains historical unknown-outcome re-dispatch risk. |
| Another row always means another action. | A logger replays the identical event ID and contents. | Event replay changes counters, not business state or finding identities. |
| Reusing an ID makes any retry harmless. | The same event ID has changed access/content. | Reject the whole trace as conflicting evidence. |
| Every new attempt under one key is another write. | A separate explicitly classified read polls the same logical operation. | Read interleavings do not create write-risk findings or imply a write outcome. |
| Two receipts mean two effects. | Both write attempts refer to the same claimed effect ID. | No distinct-effect finding; historical re-dispatch warnings can still remain. |
| Cancellation or protocol completion resolves uncertainty. | Wait cancellation precedes/follows unknown, no-effect or committed evidence; each passive protocol label is attached afterward. | Only explicit business outcome evidence resolves uncertainty; labels do not change business state. |
| Concurrent unrelated work can share state. | The same attempt patterns are interleaved under different operation keys. | Each operation has the same business state/findings as its isolated history. |

These are original synthetic counterexamples, not observations of deployed
services. In particular, a synthetic `no_effect` row assumes the contract below;
the tests do not manufacture a real provider guarantee.

## Deterministic test envelope

`python3 -m unittest test_state_space -v` runs seven test methods:

| Generated family | Cases | Construction |
| --- | ---: | --- |
| Two writes under one key | 1,280 | 4 outcome modes × 4 modes × 2 cancellation/observation orders per attempt × 20 order-preserving merges. |
| Replay invariance | 1,280 | Each preceding history with adjacent duplicate rows plus a full suffix replay. |
| Independent operations | 1,280 | Same pattern family, but different operation keys; compare each projected operation to isolated execution. |
| Same-key read insertion | 448 | 16 six-row serial write histories × 28 merges with one two-row read lane. |
| Passive protocol labels | 288 | 16 write histories × all 18 currently supported labels. |
| Conflicting replay rejection | 1,280 | Each two-write history followed by a changed copy of its first dispatch, preserving the event ID. |

Total: **5,856 generated cases**, plus one explicit paired-history test showing
that equal final state does not mean equal historical risk. These are case
executions, not 5,856 distinct production traces or separate unittest methods.
Every family asserts its declared count. No random seed, network or SDK is used.

The narrow reference oracle scans earlier rows to reconstruct unresolved writes
and observed effects; it does not import the checker's incremental state/index
implementation. It compares finding code, operation, attempt and trigger event
identity. Physical line numbers change under insertion/replay and are deliberately
not compared as if stable. Existing unit tests separately cover line reporting,
diagnostic samples and authorization behavior.

The main family independently checks each attempt's outcome, effect/evidence
identities and the operation's final effect set against the input rows. An
independent read-only review verified the 1,280 unique histories and used
process-local mutations to check sensitivity. It exposed a test-only blind spot
where empty attempt effect summaries initially survived the new family; direct
identity assertions were added. These checks do not authenticate the input IDs.
The same in-memory empty-summary mutation then produced 960 failures and no
errors in the main generated family, confirming that this particular blind spot
was closed; no mutated code was written to disk.

The passing result is a bounded negative finding: **no state-transition mismatch
was found in this envelope**. No runtime fix is claimed. The runtime modules are
byte-equal to the prior archived version; hashes and execution facts are in
`STATE_SPACE_VALIDATION.json`. Existing official source references remain pinned
in `SOURCES.json`; this round did not re-fetch or claim newer upstream heads.

## Application contract gate remains closed

Before any automatic adapter can emit business events, an auditable mapping must
provide all of the following, rather than merely assert a boolean `trusted` flag:

1. **Effect definition and cardinality:** define exactly which one intended
   effect the operation key represents. Authorization, object creation, capture,
   cancellation, refund and fulfillment must not be conflated.
2. **Identity and scope:** application-owned operation identity; account/tenant,
   environment and version boundaries; distinguish independent repeats from
   retries and continuations. A request ID, idempotency key or argument hash is
   not automatically that identity.
3. **Attempt attribution:** demonstrate which invocation or deduplicated logical
   operation a later observation concerns. A read query's own ID is not the
   original write attempt. Preserve uncertainty when attribution is missing.
4. **Qualifying effects:** define stable identities for actual effects, not just
   request/receipt/object identities; define the authoritative outcome fields and
   evidence-acquisition provenance. A list of IDs is not automatically a list of
   successful effects.
5. **No-effect finality:** establish both that the attempt caused no relevant
   effect and that it cannot still commit. A timeout, cancellation, error flag,
   unavailable handle or missing log is insufficient.
6. **Ordering and completeness:** distinguish when evidence became available from
   when the remote event happened. Define source ordering, missing-event policy,
   retention and replay identity; do not sort a late receipt into the past and
   erase a genuinely uncertain decision.
7. **Wrapper boundaries:** identify read/write behavior, MRTR/task continuation,
   and real authorization separately from protocol observations. Protocol-only
   labels cannot supply these application facts.

This is a review checklist, not an executable validator or evidence that a
specific integration satisfies it. No payment API, provider account, live trace,
MCP wrapper or production receipt has been validated.

## Limits and next stopping condition

The bounded oracle covers two writes, each with exactly one `wait_cancelled`
and one outcome observation. It does not sweep `cancel_requested`, absent
outcome rows, repeated fresh evidence IDs, or authorization epochs. It does
not exhaust all trace lengths, multiple conflicting outcomes, all authorization
epochs, stream/session/principal scope, malicious receipts, concurrency in a
running server, or incomplete logging. Unit tests are not formal verification.
The oracle and implementation could share a mistaken interpretation; passing
both does not independently authenticate the contract.

Next useful check: if a public reproducible application contract supplies the
seven boundaries, construct a small offline mapping and test falsifying cases
before implementation. If those facts remain unavailable, stop adapter work.
Do not infer them from transport flags or opaque state, and do not add larger
state spaces merely to increase a count. Revisit these tests when a concrete
schema/semantic change or a minimal failing history justifies new coverage.
