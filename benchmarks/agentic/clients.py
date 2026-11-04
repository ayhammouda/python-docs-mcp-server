"""Provider clients for the agentic loop: SDK construction, calls and error classes.

Every client sends exactly one HTTP request per ``call`` (#177 item 1): SDK
retries are off, and the loop retries by itself, one ledger unit per attempt.
A conversation without tools sends neither tools nor ``tool_choice``.
"""

from __future__ import annotations

import asyncio
import json
import os
import ssl
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import anthropic
import certifi
import httpx
import httpx2
import openai
from anthropic.types import TextBlock, ToolUseBlock
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from benchmarks.adapters.guard import (
    EGRESS_URL_ENV,
    LIVE_LATCH_ENV,
    PAID_KEY_ENVS,
    PROVIDER_API_KEY_ENV,
    require_live_environment,
)
from benchmarks.agentic.protocol import (
    assert_no_key_shaped,
    assert_no_key_shaped_json,
    scanned_json_bytes,
)
from benchmarks.agentic.schemas import (
    to_anthropic,
    to_gemini,
    to_openai_chat,
    to_openai_responses,
)
from benchmarks.agentic.types import (
    Conversation,
    ModelClient,
    ModelTurn,
    ProviderCallError,
    ToolCall,
    ToolResults,
    UserText,
)
from benchmarks.agentic.usage import (
    anthropic_buckets,
    gemini_buckets,
    ollama_buckets,
    openai_buckets,
)
from benchmarks.model_matrix import ModelFamily
from benchmarks.runner import BenchmarkValidationError, _load_yaml_mapping

#: Live API -> the one provider whose key it may carry.
_LIVE_APIS = {
    "anthropic-messages": "anthropic",
    "openai-responses": "openai",
    "gemini-generate-content": "google",
    "openai-chat-completions": "ollama",  # keyless: OLLAMA_BASE_URL instead
}


def tool_arguments(value: Any) -> tuple[dict[str, Any], str | None]:
    """A JSON object passes; anything else is ``({}, raw)`` and never reaches a tool."""
    if isinstance(value, str):
        assert_no_key_shaped(value, where="tool arguments")
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}, value
        assert_no_key_shaped_json(parsed, where="tool arguments")
        return (parsed, None) if isinstance(parsed, dict) else ({}, value)
    if isinstance(value, dict):
        assert_no_key_shaped_json(value, where="tool arguments")
        return value, None
    return {}, scanned_json_bytes(value, where="tool arguments").decode()


def parse_retry_after(headers: Mapping[str, str]) -> float | None:
    """Seconds to wait from ``retry-after-ms`` or ``retry-after`` (seconds or an HTTP date).

    A negative value is clamped to 0; the loop refuses anything above 120 s.
    """
    try:
        if (ms := headers.get("retry-after-ms")) is not None:
            return max(0.0, float(ms) / 1000)
        value = headers.get("retry-after")
        if value is None:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            when = parsedate_to_datetime(value)
            return max(0.0, (when - datetime.now(UTC)).total_seconds())
    except (TypeError, ValueError):
        return None


def _status_error(status: int, headers: Mapping[str, str], message: str) -> ProviderCallError:
    """429 and 5xx are retried, honouring retry-after; any other status is rejected."""
    if status == 429 or status >= 500:
        kind = "rate_limited" if status == 429 else "server"
        return ProviderCallError(
            kind, message, status=status, retry_after_s=parse_retry_after(headers)
        )
    return ProviderCallError("rejected", message, status=status)


def _utf8_len(payload: Any) -> int:
    # All supported renderers use this boundary, including native assistant replay.
    # Check decoded fields and the exact rendered JSON: encoding can create a token boundary.
    # SDK credentials and HTTP authentication headers are deliberately outside this payload.
    return len(
        scanned_json_bytes(
            payload, where="outgoing provider content", ensure_ascii=False, separators=(",", ":")
        )
    )


#: Pinned, so no *_BASE_URL variable can send a key to another host.
ANTHROPIC_BASE_URL = "https://api.anthropic.com"
OPENAI_BASE_URL = "https://api.openai.com/v1"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/"


