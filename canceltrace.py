"""Offline, advisory cancellation / retry trace checks. Python standard library only."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from itertools import islice
from pathlib import Path
from typing import Any

MAX_BYTES = 1_048_576
MAX_LINE_BYTES = 16_384
MAX_EVENTS = 10_000
MAX_DIAGNOSTIC_IDS = 5
PROTOCOL_OBSERVATIONS = frozenset({
    "tool_call_request", "tool_result", "tool_error", "rpc_error", "input_required",
    "request_cancel_intent", "task_cancel_intent", "task_cancel_ack",
    "task_poll_request", "task_update_request", "task_update_ack", "task_created",
    "task_working", "task_input_required", "task_completed", "task_completed_tool_error",
    "task_failed", "task_cancelled",
})
BASE = {"event_id", "operation_key", "type"}
FIELDS = {
    "authorization_granted": ({"epoch"}, set()),
    "authorization_revoked": ({"epoch"}, set()),
    "dispatch": ({"attempt_id", "access"}, {"authorization_epoch"}),
    "cancel_requested": ({"attempt_id"}, set()),
    "wait_cancelled": ({"attempt_id"}, set()),
    "read_completed": ({"attempt_id"}, set()),
    "outcome_observed": ({"attempt_id", "outcome"}, {"evidence_id", "effect_id"}),
    "protocol_observed": ({"attempt_id", "observation", "evidence_id"}, set()),
}


class TraceError(ValueError):
    def __init__(self, line: int, message: str):
        self.line = line
        super().__init__(f"line {line}: {message}")


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key!a}")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _identifier(value: Any, field_name: str, line: int) -> None:
    if not isinstance(value, str) or not 1 <= len(value) <= 128 or not value.strip():
        raise TraceError(line, f"{field_name} must be a nonblank string of 1–128 characters")
    if any(ord(char) < 32 or 0x7F <= ord(char) <= 0x9F
           or 0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise TraceError(line, f"{field_name} contains a control character or invalid Unicode")


def _epoch(value: Any, field_name: str, line: int) -> None:
    if type(value) is not int or value < 0 or value > 2**53 - 1:
        raise TraceError(line, f"{field_name} must be an integer from 0 to 2^53-1")


def _validate(event: Any, line: int) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise TraceError(line, "each event must be a JSON object")
    kind = event.get("type")
    if not isinstance(kind, str) or kind not in FIELDS:
        raise TraceError(line, "unsupported event type")
    required, optional = FIELDS[kind]
    missing = (BASE | required) - event.keys()
    extra = event.keys() - (BASE | required | optional)
    if missing:
        raise TraceError(line, f"missing fields: {', '.join(sorted(missing))}")
    if extra:
        raise TraceError(line, f"unknown fields: {', '.join(ascii(key) for key in sorted(extra))}")
    for key in ("event_id", "operation_key", "attempt_id", "evidence_id", "effect_id"):
        if key in event:
            _identifier(event[key], key, line)
    for key in ("epoch", "authorization_epoch"):
        if key in event:
            _epoch(event[key], key, line)
    if kind == "dispatch" and event["access"] not in ("read", "write"):
        raise TraceError(line, "access must be read or write")
    if kind == "dispatch" and event["access"] == "read" and "authorization_epoch" in event:
        raise TraceError(line, "read dispatch must not claim a write authorization epoch")
    if kind == "protocol_observed":
        observation = event["observation"]
        if not isinstance(observation, str) or observation not in PROTOCOL_OBSERVATIONS:
            raise TraceError(line, "unsupported protocol observation")
    if kind == "outcome_observed":
        outcome = event["outcome"]
        if outcome not in ("unknown", "committed", "no_effect"):
            raise TraceError(line, "outcome must be unknown, committed, or no_effect")
        if outcome != "unknown" and "evidence_id" not in event:
            raise TraceError(line, "confirmed outcomes require evidence_id")
        if (outcome == "committed") != ("effect_id" in event):
            raise TraceError(line, "effect_id is required only for committed outcomes")
        if outcome == "unknown" and "evidence_id" in event:
            raise TraceError(line, "unknown outcome must not claim confirmation evidence")
    return event


def parse_trace(data: bytes) -> list[tuple[int, dict[str, Any]]]:
    if len(data) > MAX_BYTES:
        raise TraceError(0, f"input exceeds {MAX_BYTES} bytes")
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TraceError(0, "input is not UTF-8") from exc
    events = []
    for number, line in enumerate(content.split("\n"), 1):
        if len(line.encode("utf-8")) > MAX_LINE_BYTES:
            raise TraceError(number, f"line exceeds {MAX_LINE_BYTES} bytes")
        if not line.strip():
            continue
        if len(events) >= MAX_EVENTS:
            raise TraceError(number, f"input exceeds {MAX_EVENTS} events")
        try:
            event = json.loads(line, object_pairs_hook=_object, parse_constant=_constant)
        except (ValueError, RecursionError) as exc:
            raise TraceError(number, f"invalid JSON: {exc}") from exc
        events.append((number, _validate(event, number)))
    if not events:
        raise TraceError(0, "trace contains no events")
    return events


@dataclass
class Attempt:
    access: str
    line: int
    outcome: str = "unknown"
    cancel_requested: bool = False
    wait_cancelled: bool = False
    read_completed: bool = False
    evidence_ids: dict[str, None] = field(default_factory=dict)
    effect_ids: set[str] = field(default_factory=set)
    protocol_observations: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Operation:
    attempts: dict[str, Attempt] = field(default_factory=dict)
    epoch: int | None = None
    revoked: bool = False
    effects: dict[str, None] = field(default_factory=dict)
    unresolved: dict[str, None] = field(default_factory=dict)


def analyze(data: bytes) -> dict[str, Any]:
    """Analyze evidence claimed by a trace, never independently verified external state."""
    events = parse_trace(data)
    seen: dict[str, dict[str, Any]] = {}
    operations: dict[str, Operation] = {}
    findings: list[dict[str, Any]] = []
    replayed = 0

    def finding(code: str, line: int, key: str, attempt: str, message: str,
                severity: str = "warning", **extra: Any) -> None:
        findings.append(dict(code=code, severity=severity, line=line, operation_key=key,
                             attempt_id=attempt, message=message, **extra))

    for line, event in events:
        event_id = event["event_id"]
        if event_id in seen:
            if seen[event_id] != event:
                raise TraceError(line, "event_id reused with different content")
            replayed += 1
            continue
        seen[event_id] = event
        key, kind = event["operation_key"], event["type"]
        op = operations.setdefault(key, Operation())
        if kind == "authorization_granted":
            if op.epoch is not None and event["epoch"] <= op.epoch:
                raise TraceError(line, "authorization grant epochs must strictly increase")
            op.epoch, op.revoked = event["epoch"], False
            continue
        if kind == "authorization_revoked":
            if op.epoch != event["epoch"]:
                raise TraceError(line, "revocation must reference the current recorded grant")
            op.revoked = True
            continue

        attempt_id = event["attempt_id"]
        if kind == "dispatch":
            if attempt_id in op.attempts:
                raise TraceError(line, "attempt_id already dispatched for this operation")
            if event["access"] == "write":
                if op.unresolved:
                    finding("REDISPATCH_WITH_UNKNOWN_OUTCOME", line, key, attempt_id,
                            "Earlier write outcome is unresolved; another dispatch may repeat an effect.",
                            unresolved_attempt_count=len(op.unresolved),
                            unresolved_attempt_sample=list(islice(op.unresolved, MAX_DIAGNOSTIC_IDS)))
                if op.effects:
                    finding("REDISPATCH_AFTER_COMMIT", line, key, attempt_id,
                            "An effect was already recorded; another write needs external deduplication.")
                if op.epoch is None and "authorization_epoch" in event:
                    raise TraceError(line, "dispatch references an unrecorded authorization grant")
                if op.revoked:
                    finding("DISPATCH_AFTER_REVOCATION", line, key, attempt_id,
                            "Write dispatched while this operation's recorded authorization is revoked.")
                elif op.epoch is not None and event.get("authorization_epoch") != op.epoch:
                    finding("STALE_OR_MISSING_AUTHORIZATION", line, key, attempt_id,
                            "Write does not reference the current recorded authorization epoch.")
                op.unresolved[attempt_id] = None
            op.attempts[attempt_id] = Attempt(event["access"], line)
            continue

        if attempt_id not in op.attempts:
            raise TraceError(line, "event references an attempt not yet dispatched")
        attempt = op.attempts[attempt_id]
        if kind == "cancel_requested":
            attempt.cancel_requested = True
        elif kind == "wait_cancelled":
            attempt.wait_cancelled = True
        elif kind == "read_completed":
            if attempt.access != "read":
                raise TraceError(line, "read_completed must reference a read attempt")
            attempt.read_completed = True
        elif kind == "protocol_observed":
            # Wire/API facts are not application effect receipts. They must never
            # resolve outcomes or mutate cancellation, access, or authorization.
            attempt.protocol_observations.append(dict(
                event_id=event_id, line=line, observation=event["observation"],
                evidence_id=event["evidence_id"],
            ))
        elif kind == "outcome_observed":
            if attempt.access != "write":
                raise TraceError(line, "outcome evidence must reference the original write attempt")
            outcome = event["outcome"]
            if outcome == "unknown":
                continue  # Absence of new evidence cannot erase an established outcome.
            if attempt.outcome != "unknown" and attempt.outcome != outcome:
                raise TraceError(line, "contradictory confirmed outcomes for one attempt")
            attempt.outcome = outcome
            op.unresolved.pop(attempt_id, None)
            attempt.evidence_ids[event["evidence_id"]] = None
            if outcome == "committed":
                effect_id = event["effect_id"]
                attempt.effect_ids.add(effect_id)
                if effect_id not in op.effects:
                    op.effects[effect_id] = None
                    if len(op.effects) > 1:
                        finding("MULTIPLE_RECORDED_EFFECTS", line, key, attempt_id,
                                "Trace records distinct effect IDs for one logical operation; verify duplicate effects.",
                                severity="error", recorded_effect_count=len(op.effects),
                                effect_id_sample=list(islice(op.effects, MAX_DIAGNOSTIC_IDS)))

    summaries = {}
    for key, op in sorted(operations.items()):
        attempts = {}
        for attempt_id, state in sorted(op.attempts.items()):
            if (state.access == "write" and state.outcome == "unknown"
                    and (state.cancel_requested or state.wait_cancelled)):
                finding("CANCELLED_OUTCOME_UNRESOLVED", state.line, key, attempt_id,
                        "Cancellation does not establish that no external effect occurred.")
            attempts[attempt_id] = dict(access=state.access, outcome=(state.outcome if state.access == "write" else None),
                                        cancel_requested=state.cancel_requested, wait_cancelled=state.wait_cancelled,
                                        read_completed=state.read_completed, evidence_ids=list(state.evidence_ids),
                                        effect_ids=sorted(state.effect_ids),
                                        protocol_observations=state.protocol_observations)
        summaries[key] = dict(authorization_state=("untracked" if op.epoch is None else
                                                   "revoked" if op.revoked else "active"),
                              authorization_epoch=op.epoch, effect_ids=sorted(op.effects), attempts=attempts)
    findings.sort(key=lambda item: (item["line"], item["code"], item["operation_key"], item["attempt_id"]))
    return dict(schema_version=2, status="findings" if findings else "no_findings",
                scope="Advisory analysis of supplied evidence only; not a safety or authorization guarantee.",
                events=len(events), unique_events=len(seen), replayed_events=replayed,
                findings=findings, operations=summaries)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", help="UTF-8 JSONL file, or - for stdin")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    try:
        if args.trace == "-":
            data = sys.stdin.buffer.read(MAX_BYTES + 1)
        else:
            with Path(args.trace).open("rb") as stream:
                data = stream.read(MAX_BYTES + 1)
        result = analyze(data)
    except (OSError, TraceError) as exc:
        error = dict(status="data_error", message=str(exc))
        if isinstance(exc, TraceError):
            error["line"] = exc.line
        if args.format == "json":
            print(json.dumps(error, ensure_ascii=True, sort_keys=True))
        else:
            print(f"Data error: {exc}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    else:
        print(f"{len(result['findings'])} finding(s) in supplied trace; {result['unique_events']} unique event(s).")
        for item in result["findings"]:
            print(f"line {item['line']}: {item['code']} [{ascii(item['operation_key'])}/{ascii(item['attempt_id'])}] {item['message']}")
        print(result["scope"])
    return 1 if result["findings"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
