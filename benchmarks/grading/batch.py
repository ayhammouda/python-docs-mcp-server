"""Worker side: one provider's batch API per client (design §§9, 12; grading note §§A–D).

A batch client submits one provider batch, polls it, and reads its results back by
item id, never by position. ``make_batch_client`` is the only way production code
builds one: every live API passes the live guard and holds one paid key, with a
pinned base URL, no redirects and SDK retries off (the same SDK builders as the
answering clients).
"""

from __future__ import annotations

import io
import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, get_args

import anthropic
import httpx
import openai
from anthropic._decoders.jsonl import AsyncJSONLDecoder
from anthropic._utils import path_template
from anthropic.types.messages import MessageBatchIndividualResponse
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from benchmarks.adapters.guard import PAID_KEY_ENVS, PROVIDER_API_KEY_ENV, require_live_environment
from benchmarks.agentic.clients import (
    _LIVE_APIS,
    GEMINI_BASE_URL,
    _status_error,
    anthropic_sdk,
    gemini_sdk,
    openai_sdk,
)
from benchmarks.agentic.types import Buckets, ErrorKind, ProviderCallError
from benchmarks.agentic.usage import anthropic_buckets, gemini_buckets, openai_buckets
from benchmarks.model_matrix import ModelFamily
from benchmarks.runner import BenchmarkValidationError, _load_yaml_mapping

_ITEM_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")  # an Anthropic custom_id

ItemStatus = Literal["succeeded", "errored", "canceled", "expired", "missing"]

GEMINI_RUNNING = frozenset(
    {
        "JOB_STATE_QUEUED",
        "JOB_STATE_PENDING",
        "JOB_STATE_RUNNING",
        "JOB_STATE_CANCELLING",
        "JOB_STATE_PAUSED",
        "JOB_STATE_UPDATING",
    }
)
_OPENAI_TERMINAL = frozenset({"completed", "failed", "expired", "cancelled"})
_GEMINI_REFUSALS = frozenset({"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII"})


@dataclass(frozen=True)
class BatchRequest:
    item_id: str  # [A-Za-z0-9_-]{1,64}: an Anthropic custom_id
    system: str
    user: str
    schema: dict[str, Any]
    schema_name: str  # "grade", "key_audit", "probe", "probe_check"

    def __post_init__(self) -> None:
        if not _ITEM_ID.fullmatch(self.item_id):
            raise ValueError(f"batch item id {self.item_id!r} must match {_ITEM_ID.pattern}")


@dataclass(frozen=True)
class BatchItemResult:
    item_id: str
    status: ItemStatus
    text: str | None  # the structured-output text
    refusal: bool
    stop_reason: str | None
    raw: dict[str, Any] | None
    raw_usage: dict[str, Any] | None
    buckets: Buckets | None
    reported_model: str | None
    unbilled: bool
    retryable: bool = True  # False: not resubmitted in this invocation, never terminal


def _no_result(
    item_id: str,
    status: ItemStatus,
    raw: dict[str, Any] | None,
    *,
    unbilled: bool,
    retryable: bool = True,
) -> BatchItemResult:
    return BatchItemResult(
        item_id, status, None, False, None, raw, None, None, None, unbilled, retryable
    )


def missing(item_id: str) -> BatchItemResult:
    """No line for this id: no usage, so it books twice its bound."""
    return _no_result(item_id, "missing", None, unbilled=False)


def _in_order(
    item_ids: Sequence[str], found: Mapping[str, BatchItemResult]
) -> list[BatchItemResult]:
    return [found.get(item_id) or missing(item_id) for item_id in item_ids]


