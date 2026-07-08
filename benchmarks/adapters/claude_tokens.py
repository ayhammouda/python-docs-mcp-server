"""Claude token counting via the Anthropic count-tokens API (issue #89).

Implements the "Token Measurement" section of
``docs/benchmarks/PUBLIC-BENCHMARK-METHODOLOGY.md`` (roadmap decision 5.8):
count the client-rewrapped message envelope with Claude token counting,
record raw payload tokens separately as diagnostic data, and mark a result
``approximation: true`` when the exact client-wrapped envelope cannot be
recovered.

**Binding pre-flight amendment (issue #89, 2026-07-08):** no third-party
dependency may be added for this -- not the ``anthropic`` SDK, not an
optional extra, not a dev/benchmark dependency group. The live HTTP call
below uses only :mod:`urllib.request` (stdlib). If a genuine SDK need is
ever found, that is a stop-and-comment per the pipeline, not a decision this
module makes unilaterally.

**Live-phase guard:** :class:`LiveClaudeTokenCounter.count` calls
:func:`benchmarks.adapters.guard.require_live_environment` itself, on every
call, regardless of caller -- so this module is structurally incapable of a
network call unless ``BENCHMARK_LIVE_PROVIDERS_ENABLED`` and
``ANTHROPIC_API_KEY`` are both set. CI and unit tests never set those, so
they never reach the network; tests use :class:`FakeTokenCounter` instead.

**Envelope exactness:** :func:`build_client_wrapped_envelope` returns
``approximation=True`` only when handed a provider-adapter mock/live-stub
payload (``benchmarks.adapters.base.AdapterResponse.raw`` from
``MockOpenAIAdapter`` / ``MockGoogleAdapter`` / their live-stub
counterparts). Those adapters never call a real ``openai-python`` /
``google-genai`` client, so there is no real wrapped request body to
recover -- the methodology requires marking that gap honestly rather than
guessing. Cells produced by adapters that do not go through a provider SDK
at all (the no-MCP baseline, or the offline
``benchmarks.adapters.python_docs_mcp_adapter`` retrieval adapter) hand this
module real prompt/tool-call data directly, so this module's own
fixed wrapping of that data is exact, not an approximation.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

from benchmarks.adapters.guard import PROVIDER_API_KEY_ENV, require_live_environment
from benchmarks.runner import BenchmarkCellFailure

#: Anthropic Messages API count-tokens endpoint. Counts a message envelope
#: without generating a response and without being billed -- see
#: https://docs.claude.com/en/api/messages-count-tokens (Anthropic API
#: docs). Called only via ``urllib.request``; no SDK involved.
COUNT_TOKENS_URL = "https://api.anthropic.com/v1/messages/count_tokens"

#: Required API version header for the Anthropic Messages API.
ANTHROPIC_API_VERSION = "2023-06-01"

#: Model id used solely to select a tokenizer for the count-tokens call.
#: This never generates text and is never billed as a completion -- it only
#: selects which Claude tokenizer counts the envelope.
DEFAULT_COUNT_MODEL = "claude-3-5-sonnet-20241022"

#: Per-call HTTP timeout for the live count-tokens request.
REQUEST_TIMEOUT_SECONDS = 30.0

#: Provider id registered in ``benchmarks.adapters.guard.PROVIDER_API_KEY_ENV``
#: for this caller. Not a competitor/model-matrix provider (see that
#: module's docstring).
PROVIDER = "anthropic"

__all__ = [
    "TokenCounter",
    "FakeTokenCounter",
    "LiveClaudeTokenCounter",
    "TokenCountResult",
    "build_client_wrapped_envelope",
    "count_cell_tokens",
]


class TokenCounter(Protocol):
    """Anything that can count tokens for a Claude-format message envelope.

    :func:`count_cell_tokens` depends only on this protocol, so it never
    has to know whether it is talking to :class:`FakeTokenCounter` (tests)
    or :class:`LiveClaudeTokenCounter` (the guarded maintainer-run phase).
    """

    def count(self, messages: list[dict[str, Any]]) -> int:
        """Return the token count for ``messages`` (an Anthropic-shaped envelope)."""
        raise NotImplementedError


@dataclass
class FakeTokenCounter:
    """Deterministic test double for :class:`TokenCounter`. No network I/O.

    Every test in this codebase for Claude token counting must use this
    (or an equivalent fake) -- CI and unit tests never call the real
    Anthropic API. Defaults to counting whitespace-separated words in the
    JSON-serialized envelope, which is deterministic and dependency-free;
    ``tokens_per_call`` overrides that with a fixed count when a test wants
    an exact, predictable number.
    """

    tokens_per_call: int | None = None
    calls: list[list[dict[str, Any]]] = field(default_factory=list)

    def count(self, messages: list[dict[str, Any]]) -> int:
        self.calls.append(messages)
        if self.tokens_per_call is not None:
            return self.tokens_per_call
        return len(json.dumps(messages, sort_keys=True).split())


class LiveClaudeTokenCounter:
    """Real Anthropic count-tokens API caller. Stdlib HTTP only, no SDK.

    Confined to the maintainer-run live phase (PLAN.md Amendment
    2026-07-08): :meth:`count` calls :func:`require_live_environment`
    before doing anything else, so it is structurally incapable of a
    network call without both ``BENCHMARK_LIVE_PROVIDERS_ENABLED`` and
    ``ANTHROPIC_API_KEY`` set. Raises
    :class:`benchmarks.adapters.guard.LiveProviderDisabledError` (a
    ``BenchmarkCellFailure`` subclass) when the guard fails, and
    ``BenchmarkCellFailure`` (category ``tool_failure``) on any HTTP or
    response-shape failure, matching every other adapter's failure-capture
    contract.
    """

    provider = PROVIDER

    def __init__(self, *, model: str = DEFAULT_COUNT_MODEL) -> None:
        self._model = model

    def count(self, messages: list[dict[str, Any]]) -> int:
        require_live_environment(self.provider)
        api_key = os.environ.get(PROVIDER_API_KEY_ENV[self.provider], "").strip()

        body = json.dumps({"model": self._model, "messages": messages}).encode("utf-8")
        request = urllib.request.Request(
            COUNT_TOKENS_URL,
            data=body,
            method="POST",
            headers={
                "content-type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": ANTHROPIC_API_VERSION,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BenchmarkCellFailure(
                "tool_failure", f"Anthropic count-tokens call failed: {exc}"
            ) from exc
        input_tokens = payload.get("input_tokens") if isinstance(payload, dict) else None
        if not isinstance(input_tokens, int):
            raise BenchmarkCellFailure(
                "tool_failure",
                f"Anthropic count-tokens response missing integer 'input_tokens': {payload!r}",
            )
        return input_tokens


@dataclass(frozen=True)
class TokenCountResult:
    """Result of counting one benchmark cell's tokens (fake or live counter)."""

    client_wrapped_tokens: int | None
    raw_payload_tokens: int | None
    approximation: bool
    notes: str
    serialization_latency_ms: float
    """Wall-clock time (ms) spent building the envelope(s) and counting them
    (decision 5.8: report serialization latency alongside token counts)."""


