"""One agentic trajectory: caps, clock, retries, bounds and the record (design §6, §10, §12).

The trajectory clock counts successful work only: model attempts that returned
and tool calls that completed. Grant waits, failed attempts and retry backoff
are recorded apart and never cut a trajectory (owner ruling 6). No call is cut
in flight: each attempt has its own timeout, and a fire is a retried
``timeout``, booked at twice its bound.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Any, Literal, Protocol

from benchmarks.agentic.bridge import BridgeCrash, ToolBridge, truncate_utf8
from benchmarks.agentic.ledger import Outcome
from benchmarks.agentic.ledger import _utc_now as _utc_now_ms  # wall_s needs sub-second times
from benchmarks.agentic.prompt import SYSTEM_PROMPT
from benchmarks.agentic.schemas import schemas_sha256
from benchmarks.agentic.types import (
    Buckets,
    Conversation,
    ModelClient,
    ModelTurn,
    ProviderCallError,
    ToolResult,
    ToolResults,
    UserText,
)
from benchmarks.model_matrix import ModelFamily
from benchmarks.runner import BenchmarkValidationError


@dataclass(frozen=True)
class Caps:
    model_calls: int = 8
    tool_calls: int = 12
    tool_call_s: float = 30.0
    trajectory_s: float = 300.0
    result_bytes: int = 100_000
    model_call_timeout_s: float = 300.0  # = trajectory_s: a long legitimate answer is never cut


HOSTED_CAPS = Caps()
AIR_GAPPED_CAPS = Caps(trajectory_s=900.0, model_call_timeout_s=600.0)
MAX_RETRIES = 4
MAX_RETRY_AFTER_S = 120.0
RETRYABLE = frozenset({"rate_limited", "server", "timeout", "connection"})


class LedgerPort(Protocol):
    async def reserve(
        self,
        *,
        provider: str,
        model_id: str,
        input_bound: int,
        output_bound: int,
        kind: str = "model_call",
    ) -> str | None:
        """A unit id, or None when the provider's cap refuses the unit."""

    async def settle(self, unit_id: str, *, outcome: Outcome, buckets: Buckets | None) -> None:
        """Book the unit's outcome and free the provider."""