def egress_proxy(environ: Mapping[str, str]) -> str | None:
    """The sandbox's egress proxy from ``BENCHMARK_EGRESS_URL``: ``http://<host>:<port>`` only."""
    value = environ.get(EGRESS_URL_ENV)
    if value is None:
        return None
    try:
        parts = urlsplit(value)
        ok = (
            parts.scheme == "http"
            and bool(parts.hostname)
            and parts.port is not None
            and "@" not in parts.netloc
            and not (parts.path or parts.query or parts.fragment)
        )
    except ValueError:  # a port that is not a number
        ok = False
    if not ok:  # never the value: it may hold user info
        raise BenchmarkValidationError(
            f"{EGRESS_URL_ENV} must be http://<host>:<port>, with no user info, path, query "
            "or fragment"
        )
    return value


def _client_args(
    transport: Any | None, proxy: str | None = None, *, http: Any = httpx2
) -> dict[str, Any]:
    """Never follow a redirect: it would be a second request, possibly carrying the key away.

    trust_env=False: no HTTPS_PROXY or SSL_CERT_FILE in a worker's env steers the connection.
    The Anthropic SDK mounts env proxies even then, so each route is mounted explicitly: direct,
    or through ``proxy`` with the direct route's own pinned trust store. ``http`` is the httpx
    module the SDK uses. Tests inject only a transport, which then carries every request; every
    other client setting is the production one.
    """
    # No proxy from the environment. In a sandbox the egress proxy comes only from
    # BENCHMARK_EGRESS_URL, and the container launcher passes no *_PROXY, SSL_CERT_* or
    # REQUESTS_CA_BUNDLE (Task 10).
    route = None
    if proxy is not None and transport is None:
        route = http.AsyncHTTPTransport(proxy=proxy, trust_env=False)
    return {
        "follow_redirects": False,
        "trust_env": False,
        "mounts": {"http://": route, "https://": route, "all://": route},
        **({} if transport is None else {"transport": transport}),
    }


def anthropic_sdk(
    api_key: str,
    timeout_s: float,
    *,
    transport: httpx2.AsyncBaseTransport | None = None,
    proxy: str | None = None,
) -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(
        api_key=api_key,
        base_url=ANTHROPIC_BASE_URL,
        max_retries=0,
        timeout=timeout_s,
        http_client=anthropic.DefaultAsyncHttpxClient(**_client_args(transport, proxy)),
    )


def openai_sdk(
    api_key: str,
    timeout_s: float,
    *,
    transport: httpx2.AsyncBaseTransport | None = None,
    proxy: str | None = None,
) -> openai.AsyncOpenAI:
    return openai.AsyncOpenAI(
        api_key=api_key,
        base_url=OPENAI_BASE_URL,
        max_retries=0,
        timeout=timeout_s,
        http_client=openai.DefaultAsyncHttpxClient(**_client_args(transport, proxy)),
    )


class AnthropicClient:
    """Anthropic Messages: no sampling parameters, no ``cache_control``."""

    provider = "anthropic"
    api = "anthropic-messages"

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
    api = "openai-responses"

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
        if response.status == "failed":  # a 200 that carries a server-side failure
            detail = response.error.message if response.error else "no error detail"
            raise ProviderCallError("server", f"response failed: {detail}")
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


def ollama_sdk(
    base_url: str,
    timeout_s: float,
    *,
    transport: httpx2.AsyncBaseTransport | None = None,
    proxy: str | None = None,
) -> openai.AsyncOpenAI:
    # "The client requires an API key value, but Ollama ignores it" (sandbox note D.1).
    return openai.AsyncOpenAI(
        api_key="ollama",
        base_url=base_url,
        max_retries=0,
        timeout=timeout_s,
        http_client=openai.DefaultAsyncHttpxClient(**_client_args(transport, proxy)),
    )


