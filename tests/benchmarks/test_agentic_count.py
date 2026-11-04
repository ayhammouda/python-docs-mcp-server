"""Post-hoc normalized token counting: items, envelopes and count clients (PR 5 Task 9a).

Design §8; owner ruling 7. The run, worker and CLI parts (Task 9b) land with PR 4.
Every count goes to a scripted client; no test reaches a network host.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict
from typing import Any

import httpx2
import pytest
from mcp.types import CallToolResult, TextContent

from benchmarks.adapters.guard import PAID_KEY_ENVS, LiveProviderDisabledError
from benchmarks.agentic.clients import anthropic_sdk
from benchmarks.agentic.count import (
    AnthropicCountClient,
    Envelope,
    ScriptedCountClient,
    count_family,
    count_item,
    count_item_from_transcript,
    make_count_client,
)
from benchmarks.agentic.loop import Caps
from benchmarks.agentic.prompt import SYSTEM_PROMPT
from benchmarks.agentic.schemas import schemas_sha256
from benchmarks.agentic.types import ProviderCallError, ToolSpec
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks.test_agentic_clients import (
    Recorder,
    _chat_completion,
    _ok,
    _ollama,
    _tool_call,
)
from tests.benchmarks.test_agentic_outgoing import RecordingLedger, _bridge, _close, _run

TOOLS_RAW = [
    {
        "name": "search_docs",
        "description": "Search Python documentation.",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
    },
    {
        "name": "get_docs",
        "description": "Get a docs page.",
        "inputSchema": {"type": "object", "properties": {"slug": {"type": "string"}}},
    },
]
TOOLS_SHA = schemas_sha256(
    [asdict(ToolSpec(t["name"], t["description"], t["inputSchema"])) for t in TOOLS_RAW]
)
PROMPT_SHA = hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()
NO_TOOLS_SHA = schemas_sha256([])


def _transcript(
    provider: str,
    messages: list[dict[str, Any]],
    calls: list[dict[str, Any]],
    *,
    tools_sha: str = TOOLS_SHA,
    status: str = "succeeded",
) -> dict[str, Any]:
    return {
        "competitor_id": "python-docs--fam--r1",
        "corpus_id": "Q1",
        "tool_model_key": "python-docs:p/m",
        "provider": provider,
        "status": status,
        "answer": "",
        "system_prompt_sha256": PROMPT_SHA,
        "tools_sha256": tools_sha,
        "canonical_messages": messages,
        "model_calls": calls,
        "tool_calls": [],
    }


def _call(index: int, message_count: int, requested: int | None, raw: Any) -> dict[str, Any]:
    return {
        "index": index,
        "message_count": message_count,
        "tool_calls_requested": requested,
        "raw_response": raw,
    }


def _use(id_: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool_use", "id": id_, "name": name, "input": arguments}


def _result(id_: str, text: str = "result text") -> dict[str, Any]:
    return {"type": "tool_result", "tool_use_id": id_, "content": text, "is_error": False}


QUESTION = {"role": "user", "content": "What does json.dumps return?"}
TWO_CALL_TRANSCRIPT = _transcript(
    "anthropic",
    [
        QUESTION,
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Let me look."},
                _use("toolu_A", "search_docs", {"query": "dumps"}),
            ],
        },
        {"role": "user", "content": [_result("toolu_A", "json.dumps returns a str.")]},
        {"role": "assistant", "content": [{"type": "text", "text": "It returns a str."}]},
    ],
    [
        _call(0, 1, 1, {"content": [_use("toolu_A", "search_docs", {"query": "dumps"})]}),
        _call(1, 3, 0, {"content": [{"type": "text", "text": "It returns a str."}]}),
    ],
)
GEMINI_REPEATED_IDS = _transcript(
    "google",
    [
        QUESTION,
        {"role": "assistant", "content": [_use("call_1", "search_docs", {"query": "a"})]},
        {"role": "user", "content": [_result("call_1")]},
        {
            "role": "assistant",
            "content": [
                _use("call_1", "get_docs", {"slug": "x"}),
                _use("call_2", "fetch", {"url": "u"}),
            ],
        },
        {"role": "user", "content": [_result("call_1"), _result("call_2")]},
        {"role": "assistant", "content": [_use("call_1", "fetch", {"url": "v"})]},
        {"role": "user", "content": [_result("call_1")]},
        {"role": "assistant", "content": [{"type": "text", "text": "Done."}]},
    ],
    [
        _call(
            0,
            1,
            1,
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"function_call": {"name": "search_docs", "args": {"query": "a"}}}
                            ]
                        }
                    }
                ]
            },
        ),
        _call(
            1,
            3,
            2,
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"function_call": {"name": "get_docs", "args": {"slug": "x"}}},
                                {"function_call": {"name": "fetch", "args": {"url": "u"}}},
                            ]
                        }
                    }
                ]
            },
        ),
        _call(
            2,
            5,
            1,
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [{"function_call": {"name": "fetch", "args": {"url": "v"}}}]
                        }
                    }
                ]
            },
        ),
        _call(3, 7, 0, {"candidates": [{"content": {"parts": [{"text": "Done."}]}}]}),
    ],
)
NO_DOCS = _transcript(
    "openai",
    [QUESTION, {"role": "assistant", "content": [{"type": "text", "text": "A str."}]}],
    [_call(0, 1, 0, {"output": [{"type": "message", "content": []}]})],
    tools_sha=NO_TOOLS_SHA,
)
RAW = "x" * 4001  # not JSON
CAP_MODEL_CALLS = _transcript(
    "openai",
    [
        QUESTION,
        {"role": "assistant", "content": [_use("fc_1", "search_docs", {"query": "a"})]},
        {"role": "user", "content": [_result("fc_1")]},
        {"role": "assistant", "content": [_use("fc_2", "search_docs", {})]},
    ],
    [
        _call(
            0,
            1,
            1,
            {
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "fc_1",
                        "name": "search_docs",
                        "arguments": '{"query": "a"}',
                    }
                ]
            },
        ),
        _call(
            1,
            3,
            1,
            {
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "fc_2",
                        "name": "search_docs",
                        "arguments": RAW,
                    }
                ]
            },
        ),
    ],
    status="failed",
)
EMPTY_ANSWER = _transcript(
    "anthropic",
    [QUESTION, {"role": "assistant", "content": [{"type": "text", "text": ""}]}],
    [_call(0, 1, 0, {"content": [{"type": "text", "text": ""}]})],
)
REJECTED = _transcript(
    "anthropic",
    [
        QUESTION,
        {"role": "assistant", "content": [_use("toolu_A", "search_docs", {"query": "a"})]},
        {"role": "user", "content": [_result("toolu_A")]},
        {"role": "assistant", "content": [{"type": "text", "text": "Answer."}]},
    ],
    [
        _call(0, 1, 1, {"content": [_use("toolu_A", "search_docs", {"query": "a"})]}),
        _call(1, 3, None, None),  # a 400: the request returned no turn
        _call(2, 3, 0, {"content": [{"type": "text", "text": "Answer."}]}),
    ],
)
FIXTURES = [TWO_CALL_TRANSCRIPT, GEMINI_REPEATED_IDS, CAP_MODEL_CALLS, EMPTY_ANSWER, REJECTED]


def _item(transcript: dict[str, Any], tools_raw: list[dict[str, Any]] = TOOLS_RAW) -> Any:
    return count_item_from_transcript(transcript, system_prompt=SYSTEM_PROMPT, tools_raw=tools_raw)


async def test_envelopes_for_a_two_call_trajectory() -> None:
    item = _item(TWO_CALL_TRANSCRIPT)
    client = ScriptedCountClient()
    await count_item(client, item)
    inp, with_, stand = client.seen[0:3]  # call 0: input, output with, stand-in
    assert inp.messages == item.messages[:1] and inp.system == SYSTEM_PROMPT
    assert with_.messages[-1] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "c1_1", "content": "."}],
    }
    assert with_.messages[1] == item.messages[1]
    assert stand.messages[1]["content"] == [
        {"type": "tool_use", "id": "c1_1", "name": "search_docs", "input": {}}
    ]
    assert client.seen[3].messages == item.messages[:3]  # call 1's input
    answer_with, answer_stand = client.seen[4:6]
    assert answer_with.messages[-1] == {"role": "user", "content": "."}
    assert answer_stand.messages[3] == {
        "role": "assistant",
        "content": [{"type": "text", "text": "."}],
    }
    assert item.calls == [1, 3] and item.tools is not None
    assert [t["name"] for t in item.tools] == ["search_docs", "get_docs"]


async def test_every_envelope_ends_with_a_user_message() -> None:
    for transcript in [*FIXTURES, NO_DOCS]:
        tools_raw = [] if transcript is NO_DOCS else TOOLS_RAW
        client = ScriptedCountClient()
        record = await count_item(client, _item(transcript, tools_raw))
        assert record["status"] == "counted" and client.seen
        assert all(env.messages[-1]["role"] == "user" for env in client.seen)


def test_tool_ids_are_normalized_and_unique_across_turns() -> None:
    item = _item(GEMINI_REPEATED_IDS)
    uses = [
        b
        for m in item.messages
        if m["role"] == "assistant"
        for b in m["content"]
        if b["type"] == "tool_use"
    ]
    assert [u["id"] for u in uses] == ["c1_1", "c2_1", "c2_2", "c3_1"]
    results = [
        b["tool_use_id"]
        for m in item.messages
        if m["role"] == "user" and isinstance(m["content"], list)
        for b in m["content"]
    ]
    assert results == ["c1_1", "c2_1", "c2_2", "c3_1"]


def test_unknown_tool_names_get_one_name_only_stub() -> None:
    item = _item(GEMINI_REPEATED_IDS)
    assert item.stubbed_tools == ["fetch"]
    assert item.tools is not None
    assert item.tools[-1] == {"name": "fetch", "input_schema": {"type": "object"}}
    assert sum(t["name"] == "fetch" for t in item.tools) == 1


async def test_no_docs_envelopes_omit_tools() -> None:
    item = _item(NO_DOCS, [])
    assert item.tools is None and item.stubbed_tools == []
    client = ScriptedCountClient()
    await count_item(client, item)
    assert client.seen and all(env.tools is None for env in client.seen)
    assert "tools" not in AnthropicCountClient.request(client.seen[0])


async def test_malformed_arguments_are_counted_from_their_raw_text() -> None:
    item = _item(CAP_MODEL_CALLS)
    assert item.messages[3]["content"] == [
        {"type": "tool_use", "id": "c2_1", "name": "search_docs", "input": {"raw_arguments": RAW}}
    ]
    record = await count_item(ScriptedCountClient(), item)
    assert record["raw_argument_calls"] == 1 and record["per_call"][-1]["output_tokens"] >= 1_000


@pytest.mark.parametrize(
    "caps, results",
    [(Caps(model_calls=1), 0), (Caps(tool_calls=2, result_bytes=16), 2)],
)
async def test_count_ollama_arguments_from_a_capped_turn(caps: Caps, results: int) -> None:
    deep = '{"query":' + "[" * 65 + "0" + "]" * 65 + "}"
    arguments = ['{"query":"ordinary"}', RAW, "", deep]
    calls = [_tool_call("search_docs", arg, None) for arg in arguments]
    response = _chat_completion({"role": "assistant", "tool_calls": calls}, "tool_calls")
    client = _ollama(Recorder(_ok(response)))
    bridge, _ = _bridge(CallToolResult(content=[TextContent(type="text", text="ordinary " * 8)]))
    try:
        transcript = await _run(client, bridge, RecordingLedger(), caps=caps)
    finally:
        await _close(client)

    assert transcript["status"] == "failed" and len(transcript["tool_calls"]) == results
    assert all(call["truncated"] for call in transcript["tool_calls"])
    canonical = transcript["canonical_messages"][1]["content"]
    assert [block["input"] for block in canonical] == [{"query": "ordinary"}, {}, {}, {}]

    counter = ScriptedCountClient()
    item = _item({**transcript, "provider": client.provider}, bridge.tools_raw)
    record = await count_item(counter, item)
    blocks = counter.seen[1].messages[1]["content"]
    assert [block["input"] for block in blocks] == [
        {"query": "ordinary"},
        {"raw_arguments": RAW},
        {"raw_arguments": ""},
        {"raw_arguments": deep},
    ]
    assert record["raw_argument_calls"] == 3
    assert record["per_call"][0]["output_tokens"] >= 1_000


async def test_a_failed_trajectory_ending_in_tool_use_is_counted() -> None:
    item = _item(CAP_MODEL_CALLS)
    client = ScriptedCountClient()
    record = await count_item(client, item)
    assert record["status"] == "counted" and len(record["per_call"]) == 2
    closing = client.seen[4].messages[-1]  # call 1: input, with, stand-in
    assert closing == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "c2_1", "content": "."}],
    }


async def test_an_empty_answer_turn_counts_zero_output() -> None:
    item = _item(EMPTY_ANSWER)
    assert item.messages[1] == {"role": "assistant", "content": []}
    client = ScriptedCountClient()
    record = await count_item(client, item)
    assert record["per_call"] == [
        {"index": 0, "input_tokens": record["per_call"][0]["input_tokens"], "output_tokens": 0}
    ]
    assert len(client.seen) == 1


def test_a_rejected_call_is_listed_not_counted() -> None:
    item = _item(REJECTED)
    assert item.calls == [1, 3] and item.uncounted_calls == 1


def test_a_tools_hash_mismatch_is_a_harness_error() -> None:
    with pytest.raises(BenchmarkValidationError, match="tools_sha256"):
        _item(TWO_CALL_TRANSCRIPT, TOOLS_RAW[:1])
    with pytest.raises(BenchmarkValidationError, match="system_prompt_sha256"):
        count_item_from_transcript(TWO_CALL_TRANSCRIPT, system_prompt="other", tools_raw=TOOLS_RAW)


async def test_token_record_sums_inputs_and_outputs() -> None:
    item = _item(TWO_CALL_TRANSCRIPT)
    record = await count_item(ScriptedCountClient(), item)
    assert record["status"] == "counted"
    assert record["client_wrapped_tokens"] == sum(
        c["input_tokens"] + c["output_tokens"] for c in record["per_call"]
    )
    assert record["count_calls"] == 6 + 2  # 3 per call, then the docs payload and its baseline
    assert record["raw_payload_tokens"] > 0 and record["ids_normalized"] is True
    assert (record["competitor_id"], record["corpus_id"]) == ("python-docs--fam--r1", "Q1")
    assert record["count_model"] == "claude-sonnet-5-5" and record["approximation"] is False
    assert record["system_prompt_sha256"] == PROMPT_SHA and record["tools_sha256"] == TOOLS_SHA
    assert record["negative_output_clamped"] == 0 and record["error"] is None
    assert "estimate" in record["notes"]


def test_scripted_count_is_a_quarter_of_the_envelope_bytes() -> None:
    env = Envelope("s", None, [{"role": "user", "content": "hello"}])
    canonical = json.dumps(
        {"system": "s", "messages": env.messages},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    assert ScriptedCountClient.tokens(env) == math.ceil(len(canonical.encode()) / 4)


class _Failing:
    def __init__(self, kind: str = "rate_limited") -> None:
        self.kind, self.calls = kind, 0

    async def count(self, envelope: Envelope) -> int:
        self.calls += 1
        raise ProviderCallError(self.kind, "slow down AIza" + "A" * 35, status=429)


async def test_count_retries_then_marks_the_record_failed() -> None:
    client, sleeps = _Failing(), []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    record = await count_item(client, _item(TWO_CALL_TRANSCRIPT), sleep=sleep)
    assert record["status"] == "failed" and client.calls == 5 and len(sleeps) == 4
    assert record["client_wrapped_tokens"] is None and "AIza" not in record["error"]
    assert "rate_limited" in record["error"]
    rejected = _Failing("rejected")
    record = await count_item(rejected, _item(TWO_CALL_TRANSCRIPT), sleep=sleep)
    assert record["status"] == "failed" and rejected.calls == 1


async def test_the_anthropic_count_client_sends_the_envelope() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"input_tokens": 42})

    sdk = anthropic_sdk("test-key", 5.0, transport=httpx2.MockTransport(handler))
    client = AnthropicCountClient(sdk, "claude-sonnet-5-5")
    env = Envelope(
        "sys",
        [{"name": "t", "input_schema": {"type": "object"}}],
        [{"role": "user", "content": "q"}],
    )
    assert await client.count(env) == 42
    body = json.loads(seen[0].content)
    assert seen[0].url.path == "/v1/messages/count_tokens"
    assert body == {
        "model": "claude-sonnet-5-5",
        "system": "sys",
        "tools": env.tools,
        "messages": env.messages,
    }


def test_make_count_client_is_guarded(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in PAID_KEY_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("BENCHMARK_LIVE_PROVIDERS_ENABLED", raising=False)
    family = count_family()
    assert (family.provider, family.model_id, family.api) == (
        "anthropic",
        "claude-sonnet-5-5",
        "anthropic-messages",
    )
    with pytest.raises(LiveProviderDisabledError):
        make_count_client(family, timeout_s=5.0)
    monkeypatch.setenv("BENCHMARK_LIVE_PROVIDERS_ENABLED", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("GOOGLE_API_KEY", "other-key")
    with pytest.raises(BenchmarkValidationError, match="GOOGLE_API_KEY"):
        make_count_client(family, timeout_s=5.0)
    monkeypatch.delenv("GOOGLE_API_KEY")
    assert isinstance(make_count_client(family, timeout_s=5.0), AnthropicCountClient)
    for name in PAID_KEY_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("BENCHMARK_LIVE_PROVIDERS_ENABLED")


def test_a_scripted_count_family_needs_no_key(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in PAID_KEY_ENVS:
        monkeypatch.delenv(name, raising=False)
    script = tmp_path / "count.yml"
    script.write_text("errors: []\n")
    family = count_family(script)
    assert family.api == "scripted" and family.raw["script"] == str(script)
    assert isinstance(make_count_client(family, timeout_s=5.0), ScriptedCountClient)