def _jsonl(text: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _provider_error(exc: Exception) -> ProviderCallError | None:
    if isinstance(exc, anthropic.APIStatusError | openai.APIStatusError):
        return _status_error(exc.status_code, exc.response.headers, str(exc))
    if isinstance(exc, anthropic.APITimeoutError | openai.APITimeoutError | httpx.TimeoutException):
        return ProviderCallError("timeout", str(exc))
    if isinstance(
        exc, anthropic.APIConnectionError | openai.APIConnectionError | httpx.TransportError
    ):
        return ProviderCallError("connection", str(exc))
    if isinstance(exc, genai_errors.APIError):  # 4xx, 5xx, and any other status (a redirect)
        headers = getattr(exc.response, "headers", None) or {}
        return _status_error(exc.code, headers, str(exc))
    return None


async def _call(awaitable: Any) -> Any:
    try:
        return await awaitable
    except Exception as exc:
        error = _provider_error(exc)
        if error is None:
            raise
        raise error from exc


class BatchClient(Protocol):
    provider: str
    model_id: str

    def request_params(self) -> dict[str, Any]: ...

    async def submit(self, requests: Sequence[BatchRequest]) -> str: ...  # provider batch id

    async def poll(self, batch_id: str) -> str | None: ...  # None while running

    async def results(self, batch_id: str, item_ids: Sequence[str]) -> list[BatchItemResult]: ...


def anthropic_result(raw: dict[str, Any]) -> BatchItemResult:
    """One results line. errored, canceled and expired are not billed (grading note §A)."""
    item_id = raw["custom_id"]
    result = raw.get("result") or {}
    kind = result.get("type")
    if kind != "succeeded":
        status: ItemStatus = kind if kind in ("errored", "canceled", "expired") else "errored"
        return _no_result(item_id, status, raw, unbilled=True)
    message = result.get("message") or {}
    usage = message.get("usage")
    text = "".join(
        b.get("text", "") for b in message.get("content") or [] if b.get("type") == "text"
    )
    stop = message.get("stop_reason")
    return BatchItemResult(
        item_id=item_id,
        status="succeeded",
        text=text,
        refusal=stop == "refusal",
        stop_reason=stop,
        raw=raw,
        raw_usage=usage,
        buckets=None if usage is None else anthropic_buckets(usage),
        reported_model=message.get("model"),
        unbilled=False,
    )


def openai_result(raw: dict[str, Any]) -> BatchItemResult:
    """One output or error line: batch_expired and 4xx lines are not billed (grading note §B)."""
    item_id = raw["custom_id"]
    error = raw.get("error") or {}
    response = raw.get("response") or {}
    status_code = response.get("status_code")
    body = response.get("body") or {}
    usage = body.get("usage")
    # A partial receipt is evidence, but cannot be priced as if the absent count were zero.
    buckets = (
        openai_buckets(usage)
        if usage is not None
        and all(usage.get(k) is not None for k in ("input_tokens", "output_tokens"))
        else None
    )
    state = body.get("status")
    reason = (body.get("incomplete_details") or {}).get("reason")
    expired = error.get("code") == "batch_expired"
    if (
        error
        or status_code != 200
        or state not in ("completed", "incomplete")
        or (state == "incomplete" and reason not in ("max_output_tokens", "content_filter"))
    ):
        # A response body can describe a failed item even inside a successful HTTP
        # response. Partial output is not a vote; retain any reported usage for pricing.
        status: ItemStatus = (
            "expired"
            if expired
            else ("canceled" if state in ("canceled", "cancelled") else "errored")
        )
        rejected = isinstance(status_code, int) and 400 <= status_code < 500
        return BatchItemResult(
            item_id=item_id,
            status=status,
            text=None,
            refusal=False,
            stop_reason=None,
            raw=raw,
            raw_usage=usage,
            buckets=buckets,
            reported_model=None,
            unbilled=usage is None and (rejected or (expired and not response)),
        )
    content = [
        part
        for item in body.get("output") or []
        if item.get("type") == "message"
        for part in item.get("content") or []
    ]
    return BatchItemResult(
        item_id=item_id,
        status="succeeded",
        text="".join(p.get("text", "") for p in content if p.get("type") == "output_text"),
        refusal=any(p.get("type") == "refusal" for p in content)
        or (state == "incomplete" and reason == "content_filter"),
        stop_reason=reason if state == "incomplete" else state,
        raw=raw,
        raw_usage=usage,
        buckets=buckets,
        reported_model=body.get("model"),
        unbilled=False,
    )


def openai_failed_result(item_id: str) -> BatchItemResult:
    """A failed batch failed input validation before any request ran: booked 0, not retried."""
    return _no_result(item_id, "errored", None, unbilled=True, retryable=False)


def _snake(usage: Mapping[str, Any]) -> dict[str, Any]:
    return {re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower(): value for key, value in usage.items()}


def gemini_result(raw: dict[str, Any]) -> BatchItemResult:
    """One keyed line. Gemini documents no billing rule for failures: none is unbilled."""
    item_id = raw["key"]
    response = raw.get("response")
    if not isinstance(response, dict):
        return _no_result(item_id, "errored", raw, unbilled=False)
    candidates = response.get("candidates") or [{}]
    candidate = candidates[0]
    parts = (candidate.get("content") or {}).get("parts") or []
    finish = candidate.get("finishReason")
    usage = response.get("usageMetadata")
    return BatchItemResult(
        item_id=item_id,
        status="succeeded",
        text="".join(p.get("text", "") for p in parts if not p.get("thought")),
        refusal=finish in _GEMINI_REFUSALS,
        stop_reason=finish,
        raw=raw,
        raw_usage=usage,
        buckets=None if usage is None else gemini_buckets(_snake(usage)),
        reported_model=response.get("modelVersion"),
        unbilled=False,
    )


class AnthropicBatchClient:
    """Message Batches: structured output, no thinking, sampling or cache_control."""

    provider = "anthropic"

    def __init__(
        self,
        sdk: anthropic.AsyncAnthropic,
        model_id: str,
        max_output_tokens: int,
        effort: str | None,
    ) -> None:
        self._sdk = sdk
        self.model_id = model_id
        self._max_output_tokens = max_output_tokens
        self._effort = effort

    def request_params(self) -> dict[str, Any]:
        return {
            "model": self.model_id,
            "max_tokens": self._max_output_tokens,
            "effort": self._effort,
        }

    def _params(self, request: BatchRequest) -> dict[str, Any]:
        output: dict[str, Any] = {"format": {"type": "json_schema", "schema": request.schema}}
        if self._effort is not None:
            output["effort"] = self._effort
        return {
            "model": self.model_id,
            "max_tokens": self._max_output_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": request.user}],
            "output_config": output,
        }

    async def submit(self, requests: Sequence[BatchRequest]) -> str:
        batch = await _call(
            self._sdk.messages.batches.create(
                requests=[
                    {"custom_id": r.item_id, "params": self._params(r)}  # type: ignore[misc]
                    for r in requests
                ]
            )
        )
        return batch.id

    async def poll(self, batch_id: str) -> str | None:
        batch = await _call(self._sdk.messages.batches.retrieve(batch_id))
        return batch.processing_status if batch.processing_status == "ended" else None

    async def results(self, batch_id: str, item_ids: Sequence[str]) -> list[BatchItemResult]:
        if not batch_id:
            raise ValueError("Expected a non-empty batch id")
        # The SDK's batches.results() follows an absolute results_url from metadata.
        path = path_template("/v1/messages/batches/{id}/results", id=batch_id)

        async def read() -> dict[str, BatchItemResult]:
            found: dict[str, BatchItemResult] = {}
            stream = await self._sdk.get(
                path,
                cast_to=AsyncJSONLDecoder[MessageBatchIndividualResponse],
                options={"headers": {"Accept": "application/binary"}, "follow_redirects": False},
                stream=True,
            )
            try:
                async for line in stream:
                    result = anthropic_result(line.model_dump(mode="json"))
                    found[result.item_id] = result
            finally:
                await stream.close()
            return found

        return _in_order(item_ids, await _call(read()))


class OpenAIBatchClient:
    """Batch API over /v1/responses: strict json_schema, store false, no prompt-cache option."""

    provider = "openai"

    def __init__(
        self, sdk: openai.AsyncOpenAI, model_id: str, max_output_tokens: int, effort: str | None
    ) -> None:
        self._sdk = sdk
        self.model_id = model_id
        self._max_output_tokens = max_output_tokens
        self._effort = effort

    def request_params(self) -> dict[str, Any]:
        return {
            "model": self.model_id,
            "max_output_tokens": self._max_output_tokens,
            "effort": self._effort,
        }

    def _body(self, request: BatchRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model_id,
            "instructions": request.system,
            "input": [{"role": "user", "content": request.user}],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": request.schema_name,
                    "schema": request.schema,
                    "strict": True,
                }
            },
            "max_output_tokens": self._max_output_tokens,
            "store": False,
        }
        if self._effort is not None:
            body["reasoning"] = {"effort": self._effort}
        return body

    async def submit(self, requests: Sequence[BatchRequest]) -> str:
        lines = [
            {
                "custom_id": r.item_id,
                "method": "POST",
                "url": "/v1/responses",
                "body": self._body(r),
            }
            for r in requests
        ]
        data = "".join(json.dumps(line) + "\n" for line in lines).encode()
        upload = await _call(
            self._sdk.files.create(file=("batch.jsonl", data, "application/jsonl"), purpose="batch")
        )
        batch = await _call(
            self._sdk.batches.create(
                input_file_id=upload.id, endpoint="/v1/responses", completion_window="24h"
            )
        )
        return batch.id

    async def poll(self, batch_id: str) -> str | None:
        batch = await _call(self._sdk.batches.retrieve(batch_id))
        return batch.status if batch.status in _OPENAI_TERMINAL else None

    async def results(self, batch_id: str, item_ids: Sequence[str]) -> list[BatchItemResult]:
        batch = await _call(self._sdk.batches.retrieve(batch_id))
        if batch.status == "failed":
            return [openai_failed_result(item_id) for item_id in item_ids]
        found: dict[str, BatchItemResult] = {}
        for file_id in (batch.output_file_id, batch.error_file_id):
            if file_id:
                content = await _call(self._sdk.files.content(file_id))
                for line in _jsonl(content.text):
                    result = openai_result(line)
                    found[result.item_id] = result
        return _in_order(item_ids, found)