def build_client_wrapped_envelope(
    prompt: str,
    *,
    tool_calls: list[dict[str, Any]] | None = None,
    provider_mock_payload: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Build the Claude-format message envelope for one benchmark cell.

    Implements methodology "Token Measurement" steps 1-3: capture the MCP
    tool response or baseline prompt context, pass it through this
    benchmark's client-side wrapping (a user message carrying the corpus
    prompt, plus one ``tool_result`` content block per recorded tool call),
    and return the resulting envelope for counting.

    ``provider_mock_payload`` is the raw payload from a provider-adapter
    mock/live-stub (``benchmarks.adapters.base.AdapterResponse.raw`` for
    ``MockOpenAIAdapter`` / ``MockGoogleAdapter`` and their live-stub
    counterparts, identifiable by their ``"mock": True`` marker). Those
    adapters never call a real client SDK, so there is no real wrapped
    request body to recover; per the methodology, this function then wraps
    only the prompt text and reports ``approximation=True`` rather than
    guessing at a shape the real SDK would have produced.

    Returns ``(envelope, approximation)``.
    """
    if provider_mock_payload is not None:
        return [{"role": "user", "content": prompt}], True

    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    if tool_calls:
        content_blocks = [
            {
                "type": "tool_result",
                "tool_use_id": f"benchmark-cell-{index}-{call.get('tool', 'tool')}",
                "content": json.dumps(call.get("result"), sort_keys=True, default=str),
                "is_error": bool(call.get("is_error", False)),
            }
            for index, call in enumerate(tool_calls)
        ]
        messages.append({"role": "user", "content": content_blocks})
    return messages, False


def _raw_payload_envelope(
    prompt: str, tool_calls: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    """Build the diagnostic-only raw-payload envelope (unwrapped tool bytes).

    Never reported under ``METHODOLOGY_TOKEN_LABEL`` on its own merits as a
    cross-model metric -- see ``benchmarks.runner``'s token record, which
    keeps ``raw_payload_tokens`` clearly separate from
    ``client_wrapped_tokens``.
    """
    if not tool_calls:
        return [{"role": "user", "content": prompt}]
    raw_results = [call.get("result") for call in tool_calls]
    return [{"role": "user", "content": json.dumps(raw_results, sort_keys=True, default=str)}]


def count_cell_tokens(
    *,
    prompt: str,
    tool_calls: list[dict[str, Any]] | None,
    counter: TokenCounter,
    provider_mock_payload: dict[str, Any] | None = None,
) -> TokenCountResult:
    """Count one benchmark cell's tokens with ``counter`` (fake or live).

    ``counter`` is injected so callers (the runner's live-phase path, or a
    test with :class:`FakeTokenCounter`) share this one code path for
    envelope construction, approximation marking, and serialization-latency
    capture -- only the counter implementation differs.
    """
    started = time.perf_counter()
    envelope, approximation = build_client_wrapped_envelope(
        prompt, tool_calls=tool_calls, provider_mock_payload=provider_mock_payload
    )
    client_wrapped_tokens = counter.count(envelope)
    raw_envelope = _raw_payload_envelope(prompt, tool_calls)
    raw_payload_tokens = counter.count(raw_envelope)
    serialization_latency_ms = round((time.perf_counter() - started) * 1000, 3)

    notes = (
        "approximation: provider-adapter mock/live-stub cannot expose its exact "
        "client-wrapped envelope (no real client SDK call was made); wrapping the "
        "raw prompt only, per PUBLIC-BENCHMARK-METHODOLOGY.md 'Token Measurement'"
        if approximation
        else "counted from this benchmark's client-side wrapped message envelope"
    )
    return TokenCountResult(
        client_wrapped_tokens=client_wrapped_tokens,
        raw_payload_tokens=raw_payload_tokens,
        approximation=approximation,
        notes=notes,
        serialization_latency_ms=serialization_latency_ms,
    )
