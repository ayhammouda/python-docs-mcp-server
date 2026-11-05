"""The grading factory uses the answering worker's validated proxy and one paid key."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from benchmarks.adapters.guard import PROVIDER_API_KEY_ENV
from benchmarks.grading import batch
from benchmarks.runner import BenchmarkValidationError


@pytest.mark.parametrize(
    "provider,api,builder",
    [
        ("anthropic", "anthropic-messages", "anthropic_sdk"),
        ("openai", "openai-responses", "openai_sdk"),
        ("google", "gemini-generate-content", "gemini_sdk"),
    ],
)
def test_batch_factory_uses_only_the_validated_proxy(monkeypatch, provider, api, builder):
    family = SimpleNamespace(
        id="judge", api=api, provider=provider, model_id="m", max_output_tokens=16384
    )
    monkeypatch.setenv("BENCHMARK_LIVE_PROVIDERS_ENABLED", "1")
    monkeypatch.setenv(PROVIDER_API_KEY_ENV[provider], "fake-test-key")
    sdk = Mock()
    monkeypatch.setattr(batch, builder, sdk)
    proxy = "http://proxy:3128"
    monkeypatch.setenv("BENCHMARK_EGRESS_URL", proxy)
    batch.make_batch_client(family, effort=None, timeout_s=5)
    sdk.assert_called_once_with("fake-test-key", 5, transport=None, proxy=proxy)
    monkeypatch.setenv("BENCHMARK_EGRESS_URL", "http://secret@proxy:3128")
    with pytest.raises(BenchmarkValidationError, match="BENCHMARK_EGRESS_URL") as exc:
        batch.make_batch_client(family, effort=None, timeout_s=5)
    assert "secret" not in str(exc.value)
    assert sdk.call_count == 1


def test_keyless_local_provider_has_no_live_batch_api():
    family = SimpleNamespace(id="local", provider="ollama", api="openai-chat-completions")
    with pytest.raises(BenchmarkValidationError, match="no batch client"):
        batch.make_batch_client(family, effort=None, timeout_s=5)
