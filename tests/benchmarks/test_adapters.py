from __future__ import annotations

import pytest

from benchmarks.adapters import (
    AdapterRequest,
    BenchmarkCellFailure,
    LiveExecutionNotImplementedError,
    LiveGoogleAdapter,
    LiveOpenAIAdapter,
    LiveProviderDisabledError,
    MockGoogleAdapter,
    MockOpenAIAdapter,
)
from benchmarks.adapters.guard import (
    LIVE_PROVIDERS_ENABLED_ENV,
    PROVIDER_API_KEY_ENV,
    require_live_environment,
)


def _request(model_id: str = "gpt-4o-mini") -> AdapterRequest:
    return AdapterRequest(
        model_family_id="openai-test",
        model_id=model_id,
        prompt="What does pathlib.Path.read_text return?",
    )


# --- Mock OpenAI adapter -----------------------------------------------------


def test_mock_openai_adapter_returns_structured_response_without_network() -> None:
    adapter = MockOpenAIAdapter()

    response = adapter.generate(_request())

    assert response.answer
    assert response.latency_ms >= 0
    assert response.tokens.provider_native_tokens == 42
    assert response.tokens.provider_token_field == "usage.total_tokens"
    assert response.raw["provider"] == "openai"
    assert response.raw["mock"] is True


def test_mock_openai_adapter_uses_configured_fake_answer() -> None:
    adapter = MockOpenAIAdapter(fake_answer="a canned answer", fake_native_tokens=7)

    response = adapter.generate(_request())

    assert response.answer == "a canned answer"
    assert response.tokens.provider_native_tokens == 7


@pytest.mark.parametrize("category", ["tool_failure", "timeout", "mcp_protocol_crash"])
def test_mock_openai_adapter_captures_forced_failures(category: str) -> None:
    adapter = MockOpenAIAdapter(force_failure=category)

    with pytest.raises(BenchmarkCellFailure) as exc_info:
        adapter.generate(_request())

    assert exc_info.value.category == category


# --- Mock Google adapter -----------------------------------------------------


def test_mock_google_adapter_returns_structured_response_without_network() -> None:
    adapter = MockGoogleAdapter()

    response = adapter.generate(
        AdapterRequest(
            model_family_id="google-test",
            model_id="gemini-1.5-flash",
            prompt="What does pathlib.Path.read_text return?",
        )
    )

    assert response.answer
    assert response.latency_ms >= 0
    assert response.tokens.provider_native_tokens == 37
    assert response.tokens.provider_token_field == "usage_metadata.total_token_count"
    assert response.raw["provider"] == "google"
    assert response.raw["mock"] is True


@pytest.mark.parametrize("category", ["tool_failure", "timeout", "mcp_protocol_crash"])
def test_mock_google_adapter_captures_forced_failures(category: str) -> None:
    adapter = MockGoogleAdapter(force_failure=category)

    with pytest.raises(BenchmarkCellFailure) as exc_info:
        adapter.generate(
            AdapterRequest(
                model_family_id="google-test",
                model_id="gemini-1.5-flash",
                prompt="prompt",
            )
        )

    assert exc_info.value.category == category


# --- Live-provider guardrail --------------------------------------------------


def test_require_live_environment_refuses_with_no_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(LIVE_PROVIDERS_ENABLED_ENV, raising=False)
    monkeypatch.delenv(PROVIDER_API_KEY_ENV["openai"], raising=False)

    with pytest.raises(LiveProviderDisabledError, match=LIVE_PROVIDERS_ENABLED_ENV):
        require_live_environment("openai")


def test_require_live_environment_refuses_with_flag_but_no_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(LIVE_PROVIDERS_ENABLED_ENV, "1")
    monkeypatch.delenv(PROVIDER_API_KEY_ENV["openai"], raising=False)

    with pytest.raises(LiveProviderDisabledError, match=PROVIDER_API_KEY_ENV["openai"]):
        require_live_environment("openai")


def test_require_live_environment_refuses_with_key_but_no_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(LIVE_PROVIDERS_ENABLED_ENV, raising=False)
    monkeypatch.setenv(PROVIDER_API_KEY_ENV["openai"], "sk-fake-not-a-real-key")

    with pytest.raises(LiveProviderDisabledError, match=LIVE_PROVIDERS_ENABLED_ENV):
        require_live_environment("openai")


def test_require_live_environment_passes_with_both_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(LIVE_PROVIDERS_ENABLED_ENV, "1")
    monkeypatch.setenv(PROVIDER_API_KEY_ENV["google"], "fake-not-a-real-key")

    require_live_environment("google")  # must not raise


def test_require_live_environment_rejects_unknown_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(LIVE_PROVIDERS_ENABLED_ENV, "1")

    # D6b sanction (issue #89 pre-flight amendment, 2026-07-08): registering
    # "anthropic" in PROVIDER_API_KEY_ENV for the guarded Claude count-tokens
    # caller means "anthropic" is no longer an unknown provider here, so
    # this assertion is re-pointed at a still-unregistered provider string.
    # This is the sole merged-test change the sanction covers.
    with pytest.raises(LiveProviderDisabledError, match="unknown provider"):
        require_live_environment("not-a-registered-provider")


# --- Live adapter stubs: guard enforced, no network ever possible -----------


def test_live_openai_adapter_refuses_without_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(LIVE_PROVIDERS_ENABLED_ENV, raising=False)
    monkeypatch.delenv(PROVIDER_API_KEY_ENV["openai"], raising=False)
    adapter = LiveOpenAIAdapter()

    with pytest.raises(LiveProviderDisabledError):
        adapter.generate(_request())


def test_live_openai_adapter_still_refuses_when_config_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Even with the guardrail satisfied, this release never makes a live
    # call: live execution is reserved for a maintainer-run phase.
    monkeypatch.setenv(LIVE_PROVIDERS_ENABLED_ENV, "1")
    monkeypatch.setenv(PROVIDER_API_KEY_ENV["openai"], "sk-fake-not-a-real-key")
    adapter = LiveOpenAIAdapter()

    with pytest.raises(LiveExecutionNotImplementedError):
        adapter.generate(_request())


def test_live_google_adapter_refuses_without_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(LIVE_PROVIDERS_ENABLED_ENV, raising=False)
    monkeypatch.delenv(PROVIDER_API_KEY_ENV["google"], raising=False)
    adapter = LiveGoogleAdapter()

    with pytest.raises(LiveProviderDisabledError):
        adapter.generate(
            AdapterRequest(
                model_family_id="google-test", model_id="gemini-1.5-flash", prompt="prompt"
            )
        )


def test_live_google_adapter_still_refuses_when_config_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(LIVE_PROVIDERS_ENABLED_ENV, "1")
    monkeypatch.setenv(PROVIDER_API_KEY_ENV["google"], "fake-not-a-real-key")
    adapter = LiveGoogleAdapter()

    with pytest.raises(LiveExecutionNotImplementedError):
        adapter.generate(
            AdapterRequest(
                model_family_id="google-test", model_id="gemini-1.5-flash", prompt="prompt"
            )
        )
