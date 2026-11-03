"""Provider clients for the agentic loop: SDK construction, calls and error classes.

Every client sends exactly one HTTP request per ``call`` (#177 item 1): SDK
retries are off, and the loop retries by itself, one ledger unit per attempt.
A conversation without tools sends neither tools nor ``tool_choice``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import anthropic
import httpx2
import openai
from anthropic.types import TextBlock, ToolUseBlock

from benchmarks.agentic.schemas import to_anthropic, to_openai_responses
from benchmarks.agentic.types import (
    Conversation,
    ModelTurn,
    ProviderCallError,
    ToolCall,
    ToolResults,
    UserText,
)
from benchmarks.agentic.usage import anthropic_buckets, openai_buckets


def tool_arguments(value: Any) -> tuple[dict[str, Any], str | None]:
    """A JSON object passes; anything else is ``({}, raw)`` and never reaches a tool."""
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}, value
    if isinstance(parsed, dict):
        return parsed, None
    return {}, value if isinstance(value, str) else json.dumps(value)


def parse_retry_after(headers: Mapping[str, str]) -> float | None:
    """Seconds to wait from ``retry-after-ms`` or ``retry-after`` (seconds or an HTTP date)."""
    try:
        if (ms := headers.get("retry-after-ms")) is not None:
            return float(ms) / 1000
        value = headers.get("retry-after")
        if value is None:
            return None
        try:
            return float(value)
        except ValueError:
            when = parsedate_to_datetime(value)
            return max(0.0, (when - datetime.now(UTC)).total_seconds())
    except (TypeError, ValueError):
        return None


def _status_error(status: int, headers: Mapping[str, str], message: str) -> ProviderCallError:
    if status == 429:
        return ProviderCallError(
            "rate_limited", message, status=status, retry_after_s=parse_retry_after(headers)
        )
    kind = "server" if status >= 500 else "rejected"
    return ProviderCallError(kind, message, status=status)


def _utf8_len(payload: Any) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def anthropic_sdk(
    api_key: str, timeout_s: float, *, http_client: httpx2.AsyncClient | None = None
) -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(
        api_key=api_key, max_retries=0, timeout=timeout_s, http_client=http_client
    )


def openai_sdk(
    api_key: str,
    timeout_s: float,
    *,
    base_url: str | None = None,
    http_client: httpx2.AsyncClient | None = None,
) -> openai.AsyncOpenAI:
    return openai.AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        max_retries=0,
        timeout=timeout_s,
        http_client=http_client,
    )


class AnthropicClient:
    """Anthropic Messages: no sampling parameters, no ``cache_control``."""

    provider = "anthropic"

    def __init__(self, sdk: anthropic.AsyncAnthropic, model_id: str, max_output_tokens: int):
        self._sdk = sdk
        self.model_id = model_id
        self._max_output_tokens = max_output_tokens

    def request_params(self) -> dict[str, Any]:
        return {
            "model": self.model_id,
            "max_tokens": self._max_output_tokens,
            "tool_choice": {"type": "auto"},  # sent only with tools
        }

    def _messages(self, entries: Sequence[Any]) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for entry in entries:
            if isinstance(entry, UserText):
                messages.append({"role": "user", "content": entry.text})
            elif isinstance(entry, ToolResults):
                content = [
                    {
                        "type": "tool_result",
                        "tool_use_id": r.call_id,
                        "content": r.text,
                        "is_error": r.is_error,
                    }
                    for r in entry.results
                ]
                messages.append({"role": "user", "content": content})
            else:
                messages.append({"role": "assistant", "content": entry.replay})
        return messages

    def rendered_bytes(self, conv: Conversation, start: int) -> int:
        messages = self._messages(conv.entries[start:])
        if start:
            return _utf8_len(messages)
        tools = to_anthropic(conv.tools)
        return _utf8_len({"system": conv.system, "tools": tools, "messages": messages})

    async def call(self, conv: Conversation) -> ModelTurn:
        kwargs: dict[str, Any] = {
            "model": self.model_id,
            "max_tokens": self._max_output_tokens,
            "system": conv.system,
            "messages": self._messages(conv.entries),
        }
        if conv.tools:
            kwargs |= {"tools": to_anthropic(conv.tools), "tool_choice": {"type": "auto"}}
        try:
            response = await self._sdk.messages.create(**kwargs)
        except anthropic.APIStatusError as exc:
            raise _status_error(exc.status_code, exc.response.headers, str(exc)) from exc
        except anthropic.APITimeoutError as exc:
            raise ProviderCallError("timeout", str(exc)) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderCallError("connection", str(exc)) from exc
        calls = []
        for block in response.content:
            if isinstance(block, ToolUseBlock):
                arguments, raw = tool_arguments(block.input)
                calls.append(ToolCall(block.id, block.name, arguments, raw))
        usage = response.usage.model_dump(mode="json") if response.usage else None
        return ModelTurn(
            text="".join(b.text for b in response.content if isinstance(b, TextBlock)),
            tool_calls=tuple(calls),
            # Thinking blocks and their signatures go back exactly as received.
            replay=[block.to_dict(mode="json") for block in response.content],
            raw_response=response.model_dump(mode="json"),
            raw_usage=usage,
            buckets=None if usage is None else anthropic_buckets(usage),
            reported_model=response.model,
            stop_reason=response.stop_reason,
        )


class OpenAIResponsesClient:
    """OpenAI Responses: ``store=False``, encrypted reasoning replayed, no prompt-cache option."""

    provider = "openai"

    def __init__(self, sdk: openai.AsyncOpenAI, model_id: str, max_output_tokens: int):
        self._sdk = sdk
        self.model_id = model_id
        self._max_output_tokens = max_output_tokens

    def request_params(self) -> dict[str, Any]:
        return {
            "model": self.model_id,
            "max_output_tokens": self._max_output_tokens,
            "store": False,
            "include": ["reasoning.encrypted_content"],
            "tool_choice": "auto",  # sent only with tools
        }

    def _input(self, entries: Sequence[Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for entry in entries:
            if isinstance(entry, UserText):
                items.append({"role": "user", "content": entry.text})
            elif isinstance(entry, ToolResults):
                items += [
                    {"type": "function_call_output", "call_id": r.call_id, "output": r.text}
                    for r in entry.results
                ]
            else:
                items += entry.replay
        return items

    def rendered_bytes(self, conv: Conversation, start: int) -> int:
        items = self._input(conv.entries[start:])
        if start:
            return _utf8_len(items)
        tools = to_openai_responses(conv.tools)
        return _utf8_len({"instructions": conv.system, "tools": tools, "input": items})

    async def call(self, conv: Conversation) -> ModelTurn:
        kwargs: dict[str, Any] = {
            "model": self.model_id,
            "instructions": conv.system,
            "input": self._input(conv.entries),
            "max_output_tokens": self._max_output_tokens,
            "store": False,
            "include": ["reasoning.encrypted_content"],
        }
        if conv.tools:
            kwargs |= {"tools": to_openai_responses(conv.tools), "tool_choice": "auto"}
        try:
            response = await self._sdk.responses.create(**kwargs)
        except openai.APIStatusError as exc:
            raise _status_error(exc.status_code, exc.response.headers, str(exc)) from exc
        except openai.APITimeoutError as exc:
            raise ProviderCallError("timeout", str(exc)) from exc
        except openai.APIConnectionError as exc:
            raise ProviderCallError("connection", str(exc)) from exc
        calls, texts = [], []
        for item in response.output:
            if item.type == "function_call":
                arguments, raw = tool_arguments(item.arguments)
                calls.append(ToolCall(item.call_id, item.name, arguments, raw))
            elif item.type == "message":
                texts += [part.text for part in item.content if part.type == "output_text"]
        usage = response.usage.model_dump(mode="json") if response.usage else None
        details = response.incomplete_details
        return ModelTurn(
            text="".join(texts),
            tool_calls=tuple(calls),
            replay=[item.to_dict(mode="json") for item in response.output],
            raw_response=response.model_dump(mode="json"),
            raw_usage=usage,
            buckets=None if usage is None else openai_buckets(usage),
            reported_model=response.model,
            stop_reason=details.reason
            if response.status == "incomplete" and details is not None
            else response.status,
        )
