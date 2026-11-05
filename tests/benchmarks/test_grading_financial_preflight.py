"""Financial preflight findings 1/2: read retries never become paid resubmissions."""

import json
from dataclasses import asdict
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import httpx2
import pytest

from benchmarks.agentic.host import _GradingJob
from benchmarks.agentic.ledger import LedgerService, load_price_snapshot
from benchmarks.agentic.types import ProviderCallError
from benchmarks.agentic.worker import ProtocolChannel
from benchmarks.grading import worker_task
from benchmarks.grading.batch import (
    BatchUploadError,
    GeminiBatchClient,
    OpenAIBatchClient,
    ScriptedBatchClient,
)
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks.test_grading_batch import (
    Router,
    _gemini_client,
    _gemini_response,
    _json,
    _openai_body,
    _openai_client,
    _req,
)
from tests.benchmarks.test_grading_jobs import FIX, Judges


@pytest.fixture
def judges(tmp_path):
    harness = Judges(tmp_path)
    yield harness
    harness.ledger.close()


class WorkerPort:
    """In-process framing seam; run the real host state machine and durable ledger."""

    check_content = ProtocolChannel.check_content

    def __init__(self, grading):
        self._secrets = (b"synthetic-worker-only-key",)
        self.grading = grading
        self.pending = []

    def send(self, message):
        self.pending.append(message)

    async def flush(self):
        while self.pending:
            await self.grading.handle(self.pending.pop(0))

    async def request(self, message):
        await self.flush()
        return await self.grading.handle(message)

    async def settle(self, unit_id, *, outcome, buckets):
        await self.request(
            {"type": "settle", "unit_id": unit_id, "outcome": outcome, "buckets": buckets}
        )


async def without_signals(coroutine):
    """In-process acceptance runs need neither subprocesses nor signal handlers."""
    return await coroutine


async def run_grading_jobs(jobs, *, ledger, on_message, **kwargs):
    """Replace worker I/O only, retaining host validation and durable settlement."""
    for job in jobs:
        port = WorkerPort(_GradingJob(job, ledger, on_message))
        await worker_task.run_batch_task(
            {"family": job.family.raw, "payload": job.payload, "caps": asdict(job.caps)},
            port,
            port,
        )
        await port.flush()
        await port.grading.finish()


async def run_worker(
    judges,
    monkeypatch,
    client,
    *,
    judge="luna",
    count=1,
    sleep=None,
    clock=lambda: 0,
    ceiling_s=100,
):
    job = judges.job(judge=judge, count=count)
    port = make_port(judges, job)
    monkeypatch.setattr(worker_task, "make_batch_client", lambda *a, **kw: client)
    await worker_task.run_batch_task(
        {"family": job.family.raw, "payload": job.payload, "caps": {"model_call_timeout_s": 5}},
        port,
        port,
        poll_s=1,
        ceiling_s=ceiling_s,
        sleep=sleep or AsyncMock(),
        clock=clock,
    )
    await port.flush()
    await port.grading.finish()


def make_port(judges, job):
    service = LedgerService(
        judges.ledger,
        load_price_snapshot(FIX / "prices.yml").models,
        {job.family.model_id: job.family},
        {job.family.provider: D("20")},
    )

    async def collect(_job, message):
        judges.messages.append(message)

    return WorkerPort(_GradingJob(job, service, collect))


def scripted(provider, model_id):
    usage = (
        {"prompt_token_count": 100, "candidates_token_count": 2}
        if provider == "google"
        else {"input_tokens": 100, "output_tokens": 2}
    )
    return ScriptedBatchClient(
        provider, model_id, {"rules": [{"match": ".*", "text": "ok", "usage": usage}]}
    )