class GeminiBatchClient:
    """Batch mode from one keyed JSONL file; response_json_schema structured output."""

    provider = "google"

    def __init__(
        self, sdk: genai.Client, model_id: str, max_output_tokens: int, effort: str | None
    ) -> None:
        self._sdk = sdk
        self.model_id = model_id
        self._max_output_tokens = max_output_tokens
        self._effort = effort

    def request_params(self) -> dict[str, Any]:
        return {
            "model": self.model_id,
            "max_output_tokens": self._max_output_tokens,
            "effort": self._effort,
        }

    def _request(self, request: BatchRequest) -> dict[str, Any]:
        config: dict[str, Any] = {
            "response_mime_type": "application/json",
            "response_json_schema": request.schema,
            "max_output_tokens": self._max_output_tokens,
        }
        if self._effort is not None:
            config["thinking_config"] = {"thinking_level": self._effort}
        return {
            "contents": [{"role": "user", "parts": [{"text": request.user}]}],
            "system_instruction": {"parts": [{"text": request.system}]},
            "generation_config": config,
        }

    async def _upload_jsonl(self, data: bytes) -> str:
        # The SDK only rebases the resumable chunk URL when the *upload's* options
        # specify the origin. The client-wide base URL alone pins only the start call.
        config = genai_types.UploadFileConfig(
            mime_type="jsonl", http_options=genai_types.HttpOptions(base_url=GEMINI_BASE_URL)
        )
        try:
            upload = await self._sdk.aio.files.upload(file=io.BytesIO(data), config=config)
        except genai_errors.APIError as exc:
            if 300 <= exc.code < 400:
                raise ProviderCallError(
                    "rejected", "Gemini upload redirect", status=exc.code
                ) from None
            raise
        except (KeyError, ValueError, httpx.InvalidURL):
            raise ProviderCallError("rejected", "Invalid Gemini upload response") from None
        if not upload.name:
            raise ProviderCallError("rejected", "Gemini upload returned no file name")
        return upload.name

    async def submit(self, requests: Sequence[BatchRequest]) -> str:
        data = "".join(
            json.dumps({"key": r.item_id, "request": self._request(r)}) + "\n" for r in requests
        ).encode()
        file_name = await _call(self._upload_jsonl(data))
        job = await _call(self._sdk.aio.batches.create(model=self.model_id, src=file_name))
        return job.name

    async def _state(self, batch_id: str) -> tuple[str, Any]:
        job = await _call(self._sdk.aio.batches.get(name=batch_id))
        return str(getattr(job.state, "value", job.state)), job

    async def poll(self, batch_id: str) -> str | None:
        state, _job = await self._state(batch_id)
        return None if state in GEMINI_RUNNING else state

    async def results(self, batch_id: str, item_ids: Sequence[str]) -> list[BatchItemResult]:
        _state, job = await self._state(batch_id)
        file_name = job.dest.file_name if job.dest is not None else None
        if not file_name:  # FAILED, CANCELLED or EXPIRED with no results: every item missing
            return [missing(item_id) for item_id in item_ids]
        # The SDK also accepts URIs and interpolates names into the download path.
        if not re.fullmatch(r"files/[A-Za-z0-9_-]+", file_name):
            raise ProviderCallError("rejected", "Expected a Gemini output file resource name")
        data = await _call(self._sdk.aio.files.download(file=file_name))
        found = {r.item_id: r for r in map(gemini_result, _jsonl(data.decode("utf-8")))}
        return _in_order(item_ids, found)


