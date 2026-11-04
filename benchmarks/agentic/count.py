"""Post-hoc normalized token counting (design §8; owner ruling 7).

Every model call of a scored trajectory is counted with the Anthropic count-tokens
endpoint, after the run, from the stored canonical transcript:

- input: the request as sent (system, tools and the messages before the call's turn);
- output: the envelope with the turn minus the envelope with a stand-in turn, both
  closed by a user message, since Claude 4.6 and later reject a trailing assistant turn.

Tool ids are renamed per turn (``c{turn}_{n}``), a tool name the system never offered
gets a name-only stub, and a tool call whose raw arguments were not a JSON object is
counted from its raw text. The endpoint is free, so counting reserves no ledger unit.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import anthropic

from benchmarks.adapters.claude_tokens import ANTHROPIC_API_VERSION, DEFAULT_COUNT_MODEL
from benchmarks.adapters.guard import redact_api_keys
from benchmarks.agentic.clients import anthropic_sdk, tool_arguments
from benchmarks.agentic.loop import MAX_RETRIES, RETRYABLE, backoff_s
from benchmarks.agentic.protocol import KEY_SHAPED
from benchmarks.agentic.schemas import schemas_sha256, to_anthropic
from benchmarks.agentic.types import ProviderCallError, ToolSpec
from benchmarks.grading.batch import _call, live_key, scripted_source
from benchmarks.model_matrix import METHODOLOGY_TOKEN_LABEL, ModelFamily, model_family_from_raw
from benchmarks.runner import BenchmarkValidationError

NOTES = (
    "Anthropic count-tokens endpoint, which its docs call an estimate; counted after the run "
    "(design §8)"
)
_CLOSE = "."


@dataclass(frozen=True)
class Envelope:
    system: str
    tools: list[dict[str, Any]] | None  # None: the request omits "tools" (no-docs, no stubs)
    messages: list[dict[str, Any]]


@dataclass(frozen=True)
class CountItem:
    cell: str
    corpus_id: str
    tool_model_key: str
    system: str
    tools: list[dict[str, Any]] | None
    messages: list[dict[str, Any]]  # canonical, ids normalized, raw arguments restored
    calls: list[int]  # message_count of each model call that returned a turn
    stubbed_tools: list[str]
    uncounted_calls: int  # calls that returned no turn (a rejected request)
    raw_argument_calls: int = 0
    system_prompt_sha256: str = ""
    tools_sha256: str = ""


def _raw_arguments(provider: str, raw_response: Any) -> list[Any]:
    """Each tool call's raw argument value from a call's raw response, in order."""
    raw = raw_response if isinstance(raw_response, Mapping) else {}
    if "scripted_step" in raw:
        return [t.get("arguments", {}) for t in raw["scripted_step"].get("tools") or []]
    if provider == "anthropic":
        return [b.get("input") for b in raw.get("content") or [] if b.get("type") == "tool_use"]
    if provider == "openai":
        return [
            o.get("arguments") for o in raw.get("output") or [] if o.get("type") == "function_call"
        ]
    parts = [
        p
        for c in raw.get("candidates") or []
        for p in ((c.get("content") or {}).get("parts") or [])
    ]
    return [p["function_call"].get("args") for p in parts if p.get("function_call")]


def count_item_from_transcript(
    transcript: Mapping[str, Any], *, system_prompt: str, tools_raw: list[dict[str, Any]]
) -> CountItem:
    """One trajectory's countable messages; a hash mismatch is a harness error."""
    if hashlib.sha256(system_prompt.encode("utf-8")).hexdigest() != transcript.get(
        "system_prompt_sha256"
    ):
        raise BenchmarkValidationError(
            f"{transcript.get('competitor_id')}/{transcript.get('corpus_id')}: the system prompt "
            "does not match the transcript's system_prompt_sha256"
        )
    specs = [ToolSpec(t["name"], t.get("description") or "", t["inputSchema"]) for t in tools_raw]
    if schemas_sha256([asdict(s) for s in specs]) != transcript.get("tools_sha256"):
        raise BenchmarkValidationError(
            f"{transcript.get('competitor_id')}/{transcript.get('corpus_id')}: the tools "
            "rebuilt from tools_raw do not match the transcript's tools_sha256"
        )
    provider = str(transcript.get("provider") or "")
    raw_by_message = {
        call["message_count"]: _raw_arguments(provider, call.get("raw_response"))
        for call in transcript.get("model_calls") or []
        if call.get("tool_calls_requested") is not None
    }
    known = {s.name for s in specs}
    stubbed: list[str] = []
    messages: list[dict[str, Any]] = []
    renamed: dict[str, str] = {}
    turn = raw_calls = 0
    for position, message in enumerate(transcript.get("canonical_messages") or []):
        content = message["content"]
        if message["role"] == "assistant":
            turn += 1
            renamed = {}
            raw_values = raw_by_message.get(position, [])
            blocks: list[dict[str, Any]] = []
            uses = 0
            for block in content:
                if block.get("type") != "tool_use":
                    if block.get("type") != "text" or block.get("text"):  # empty text dropped
                        blocks.append(block)
                    continue
                uses += 1
                new_id = f"c{turn}_{uses}"
                renamed[block["id"]] = new_id
                arguments = block.get("input") or {}
                if uses <= len(raw_values):
                    _, raw = tool_arguments(raw_values[uses - 1])
                    if raw is not None:  # malformed: counted from its raw text
                        arguments, raw_calls = {"raw_arguments": raw}, raw_calls + 1
                if block["name"] not in known and block["name"] not in stubbed:
                    stubbed.append(block["name"])
                blocks.append({**block, "id": new_id, "input": arguments})
            messages.append({"role": "assistant", "content": blocks})
        elif isinstance(content, list):
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            **b,
                            "tool_use_id": renamed.get(b.get("tool_use_id"), b.get("tool_use_id")),
                        }
                        for b in content
                    ],
                }
            )
        else:
            messages.append({"role": "user", "content": content})
    tools = to_anthropic(specs) + [
        {"name": name, "input_schema": {"type": "object"}} for name in stubbed
    ]
    calls = transcript.get("model_calls") or []
    return CountItem(
        cell=str(transcript.get("competitor_id")),
        corpus_id=str(transcript.get("corpus_id")),
        tool_model_key=str(transcript.get("tool_model_key")),
        system=system_prompt,
        tools=tools or None,
        messages=messages,
        calls=[c["message_count"] for c in calls if c.get("tool_calls_requested") is not None],
        stubbed_tools=stubbed,
        uncounted_calls=sum(1 for c in calls if c.get("tool_calls_requested") is None),
        raw_argument_calls=raw_calls,
        system_prompt_sha256=str(transcript.get("system_prompt_sha256")),
        tools_sha256=str(transcript.get("tools_sha256")),
    )


