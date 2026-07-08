"""Provider adapter interfaces for the v0.5.0 public benchmark (issue #73).

Mocked plumbing only: no adapter in this package ever performs network I/O.
See ``benchmarks/adapters/base.py`` for the shared request/response contract,
``benchmarks/adapters/guard.py`` for the live-provider refusal guardrail, and
``benchmarks/adapters/openai_adapter.py`` / ``google_adapter.py`` for the
mock and guarded live-stub implementations.
"""

from __future__ import annotations

from benchmarks.adapters.base import (
    AdapterRequest,
    AdapterResponse,
    ProviderAdapter,
    TokenMetadata,
)
from benchmarks.adapters.google_adapter import LiveGoogleAdapter, MockGoogleAdapter
from benchmarks.adapters.guard import (
    LiveExecutionNotImplementedError,
    LiveProviderDisabledError,
    require_live_environment,
)
from benchmarks.adapters.openai_adapter import LiveOpenAIAdapter, MockOpenAIAdapter
from benchmarks.runner import BenchmarkCellFailure

__all__ = [
    "AdapterRequest",
    "AdapterResponse",
    "ProviderAdapter",
    "TokenMetadata",
    "BenchmarkCellFailure",
    "LiveExecutionNotImplementedError",
    "LiveProviderDisabledError",
    "require_live_environment",
    "MockOpenAIAdapter",
    "LiveOpenAIAdapter",
    "MockGoogleAdapter",
    "LiveGoogleAdapter",
]
