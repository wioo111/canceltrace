"""Classify a bounded MCP 2026-07-28 message without inferring business effects.

This is a shape-checked offline observation classifier, not a protocol validator,
trace correlator, or automatic canceltrace normalizer. No SDK is imported.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

from canceltrace import MAX_BYTES, PROTOCOL_OBSERVATIONS

METHODS = ("tools/call", "tasks/get", "tasks/update", "tasks/cancel", "notifications/cancelled")
TASK_STATES = ("working", "input_required", "completed", "failed", "cancelled")
MAX_JSON_DEPTH = 64
UNSET = object()


class BoundaryError(ValueError):
    pass


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BoundaryError(f"{label} must be an object")
    return value


def _string(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value:
        raise BoundaryError(f"{label} must be a nonempty string")


def _request_id(value: Any) -> None:
    if not isinstance(value, str) and type(value) is not int:
        raise BoundaryError("request id must be a string or integer, not null/bool")


def _error(value: Any) -> None:
    error = _object(value, "error")
    if type(error.get("code")) is not int or not isinstance(error.get("message"), str):
        raise BoundaryError("error requires integer code and string message")


def _observation(name: str, **details: Any) -> dict[str, Any]:
    assert name in PROTOCOL_OBSERVATIONS
    return dict(
        observation=name, business_outcome="unknown", **details,
        limit="Protocol fact only. No operation identity, access classification, effect, or authorization inferred.",
    )


def _tool_result(result: Any, *, allow_missing_result_type: bool = False) -> bool:
    result = _object(result, "tool result")
    type_ok = result.get("resultType") == "complete" or (
        allow_missing_result_type and "resultType" not in result
    )
    if not type_ok or not isinstance(result.get("content"), list):
        raise BoundaryError("2026-07-28 tool result requires resultType=complete and content array")
    if type(result.get("isError", False)) is not bool:
        raise BoundaryError("isError must be a boolean")
    return result.get("isError", False)


def _input_required(result: dict[str, Any]) -> None:
    if "inputRequests" not in result and "requestState" not in result:
        raise BoundaryError("input_required requires inputRequests or requestState")
    if "inputRequests" in result:
        _object(result["inputRequests"], "inputRequests")
    if "requestState" in result and not isinstance(result["requestState"], str):
        raise BoundaryError("requestState must be an opaque string")


def _task_status(result: dict[str, Any]) -> str:
    _string(result.get("taskId"), "taskId")
    status = result.get("status")
    if status not in TASK_STATES:
        raise BoundaryError("unsupported task status")
    return status


def classify_result(method: str, value: Any, *, expected_task_id: str | None = None) -> dict[str, Any]:
    """Classify result-only data; no RPC correlation is possible for this form."""
    if method not in METHODS or method == "notifications/cancelled":
        raise BoundaryError("unsupported result method")
    result = _object(value, "result")
    result_type = result.get("resultType")
    if method == "tools/call":
        if result_type == "input_required":
            _input_required(result)
            return _observation("input_required")
        if result_type == "task":
            return _observation("task_created", task_status=_task_status(result))
        is_error = _tool_result(result)
        return _observation("tool_error" if is_error else "tool_result", tool_is_error=is_error)
    if result_type != "complete":
        raise BoundaryError("task-method result requires resultType=complete in this protocol profile")
    if method in ("tasks/cancel", "tasks/update"):
        if set(result) - {"resultType", "_meta"}:
            raise BoundaryError("acknowledgement must not contain task state or business result fields")
        return _observation("task_cancel_ack" if method == "tasks/cancel" else "task_update_ack")
    status = _task_status(result)
    if expected_task_id is None or result["taskId"] != expected_task_id:
        raise BoundaryError("tasks/get requires an exact expected_task_id match")
    if status == "completed":
        # Official task examples omit this discriminator inside the embedded
        # tool result. Preserve that absence; do not synthesize conformance.
        is_error = _tool_result(result.get("result"), allow_missing_result_type=True)
        return _observation("task_completed_tool_error" if is_error else "task_completed",
                            task_status=status, tool_is_error=is_error,
                            nested_result_type_present="resultType" in result["result"])
    if status == "failed":
        _error(result.get("error"))
    if status == "input_required":
        _object(result.get("inputRequests"), "inputRequests")
    return _observation("task_" + status, task_status=status)


def classify_message(method: str, value: Any, *, expected_request_id: Any = UNSET,
                     expected_task_id: str | None = None) -> dict[str, Any]:
    if method not in METHODS:
        raise BoundaryError("unsupported method")
    message = _object(value, "message")
    if message.get("jsonrpc") != "2.0":
        raise BoundaryError("message requires jsonrpc=2.0")
    if "method" in message:
        if message["method"] != method or "result" in message or "error" in message:
            raise BoundaryError("request method/context mismatch or mixed request/response envelope")
        params = _object(message.get("params"), "params")
        if method == "notifications/cancelled":
            if "id" in message:
                raise BoundaryError("cancellation notification must not have id")
            _request_id(params.get("requestId"))
            return _observation("request_cancel_intent")
        _request_id(message.get("id"))
        if method == "tools/call":
            _string(params.get("name"), "tool name")
            if "inputResponses" in params:
                _object(params["inputResponses"], "inputResponses")
            if "requestState" in params and not isinstance(params["requestState"], str):
                raise BoundaryError("requestState must be an opaque string")
            return _observation("tool_call_request", continuation_fields_present=(
                "requestState" in params or "inputResponses" in params
            ))
        _string(params.get("taskId"), "taskId")
        return _observation({"tasks/get": "task_poll_request", "tasks/update": "task_update_request",
                             "tasks/cancel": "task_cancel_intent"}[method])
    if method == "notifications/cancelled":
        raise BoundaryError("notifications/cancelled does not have responses")
    _request_id(message.get("id"))
    if expected_request_id is UNSET:
        raise BoundaryError("response classification requires expected_request_id context")
    _request_id(expected_request_id)
    if type(message["id"]) is not type(expected_request_id) or message["id"] != expected_request_id:
        raise BoundaryError("response id does not exactly match expected_request_id")
    if ("result" in message) == ("error" in message):
        raise BoundaryError("response requires exactly one of result/error")
    if "error" in message:
        _error(message["error"])
        return _observation("rpc_error")
    return classify_result(method, message["result"], expected_task_id=expected_task_id)


def parse_json(data: bytes) -> Any:
    if len(data) > MAX_BYTES:
        raise BoundaryError(f"input exceeds {MAX_BYTES} bytes")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in items:
            if key in result:
                raise BoundaryError(f"duplicate JSON key: {key!a}")
            result[key] = value
        return result

    def constant(_: str) -> None:
        raise BoundaryError("non-finite number in JSON")

    try:
        parsed = json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, RecursionError) as exc:
        raise BoundaryError(f"invalid bounded JSON input: {exc}") from exc
    pending = [(parsed, 0)]
    while pending:
        value, depth = pending.pop()
        if depth > MAX_JSON_DEPTH:
            raise BoundaryError(f"JSON nesting exceeds {MAX_JSON_DEPTH}")
        if isinstance(value, float) and not math.isfinite(value):
            raise BoundaryError("non-finite number produced by JSON numeric overflow")
        if isinstance(value, dict):
            pending.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            pending.extend((child, depth + 1) for child in value)
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("method", choices=METHODS)
    parser.add_argument("file", help="JSON file or - for stdin")
    parser.add_argument("--form", choices=("message", "result"), default="message")
    parser.add_argument("--request-id-json", help="Expected response id as a JSON string or integer")
    parser.add_argument("--task-id", help="Expected task handle for tasks/get observations")
    args = parser.parse_args(argv)
    try:
        if args.file == "-":
            data = sys.stdin.buffer.read(MAX_BYTES + 1)
        else:
            with Path(args.file).open("rb") as stream:
                data = stream.read(MAX_BYTES + 1)
        if args.form == "message":
            expected_id = UNSET if args.request_id_json is None else parse_json(args.request_id_json.encode())
            report = classify_message(args.method, parse_json(data), expected_request_id=expected_id,
                                      expected_task_id=args.task_id)
        else:
            if args.request_id_json is not None:
                raise BoundaryError("result-only input has no response id to correlate")
            report = classify_result(args.method, parse_json(data), expected_task_id=args.task_id)
    except (OSError, BoundaryError) as exc:
        print(json.dumps(dict(status="data_error", message=str(exc)), ensure_ascii=True))
        return 2
    print(json.dumps(dict(status="protocol_observation", input_form=args.form, **report),
                     ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
