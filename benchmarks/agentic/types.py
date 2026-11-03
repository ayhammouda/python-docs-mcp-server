"""Neutral value types shared by every agentic module."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class ToolSpec:
    """One tool as an MCP server advertises it."""

    name: str
    description: str
    input_schema: dict[str, Any]


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

    @property
    def is_empty(self) -> bool:
        """All zero: no usage was really reported."""
        return self.total_input == 0 and self.output == 0


@dataclass(frozen=True)
class ToolCall:
    id: str  # provider id, or "call_<n>" (1-based within the turn) when missing
    name: str
    arguments: dict[str, Any]
    raw_arguments: str | None = None  # set only when the arguments were not a JSON object


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    name: str
    text: str  # as sent, after truncation
    is_error: bool


@dataclass(frozen=True)
class UserText:
    text: str


@dataclass(frozen=True)
class ToolResults:
    results: tuple[ToolResult, ...]


@dataclass(frozen=True)
class ModelTurn:
    text: str  # visible text blocks joined with ""
    tool_calls: tuple[ToolCall, ...]
    replay: Any  # provider-native assistant content, appended back unchanged
    raw_response: dict[str, Any]  # SDK response model_dump(mode="json")
    raw_usage: dict[str, Any] | None
    buckets: Buckets | None
    reported_model: str | None
    stop_reason: str | None  # normalized per provider


@dataclass(eq=False)  # identity equality: a conversation is one object, never compared by value
class Conversation:
    system: str
    tools: list[ToolSpec]
    entries: list[UserText | ModelTurn | ToolResults]


ErrorKind = Literal["rejected", "rate_limited", "server", "timeout", "connection"]


class ProviderCallError(Exception):
    """One failed provider request, classified for the ledger and the retry policy."""

    def __init__(
        self,
        kind: ErrorKind,
        message: str,
        *,
        status: int | None = None,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(message)
        self.kind: ErrorKind = kind
        self.status = status
        self.retry_after_s = retry_after_s


class ModelClient(Protocol):
    provider: str
    model_id: str

    def request_params(self) -> dict[str, Any]:
        """Every setting sent except system, tools and messages."""
        raise NotImplementedError

    def rendered_bytes(self, conv: Conversation, start: int) -> int:
        """UTF-8 bytes of the provider-rendered request.

        System, tools and ``entries[start:]`` when ``start == 0``, else ``entries[start:]`` only.
        """
        raise NotImplementedError

    async def call(self, conv: Conversation) -> ModelTurn:
        """One HTTP request; raises :class:`ProviderCallError`."""
        raise NotImplementedError
