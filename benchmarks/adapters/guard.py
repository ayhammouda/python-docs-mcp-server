"""Live-provider refusal guardrail.

Issue #73 requires that this implementation refuse to run live providers
unless explicit environment variables/config are present. This module is
that guardrail: every live call must pass :func:`require_live_environment`
first. The agentic harness builds every live client through
``benchmarks.agentic.clients.make_client``, which calls it; tests use
scripted clients and mock transports, and never set the flag and a key.

``benchmarks.adapters.claude_tokens``'s Anthropic count-tokens caller
(issue #89) performs a real HTTP call once this guard passes, because the
maintainer-run live phase is exactly what that call is for (Claude token
counting after client-side rewrap, roadmap decision 5.8). It still never
runs in CI or unit tests -- tests use a fake counter -- and it is
structurally incapable of running without both
``BENCHMARK_LIVE_PROVIDERS_ENABLED`` and ``ANTHROPIC_API_KEY`` set, exactly
like every other provider gated here.

The keyless competitors' allowlist latch is gone with the fixed-flow
adapters that called it; round 2 brings it back together with its caller.
"""

from __future__ import annotations

import os
import re

from benchmarks.runner import BenchmarkCellFailure

#: Explicit opt-in flag. Must be a truthy value ("1", "true", or "yes",
#: case-insensitive) for any live-provider guard to pass.
LIVE_PROVIDERS_ENABLED_ENV = "BENCHMARK_LIVE_PROVIDERS_ENABLED"

#: The sandbox's egress proxy (``http://<host>:<port>``), the only proxy a live client uses.
EGRESS_URL_ENV = "BENCHMARK_EGRESS_URL"

#: Per-provider environment variable that must also be non-empty.
#:
#: ``anthropic`` (issue #89) is not a competitor/model-matrix provider --
#: it gates the Claude count-tokens methodology caller in
#: ``benchmarks.adapters.claude_tokens``, not a competitor answer adapter.
#:
#: ``ref`` and ``context7`` (issue #87) gate the keyed competitor docs MCPs:
#: Ref.tools (always keyed) and Context7 when its manifest entry declares key
#: mode. Round 2 runs them through the agentic bridge.
PROVIDER_API_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "ref": "REF_API_KEY",
    "context7": "CONTEXT7_API_KEY",
}

#: The model providers' keys: they pay for every call, and no docs system ever receives one.
PAID_KEY_ENVS = tuple(PROVIDER_API_KEY_ENV[p] for p in ("anthropic", "openai", "google"))

#: Keyless live providers, and the variable that must be set instead of a key (#184 ruling 9).
LIVE_LATCH_ENV = {"ollama": "OLLAMA_BASE_URL"}

_TRUTHY = {"1", "true", "yes"}
#: Every provider key is made of these; anything else could be quoted by an HTTP error.
_KEY_CHARACTERS = re.compile(r"[A-Za-z0-9_.-]+")


class LiveProviderDisabledError(BenchmarkCellFailure):
    """Raised when a live provider is invoked without explicit opt-in config."""

    def __init__(self, message: str) -> None:
        super().__init__("tool_failure", message)


class MalformedApiKeyError(LiveProviderDisabledError):
    """Raised when a provider API key is set but holds characters no HTTP header may carry."""


def require_live_environment(provider: str) -> None:
    """Raise :class:`LiveProviderDisabledError` unless live calls are explicitly enabled.

    Checks, in order:

    1. ``BENCHMARK_LIVE_PROVIDERS_ENABLED`` is set to a truthy value.
    2. The provider-specific API key environment variable (see
       :data:`PROVIDER_API_KEY_ENV`) is set and non-empty; for a keyless provider,
       its :data:`LIVE_LATCH_ENV` variable instead, and nothing else.
    3. That variable's raw value uses only ``[A-Za-z0-9_.-]`` (an HTTP client error
       would quote any other character).

    All conditions must hold. Passing this check does not mean a live call
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
    latch = LIVE_LATCH_ENV.get(provider)
    if latch is not None:  # a keyless provider: its latch, and never a key
        if not os.environ.get(latch, "").strip():
            raise LiveProviderDisabledError(
                f"live provider calls are disabled: missing required {latch} for "
                f"provider {provider!r}"
            )
        return
    key_env = PROVIDER_API_KEY_ENV.get(provider)
    if key_env is None:
        raise LiveProviderDisabledError(f"unknown provider for live guard: {provider!r}")
    raw_key = os.environ.get(key_env, "")
    if not raw_key.strip():
        raise LiveProviderDisabledError(
            f"live provider calls are disabled: missing required {key_env} for "
            f"provider {provider!r}"
        )
    if not _KEY_CHARACTERS.fullmatch(raw_key):
        raise MalformedApiKeyError(
            f"live provider calls are disabled: {key_env} holds a character outside "
            "[A-Za-z0-9_.-]"
        )


def redact_api_keys(text: str) -> str:
    """Replace every set :data:`PROVIDER_API_KEY_ENV` key in ``text`` with ``[redacted]``."""
    # Find every match of every key in the original text (overlaps included), merge
    # overlapping or adjacent spans, then redact each merged span once, so keys that
    # contain or overlap each other leave no fragment. Empty values are skipped.
    api_keys = {os.environ.get(key_env, "").strip() for key_env in PROVIDER_API_KEY_ENV.values()}
    spans: list[tuple[int, int]] = []
    for api_key in filter(None, api_keys):
        start = text.find(api_key)
        while start != -1:
            spans.append((start, start + len(api_key)))
            start = text.find(api_key, start + 1)
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    parts: list[str] = []
    position = 0
    for start, end in merged:  # one pass: copying the tail per match is quadratic
        parts += (text[position:start], "[redacted]")
        position = end
    parts.append(text[position:])
    return "".join(parts)