class OllamaChatClient:
    """Ollama's OpenAI-compatible Chat Completions: Ollama defaults (design §7.1).

    No ``max_tokens``, no ``tool_choice`` (unsupported; auto is Ollama's only behaviour) and no
    sampling parameter is ever sent.
    """

    provider = "ollama"
    api = "openai-chat-completions"

    def __init__(self, sdk: openai.AsyncOpenAI, model_id: str):
        self._sdk = sdk
        self.model_id = model_id

    def request_params(self) -> dict[str, Any]:
        return {"model": self.model_id}

    def _messages(self, entries: Sequence[Any]) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for entry in entries:
            if isinstance(entry, UserText):
                messages.append({"role": "user", "content": entry.text})
            elif isinstance(entry, ToolResults):
                messages += [
                    {"role": "tool", "tool_call_id": r.call_id, "content": r.text}
                    for r in entry.results
                ]
            else:
                messages.append(entry.replay)
        return messages

    def rendered_bytes(self, conv: Conversation, start: int) -> int:
        messages = self._messages(conv.entries[start:])
        if start:
            return _utf8_len(messages)
        system = {"role": "system", "content": conv.system}
        return _utf8_len({"messages": [system, *messages], "tools": to_openai_chat(conv.tools)})

    async def call(self, conv: Conversation) -> ModelTurn:
        kwargs: dict[str, Any] = {
            "model": self.model_id,
            "messages": [{"role": "system", "content": conv.system}, *self._messages(conv.entries)],
        }
        if conv.tools:
            kwargs["tools"] = to_openai_chat(conv.tools)
        try:
            response = await self._sdk.chat.completions.create(**kwargs)
        except openai.APIStatusError as exc:
            raise _status_error(exc.status_code, exc.response.headers, str(exc)) from exc
        except openai.APITimeoutError as exc:
            raise ProviderCallError("timeout", str(exc)) from exc
        except openai.APIConnectionError as exc:
            raise ProviderCallError("connection", str(exc)) from exc
        choice = response.choices[0] if response.choices else None
        message = choice.message if choice else None
        calls: list[ToolCall] = []
        replayed: list[dict[str, Any]] = []
        position = 0
        for call in (message.tool_calls if message else None) or []:
            if call.type != "function":
                continue
            position += 1
            # A call without an id gets one by position; the replay carries the same id, so
            # each tool result pairs with its call.
            call_id = call.id or f"call_{position}"
            arguments, raw = tool_arguments(call.function.arguments)
            calls.append(ToolCall(call_id, call.function.name, arguments, raw))
            replayed.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": call.function.name, "arguments": call.function.arguments},
                }
            )
        content = message.content if message else None
        replay: dict[str, Any] = {"role": "assistant", "content": content}
        if replayed:
            replay["tool_calls"] = replayed
        usage = response.usage.model_dump(mode="json") if response.usage else None
        return ModelTurn(
            text=content or "",
            tool_calls=tuple(calls),
            replay=replay,
            raw_response=response.model_dump(mode="json"),
            raw_usage=usage,
            buckets=None if usage is None else ollama_buckets(usage),
            reported_model=response.model,
            stop_reason=choice.finish_reason if choice else None,
        )


def gemini_sdk(
    api_key: str,
    timeout_s: float,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    proxy: str | None = None,
) -> genai.Client:
    # Without a context the SDK builds its own from SSL_CERT_FILE and SSL_CERT_DIR: one for
    # httpx ("verify") and one for websockets ("ssl").
    ca = ssl.create_default_context(cafile=certifi.where())
    return genai.Client(
        api_key=api_key,
        vertexai=False,
        http_options=genai_types.HttpOptions(
            base_url=GEMINI_BASE_URL,
            timeout=int(timeout_s * 1000),
            retry_options=genai_types.HttpRetryOptions(attempts=1),
            httpx_async_client=httpx.AsyncClient(**_client_args(transport, proxy, http=httpx)),
            # The unused sync client reads no proxy variable either (a SOCKS one would crash it).
            client_args={"verify": ca, "trust_env": False},
            async_client_args={"ssl": ca},
        ),
    )


