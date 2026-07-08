"""Google-backed provider adapters (mock and guarded live stub).

No Google SDK is imported or required here -- see the module docstring in
``benchmarks/adapters/guard.py`` for why the live adapter never makes a real
call. Mock and live adapters both never perform network I/O.
"""

from __future__ import annotations

from typing import Any

from benchmarks.adapters.base import AdapterRequest, ProviderAdapter, TokenMetadata
from benchmarks.adapters.guard import LiveExecutionNotImplementedError, require_live_environment
from benchmarks.runner import BenchmarkCellFailure

PROVIDER_NATIVE_TOKEN_FIELD = "usage_metadata.total_token_count"


class MockGoogleAdapter(ProviderAdapter):
    """Deterministic Google adapter double. Never makes a network call.

    ``force_failure`` optionally forces a ``BenchmarkCellFailure`` with the
    given category (``tool_failure`` | ``timeout`` | ``mcp_protocol_crash``)
    instead of a canned answer, so failure-capture behavior can be tested
    the same way ``benchmarks.runner`` tests it for the fake competitor
    adapter.
    """

    provider = "google"

    def __init__(
        self,
        *,
        fake_answer: str | None = None,
        fake_native_tokens: int = 37,
        force_failure: str | None = None,
    ) -> None:
        self._fake_answer = fake_answer
        self._fake_native_tokens = fake_native_tokens
        self._force_failure = force_failure

    def _generate(self, request: AdapterRequest) -> tuple[str, TokenMetadata, dict[str, Any]]:
        if self._force_failure is not None:
            raise BenchmarkCellFailure(
                self._force_failure, f"forced mock google adapter {self._force_failure}"
            )
        answer = self._fake_answer or f"[mock-google:{request.model_id}] {request.prompt}"
        tokens = TokenMetadata(
            provider_native_tokens=self._fake_native_tokens,
            provider_token_field=PROVIDER_NATIVE_TOKEN_FIELD,
        )
        raw = {
            "provider": self.provider,
            "model": request.model_id,
            "mock": True,
            "usage_metadata": {"total_token_count": self._fake_native_tokens},
        }
        return answer, tokens, raw


class LiveGoogleAdapter(ProviderAdapter):
    """Guarded live-provider stub. Structurally incapable of a network call.

    Calling :meth:`generate` first enforces
    :func:`benchmarks.adapters.guard.require_live_environment`, then always
    raises :class:`LiveExecutionNotImplementedError` -- live execution is
    reserved for a maintainer-run phase, not this mocked-plumbing release.
    """

    provider = "google"

    def _generate(self, request: AdapterRequest) -> tuple[str, TokenMetadata, dict[str, Any]]:
        require_live_environment(self.provider)
        raise LiveExecutionNotImplementedError(
            "live Google execution is not implemented in this release; issue #73 ships "
            "mocked plumbing only. Real provider calls are confined to a maintainer-run "
            "live phase (see PLAN.md Amendment 2026-07-08)."
        )