_SCRIPT_BUCKETS = {
    "anthropic": anthropic_buckets,
    "openai": openai_buckets,
    "google": gemini_buckets,
}
_SCRIPT_STOP = {"anthropic": "end_turn", "openai": "completed", "google": "STOP"}
_SCRIPT_DONE = {"anthropic": "ended", "openai": "completed", "google": "JOB_STATE_SUCCEEDED"}


class ScriptedBatchClient:
    """Plays a YAML script: ``rules`` per item id, ``polls`` in order, ``submit_errors``.

    A rule has ``match`` (an item-id regex), ``output`` (a mapping sent as JSON text) or
    ``text``, ``status``, ``refusal``, ``stop_reason``, ``unbilled`` and ``usage`` in the
    provider's usage shape; the first match wins and the last rule matches ``.*``.
    ``polls`` entries are returned in order (null while running), then the provider's
    terminal state. ``submit_errors`` entries (``{kind, status}``, or null for a submit
    that succeeds) are consumed by successive submits.
    """

    def __init__(
        self,
        provider: str,
        model_id: str,
        script: Mapping[str, Any],
        *,
        batch_status: str | None = None,
    ) -> None:
        rules = script.get("rules")
        if not isinstance(rules, list) or not rules or rules[-1].get("match") != ".*":
            raise BenchmarkValidationError("a batch script's last rule must match '.*'")
        self.provider = provider
        self.model_id = model_id
        self._rules = [(re.compile(str(rule["match"])), rule) for rule in rules]
        self._polls = list(script.get("polls") or [])
        self._submit_errors = list(script.get("submit_errors") or [])
        self._batch_status = batch_status
        self._batches: dict[str, list[str]] = {}
        self._pending_polls: dict[str, list[Any]] = {}
        self.requests: list[BatchRequest] = []
        self.submits = 0

    def request_params(self) -> dict[str, Any]:
        return {"model": self.model_id, "scripted": True}

    async def submit(self, requests: Sequence[BatchRequest]) -> str:
        self.submits += 1
        if self._submit_errors and (error := self._submit_errors.pop(0)) is not None:
            kind = error.get("kind")
            if kind not in get_args(ErrorKind):
                raise BenchmarkValidationError(f"unknown scripted submit error kind {kind!r}")
            raise ProviderCallError(kind, "scripted submit error", status=error.get("status"))
        self.requests.extend(requests)
        batch_id = f"scripted-batch-{self.submits}"
        self._batches[batch_id] = [r.item_id for r in requests]
        self._pending_polls[batch_id] = list(self._polls)
        return batch_id

    async def poll(self, batch_id: str) -> str | None:
        pending = self._pending_polls[batch_id]
        if pending:
            return pending.pop(0)
        return self._batch_status or _SCRIPT_DONE[self.provider]

    def _result(self, item_id: str) -> BatchItemResult:
        rule = next(rule for pattern, rule in self._rules if pattern.search(item_id))
        status = rule.get("status", "succeeded")
        if status == "missing":
            return missing(item_id)
        if status != "succeeded":
            unbilled = rule.get("unbilled", self.provider == "anthropic")
            return _no_result(item_id, status, {"scripted_rule": rule}, unbilled=unbilled)
        usage = rule.get("usage")
        text = json.dumps(rule["output"]) if "output" in rule else str(rule.get("text", ""))
        return BatchItemResult(
            item_id=item_id,
            status="succeeded",
            text=text,
            refusal=bool(rule.get("refusal", False)),
            stop_reason=rule.get("stop_reason") or _SCRIPT_STOP[self.provider],
            raw={"scripted_rule": rule},
            raw_usage=usage,
            buckets=None if usage is None else _SCRIPT_BUCKETS[self.provider](usage),
            reported_model=self.model_id,
            unbilled=False,
        )

    async def results(self, batch_id: str, item_ids: Sequence[str]) -> list[BatchItemResult]:
        if self._batch_status == "failed":
            return [openai_failed_result(item_id) for item_id in item_ids]
        submitted = set(self._batches[batch_id])
        return [self._result(i) if i in submitted else missing(i) for i in item_ids]