class GeminiClient:
    """Gemini ``generate_content``: AUTO mode, no automatic calling, no continuation."""

    provider = "google"
    api = "gemini-generate-content"

    def __init__(self, sdk: genai.Client, model_id: str, max_output_tokens: int):
        self._sdk = sdk
        self.model_id = model_id
        self._max_output_tokens = max_output_tokens

    def request_params(self) -> dict[str, Any]:
        return {
            "model": self.model_id,
            "max_output_tokens": self._max_output_tokens,
            "function_calling_mode": "AUTO",  # sent only with tools
            "automatic_function_calling": {"disable": True},
            "automatic_continuation": False,
        }

    def _contents(self, entries: Sequence[Any]) -> list[genai_types.Content]:
        contents: list[genai_types.Content] = []
        provider_ids: set[str] = set()
        for entry in entries:
            if isinstance(entry, UserText):
                contents.append(
                    genai_types.Content(role="user", parts=[genai_types.Part(text=entry.text)])
                )
            elif isinstance(entry, ToolResults):
                # A call without a provider id pairs with its result by position: no id.
                parts = [
                    genai_types.Part(
                        function_response=genai_types.FunctionResponse(
                            id=r.call_id if r.call_id in provider_ids else None,
                            name=r.name,
                            response={"error": r.text} if r.is_error else {"output": r.text},
                        )
                    )
                    for r in entry.results
                ]
                contents.append(genai_types.Content(role="user", parts=parts))
            elif entry.replay is not None:
                contents.append(entry.replay)
                provider_ids = {
                    p.function_call.id
                    for p in entry.replay.parts or []
                    if p.function_call and p.function_call.id
                }
        return contents

    def _config(self, conv: Conversation) -> genai_types.GenerateContentConfig:
        tools: genai_types.ToolListUnion | None = None
        tool_config: genai_types.ToolConfig | None = None
        if conv.tools:
            declarations = [
                genai_types.FunctionDeclaration.model_validate(d) for d in to_gemini(conv.tools)[0]
            ]
            tools = [genai_types.Tool(function_declarations=declarations)]
            mode = genai_types.FunctionCallingConfigMode.AUTO
            tool_config = genai_types.ToolConfig(
                function_calling_config=genai_types.FunctionCallingConfig(mode=mode)
            )
        return genai_types.GenerateContentConfig(
            system_instruction=conv.system,
            tools=tools,
            tool_config=tool_config,
            automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(disable=True),
            # google-genai follows a CONTINUATION stop with extra requests by default.
            automatic_continuation=False,
            max_output_tokens=self._max_output_tokens,
        )

    def rendered_bytes(self, conv: Conversation, start: int) -> int:
        contents = [
            c.model_dump(mode="json", exclude_none=True)
            for c in self._contents(conv.entries[start:])
        ]
        if start:
            return _utf8_len(contents)
        tools = to_gemini(conv.tools)[0]
        return _utf8_len({"systemInstruction": conv.system, "tools": tools, "contents": contents})

    async def call(self, conv: Conversation) -> ModelTurn:
        try:
            response = await self._sdk.aio.models.generate_content(
                model=self.model_id,
                contents=self._contents(conv.entries),
                config=self._config(conv),
            )
        except genai_errors.APIError as exc:  # 4xx, 5xx, and any other status (a redirect)
            headers = getattr(exc.response, "headers", None) or {}
            raise _status_error(exc.code, headers, str(exc)) from exc
        except httpx.TimeoutException as exc:
            raise ProviderCallError("timeout", str(exc)) from exc
        except httpx.TransportError as exc:
            raise ProviderCallError("connection", str(exc)) from exc
        candidate = response.candidates[0] if response.candidates else None
        content = candidate.content if candidate else None
        parts = (content.parts or []) if content else []
        calls = []
        for position, part in enumerate((p for p in parts if p.function_call), 1):
            call = part.function_call
            assert call is not None
            arguments, raw = tool_arguments(call.args if call.args is not None else {})
            calls.append(ToolCall(call.id or f"call_{position}", call.name or "", arguments, raw))
        usage = response.usage_metadata.model_dump(mode="json") if response.usage_metadata else None
        reason = candidate.finish_reason if candidate else None
        return ModelTurn(
            text="".join(p.text for p in parts if p.text and not p.thought),
            tool_calls=tuple(calls),
            replay=content,  # thought signatures go back exactly as received
            raw_response=response.model_dump(mode="json", exclude_none=True),
            raw_usage=usage,
            buckets=None if usage is None else gemini_buckets(usage),
            reported_model=response.model_version,
            stop_reason=None if reason is None else str(getattr(reason, "value", reason)),
        )


