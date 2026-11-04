"""Batch clients for the three providers and the scripted one (PR 5 Task 3).

Grading note §§A–D is the source of every request shape and billing rule.
Every HTTP exchange goes through a mock transport; no test reaches a network host.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import httpx2
import pytest
import yaml

from benchmarks.adapters.guard import PAID_KEY_ENVS, LiveProviderDisabledError
from benchmarks.agentic.clients import anthropic_sdk, gemini_sdk, openai_sdk
from benchmarks.agentic.types import Buckets, ProviderCallError
from benchmarks.grading.batch import (
    AnthropicBatchClient,
    BatchItemResult,
    BatchRequest,
    GeminiBatchClient,
    OpenAIBatchClient,
    ScriptedBatchClient,
    anthropic_result,
    gemini_result,
    make_batch_client,
    openai_failed_result,
    openai_result,
)
from benchmarks.model_matrix import model_family_from_raw
from benchmarks.runner import BenchmarkValidationError

SCHEMA = {
    "type": "object",
    "properties": {"explanation": {"type": "string"}, "attempted": {"type": "boolean"}},
    "required": ["explanation", "attempted"],
    "additionalProperties": False,
}
VOTE_TEXT = '{"explanation": "ok", "attempted": true}'


def _req(item_id: str = "i1") -> BatchRequest:
    return BatchRequest(item_id, "You grade.", f"<answer>\n{item_id}\n</answer>", SCHEMA, "grade")


class Router:
    """A mock transport handler: routes by method and path regex, records every request."""

    def __init__(self, routes: list[tuple[str, str, Callable[[Any], Any]]]) -> None:
        self.routes = routes
        self.seen: list[Any] = []

    def __call__(self, request: Any) -> Any:
        self.seen.append(request)
        body = request.read()
        assert isinstance(body, bytes)
        for method, pattern, respond in self.routes:
            if request.method == method and re.search(pattern, str(request.url)):
                return respond(request)
        raise AssertionError(f"unexpected request {request.method} {request.url}")


def _json(kind: Any, body: Any, status: int = 200, headers: dict[str, str] | None = None) -> Any:
    return lambda _request: kind.Response(status, json=body, headers=headers or {})


def _text(kind: Any, text: str) -> Any:
    return lambda _request: kind.Response(200, content=text.encode())


ANTHROPIC_BATCH = {
    "id": "msgbatch_1",
    "type": "message_batch",
    "processing_status": "in_progress",
    "request_counts": {"processing": 1, "succeeded": 0, "errored": 0, "canceled": 0, "expired": 0},
    "created_at": "2026-11-04T00:00:00Z",
    "expires_at": "2026-11-05T00:00:00Z",
    "results_url": None,
}


def _anthropic_line(custom_id: str, result: dict[str, Any]) -> dict[str, Any]:
    return {"custom_id": custom_id, "result": result}


def _anthropic_ok(custom_id: str, text: str = VOTE_TEXT, stop: str = "end_turn") -> dict[str, Any]:
    return _anthropic_line(
        custom_id,
        {
            "type": "succeeded",
            "message": {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-haiku-5-5",
                "content": [{"type": "text", "text": text}],
                "stop_reason": stop,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 1200,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 300,
                },
            },
        },
    )


ANTHROPIC_ERRORED = _anthropic_line(
    "i1",
    {"type": "errored", "error": {"type": "error", "error": {"type": "api_error", "message": "x"}}},
)
ANTHROPIC_EXPIRED = _anthropic_line("i1", {"type": "expired"})


def _anthropic_client(router: Router, effort: str | None = "low") -> AnthropicBatchClient:
    sdk = anthropic_sdk("test-key", 5.0, transport=httpx2.MockTransport(router))
    return AnthropicBatchClient(sdk, "claude-haiku-5-5", 16384, effort)


def _anthropic(respond: dict[str, Any]) -> tuple[list[Any], AnthropicBatchClient]:
    router = Router([("POST", r"/v1/messages/batches$", _json(httpx2, respond))])
    return router.seen, _anthropic_client(router)


def _anthropic_results(lines: list[dict[str, Any]]) -> AnthropicBatchClient:
    url = "https://api.anthropic.com/v1/messages/batches/msgbatch_1/results"
    ended = {**ANTHROPIC_BATCH, "processing_status": "ended", "results_url": url}
    jsonl = "".join(json.dumps(line) + "\n" for line in lines)
    router = Router(
        [
            ("GET", r"/results$", _text(httpx2, jsonl)),
            ("GET", r"/v1/messages/batches/msgbatch_1$", _json(httpx2, ended)),
        ]
    )
    return _anthropic_client(router)


def _openai_body(custom_id: str, **overrides: Any) -> dict[str, Any]:
    body = {
        "id": "resp_1",
        "object": "response",
        "status": "completed",
        "incomplete_details": None,
        "model": "gpt-6-luna",
        "output": [
            {
                "type": "message",
                "id": "msg_1",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": VOTE_TEXT, "annotations": []}],
            }
        ],
        "usage": {
            "input_tokens": 1000,
            "input_tokens_details": {"cached_tokens": 200, "cache_write_tokens": 0},
            "output_tokens": 50,
            "output_tokens_details": {"reasoning_tokens": 40},
        },
    }
    body.update(overrides)
    return {
        "id": f"batch_req_{custom_id}",
        "custom_id": custom_id,
        "response": {"status_code": 200, "request_id": "r", "body": body},
        "error": None,
    }


def _openai_error_line(custom_id: str, status: int) -> dict[str, Any]:
    return {
        "id": "batch_req_x",
        "custom_id": custom_id,
        "response": {"status_code": status, "request_id": "r", "body": {"error": {"message": "x"}}},
        "error": None,
    }


OPENAI_BATCH_EXPIRED = {
    "id": "batch_req_x",
    "custom_id": "i1",
    "response": None,
    "error": {"code": "batch_expired", "message": "This request could not be executed."},
}
OPENAI_400_LINE = _openai_error_line("i1", 400)
OPENAI_500_LINE = _openai_error_line("i1", 500)
OPENAI_FAILED_BATCH = "failed-batch"


def _openai_client(router: Router, effort: str | None = "none") -> OpenAIBatchClient:
    sdk = openai_sdk("test-key", 5.0, transport=httpx2.MockTransport(router))
    return OpenAIBatchClient(sdk, "gpt-6-luna", 16384, effort)


def _openai_batch(status: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": "batch_1",
        "object": "batch",
        "endpoint": "/v1/responses",
        "input_file_id": "file-in",
        "completion_window": "24h",
        "status": status,
        "created_at": 0,
        **extra,
    }


def _openai_results(lines: list[dict[str, Any]], status: str = "completed") -> OpenAIBatchClient:
    jsonl = "".join(json.dumps(line) + "\n" for line in lines)
    batch = _openai_batch(status, output_file_id="file-out", error_file_id=None)
    router = Router(
        [
            ("GET", r"/v1/files/file-out/content$", _text(httpx2, jsonl)),
            ("GET", r"/v1/batches/batch_1$", _json(httpx2, batch)),
        ]
    )
    return _openai_client(router)


def _gemini_response(custom_id: str, finish: str = "STOP", text: str = VOTE_TEXT) -> dict:
    return {
        "key": custom_id,
        "response": {
            "candidates": [
                {"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": finish}
            ],
            "usageMetadata": {
                "promptTokenCount": 900,
                "candidatesTokenCount": 120,
                "thoughtsTokenCount": 30,
                "cachedContentTokenCount": 100,
            },
            "modelVersion": "gemini-3.1-flash-lite",
        },
    }


GEMINI_ERROR_LINE = {"key": "i1", "error": {"code": 500, "message": "internal"}}


def _gemini_client(router: Router, effort: str | None = "minimal") -> GeminiBatchClient:
    sdk = gemini_sdk("test-key", 5.0, transport=httpx.MockTransport(router))
    return GeminiBatchClient(sdk, "gemini-3.1-flash-lite", 16384, effort)


def _gemini_job(state: str, responses_file: str | None = None) -> dict[str, Any]:
    metadata: dict[str, Any] = {"state": state, "model": "models/gemini-3.1-flash-lite"}
    if responses_file is not None:
        metadata["output"] = {"responsesFile": responses_file}
    return {"name": "batches/b1", "metadata": metadata}


def _gemini_results(lines: list[dict[str, Any]], state: str = "JOB_STATE_SUCCEEDED") -> Any:
    jsonl = "".join(json.dumps(line) + "\n" for line in lines)
    router = Router(
        [
            ("GET", r"files/out:download", _text(httpx, jsonl)),
            ("GET", r"/v1beta/batches/b1", _json(httpx, _gemini_job(state, "files/out"))),
        ]
    )
    return _gemini_client(router)


async def test_anthropic_batch_request_shape() -> None:
    seen, client = _anthropic(respond={"id": "msgbatch_1", "processing_status": "in_progress"})
    assert await client.submit([_req("i1")]) == "msgbatch_1" and len(seen) == 1
    body = json.loads(seen[0].content)
    params = body["requests"][0]["params"]
    assert body["requests"][0]["custom_id"] == "i1"
    assert params["output_config"] == {
        "format": {"type": "json_schema", "schema": SCHEMA},
        "effort": "low",
    }
    assert (
        params["max_tokens"] == 16384 and "thinking" not in params and "temperature" not in params
    )
    assert params["system"] == "You grade." and params["model"] == "claude-haiku-5-5"
    assert params["messages"] == [{"role": "user", "content": "<answer>\ni1\n</answer>"}]
    assert "cache_control" not in seen[0].content.decode()


async def test_anthropic_without_effort_omits_it() -> None:
    router = Router([("POST", r"/v1/messages/batches$", _json(httpx2, ANTHROPIC_BATCH))])
    await _anthropic_client(router, effort=None).submit([_req()])
    params = json.loads(router.seen[0].content)["requests"][0]["params"]
    assert params["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}}


async def test_openai_batch_uploads_jsonl_then_creates_a_responses_batch() -> None:
    file_body = {
        "id": "file-in",
        "object": "file",
        "bytes": 1,
        "created_at": 0,
        "filename": "batch.jsonl",
        "purpose": "batch",
        "status": "processed",
    }
    router = Router(
        [
            ("POST", r"/v1/files$", _json(httpx2, file_body)),
            ("POST", r"/v1/batches$", _json(httpx2, _openai_batch("validating"))),
        ]
    )
    assert await _openai_client(router).submit([_req("i1")]) == "batch_1"
    upload, create = router.seen
    assert upload.url.path == "/v1/files" and b'name="purpose"' in upload.content
    assert b"batch" in upload.content
    line = json.loads(re.search(rb'\{"custom_id".*\}', upload.content).group(0))  # type: ignore[union-attr]
    assert (line["custom_id"], line["method"], line["url"]) == ("i1", "POST", "/v1/responses")
    body = line["body"]
    assert body["text"]["format"] == {
        "type": "json_schema",
        "name": "grade",
        "schema": SCHEMA,
        "strict": True,
    }
    assert body["reasoning"] == {"effort": "none"} and body["max_output_tokens"] == 16384
    assert body["store"] is False and body["instructions"] == "You grade."
    assert body["input"] == [{"role": "user", "content": "<answer>\ni1\n</answer>"}]
    assert "prompt_cache" not in json.dumps(body)
    created = json.loads(create.content)
    assert created == {
        "input_file_id": "file-in",
        "endpoint": "/v1/responses",
        "completion_window": "24h",
    }


async def test_gemini_batch_uploads_keyed_jsonl() -> None:
    upload_url = "https://generativelanguage.googleapis.com/upload/v1beta/files?upload_id=u1"
    router = Router(
        [
            (
                "POST",
                r"upload_id=u1",
                _json(
                    httpx, {"file": {"name": "files/in"}}, headers={"x-goog-upload-status": "final"}
                ),
            ),
            (
                "POST",
                r"/upload/v1beta/files",
                _json(httpx, {}, headers={"x-goog-upload-url": upload_url}),
            ),
            ("POST", r"batchGenerateContent", _json(httpx, _gemini_job("BATCH_STATE_PENDING"))),
        ]
    )
    assert await _gemini_client(router).submit([_req("i1")]) == "batches/b1"
    chunk = next(r for r in router.seen if "upload_id" in str(r.url))
    line = json.loads(chunk.content.decode().strip())
    assert line["key"] == "i1"
    request = line["request"]
    assert request["contents"] == [{"role": "user", "parts": [{"text": "<answer>\ni1\n</answer>"}]}]
    assert request["system_instruction"] == {"parts": [{"text": "You grade."}]}
    assert request["generation_config"] == {
        "response_mime_type": "application/json",
        "response_json_schema": SCHEMA,
        "max_output_tokens": 16384,
        "thinking_config": {"thinking_level": "minimal"},
    }
    create = json.loads(router.seen[-1].content)
    assert create["batch"]["inputConfig"] == {"fileName": "files/in"}


@pytest.mark.parametrize(
    "state,terminal",
    [
        ("JOB_STATE_RUNNING", False),
        ("JOB_STATE_PAUSED", False),
        ("JOB_STATE_UPDATING", False),
        ("JOB_STATE_PARTIALLY_SUCCEEDED", True),
        ("JOB_STATE_SUCCEEDED", True),
        ("JOB_STATE_EXPIRED", True),
    ],
)
async def test_gemini_terminal_states(state: str, terminal: bool) -> None:
    router = Router([("GET", r"/v1beta/batches/b1", _json(httpx, _gemini_job(state)))])
    assert await _gemini_client(router).poll("batches/b1") == (state if terminal else None)


def _results_client(provider: str) -> Any:
    if provider == "anthropic":
        return _anthropic_results([_anthropic_ok("i3"), _anthropic_ok("i1")])
    if provider == "openai":
        return _openai_results([_openai_body("i3"), _openai_body("i1")])
    return _gemini_results([_gemini_response("i3"), _gemini_response("i1")])


@pytest.mark.parametrize("provider", ["anthropic", "openai", "google"])
async def test_results_map_by_id_not_order(provider: str) -> None:
    client = _results_client(provider)
    batch_id = {"anthropic": "msgbatch_1", "openai": "batch_1", "google": "batches/b1"}[provider]
    results = await client.results(batch_id, ["i1", "i2", "i3"])
    assert [(r.item_id, r.status) for r in results] == [
        ("i1", "succeeded"),
        ("i2", "missing"),
        ("i3", "succeeded"),
    ]
    assert results[1].unbilled is False and results[1].retryable is True
    assert results[1].buckets is None
    assert results[0].text == VOTE_TEXT and results[0].raw is not None


def _parse(provider: str, case: Any) -> BatchItemResult:
    if case == OPENAI_FAILED_BATCH:
        return openai_failed_result("i1")
    return {"anthropic": anthropic_result, "openai": openai_result, "google": gemini_result}[
        provider
    ](case)


@pytest.mark.parametrize(
    "provider,case,unbilled,retryable",
    [
        ("anthropic", ANTHROPIC_ERRORED, True, True),
        ("anthropic", ANTHROPIC_EXPIRED, True, True),
        ("openai", OPENAI_BATCH_EXPIRED, True, True),
        ("openai", OPENAI_400_LINE, True, True),
        ("openai", OPENAI_500_LINE, False, True),
        ("openai", OPENAI_FAILED_BATCH, True, False),
        ("google", GEMINI_ERROR_LINE, False, True),
    ],
)
def test_unbilled_follows_each_providers_documented_rule(
    provider: str, case: Any, unbilled: bool, retryable: bool
) -> None:
    result = _parse(provider, case)
    assert result.status != "succeeded" and result.buckets is None and result.text is None
    assert (result.unbilled, result.retryable) == (unbilled, retryable)


@pytest.mark.parametrize(
    "provider,line,refusal,stop_reason",
    [
        ("anthropic", _anthropic_ok("i1", stop="refusal"), True, "refusal"),
        ("anthropic", _anthropic_ok("i1", stop="max_tokens"), False, "max_tokens"),
        (
            "openai",
            _openai_body(
                "i1",
                output=[
                    {
                        "type": "message",
                        "id": "m",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "refusal", "refusal": "No."}],
                    }
                ],
            ),
            True,
            "completed",
        ),
        (
            "openai",
            _openai_body(
                "i1", status="incomplete", incomplete_details={"reason": "max_output_tokens"}
            ),
            False,
            "max_output_tokens",
        ),
        ("google", _gemini_response("i1", finish="SAFETY"), True, "SAFETY"),
        ("google", _gemini_response("i1", finish="MAX_TOKENS"), False, "MAX_TOKENS"),
    ],
)
def test_refusals_and_output_cap_stops_are_flagged(
    provider: str, line: dict[str, Any], refusal: bool, stop_reason: str
) -> None:
    result = _parse(provider, line)
    assert result.status == "succeeded" and result.unbilled is False
    assert (result.refusal, result.stop_reason) == (refusal, stop_reason)


def test_usage_becomes_buckets_for_every_provider() -> None:
    a = anthropic_result(_anthropic_ok("i1"))
    assert a.buckets == Buckets(1200, 0, 0, 300, 0) and a.reported_model == "claude-haiku-5-5"
    o = openai_result(_openai_body("i1"))
    assert o.buckets == Buckets(800, 0, 200, 50, 40) and o.reported_model == "gpt-6-luna"
    g = gemini_result(_gemini_response("i1"))
    assert g.buckets == Buckets(800, 0, 100, 150, 30)
    assert g.raw_usage == _gemini_response("i1")["response"]["usageMetadata"]
    assert g.reported_model == "gemini-3.1-flash-lite"


async def test_poll_returns_none_until_terminal() -> None:
    states = iter(["in_progress", "ended"])
    router = Router(
        [
            (
                "GET",
                r"/v1/messages/batches/msgbatch_1$",
                lambda _r: httpx2.Response(
                    200, json={**ANTHROPIC_BATCH, "processing_status": next(states)}
                ),
            )
        ]
    )
    client = _anthropic_client(router)
    assert await client.poll("msgbatch_1") is None
    assert await client.poll("msgbatch_1") == "ended"
    openai_states = iter(["in_progress", "finalizing", "completed"])
    router = Router(
        [
            (
                "GET",
                r"/v1/batches/batch_1$",
                lambda _r: httpx2.Response(200, json=_openai_batch(next(openai_states))),
            )
        ]
    )
    client = _openai_client(router)
    assert [await client.poll("batch_1") for _ in range(3)] == [None, None, "completed"]


async def test_a_failed_openai_batch_returns_every_item_errored_and_not_retryable() -> None:
    router = Router([("GET", r"/v1/batches/batch_1$", _json(httpx2, _openai_batch("failed")))])
    results = await _openai_client(router).results("batch_1", ["i1", "i2"])
    assert [(r.item_id, r.status, r.unbilled, r.retryable) for r in results] == [
        ("i1", "errored", True, False),
        ("i2", "errored", True, False),
    ]


async def test_a_failed_gemini_job_without_results_is_all_missing() -> None:
    job = _gemini_job("JOB_STATE_FAILED")
    router = Router([("GET", r"/v1beta/batches/b1", _json(httpx, job))])
    results = await _gemini_client(router).results("batches/b1", ["i1", "i2"])
    assert {(r.status, r.unbilled, r.retryable) for r in results} == {("missing", False, True)}


@pytest.mark.parametrize("status,kind", [(400, "rejected"), (429, "rate_limited"), (500, "server")])
async def test_submit_errors_are_classified(status: int, kind: str) -> None:
    error = {"type": "error", "error": {"type": "api_error", "message": "boom"}}
    router = Router([("POST", r"/v1/messages/batches$", _json(httpx2, error, status=status))])
    with pytest.raises(ProviderCallError) as raised:
        await _anthropic_client(router).submit([_req()])
    assert raised.value.kind == kind and raised.value.status == status
    router = Router([("POST", r"/v1/files$", _json(httpx2, {"error": {"message": "x"}}, status))])
    with pytest.raises(ProviderCallError) as raised:
        await _openai_client(router).submit([_req()])
    assert raised.value.kind == kind


async def test_a_submit_timeout_is_classified() -> None:
    def hang(request: Any) -> Any:
        raise httpx2.ReadTimeout("slow", request=request)

    router = Router([("POST", r"/v1/messages/batches$", hang)])
    with pytest.raises(ProviderCallError) as raised:
        await _anthropic_client(router).submit([_req()])
    assert raised.value.kind == "timeout"


def test_batch_request_refuses_a_bad_item_id() -> None:
    for bad in ("", "a.b", "x" * 65):
        with pytest.raises(ValueError):
            BatchRequest(bad, "s", "u", SCHEMA, "grade")


def _family(**overrides: Any) -> Any:
    raw = {
        "id": "haiku",
        "provider": "anthropic",
        "model_id": "claude-haiku-5-5",
        "api": "anthropic-messages",
        "arm": "hosted",
        "client": "anthropic",
        "token_count_method": "not_applicable",
        "latency_scope": "not_applicable",
        "headline_eligible": False,
        "headline_ineligibility_reason": "a judge",
        "max_input_tokens": 1_000_000,
        "max_output_tokens": 16384,
        "framing_allowance_tokens": 256,
        "output_cap_includes_reasoning": True,
        **overrides,
    }
    return model_family_from_raw(raw)


def test_make_batch_client_is_guarded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in PAID_KEY_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("BENCHMARK_LIVE_PROVIDERS_ENABLED", raising=False)
    with pytest.raises(LiveProviderDisabledError):
        make_batch_client(_family(), effort="low", timeout_s=5.0)
    monkeypatch.setenv("BENCHMARK_LIVE_PROVIDERS_ENABLED", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "other-key")
    with pytest.raises(BenchmarkValidationError, match="OPENAI_API_KEY"):
        make_batch_client(_family(), effort="low", timeout_s=5.0)
    monkeypatch.delenv("OPENAI_API_KEY")
    assert isinstance(
        make_batch_client(_family(), effort="low", timeout_s=5.0), AnthropicBatchClient
    )
    with pytest.raises(BenchmarkValidationError, match="provider"):
        make_batch_client(_family(provider="openai"), effort=None, timeout_s=5.0)
    for name in PAID_KEY_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("BENCHMARK_LIVE_PROVIDERS_ENABLED")
    script = tmp_path / "judge.yml"
    script.write_text(yaml.safe_dump({"rules": [{"match": ".*", "output": {"a": True}}]}))
    scripted = make_batch_client(
        _family(api="scripted", script=str(script)), effort="low", timeout_s=5.0
    )
    assert isinstance(scripted, ScriptedBatchClient)
    with pytest.raises(BenchmarkValidationError, match="absolute"):
        make_batch_client(_family(api="scripted", script="judge.yml"), effort=None, timeout_s=5.0)


def _script(tmp_path: Path, data: dict[str, Any]) -> dict[str, Any]:
    path = tmp_path / "judge.yml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


async def test_scripted_batch_client_plays_rules_polls_and_submit_errors(tmp_path: Path) -> None:
    script = _script(
        tmp_path,
        {
            "rules": [
                {"match": "^i2$", "status": "errored"},
                {"match": "^i3$", "status": "missing"},
                {"match": "^i4$", "text": "not json", "stop_reason": "max_tokens"},
                {
                    "match": ".*",
                    "output": {"explanation": "ok", "attempted": True},
                    "usage": {"input_tokens": 10, "output_tokens": 2},
                },
            ],
            "polls": [None, None],
            "submit_errors": [{"kind": "rate_limited", "status": 429}, None],
        },
    )
    client = ScriptedBatchClient("anthropic", "scripted-haiku", script)
    with pytest.raises(ProviderCallError) as raised:
        await client.submit([_req("i1")])
    assert (raised.value.kind, raised.value.status) == ("rate_limited", 429)
    requests = [_req(i) for i in ("i1", "i2", "i3", "i4")]
    batch_id = await client.submit(requests)
    assert client.requests == requests and client.submits == 2
    assert [await client.poll(batch_id) for _ in range(3)] == [None, None, "ended"]
    results = await client.results(batch_id, ["i1", "i2", "i3", "i4"])
    ok, errored, missing, capped = results
    assert json.loads(ok.text or "") == {"explanation": "ok", "attempted": True}
    assert ok.buckets == Buckets(10, 0, 0, 2, 0) and ok.reported_model == "scripted-haiku"
    assert ok.stop_reason == "end_turn"
    assert (errored.status, errored.unbilled, errored.retryable) == ("errored", True, True)
    assert (missing.status, missing.unbilled) == ("missing", False)
    assert (capped.text, capped.stop_reason) == ("not json", "max_tokens")
    no_errors = {**script, "submit_errors": []}
    failed = ScriptedBatchClient("openai", "scripted-luna", no_errors, batch_status="failed")
    batch_id = await failed.submit([_req("i1")])
    for _ in range(3):
        state = await failed.poll(batch_id)
    assert state == "failed"
    (only,) = await failed.results(batch_id, ["i1"])
    assert (only.status, only.unbilled, only.retryable) == ("errored", True, False)


def test_scripted_rules_must_end_with_a_catch_all(tmp_path: Path) -> None:
    with pytest.raises(BenchmarkValidationError, match=r"\.\*"):
        ScriptedBatchClient("google", "m", {"rules": [{"match": "^i1$", "output": {}}]})
