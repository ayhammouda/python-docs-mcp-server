"""Provider clients for the agentic loop (design §6, §8, §12; #177 item 1).

Every HTTP exchange goes through a mock transport; no test reaches a network host.
"""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from typing import Any

import httpx
import httpx2
import pytest
import yaml

from benchmarks.adapters.guard import PAID_KEY_ENVS, PROVIDER_API_KEY_ENV, LiveProviderDisabledError
from benchmarks.agentic.clients import (
    AnthropicClient,
    GeminiClient,
    OpenAIResponsesClient,
    ScriptedClient,
    anthropic_sdk,
    gemini_sdk,
    make_client,
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
from benchmarks.model_matrix import ModelFamily, load_model_matrix, model_family_from_raw
from benchmarks.runner import BenchmarkValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_MATRIX_PATH = REPO_ROOT / "docs" / "benchmarks" / "model-matrix.yml"
FIXTURES = Path(__file__).parent / "fixtures" / "agentic"
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

    def transport(self) -> Any:
        return httpx2.MockTransport(self)

    def body(self, index: int) -> dict[str, Any]:
        return json.loads(self.seen[index].content)


def _ok(body: dict[str, Any], status: int = 200, headers: dict[str, str] | None = None):
    return httpx2.Response(status, json=body, headers=headers or {})


def _anthropic(recorder: Recorder) -> AnthropicClient:
    sdk = anthropic_sdk("test-key", 30, transport=recorder.transport())
    return AnthropicClient(sdk, "claude-sonnet-5-5", 32_768)


def _openai(recorder: Recorder) -> OpenAIResponsesClient:
    sdk = openai_sdk("test-key", 30, transport=recorder.transport())
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


async def test_openai_failed_response_is_a_server_error() -> None:
    body = _openai_response([], status="failed")
    body["error"] = {"code": "server_error", "message": "the model failed"}
    recorder = Recorder(_ok(body))
    with pytest.raises(ProviderCallError) as caught:
        await _openai(recorder).call(_conv())
    assert caught.value.kind == "server" and "the model failed" in str(caught.value)


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


def test_parse_retry_after_clamps_a_negative_value_to_zero() -> None:
    assert parse_retry_after({"retry-after": "-5"}) == 0.0
    assert parse_retry_after({"retry-after-ms": "-1500"}) == 0.0


@pytest.mark.parametrize("provider", ["anthropic", "openai", "google"])
async def test_a_retryable_5xx_carries_its_retry_after(provider: str) -> None:
    error = {"error": {"code": 503, "message": "busy", "status": "UNAVAILABLE"}}
    body = error if provider == "google" else ERROR_BODY
    recorder = _recorder(provider, _response(provider, 503, body, {"retry-after": "90"}))
    with pytest.raises(ProviderCallError) as caught:
        await _live_client(provider, recorder).call(_conv())
    assert (caught.value.kind, caught.value.retry_after_s) == ("server", 90.0)


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


# --- Task 7: Gemini, scripted client, factory --------------------------------------------------

GEMINI_USAGE = {
    "promptTokenCount": 1000,
    "cachedContentTokenCount": 100,
    "toolUsePromptTokenCount": 50,
    "candidatesTokenCount": 30,
    "thoughtsTokenCount": 20,
}


def _gemini_response(parts: list[dict[str, Any]], finish: str = "STOP", **extra: Any):
    candidate = {"content": {"role": "model", "parts": parts}, "finishReason": finish, **extra}
    return {
        "candidates": [candidate],
        "usageMetadata": GEMINI_USAGE,
        "modelVersion": "gemini-3.8-flash-001",
    }


class GeminiRecorder(Recorder):
    def transport(self) -> Any:  # an httpx (not httpx2) transport: google-genai uses httpx
        return httpx.MockTransport(self)


def _gemini_ok(body: dict[str, Any], status: int = 200, headers: dict[str, str] | None = None):
    return httpx.Response(status, json=body, headers=headers or {})


def _gemini(recorder: GeminiRecorder) -> GeminiClient:
    sdk = gemini_sdk("test-key", 30, transport=recorder.transport())
    return GeminiClient(sdk, "gemini-3.8-flash", 32_768)


def _matrix_family(family_id: str) -> ModelFamily:
    families = load_model_matrix(MODEL_MATRIX_PATH).model_families
    (family,) = [f for f in families if f.id == family_id]
    return family


def _scripted_family(script: Path, provider: str = "anthropic") -> ModelFamily:
    return model_family_from_raw(
        {
            "id": "scripted-test",
            "provider": provider,
            "model_id": "scripted-1",
            "client": "scripted",
            "token_count_method": "claude_normalized_payload",
            "latency_scope": "request_dispatch_to_final_answer",
            "headline_eligible": False,
            "headline_ineligibility_reason": "test",
            "api": "scripted",
            "script": str(script),
        }
    )


def _write_script(path: Path, steps: list[dict[str, Any]]) -> Path:
    path.write_text(yaml.safe_dump({"steps": steps}), encoding="utf-8")
    return path


def test_gemini_client_makes_one_attempt_and_aiohttp_is_absent() -> None:
    sdk = gemini_sdk("test-key", 30)
    assert sdk._api_client._http_options.retry_options.attempts == 1
    # google-genai 2.31.0's aiohttp transport retries a connection error once whatever the
    # options say (_api_client.py); keep that transport out of the environment
    assert importlib.util.find_spec("aiohttp") is None


async def test_gemini_sends_one_request_when_the_response_asks_to_continue() -> None:
    body = _gemini_response([{"text": "partial"}], "CONTINUATION", continuationToken="dG9r")
    recorder = GeminiRecorder(_gemini_ok(body), _gemini_ok(body))
    turn = await _gemini(recorder).call(_conv())
    assert len(recorder.seen) == 1 and turn.stop_reason == "CONTINUATION"
    assert turn.text == "partial" and turn.tool_calls == ()


async def test_gemini_sends_one_request_on_a_server_error() -> None:
    error = {"error": {"code": 500, "message": "boom", "status": "INTERNAL"}}
    recorder = GeminiRecorder(_gemini_ok(error, 500), _gemini_ok(error, 500))
    with pytest.raises(ProviderCallError) as caught:
        await _gemini(recorder).call(_conv())
    assert caught.value.kind == "server" and len(recorder.seen) == 1


async def test_gemini_request_uses_auto_mode_and_disables_automatic_calling() -> None:
    call = {"functionCall": {"name": "search_docs", "args": {"query": "json"}}}
    recorder = GeminiRecorder(_gemini_ok(_gemini_response([call])))
    turn = await _gemini(recorder).call(_conv())
    body = recorder.body(0)
    assert body["toolConfig"]["functionCallingConfig"]["mode"] == "AUTO"
    assert body["generationConfig"]["maxOutputTokens"] == 32768
    assert body["systemInstruction"]["parts"] == [{"text": "system prompt"}]
    assert body["tools"][0]["functionDeclarations"][0]["name"] == "search_docs"
    assert body["contents"] == [{"role": "user", "parts": [{"text": "q?"}]}]
    assert turn.buckets == Buckets(950, 0, 100, 50, 20)
    assert turn.reported_model == "gemini-3.8-flash-001"
    assert turn.tool_calls == (ToolCall("call_1", "search_docs", {"query": "json"}),)


async def test_gemini_pairs_two_calls_of_the_same_function_by_position() -> None:
    calls = [
        {"text": "thinking", "thought": True, "thoughtSignature": "c2ln"},
        {"functionCall": {"name": "search_docs", "args": {"query": "a"}}},
        {"functionCall": {"name": "search_docs", "args": {"query": "b"}}},
    ]
    recorder = GeminiRecorder(
        _gemini_ok(_gemini_response(calls)), _gemini_ok(_gemini_response([{"text": "done"}]))
    )
    client = _gemini(recorder)
    conv = _conv()
    turn = await client.call(conv)
    assert [c.id for c in turn.tool_calls] == ["call_1", "call_2"] and turn.text == ""
    results = tuple(
        ToolResult(c.id, c.name, f"result {c.arguments['query']}", False) for c in turn.tool_calls
    )
    conv.entries += [turn, ToolResults(results)]
    await client.call(conv)
    contents = recorder.body(1)["contents"]
    assert contents[1] == {"role": "model", "parts": calls}  # replayed, signature included
    assert contents[-1]["parts"] == [
        {"functionResponse": {"name": "search_docs", "response": {"output": "result a"}}},
        {"functionResponse": {"name": "search_docs", "response": {"output": "result b"}}},
    ]


async def test_gemini_sends_an_error_result_under_error_and_keeps_a_provider_id() -> None:
    call = {"functionCall": {"id": "fc-9", "name": "search_docs", "args": {"query": "a"}}}
    recorder = GeminiRecorder(
        _gemini_ok(_gemini_response([call])), _gemini_ok(_gemini_response([{"text": "done"}]))
    )
    client = _gemini(recorder)
    conv = _conv()
    turn = await client.call(conv)
    conv.entries += [turn, ToolResults((ToolResult("fc-9", "search_docs", "bad", True),))]
    await client.call(conv)
    assert recorder.body(1)["contents"][-1]["parts"] == [
        {"functionResponse": {"id": "fc-9", "name": "search_docs", "response": {"error": "bad"}}}
    ]


@pytest.mark.parametrize("status,kind", [(429, "rate_limited"), (400, "rejected"), (500, "server")])
async def test_gemini_status_errors_are_classified(status: int, kind: str) -> None:
    error = {"error": {"code": status, "message": "boom", "status": "X"}}
    recorder = GeminiRecorder(_gemini_ok(error, status, {"retry-after": "3"}))
    with pytest.raises(ProviderCallError) as caught:
        await _gemini(recorder).call(_conv())
    assert caught.value.kind == kind and caught.value.status == status
    assert caught.value.retry_after_s == (None if status == 400 else 3.0)  # 429 and 5xx


async def test_gemini_timeout_and_connection_errors_are_classified() -> None:
    def raise_(error: type[Exception]):
        def handler(request: httpx.Request) -> httpx.Response:
            raise error("simulated", request=request)

        return handler

    recorder = GeminiRecorder(raise_(httpx.ReadTimeout), raise_(httpx.ConnectError))
    client = _gemini(recorder)
    kinds = []
    for _ in range(2):
        with pytest.raises(ProviderCallError) as caught:
            await client.call(_conv())
        kinds.append(caught.value.kind)
    assert kinds == ["timeout", "connection"] and len(recorder.seen) == 2


def _clear_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*PAID_KEY_ENVS, "BENCHMARK_LIVE_PROVIDERS_ENABLED"):
        monkeypatch.delenv(name, raising=False)