_SCRIPT_BUCKETS = {
    "anthropic": anthropic_buckets,
    "openai": openai_buckets,
    "google": gemini_buckets,
    "ollama": ollama_buckets,
}
_SCRIPT_STOP = {"anthropic": "end_turn", "openai": "completed", "google": "STOP", "ollama": "stop"}


def _fill_prompt(value: Any, prompt: str) -> Any:
    if isinstance(value, str):
        return value.replace("{prompt}", prompt)
    if isinstance(value, list):
        return [_fill_prompt(item, prompt) for item in value]
    if isinstance(value, dict):
        return {key: _fill_prompt(item, prompt) for key, item in value.items()}
    return value


class ScriptedClient:
    """Plays a YAML script of turns, errors and usage: CI's stand-in for a provider.

    Steps: ``{tools: [{name, arguments}], usage}``, ``{text, usage, stop_reason}`` or
    ``{error: {kind, status, retry_after_s}}``; any step may set ``delay_s`` and
    ``prompt_contains``. A tools step applies only when every tool it names was given.
    """

    api = "scripted"

    def __init__(self, provider: str, model_id: str, steps: list[dict[str, Any]]):
        self.provider = provider
        self.model_id = model_id
        self._steps = steps
        # Keyed by the Conversation object, held strongly: a bare id(conv) can be reused
        # by CPython once a conversation is freed (owner amendment on #184).
        self._calls: dict[Conversation, int] = {}

    def request_params(self) -> dict[str, Any]:
        return {"model": self.model_id}

    def rendered_bytes(self, conv: Conversation, start: int) -> int:
        rendered: list[Any] = []
        for entry in conv.entries[start:]:
            if isinstance(entry, UserText):
                rendered.append({"role": "user", "content": entry.text})
            elif isinstance(entry, ToolResults):
                rendered.append([[r.call_id, r.text, r.is_error] for r in entry.results])
            else:
                calls = [[c.id, c.name, c.arguments] for c in entry.tool_calls]
                rendered.append({"role": "assistant", "text": entry.text, "calls": calls})
        if start:
            return _utf8_len(rendered)
        tools = to_anthropic(conv.tools)
        return _utf8_len({"system": conv.system, "tools": tools, "messages": rendered})

    def _applies(self, step: dict[str, Any], conv: Conversation, prompt: str) -> bool:
        if "prompt_contains" in step and str(step["prompt_contains"]) not in prompt:
            return False
        given = {t.name for t in conv.tools}
        return all(tool["name"] in given for tool in step.get("tools") or [])

    async def call(self, conv: Conversation) -> ModelTurn:
        prompt = next((e.text for e in conv.entries if isinstance(e, UserText)), "")
        steps = [s for s in self._steps if self._applies(s, conv, prompt)]
        index = self._calls.get(conv, 0)
        self._calls[conv] = index + 1
        if index >= len(steps):
            raise BenchmarkValidationError(
                f"scripted model {self.model_id!r} has no step {index + 1} for this conversation"
            )
        step = steps[index]
        if step.get("delay_s"):
            await asyncio.sleep(float(step["delay_s"]))
        if "error" in step:
            error = step["error"]
            raise ProviderCallError(
                error["kind"],
                f"scripted {error['kind']}",
                status=error.get("status"),
                retry_after_s=error.get("retry_after_s"),
            )
        calls = []
        for position, tool in enumerate(step.get("tools") or [], 1):
            arguments, raw = tool_arguments(_fill_prompt(tool.get("arguments", {}), prompt))
            calls.append(ToolCall(f"call_{position}", tool["name"], arguments, raw))
        usage = step.get("usage")
        default_stop = "tool_use" if calls and self.provider == "anthropic" else None
        return ModelTurn(
            text="" if calls else str(step.get("text", "")),
            tool_calls=tuple(calls),
            replay=None,
            raw_response={"scripted_step": step},
            raw_usage=usage,
            buckets=None if usage is None else _SCRIPT_BUCKETS[self.provider](usage),
            reported_model=self.model_id,
            stop_reason=step.get("stop_reason") or default_stop or _SCRIPT_STOP[self.provider],
        )