@pytest.mark.parametrize(
    "provider,judge", [("anthropic", "haiku"), ("openai", "luna"), ("google", "flash-lite")]
)
@pytest.mark.parametrize("failures", [1, 10])
async def test_terminal_batch_reads_retry_same_id_without_another_take(
    judges, monkeypatch, provider, judge, failures
):
    client = scripted(provider, judges.job(judge=judge).family.model_id)
    original = client.results
    reads = []
    polls = []

    async def results(batch_id, ids):
        reads.append((batch_id, list(ids)))
        if len(reads) <= failures:
            raise ProviderCallError("connection", "synthetic result reset")
        return await original(batch_id, ids)

    original_poll = client.poll

    async def poll(batch_id):
        polls.append(batch_id)
        return await original_poll(batch_id)

    monkeypatch.setattr(client, "results", results)
    monkeypatch.setattr(client, "poll", poll)
    await run_worker(judges, monkeypatch, client, judge=judge)
    assert client.submits == 1
    assert polls == ["scripted-batch-1"]
    assert reads == [("scripted-batch-1", ["i1"])] * (2 if failures == 1 else 10)
    assert len(judges.events("reserve")) == len(judges.events("settle")) == 1
    assert len(judges.frames("submitted")) == len(judges.frames("done")) == 1
    assert not judges.frames("item_terminal")
    if failures == 1:
        assert [D(e["booked"]) for e in judges.events("settle")] == [D(".000060")]
        assert [(r["status"], r["attempt"]) for r in judges.results()] == [("succeeded", 1)]
        assert not judges.frames("item_not_run")
    else:
        assert [D(e["booked"]) for e in judges.events("settle")] == [D(".32788")]
        assert not judges.results()
        assert judges.frames("item_not_run") == [
            {"type": "item_not_run", "item_id": "i1", "attempt": 1, "reason": "batch_failed"}
        ]


async def test_result_reads_obey_the_original_poll_deadline(judges, monkeypatch):
    client = scripted("openai", judges.job(judge="luna").family.model_id)
    client.results = AsyncMock(side_effect=ProviderCallError("server", "read failed"))
    now = [0]

    async def sleep(delay):
        now[0] += delay

    await run_worker(judges, monkeypatch, client, sleep=sleep, clock=lambda: now[0], ceiling_s=2)
    assert client.submits == 1
    assert client.results.await_count == 1
    assert now[0] == 2
    assert [D(e["booked"]) for e in judges.events("settle")] == [D(".32788")]


def openai_sdk(*, status="completed", upload_error=None, create_error=None, content=None):
    return SimpleNamespace(
        files=SimpleNamespace(
            create=AsyncMock(return_value=SimpleNamespace(id="file-in"), side_effect=upload_error),
            content=AsyncMock(return_value=SimpleNamespace(text=content or "")),
        ),
        batches=SimpleNamespace(
            create=AsyncMock(return_value=SimpleNamespace(id="b1"), side_effect=create_error),
            retrieve=AsyncMock(
                return_value=SimpleNamespace(
                    status=status, output_file_id="out", error_file_id=None
                )
            ),
        ),
    )


async def test_failed_openai_batch_uses_observed_state_and_books_all_rows_at_zero(
    judges, monkeypatch
):
    sdk = openai_sdk(status="failed")

    async def retrieve(_batch_id):
        if sdk.batches.retrieve.await_count > 1:
            raise ProviderCallError("connection", "redundant read failed")
        return SimpleNamespace(status="failed")

    sdk.batches.retrieve.side_effect = retrieve
    await run_worker(judges, monkeypatch, OpenAIBatchClient(sdk, "fixture", 16384, None), count=3)
    assert sdk.batches.create.await_count == sdk.batches.retrieve.await_count == 1
    assert not sdk.files.content.called
    assert [D(e["booked"]) for e in judges.events("settle")] == [D(0)]
    assert len(judges.results()) == 3
    assert all(
        r["status"] == "errored" and r["unbilled"] and not r["retryable"] and not r["terminal"]
        for r in judges.results()
    )
    assert [(r["reason"], r["attempt"]) for r in judges.frames("item_not_run")] == [
        ("batch_failed", 1)
    ] * 3


