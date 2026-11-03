"""One agentic trajectory: caps, clock, retries, bounds, records (design §6, §10, §12)."""

from __future__ import annotations

import asyncio
import hashlib
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from benchmarks.agentic.bridge import BridgeCrash, ToolOutcome
from benchmarks.agentic.clients import ScriptedClient
from benchmarks.agentic.ledger import (
    Ledger,
    LedgerService,
    ModelPrice,
    Rates,
    Tier,
    load_price_snapshot,
)
from benchmarks.agentic.loop import (
    Caps,
    TrajectoryDiscarded,
    backoff_s,
    next_input_bound,
    run_trajectory,
)
from benchmarks.agentic.prompt import SYSTEM_PROMPT
from benchmarks.agentic.schemas import schemas_sha256
from benchmarks.agentic.types import Buckets, Conversation, ModelTurn, ToolSpec
from benchmarks.model_matrix import ModelFamily, load_model_matrix, model_family_from_raw

REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_MATRIX_PATH = REPO_ROOT / "docs" / "benchmarks" / "model-matrix.yml"
PRICE_SNAPSHOT_PATH = REPO_ROOT / "docs" / "benchmarks" / "price-snapshot.yml"
RUN_ID = "loop-test"
PROMPT = "How do I parse JSON in Python 3.13?"
FAKE_TEXT = "The json module encodes and decodes JSON."
D = Decimal

USAGE = {"input_tokens": 100, "output_tokens": 10}
SEARCH = {"name": "search_docs", "arguments": {"query": "json"}}
TOOL = {"tools": [SEARCH], "usage": USAGE}
ANSWER = {"text": "Scripted answer.", "usage": USAGE}
FAMILY = model_family_from_raw(
    {
        "id": "scripted-anthropic",
        "provider": "anthropic",
        "model_id": "scripted-1",
        "client": "scripted",
        "token_count_method": "claude_normalized_payload",
        "latency_scope": "request_dispatch_to_final_answer",
        "headline_eligible": False,
        "headline_ineligibility_reason": "test",
        "api": "scripted",
        "arm": "hosted",
        "max_input_tokens": 200_000,
        "max_output_tokens": 1024,
        "output_cap_includes_reasoning": True,
        "framing_allowance_tokens": 256,
    }
)
FLAT = ModelPrice(
    "scripted-1", "anthropic", (Tier(None, Rates(D(1), D(1), D(1), D(1))),), "https://x.invalid"
)


class FakeBridge:
    """Canned tool outcomes; "no_such_tool" answers as an unknown tool does."""

    def __init__(
        self,
        names: tuple[str, ...] = ("search_docs",),
        *,
        text: str = FAKE_TEXT,
        delay: float = 0.0,
        crash: bool = False,
    ) -> None:
        self.tools = [ToolSpec(name, "d", {"type": "object"}) for name in names]
        self.text, self.delay, self.crash = text, delay, crash
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append((name, arguments))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.crash:
            raise BridgeCrash("the server died")
        text, is_error = (
            (f"Unknown tool: {name}", True) if name == "no_such_tool" else (self.text, False)
        )
        return ToolOutcome(text, is_error)


class RecordingLedger:
    """Records every reserve's keyword arguments, then forwards to the real service."""

    def __init__(self, inner: LedgerService, reserve_delay: float = 0.0) -> None:
        self.inner, self.reserve_delay = inner, reserve_delay
        self.reserves: list[dict[str, Any]] = []

    async def reserve(self, **kwargs: Any) -> str | None:
        self.reserves.append(kwargs)
        if self.reserve_delay:
            await asyncio.sleep(self.reserve_delay)
        return await self.inner.reserve(**kwargs)

    async def settle(self, unit_id: str, **kwargs: Any) -> None:
        await self.inner.settle(unit_id, **kwargs)


class FixedBytesClient(ScriptedClient):
    def __init__(self, sizes: dict[tuple[int, int], int], *args: Any) -> None:
        super().__init__(*args)
        self.sizes = sizes

    def rendered_bytes(self, conv: Conversation, start: int) -> int:
        return self.sizes[(start, len(conv.entries))]


