"""Neutral value types shared by every agentic module."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Buckets:
    """Provider usage in the four disjoint design §8 buckets."""

    uncached_input: int
    cache_write_input: int
    cached_input: int
    output: int  # includes thinking/reasoning
    reasoning: int = 0  # information only, already inside `output`

    @property
    def total_input(self) -> int:
        return self.uncached_input + self.cache_write_input + self.cached_input
