"""Live-provider refusal guardrail.

Issue #73 requires that this implementation refuse to run live providers
unless explicit environment variables/config are present. This module is
that guardrail: any live adapter (see ``openai_adapter.LiveOpenAIAdapter`` /
``google_adapter.LiveGoogleAdapter``) must call
:func:`require_live_environment` before doing anything else.

The OpenAI/Google adapters in this package never make a live or paid API
call, with or without the guard passing -- they are stubs that, after the
guard passes, still refuse via ``LiveExecutionNotImplementedError``.
Actually implementing a live call for those providers is out of scope for
issue #73's mocked plumbing and is reserved for a maintainer-run,
human-supervised phase (see PLAN.md Amendment 2026-07-08).

Issue #89 is the one exception: ``benchmarks.adapters.claude_tokens``'s
Anthropic count-tokens caller *does* perform a real HTTP call once this
guard passes, because the maintainer-run live phase is exactly what that
call is for (Claude token counting after client-side rewrap, roadmap
decision 5.8). It still never runs in CI or unit tests -- tests use a fake
counter -- and it is structurally incapable of running without both
``BENCHMARK_LIVE_PROVIDERS_ENABLED`` and ``ANTHROPIC_API_KEY`` set, exactly
like every other provider gated here.

Issue #87 adds :func:`require_live_competitor` for the keyless competitor
docs MCPs (GitMCP, DeepWiki): they have no per-provider API key to gate on,
so a second non-secret latch (:data:`LIVE_COMPETITORS_ENV`, a comma-separated
allowlist) stands in for the key, keeping the same two-latch shape (global
flag + per-target opt-in) as the keyed providers above. Keyed competitors
(Context7 in key mode, Ref.tools) still go through
:func:`require_live_environment` via their own ``PROVIDER_API_KEY_ENV``
entries.
"""

from __future__ import annotations

import os

from benchmarks.runner import BenchmarkCellFailure

#: Explicit opt-in flag. Must be a truthy value ("1", "true", or "yes",
#: case-insensitive) for any live-provider guard to pass.
LIVE_PROVIDERS_ENABLED_ENV = "BENCHMARK_LIVE_PROVIDERS_ENABLED"

#: Per-provider environment variable that must also be non-empty.
#:
#: ``anthropic`` (issue #89) is not a competitor/model-matrix provider --
#: it gates the Claude count-tokens methodology caller in
#: ``benchmarks.adapters.claude_tokens``, not a competitor answer adapter.
#:
#: ``ref`` and ``context7`` (issue #87) gate the keyed competitor docs MCP
#: adapters: ``benchmarks.adapters.ref_tools_adapter.RefToolsAdapter``
#: (always keyed -- Ref.tools requires an API key) and
#: ``benchmarks.adapters.context7_adapter.Context7Adapter`` when its
#: manifest entry declares key mode (Context7 also has a keyless mode; see
#: :func:`require_live_competitor`).
PROVIDER_API_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "ref": "REF_API_KEY",
    "context7": "CONTEXT7_API_KEY",
}

#: Comma-separated allowlist of keyless competitor names permitted to run
#: live (issue #87). Parsed case-insensitively with surrounding whitespace
#: stripped from each item -- e.g. ``"gitmcp, DeepWiki"`` allows both
#: ``gitmcp`` and ``deepwiki``. This is the second latch for
#: :func:`require_live_competitor`; it deliberately holds no secret, so a
#: keyless competitor still requires two explicit, independent opt-ins
#: (the shared global flag plus this per-target allowlist) rather than one.
LIVE_COMPETITORS_ENV = "BENCHMARK_LIVE_COMPETITORS"

_TRUTHY = {"1", "true", "yes"}


class LiveProviderDisabledError(BenchmarkCellFailure):
    """Raised when a live provider is invoked without explicit opt-in config."""

    def __init__(self, message: str) -> None:
        super().__init__("tool_failure", message)


class LiveExecutionNotImplementedError(BenchmarkCellFailure):
    """Raised after the guard passes: live execution itself is not implemented.

    This package ships mocked plumbing only; it is structurally incapable of
    a live call regardless of environment configuration.
    """

    def __init__(self, message: str) -> None:
        super().__init__("tool_failure", message)


def require_live_environment(provider: str) -> None:
    """Raise :class:`LiveProviderDisabledError` unless live calls are explicitly enabled.

    Checks, in order:

    1. ``BENCHMARK_LIVE_PROVIDERS_ENABLED`` is set to a truthy value.
    2. The provider-specific API key environment variable (see
       :data:`PROVIDER_API_KEY_ENV`) is set and non-empty.

    Both conditions must hold. Passing this check does not mean a live call
    will be made -- see the module docstring.
    """
    flag = os.environ.get(LIVE_PROVIDERS_ENABLED_ENV, "").strip().lower()
    if flag not in _TRUTHY:
        raise LiveProviderDisabledError(
            "live provider calls are disabled: set "
            f"{LIVE_PROVIDERS_ENABLED_ENV}=1 to pass the live-provider guardrail check "
            f"for provider {provider!r} (this does not by itself enable live calls; "
            "see benchmarks/adapters/guard.py)"
        )
    key_env = PROVIDER_API_KEY_ENV.get(provider)
    if key_env is None:
        raise LiveProviderDisabledError(f"unknown provider for live guard: {provider!r}")
    if not os.environ.get(key_env, "").strip():
        raise LiveProviderDisabledError(
            f"live provider calls are disabled: missing required {key_env} for "
            f"provider {provider!r}"
        )


def require_live_competitor(name: str) -> None:
    """Raise :class:`LiveProviderDisabledError` unless a keyless competitor is explicitly enabled.

    Two non-secret latches, both required (issue #87, Codex round-1 finding
    3: flag-only gating would weaken the guard posture below the keyed
    providers' two-condition shape):

    1. ``BENCHMARK_LIVE_PROVIDERS_ENABLED`` is set to a truthy value (same
       flag, same check, as :func:`require_live_environment`).
    2. ``name`` appears in the comma-separated :data:`LIVE_COMPETITORS_ENV`
       allowlist (case-insensitive, whitespace-tolerant).

    Both conditions must hold. Passing this check does not mean a live call
    will be made -- competitor adapters that pass this guard still route all
    transport construction through their own ``_transport_factory`` seam,
    which nothing in this module or the guard itself invokes.
    """
    flag = os.environ.get(LIVE_PROVIDERS_ENABLED_ENV, "").strip().lower()
    if flag not in _TRUTHY:
        raise LiveProviderDisabledError(
            "live competitor calls are disabled: set "
            f"{LIVE_PROVIDERS_ENABLED_ENV}=1 to pass the live-competitor guardrail check "
            f"for competitor {name!r} (this does not by itself enable live calls; "
            "see benchmarks/adapters/guard.py)"
        )
    allowlist = {
        item.strip().lower()
        for item in os.environ.get(LIVE_COMPETITORS_ENV, "").split(",")
        if item.strip()
    }
    if name.strip().lower() not in allowlist:
        raise LiveProviderDisabledError(
            f"live competitor calls are disabled: {name!r} is not present in "
            f"{LIVE_COMPETITORS_ENV} (comma-separated allowlist); set "
            f"{LIVE_COMPETITORS_ENV}={name},... to enable it"
        )
