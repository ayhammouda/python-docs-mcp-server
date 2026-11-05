"""Validate a counter result against its assigned canonical trajectory.

Shared by exported-bundle readers and the count producer's result boundary.
No filesystem, worker, inference ledger or report dependencies belong here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from benchmarks.agentic.loop import MAX_RETRIES
from benchmarks.runner import BenchmarkValidationError


@dataclass(frozen=True)
class CountItem:
    cell: str
    corpus_id: str
    tool_model_key: str
    system: str
    tools: list[dict[str, Any]] | None
    messages: list[dict[str, Any]]  # canonical, ids normalized, raw arguments restored
    calls: list[int]  # message_count of each model call that returned a turn
    stubbed_tools: list[str]
    uncounted_calls: int  # calls that returned no turn (a rejected request)
    raw_argument_calls: int = 0
    system_prompt_sha256: str = ""
    tools_sha256: str = ""


def is_count_coordinate(value: Any) -> bool:
    """One non-hidden path component, at most 240 ASCII characters.

    Use the runner's _SAFE_ID character set, preserving legacy leading dashes
    and underscores. Run IDs and provider item IDs have separate grammars.
    """
    return (
        isinstance(value, str)
        and re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,239}", value) is not None
    )


def planned_count_calls(item: CountItem) -> int:
    """Endpoint requests before retries, including the payload diagnostics."""
    calls = sum(1 + 2 * bool(item.messages[p]["content"]) for p in item.calls)
    payload = any(
        b.get("type") == "tool_result"
        for m in item.messages
        if m["role"] == "user" and isinstance(m["content"], list)
        for b in m["content"]
    )
    return calls + 2 * payload


def validate_count_fields(record: Any, item: CountItem, method: dict[str, Any]) -> None:
    """The emitted count shape, including zero-output and retry accounting."""
    expected = {
        "competitor_id": item.cell,
        "corpus_id": item.corpus_id,
        "tool_model_key": item.tool_model_key,
        "system_prompt_sha256": item.system_prompt_sha256,
        "tools_sha256": item.tools_sha256,
        **method,
        "ids_normalized": True,
        "stubbed_tools": item.stubbed_tools,
        "uncounted_calls": item.uncounted_calls,
        "raw_argument_calls": item.raw_argument_calls,
    }
    if not isinstance(record, dict) or any(
        type(record.get(k)) is not type(v) or record.get(k) != v for k, v in expected.items()
    ):
        raise BenchmarkValidationError("count result disagrees with assigned item")
    for field in ("count_calls", "negative_output_clamped"):
        if type(record.get(field)) is not int or record[field] < 0:
            raise BenchmarkValidationError("invalid count result counter")
    if not isinstance(record.get("counted_at"), str) or not record["counted_at"]:
        raise BenchmarkValidationError("missing count provenance")
    rows = record.get("per_call")
    if not isinstance(rows, list):
        raise BenchmarkValidationError("invalid per-call counts")
    if record.get("status") == "failed":
        if (
            rows
            or record.get("client_wrapped_tokens") is not None
            or record.get("raw_payload_tokens") is not None
            or not isinstance(record.get("error"), str)
        ):
            raise BenchmarkValidationError("invalid failed count result")
        return
    if (
        record.get("status") != "counted"
        or record.get("error") is not None
        or len(rows) != len(item.calls)
    ):
        raise BenchmarkValidationError("invalid counted result")
    for index, row in enumerate(rows):
        if (
            not isinstance(row, dict)
            or set(row) != {"index", "input_tokens", "output_tokens"}
            or any(type(v) is not int or v < 0 for v in row.values())
            or row["index"] != index
        ):
            raise BenchmarkValidationError("invalid per-call counts")
        if not item.messages[item.calls[index]]["content"] and row["output_tokens"] != 0:
            raise BenchmarkValidationError("empty output must count as zero")
    total, payload = record.get("client_wrapped_tokens"), record.get("raw_payload_tokens")
    planned = planned_count_calls(item)
    zero_outputs = sum(
        not row["output_tokens"] and bool(item.messages[p]["content"])
        for row, p in zip(rows, item.calls, strict=True)
    )
    has_payload = any(
        b.get("type") == "tool_result"
        for m in item.messages
        if m["role"] == "user" and isinstance(m["content"], list)
        for b in m["content"]
    )
    if (
        type(total) is not int
        or total != sum(r["input_tokens"] + r["output_tokens"] for r in rows)
        or type(payload) is not int
        or payload < 0
        or (not has_payload and payload != 0)
        or not planned <= record["count_calls"] <= planned * (MAX_RETRIES + 1)
        or record["negative_output_clamped"] > zero_outputs
    ):
        raise BenchmarkValidationError("invalid count result totals")