class ToolsSeenClient(ScriptedClient):
    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self.tools_seen: list[list[ToolSpec]] = []

    async def call(self, conv: Conversation) -> ModelTurn:
        self.tools_seen.append(list(conv.tools))
        return await super().call(conv)


class Harness:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.sleeps: list[float] = []
        self.ledgers: list[Ledger] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    async def run(
        self,
        steps: list[dict[str, Any]] | None = None,
        *,
        client: ScriptedClient | None = None,
        bridge: Any = "fake",
        caps: Caps = Caps(),
        family: ModelFamily = FAMILY,
        price: ModelPrice = FLAT,
        ledger_caps: dict[str, Decimal] | None = None,
        seen: set[str] | None = None,
        reserve_delay: float = 0.0,
    ) -> dict[str, Any]:
        self.ledger = Ledger.open(
            self.tmp_path / f"ledger-{len(self.ledgers)}",
            run_id=RUN_ID,
            caps=ledger_caps or {"anthropic": D(180), "openai": D(65), "google": D(180)},
        )
        self.ledgers.append(self.ledger)
        service = LedgerService(self.ledger, {family.model_id: price}, {family.model_id: family})
        self.recording = RecordingLedger(service, reserve_delay)
        self.bridge = FakeBridge() if bridge == "fake" else bridge
        return await run_trajectory(
            client=client or ScriptedClient(family.provider, family.model_id, steps or []),
            bridge=self.bridge,
            family=family,
            prompt=PROMPT,
            ledger=self.recording,
            caps=caps,
            seen_tool_args=set() if seen is None else seen,
            sleep=self.sleep,
        )

    def reserve_bounds(self) -> list[Decimal]:
        return [D(e["bound"]) for e in self.ledger.events(RUN_ID) if e["event"] == "reserve"]

    def settles(self) -> list[dict[str, Any]]:
        return [e for e in self.ledger.events(RUN_ID) if e["event"] == "settle"]


@pytest.fixture
def harness(tmp_path: Path):
    h = Harness(tmp_path)
    yield h
    for ledger in h.ledgers:
        ledger.close()


def _outcomes(record: dict[str, Any], index: int = 0) -> list[str]:
    return [a["outcome"] for a in record["model_calls"][index]["attempts"]]


async def test_trajectory_calls_a_tool_then_answers_and_records_every_call(
    harness: Harness,
) -> None:
    record = await harness.run([TOOL, ANSWER])
    assert record["status"] == "succeeded" and record["answer"] == "Scripted answer."
    assert record["error"] is None
    assert [c["tool"] for c in record["tool_calls"]] == ["search_docs"]
    assert (
        record["tool_calls"][0]["result"] == FAKE_TEXT and record["tool_calls"][0]["warm"] is False
    )
    assert record["model_calls"][1]["message_count"] == 3
    assert record["canonical_messages"][0] == {"role": "user", "content": PROMPT}
    assert record["canonical_messages"][1]["content"][0]["type"] == "tool_use"
    assert record["canonical_messages"][2]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": record["canonical_messages"][1]["content"][0]["id"],
        "content": FAKE_TEXT,
        "is_error": False,
    }
    assert record["system_prompt_sha256"] == hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()
    assert record["model_calls"][0]["request_params"] == {"model": "scripted-1"}
    assert record["model_calls"][0]["buckets"] == {
        "uncached_input": 100,
        "cache_write_input": 0,
        "cached_input": 0,
        "output": 10,
        "reasoning": 0,
    }
    assert set(record["timing"]) == {
        "wall_ms",
        "used_ms",
        "model_ms",
        "tool_ms",
        "grant_wait_ms",
        "failed_attempt_ms",
        "retry_wait_ms",
        "overhead_ms",
    }
    assert record["started_at"] and record["completed_at"]


