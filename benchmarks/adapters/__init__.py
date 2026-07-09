"""Provider adapter interfaces for the v0.5.0 public benchmark (issue #73).

Mocked plumbing only: no OpenAI/Google adapter in this package ever
performs network I/O. See ``benchmarks/adapters/base.py`` for the shared
request/response contract, ``benchmarks/adapters/guard.py`` for the
live-provider refusal guardrail, and
``benchmarks/adapters/openai_adapter.py`` / ``google_adapter.py`` for the
mock and guarded live-stub implementations.

``benchmarks/adapters/claude_tokens.py`` (issue #89) is the one exception:
its ``LiveClaudeTokenCounter`` does perform a real, guarded HTTP call to the
Anthropic count-tokens API, confined to the maintainer-run live phase (see
that module's docstring).

``context7_adapter.py`` / ``gitmcp_adapter.py`` / ``deepwiki_adapter.py`` /
``ref_tools_adapter.py`` (issue #87) are the competitor docs-MCP adapters:
like ``python_docs_mcp_adapter.py`` (issue #86), these model a retrieval
tool call rather than an LLM provider call, so none of them subclass
``ProviderAdapter``. ``eligibility.py`` (issue #87) is the manifest-load-time
eligibility screener that keeps ineligible/excluded competitors out of
scored cells entirely (see that module's docstring).
"""

from __future__ import annotations

from benchmarks.adapters.base import (
    AdapterRequest,
    AdapterResponse,
    ProviderAdapter,
    TokenMetadata,
)
from benchmarks.adapters.claude_tokens import (
    FakeTokenCounter,
    LiveClaudeTokenCounter,
    TokenCounter,
    TokenCountResult,
    build_client_wrapped_envelope,
    count_cell_tokens,
)
from benchmarks.adapters.context7_adapter import Context7Adapter, Context7Result
from benchmarks.adapters.deepwiki_adapter import DeepWikiAdapter, DeepWikiResult
from benchmarks.adapters.eligibility import (
    COMPETITOR_ADAPTER_IDS,
    validate_competitor_eligibility,
    validate_manifest_eligibility,
)
from benchmarks.adapters.gitmcp_adapter import GitMcpAdapter, GitMcpResult
from benchmarks.adapters.google_adapter import LiveGoogleAdapter, MockGoogleAdapter
from benchmarks.adapters.guard import (
    LiveExecutionNotImplementedError,
    LiveProviderDisabledError,
    require_live_competitor,
    require_live_environment,
)
from benchmarks.adapters.openai_adapter import LiveOpenAIAdapter, MockOpenAIAdapter
from benchmarks.adapters.ref_tools_adapter import RefToolsAdapter, RefToolsResult
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
    "require_live_competitor",
    "MockOpenAIAdapter",
    "LiveOpenAIAdapter",
    "MockGoogleAdapter",
    "LiveGoogleAdapter",
    "TokenCounter",
    "FakeTokenCounter",
    "LiveClaudeTokenCounter",
    "TokenCountResult",
    "build_client_wrapped_envelope",
    "count_cell_tokens",
    "Context7Adapter",
    "Context7Result",
    "GitMcpAdapter",
    "GitMcpResult",
    "DeepWikiAdapter",
    "DeepWikiResult",
    "RefToolsAdapter",
    "RefToolsResult",
    "COMPETITOR_ADAPTER_IDS",
    "validate_competitor_eligibility",
    "validate_manifest_eligibility",
]