async def fetch_model_limits(client: ModelClient) -> dict[str, Any] | None:
    """The model's token limits from its provider's free models endpoint, in one GET.

    Anthropic and Gemini only: the OpenAI and Ollama model endpoints report no limits, so
    they make no request. It reserves no ledger unit (the endpoint is not billed), and an SDK
    error is recorded as ``{"error": "<Type>"}``, never raised.
    """
    try:
        if isinstance(client, AnthropicClient):
            info = await client._sdk.models.retrieve(client.model_id)
            return {"max_input_tokens": info.max_input_tokens, "max_tokens": info.max_tokens}
        if isinstance(client, GeminiClient):
            model = await client._sdk.aio.models.get(model=client.model_id)
            return {
                "max_input_tokens": model.input_token_limit,
                "max_tokens": model.output_token_limit,
            }
    except Exception as exc:  # noqa: BLE001 - recorded in system_info, never fatal
        return {"error": type(exc).__name__}
    return None


def make_client(
    family: ModelFamily, *, timeout_s: float, transport: Any | None = None
) -> ModelClient:
    """The only way production code builds a client; every live API passes the guard."""
    if family.api == "scripted":
        script = Path(str(family.raw.get("script", "")))
        if not script.is_absolute():
            raise BenchmarkValidationError(
                f"scripted model family {family.id!r} needs an absolute 'script' path, "
                f"got {str(script)!r}"
            )
        steps = _load_yaml_mapping(script, "model script").get("steps")
        if not isinstance(steps, list) or not all(isinstance(s, dict) for s in steps):
            raise BenchmarkValidationError(f"model script {script} must hold a 'steps' list")
        return ScriptedClient(family.provider, family.model_id, steps)
    provider = _LIVE_APIS.get(family.api or "")
    if provider is None:
        raise BenchmarkValidationError(
            f"model family {family.id!r} uses api {family.api!r}, which has no client"
        )
    if family.provider != provider:
        raise BenchmarkValidationError(
            f"model family {family.id!r}: api {family.api!r} carries the {provider!r} key, "
            f"not provider {family.provider!r}"
        )
    require_live_environment(provider)
    if provider in LIVE_LATCH_ENV:  # keyless: the Ollama worker holds no paid key at all
        held = [name for name in PAID_KEY_ENVS if os.environ.get(name)]
        if held:
            raise BenchmarkValidationError(
                f"a keyless {provider} worker holds no paid key, but {', '.join(held)} is set"
            )
        base_url = os.environ[LIVE_LATCH_ENV[provider]]
        if not base_url.startswith(("http://", "https://")):
            raise BenchmarkValidationError(
                f"{LIVE_LATCH_ENV[provider]} must start with http:// or https://"
            )
        sdk = ollama_sdk(base_url, timeout_s, transport=transport, proxy=egress_proxy(os.environ))
        return OllamaChatClient(sdk, family.model_id)
    own_key_env = PROVIDER_API_KEY_ENV[provider]
    others = [name for name in PAID_KEY_ENVS if name != own_key_env and os.environ.get(name)]
    if others:
        raise BenchmarkValidationError(
            f"a worker holds at most one paid key: {', '.join(others)} is set beside {own_key_env}"
        )
    if family.max_output_tokens is None:
        raise BenchmarkValidationError(f"model family {family.id!r} must set max_output_tokens")
    proxy = egress_proxy(os.environ)
    key = os.environ[own_key_env]
    if provider == "anthropic":
        sdk = anthropic_sdk(key, timeout_s, transport=transport, proxy=proxy)
        return AnthropicClient(sdk, family.model_id, family.max_output_tokens)
    if provider == "openai":
        sdk = openai_sdk(key, timeout_s, transport=transport, proxy=proxy)
        return OpenAIResponsesClient(sdk, family.model_id, family.max_output_tokens)
    sdk = gemini_sdk(key, timeout_s, transport=transport, proxy=proxy)
    return GeminiClient(sdk, family.model_id, family.max_output_tokens)
