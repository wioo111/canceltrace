"""Deterministic synthetic state-space checks; no SDK, network or real traces.

The small oracle deliberately scans prior records rather than reusing the
checker's incremental unresolved/effect indexes. Its domain is two writes with
one outcome observation each, not every valid canceltrace history.
"""

import itertools
import json
import unittest

from canceltrace import PROTOCOL_OBSERVATIONS, TraceError, analyze


MODES = ("unknown", "no_effect", "shared", "distinct")


def event(kind, event_id, attempt, operation="op", **fields):
    return dict(type=kind, event_id=event_id, operation_key=operation,
                attempt_id=attempt, **fields)


def lane(attempt, mode, outcome_first=False, operation="op"):
    dispatch = event("dispatch", attempt + ":d", attempt, operation, access="write")
    cancel = event("wait_cancelled", attempt + ":w", attempt, operation)
    fields = {"outcome": mode if mode in ("unknown", "no_effect") else "committed"}
    if mode != "unknown":
        fields["evidence_id"] = attempt + ":receipt"
    if fields["outcome"] == "committed":
        fields["effect_id"] = "shared-effect" if mode == "shared" else attempt + ":effect"
    outcome = event("outcome_observed", attempt + ":o", attempt, operation, **fields)
    return [dispatch, outcome, cancel] if outcome_first else [dispatch, cancel, outcome]


def interleavings(left, right):
    """All order-preserving merges, with no probabilistic sampling."""
    if not left or not right:
        yield left + right
        return
    for rest in interleavings(left[1:], right):
        yield [left[0]] + rest
    for rest in interleavings(left, right[1:]):
        yield [right[0]] + rest


def two_write_histories(independent=False):
    for first, second, first_order, second_order in itertools.product(MODES, MODES, (False, True), (False, True)):
        yield from interleavings(lane("a", first, first_order),
                                lane("b", second, second_order, "other" if independent else "op"))


def checked(events):
    return analyze(("\n".join(json.dumps(row) for row in events) + "\n").encode())


def semantic_findings(result, events):
    # Compare trigger identity, not shifted physical line numbers. Counts and
    # diagnostic samples are checked separately in the existing unit tests.
    return sorted((row["code"], row["operation_key"], row["attempt_id"],
                   events[row["line"] - 1]["event_id"]) for row in result["findings"])


def business_state(result):
    # Protocol observations are intentionally absent from this projection.
    return {key: {"authorization_state": op["authorization_state"],
                  "authorization_epoch": op["authorization_epoch"],
                  "effect_ids": op["effect_ids"],
                  "attempts": {attempt: {k: v for k, v in state.items()
                                         if k != "protocol_observations"}
                               for attempt, state in op["attempts"].items()}}
            for key, op in result["operations"].items()}


def reference_findings(events):
    """Slow, narrow prefix-based oracle, derived from the documented contract."""
    prefix = []
    expected = []

    def add(code, row):
        expected.append((code, row["operation_key"], row["attempt_id"], row["event_id"]))

    for row in events:
        prior = [x for x in prefix if x["operation_key"] == row["operation_key"]]
        effects = {x["effect_id"] for x in prior if x["type"] == "outcome_observed"
                   and x["outcome"] == "committed"}
        if row["type"] == "dispatch":
            dispatched = {x["attempt_id"] for x in prior if x["type"] == "dispatch"}
            resolved = {x["attempt_id"] for x in prior if x["type"] == "outcome_observed"
                        and x["outcome"] != "unknown"}
            if dispatched - resolved:
                add("REDISPATCH_WITH_UNKNOWN_OUTCOME", row)
            if effects:
                add("REDISPATCH_AFTER_COMMIT", row)
        if row["type"] == "outcome_observed" and row["outcome"] == "committed":
            if effects and row["effect_id"] not in effects:
                add("MULTIPLE_RECORDED_EFFECTS", row)
        prefix.append(row)
    for dispatch in (row for row in events if row["type"] == "dispatch"):
        related = [row for row in events if (row["operation_key"], row["attempt_id"]) ==
                   (dispatch["operation_key"], dispatch["attempt_id"])]
        cancelled = any(row["type"] in ("wait_cancelled", "cancel_requested") for row in related)
        resolved = any(row["type"] == "outcome_observed" and row["outcome"] != "unknown"
                       for row in related)
        if cancelled and not resolved:
            add("CANCELLED_OUTCOME_UNRESOLVED", dispatch)
    return sorted(expected)


