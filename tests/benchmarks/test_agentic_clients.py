"""Provider clients for the agentic loop (design §6, §8, §12; #177 item 1).

Every HTTP exchange goes through a mock transport; no test reaches a network host.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any

import httpx2
import pytest

from benchmarks.agentic.clients import (
    AnthropicClient,
    OpenAIResponsesClient,
    anthropic_sdk,
    openai_sdk,
    parse_retry_after,
    tool_arguments,
)
from benchmarks.agentic.types import (
    Buckets,
    Conversation,
    ModelTurn,
    ProviderCallError,
    ToolCall,
    ToolResult,
    ToolResults,
    ToolSpec,
    UserText,
)

TOOLS = [
    ToolSpec(
        "search_docs",
        "Search Python documentation.",
        {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    )
]

ANTHROPIC_TOOL_TURN = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-5-5",
    "content": [
        {"type": "thinking", "thinking": "t", "signature": "sig"},
        {"type": "tool_use", "id": "toolu_1", "name": "search_docs", "input": {"query": "json"}},
    ],
    "stop_reason": "tool_use",
    "stop_sequence": None,
    "usage": {
        "input_tokens": 100,
        "cache_creation_input_tokens": None,
        "cache_read_input_tokens": 20,
        "output_tokens": 30,
    },
}
ANTHROPIC_ANSWER = {
    **ANTHROPIC_TOOL_TURN,
    "id": "msg_2",
    "content": [{"type": "text", "text": "The answer."}],
    "stop_reason": "end_turn",
}


def _openai_response(
    output: list[dict[str, Any]], *, status: str = "completed", details: Any = None
) -> dict[str, Any]:
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 0,
        "status": status,
        "incomplete_details": details,
        "model": "gpt-6.1-sol",
        "output": output,
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "usage": {
            "input_tokens": 1000,
            "input_tokens_details": {"cached_tokens": 200, "cache_write_tokens": 300},
            "output_tokens": 50,
            "output_tokens_details": {"reasoning_tokens": 40},
            "total_tokens": 1050,
        },
    }


OPENAI_CALL = {
    "type": "function_call",
    "id": "fc_1",
    "call_id": "call_abc",
    "name": "search_docs",
    "arguments": '{"query": "json"}',
    "status": "completed",
}
OPENAI_REASONING = {
    "type": "reasoning",
    "id": "rs_1",
    "summary": [],
    "encrypted_content": "opaque",
}
OPENAI_MESSAGE = {
    "type": "message",
    "id": "msg_1",
    "role": "assistant",
    "status": "completed",
    "content": [{"type": "output_text", "text": "The answer.", "annotations": []}],
}
ERROR_BODY = {"type": "error", "error": {"type": "api_error", "message": "boom"}}


class Recorder:
    """A mock transport handler that records each request and plays canned responses."""

    def __init__(self, *responses: httpx2.Response | Callable[[httpx2.Request], Any]) -> None:
        self.responses = list(responses)
        self.seen: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.seen.append(request)
        response = self.responses.pop(0)
        if callable(response):
            return response(request)
        return response

    def client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(self))

    def body(self, index: int) -> dict[str, Any]:
        return json.loads(self.seen[index].content)


def _ok(body: dict[str, Any], status: int = 200, headers: dict[str, str] | None = None):
    return httpx2.Response(status, json=body, headers=headers or {})


def _anthropic(recorder: Recorder) -> AnthropicClient:
    sdk = anthropic_sdk("test-key", 30, http_client=recorder.client())
    return AnthropicClient(sdk, "claude-sonnet-5-5", 32_768)


def _openai(recorder: Recorder) -> OpenAIResponsesClient:
    sdk = openai_sdk("test-key", 30, http_client=recorder.client())
    return OpenAIResponsesClient(sdk, "gpt-6.1-sol", 32_768)


def _conv(tools: list[ToolSpec] | None = None) -> Conversation:
    return Conversation("system prompt", TOOLS if tools is None else tools, [UserText("q?")])


def test_sdk_clients_have_their_retries_off() -> None:
    assert anthropic_sdk("test-key", 30).max_retries == 0
    assert openai_sdk("test-key", 30).max_retries == 0
    assert anthropic_sdk("test-key", 30).timeout == 30


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
@pytest.mark.parametrize("status", [200, 500, 529])
async def test_each_client_sends_one_request_per_call(provider: str, status: int) -> None:
    good = ANTHROPIC_ANSWER if provider == "anthropic" else _openai_response([OPENAI_MESSAGE])
    recorder = Recorder(_ok(good if status == 200 else ERROR_BODY, status))
    client = _anthropic(recorder) if provider == "anthropic" else _openai(recorder)
    if status == 200:
        await client.call(_conv())
    else:
        with pytest.raises(ProviderCallError) as caught:
            await client.call(_conv())
        assert caught.value.kind == "server" and caught.value.status == status
    assert len(recorder.seen) == 1


async def test_anthropic_request_has_auto_tool_choice_and_no_sampling_parameters() -> None:
    recorder = Recorder(_ok(ANTHROPIC_TOOL_TURN))
    turn = await _anthropic(recorder).call(_conv())
    body = recorder.body(0)
    assert body["tool_choice"] == {"type": "auto"} and body["max_tokens"] == 32768
    assert body["model"] == "claude-sonnet-5-5" and body["system"] == "system prompt"
    assert body["tools"] == [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in TOOLS
    ]
    assert body["messages"] == [{"role": "user", "content": "q?"}]
    assert not {"temperature", "top_p", "top_k", "cache_control"} & set(body)
    assert turn.tool_calls == (ToolCall("toolu_1", "search_docs", {"query": "json"}),)
    assert turn.buckets == Buckets(100, 0, 20, 30, 0) and turn.reported_model == "claude-sonnet-5-5"
    assert turn.stop_reason == "tool_use" and turn.raw_usage is not None
    assert turn.raw_response["id"] == "msg_1"


async def test_anthropic_replays_the_assistant_content_unchanged() -> None:
    recorder = Recorder(_ok(ANTHROPIC_TOOL_TURN), _ok(ANTHROPIC_ANSWER))
    client = _anthropic(recorder)
    conv = _conv()
    turn = await client.call(conv)
    conv.entries += [turn, ToolResults((ToolResult("toolu_1", "search_docs", "found", False),))]
    answer = await client.call(conv)
    messages = recorder.body(1)["messages"]
    assert messages[1] == {"role": "assistant", "content": ANTHROPIC_TOOL_TURN["content"]}
    assert messages[2] == {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "found", "is_error": False}
        ],
    }
    assert answer.text == "The answer." and answer.tool_calls == ()


async def test_a_conversation_without_tools_sends_neither_tools_nor_tool_choice() -> None:
    recorder = Recorder(_ok(ANTHROPIC_ANSWER), _ok(_openai_response([OPENAI_MESSAGE])))
    await _anthropic(recorder).call(_conv(tools=[]))
    await _openai(recorder).call(_conv(tools=[]))
    for index in (0, 1):
        assert not {"tools", "tool_choice"} & set(recorder.body(index))


@pytest.mark.parametrize(
    "status,headers,kind,retry_after",
    [
        (429, {"retry-after": "7"}, "rate_limited", 7.0),
        (429, {"retry-after-ms": "1500"}, "rate_limited", 1.5),
        (400, {}, "rejected", None),
        (500, {}, "server", None),
        (529, {}, "server", None),
    ],
)
async def test_anthropic_status_errors_are_classified(
    status: int, headers: dict[str, str], kind: str, retry_after: float | None
) -> None:
    recorder = Recorder(_ok(ERROR_BODY, status, headers))
    with pytest.raises(ProviderCallError) as caught:
        await _anthropic(recorder).call(_conv())
    assert (caught.value.kind, caught.value.status, caught.value.retry_after_s) == (
        kind,
        status,
        retry_after,
    )


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
async def test_timeout_and_connection_errors_are_classified(provider: str) -> None:
    def raise_(error: type[Exception]):
        def handler(request: httpx2.Request) -> httpx2.Response:
            raise error("simulated", request=request)

        return handler

    recorder = Recorder(raise_(httpx2.ReadTimeout), raise_(httpx2.ConnectError))
    client = _anthropic(recorder) if provider == "anthropic" else _openai(recorder)
    kinds = []
    for _ in range(2):
        with pytest.raises(ProviderCallError) as caught:
            await client.call(_conv())
        kinds.append(caught.value.kind)
    assert kinds == ["timeout", "connection"] and len(recorder.seen) == 2


async def test_openai_responses_request_and_usage() -> None:
    recorder = Recorder(_ok(_openai_response([OPENAI_REASONING, OPENAI_CALL])))
    client = _openai(recorder)
    turn = await client.call(_conv())
    body = recorder.body(0)
    assert body["tool_choice"] == "auto" and body["store"] is False
    assert body["include"] == ["reasoning.encrypted_content"] and body["max_output_tokens"] == 32768
    assert body["instructions"] == "system prompt" and body["model"] == "gpt-6.1-sol"
    assert body["input"] == [{"role": "user", "content": "q?"}]
    assert all(t["strict"] is False for t in body["tools"]) and "temperature" not in body
    assert turn.buckets == Buckets(500, 300, 200, 50, 40)
    assert turn.tool_calls == (ToolCall("call_abc", "search_docs", {"query": "json"}),)
    assert turn.reported_model == "gpt-6.1-sol"


@pytest.mark.parametrize(
    "status,details,expected",
    [
        ("incomplete", {"reason": "max_output_tokens"}, "max_output_tokens"),
        ("completed", None, "completed"),
    ],
)
async def test_openai_stop_reason_is_normalized(status: str, details: Any, expected: str) -> None:
    recorder = Recorder(_ok(_openai_response([OPENAI_MESSAGE], status=status, details=details)))
    turn = await _openai(recorder).call(_conv())
    assert turn.stop_reason == expected


async def test_openai_request_never_asks_for_extended_cache_retention() -> None:
    recorder = Recorder(
        _ok(_openai_response([OPENAI_CALL])), _ok(_openai_response([OPENAI_MESSAGE]))
    )
    client = _openai(recorder)
    conv = _conv()
    turn = await client.call(conv)
    conv.entries += [turn, ToolResults((ToolResult("call_abc", "search_docs", "found", False),))]
    await client.call(conv)
    for body in (recorder.body(0), recorder.body(1), client.request_params()):
        assert not {"prompt_cache_options", "prompt_cache_retention"} & set(body)


async def test_openai_replays_output_items_and_sends_function_call_output() -> None:
    recorder = Recorder(
        _ok(_openai_response([OPENAI_REASONING, OPENAI_CALL])),
        _ok(_openai_response([OPENAI_MESSAGE])),
    )
    client = _openai(recorder)
    conv = _conv()
    turn = await client.call(conv)
    conv.entries += [turn, ToolResults((ToolResult("call_abc", "search_docs", "found", False),))]
    answer = await client.call(conv)
    assert recorder.body(1)["input"] == [
        {"role": "user", "content": "q?"},
        OPENAI_REASONING,
        OPENAI_CALL,
        {"type": "function_call_output", "call_id": "call_abc", "output": "found"},
    ]
    assert answer.text == "The answer." and answer.tool_calls == ()


@pytest.mark.parametrize("raw", ["{not json", "[1, 2]", "3", '"x"', "null", "true"])
async def test_openai_non_object_arguments_are_kept_raw(raw: str) -> None:
    recorder = Recorder(_ok(_openai_response([{**OPENAI_CALL, "arguments": raw}])))
    turn = await _openai(recorder).call(_conv())
    assert turn.tool_calls[0].arguments == {} and turn.tool_calls[0].raw_arguments == raw


@pytest.mark.parametrize(
    "value,expected",
    [
        ('{"a": 1}', ({"a": 1}, None)),
        ({"a": 1}, ({"a": 1}, None)),
        ([1], ({}, "[1]")),
        (None, ({}, "null")),
        ("[1]", ({}, "[1]")),
    ],
)
def test_tool_arguments(value: Any, expected: tuple[dict[str, Any], str | None]) -> None:
    assert tool_arguments(value) == expected


def test_parse_retry_after_accepts_an_http_date() -> None:
    when = format_datetime(datetime.now(UTC) + timedelta(seconds=30), usegmt=True)
    value = parse_retry_after({"retry-after": when})
    assert value is not None and 28 < value <= 30
    past = format_datetime(datetime.now(UTC) - timedelta(seconds=30), usegmt=True)
    assert parse_retry_after({"retry-after": past}) == 0
    assert parse_retry_after({"retry-after": "soon"}) is None
    assert parse_retry_after({}) is None


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_rendered_bytes_grows_with_each_entry(provider: str) -> None:
    client = _anthropic(Recorder()) if provider == "anthropic" else _openai(Recorder())
    conv = _conv()
    first = client.rendered_bytes(conv, 0)
    assert first > len("system prompt") + len("q?")
    turn = ModelTurn(
        text="",
        tool_calls=(),
        replay=ANTHROPIC_TOOL_TURN["content"]
        if provider == "anthropic"
        else [OPENAI_REASONING, OPENAI_CALL],
        raw_response={},
        raw_usage=None,
        buckets=None,
        reported_model=None,
        stop_reason=None,
    )
    conv.entries += [turn]
    second = client.rendered_bytes(conv, 0)
    conv.entries += [ToolResults((ToolResult("call_abc", "search_docs", "é" * 10, False),))]
    third = client.rendered_bytes(conv, 0)
    assert first < second < third
    assert client.rendered_bytes(conv, 2) < third - second + 100  # only the added entry
    assert client.rendered_bytes(conv, 2) >= 20  # "é" is two UTF-8 bytes
