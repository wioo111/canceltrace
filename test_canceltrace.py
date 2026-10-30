import json
import subprocess
import sys
import unittest
from pathlib import Path

from canceltrace import MAX_BYTES, MAX_DIAGNOSTIC_IDS, MAX_LINE_BYTES, TraceError, analyze, parse_trace


def record(kind, event_id, operation_key="op", **fields):
    return dict(type=kind, event_id=event_id, operation_key=operation_key, **fields)


def dispatch(event_id="d1", attempt="a1", access="write", **fields):
    return record("dispatch", event_id, attempt_id=attempt, access=access, **fields)


def outcome(event_id="o1", attempt="a1", value="committed", effect="effect-1", **fields):
    if value != "unknown":
        fields["evidence_id"] = "receipt-" + event_id
    if value == "committed":
        fields["effect_id"] = effect
    return record("outcome_observed", event_id, attempt_id=attempt, outcome=value, **fields)


def run(*events):
    return analyze(("\n".join(json.dumps(e) for e in events) + "\n").encode())


def codes(result):
    return {finding["code"] for finding in result["findings"]}


class SemanticsTests(unittest.TestCase):
    def test_success(self):
        result = run(dispatch(), outcome())
        self.assertEqual(result["status"], "no_findings")
        self.assertEqual(result["operations"]["op"]["effect_ids"], ["effect-1"])

    def test_cancel_request_is_not_rollback(self):
        result = run(dispatch(), record("cancel_requested", "c1", attempt_id="a1"))
        self.assertEqual(codes(result), {"CANCELLED_OUTCOME_UNRESOLVED"})
        self.assertEqual(result["operations"]["op"]["attempts"]["a1"]["outcome"], "unknown")

    def test_local_wait_cancelled_does_not_imply_cancel_requested_or_no_effect(self):
        result = run(dispatch(), record("wait_cancelled", "w1", attempt_id="a1"))
        state = result["operations"]["op"]["attempts"]["a1"]
        self.assertFalse(state["cancel_requested"])
        self.assertTrue(state["wait_cancelled"])
        self.assertEqual(state["outcome"], "unknown")

    def test_commit_before_or_after_cancel_is_retained(self):
        cancel = record("wait_cancelled", "w1", attempt_id="a1")
        for sequence in ((dispatch(), outcome(), cancel), (dispatch(), cancel, outcome())):
            with self.subTest(sequence=sequence):
                result = run(*sequence)
                self.assertEqual(result["findings"], [])
                self.assertEqual(result["operations"]["op"]["attempts"]["a1"]["outcome"], "committed")

    def test_unknown_observation_cannot_erase_confirmed_outcome(self):
        result = run(dispatch(), outcome(), outcome("o2", value="unknown"))
        self.assertEqual(result["operations"]["op"]["attempts"]["a1"]["outcome"], "committed")

    def test_unresolved_retry_warns_without_claiming_duplicate(self):
        result = run(dispatch(), record("wait_cancelled", "w1", attempt_id="a1"), dispatch("d2", "a2"))
        self.assertIn("REDISPATCH_WITH_UNKNOWN_OUTCOME", codes(result))
        self.assertNotIn("MULTIPLE_RECORDED_EFFECTS", codes(result))
        self.assertEqual(result["operations"]["op"]["effect_ids"], [])

    def test_two_distinct_effects_detected(self):
        result = run(dispatch(), outcome(), dispatch("d2", "a2"), outcome("o2", "a2", effect="effect-2"))
        self.assertEqual(codes(result), {"REDISPATCH_AFTER_COMMIT", "MULTIPLE_RECORDED_EFFECTS"})

    def test_repeated_receipt_for_same_effect_is_not_duplicate(self):
        result = run(dispatch(), outcome(), outcome("o2"))
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["operations"]["op"]["effect_ids"], ["effect-1"])

    def test_idempotent_provider_returning_same_effect_on_new_attempt(self):
        result = run(dispatch(), outcome(), dispatch("d2", "a2"), outcome("o2", "a2"))
        self.assertIn("REDISPATCH_AFTER_COMMIT", codes(result))
        self.assertNotIn("MULTIPLE_RECORDED_EFFECTS", codes(result))

    def test_identical_event_replay_is_idempotent(self):
        event = outcome()
        result = run(dispatch(), event, event)
        self.assertEqual(result["replayed_events"], 1)
        self.assertEqual(result["unique_events"], 2)
        self.assertEqual(result["findings"], [])

    def test_explicit_no_effect_allows_new_attempt_without_unknown_warning(self):
        result = run(dispatch(), outcome(value="no_effect"), dispatch("d2", "a2"), outcome("o2", "a2"))
        self.assertEqual(result["findings"], [])

    def test_legal_same_key_read_does_not_count_as_redispatch(self):
        result = run(dispatch(), record("wait_cancelled", "w1", attempt_id="a1"),
                     dispatch("q1", "query", access="read"), outcome(),
                     record("read_completed", "q2", attempt_id="query"))
        self.assertEqual(result["findings"], [])
        self.assertTrue(result["operations"]["op"]["attempts"]["query"]["read_completed"])

    def test_cancelled_read_is_not_unknown_write(self):
        result = run(dispatch(access="read"), record("wait_cancelled", "w1", attempt_id="a1"))
        self.assertEqual(result["findings"], [])

    def test_different_operations_and_cancelled_sibling_do_not_interfere(self):
        result = run(dispatch(), record("wait_cancelled", "w1", attempt_id="a1"),
                     dispatch("d2", "a1", operation_key="other"), outcome("o2", operation_key="other"))
        self.assertEqual(codes(result), {"CANCELLED_OUTCOME_UNRESOLVED"})
        self.assertFalse(result["operations"]["other"]["attempts"]["a1"]["wait_cancelled"])

    def test_revoked_write_warns_but_same_key_read_is_legal(self):
        result = run(record("authorization_granted", "g1", epoch=1),
                     record("authorization_revoked", "r1", epoch=1),
                     dispatch("q1", "query", access="read"),
                     dispatch(authorization_epoch=1))
        self.assertEqual(codes(result), {"DISPATCH_AFTER_REVOCATION"})

    def test_regrant_does_not_reset_old_unknown_outcome(self):
        result = run(record("authorization_granted", "g1", epoch=1), dispatch(authorization_epoch=1),
                     record("wait_cancelled", "w1", attempt_id="a1"),
                     record("authorization_revoked", "r1", epoch=1),
                     record("authorization_granted", "g2", epoch=2),
                     dispatch("d2", "a2", authorization_epoch=2))
        self.assertIn("REDISPATCH_WITH_UNKNOWN_OUTCOME", codes(result))
        self.assertNotIn("DISPATCH_AFTER_REVOCATION", codes(result))

    def test_stale_or_missing_epoch_warns(self):
        for fields in ({}, {"authorization_epoch": 1}):
            with self.subTest(fields=fields):
                result = run(record("authorization_granted", "g2", epoch=2), dispatch(**fields))
                self.assertEqual(codes(result), {"STALE_OR_MISSING_AUTHORIZATION"})

    def test_missing_authorization_log_is_untracked_not_asserted_safe(self):
        result = run(dispatch())
        self.assertEqual(result["operations"]["op"]["authorization_state"], "untracked")
        self.assertEqual(result["operations"]["op"]["attempts"]["a1"]["outcome"], "unknown")

    def test_output_deterministic(self):
        self.assertEqual(run(dispatch(), outcome()), run(dispatch(), outcome()))

    def test_unresolved_diagnostic_samples_are_bounded(self):
        events = [dispatch(f"d{i}", f"a{i}") for i in range(2000)]
        raw = "\n".join(json.dumps(event) for event in events).encode()
        result = analyze(raw)
        last = result["findings"][-1]
        self.assertEqual(last["unresolved_attempt_count"], 1999)
        self.assertEqual(len(last["unresolved_attempt_sample"]), MAX_DIAGNOSTIC_IDS)
        self.assertLess(len(json.dumps(result)), len(raw) * 12)

    def test_effect_diagnostic_samples_are_bounded(self):
        events = [dispatch()] + [outcome(f"o{i}", effect=f"e{i}") for i in range(1000)]
        raw = "\n".join(json.dumps(event) for event in events).encode()
        result = analyze(raw)
        last = result["findings"][-1]
        self.assertEqual(last["recorded_effect_count"], 1000)
        self.assertEqual(len(last["effect_id_sample"]), MAX_DIAGNOSTIC_IDS)
        self.assertEqual(len(result["operations"]["op"]["effect_ids"]), 1000)
        self.assertLess(len(json.dumps(result)), len(raw) * 12)


