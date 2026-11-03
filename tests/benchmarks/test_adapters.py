from __future__ import annotations

import time

import pytest

from benchmarks.adapters import LiveProviderDisabledError
from benchmarks.adapters.guard import (
    LIVE_PROVIDERS_ENABLED_ENV,
    PROVIDER_API_KEY_ENV,
    redact_api_keys,
    require_live_environment,
)

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
