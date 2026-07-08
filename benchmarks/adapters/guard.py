"""Live-provider refusal guardrail.

Issue #73 requires that this implementation refuse to run live providers
unless explicit environment variables/config are present. This module is
that guardrail: any live adapter (see ``openai_adapter.LiveOpenAIAdapter`` /
``google_adapter.LiveGoogleAdapter``) must call
:func:`require_live_environment` before doing anything else.

This package never makes a live or paid API call, with or without the
guard passing -- the live adapters in this release are stubs that, after
the guard passes, still refuse via ``LiveExecutionNotImplementedError``.
Actually implementing a live call is out of scope for issue #73's mocked
plumbing and is reserved for a maintainer-run, human-supervised phase (see
PLAN.md Amendment 2026-07-08).
"""

from __future__ import annotations

import os

from benchmarks.runner import BenchmarkCellFailure

#: Explicit opt-in flag. Must be a truthy value ("1", "true", or "yes",
#: case-insensitive) for any live-provider guard to pass.
LIVE_PROVIDERS_ENABLED_ENV = "BENCHMARK_LIVE_PROVIDERS_ENABLED"

#: Per-provider environment variable that must also be non-empty.
PROVIDER_API_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
}

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