class CountClient(Protocol):
    async def count(self, envelope: Envelope) -> int: ...  # raises ProviderCallError


def _body(envelope: Envelope) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if envelope.system:
        body["system"] = envelope.system
    if envelope.tools is not None:
        body["tools"] = envelope.tools
    body["messages"] = envelope.messages
    return body


class ScriptedCountClient:
    """ceil(UTF-8 bytes of the canonical envelope JSON / 4); records every envelope."""

    def __init__(self, errors: Sequence[Mapping[str, Any]] = ()) -> None:
        self.seen: list[Envelope] = []
        self._errors = list(errors)

    @staticmethod
    def tokens(envelope: Envelope) -> int:
        text = json.dumps(
            _body(envelope), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return math.ceil(len(text.encode("utf-8")) / 4)

    async def count(self, envelope: Envelope) -> int:
        self.seen.append(envelope)
        if self._errors:
            error = self._errors.pop(0)
            raise ProviderCallError(
                error["kind"], "scripted count error", status=error.get("status")
            )
        return self.tokens(envelope)


class AnthropicCountClient:
    """``messages.count_tokens``: free, rate-limited, one request per count."""

    def __init__(self, sdk: anthropic.AsyncAnthropic, model_id: str) -> None:
        self._sdk = sdk
        self.model_id = model_id

    @staticmethod
    def request(envelope: Envelope) -> dict[str, Any]:
        return _body(envelope)

    async def count(self, envelope: Envelope) -> int:
        result = await _call(
            self._sdk.messages.count_tokens(model=self.model_id, **self.request(envelope))
        )
        return int(result.input_tokens)


def count_family(script: Path | None = None) -> ModelFamily:
    """The count model (claude-sonnet-5-5), or a scripted counter for tests."""
    raw: dict[str, Any] = {
        "id": "token-counter",
        "provider": "anthropic",
        "model_id": DEFAULT_COUNT_MODEL,
        "api": "anthropic-messages" if script is None else "scripted",
        "arm": "hosted",
        "client": "anthropic" if script is None else "scripted",
        "token_count_method": "claude_normalized_payload",
        "latency_scope": "not_applicable",
        "headline_eligible": False,
        "headline_ineligibility_reason": "the token counter, not an evaluated model",
        "max_input_tokens": 1_000_000,
        "max_output_tokens": 1,
        "framing_allowance_tokens": 256,
        "output_cap_includes_reasoning": True,
    }
    if script is not None:
        raw["script"] = str(script)
    return model_family_from_raw(raw, label="count family")


def make_count_client(
    family: ModelFamily, *, timeout_s: float, transport: Any | None = None
) -> CountClient:
    """The only way production code builds a count client; a live one passes the guard."""
    if family.api == "scripted":
        return ScriptedCountClient(scripted_source(family, "count script").get("errors") or [])
    provider, key = live_key(family)
    if provider != "anthropic":
        raise BenchmarkValidationError("only the Anthropic count-tokens endpoint counts tokens")
    # shortcut: no egress proxy yet; once PR 4 lands, pass proxy=egress_proxy(os.environ)
    # as make_client does (PR 5 part 2, Task 9b).
    return AnthropicCountClient(anthropic_sdk(key, timeout_s, transport=transport), family.model_id)


def _closing(turn: Mapping[str, Any]) -> dict[str, Any]:
    ids = [b["id"] for b in turn["content"] if b.get("type") == "tool_use"]
    if ids:
        return {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": i, "content": _CLOSE} for i in ids],
        }
    return {"role": "user", "content": _CLOSE}


def _stand_in(turn: Mapping[str, Any]) -> dict[str, Any]:
    uses = [{**b, "input": {}} for b in turn["content"] if b.get("type") == "tool_use"]
    return {"role": "assistant", "content": uses or [{"type": "text", "text": _CLOSE}]}


def _redacted(exc: ProviderCallError) -> str:
    text = redact_api_keys(f"{exc.kind} (HTTP {exc.status}): {exc}")
    return " ".join(KEY_SHAPED.sub("[redacted]", text).split())[:300]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


async def count_item(
    client: CountClient,
    item: CountItem,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict[str, Any]:
    """The token record for one trajectory: counted, or failed with a redacted error."""
    count_calls = 0

    async def counted(envelope: Envelope) -> int:
        nonlocal count_calls
        retries = 0
        while True:
            count_calls += 1
            try:
                return await client.count(envelope)
            except ProviderCallError as exc:
                retries += 1
                delay = (
                    backoff_s(retries, exc.retry_after_s)
                    if exc.kind in RETRYABLE and retries <= MAX_RETRIES
                    else None
                )
                if delay is None:
                    raise
                await sleep(delay)

    per_call: list[dict[str, int]] = []
    clamped = 0
    status, error, total, payload = "counted", None, None, None
    try:
        for index, position in enumerate(item.calls):
            prefix, turn = item.messages[:position], item.messages[position]
            input_tokens = await counted(Envelope(item.system, item.tools, prefix))
            output_tokens = 0
            if turn["content"]:  # an empty turn counts as its stand-in: output 0
                closing = _closing(turn)
                with_turn = await counted(
                    Envelope(item.system, item.tools, [*prefix, turn, closing])
                )
                stand_in = await counted(
                    Envelope(item.system, item.tools, [*prefix, _stand_in(turn), closing])
                )
                output_tokens = with_turn - stand_in
                if output_tokens < 0:
                    clamped, output_tokens = clamped + 1, 0
            per_call.append(
                {"index": index, "input_tokens": input_tokens, "output_tokens": output_tokens}
            )
        results = [
            str(b.get("content") or "")
            for m in item.messages
            if m["role"] == "user" and isinstance(m["content"], list)
            for b in m["content"]
            if b.get("type") == "tool_result"
        ]
        payload = 0
        if results:
            docs = await counted(
                Envelope("", None, [{"role": "user", "content": "\n\n".join(results)}])
            )
            baseline = await counted(Envelope("", None, [{"role": "user", "content": _CLOSE}]))
            payload = max(0, docs - baseline)
        total = sum(c["input_tokens"] + c["output_tokens"] for c in per_call)
    except ProviderCallError as exc:
        status, error, per_call, payload = "failed", _redacted(exc), [], None
    return {
        "competitor_id": item.cell,
        "corpus_id": item.corpus_id,
        "tool_model_key": item.tool_model_key,
        "token_label": METHODOLOGY_TOKEN_LABEL,
        "count_model": getattr(client, "model_id", DEFAULT_COUNT_MODEL),
        "anthropic_version": ANTHROPIC_API_VERSION,
        "status": status,
        "approximation": False,
        "client_wrapped_tokens": total,
        "raw_payload_tokens": payload,
        "per_call": per_call,
        "stubbed_tools": item.stubbed_tools,
        "raw_argument_calls": item.raw_argument_calls,
        "uncounted_calls": item.uncounted_calls,
        "negative_output_clamped": clamped,
        "ids_normalized": True,
        "system_prompt_sha256": item.system_prompt_sha256,
        "tools_sha256": item.tools_sha256,
        "count_calls": count_calls,
        "notes": NOTES,
        "counted_at": _utc_now(),
        "error": error,
    }
