"""Benchmark guards, token counting and competitor eligibility.

``benchmarks/adapters/guard.py`` is the live-provider refusal guardrail
that every live call passes first, including the agentic harness's client
factory (``benchmarks.agentic.clients.make_client``).

``benchmarks/adapters/claude_tokens.py`` (issue #89) performs a real,
guarded HTTP call to the Anthropic count-tokens API, confined to the
maintainer-run live phase (see that module's docstring).

``eligibility.py`` (issue #87) is the manifest-load-time eligibility
screener that keeps ineligible/excluded competitors out of scored cells
entirely (see that module's docstring).

The fixed-flow retrieval adapters and the OpenAI/Google provider stubs are
gone: the agentic harness (``benchmarks.agentic``) replaces them with one
MCP bridge for every system and real, guarded SDK clients (design §11.1).
"""

from __future__ import annotations

from benchmarks.adapters.claude_tokens import (
    FakeTokenCounter,
    LiveClaudeTokenCounter,
    TokenCounter,
    TokenCountResult,
    build_client_wrapped_envelope,
    count_cell_tokens,
)
from benchmarks.adapters.eligibility import (
    COMPETITOR_ADAPTER_IDS,
    validate_competitor_eligibility,
    validate_manifest_eligibility,
)
from benchmarks.adapters.guard import (
    LiveProviderDisabledError,
    require_live_environment,
)
from benchmarks.runner import BenchmarkCellFailure

__all__ = [
    "BenchmarkCellFailure",
    "LiveProviderDisabledError",
    "require_live_environment",
    "TokenCounter",
    "FakeTokenCounter",
    "LiveClaudeTokenCounter",
    "TokenCountResult",
    "build_client_wrapped_envelope",
    "count_cell_tokens",
    "COMPETITOR_ADAPTER_IDS",
    "validate_competitor_eligibility",
    "validate_manifest_eligibility",
]