class StateSpaceTests(unittest.TestCase):
    def test_all_1280_two_write_schedules_against_prefix_oracle(self):
        count = 0
        for rows in two_write_histories():
            with self.subTest(events=[x["event_id"] for x in rows], outcomes=[x.get("outcome") for x in rows]):
                result = checked(rows)
                self.assertEqual(semantic_findings(result, rows), reference_findings(rows))
                for outcome in (x for x in rows if x["type"] == "outcome_observed"):
                    state = result["operations"]["op"]["attempts"][outcome["attempt_id"]]
                    self.assertEqual(state["outcome"], outcome["outcome"])
                    self.assertTrue(state["wait_cancelled"])
                    self.assertFalse(state["cancel_requested"])
                    self.assertEqual(state["effect_ids"], [outcome["effect_id"]] if "effect_id" in outcome else [])
                    self.assertEqual(state["evidence_ids"], [outcome["evidence_id"]] if "evidence_id" in outcome else [])
                self.assertEqual(result["operations"]["op"]["effect_ids"],
                                 sorted({row["effect_id"] for row in rows if "effect_id" in row}))
            count += 1
        self.assertEqual(count, 1280)

    def test_replay_invariance_for_all_1280_schedules(self):
        count = 0
        for rows in two_write_histories():
            base = checked(rows)
            replay = [row for event_row in rows for row in (event_row, event_row)] + rows
            result = checked(replay)
            self.assertEqual(semantic_findings(result, replay), semantic_findings(base, rows))
            self.assertEqual(business_state(result), business_state(base))
            self.assertEqual(result["unique_events"], 6)
            self.assertEqual(result["replayed_events"], 12)
            count += 1
        self.assertEqual(count, 1280)

    def test_all_1280_independent_operation_interleavings(self):
        count = 0
        for rows in two_write_histories(independent=True):
            result = checked(rows)
            self.assertEqual(semantic_findings(result, rows), reference_findings(rows))
            for key in ("op", "other"):
                alone = [row for row in rows if row["operation_key"] == key]
                isolated = checked(alone)
                self.assertEqual(business_state(result)[key], business_state(isolated)[key])
                self.assertEqual([x for x in semantic_findings(result, rows) if x[1] == key],
                                 semantic_findings(isolated, alone))
            count += 1
        self.assertEqual(count, 1280)

    def test_448_same_key_read_interleavings_never_add_write_risk(self):
        count = 0
        read = [event("dispatch", "q:d", "q", access="read"), event("read_completed", "q:r", "q")]
        for first, second in itertools.product(MODES, repeat=2):
            writes = lane("a", first) + lane("b", second)
            expected = semantic_findings(checked(writes), writes)
            for rows in interleavings(writes, read):
                result = checked(rows)
                self.assertEqual(semantic_findings(result, rows), expected)
                self.assertEqual(result["operations"]["op"]["attempts"]["q"]["outcome"], None)
                count += 1
        self.assertEqual(count, 448)

    def test_288_protocol_labels_leave_business_state_unchanged(self):
        count = 0
        for first, second in itertools.product(MODES, repeat=2):
            rows = lane("a", first) + lane("b", second)
            base = checked(rows)
            for label in sorted(PROTOCOL_OBSERVATIONS):
                observed = event("protocol_observed", "wire", "a", observation=label, evidence_id="wire-reference")
                augmented = rows + [observed]
                result = checked(augmented)
                self.assertEqual(business_state(result), business_state(base))
                self.assertEqual(semantic_findings(result, augmented), semantic_findings(base, rows))
                self.assertEqual(result["operations"]["op"]["attempts"]["a"]["protocol_observations"][0]["observation"], label)
                count += 1
        self.assertEqual(count, 288)

    def test_all_1280_histories_reject_a_conflicting_replay(self):
        count = 0
        for rows in two_write_histories():
            conflict = dict(rows[0], access="read")
            with self.assertRaisesRegex(TraceError, "event_id reused with different content"):
                checked(rows + [conflict])
            count += 1
        self.assertEqual(count, 1280)

    def test_identical_final_state_can_have_different_historical_risk(self):
        a, b = lane("a", "no_effect"), lane("b", "shared")
        before = a + b
        after = [a[0], a[1], b[0], a[2], b[1], b[2]]
        left, right = checked(before), checked(after)
        self.assertEqual(business_state(left), business_state(right))
        self.assertEqual(semantic_findings(left, before), [])
        self.assertEqual([x[0] for x in semantic_findings(right, after)], ["REDISPATCH_WITH_UNKNOWN_OUTCOME"])


if __name__ == "__main__":
    unittest.main()
