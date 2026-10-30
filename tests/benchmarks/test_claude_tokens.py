"""Tests for the Claude count-tokens integration (issue #89).

Every test here uses :class:`~benchmarks.adapters.claude_tokens.FakeTokenCounter`
or a stubbed ``urllib.request.urlopen`` -- none ever calls the real Anthropic
API. The guard-refusal tests additionally monkeypatch ``urlopen`` to raise if
it is ever invoked, proving this module is structurally incapable of a
network call when the live-phase guard is not satisfied.
"""

from __future__ import annotations

import io
import json
import urllib.error
from typing import Any

import pytest

from benchmarks.adapters.claude_tokens import (
    FakeTokenCounter,
    LiveClaudeTokenCounter,
    build_client_wrapped_envelope,
    count_cell_tokens,
)
from benchmarks.adapters.guard import (
    LIVE_PROVIDERS_ENABLED_ENV,
    PROVIDER_API_KEY_ENV,
    LiveProviderDisabledError,
)
from benchmarks.runner import BenchmarkCellFailure

ANTHROPIC_KEY_ENV = PROVIDER_API_KEY_ENV["anthropic"]


# --- FakeTokenCounter --------------------------------------------------------


def test_fake_token_counter_returns_configured_fixed_count() -> None:
    counter = FakeTokenCounter(tokens_per_call=17)

    assert counter.count([{"role": "user", "content": "hi"}]) == 17
    assert counter.count([{"role": "user", "content": "something else entirely"}]) == 17
    assert len(counter.calls) == 2


def test_fake_token_counter_default_count_is_deterministic() -> None:
    counter = FakeTokenCounter()
    messages = [{"role": "user", "content": "one two three"}]

    assert counter.count(messages) == counter.count(messages)
    assert counter.count(messages) > 0


# --- Envelope construction / approximation marking --------------------------


def test_envelope_for_a_plain_prompt_is_the_prompt_alone() -> None:
    envelope, approximation = build_client_wrapped_envelope("what is pathlib?", tool_calls=None)

    assert approximation is True
    assert envelope == [{"role": "user", "content": "what is pathlib?"}]


def test_envelope_wraps_tool_call_results_as_tool_result_blocks() -> None:
    tool_calls = [
        {"tool": "search_docs", "result": {"hits": [{"slug": "x"}]}, "is_error": False},
        {"tool": "get_docs", "result": {"content": "docs"}, "is_error": False},
    ]

    envelope, approximation = build_client_wrapped_envelope("prompt", tool_calls=tool_calls)

    assert approximation is True
    assert envelope[0] == {"role": "user", "content": "prompt"}
    tool_result_blocks = envelope[2]["content"]
    assert len(tool_result_blocks) == 2
    assert all(block["type"] == "tool_result" for block in tool_result_blocks)
    assert "search_docs" in tool_result_blocks[0]["tool_use_id"]
    assert "get_docs" in tool_result_blocks[1]["tool_use_id"]


def test_envelope_is_approximation_when_built_from_a_provider_mock_payload() -> None:
    # MockOpenAIAdapter/MockGoogleAdapter (and their live-stub counterparts)
    # never call a real client SDK, so there is no real wrapped envelope to
    # recover -- the methodology requires marking that gap, not guessing.
    envelope, approximation = build_client_wrapped_envelope(
        "prompt", provider_mock_payload={"provider": "openai", "mock": True}
    )

    assert approximation is True
    assert envelope == [{"role": "user", "content": "prompt"}]


# --- count_cell_tokens: fake-counter records, approximation, latency -------


def test_count_cell_tokens_fills_client_wrapped_and_raw_payload_tokens() -> None:
    counter = FakeTokenCounter(tokens_per_call=25)

    result = count_cell_tokens(prompt="prompt", tool_calls=None, counter=counter)

    assert result.client_wrapped_tokens == 25
    assert result.raw_payload_tokens == 25
    assert result.approximation is True
    assert "system prompt" in result.notes
    # One counter call for the client-wrapped envelope, one for the raw
    # payload envelope -- they are always counted (and reported) separately.
    assert len(counter.calls) == 2


def test_count_cell_tokens_marks_approximation_for_provider_mock_payload() -> None:
    counter = FakeTokenCounter(tokens_per_call=9)

    result = count_cell_tokens(
        prompt="prompt",
        tool_calls=None,
        counter=counter,
        provider_mock_payload={"provider": "google", "mock": True},
    )

    assert result.approximation is True
    assert "approximation" in result.notes