class ValidationTests(unittest.TestCase):
    def test_conflicting_event_id(self):
        with self.assertRaisesRegex(TraceError, "event_id reused"):
            run(dispatch(), dispatch(attempt="a2"))

    def test_unknown_attempt(self):
        with self.assertRaisesRegex(TraceError, "not yet dispatched"):
            run(outcome())

    def test_duplicate_attempt(self):
        with self.assertRaisesRegex(TraceError, "already dispatched"):
            run(dispatch(), dispatch("d2"))

    def test_contradictory_evidence(self):
        with self.assertRaisesRegex(TraceError, "contradictory"):
            run(dispatch(), outcome(), outcome("o2", value="no_effect"))

    def test_read_cannot_claim_effect(self):
        with self.assertRaisesRegex(TraceError, "original write"):
            run(dispatch(access="read"), outcome())

    def test_write_cannot_emit_read_completed(self):
        with self.assertRaisesRegex(TraceError, "read attempt"):
            run(dispatch(), record("read_completed", "r1", attempt_id="a1"))

    def test_unknown_and_missing_fields(self):
        for event in (dict(dispatch(), unexpected=1), record("dispatch", "x"), record("nonsense", "x")):
            with self.subTest(event=event), self.assertRaises(TraceError):
                run(event)

    def test_bad_json_shapes_and_duplicate_keys(self):
        for data in (b"[]", b"null", b"{", b'{"x": 1, "x": 2}', b'{"x": NaN}', b"\xff", b"\n"):
            with self.subTest(data=data), self.assertRaises(TraceError):
                analyze(data)

    def test_bad_values(self):
        events = [dict(dispatch(), access=[]), dict(dispatch(), operation_key=""),
                  dict(dispatch(), attempt_id="x\x1b[0m"), dict(dispatch(), attempt_id="\ud800"),
                  dict(dispatch(), event_id=3), record("authorization_granted", "g", epoch=True),
                  record("authorization_granted", "g", epoch=-1),
                  record("authorization_granted", "g", epoch=2**53),
                  dict(outcome(), outcome=[])]
        for event in events:
            with self.subTest(event=event), self.assertRaises(TraceError):
                run(event)

    def test_identifiers_reject_all_unicode_control_characters(self):
        # Parse a structurally valid event in isolation: invalid identifiers must
        # fail validation, not merely fail later attempt/operation correlation.
        for codepoint in (*range(0x20), *range(0x7F, 0xA0)):
            for field in ("event_id", "operation_key", "attempt_id", "evidence_id", "effect_id"):
                for escaped in (True, False):
                    event = dict(outcome(), **{field: "left" + chr(codepoint) + "right"})
                    data = json.dumps(event, ensure_ascii=escaped).encode("utf-8")
                    with self.subTest(codepoint=codepoint, field=field, escaped=escaped):
                        with self.assertRaisesRegex(TraceError, field + " contains a control"):
                            parse_trace(data)

    def test_identifiers_preserve_valid_non_ascii_and_control_range_boundaries(self):
        for value in ("left~right", "left\u00a0right", "café", "操作", "effect-😀", "e\u0301"):
            for field in ("event_id", "operation_key", "attempt_id", "evidence_id", "effect_id"):
                event = dict(outcome(), **{field: value})
                for escaped in (True, False):
                    with self.subTest(value=value, field=field, escaped=escaped):
                        data = json.dumps(event, ensure_ascii=escaped).encode("utf-8")
                        self.assertEqual(parse_trace(data)[0][1][field], value)

    def test_confirmation_requires_evidence_and_effect_ids(self):
        for missing in ("evidence_id", "effect_id"):
            event = outcome()
            del event[missing]
            with self.subTest(missing=missing), self.assertRaises(TraceError):
                run(dispatch(), event)

    def test_epoch_order_and_unrecorded_grants(self):
        cases = [(record("authorization_revoked", "r1", epoch=1),),
                 (record("authorization_granted", "g1", epoch=2), record("authorization_granted", "g2", epoch=1)),
                 (dispatch(authorization_epoch=1),)]
        for events in cases:
            with self.subTest(events=events), self.assertRaises(TraceError):
                run(*events)

    def test_size_limits_and_deep_json(self):
        for data in (b" " * (MAX_BYTES + 1), b" " * (MAX_LINE_BYTES + 1), b"[" * 2000 + b"]" * 2000):
            with self.subTest(size=len(data)), self.assertRaises(TraceError):
                analyze(data)

    def test_physical_line_numbers_with_blank_lines(self):
        data = b"\n" + json.dumps(dispatch()).encode() + b"\n\n{}\n"
        with self.assertRaises(TraceError) as context:
            analyze(data)
        self.assertEqual(context.exception.line, 4)