def live_key(family: ModelFamily) -> tuple[str, str]:
    """``(provider, key)`` for a live family: the guard passed, and no second paid key set."""
    provider = _LIVE_APIS.get(family.api or "")
    if provider is None:
        raise BenchmarkValidationError(
            f"model family {family.id!r} uses api {family.api!r}, which has no batch client"
        )
    if family.provider != provider:
        raise BenchmarkValidationError(
            f"model family {family.id!r}: api {family.api!r} carries the {provider!r} key, "
            f"not provider {family.provider!r}"
        )
    require_live_environment(provider)
    own_key_env = PROVIDER_API_KEY_ENV[provider]
    others = [name for name in PAID_KEY_ENVS if name != own_key_env and os.environ.get(name)]
    if others:
        raise BenchmarkValidationError(
            f"a worker holds at most one paid key: {', '.join(others)} is set beside {own_key_env}"
        )
    return provider, os.environ[own_key_env]


def scripted_source(family: ModelFamily, label: str) -> dict[str, Any]:
    script = Path(str(family.raw.get("script", "")))
    if not script.is_absolute():
        raise BenchmarkValidationError(
            f"scripted model family {family.id!r} needs an absolute 'script' path, "
            f"got {str(script)!r}"
        )
    return _load_yaml_mapping(script, label)


