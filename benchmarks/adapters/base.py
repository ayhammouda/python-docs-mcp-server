"""Provider adapter interface contracts.

Defines the request/response/latency/token-metadata/failure-capture shape
that every provider adapter (mock or, eventually, live) must implement for
issue #73. See ``benchmarks/adapters/guard.py`` for the live-provider
refusal guardrail and ``benchmarks/adapters/openai_adapter.py`` /
``google_adapter.py`` for the mock and live-stub implementations.

Failure capture reuses ``benchmarks.runner.BenchmarkCellFailure`` directly
(same ``category`` vocabulary: ``tool_failure`` | ``timeout`` |
``mcp_protocol_crash``) so a future runner integration can catch adapter
failures with the exact except clause the runner already uses for
``_execute_cell``.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from benchmarks.runner import BenchmarkCellFailure

__all__ = [
    "AdapterRequest",
    "TokenMetadata",
    "AdapterResponse",
    "ProviderAdapter",
    "BenchmarkCellFailure",
]


@dataclass(frozen=True)
class AdapterRequest:
    """Input to a provider adapter for one benchmark cell."""

    model_family_id: str
    """Matches a ``docs/benchmarks/model-matrix.yml`` entry ``id``."""

    model_id: str
    """The provider's own model identifier, e.g. ``gpt-4o-mini``."""

    prompt: str
    """The corpus question prompt, unmodified (see benchmark methodology's
    "Prompting Rules": every system under test receives the same question)."""


@dataclass(frozen=True)
class TokenMetadata:
    """Token metadata captured from one adapter call.

    ``provider_native_tokens`` is that provider's own billing/usage token
    count (e.g. OpenAI ``usage.total_tokens``, Google
    ``usage_metadata.total_token_count``). It is diagnostic data only and
    must never be reported under the
    ``benchmarks.model_matrix.METHODOLOGY_TOKEN_LABEL`` label -- that label
    is reserved for the methodology tokenizer's cross-model count, which
    this package does not compute (real Claude token counting is confined to
    the maintainer-run live phase per PLAN.md Amendment 2026-07-08).
    """

    provider_native_tokens: int | None
    provider_token_field: str


@dataclass(frozen=True)
class AdapterResponse:
    """Structured output from a provider adapter call."""

    answer: str
    latency_ms: float
    tokens: TokenMetadata
    raw: dict[str, Any]
    """Raw, mock-safe provider payload metadata kept for audit/debugging."""


class ProviderAdapter(ABC):
    """Base class for a provider adapter.

    Subclasses implement :meth:`_generate` with the provider-specific
    behavior (mock or live-stub); this base class provides the shared
    latency measurement and generic failure-capture wrapping so every
    adapter reports latency and failures the same way.
    """

    provider: str

    def generate(self, request: AdapterRequest) -> AdapterResponse:
        """Run one adapter call and return a structured response.

        Any exception raised by :meth:`_generate` other than
        ``BenchmarkCellFailure`` is wrapped as a ``BenchmarkCellFailure``
        with category ``tool_failure`` so callers only ever need to catch
        one exception type for failure capture.
        """
        started = time.perf_counter()
        try:
            answer, tokens, raw = self._generate(request)
        except BenchmarkCellFailure:
            raise
        except Exception as exc:  # noqa: BLE001 - failures are benchmark artifacts
            raise BenchmarkCellFailure("tool_failure", str(exc)) from exc
        latency_ms = round((time.perf_counter() - started) * 1000, 3)
        return AdapterResponse(answer=answer, latency_ms=latency_ms, tokens=tokens, raw=raw)

    @abstractmethod
    def _generate(self, request: AdapterRequest) -> tuple[str, TokenMetadata, dict[str, Any]]:
        """Provider-specific call. Must not perform any network I/O in mock mode."""
        raise NotImplementedError