async def test_reservations_carry_independently_computed_bounds(harness: Harness) -> None:
    # OpenAI family: max_output 32_768, max_input 1_017_232, the committed gpt-6.1-sol rates.
    (family,) = [
        f for f in load_model_matrix(MODEL_MATRIX_PATH).model_families if f.id == "gpt-6.1-sol"
    ]
    price = load_price_snapshot(PRICE_SNAPSHOT_PATH).models["gpt-6.1-sol"]
    openai_usage = {
        "input_tokens": 1000,
        "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
        "output_tokens": 50,
    }
    steps = [
        {"tools": [SEARCH], "usage": openai_usage},  # call 0
        {"error": {"kind": "server", "status": 500}},  # call 1, attempt 1
        {"tools": [SEARCH], "usage": None},  # call 1, retry
        {"text": "done", "usage": openai_usage},  # call 2, after the call without usage
    ]
    sizes = {(0, 1): 500, (1, 3): 300, (0, 5): 2_000}
    client = FixedBytesClient(sizes, "openai", "gpt-6.1-sol", steps)
    record = await harness.run(client=client, family=family, price=price)
    assert record["status"] == "succeeded"
    # Every expected value is plain arithmetic here, not a call to the loop's helpers:
    #   call 0:  500 + 256 * 2                       = 1_012
    #   call 1:  1_000 + 50 + 300 + 256 * 2          = 1_862   (the retry reserves the same)
    #   call 2:  2_000 + 256 * (5 + 1)               = 3_536   (full request after no usage)
    #   dollars: input * $2.50 / 1M (cache-write rate, short tier) + 32_768 * $10 / 1M
    assert [r["input_bound"] for r in harness.recording.reserves] == [1_012, 1_862, 1_862, 3_536]
    assert [r["output_bound"] for r in harness.recording.reserves] == [32_768] * 4
    assert harness.reserve_bounds() == [D("0.33021"), D("0.332335"), D("0.332335"), D("0.33652")]
    # a fake that reserved a fixed 200_000 input tokens would book 0.82768 for call 0 and fail here
    # The call without usage is recorded and booked as no_usage, at twice its bound.
    assert _outcomes(record, 1) == ["server", "no_usage"]
    third = harness.settles()[2]
    assert third["outcome"] == "no_usage" and D(third["booked"]) == 2 * D("0.332335")


async def test_ninth_model_call_is_a_cap_failure(harness: Harness) -> None:
    record = await harness.run([TOOL] * 9 + [ANSWER])
    assert record["status"] == "failed"
    assert record["error"]["category"] == "cap_model_calls" and len(record["model_calls"]) == 8


async def test_thirteenth_tool_call_is_a_cap_failure(harness: Harness) -> None:
    five = {"tools": [SEARCH] * 5, "usage": USAGE}
    record = await harness.run([five, five, five, ANSWER])
    assert record["error"]["category"] == "cap_tool_calls" and len(record["tool_calls"]) == 12


async def test_slow_tool_call_ends_the_trajectory(harness: Harness) -> None:
    record = await harness.run(
        [TOOL, ANSWER], bridge=FakeBridge(delay=0.2), caps=Caps(tool_call_s=0.05)
    )
    assert record["status"] == "failed" and record["error"]["category"] == "cap_tool_timeout"


async def test_successful_work_over_the_allowance_is_a_cap_failure(harness: Harness) -> None:
    record = await harness.run([{**TOOL, "delay_s": 0.25}, ANSWER], caps=Caps(trajectory_s=0.1))
    assert record["error"]["category"] == "cap_trajectory_time"
    assert len(record["model_calls"]) == 1 and len(harness.recording.reserves) == 1


async def test_a_late_answer_is_still_recorded(harness: Harness) -> None:
    late = {"text": "late", "delay_s": 0.25, "stop_reason": "max_tokens", "usage": USAGE}
    record = await harness.run([late], caps=Caps(trajectory_s=0.1))
    assert record["error"]["category"] == "cap_trajectory_time"
    assert record["canonical_messages"][-1] == {
        "role": "assistant",
        "content": [{"type": "text", "text": "late"}],
    }
    assert record["model_calls"][0]["tool_calls_requested"] == 0


async def test_late_tool_results_are_still_recorded(harness: Harness) -> None:
    record = await harness.run(
        [TOOL, ANSWER], bridge=FakeBridge(delay=0.25), caps=Caps(trajectory_s=0.1)
    )
    assert record["error"]["category"] == "cap_trajectory_time"
    assert record["canonical_messages"][-1]["content"][0]["content"] == FAKE_TEXT
    assert record["model_calls"][0]["tool_calls_requested"] == 1