def make_batch_client(
    family: ModelFamily, *, effort: str | None, timeout_s: float, transport: Any | None = None
) -> BatchClient:
    """The only way production code builds a batch client; every live API passes the guard."""
    if family.api == "scripted":
        script = scripted_source(family, "batch script")
        return ScriptedBatchClient(
            family.provider, family.model_id, script, batch_status=script.get("batch_status")
        )
    provider, key = live_key(family)
    if family.max_output_tokens is None:
        raise BenchmarkValidationError(f"model family {family.id!r} must set max_output_tokens")
    # shortcut: no egress proxy yet; once PR 4 lands, pass proxy=egress_proxy(os.environ) to
    # each SDK builder below, as make_client does (PR 5 part 2, Task 3b).
    if provider == "anthropic":
        sdk = anthropic_sdk(key, timeout_s, transport=transport)
        return AnthropicBatchClient(sdk, family.model_id, family.max_output_tokens, effort)
    if provider == "openai":
        sdk = openai_sdk(key, timeout_s, transport=transport)
        return OpenAIBatchClient(sdk, family.model_id, family.max_output_tokens, effort)
    sdk = gemini_sdk(key, timeout_s, transport=transport)
    return GeminiBatchClient(sdk, family.model_id, family.max_output_tokens, effort)
