"""Design §8 usage buckets per hosted provider.

Each function takes the SDK usage object's ``model_dump()`` (snake_case keys).
A missing or ``None`` count is 0.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from benchmarks.agentic.types import Buckets
from benchmarks.runner import BenchmarkValidationError


def _count(usage: Mapping[str, Any] | None, key: str) -> int:
    value = (usage or {}).get(key)
    return int(value) if value is not None else 0


def _buckets(uncached: int, cache_write: int, cached: int, output: int, reasoning: int) -> Buckets:
    if uncached < 0:
        raise BenchmarkValidationError(
            f"provider usage gives a negative uncached input ({uncached}); the usage object "
            "does not match the design section 8 bucket rules"
        )
    return Buckets(uncached, cache_write, cached, output, reasoning)


def anthropic_buckets(usage: Mapping[str, Any]) -> Buckets:
    return _buckets(
        _count(usage, "input_tokens"),
        _count(usage, "cache_creation_input_tokens"),
        _count(usage, "cache_read_input_tokens"),
        _count(usage, "output_tokens"),
        0,
    )


def openai_buckets(usage: Mapping[str, Any]) -> Buckets:
    """Responses API usage: cached and cache-write tokens are nested inside input."""
    details = usage.get("input_tokens_details")
    cached = _count(details, "cached_tokens")
    cache_write = _count(details, "cache_write_tokens")
    return _buckets(
        _count(usage, "input_tokens") - cached - cache_write,
        cache_write,
        cached,
        _count(usage, "output_tokens"),
        _count(usage.get("output_tokens_details"), "reasoning_tokens"),
    )


def gemini_buckets(usage: Mapping[str, Any]) -> Buckets:
    cached = _count(usage, "cached_content_token_count")
    thoughts = _count(usage, "thoughts_token_count")
    return _buckets(
        _count(usage, "prompt_token_count") - cached + _count(usage, "tool_use_prompt_token_count"),
        0,
        cached,
        _count(usage, "candidates_token_count") + thoughts,
        thoughts,
    )