class CLITests(unittest.TestCase):
    def invoke(self, data, *extra):
        return subprocess.run([sys.executable, str(Path(__file__).with_name("canceltrace.py")), "-", *extra],
                              input=data, capture_output=True, timeout=5)

    def test_exit_codes_json_and_text(self):
        clean = (json.dumps(dispatch()) + "\n" + json.dumps(outcome())).encode()
        result = self.invoke(clean, "--format", "json")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["status"], "no_findings")
        risky = (json.dumps(dispatch()) + "\n" + json.dumps(dispatch("d2", "a2"))).encode()
        result = self.invoke(risky)
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"REDISPATCH_WITH_UNKNOWN_OUTCOME", result.stdout)
        result = self.invoke(b"not json", "--format", "json")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["status"], "data_error")

    def test_invalid_suffix_does_not_emit_partial_pass(self):
        result = self.invoke(json.dumps(dispatch()).encode() + b"\n{", "--format", "json")
        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("findings", payload)
        self.assertNotIn("operations", payload)

    def test_del_and_c1_identifiers_are_data_errors_not_partial_reports(self):
        for codepoint in (0x7F, 0x85, 0x9B):
            for escaped in (True, False):
                event = dict(dispatch(), operation_key="left" + chr(codepoint) + "right")
                with self.subTest(codepoint=codepoint, escaped=escaped):
                    result = self.invoke(json.dumps(event, ensure_ascii=escaped).encode(), "--format", "json")
                    payload = json.loads(result.stdout)
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(payload["status"], "data_error")
                    self.assertNotIn("findings", payload)
                    self.assertNotIn("operations", payload)

    def test_terminal_control_in_unknown_key_is_escaped(self):
        event = dict(dispatch())
        event["\x1b[2J"] = "value"
        result = self.invoke(json.dumps(event).encode())
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(b"\x1b", result.stderr)
        self.assertIn(b"\\x1b", result.stderr)

    def test_terminal_control_in_duplicate_key_is_escaped(self):
        result = self.invoke(b'{"\\u001b[2J":1,"\\u001b[2J":2}')
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(b"\x1b", result.stderr)

    def test_missing_file(self):
        result = subprocess.run([sys.executable, str(Path(__file__).with_name("canceltrace.py")),
                                 str(Path(__file__).with_name("DOES_NOT_EXIST.jsonl")), "--format", "json"],
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["status"], "data_error")


if __name__ == "__main__":
    unittest.main()