@pytest.mark.parametrize("provider", ["openai", "google"])
async def test_terminal_metadata_is_retained_per_batch_across_download_retries(provider):
    if provider == "openai":
        sdk = openai_sdk()
        sdk.batches.retrieve.side_effect = lambda batch_id: SimpleNamespace(
            status="completed",
            output_file_id="first" if batch_id == "b1" else "second",
            error_file_id=None,
        )
        sdk.files.content.side_effect = [
            ProviderCallError("connection", "reset"),
            SimpleNamespace(text=json.dumps(_openai_body("i1"))),
        ]
        client = OpenAIBatchClient(sdk, "fixture", 16384, None)
        retrieve, download = sdk.batches.retrieve, sdk.files.content
    else:
        retrieve = AsyncMock(
            side_effect=lambda *, name: SimpleNamespace(
                state="JOB_STATE_SUCCEEDED",
                dest=SimpleNamespace(file_name="files/first" if name == "b1" else "files/second"),
            )
        )
        download = AsyncMock(
            side_effect=[
                ProviderCallError("connection", "reset"),
                json.dumps(_gemini_response("i1")).encode(),
            ]
        )
        sdk = SimpleNamespace(
            aio=SimpleNamespace(
                batches=SimpleNamespace(get=retrieve), files=SimpleNamespace(download=download)
            )
        )
        client = GeminiBatchClient(sdk, "fixture", 16384, None)
    await client.poll("b1")
    await client.poll("b2")
    with pytest.raises(ProviderCallError):
        await client.results("b1", ["i1"])
    rows = await client.results("b1", ["i1"])
    assert rows[0].status == "succeeded"
    assert retrieve.await_count == 2
    assert download.call_args_list[0] == download.call_args_list[1]
    if provider == "openai":
        assert download.call_args.args == ("first",)
    else:
        assert download.call_args.kwargs == {"file": "files/first"}


def submission_client(provider, *, upload_error=None, create_error=None):
    if provider == "openai":
        sdk = openai_sdk(
            upload_error=upload_error,
            create_error=create_error,
            content=json.dumps(_openai_body("i1")),
        )
        return (
            OpenAIBatchClient(sdk, "fixture", 16384, None),
            sdk.files.create,
            sdk.batches.create,
        )
    upload = AsyncMock(return_value=SimpleNamespace(name="files/in"), side_effect=upload_error)
    create = AsyncMock(return_value=SimpleNamespace(name="b1"), side_effect=create_error)
    sdk = SimpleNamespace(
        aio=SimpleNamespace(
            files=SimpleNamespace(
                upload=upload,
                download=AsyncMock(return_value=json.dumps(_gemini_response("i1")).encode()),
            ),
            batches=SimpleNamespace(
                create=create,
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        state="JOB_STATE_SUCCEEDED", dest=SimpleNamespace(file_name="files/out")
                    )
                ),
            ),
        )
    )
    return GeminiBatchClient(sdk, "fixture", 16384, None), upload, create


@pytest.mark.parametrize("provider,judge", [("openai", "luna"), ("google", "flash-lite")])
@pytest.mark.parametrize("kind", ["server", "connection", "timeout"])
async def test_upload_failure_books_zero_and_exhausts_three_free_attempts(
    judges, monkeypatch, provider, judge, kind
):
    client, upload, create = submission_client(
        provider, upload_error=ProviderCallError(kind, "upload failed", retry_after_s=3)
    )
    sleep = AsyncMock()
    await run_worker(judges, monkeypatch, client, judge=judge, sleep=sleep)
    assert upload.await_count == 3 and create.await_count == 0
    assert [D(e["booked"]) for e in judges.events("settle")] == [D(0)] * 3
    assert len(judges.events("reserve")) == 3
    assert not judges.frames("submitted") and not judges.frames("item_terminal")
    assert judges.frames("item_not_run") == [
        {"type": "item_not_run", "item_id": "i1", "attempt": 3, "reason": "submit_rejected"}
    ]
    assert all(call.args == (3,) for call in sleep.call_args_list)


