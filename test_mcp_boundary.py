"""Original synthetic counterexamples; these are not captured MCP traffic."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

from canceltrace import PROTOCOL_OBSERVATIONS, TraceError, analyze
from mcp_boundary import BoundaryError, classify_message, classify_result, parse_json


def response(result, rid="rpc-1"):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def tool_result(is_error=False):
    return {"resultType": "complete", "content": [], "isError": is_error}


def task(status, **fields):
    return {"resultType": "complete", "taskId": "synthetic-task", "status": status, **fields}


def classify(method, payload, **kwargs):
    return classify_message(method, response(payload), expected_request_id="rpc-1", **kwargs)


def trace(*events):
    return analyze("\n".join(json.dumps(e) for e in events).encode())


def event(kind, eid, **fields):
    return dict(type=kind, event_id=eid, operation_key="synthetic-op", **fields)


class WireBoundaryTests(unittest.TestCase):
    def test_success_and_tool_error_do_not_establish_effect(self):
        for error in (False, True):
            result = classify("tools/call", tool_result(error))
            self.assertEqual(result["observation"], "tool_error" if error else "tool_result")
            self.assertEqual(result["tool_is_error"], error)
            self.assertEqual(result["business_outcome"], "unknown")

    def test_cancel_ack_is_not_remote_cancel_or_no_effect(self):
        result = classify("tasks/cancel", {"resultType": "complete"})
        self.assertEqual(result["observation"], "task_cancel_ack")
        self.assertEqual(result["business_outcome"], "unknown")
        self.assertNotIn("cancel_requested", result)
        self.assertNotIn("wait_cancelled", result)

    def test_completed_task_can_contain_tool_error(self):
        result = classify("tasks/get", task("completed", result=tool_result(True)), expected_task_id="synthetic-task")
        self.assertEqual(result["task_status"], "completed")
        self.assertTrue(result["tool_is_error"])
        self.assertEqual(result["observation"], "task_completed_tool_error")
        self.assertEqual(result["business_outcome"], "unknown")

    def test_remote_cancelled_does_not_prove_rollback(self):
        result = classify("tasks/get", task("cancelled"), expected_task_id="synthetic-task")
        self.assertEqual(result["observation"], "task_cancelled")
        self.assertEqual(result["business_outcome"], "unknown")

    def test_embedded_result_discriminator_absence_is_retained_not_repaired(self):
        inner = {"content": [], "isError": True}
        result = classify("tasks/get", task("completed", result=inner), expected_task_id="synthetic-task")
        self.assertEqual(result["observation"], "task_completed_tool_error")
        self.assertFalse(result["nested_result_type_present"])
        self.assertNotIn("resultType", inner)
        self.assertEqual(result["business_outcome"], "unknown")

    def test_cancel_ack_then_working_then_complete_remains_observational(self):
        results = [classify("tasks/cancel", {"resultType": "complete"})]
        for payload in (task("working"), task("completed", result=tool_result())):
            results.append(classify("tasks/get", payload, expected_task_id="synthetic-task"))
        self.assertEqual([r["observation"] for r in results], ["task_cancel_ack", "task_working", "task_completed"])
        self.assertTrue(all(r["business_outcome"] == "unknown" for r in results))

    def test_input_required_and_mrtr_followup_do_not_generate_write_attempts(self):
        pending = classify("tools/call", {"resultType": "input_required", "requestState": "opaque-not-parsed"})
        self.assertEqual(pending["observation"], "input_required")
        request = {"jsonrpc": "2.0", "id": "new-rpc-id", "method": "tools/call",
                   "params": {"name": "synthetic_tool", "requestState": "opaque-not-parsed", "inputResponses": {}}}
        followup = classify_message("tools/call", request)
        self.assertEqual(followup["observation"], "tool_call_request")
        self.assertTrue(followup["continuation_fields_present"])
        self.assertNotIn("attempt_id", followup)
        self.assertNotIn("operation_key", followup)

    def test_poll_and_update_are_not_automatically_business_retries(self):
        for method, expected in (("tasks/get", "task_poll_request"), ("tasks/update", "task_update_request")):
            request = {"jsonrpc": "2.0", "id": 17, "method": method, "params": {"taskId": "synthetic-task"}}
            result = classify_message(method, request)
            self.assertEqual(result["observation"], expected)
            self.assertNotIn("access", result)
        self.assertEqual(classify("tasks/update", {"resultType": "complete"})["observation"], "task_update_ack")

    def test_cancel_intents_preserved_without_side_effect_inference(self):
        notification = {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": "old-rpc"}}
        self.assertEqual(classify_message("notifications/cancelled", notification)["observation"], "request_cancel_intent")
        request = {"jsonrpc": "2.0", "id": 9, "method": "tasks/cancel", "params": {"taskId": "synthetic-task"}}
        self.assertEqual(classify_message("tasks/cancel", request)["observation"], "task_cancel_intent")

    def test_task_creation_is_not_business_commit(self):
        result = classify("tools/call", {"resultType": "task", "taskId": "synthetic-task", "status": "working"})
        self.assertEqual(result["observation"], "task_created")
        self.assertEqual(result["business_outcome"], "unknown")

    def test_not_found_expired_and_arbitrary_rpc_error_never_prove_no_effect(self):
        for message in ("Task expired", "Task not found", "failure after external commit"):
            payload = {"jsonrpc": "2.0", "id": 7, "error": {"code": -32602, "message": message}}
            result = classify_message("tasks/get", payload, expected_request_id=7)
            self.assertEqual(result["observation"], "rpc_error")
            self.assertEqual(result["business_outcome"], "unknown")
            self.assertNotIn(message, json.dumps(result))

    def test_task_failed_is_not_tool_error_or_rollback(self):
        result = classify("tasks/get", task("failed", error={"code": -32603, "message": "synthetic"}),
                          expected_task_id="synthetic-task")
        self.assertEqual(result["observation"], "task_failed")
        self.assertNotIn("tool_is_error", result)
        self.assertEqual(result["business_outcome"], "unknown")

    def test_wrong_missing_and_type_different_rpc_ids_rejected(self):
        payload = response(tool_result(), rid=1)
        for expected in ("1", True, 2, None):
            with self.subTest(expected=expected), self.assertRaises(BoundaryError):
                classify_message("tools/call", payload, expected_request_id=expected)
        with self.assertRaises(BoundaryError):
            classify_message("tools/call", payload)

    def test_wrong_or_missing_task_id_rejected(self):
        for expected in (None, "other-task"):
            with self.subTest(expected=expected), self.assertRaises(BoundaryError):
                classify("tasks/get", task("working"), expected_task_id=expected)

    def test_unsupported_and_malformed_results_rejected(self):
        cases = [("tools/call", {}), ("tools/call", {"resultType": "extension-not-supported"}),
                 ("tools/call", dict(tool_result(), isError="false")),
                 ("tools/call", {"resultType": "input_required"}),
                 ("tasks/get", task("completed")),
                 ("tasks/get", task("input_required")),
                 ("tasks/get", task("new-state")),
                 ("tasks/cancel", dict(task("cancelled"))),
                 ("tasks/update", {"resultType": "complete", "result": {}})]
        for method, payload in cases:
            with self.subTest(method=method, payload=payload), self.assertRaises(BoundaryError):
                classify(method, payload, expected_task_id="synthetic-task")

    def test_annotations_do_not_automatically_assign_read_access(self):
        with self.assertRaises(BoundaryError):
            classify_result("tools/list", {"resultType": "complete", "tools": [
                {"annotations": {"readOnlyHint": True, "idempotentHint": True}}]})

    def test_result_only_form_is_explicit_and_has_no_rpc_binding(self):
        self.assertEqual(classify_result("tools/call", tool_result())["observation"], "tool_result")

    def test_malformed_envelopes_are_not_normalized(self):
        bad = [dict(response(tool_result()), error={"code": 0, "message": "x"}),
               dict(response(tool_result()), id=True),
               dict(response(tool_result()), jsonrpc="1.0"),
               dict(response(tool_result()), method="tasks/get"),
               {"jsonrpc": "2.0", "id": "rpc-1"}]
        for payload in bad:
            with self.subTest(payload=payload), self.assertRaises(BoundaryError):
                classify_message("tools/call", payload, expected_request_id="rpc-1")

    def test_json_limits_duplicates_and_invalid_encoding(self):
        for data in (b"\xff", b'{"x":1,"x":2}', b'{"x":NaN}', b"[" * 2000 + b"]" * 2000,
                     b" " * 1_048_577):
            with self.subTest(size=len(data)), self.assertRaises(BoundaryError):
                parse_json(data)

    def test_json_numeric_overflow_is_rejected(self):
        for literal in (b"1e999", b"-1e999"):
            with self.subTest(literal=literal), self.assertRaises(BoundaryError):
                parse_json(b'{"content":[' + literal + b"]}")

    def test_report_does_not_echo_payload_or_opaque_state(self):
        payload = {"resultType": "input_required", "requestState": "SYNTHETIC_SECRET_NOT_A_REAL_TOKEN"}
        report = classify("tools/call", payload)
        self.assertNotIn("SYNTHETIC_SECRET", json.dumps(report))
        report = classify("tools/call", {"resultType": "task", "taskId": "SYNTHETIC_PRIVATE_HANDLE", "status": "working"})
        self.assertNotIn("SYNTHETIC_PRIVATE_HANDLE", json.dumps(report))


class PassiveSchemaTests(unittest.TestCase):
    def dispatch(self, eid="d1", aid="a1", **fields):
        return event("dispatch", eid, attempt_id=aid, access="write", **fields)

    def observed(self, observation, eid="p1", aid="a1"):
        return event("protocol_observed", eid, attempt_id=aid, observation=observation, evidence_id="synthetic-evidence-" + eid)

    def test_every_protocol_observation_is_passive(self):
        for observation in sorted(PROTOCOL_OBSERVATIONS):
            with self.subTest(observation=observation):
                result = trace(self.dispatch(), self.observed(observation))
                state = result["operations"]["synthetic-op"]["attempts"]["a1"]
                self.assertEqual(state["outcome"], "unknown")
                self.assertFalse(state["cancel_requested"])
                self.assertFalse(state["wait_cancelled"])
                self.assertEqual(state["effect_ids"], [])
                self.assertEqual(state["evidence_ids"], [])
                self.assertEqual(len(state["protocol_observations"]), 1)

    def test_late_protocol_result_does_not_reopen_revoked_authorization_or_resolve_outcome(self):
        result = trace(event("authorization_granted", "g1", epoch=1), self.dispatch(authorization_epoch=1),
                       event("wait_cancelled", "w1", attempt_id="a1"),
                       event("authorization_revoked", "r1", epoch=1), self.observed("task_cancel_ack"),
                       self.observed("task_completed_tool_error", "p2"), self.dispatch("d2", "a2", authorization_epoch=1))
        self.assertEqual(result["operations"]["synthetic-op"]["authorization_state"], "revoked")
        codes = {f["code"] for f in result["findings"]}
        self.assertEqual(codes, {"CANCELLED_OUTCOME_UNRESOLVED", "REDISPATCH_WITH_UNKNOWN_OUTCOME", "DISPATCH_AFTER_REVOCATION"})

    def test_explicit_effect_receipt_survives_later_protocol_error(self):
        result = trace(self.dispatch(), event("outcome_observed", "o1", attempt_id="a1", outcome="committed",
                                             evidence_id="receipt", effect_id="effect"), self.observed("rpc_error"))
        self.assertEqual(result["operations"]["synthetic-op"]["attempts"]["a1"]["outcome"], "committed")

    def test_replayed_observation_is_idempotent(self):
        observed = self.observed("tool_result")
        result = trace(self.dispatch(), observed, observed)
        self.assertEqual(len(result["operations"]["synthetic-op"]["attempts"]["a1"]["protocol_observations"]), 1)

    def test_same_key_read_observations_do_not_create_write_retry(self):
        result = trace(self.dispatch(), event("dispatch", "q1", attempt_id="query", access="read"),
                       self.observed("task_poll_request", aid="query"), self.observed("task_completed", "p2", "query"))
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["operations"]["synthetic-op"]["attempts"]["a1"]["outcome"], "unknown")

    def test_unknown_observation_or_attempt_rejected(self):
        for events in ((self.dispatch(), self.observed("invented-safe")), (self.observed("tool_result"),)):
            with self.assertRaises(TraceError):
                trace(*events)


class BoundaryCLITests(unittest.TestCase):
    def invoke(self, data, *args):
        return subprocess.run([sys.executable, str(Path(__file__).with_name("mcp_boundary.py")), *args],
                              input=json.dumps(data).encode(), capture_output=True, timeout=5)

    def test_success_is_observation_not_verdict(self):
        result = self.invoke(response(tool_result(), rid=1), "tools/call", "-", "--request-id-json", "1")
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "protocol_observation")
        self.assertEqual(payload["business_outcome"], "unknown")

    def test_missing_correlation_is_data_error(self):
        result = self.invoke(response(tool_result()), "tools/call", "-")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["status"], "data_error")

    def test_result_only_input_is_explicit(self):
        result = self.invoke(tool_result(True), "tools/call", "-", "--form", "result")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["input_form"], "result")


if __name__ == "__main__":
    unittest.main()