async def test_grant_wait_longer_than_the_trajectory_allowance_still_succeeds(
    harness: Harness,
) -> None:
    record = await harness.run([TOOL, ANSWER], caps=Caps(trajectory_s=0.2), reserve_delay=0.3)
    assert record["status"] == "succeeded" and record["timing"]["grant_wait_ms"] >= 300
    assert record["timing"]["used_ms"] < 200


async def test_failed_attempt_longer_than_the_remaining_allowance_is_retried(
    harness: Harness,
) -> None:
    slow_error = {"delay_s": 0.5, "error": {"kind": "server", "status": 500}}
    record = await harness.run([slow_error, ANSWER], caps=Caps(trajectory_s=0.3))
    assert record["status"] == "succeeded"
    assert _outcomes(record) == ["server", "ok"]
    assert record["timing"]["failed_attempt_ms"] >= 500


async def test_attempt_over_its_own_timeout_is_a_retried_timeout(harness: Harness) -> None:
    slow = {"delay_s": 1, "text": "too late", "usage": USAGE}
    record = await harness.run([slow, ANSWER], caps=Caps(model_call_timeout_s=0.1))
    assert record["status"] == "succeeded" and _outcomes(record) == ["timeout", "ok"]
    first = harness.settles()[0]
    assert first["outcome"] == "timeout"
    assert D(first["booked"]) == 2 * harness.reserve_bounds()[0]  # booked at 2x its bound
    assert harness.sleeps == [2.0]


async def test_result_over_the_cap_is_truncated_and_the_loop_continues(harness: Harness) -> None:
    record = await harness.run([TOOL, ANSWER], caps=Caps(result_bytes=10))
    call = record["tool_calls"][0]
    assert call["truncated"] is True and record["status"] == "succeeded"
    assert call["result"] == FAKE_TEXT  # the full canonical text is kept
    sent = record["canonical_messages"][2]["content"][0]["content"]
    assert sent == FAKE_TEXT[:10] + "\n[benchmark harness: tool result truncated at 10 UTF-8 bytes]"


async def test_unknown_tool_call_is_returned_to_the_model_and_the_loop_continues(
    harness: Harness,
) -> None:
    unknown = {"tools": [{"name": "no_such_tool", "arguments": {}}], "usage": USAGE}
    bridge = FakeBridge(("search_docs", "no_such_tool"))
    record = await harness.run([unknown, ANSWER], bridge=bridge)
    assert record["tool_calls"][0]["is_error"] is True and record["status"] == "succeeded"
    assert record["canonical_messages"][2]["content"][0]["is_error"] is True


@pytest.mark.parametrize("arguments", [[1, 2], "x", 3, None, True])
async def test_non_object_tool_arguments_are_returned_to_the_model(
    harness: Harness, arguments: Any
) -> None:
    bad = {"tools": [{"name": "search_docs", "arguments": arguments}], "usage": USAGE}
    record = await harness.run([bad, ANSWER])
    assert record["tool_calls"][0]["is_error"] is True and harness.bridge.calls == []
    assert record["tool_calls"][0]["result"].startswith("tool arguments must be a JSON object")
    assert record["status"] == "succeeded"


async def test_rate_limit_is_retried_honouring_retry_after_and_not_counted_as_a_model_call(
    harness: Harness,
) -> None:
    limited = {"error": {"kind": "rate_limited", "status": 429, "retry_after_s": 7}}
    record = await harness.run([limited, ANSWER])
    assert harness.sleeps == [7.0] and len(record["model_calls"]) == 1
    assert _outcomes(record) == ["rate_limited", "ok"]
    assert record["model_calls"][0]["attempts"][0]["status"] == 429
    assert record["timing"]["retry_wait_ms"] == 7000


async def test_a_503_with_retry_after_waits_that_long_before_its_retry(harness: Harness) -> None:
    import httpx2

    from benchmarks.agentic.clients import AnthropicClient, anthropic_sdk

    answer = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "scripted-1",
        "content": [{"type": "text", "text": "ok"}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 10},
    }
    replies = [
        httpx2.Response(503, json={"type": "error"}, headers={"retry-after": "90"}),
        httpx2.Response(200, json=answer),
    ]
    sdk = anthropic_sdk("test-key", 30, transport=httpx2.MockTransport(lambda _: replies.pop(0)))
    record = await harness.run(client=AnthropicClient(sdk, "scripted-1", 1024))  # type: ignore[arg-type]
    assert harness.sleeps == [90.0] and _outcomes(record) == ["server", "ok"]