@pytest.mark.parametrize("provider,judge", [("openai", "luna"), ("google", "flash-lite")])
@pytest.mark.parametrize("kind", ["server", "connection", "timeout"])
async def test_ambiguous_create_failure_keeps_two_conservative_paid_bookings(
    judges, monkeypatch, provider, judge, kind
):
    client, upload, create = submission_client(
        provider, create_error=ProviderCallError(kind, "create may have reached provider")
    )
    await run_worker(judges, monkeypatch, client, judge=judge)
    assert upload.await_count == create.await_count == 2
    assert [D(e["booked"]) for e in judges.events("settle")] == [D(".32788")] * 2
    assert judges.frames("item_not_run")[0]["reason"] == "whole_batch_failures"


async def test_upload_error_preserves_classification_for_retry_policy():
    error = ProviderCallError("rate_limited", "upload quota", status=429, retry_after_s=4)
    client, _upload, _create = submission_client("openai", upload_error=error)
    with pytest.raises(BatchUploadError) as caught:
        await client.submit([_req()])
    assert (caught.value.kind, caught.value.status, caught.value.retry_after_s) == (
        "rate_limited",
        429,
        4,
    )


@pytest.mark.parametrize(
    "provider,judge,booked", [("openai", "luna", ".000660"), ("google", "flash-lite", ".001155")]
)
async def test_two_upload_failures_then_success_create_only_one_billable_batch(
    judges, monkeypatch, provider, judge, booked
):
    client, upload, create = submission_client(provider)
    uploaded = upload.return_value
    upload.side_effect = [
        ProviderCallError("server", "upload 1"),
        ProviderCallError("timeout", "upload 2"),
        uploaded,
    ]
    await run_worker(judges, monkeypatch, client, judge=judge)
    assert upload.await_count == 3 and create.await_count == 1
    assert [D(e["booked"]) for e in judges.events("settle")] == [D(0), D(0), D(booked)]
    assert [(r["status"], r["attempt"]) for r in judges.results()] == [("succeeded", 3)]
    assert not judges.frames("item_not_run") and len(judges.frames("submitted")) == 1


@pytest.mark.parametrize("stage", ["openai_upload", "gemini_start", "gemini_finalize"])
async def test_sdk_upload_errors_are_free_even_when_the_upload_response_is_ambiguous(
    judges, monkeypatch, stage
):
    error = {"error": {"message": "synthetic unavailable", "code": 503, "status": "UNAVAILABLE"}}
    if stage == "openai_upload":
        router = Router([("POST", r"/v1/files$", _json(httpx2, error, status=503))])
        client, judge = _openai_client(router), "luna"
    else:
        routes = [("POST", r"/upload/v1beta/files", _json(httpx, error, status=503))]
        if stage == "gemini_finalize":
            routes = [
                ("POST", r"upload_id=u1", _json(httpx, error, status=503)),
                (
                    "POST",
                    r"/upload/v1beta/files",
                    _json(
                        httpx,
                        {},
                        headers={
                            "x-goog-upload-url": "https://generativelanguage.googleapis.com/upload/v1beta/files?upload_id=u1"
                        },
                    ),
                ),
            ]
        router = Router(routes)
        client, judge = _gemini_client(router), "flash-lite"
    await run_worker(judges, monkeypatch, client, judge=judge)
    assert [D(e["booked"]) for e in judges.events("settle")] == [D(0)] * 3
    assert not judges.frames("submitted")
    assert judges.frames("item_not_run")[0]["reason"] == "submit_rejected"


@pytest.mark.parametrize(
    "flag,submitted,outcome",
    [(True, False, "connection"), ("true", True, "connection"), (True, True, "rejected")],
)
async def test_results_unavailable_flag_requires_a_submitted_paid_unit(
    judges, flag, submitted, outcome
):
    port = make_port(judges, judges.job())
    grant = await port.request({"type": "reserve", "kind": "batch"})
    unit = grant["unit_id"]
    if submitted:
        await port.request({"type": "submitted", "unit_id": unit, "provider_batch_id": "b1"})
    with pytest.raises(BenchmarkValidationError, match="invalid batch failure"):
        await port.request(
            {
                "type": "settle",
                "unit_id": unit,
                "outcome": outcome,
                "buckets": None,
                "results_unavailable": flag,
            }
        )
    assert not judges.events("settle")
