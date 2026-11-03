from __future__ import annotations

import time

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
    redact_api_keys,
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


@pytest.mark.parametrize(
    "api_key", ["fake-not-a-real-key\n", "fake-not-a-real key", "fake-not-a-real-日key"]
)
def test_require_live_environment_refuses_a_malformed_key_without_leaking_it(
    monkeypatch: pytest.MonkeyPatch, api_key: str
) -> None:
    # http.client would quote such a key (or one of its characters) in an error.
    monkeypatch.setenv(LIVE_PROVIDERS_ENABLED_ENV, "1")
    monkeypatch.setenv(PROVIDER_API_KEY_ENV["ref"], api_key)

    with pytest.raises(LiveProviderDisabledError) as exc_info:
        require_live_environment("ref")

    assert "fake-not-a-real" not in str(exc_info.value)
    assert "日" not in str(exc_info.value)


def test_redact_api_keys_removes_a_key_that_contains_another_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key_env in PROVIDER_API_KEY_ENV.values():
        monkeypatch.delenv(key_env, raising=False)
    # OPENAI_API_KEY comes first in the map; replacing it first would leave "y2".
    monkeypatch.setenv(PROVIDER_API_KEY_ENV["openai"], "fake-x1")
    monkeypatch.setenv(PROVIDER_API_KEY_ENV["anthropic"], "fake-x1y2")

    assert redact_api_keys("echo fake-x1y2 and fake-x1") == "echo [redacted] and [redacted]"


@pytest.mark.parametrize(
    ("openai_key", "anthropic_key"),
    [
        pytest.param("fake-abcXYZ", "XYZsecret123", id="second-key-longer"),
        pytest.param("fake-abcdefXYZ", "XYZsecret1", id="first-key-longer"),
    ],
)
def test_redact_api_keys_leaves_no_fragment_when_two_keys_overlap_in_the_text(
    monkeypatch: pytest.MonkeyPatch, openai_key: str, anthropic_key: str
) -> None:
    for key_env in PROVIDER_API_KEY_ENV.values():
        monkeypatch.delenv(key_env, raising=False)
    monkeypatch.setenv(PROVIDER_API_KEY_ENV["openai"], openai_key)
    monkeypatch.setenv(PROVIDER_API_KEY_ENV["anthropic"], anthropic_key)
    overlapped = openai_key + anthropic_key.removeprefix("XYZ")

    assert redact_api_keys(f"echo {overlapped} end") == "echo [redacted] end"


def test_redact_api_keys_redacts_every_occurrence_of_a_repeated_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key_env in PROVIDER_API_KEY_ENV.values():
        monkeypatch.delenv(key_env, raising=False)
    monkeypatch.setenv(PROVIDER_API_KEY_ENV["ref"], "fake-ref-key")

    assert (
        redact_api_keys("a fake-ref-key b fake-ref-key c") == "a [redacted] b [redacted] c"
    )


def test_redact_api_keys_merges_overlapping_occurrences_of_one_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key_env in PROVIDER_API_KEY_ENV.values():
        monkeypatch.delenv(key_env, raising=False)
    monkeypatch.setenv(PROVIDER_API_KEY_ENV["ref"], "ababa")

    # str.replace finds only "ababa" at 0 and would leave "ba" behind.
    assert redact_api_keys("abababa") == "[redacted]"


def test_redact_api_keys_stays_linear_on_a_large_body_with_many_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key_env in PROVIDER_API_KEY_ENV.values():
        monkeypatch.delenv(key_env, raising=False)
    monkeypatch.setenv(PROVIDER_API_KEY_ENV["ref"], "fake-ref-key")
    filler = " " + "x" * 100 + " "
    body = ("fake-ref-key" + filler) * 20_000  # 2.3 MB, 20,000 matches

    started = time.perf_counter()
    redacted = redact_api_keys(body)
    elapsed = time.perf_counter() - started

    assert redacted == ("[redacted]" + filler) * 20_000
    # Rebuilding the string once per match took seconds here; one pass takes milliseconds.
    assert elapsed < 1.0


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