async def test_server_error_is_booked_at_twice_the_bound_and_retried(harness: Harness) -> None:
    record = await harness.run([{"error": {"kind": "server", "status": 500}}, ANSWER])
    assert record["status"] == "succeeded" and _outcomes(record) == ["server", "ok"]
    first, second = harness.reserve_bounds()
    assert first == second  # the retry reuses the failed attempt's bound
    assert harness.ledger.spent("anthropic") == 2 * first + D("0.00011")  # + (100 + 10) * $1/1M


async def test_retries_exhausted_discards_the_trajectory(harness: Harness) -> None:
    with pytest.raises(TrajectoryDiscarded) as caught:
        await harness.run([{"error": {"kind": "server", "status": 500}}] * 5)
    assert caught.value.reason == "retries_exhausted" and harness.sleeps == [2, 4, 8, 16]
    assert caught.value.record["status"] == "discarded"
    assert len(caught.value.record["model_calls"][0]["attempts"]) == 5


async def test_rejected_request_is_a_scored_failure_booked_at_zero(harness: Harness) -> None:
    record = await harness.run([{"error": {"kind": "rejected", "status": 400}}])
    assert record["status"] == "failed" and record["error"]["category"] == "provider_rejected"
    assert harness.ledger.spent("anthropic") == 0 and harness.sleeps == []


async def test_budget_stop_discards_the_trajectory(harness: Harness) -> None:
    with pytest.raises(TrajectoryDiscarded) as caught:
        await harness.run([TOOL, ANSWER], ledger_caps={"anthropic": D("0")})
    assert caught.value.reason == "budget_stop" and caught.value.record["model_calls"]


async def test_repeated_tool_arguments_are_marked_warm(harness: Harness) -> None:
    seen: set[str] = set()
    first = await harness.run([TOOL, ANSWER], seen=seen)
    second = await harness.run([TOOL, ANSWER], seen=seen)
    assert first["tool_calls"][0]["warm"] is False and second["tool_calls"][0]["warm"] is True
    assert seen == {'search_docs:{"query": "json"}'}


async def test_empty_final_text_is_an_empty_answer(harness: Harness) -> None:
    record = await harness.run([{"text": "", "usage": USAGE}])
    assert record["status"] == "succeeded" and record["answer"] == ""


async def test_bridge_crash_fails_the_trajectory_as_a_protocol_crash(harness: Harness) -> None:
    record = await harness.run([TOOL, ANSWER], bridge=FakeBridge(crash=True))
    assert record["status"] == "failed" and record["error"]["category"] == "mcp_protocol_crash"
    assert record["error"]["type"] == "BridgeCrash"


async def test_no_docs_sends_no_tools(harness: Harness) -> None:
    client = ToolsSeenClient("anthropic", "scripted-1", [TOOL, ANSWER])
    record = await harness.run(client=client, bridge=None)
    assert client.tools_seen == [[]] and record["tool_calls"] == []
    assert record["answer"] == "Scripted answer."
    assert record["tools_sha256"] == schemas_sha256([])


def test_input_bounds() -> None:
    assert (
        next_input_bound(Buckets(100, 0, 20, 30), added_bytes=500, added_entries=2, framing=256)
        == 1162
    )


def test_backoff() -> None:
    assert [backoff_s(r, None) for r in (1, 2, 3, 4)] == [2, 4, 8, 16]
    assert backoff_s(1, 7.0) == 7.0 and backoff_s(1, 121.0) is None
    assert backoff_s(1, 0.0) == 0.0
    assert backoff_s(10, None) == 60


def test_system_prompt_follows_the_design() -> None:
    assert (
        "ground your answer in the official python documentation; if a documentation tool is "
        "available, use it before answering and cite the section url you relied on"
    ) in SYSTEM_PROMPT.lower()
    for name in ("python-docs", "docs-fetch", "context7", "mcp", "fetch"):
        assert name not in SYSTEM_PROMPT.lower()