def test_count_cell_tokens_captures_serialization_latency_alongside_counts() -> None:
    # Roadmap decision 5.8: report tokens AND serialization latency, not
    # tokens alone.
    counter = FakeTokenCounter(tokens_per_call=3)

    result = count_cell_tokens(prompt="prompt", tool_calls=None, counter=counter)

    assert isinstance(result.serialization_latency_ms, float)
    assert result.serialization_latency_ms >= 0


# --- LiveClaudeTokenCounter: guarded, stdlib-only, never in CI --------------


def test_live_claude_token_counter_refuses_without_config_and_never_touches_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(LIVE_PROVIDERS_ENABLED_ENV, raising=False)
    monkeypatch.delenv(ANTHROPIC_KEY_ENV, raising=False)

    def _network_must_not_be_called(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("urlopen must never be called when the live guard is disabled")

    monkeypatch.setattr("urllib.request.urlopen", _network_must_not_be_called)

    with pytest.raises(LiveProviderDisabledError):
        LiveClaudeTokenCounter().count([{"role": "user", "content": "prompt"}])


def test_live_claude_token_counter_refuses_with_flag_but_no_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(LIVE_PROVIDERS_ENABLED_ENV, "1")
    monkeypatch.delenv(ANTHROPIC_KEY_ENV, raising=False)

    def _network_must_not_be_called(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("network reached")

    # Fail closed: if the guard order ever regresses, this test must fail on
    # the stubbed network call, never actually reach the network.
    monkeypatch.setattr("urllib.request.urlopen", _network_must_not_be_called)

    with pytest.raises(LiveProviderDisabledError, match=ANTHROPIC_KEY_ENV):
        LiveClaudeTokenCounter().count([{"role": "user", "content": "prompt"}])


def test_live_claude_token_counter_refuses_with_key_but_no_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(LIVE_PROVIDERS_ENABLED_ENV, raising=False)
    monkeypatch.setenv(ANTHROPIC_KEY_ENV, "fake-not-a-real-key")

    def _network_must_not_be_called(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("network reached")

    # Fail closed: if the guard order ever regresses, this test must fail on
    # the stubbed network call, never actually reach the network.
    monkeypatch.setattr("urllib.request.urlopen", _network_must_not_be_called)

    with pytest.raises(LiveProviderDisabledError, match=LIVE_PROVIDERS_ENABLED_ENV):
        LiveClaudeTokenCounter().count([{"role": "user", "content": "prompt"}])


class _FakeHttpResponse:
    """Stand-in for the context manager ``urllib.request.urlopen`` returns."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def test_live_claude_token_counter_calls_count_tokens_endpoint_when_guard_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The guard is satisfied (simulating a maintainer-run live phase), but
    # the HTTP layer is stubbed -- this test still never reaches the real
    # network or needs a real key. It proves the request is built correctly
    # via stdlib urllib only (no SDK) once the guard passes.
    monkeypatch.setenv(LIVE_PROVIDERS_ENABLED_ENV, "1")
    monkeypatch.setenv(ANTHROPIC_KEY_ENV, "fake-not-a-real-key")
    captured: dict[str, Any] = {}

    def _fake_urlopen(request: Any, timeout: float) -> _FakeHttpResponse:
        captured["url"] = request.full_url
        captured["headers"] = {k.lower(): v for k, v in request.header_items()}
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeHttpResponse({"input_tokens": 123})

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

    tokens = LiveClaudeTokenCounter().count([{"role": "user", "content": "prompt"}])

    assert tokens == 123
    assert captured["url"] == "https://api.anthropic.com/v1/messages/count_tokens"
    assert captured["headers"]["x-api-key"] == "fake-not-a-real-key"
    assert captured["headers"]["anthropic-version"]
    assert captured["body"]["messages"] == [{"role": "user", "content": "prompt"}]
    assert captured["body"]["model"] == "claude-sonnet-5-5"
    assert "tools" not in captured["body"]


def test_live_claude_token_counter_raises_benchmark_cell_failure_on_malformed_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(LIVE_PROVIDERS_ENABLED_ENV, "1")
    monkeypatch.setenv(ANTHROPIC_KEY_ENV, "fake-not-a-real-key")
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: _FakeHttpResponse({"unexpected": "shape"}),
    )

    with pytest.raises(BenchmarkCellFailure, match="input_tokens"):
        LiveClaudeTokenCounter().count([{"role": "user", "content": "prompt"}])


# --- Valid tool envelope, tool stubs, safe HTTP errors ----------------------


def _enable_live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(LIVE_PROVIDERS_ENABLED_ENV, "1")
    monkeypatch.setenv(ANTHROPIC_KEY_ENV, "fake-not-a-real-key")


def _call(tool: str, arguments: Any) -> dict[str, Any]:
    return {"tool": tool, "arguments": arguments, "result": {"ok": True}, "is_error": False}


def test_envelope_pairs_every_tool_result_with_a_preceding_tool_use() -> None:
    envelope, approximation = build_client_wrapped_envelope(
        "prompt",
        tool_calls=[_call("search_docs", {"query": "TaskGroup"}), _call("get_docs", {"slug": "x"})],
    )

    assert approximation is True
    assert [message["role"] for message in envelope] == ["user", "assistant", "user"]
    tool_uses = envelope[1]["content"]
    assert [(block["type"], block["name"], block["input"]) for block in tool_uses] == [
        ("tool_use", "search_docs", {"query": "TaskGroup"}),
        ("tool_use", "get_docs", {"slug": "x"}),
    ]
    tool_results = envelope[2]["content"]
    assert all(block["type"] == "tool_result" for block in tool_results)
    assert [block["tool_use_id"] for block in tool_results] == [block["id"] for block in tool_uses]


def test_count_cell_tokens_sends_tool_stubs_only_with_tool_use_blocks() -> None:
    seen: list[Any] = []

    class _RecordingCounter:
        def count(
            self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
        ) -> int:
            seen.append(tools)
            return 7

    calls = [_call("search_docs", {}), _call("get_docs", {}), _call("search_docs", {})]

    result = count_cell_tokens(prompt="prompt", tool_calls=calls, counter=_RecordingCounter())
    count_cell_tokens(
        prompt="prompt",
        tool_calls=calls,
        counter=_RecordingCounter(),
        provider_mock_payload={"provider": "openai", "mock": True},
    )

    assert seen == [
        [
            {"name": "search_docs", "input_schema": {"type": "object"}},
            {"name": "get_docs", "input_schema": {"type": "object"}},
        ],
        None,
        None,
        None,
    ]
    assert "name-only tool stubs" in result.notes


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (
            400,
            b'{"error":{"message":"bad   tool_use_id"}}',
            'HTTP 400: {"error":{"message":"bad tool_use_id"}}',
        ),
        (
            429,
            b'{"error":{"message":"rate limited"}}',
            'HTTP 429: {"error":{"message":"rate limited"}}',
        ),
        (
            401,
            b'{"error":{"message":"bad key fake-not-a-real-key"}}',
            'HTTP 401: {"error":{"message":"bad key [redacted]"}}',
        ),
        (500, b"x" * 1000, "HTTP 500: " + "x" * 300),
    ],
)
def test_live_claude_token_counter_puts_a_safe_http_error_in_the_failure(
    monkeypatch: pytest.MonkeyPatch, status: int, body: bytes, expected: str
) -> None:
    _enable_live(monkeypatch)

    def _raise_http_error(request: Any, timeout: float) -> Any:
        raise urllib.error.HTTPError(request.full_url, status, "error", None, io.BytesIO(body))

    monkeypatch.setattr("urllib.request.urlopen", _raise_http_error)

    with pytest.raises(BenchmarkCellFailure) as exc_info:
        LiveClaudeTokenCounter().count([{"role": "user", "content": "prompt"}])

    assert str(exc_info.value).endswith(expected)
    assert "fake-not-a-real-key" not in str(exc_info.value)


def test_live_claude_token_counter_sends_tools_when_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_live(monkeypatch)
    captured: dict[str, Any] = {}
    tools = [{"name": "search_docs", "input_schema": {"type": "object"}}]

    def _fake_urlopen(request: Any, timeout: float) -> _FakeHttpResponse:
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeHttpResponse({"input_tokens": 5})

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

    LiveClaudeTokenCounter().count([{"role": "user", "content": "prompt"}], tools=tools)

    assert captured["body"]["tools"] == tools


def test_live_claude_token_counter_raises_benchmark_cell_failure_on_a_non_json_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_live(monkeypatch)
    response = _FakeHttpResponse({})
    response._body = b"<html>proxy login</html>"
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: response)

    with pytest.raises(BenchmarkCellFailure, match="not JSON"):
        LiveClaudeTokenCounter().count([{"role": "user", "content": "prompt"}])


def test_live_claude_token_counter_refuses_a_key_with_whitespace_without_leaking_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(LIVE_PROVIDERS_ENABLED_ENV, "1")
    monkeypatch.setenv(ANTHROPIC_KEY_ENV, "fake-not-a-real-key\nsecond-line")

    def _network_must_not_be_called(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("urlopen must never be called with a malformed key")

    monkeypatch.setattr("urllib.request.urlopen", _network_must_not_be_called)

    with pytest.raises(BenchmarkCellFailure) as exc_info:
        LiveClaudeTokenCounter().count([{"role": "user", "content": "prompt"}])

    assert "fake-not-a-real-key" not in str(exc_info.value)
    assert "second-line" not in str(exc_info.value)
