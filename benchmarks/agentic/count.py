"""Post-hoc normalized token counting (design §8; owner ruling 7).

Every model call of a scored trajectory is counted with the Anthropic count-tokens
endpoint, after the run, from the stored canonical transcript:

- input: the request as sent (system, tools and the messages before the call's turn);
- output: the envelope with the turn minus the envelope with a stand-in turn, both
  closed by a user message, since Claude 4.6 and later reject a trailing assistant turn.

Tool ids are renamed per turn (``c{turn}_{n}``), a tool name the system never offered
gets a name-only stub, and a tool call whose raw arguments were not a JSON object is
counted from its raw text. The endpoint is free, so counting reserves no ledger unit.

``python -m benchmarks agentic-count-tokens --run-dir DIR`` writes a plan. Add
``--launcher container --image sha256:... --keys-file PATH --live`` to execute,
after CI refusal and interactive confirmation. ``--index PATH`` selects the docs index;
``--dry-run`` plans explicitly and ``--all`` recounts even successful records.
Inputs and result history live under grading/; tokens/ holds the current projection.
Each container invocation owns grading/sandbox/<count_run_id>/ and never changes
the answering run's isolation evidence.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import math
import os
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import anthropic

from benchmarks.adapters.claude_tokens import ANTHROPIC_API_VERSION, DEFAULT_COUNT_MODEL
from benchmarks.adapters.guard import redact_api_keys
from benchmarks.agentic import live as live_safety
from benchmarks.agentic.clients import anthropic_sdk, egress_proxy, tool_arguments
from benchmarks.agentic.count_evidence import CountItem as CountItem
from benchmarks.agentic.loop import MAX_RETRIES, RETRYABLE, backoff_s
from benchmarks.agentic.protocol import (
    KEY_SHAPED,
    assert_no_key_shaped,
    assert_no_key_shaped_json,
    scanned_json_bytes,
)
from benchmarks.agentic.schemas import schemas_sha256, to_anthropic
from benchmarks.agentic.types import ProviderCallError, ToolSpec
from benchmarks.agentic.worker_ports import TaskChannel, TaskHost
from benchmarks.grading.batch import _call, check_error_content, live_key, scripted_source
from benchmarks.model_matrix import METHODOLOGY_TOKEN_LABEL, ModelFamily, model_family_from_raw
from benchmarks.runner import BenchmarkValidationError

if TYPE_CHECKING:
    from benchmarks.agentic.host import Launcher

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
    if provider == "ollama":
        choices = raw.get("choices") or []
        message = choices[0].get("message") if choices else None
        return [
            call["function"]["arguments"]
            for call in (message or {}).get("tool_calls") or []
            if call.get("type") == "function"
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


@dataclass(frozen=True)
class _CountMethod:
    provider: str
    api: str
    model_id: str
    token_label: str
    notes: str

    def record_fields(self) -> dict[str, Any]:
        endpoint = self.api == "anthropic-messages"
        return {
            "execution": {"provider": self.provider, "api": self.api, "model_id": self.model_id},
            "count_model": self.model_id,
            "token_label": self.token_label,
            "approximation": not endpoint,
            "anthropic_version": ANTHROPIC_API_VERSION if endpoint else None,
            "notes": self.notes,
        }


def _anthropic_method(model_id: str) -> _CountMethod:
    return _CountMethod("anthropic", "anthropic-messages", model_id, METHODOLOGY_TOKEN_LABEL, NOTES)


_SCRIPTED_METHOD = _CountMethod(
    "scripted",
    "scripted",
    "bytes/4",
    "Scripted token estimate",
    "Scripted estimate: ceil(UTF-8 bytes of the canonical envelope JSON / 4).",
)
_UNKNOWN_METHOD = _CountMethod(
    "unknown",
    "unknown",
    "unknown",
    "Unverified token count",
    "Unknown counting method.",
)


def _count_method(client: CountClient) -> _CountMethod:
    # Count-only clients remain valid, but cannot establish endpoint provenance.
    method = getattr(client, "_method", None)
    return method if isinstance(method, _CountMethod) else _UNKNOWN_METHOD


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

    _method = _SCRIPTED_METHOD

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

    @property
    def _method(self) -> _CountMethod:
        return _anthropic_method(self.model_id)

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
    return AnthropicCountClient(
        anthropic_sdk(key, timeout_s, transport=transport, proxy=egress_proxy(os.environ)),
        family.model_id,
    )


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
        **_count_method(client).record_fields(),
        "status": status,
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
        "counted_at": _utc_now(),
        "error": error,
    }


async def run_count_task(job: dict[str, Any], host: TaskHost, channel: TaskChannel) -> None:
    """Pull free items; heartbeat after each attempt unless a credential check terminates it."""

    def check(value: object) -> None:
        scanned_json_bytes(
            value, where="count provider content", check_content=channel.check_content
        )

    check(job["family"])
    family = model_family_from_raw(job["family"])
    client = make_count_client(family, timeout_s=job["caps"]["model_call_timeout_s"])
    check(getattr(client, "model_id", family.model_id))

    class HeartbeatClient:
        _method = _count_method(client)

        async def count(self, envelope: Envelope) -> int:
            # Check decoded strings too: JSON escaping must not hide a key from the scan.
            check(_body(envelope))
            try:
                tokens = await client.count(envelope)
            except ProviderCallError as exc:
                # Detect before count_item can redact, retry, or continue with another item.
                check_error_content(exc, check_content=channel.check_content)
                channel.send({"type": "heartbeat"})
                raise
            check(tokens)
            if type(tokens) is not int or tokens < 0:
                raise BenchmarkValidationError("invalid count endpoint result")
            channel.send({"type": "heartbeat"})
            return tokens

    wrapped = HeartbeatClient()
    while True:
        reply = await host.request({"type": "next_item"})
        if reply.get("type") == "no_more_items":
            break
        if reply.get("type") != "item":
            raise BenchmarkValidationError("expected a count item")
        wire = reply["item"]
        # Later turns and tool results are available now: refuse the whole item
        # before even its first (otherwise safe) prefix is sent to the count API.
        check(wire)
        if wire.get("kind") == "envelope":
            record: dict[str, Any] = {"name": wire["name"], "tokens": None, "error": None}
            try:
                record["tokens"] = await wrapped.count(Envelope(**wire["envelope"]))
            except ProviderCallError as exc:
                record.update(error=_redacted(exc), status=exc.status)
            frame = {"type": "counted", "item_id": wire["item_id"], **record}
        elif wire.get("kind") == "trajectory":
            record = await count_item(wrapped, CountItem(**wire["item"]))
            frame = {"type": "counted", "item_id": wire["item_id"], "record": record}
        else:
            raise BenchmarkValidationError("unknown count item kind")
        channel.send(frame)
    channel.send({"type": "done"})


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,240}", value):
        raise BenchmarkValidationError("invalid count coordinate")
    assert_no_key_shaped(value, where="count coordinate")
    return value


def _coordinate(value: Any) -> str:
    # Import lazily: count_evidence also validates CountItem results from this module.
    from benchmarks.agentic.count_evidence import is_count_coordinate

    if not is_count_coordinate(value):
        raise BenchmarkValidationError("invalid count coordinate")
    assert_no_key_shaped(value, where="count coordinate")
    return value


def _json_bytes(value: object) -> bytes:
    return scanned_json_bytes(value, where="count evidence", sort_keys=True, indent=2) + b"\n"


def _read_json(path: Path) -> dict[str, Any]:
    body = path.read_bytes()
    assert_no_key_shaped(body, where="count input")

    def checked_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        # A duplicate field must not shadow a decoded, escaped credential.
        assert_no_key_shaped_json(pairs, where="count input")
        if len(dict(pairs)) != len(pairs):
            raise BenchmarkValidationError("duplicate count input fields")
        return dict(pairs)

    record = json.loads(body, object_pairs_hook=checked_pairs)
    assert_no_key_shaped_json(record, where="count input")
    if not isinstance(record, dict):
        raise BenchmarkValidationError("count evidence must be an object")
    return record


def _publish(path: Path, body: bytes, *, replace: bool = False) -> None:
    """Publish complete evidence, exclusively unless updating the token projection."""
    from tempfile import TemporaryDirectory

    path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path.parent) as directory:
        stage = Path(directory) / "record"
        stage.write_bytes(body)
        if replace:
            os.replace(stage, path)
        else:
            os.link(stage, path)


def _freeze(path: Path, record: object) -> None:
    body = _json_bytes(record)
    if path.exists():
        if path.read_bytes() != body:
            raise BenchmarkValidationError("immutable count inputs differ")
    else:
        _publish(path, body)


def _count_inputs(run_dir: Path) -> tuple[dict[str, Any], dict[str, CountItem]]:
    prompt = (run_dir / "snapshots/system-prompt.txt").read_text(encoding="utf-8")
    assert_no_key_shaped(prompt, where="count prompt")
    items: dict[str, CountItem] = {}
    frozen: dict[str, Any] = {}
    for path in sorted((run_dir / "scoring").glob("*/*.json")):
        cell, qid = _coordinate(path.parent.name), _coordinate(path.stem)
        score = _read_json(path)
        transcript_path = run_dir / "transcripts" / cell / f"{qid}.json"
        transcript = _read_json(transcript_path)
        for record in (score, transcript):
            if (record.get("competitor_id"), record.get("corpus_id")) != (cell, qid):
                raise BenchmarkValidationError("count coordinates disagree with answering evidence")
            if record.get("tool_model_key") != transcript.get("tool_model_key"):
                raise BenchmarkValidationError("count tool-model coordinate mismatch")
        system = _coordinate(transcript["system_id"])
        model = _coordinate(transcript["model_family_id"])
        repeat = transcript["repeat"]
        if type(repeat) is not int or repeat < 1 or cell != f"{system}--{model}--r{repeat}":
            raise BenchmarkValidationError("invalid answering cell coordinate")
        if transcript.get("status") not in ("succeeded", "failed"):
            raise BenchmarkValidationError("only scored trajectories may be counted")
        system_path = run_dir / "systems" / f"{model}--{system}.json"
        info = _read_json(system_path)
        item = count_item_from_transcript(
            transcript, system_prompt=prompt, tools_raw=info["tools_raw"]
        )
        for position in item.calls:
            if (
                type(position) is not int
                or not 0 < position < len(item.messages)
                or item.messages[position]["role"] != "assistant"
                or item.messages[position - 1]["role"] != "user"
            ):
                raise BenchmarkValidationError("invalid count call position")
        if item.calls != sorted(set(item.calls)):
            raise BenchmarkValidationError("duplicate or unordered count calls")
        key = hashlib.sha256(f"{cell}/{qid}".encode()).hexdigest()
        items[key] = item
        frozen[key] = {
            "item": asdict(item),
            "transcript_sha256": hashlib.sha256(transcript_path.read_bytes()).hexdigest(),
            "system_sha256": hashlib.sha256(system_path.read_bytes()).hexdigest(),
        }
    if not items:
        raise BenchmarkValidationError("counting requires scored answering cells")
    return frozen, items


def planned_count_calls(item: CountItem) -> int:
    from benchmarks.agentic.count_evidence import planned_count_calls as planned

    return planned(item)


def _assigned_method(family: ModelFamily) -> _CountMethod:
    if family.provider != "anthropic" or family.api not in ("scripted", "anthropic-messages"):
        raise BenchmarkValidationError("counting requires the Anthropic count endpoint or a script")
    return _SCRIPTED_METHOD if family.api == "scripted" else _anthropic_method(family.model_id)


def _validate_record(record: Any, item: CountItem, family: ModelFamily) -> None:
    _validate_count_fields(record, item, _assigned_method(family).record_fields())


def _validate_count_fields(record: Any, item: CountItem, method_fields: dict[str, Any]) -> None:
    from benchmarks.agentic.count_evidence import validate_count_fields

    validate_count_fields(record, item, method_fields)


def _previous(
    run_dir: Path, root: Path, items: Mapping[str, CountItem]
) -> dict[str, dict[str, Any]]:
    # Receipts bind every archived result, including unsuccessful reruns, to its bytes.
    for path in (root / "count-runs").glob("*.json"):
        summary = _read_json(path)
        for relative, digest in summary["results"].items():
            archived = root / "counts" / _identifier(path.stem) / relative
            # Receipt paths are always two safe coordinates, never worker supplied paths.
            parts = relative.split("/")
            if len(parts) != 2 or not parts[1].endswith(".json"):
                raise BenchmarkValidationError("invalid count receipt coordinate")
            _coordinate(parts[0])
            _coordinate(parts[1][:-5])
            if hashlib.sha256(archived.read_bytes()).hexdigest() != digest:
                raise BenchmarkValidationError("immutable count result differs")
    previous = {}
    for key, item in items.items():
        path = run_dir / "tokens" / item.cell / f"{item.corpus_id}.json"
        if not path.exists():
            continue
        row = _read_json(path)
        if row.get("status") == "placeholder":
            if any(
                row.get(k) != v
                for k, v in {
                    "competitor_id": item.cell,
                    "corpus_id": item.corpus_id,
                    "tool_model_key": item.tool_model_key,
                }.items()
            ):
                raise BenchmarkValidationError("placeholder count coordinates differ")
            continue
        archived = (
            root
            / "counts"
            / _identifier(row.get("count_run_id"))
            / item.cell
            / f"{item.corpus_id}.json"
        )
        if archived.read_bytes() != path.read_bytes():
            raise BenchmarkValidationError("token projection differs from immutable count result")
        # Historical projections belong to their original invocation. Pre-provenance
        # rows can be read and recounted, but never satisfy a new assignment.
        model = row.get("count_model")
        if not isinstance(model, str) or not model:
            raise BenchmarkValidationError("missing archived count model")
        if row.get("execution") == _SCRIPTED_METHOD.record_fields()["execution"]:
            fields = _SCRIPTED_METHOD.record_fields()
        else:
            fields = _anthropic_method(model).record_fields()
            if "execution" not in row:
                del fields["execution"]  # validate the original format without upgrading the row
        _validate_count_fields(row, item, fields)
        previous[key] = row
    return previous


def run_counting(
    run_dir: Path,
    *,
    live: bool,
    family: ModelFamily | None = None,
    launcher: Launcher | None = None,
    only_missing: bool = True,
    confirm: Callable[[str], bool] = live_safety.interactive_confirm,
    dry_run: bool = False,
    count_run_id: str | None = None,
) -> dict[str, Any]:
    """Count scored cells through one isolated worker, with immutable input/result history.

    tokens/ is the current projection. A failed recount never replaces a successful count;
    every attempt remains under grading/counts/<invocation>/. No ledger is opened.
    """
    from benchmarks.agentic.cleanup import until_signalled as _until_signalled
    from benchmarks.agentic.host import (
        GRADING_CAPS,
        GRADING_SYSTEM,
        ItemSource,
        LocalLauncher,
        WorkerJob,
        refuse_host_keys,
        run_jobs,
    )
    from benchmarks.agentic.ledger import PlannedItem

    refuse_host_keys(os.environ)
    run_dir = Path(run_dir)
    family = family or count_family()
    execution = _assigned_method(family).record_fields()["execution"]
    ident = _identifier(count_run_id if count_run_id is not None else uuid.uuid4().hex)
    root = run_dir / "grading"
    try:
        if not run_dir.is_dir() or run_dir.is_symlink():
            raise BenchmarkValidationError("counting requires an answering run directory")
        root.mkdir(exist_ok=True)
        fd = os.open(root, os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for directory in (
                root,
                run_dir / "tokens",
                run_dir / "scoring",
                run_dir / "systems",
                run_dir / "transcripts",
                run_dir / "snapshots",
            ):
                for path in [directory, *directory.rglob("*")]:
                    if path.is_symlink() or (
                        path.exists() and not (path.is_dir() or path.is_file())
                    ):
                        raise BenchmarkValidationError(
                            "count evidence must be regular files and directories"
                        )
            frozen, items = _count_inputs(run_dir)
            _freeze(root / "count-inputs.json", frozen)
            previous = _previous(run_dir, root, items)
            pending = {
                k: i
                for k, i in items.items()
                if not (
                    only_missing
                    and k in previous
                    and previous[k]["status"] == "counted"
                    and previous[k].get("execution") == execution
                )
            }
            summary: dict[str, Any] = {
                "count_run_id": ident,
                "items": len(pending),
                "count_calls": sum(planned_count_calls(i) for i in pending.values()),
                "dry_run": dry_run or (family.api != "scripted" and not live),
                "units": [],
                "records_written": 0,
                "not_run": [],
                "worker_errors": [],
                "results": {},
            }
            claim = root / "count-invocations" / ident
            claim.parent.mkdir(exist_ok=True)
            claim.mkdir()  # never reuse an invocation, even if it died before its first result
            if summary["dry_run"]:
                _freeze(claim / "plan.json", summary)
                return summary
            if family.api != "scripted":
                if launcher is None or not launcher.supports(family):
                    raise BenchmarkValidationError("live counting requires the container launcher")
                live_safety.authorize_live(
                    f"{summary['count_calls']} planned count calls (before retries); free "
                    "Anthropic count-tokens endpoint; no ledger unit.",
                    confirm=confirm,
                )
            source = ItemSource(
                [
                    (PlannedItem(k, 0, 0, 0), {"kind": "trajectory", "item": asdict(i)})
                    for k, i in pending.items()
                ]
            )
            job = WorkerJob(
                "token-counter--grading",
                family,
                GRADING_SYSTEM,
                (),
                GRADING_CAPS,
                task="items",
                source=source,
            )

            async def collect(job: WorkerJob, message: dict[str, Any]) -> None:
                kind = message["type"]
                if kind in ("item_not_run", "worker_error"):
                    summary["not_run" if kind == "item_not_run" else "worker_errors"].append(
                        message
                    )
                if kind != "counted":
                    return
                key = message["item_id"]
                item = pending[key]
                record = message["record"]
                _validate_record(record, item, family)
                record = {
                    **record,
                    "count_run_id": ident,
                    "input_sha256": hashlib.sha256(_json_bytes(frozen[key])).hexdigest(),
                }
                body = _json_bytes(record)
                relative = f"{item.cell}/{item.corpus_id}.json"
                _publish(root / "counts" / ident / relative, body)
                summary["results"][relative] = hashlib.sha256(body).hexdigest()
                summary["records_written"] += 1
                summary["count_calls"] += record["count_calls"]
                if (
                    record["status"] == "counted"
                    or previous.get(key, {}).get("status") != "counted"
                ):
                    _publish(run_dir / "tokens" / relative, body, replace=True)

            summary.update(status="aborted", started_at=_utc_now(), count_calls=0)
            try:
                # Refuse edits made while the plan/authorization was being prepared.
                if _count_inputs(run_dir)[0] != frozen:
                    raise BenchmarkValidationError("immutable count inputs differ")
                if pending:
                    asyncio.run(
                        _until_signalled(
                            run_jobs(
                                [job],
                                launcher=launcher or LocalLauncher(),
                                ledger=None,
                                on_message=collect,
                                log_dir=root / "count-logs" / ident,
                                cwd=Path(__file__).resolve().parents[2],
                            )
                        )
                    )
                summary["status"] = (
                    "completed"
                    if not summary["not_run"] and not summary["worker_errors"]
                    else "partial"
                )
            finally:
                summary["completed_at"] = _utc_now()
                _publish(root / "count-runs" / f"{ident}.json", _json_bytes(summary))
            return summary
        finally:
            os.close(fd)
    except BenchmarkValidationError:
        raise
    except (OSError, KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
        raise BenchmarkValidationError(
            f"invalid or unavailable count input ({type(exc).__name__})"
        ) from exc