class TrajectoryDiscarded(Exception):
    """A budget stop or exhausted retries: discarded, never scored (design §12, #177)."""

    def __init__(
        self, reason: Literal["budget_stop", "retries_exhausted"], record: dict[str, Any]
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.record = record  # the partial record, for audit only


class _Failed(Exception):
    """Ends the trajectory as a scored failure."""

    def __init__(self, category: str, message: str, kind: str = "CapReached") -> None:
        super().__init__(message)
        self.category = category
        self.kind = kind


def full_input_bound(client: ModelClient, conv: Conversation, framing: int) -> int:
    # +1: the system/tools block
    return client.rendered_bytes(conv, 0) + framing * (len(conv.entries) + 1)


def next_input_bound(
    previous: Buckets, *, added_bytes: int, added_entries: int, framing: int
) -> int:
    return previous.total_input + previous.output + added_bytes + framing * added_entries


def backoff_s(retry: int, retry_after_s: float | None) -> float | None:
    """Seconds before 1-based ``retry``; None when the provider asks for more than 120 s."""
    if retry_after_s is not None:
        return None if retry_after_s > MAX_RETRY_AFTER_S else retry_after_s
    return min(60.0, 2.0**retry)


def canonical_messages(conv: Conversation) -> list[dict[str, Any]]:
    """Anthropic-Messages shape, visible content only (no thinking or reasoning)."""
    messages: list[dict[str, Any]] = []
    for entry in conv.entries:
        if isinstance(entry, UserText):
            messages.append({"role": "user", "content": entry.text})
        elif isinstance(entry, ToolResults):
            content = [
                {
                    "type": "tool_result",
                    "tool_use_id": r.call_id,
                    "content": r.text,
                    "is_error": r.is_error,
                }
                for r in entry.results
            ]
            messages.append({"role": "user", "content": content})
        else:
            blocks: list[dict[str, Any]] = []
            if entry.text or not entry.tool_calls:
                blocks.append({"type": "text", "text": entry.text})
            blocks += [
                {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                for c in entry.tool_calls
            ]
            messages.append({"role": "assistant", "content": blocks})
    return messages


def _usage(turn: ModelTurn) -> Buckets | None:
    """The turn's usage, or None when none, or only zeros, was reported."""
    return None if turn.buckets is None or turn.buckets.is_empty else turn.buckets


def _ms(seconds: float) -> float:
    return round(seconds * 1000, 3)


class _Trajectory:
    def __init__(
        self,
        client: ModelClient,
        bridge: ToolBridge | None,
        family: ModelFamily,
        prompt: str,
        ledger: LedgerPort,
        caps: Caps,
        seen_tool_args: set[str],
        sleep: Callable[[float], Awaitable[None]],
    ) -> None:
        if family.max_output_tokens is None or family.framing_allowance_tokens is None:
            raise BenchmarkValidationError(
                f"model family {family.id!r} needs max_output_tokens and "
                "framing_allowance_tokens for its spend bounds"
            )
        self.client, self.bridge, self.ledger, self.caps = client, bridge, ledger, caps
        self.seen, self.sleep = seen_tool_args, sleep
        self.output_bound = family.max_output_tokens
        self.framing = family.framing_allowance_tokens
        tools = list(bridge.tools) if bridge is not None else []
        self.conv = Conversation(SYSTEM_PROMPT, tools, [UserText(prompt)])
        self.model_calls: list[dict[str, Any]] = []
        self.tool_calls: list[dict[str, Any]] = []
        self.seconds: dict[str, float] = dict.fromkeys(
            ("used", "model", "tool", "grant_wait", "failed_attempt", "retry_wait"), 0.0
        )
        self.started_at = _utc_now_ms()
        self.started = time.perf_counter()

    def record(self, status: str, error: dict[str, str] | None, answer: str) -> dict[str, Any]:
        wall = time.perf_counter() - self.started
        s = self.seconds
        overhead = wall - sum(s[k] for k in s if k != "used")
        return {
            "status": status,
            "error": error,
            "answer": answer,
            "system_prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
            "tools_sha256": schemas_sha256([asdict(t) for t in self.conv.tools]),
            "canonical_messages": canonical_messages(self.conv),
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "timing": {
                "wall_ms": _ms(wall),
                **{f"{k}_ms": _ms(v) for k, v in s.items()},
                "overhead_ms": _ms(max(0.0, overhead)),
            },
            "started_at": self.started_at,
            "completed_at": _utc_now_ms(),
        }

    def discard(
        self, reason: Literal["budget_stop", "retries_exhausted"], message: str
    ) -> TrajectoryDiscarded:
        error = {"category": reason, "type": "TrajectoryDiscarded", "message": message}
        return TrajectoryDiscarded(reason, self.record("discarded", error, ""))

    def use(self, seconds: float, kind: str) -> None:
        """Count one unit of successful work; over the allowance ends the trajectory."""
        self.seconds["used"] += seconds
        self.seconds[kind] += seconds
        if self.seconds["used"] > self.caps.trajectory_s:
            raise _Failed(
                "cap_trajectory_time",
                f"successful work reached {self.seconds['used']:.3f}s, over the "
                f"{self.caps.trajectory_s}s trajectory allowance",
            )

    async def run(self) -> str:
        previous: Buckets | None = None
        previous_count = 0
        while True:
            if previous is None:  # first call, or the last call reported no usage
                bound = full_input_bound(self.client, self.conv, self.framing)
            else:
                count = len(self.conv.entries)
                bound = next_input_bound(
                    previous,
                    added_bytes=self.client.rendered_bytes(self.conv, previous_count),
                    added_entries=count - previous_count,
                    framing=self.framing,
                )
            previous_count = len(self.conv.entries)
            turn = await self.model_call(bound)
            previous = _usage(turn)  # None: the next bound is the full request again
            if not turn.tool_calls:
                return turn.text
            if len(self.model_calls) >= self.caps.model_calls:
                raise _Failed(
                    "cap_model_calls",
                    f"model call {len(self.model_calls)} asked for tools; the cap is "
                    f"{self.caps.model_calls} model calls",
                )
            await self.run_tools(turn)

    async def model_call(self, bound: int) -> ModelTurn:
        entry: dict[str, Any] = {
            "index": len(self.model_calls),
            "message_count": len(self.conv.entries),
            "request_params": self.client.request_params(),
            "input_bound": bound,
            "output_bound": self.output_bound,
            "attempts": [],
            "raw_response": None,
            "raw_usage": None,
            "buckets": None,
            "reported_model": None,
            "stop_reason": None,
            "wall_ms": None,
            "tool_calls_requested": None,  # 0 marks an answer turn
        }
        self.model_calls.append(entry)
        retries = 0
        while True:
            waited = time.perf_counter()
            unit = await self.ledger.reserve(
                provider=self.client.provider,
                model_id=self.client.model_id,
                input_bound=bound,
                output_bound=self.output_bound,
            )
            waited = time.perf_counter() - waited
            self.seconds["grant_wait"] += waited
            if unit is None:
                raise self.discard("budget_stop", f"the {self.client.provider} spend cap stopped")
            started = time.perf_counter()
            turn: ModelTurn | None = None
            error: ProviderCallError | None = None
            timer = asyncio.timeout(self.caps.model_call_timeout_s)
            try:
                async with timer:
                    turn = await self.client.call(self.conv)
            except ProviderCallError as exc:
                error = exc
            except TimeoutError:
                if not timer.expired():
                    raise
                error = ProviderCallError(
                    "timeout", f"model call exceeded its {self.caps.model_call_timeout_s}s timeout"
                )
            wall = time.perf_counter() - started
            attempt = {"wall_ms": _ms(wall), "wait_ms": _ms(waited), "unit_id": unit}
            entry["attempts"].append(attempt)
            if turn is not None:
                outcome: Outcome = "ok" if _usage(turn) else "no_usage"
                attempt |= {"outcome": outcome, "status": None}
                await self.ledger.settle(unit, outcome=outcome, buckets=_usage(turn))
                entry |= {
                    "raw_response": turn.raw_response,
                    "raw_usage": turn.raw_usage,
                    "buckets": None if turn.buckets is None else asdict(turn.buckets),
                    "reported_model": turn.reported_model,
                    "stop_reason": turn.stop_reason,
                    "wall_ms": _ms(wall),
                    "tool_calls_requested": len(turn.tool_calls),
                }
                self.conv.entries.append(turn)  # kept even when the clock now ends the trajectory
                self.use(wall, "model")
                return turn
            assert error is not None
            attempt |= {"outcome": error.kind, "status": error.status}
            await self.ledger.settle(unit, outcome=error.kind, buckets=None)
            self.seconds["failed_attempt"] += wall
            if error.kind not in RETRYABLE:
                raise _Failed("provider_rejected", str(error), "ProviderCallError")
            retries += 1
            delay = backoff_s(retries, error.retry_after_s) if retries <= MAX_RETRIES else None
            if delay is None:
                raise self.discard(
                    "retries_exhausted",
                    f"{error.kind} after {retries} attempts: {error}",
                )
            await self.sleep(delay)
            self.seconds["retry_wait"] += delay

    async def run_tools(self, turn: ModelTurn) -> None:
        results: list[ToolResult] = []
        try:
            await self._run_tools(turn, results)
        finally:  # results that came back stay in the record, even when a cap ends the turn
            if results:
                self.conv.entries.append(ToolResults(tuple(results)))

    async def _run_tools(self, turn: ModelTurn, results: list[ToolResult]) -> None:
        for call in turn.tool_calls:
            if len(self.tool_calls) >= self.caps.tool_calls:
                raise _Failed(
                    "cap_tool_calls", f"tool call {len(self.tool_calls) + 1} is over the cap"
                )
            entry: dict[str, Any] = {
                "index": len(self.tool_calls),
                "model_call_index": len(self.model_calls) - 1,
                "tool": call.name,
                "arguments": call.arguments,
                "raw_arguments": call.raw_arguments,
                "warm": False,
            }
            self.tool_calls.append(entry)
            wall = 0.0
            if call.raw_arguments is not None:  # never sent to the bridge (ruling B7)
                text, is_error = f"tool arguments must be a JSON object: {call.raw_arguments}", True
            elif self.bridge is None:
                text, is_error = f"unknown tool {call.name!r}: no tools are available", True
            else:
                key = (
                    f"{call.name}:{json.dumps(call.arguments, sort_keys=True, ensure_ascii=False)}"
                )
                entry["warm"] = key in self.seen
                started = time.perf_counter()
                timer = asyncio.timeout(self.caps.tool_call_s)
                try:
                    async with timer:
                        outcome = await self.bridge.call(call.name, call.arguments)
                except TimeoutError:
                    if not timer.expired():
                        raise
                    raise _Failed(
                        "cap_tool_timeout",
                        f"{call.name} did not complete within {self.caps.tool_call_s}s",
                        "TimeoutError",
                    ) from None
                except BridgeCrash as exc:
                    raise _Failed("mcp_protocol_crash", str(exc), "BridgeCrash") from exc
                wall = time.perf_counter() - started
                self.seen.add(key)
                text, is_error = outcome.text, outcome.is_error
            sent, truncated = truncate_utf8(text, self.caps.result_bytes)
            entry |= {
                "result": text,
                "result_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "is_error": is_error,
                "truncated": truncated,
                "wall_ms": _ms(wall),
            }
            results.append(ToolResult(call.id, call.name, sent, is_error))
            self.use(wall, "tool")


async def run_trajectory(
    *,
    client: ModelClient,
    bridge: ToolBridge | None,
    family: ModelFamily,
    prompt: str,
    ledger: LedgerPort,
    caps: Caps,
    seen_tool_args: set[str],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict[str, Any]:
    """Run one trajectory to an answer or a scored failure; raise TrajectoryDiscarded."""
    trajectory = _Trajectory(client, bridge, family, prompt, ledger, caps, seen_tool_args, sleep)
    try:
        answer = await trajectory.run()
    except _Failed as failure:
        error = {"category": failure.category, "type": failure.kind, "message": str(failure)}
        return trajectory.record("failed", error, "")
    return trajectory.record("succeeded", None, answer)