def test_make_client_refuses_a_live_api_without_the_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_keys(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    with pytest.raises(LiveProviderDisabledError):
        make_client(_matrix_family("claude-sonnet-5-5"), timeout_s=30)


def test_make_client_refuses_a_second_paid_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_keys(monkeypatch)
    monkeypatch.setenv("BENCHMARK_LIVE_PROVIDERS_ENABLED", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key-1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-a-real-key-2")
    with pytest.raises(BenchmarkValidationError, match="OPENAI_API_KEY") as caught:
        make_client(_matrix_family("claude-sonnet-5-5"), timeout_s=30)
    assert "not-a-real-key" not in str(caught.value)


def test_make_client_refuses_the_ollama_api_until_pr_4(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_keys(monkeypatch)
    with pytest.raises(BenchmarkValidationError, match="openai-chat-completions"):
        make_client(_matrix_family("gemma4-12b"), timeout_s=600)


def test_make_client_refuses_a_provider_that_does_not_match_its_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_keys(monkeypatch)
    monkeypatch.setenv("BENCHMARK_LIVE_PROVIDERS_ENABLED", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-a-real-key")
    family = replace(_matrix_family("claude-sonnet-5-5"), provider="openai")
    with pytest.raises(BenchmarkValidationError, match="anthropic-messages"):
        make_client(family, timeout_s=30)


@pytest.mark.parametrize(
    "family_id,client_type",
    [
        ("claude-sonnet-5-5", AnthropicClient),
        ("gpt-6.1-sol", OpenAIResponsesClient),
        ("gemini-3.8-flash", GeminiClient),
    ],
)
def test_make_client_builds_each_live_client_behind_the_guard(
    monkeypatch: pytest.MonkeyPatch, family_id: str, client_type: type
) -> None:
    _clear_keys(monkeypatch)
    family = _matrix_family(family_id)
    monkeypatch.setenv("BENCHMARK_LIVE_PROVIDERS_ENABLED", "1")
    monkeypatch.setenv(PROVIDER_API_KEY_ENV[family.provider], "not-a-real-key")
    client = make_client(family, timeout_s=30)  # builds an SDK object; sends nothing
    assert isinstance(client, client_type) and client.model_id == family.model_id


def test_make_client_builds_a_scripted_client_without_any_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_keys(monkeypatch)
    script = _write_script(tmp_path / "s.yml", [{"text": "hi", "usage": None}])
    client = make_client(_scripted_family(script), timeout_s=30)
    assert isinstance(client, ScriptedClient) and client.provider == "anthropic"
    with pytest.raises(BenchmarkValidationError, match="absolute"):
        make_client(_scripted_family(Path("s.yml")), timeout_s=30)


async def test_scripted_client_skips_tool_steps_for_tools_it_was_not_given() -> None:
    steps = [
        {
            "tools": [{"name": "search_docs", "arguments": {"query": "{prompt}"}}],
            "usage": {"input_tokens": 10, "output_tokens": 1},
        },
        {"tools": [{"name": "fetch", "arguments": [1]}], "usage": None},
        {"text": "Scripted answer.", "usage": {"input_tokens": 20, "output_tokens": 2}},
    ]
    client = ScriptedClient("anthropic", "scripted-1", steps)
    docs = _conv()
    first = await client.call(docs)
    assert first.tool_calls == (ToolCall("call_1", "search_docs", {"query": "q?"}),)
    assert first.buckets == Buckets(10, 0, 0, 1)
    answer = await client.call(docs)
    assert answer.text == "Scripted answer." and answer.stop_reason == "end_turn"
    no_docs = _conv(tools=[])
    answer = await client.call(no_docs)
    assert answer.text == "Scripted answer." and answer.tool_calls == ()
    fetch_only = _conv(tools=[ToolSpec("fetch", "d", {"type": "object"})])
    turn = await client.call(fetch_only)
    assert turn.tool_calls == (ToolCall("call_1", "fetch", {}, "[1]"),) and turn.buckets is None


async def test_scripted_client_counts_steps_per_conversation_object() -> None:
    client = ScriptedClient("openai", "scripted-2", [{"text": "one"}, {"text": "two"}])
    for _ in range(3):  # a fresh conversation always starts at the first step
        conv = _conv(tools=[])
        assert (await client.call(conv)).text == "one"
        assert (await client.call(conv)).stop_reason == "completed"
        del conv
    conv = _conv(tools=[])
    await client.call(conv)
    await client.call(conv)
    with pytest.raises(BenchmarkValidationError, match="no step 3"):
        await client.call(conv)


async def test_scripted_client_applies_a_step_only_to_the_prompt_it_names() -> None:
    steps = [
        {"text": "special", "prompt_contains": "magic", "stop_reason": "CONTINUATION"},
        {"text": "plain"},
    ]
    client = ScriptedClient("google", "scripted-3", steps)
    special = Conversation("s", [], [UserText("a magic question")])
    turn = await client.call(special)
    assert (turn.text, turn.stop_reason) == ("special", "CONTINUATION")
    assert (await client.call(_conv(tools=[]))).stop_reason == "STOP"


async def test_scripted_client_raises_a_scripted_provider_error() -> None:
    steps = [
        {"error": {"kind": "rate_limited", "status": 429, "retry_after_s": 0}},
        {"text": "after"},
    ]
    client = ScriptedClient("anthropic", "scripted-1", steps)
    conv = _conv()
    with pytest.raises(ProviderCallError) as caught:
        await client.call(conv)
    assert (caught.value.kind, caught.value.status, caught.value.retry_after_s) == (
        "rate_limited",
        429,
        0,
    )
    assert (await client.call(conv)).text == "after"


@pytest.mark.parametrize("provider", ["anthropic", "openai", "google"])
async def test_committed_scripts_play_tools_then_the_answer(provider: str) -> None:
    script = FIXTURES / f"script-{provider}.yml"
    client = make_client(_scripted_family(script, provider), timeout_s=30)
    conv = _conv(tools=[TOOLS[0], ToolSpec("fetch", "d", {"type": "object"})])
    turns = [await client.call(conv) for _ in range(3)]
    assert [c.name for t in turns for c in t.tool_calls] == ["search_docs", "fetch"]
    assert turns[-1].text == "Scripted answer." and all(t.buckets for t in turns)


# --- Round 2: redirects, pinned endpoints ------------------------------------------------------

ELSEWHERE = "https://elsewhere.invalid/collect"
OK_BODIES = {
    "anthropic": ANTHROPIC_ANSWER,
    "openai": _openai_response([OPENAI_MESSAGE]),
    "google": _gemini_response([{"text": "ok"}]),
}


def _live_client(provider: str, recorder: Recorder) -> Any:
    return {"anthropic": _anthropic, "openai": _openai, "google": _gemini}[provider](recorder)  # type: ignore[operator]


def _recorder(provider: str, *responses: Any) -> Recorder:
    return GeminiRecorder(*responses) if provider == "google" else Recorder(*responses)


def _response(provider: str, status: int, body: Any = None, headers: Any = None) -> Any:
    kind = httpx if provider == "google" else httpx2
    return kind.Response(status, json=body or {}, headers=headers or {})


@pytest.mark.parametrize("status", [307, 308])
@pytest.mark.parametrize("provider", ["anthropic", "openai", "google"])
async def test_a_redirect_is_never_followed_and_is_a_rejected_call(
    provider: str, status: int
) -> None:
    # Production client construction; the test injects only the transport.
    recorder = _recorder(
        provider,
        _response(provider, status, headers={"location": ELSEWHERE}),
        _response(provider, 200, OK_BODIES[provider]),
    )
    with pytest.raises(ProviderCallError) as caught:
        await _live_client(provider, recorder).call(_conv())
    assert (caught.value.kind, caught.value.status) == ("rejected", status)
    assert [r.method for r in recorder.seen] == ["POST"]
    assert "elsewhere.invalid" not in {r.url.host for r in recorder.seen}


@pytest.mark.parametrize(
    "family_id,env,host",
    [
        ("claude-sonnet-5-5", "ANTHROPIC_BASE_URL", "api.anthropic.com"),
        ("gpt-6.1-sol", "OPENAI_BASE_URL", "api.openai.com"),
        ("gemini-3.8-flash", "GOOGLE_GEMINI_BASE_URL", "generativelanguage.googleapis.com"),
    ],
)
async def test_a_base_url_variable_cannot_redirect_a_key(
    monkeypatch: pytest.MonkeyPatch, family_id: str, env: str, host: str
) -> None:
    _clear_keys(monkeypatch)
    family = _matrix_family(family_id)
    monkeypatch.setenv("BENCHMARK_LIVE_PROVIDERS_ENABLED", "1")
    monkeypatch.setenv(PROVIDER_API_KEY_ENV[family.provider], "not-a-real-key")
    monkeypatch.setenv(env, "https://attacker.invalid/v1")
    recorder = _recorder(
        family.provider, _response(family.provider, 200, OK_BODIES[family.provider])
    )
    client = make_client(family, timeout_s=30, transport=recorder.transport())
    await client.call(_conv())
    assert [r.url.host for r in recorder.seen] == [host]
