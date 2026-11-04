"""Read-only joins of exported measurement records; no local runner state needed."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from benchmarks.adapters.claude_tokens import DEFAULT_COUNT_MODEL
from benchmarks.agentic.count import (
    CountItem,
    _anthropic_method,
    _raw_arguments,
    count_item_from_transcript,
)
from benchmarks.agentic.count_evidence import planned_count_calls, validate_count_fields
from benchmarks.agentic.inputs import json_mapping
from benchmarks.agentic.protocol import KeyLeakError, scanned_json_bytes
from benchmarks.grading.execution_evidence import grading_execution_matches
from benchmarks.report_files import artifact_bytes, optional_json
from benchmarks.runner import BenchmarkValidationError


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,240}", value):
        raise ValueError("invalid measurement coordinate")
    return value


def _digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return scanned_json_bytes(value, where="measurement evidence", indent=2, sort_keys=True) + b"\n"


def grading_receipt_matches(
    root: Path,
    vote: dict[str, Any],
    digest: str,
    judge: dict[str, Any],
    invocation: dict[str, Any],
    events: list[dict[str, Any]],
) -> bool:
    """Use the host's durable execution history, bound by the invocation manifest."""
    run = _identifier(vote["grading_run_id"])
    judge_id, item = _identifier(vote["judge_id"]), _identifier(vote["item_id"])
    attempt = vote["attempt"]
    if type(attempt) is not int or attempt < 1:
        return False
    relative = f"{judge_id}/{item}--a{attempt}.json"
    path = root / "grading/executions" / run / relative
    body = artifact_bytes(root, path)
    try:
        history = json_mapping(body, "grading execution history")
    except KeyLeakError:
        raise
    except BenchmarkValidationError:
        return False
    receipts = invocation["execution_results"]
    if (
        receipts.get(relative) != _digest(body)
        or type(invocation["records_written"]) is not int
        or len(receipts) != invocation["records_written"]
    ):
        return False
    try:
        return grading_execution_matches(vote, digest, judge, invocation, events, history)
    except BenchmarkValidationError:
        return False


def live_count_matches(root: Path, transcript: dict[str, Any], token: dict[str, Any]) -> bool:
    """Bind a current projection to archived per-call counts and frozen inputs."""
    try:
        cell, qid = _identifier(transcript["competitor_id"]), _identifier(transcript["corpus_id"])
        family, system = (
            _identifier(transcript["model_family_id"]),
            _identifier(transcript["system_id"]),
        )
        prompt = artifact_bytes(root, root / "snapshots/system-prompt.txt").decode("utf-8")
        system_path = root / "systems" / f"{family}--{system}.json"
        system_body = artifact_bytes(root, system_path)
        info = json_mapping(system_body, "count system")
        try:
            item = count_item_from_transcript(
                transcript, system_prompt=prompt, tools_raw=info["tools_raw"]
            )
            validate_count_fields(
                token, item, _anthropic_method(DEFAULT_COUNT_MODEL).record_fields()
            )
        except BenchmarkValidationError:
            return False
        for position in item.calls:
            if (
                type(position) is not int
                or not 0 < position < len(item.messages)
                or item.messages[position]["role"] != "assistant"
                or item.messages[position - 1]["role"] != "user"
            ):
                return False
        if item.calls != sorted(set(item.calls)):
            return False
        for call, position in zip(
            (c for c in transcript["model_calls"] if c.get("tool_calls_requested") is not None),
            item.calls,
            strict=True,
        ):
            uses = sum(b.get("type") == "tool_use" for b in item.messages[position]["content"])
            raw = _raw_arguments(transcript["provider"], call.get("raw_response"))
            if (
                type(call["tool_calls_requested"]) is not int
                or uses != call["tool_calls_requested"]
                or len(raw) != uses
            ):
                return False
        if token["status"] != "counted":
            return False
        transcript_body = artifact_bytes(root, root / "transcripts" / cell / f"{qid}.json")
        if json_mapping(transcript_body, "count transcript") != transcript:
            return False
        frozen = {
            "item": asdict(item),
            "transcript_sha256": _digest(transcript_body),
            "system_sha256": _digest(system_body),
        }
        key = _digest(f"{cell}/{qid}".encode())
        inputs = optional_json(root, root / "grading/count-inputs.json") or {}
        if inputs.get(key) != frozen or token.get("input_sha256") != _digest(_json_bytes(frozen)):
            return False
        run = _identifier(token["count_run_id"])
        relative = f"{cell}/{qid}.json"
        archived = artifact_bytes(root, root / "grading/counts" / run / relative)
        current = artifact_bytes(root, root / "tokens" / relative)
        receipt = optional_json(root, root / "grading/count-runs" / f"{run}.json") or {}
        planned = 0
        for relative_result, digest in receipt.get("results", {}).items():
            parts = relative_result.split("/")
            if len(parts) != 2 or not parts[1].endswith(".json"):
                return False
            rcell, rqid = _identifier(parts[0]), _identifier(parts[1][:-5])
            result_body = artifact_bytes(root, root / "grading/counts" / run / relative_result)
            row = json_mapping(result_body, "archived count result")
            assigned = inputs[_digest(f"{rcell}/{rqid}".encode())]
            counted_item = CountItem(**assigned["item"])
            try:
                validate_count_fields(
                    row, counted_item, _anthropic_method(DEFAULT_COUNT_MODEL).record_fields()
                )
            except BenchmarkValidationError:
                return False
            if (
                row.get("status") != "counted"
                or row.get("count_run_id") != run
                or row.get("input_sha256") != _digest(_json_bytes(assigned))
                or (counted_item.cell, counted_item.corpus_id) != (rcell, rqid)
                or digest != _digest(result_body)
            ):
                return False
            planned += planned_count_calls(counted_item)
        return bool(
            archived == current
            and json_mapping(current, "count projection") == token
            and receipt.get("count_run_id") == run
            and receipt.get("dry_run") is False
            and receipt.get("status") == "completed"
            and receipt.get("not_run") == []
            and receipt.get("worker_errors") == []
            and receipt.get("units") == []
            and type(receipt.get("records_written")) is int
            and receipt.get("records_written") == len(receipt.get("results", {}))
            and type(receipt.get("items")) is int
            and receipt["items"] == receipt["records_written"]
            and type(receipt.get("count_calls")) is int
            and receipt["count_calls"] == planned
            and receipt.get("results", {}).get(relative) == _digest(archived)
        )
    except KeyLeakError:
        raise
    except BenchmarkValidationError:
        raise  # Unsafe artifact reads are errors, not missing measurements.
    except (FileNotFoundError, ValueError, KeyError, TypeError, AttributeError, IndexError):
        return False
