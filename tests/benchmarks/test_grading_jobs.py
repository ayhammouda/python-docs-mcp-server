"""Scripted batch and pull workers use the same framed launcher and ledger as answering."""

import asyncio
import json
import sys
from dataclasses import asdict, replace
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from benchmarks.agentic.host import (
    GRADING_CAPS,
    GRADING_SYSTEM,
    RESULT_CHUNK_BYTES,
    ItemSource,
    LocalLauncher,
    WorkerJob,
    run_jobs,
)
from benchmarks.agentic.ledger import Ledger, LedgerService, PlannedItem, load_price_snapshot
from benchmarks.agentic.protocol import FrameTooLarge, KeyLeakError, encode_frame
from benchmarks.agentic.types import Buckets
from benchmarks.agentic.worker import run_worker
from benchmarks.grading.aggregate import has_final_record, load_answer_votes, write_vote_record
from benchmarks.grading.batch import (
    BatchItemResult,
    BatchRequest,
    ScriptedBatchClient,
    anthropic_result,
    gemini_result,
    missing,
    openai_failed_result,
    openai_result,
)
from benchmarks.grading.panel import as_model_family, load_panel
from benchmarks.grading.worker_task import chunk_results
from benchmarks.runner import BenchmarkValidationError

FIX = Path(__file__).parent / "fixtures" / "grading"
REPO = FIX.parents[3]
OK = {"match": ".*", "text": "ok", "usage": {"input_tokens": 100, "output_tokens": 2}}
FREE = {"kind": "rate_limited", "status": 429}
PAID = {"kind": "server", "status": 500}


# Override only timing in a real scripted worker, after the output guard has been installed.
class ScriptLauncher(LocalLauncher):
    def __init__(self, *, poll_s=0, ceiling_s=100, extra=""):
        self.patch = f"""
from functools import partial
from benchmarks.grading import worker_task as task
task.backoff_s = lambda *_: 0
task.run_batch_task = partial(task.run_batch_task, poll_s={poll_s}, ceiling_s={ceiling_s})
{extra}
"""

    def command(self, job):
        _, env = super().command(job)
        code = (
            "from benchmarks.agentic import worker\n"
            "protect = worker.protect_output\n"
            "def setup():\n    channel = protect()\n    exec("
            + repr(self.patch)
            + ", {})\n    return channel\nworker.protect_output = setup\n"
            "raise SystemExit(worker.main_worker())"
        )
        return [sys.executable, "-c", code], env


class StubLauncher(LocalLauncher):
    def __init__(self, body):
        self.body = body

    def command(self, job):
        _, env = super().command(job)
        code = (
            "import os\nfrom benchmarks.agentic.protocol import encode_frame,read_frame_fd\n"
            "def send(m): os.write(1,encode_frame(m))\n"
            "def req(m): send(m); return read_frame_fd(0)\n"
            "job=read_frame_fd(0)\n" + self.body
        )
        return [sys.executable, "-c", code], env


class Judges:
    def __init__(self, tmp):
        self.tmp = tmp
        self.panel = load_panel(FIX / "panel.yml")
        self.ledger = Ledger.open(tmp / "ledger", run_id="test")
        self.messages = []

    def job(self, *, count=1, judge="haiku", script=None):
        spec = self.panel.judges[judge]
        if script is not None:
            path = self.tmp / f"{judge}.yml"
            path.write_text(yaml.safe_dump({"rules": [OK], **script}))
            spec = replace(spec, raw={**spec.raw, "script": str(path)})
        items = []
        for i in range(count):
            key = f"i{i + 1}"
            planned = PlannedItem(key, 100, 16384, 100)
            items.append((planned, asdict(BatchRequest(key, "s", "u", {}, "grade"))))
        return WorkerJob(
            judge,
            as_model_family(spec),
            GRADING_SYSTEM,
            (),
            GRADING_CAPS,
            task="batches",
            payload={"effort": "low"},
            source=ItemSource(items),
        )

    async def run(self, *jobs, launcher=None, margin="20", on_message=None):
        prices = load_price_snapshot(FIX / "prices.yml").models
        service = LedgerService(
            self.ledger,
            prices,
            {j.family.model_id: j.family for j in jobs},
            {p: D(margin) for p in ("anthropic", "openai", "google")},
        )

        async def collect(job, msg):
            self.messages.append(msg)
            if on_message:
                await on_message(job, msg)

        await run_jobs(
            jobs,
            launcher=launcher or ScriptLauncher(),
            ledger=service,
            on_message=collect,
            log_dir=self.tmp / "logs",
            cwd=REPO,
        )

    def events(self, kind):
        return [e for e in self.ledger.events("test") if e["event"] == kind]

    def frames(self, kind):
        return [m for m in self.messages if m["type"] == kind]

    def results(self):
        return [r for m in self.frames("batch_results") for r in m["results"]]


@pytest.fixture
def judges(tmp_path):
    h = Judges(tmp_path)
    yield h
    h.ledger.close()


async def test_a_batch_job_grades_every_item_once_and_books_each_unit(judges):
    job = judges.job(count=5)
    await judges.run(job, margin=".33", launcher=LocalLauncher())
    assert [r["item_id"] for r in judges.results()] == [f"i{i}" for i in range(1, 6)]
    assert len(judges.events("reserve")) == len(judges.events("settle")) == 3
    assert judges.ledger.spent("anthropic") == D(".000060") * 5


async def test_a_default_worker_job_is_a_trajectory_job(judges):
    j = judges.job()
    default = WorkerJob(j.job_id, j.family, j.system, (), j.caps)
    assert default.task == "trajectories" and default.payload == {}
    await judges.run(
        default,
        launcher=StubLauncher("send({'type':'done','task':job['task'],'payload':job['payload']})"),
    )
    assert judges.frames("done") == [{"type": "done", "task": "trajectories", "payload": {}}]


RESERVE = "unit=req({'type':'reserve','kind':'batch','bound':'0'})['unit_id']\n"


async def test_the_host_prices_a_batch_from_its_own_items_never_the_workers_claim(judges):
    await judges.run(judges.job(), launcher=StubLauncher(RESERVE))
    assert D(judges.events("reserve")[0]["bound"]) == D(".16394")


async def test_oversized_batch_results_stream_in_bounded_frames(judges):
    job = judges.job(count=700, script={"rules": [{**OK, "text": "ü" * 25000}]})
    await judges.run(job, margin="130")
    assert len(judges.events("reserve")) == len(judges.events("settle")) == 1
    frames = judges.frames("batch_results")
    assert len(frames) > 1 and all(len(encode_frame(f)) <= RESULT_CHUNK_BYTES for f in frames)
    assert len({r["item_id"] for r in judges.results()}) == 700


def test_chunk_results_drops_raw_only_for_a_single_oversized_result():
    small = replace(missing("i1"), text="keep", raw_usage={"x": 3}, raw={"a": "a"})
    big = replace(small, item_id="i2", raw={"a": "a" * 4000})
    a, b = [r for chunk in chunk_results([small, big], limit=1024) for r in chunk]
    assert a["raw"] == {"a": "a"} and b["raw"] is None and b["raw_dropped"]
    assert (b["text"], b["raw_usage"], b["buckets"]) == ("keep", {"x": 3}, None)
    with pytest.raises(FrameTooLarge):
        list(chunk_results([replace(big, text="a" * 4000)], limit=1024))


async def test_a_denied_batch_lists_the_remaining_items_as_not_run(judges):
    judges.ledger.close()
    judges.ledger = Ledger.open(judges.tmp / "ledger", run_id="test", caps={"anthropic": D(".1")})
    await judges.run(judges.job(count=2))
    assert not judges.events("reserve")
    assert [(m["reason"], m["attempt"]) for m in judges.frames("item_not_run")] == [
        ("budget_stop", 0)
    ] * 2


async def test_every_grading_reserve_event_names_its_job(judges):
    await judges.run(judges.job(), judges.job(judge="luna"))
    assert {(e["provider"], e["job_id"], e["kind"]) for e in judges.events("reserve")} == {
        ("anthropic", "haiku", "batch"),
        ("openai", "luna", "batch"),
    }


async def test_errored_items_are_retried_at_most_twice(judges):
    await judges.run(
        judges.job(count=2, script={"rules": [{"match": "^i2$", "status": "errored"}, OK]})
    )
    assert [(r["attempt"], r["terminal"]) for r in judges.results() if r["item_id"] == "i2"] == [
        (1, False),
        (2, False),
        (3, True),
    ]
    assert judges.frames("item_not_run") == [
        {"type": "item_not_run", "item_id": "i2", "attempt": 3, "reason": "retries_exhausted"}
    ]


async def test_three_free_submit_failures_are_not_terminal(judges):
    await judges.run(judges.job(script={"submit_errors": [FREE] * 3}))
    assert [D(e["booked"]) for e in judges.events("settle")] == [D(0)] * 3
    assert not judges.results() and not judges.frames("item_terminal")
    assert judges.frames("item_not_run")[0]["reason"] == "submit_rejected"
    judges.ledger.close()
    judges.ledger = Ledger.open(judges.tmp / "ledger", run_id="retry")
    await judges.run(judges.job())
    assert judges.results()[0]["attempt"] == 1
    assert len(judges.ledger.events("retry")) == 2


@pytest.mark.parametrize(
    "errors,missing_first", [([FREE, PAID, FREE], False), ([None, FREE, FREE], True)]
)
async def test_a_paid_no_result_take_makes_the_third_take_terminal(judges, errors, missing_first):
    job = judges.job(
        script={"submit_errors": errors, "rules": [{"match": ".*", "status": "missing"}]}
    )
    await judges.run(job)
    assert len(judges.results()) == int(missing_first)
    (terminal,) = judges.frames("item_terminal")
    assert terminal["item_id"] == "i1" and terminal["attempt"] == 3
    assert terminal["unit_id"] == judges.events("settle")[-1]["unit_id"]
    assert judges.frames("item_not_run")[0]["reason"] == "retries_exhausted"
    path = judges.tmp / "items" / "answers" / "i1.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"meta": {"competitor_id": "c", "corpus_id": "q", "input_sha256": "abc"}})
    )
    record = write_vote_record(
        judges.tmp,
        target="answers",
        judge=judges.panel.judges["haiku"],
        effort="low",
        pass_number=1,
        grading_run_id="r",
        attempt=terminal["attempt"],
        result=missing("i1"),
        terminal=True,
        unit_id=terminal["unit_id"],
        provider_batch_id=None,
        input_sha256=json.loads(path.read_text())["meta"]["input_sha256"],
        schema={},
    )
    assert json.loads(record.read_text())["withheld"] == "missing"
    votes = load_answer_votes(judges.tmp, panel=judges.panel, judges={"haiku": "low"})
    assert votes["c", "q"].records["haiku"]["attempts"] == 3


async def test_a_failed_openai_batch_is_not_resubmitted(judges):
    await judges.run(judges.job(judge="luna", script={"batch_status": "failed"}))
    assert len(judges.events("reserve")) == 1 and judges.ledger.spent("openai") == 0
    assert [(r["status"], r["terminal"]) for r in judges.results()] == [("errored", False)]
    assert judges.frames("item_not_run")[0]["reason"] == "batch_failed"


async def test_a_second_whole_batch_gemini_failure_stops_the_provider(judges):
    await judges.run(
        judges.job(
            judge="flash-lite",
            script={
                "batch_status": "JOB_STATE_FAILED",
                "rules": [{"match": ".*", "status": "missing"}],
            },
        )
    )
    assert len(judges.events("reserve")) == 2
    assert judges.ledger.spent("google") == 4 * D(judges.events("reserve")[0]["bound"])
    assert not any(r["terminal"] for r in judges.results())
    assert judges.frames("item_not_run")[0]["reason"] == "whole_batch_failures"


async def test_a_failed_submit_releases_its_items_and_books_by_outcome(judges):
    await judges.run(judges.job(script={"submit_errors": [PAID]}))
    assert D(judges.events("settle")[0]["booked"]) == 2 * D(judges.events("reserve")[0]["bound"])
    assert [r["attempt"] for r in judges.results()] == [2]


async def test_a_lost_worker_releases_its_taken_items_as_not_run(judges):
    await judges.run(judges.job(count=3), launcher=StubLauncher(RESERVE), margin=".33")
    assert D(judges.events("settle")[0]["booked"]) == 2 * D(judges.events("reserve")[0]["bound"])
    assert sorted(
        (m["item_id"], m["attempt"], m["reason"]) for m in judges.frames("item_not_run")
    ) == [("i1", 1, "worker_lost"), ("i2", 1, "worker_lost"), ("i3", 0, "worker_lost")]


async def test_free_count_worker_leak_preserves_the_credential_failure(judges):
    job = replace(judges.job(), task="items")
    launcher = StubLauncher(
        "from benchmarks.agentic.protocol import WORKER_LEAK_REASON\n"
        "send({'type':'error','reason':WORKER_LEAK_REASON})"
    )

    async def collect(job, msg):
        judges.messages.append(msg)

    with pytest.raises(KeyLeakError, match="credential check"):
        await run_jobs(
            [job],
            launcher=launcher,
            ledger=None,
            on_message=collect,
            log_dir=judges.tmp / "logs",
            cwd=REPO,
        )
    assert judges.ledger.invocation_events() == []


async def test_a_pull_job_hands_out_items_one_at_a_time(judges):
    job = replace(judges.job(count=2), task="items")
    await judges.run(
        job,
        launcher=StubLauncher(
            "while (r:=req({'type':'next_item'}))['type']=='item':\n"
            " send({'type':'counted','item_id':r['item']['item_id']})\nsend({'type':'done'})"
        ),
    )
    assert [m["item_id"] for m in judges.frames("counted")] == ["i1", "i2"]
    assert not judges.events("reserve")


async def test_a_live_batch_job_without_its_bootstrap_is_refused(judges, monkeypatch):
    job = judges.job()
    frame = {
        "type": "job",
        "task": "batches",
        "family": {**job.family.raw, "api": "anthropic-messages"},
        "system": job.system.raw,
        "caps": asdict(job.caps),
    }
    client = Mock(side_effect=AssertionError())
    monkeypatch.setattr("benchmarks.grading.worker_task.make_batch_client", client)
    channel = Mock()
    assert await run_worker(channel, asyncio.StreamReader(), frame) == 2
    assert "bootstrap" in channel.send.call_args.args[0]["reason"]
    client.assert_not_called()


@pytest.mark.parametrize(
    "body",
    [
        "req({'type':'settle','unit_id':'foreign','outcome':'rejected'})",
        "req({'type':'reserve','kind':'model_call','input_bound':0,'output_bound':0})",
        "req({'type':'item_terminal','item_id':'i1'})",
        "req({'type':'batch_results','unit_id':unit,'results':[{'item_id':'../foreign'}]})",
        "req({'type':'settle_batch','unit_id':unit,'batch_state':'ended','result_count':0})",
    ],
)
async def test_invalid_batch_frames_cannot_reach_the_collector_or_cancel_a_booking(judges, body):
    with pytest.raises(BenchmarkValidationError):
        await judges.run(judges.job(), launcher=StubLauncher(RESERVE + body))
    assert not judges.results() and not judges.frames("item_terminal")
    assert judges.events("settle")[0]["outcome"] == "worker_lost"


async def test_batch_frames_use_the_existing_key_scan(judges):
    with pytest.raises(KeyLeakError) as exc:
        await judges.run(
            judges.job(),
            launcher=StubLauncher(
                RESERVE + "send({'type':'heartbeat','text':'sk-ant-api03-'+'x'*90})"
            ),
        )
    assert "x" * 90 not in str(exc.value) and not judges.frames("heartbeat")
    assert judges.events("settle")[0]["outcome"] == "worker_lost"


@pytest.mark.parametrize("missing_second", [True, False])
async def test_the_poll_ceiling_books_twice_the_bound_and_releases_the_items(
    judges, missing_second
):
    second = {"match": ".*", "status": "missing"} if missing_second else OK
    extra = """
now = [0]
async def sleep(delay):
    now[0] += delay
task.run_batch_task = partial(task.run_batch_task, clock=lambda: now[0],sleep=sleep)
from benchmarks.grading.batch import ScriptedBatchClient
original = ScriptedBatchClient.poll
async def poll(self,batch_id):
    if batch_id == 'scripted-batch-1':
        return None
    return await original(self,batch_id)
ScriptedBatchClient.poll = poll
"""
    await judges.run(
        judges.job(script={"rules": [second]}),
        launcher=ScriptLauncher(poll_s=1, ceiling_s=5, extra=extra),
    )
    events = judges.events("settle")
    assert [e["outcome"] for e in events] == ["timeout", "batch"]
    assert D(events[0]["booked"]) == 2 * D(judges.events("reserve")[0]["bound"])
    if missing_second:
        assert judges.frames("item_not_run")[0]["reason"] == "whole_batch_failures"
        assert D(events[1]["booked"]) == D(events[0]["booked"])
    else:
        assert judges.results()[0]["attempt"] == 2 and not judges.frames("item_not_run")


async def test_ten_consecutive_poll_errors_book_connection_then_stop(judges):
    extra = """
from benchmarks.grading.batch import ScriptedBatchClient
from benchmarks.agentic.types import ProviderCallError
async def poll(self,b):
    raise ProviderCallError("server","synthetic")
ScriptedBatchClient.poll = poll
"""
    await judges.run(judges.job(), launcher=ScriptLauncher(extra=extra))
    assert [e["outcome"] for e in judges.events("settle")] == ["connection", "connection"]
    assert judges.frames("item_not_run")[0]["reason"] == "whole_batch_failures"


async def test_heartbeats_keep_a_slow_batch_alive(judges, monkeypatch):
    # Allow cold interpreter startup. Once submitted, run under a one-second silence limit.
    real_timeout = asyncio.timeout

    def timeout(seconds):
        if seconds == 1 and not judges.frames("submitted"):
            seconds = GRADING_CAPS.model_call_timeout_s
        return real_timeout(seconds)

    monkeypatch.setattr(asyncio, "timeout", timeout)
    job = judges.job(script={"polls": [None] * 6})
    job = replace(job, caps=replace(job.caps, trajectory_s=0.5, model_call_timeout_s=0.5))
    await judges.run(job, launcher=ScriptLauncher(poll_s=0.2))
    assert len(judges.frames("heartbeat")) == 6 and judges.results()[0]["status"] == "succeeded"


async def test_cancellation_after_submit_settles_and_reports_its_items(judges):
    submitted = asyncio.Event()

    async def cancel_point(job, msg):
        if msg["type"] == "submitted":
            submitted.set()
            await asyncio.Future()

    task = asyncio.create_task(judges.run(judges.job(), on_message=cancel_point))
    try:
        await asyncio.wait_for(submitted.wait(), 10)
    finally:
        task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert judges.events("settle")[0]["outcome"] == "worker_lost"
    assert judges.frames("item_not_run")[0]["reason"] == "worker_lost"


def _results_stub(rows):
    return (
        RESERVE
        + "send({'type':'submitted','unit_id':unit,'provider_batch_id':'b1'})\n"
        + "req({'type':'batch_results','unit_id':unit,'results':"
        + repr(rows)
        + "})\n"
    )


@pytest.mark.parametrize(
    "rows",
    [
        [asdict(missing("other"))],
        [asdict(missing("i1")), asdict(missing("i1"))],
        [{**asdict(missing("i1")), "attempt": True}],
        [{**asdict(missing("i1")), "judge_id": "other", "target": "reference"}],
        [{**asdict(missing("i1")), "status": "succeeded", "buckets": {"uncached_input": -1}}],
    ],
)
async def test_only_the_assigned_item_and_take_reach_the_collector(judges, rows):
    with pytest.raises(BenchmarkValidationError):
        await judges.run(judges.job(), launcher=StubLauncher(_results_stub(rows)))
    assert not judges.results()
    assert judges.events("settle")[0]["outcome"] == "worker_lost"


async def test_receiver_enforces_chunk_limit_even_for_a_short_item_count(judges, monkeypatch):
    monkeypatch.setattr("benchmarks.agentic.host.RESULT_CHUNK_BYTES", 512)
    rows = [{**asdict(missing("i1")), "text": "x" * 600}]
    with pytest.raises(FrameTooLarge):
        await judges.run(judges.job(), launcher=StubLauncher(_results_stub(rows)))
    assert not judges.results()


async def test_openai_server_error_line_cannot_claim_a_free_booking(judges):
    rows = [
        {
            **asdict(missing("i1")),
            "status": "errored",
            "unbilled": True,
            "raw": {"response": {"status_code": 500}},
        }
    ]
    with pytest.raises(BenchmarkValidationError):
        await judges.run(judges.job(judge="luna"), launcher=StubLauncher(_results_stub(rows)))
    assert not judges.results()


async def test_terminal_collector_failure_keeps_settlement_and_lists_all_remaining_items(judges):
    async def fail(job, msg):
        if msg["type"] == "item_terminal":
            raise ValueError("failed to persist terminal")

    with pytest.raises(ValueError, match="failed to persist"):
        await judges.run(
            judges.job(count=2, script={"submit_errors": [FREE, PAID, FREE]}), on_message=fail
        )
    assert len(judges.events("settle")) == 3
    assert [m["item_id"] for m in judges.frames("item_not_run")] == ["i1", "i2"]


async def test_all_scripted_panel_roles_can_run_as_batch_jobs(judges):
    jobs = [judges.job(judge=name) for name in judges.panel.judges]
    await judges.run(*jobs)
    assert len(judges.results()) == len(jobs)
    assert {r["status"] for r in judges.results()} == {"succeeded"}
    assert not judges.frames("worker_error")


def _provider_success(judge):
    if judge == "haiku":
        return anthropic_result(
            {
                "custom_id": "i1",
                "result": {
                    "type": "succeeded",
                    "message": {
                        "content": [{"type": "text", "text": "ok"}],
                        "usage": {"input_tokens": 100, "output_tokens": 2},
                    },
                },
            }
        )
    if judge == "luna":
        return openai_result(
            {
                "custom_id": "i1",
                "response": {
                    "status_code": 200,
                    "body": {
                        "status": "completed",
                        "usage": {
                            "input_tokens": 100,
                            "output_tokens": 2,
                            "input_tokens_details": {"cached_tokens": 10},
                        },
                    },
                },
            }
        )
    return gemini_result(
        {
            "key": "i1",
            "response": {
                "usageMetadata": {
                    "promptTokenCount": 100,
                    "candidatesTokenCount": 2,
                    "thoughtsTokenCount": 5,
                }
            },
        }
    )


def _settle_stub(state, n=1):
    return (
        "req({'type':'settle_batch','unit_id':unit,"
        f"'result_count':{n},'batch_state':{state!r}}})\n"
    )


@pytest.mark.parametrize(
    "judge,state",
    [("haiku", "ended"), ("luna", "completed"), ("flash-lite", "JOB_STATE_SUCCEEDED")],
)
@pytest.mark.parametrize("dropped", [False, True])
async def test_provider_usage_survives_raw_drop_and_reaches_the_settled_collector(
    judges, judge, state, dropped
):
    row = asdict(_provider_success(judge))
    if dropped:
        row.update(raw=None, raw_dropped=True)

    async def collector(job, msg):
        if msg["type"] == "batch_results":
            assert judges.events("settle")[-1]["unit_id"] == msg["unit_id"]
            assert msg["provider_batch_id"] == "b1"

    await judges.run(
        judges.job(judge=judge),
        launcher=StubLauncher(_results_stub([row]) + _settle_stub(state)),
        on_message=collector,
    )
    assert judges.results()[0]["buckets"] == row["buckets"]
    item = judges.events("settle")[0]["items"][0]
    assert item["status"] == "ok"
    assert D(item["booked"]) == D(
        {"haiku": ".000060", "luna": ".0000555", "flash-lite": ".000085"}[judge]
    )


@pytest.mark.parametrize(
    "judge,change",
    [
        ("haiku", {"buckets": asdict(Buckets(1, 0, 0, 0))}),
        ("luna", {"raw_usage": None}),
        ("flash-lite", {"buckets": None}),
        (
            "haiku",
            {
                "raw_usage": {"input_tokens": True, "output_tokens": 2},
                "buckets": asdict(Buckets(1, 0, 0, 2)),
                "raw": None,
                "raw_dropped": True,
            },
        ),
        (
            "luna",
            {
                "raw_usage": {"input_tokens": 90, "output_tokens": 2},
                "buckets": asdict(Buckets(90, 0, 0, 2)),
            },
        ),
        ("flash-lite", {"text": {}}),
        ("luna", {"refusal": "false"}),
        ("haiku", {"reported_model": True}),
        ("luna", {"stop_reason": []}),
        ("haiku", {"raw": "invalid"}),
        ("luna", {"raw_dropped": "yes"}),
    ],
)
async def test_malformed_success_fields_never_reach_a_booking_or_vote(judges, judge, change):
    row = {**asdict(_provider_success(judge)), **change}
    with pytest.raises(BenchmarkValidationError):
        await judges.run(judges.job(judge=judge), launcher=StubLauncher(_results_stub([row])))
    assert not judges.results()
    assert judges.events("settle")[0]["outcome"] == "worker_lost"


@pytest.mark.parametrize(
    "judge,row,state",
    [
        ("luna", {**asdict(openai_failed_result("i1")), "retryable": True}, "completed"),
        ("haiku", {**asdict(openai_failed_result("i1")), "retryable": True}, "ended"),
        (
            "haiku",
            {
                **asdict(openai_failed_result("i1")),
                "retryable": True,
                "raw": {"custom_id": "i1", "result": {"type": "unknown"}},
            },
            "ended",
        ),
        ("luna", {**asdict(openai_failed_result("i1")), "refusal": "false"}, "failed"),
        (
            "luna",
            {**asdict(openai_failed_result("i1")), "stop_reason": "max_output_tokens"},
            "failed",
        ),
        ("luna", asdict(_provider_success("luna")), "failed"),
        ("luna", asdict(missing("i1")), "failed"),
        ("luna", asdict(_provider_success("luna")), "in_progress"),
        ("luna", asdict(openai_failed_result("i1")), "completed"),
    ],
)
async def test_free_and_failed_rows_need_consistent_evidence(judges, judge, row, state):
    with pytest.raises(BenchmarkValidationError):
        await judges.run(
            judges.job(judge=judge),
            launcher=StubLauncher(_results_stub([row]) + _settle_stub(state)),
        )
    assert not judges.results()
    assert judges.events("settle")[0]["outcome"] == "worker_lost"


async def test_worker_loss_after_row_ack_keeps_item_gradable(judges):
    target = dict(target="answers", judge_id="haiku", effort="low", pass_number=1, item_id="i1")

    async def collector(job, msg):
        if msg["type"] == "batch_results":
            for row in msg["results"]:
                result = BatchItemResult(
                    **{k: row[k] for k in BatchItemResult.__dataclass_fields__ if k != "buckets"},
                    buckets=Buckets(**row["buckets"]),
                )
                write_vote_record(
                    judges.tmp,
                    target="answers",
                    judge=judges.panel.judges["haiku"],
                    effort="low",
                    pass_number=1,
                    grading_run_id="r",
                    attempt=row["attempt"],
                    result=result,
                    terminal=row["terminal"],
                    unit_id=msg["unit_id"],
                    provider_batch_id=msg["provider_batch_id"],
                    input_sha256="abc",
                    schema={},
                )

    await judges.run(
        judges.job(),
        launcher=StubLauncher(_results_stub([asdict(_provider_success("haiku"))])),
        on_message=collector,
    )
    assert not has_final_record(judges.tmp, **target)
    assert judges.frames("item_not_run")[0]["reason"] == "worker_lost"
    assert D(judges.events("settle")[0]["booked"]) == 2 * D(judges.events("reserve")[0]["bound"])


async def test_mixed_paid_missing_history_survives_two_free_submits(judges):
    await judges.run(
        judges.job(
            count=2,
            script={
                "rules": [{"match": "^i2$", "status": "missing"}, OK],
                "submit_errors": [None, FREE, FREE],
            },
        )
    )
    assert [(r["item_id"], r["attempt"]) for r in judges.frames("item_terminal")] == [("i2", 3)]
    assert judges.frames("item_not_run")[0]["reason"] == "retries_exhausted"


async def test_intervening_success_resets_whole_job_failures(judges):
    await judges.run(
        judges.job(count=2, script={"submit_errors": [PAID, None, PAID, None]}), margin=".17"
    )
    assert [(r["item_id"], r["attempt"]) for r in judges.results()] == [("i1", 2), ("i2", 2)]
    assert not judges.frames("item_not_run")


async def test_cancellation_retries_only_the_owed_terminal_notification(judges):
    second = asyncio.Event()
    saved = []

    async def collector(job, msg):
        if msg["type"] == "item_terminal":
            if msg["item_id"] == "i2" and not second.is_set():
                second.set()
                await asyncio.Future()
            saved.append((msg["item_id"], msg["attempt"], msg["unit_id"]))

    run = asyncio.create_task(
        judges.run(
            judges.job(count=2, script={"submit_errors": [FREE, PAID, FREE]}), on_message=collector
        )
    )
    try:
        await asyncio.wait_for(second.wait(), 10)
    finally:
        run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run
    unit = judges.events("settle")[-1]["unit_id"]
    assert saved == [("i1", 3, unit), ("i2", 3, unit)]
    assert len(judges.events("settle")) == 3


async def test_interrupted_result_publication_resumes_after_durable_settlement(judges):
    started = asyncio.Event()
    saved = []

    async def collector(job, msg):
        if msg["type"] == "batch_results":
            if not started.is_set():
                started.set()
                await asyncio.Future()
            saved.extend(row["item_id"] for row in msg["results"])

    run = asyncio.create_task(judges.run(judges.job(count=2), on_message=collector))
    try:
        await asyncio.wait_for(started.wait(), 10)
    finally:
        run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run
    assert saved == ["i1", "i2"]
    assert [e["outcome"] for e in judges.events("settle")] == ["batch"]
    assert not judges.frames("item_not_run")


@pytest.mark.parametrize(
    "judge,row,state,free",
    [
        (
            "haiku",
            anthropic_result({"custom_id": "i1", "result": {"type": "canceled"}}),
            "ended",
            True,
        ),
        (
            "luna",
            openai_result({"custom_id": "i1", "response": {"status_code": 429}}),
            "completed",
            True,
        ),
        (
            "luna",
            openai_result({"custom_id": "i1", "error": {"code": "batch_expired"}}),
            "expired",
            True,
        ),
        (
            "luna",
            openai_result({"custom_id": "i1", "response": {"status_code": 500}}),
            "completed",
            False,
        ),
        (
            "flash-lite",
            gemini_result({"key": "i1", "error": {"code": 400}}),
            "JOB_STATE_FAILED",
            False,
        ),
    ],
)
async def test_documented_provider_failures_book_by_retained_evidence(
    judges, judge, row, state, free
):
    await judges.run(
        judges.job(judge=judge),
        launcher=StubLauncher(_results_stub([asdict(row)]) + _settle_stub(state)),
    )
    item = judges.events("settle")[0]["items"][0]
    assert item["status"] == ("unbilled" if free else "no_usage")
    expected = 0 if free else 2 * D(judges.events("reserve")[0]["bound"])
    assert D(item["booked"]) == expected


async def test_scripted_evidence_cannot_hide_a_usage_or_vote_change(judges):
    row = asdict(_provider_success("haiku"))
    row.update(
        raw={"scripted_rule": OK},
        reported_model="scripted-haiku",
        stop_reason="end_turn",
        refusal=True,
    )
    with pytest.raises(BenchmarkValidationError):
        await judges.run(judges.job(), launcher=StubLauncher(_results_stub([row])))
    assert not judges.results()


async def test_staging_limit_precedes_settlement_and_publication(judges, monkeypatch):
    monkeypatch.setattr("benchmarks.agentic.host._MAX_STAGED_BYTES", 1)
    with pytest.raises(BenchmarkValidationError, match="staging limit"):
        await judges.run(judges.job())
    assert not judges.results()
    assert judges.events("settle")[0]["outcome"] == "worker_lost"


async def test_a_late_failed_batch_disagreement_discards_all_earlier_chunks(judges):
    first = _results_stub([asdict(openai_failed_result("i1"))])
    second = asdict(replace(_provider_success("luna"), item_id="i2", raw=None))
    body = first + f"req({{'type':'batch_results','unit_id':unit,'results':[{second!r}]}})\n"
    with pytest.raises(BenchmarkValidationError):
        await judges.run(
            judges.job(judge="luna", count=2),
            launcher=StubLauncher(body + _settle_stub("failed", n=2)),
        )
    assert not judges.results() and not judges.frames("item_terminal")
    assert len(judges.frames("item_not_run")) == 2
    assert judges.events("settle")[0]["outcome"] == "worker_lost"


async def test_worker_cannot_expire_a_completed_paid_openai_response(judges):
    row = asdict(
        openai_result(
            {
                "custom_id": "i1",
                "error": {"code": "batch_expired"},
                "response": _provider_success("luna").raw["response"],
            }
        )
    )
    with pytest.raises(BenchmarkValidationError):
        await judges.run(judges.job(judge="luna"), launcher=StubLauncher(_results_stub([row])))
    assert not judges.results()


async def test_one_unwritable_terminal_does_not_discard_its_sibling(judges):
    saved = []

    async def collector(job, msg):
        if msg["type"] == "item_terminal":
            if msg["item_id"] == "i1":
                raise OSError("failed to save i1")
            saved.append(msg["item_id"])

    with pytest.raises(OSError, match="failed to save i1"):
        await judges.run(
            judges.job(count=2, script={"submit_errors": [FREE, PAID, FREE]}), on_message=collector
        )
    assert saved == ["i2"]
    assert len(judges.events("settle")) == 3


async def test_provisional_write_failure_cannot_settle_results(judges, monkeypatch):
    import tempfile

    temp_file = tempfile.TemporaryFile

    def open_file(**kwargs):
        file = temp_file(**kwargs)
        if not kwargs:  # the result file, not the worker's stderr
            file.flush = Mock(side_effect=OSError("out of staging space"))
        return file

    monkeypatch.setattr(tempfile, "TemporaryFile", open_file)
    with pytest.raises(OSError, match="out of staging space"):
        await judges.run(judges.job())
    assert not judges.results()
    assert judges.events("settle")[0]["outcome"] == "worker_lost"


async def test_host_metadata_can_push_a_valid_row_over_the_chunk_boundary(judges, monkeypatch):
    row = asdict(_provider_success("haiku"))
    row["raw"]["padding"] = "x" * 4096
    limit = len(
        encode_frame(
            {"type": "batch_results", "unit_id": "anthropic-" + "0" * 32, "results": [row]}
        )
    )
    monkeypatch.setattr("benchmarks.agentic.host.RESULT_CHUNK_BYTES", limit)
    await judges.run(
        judges.job(), launcher=StubLauncher(_results_stub([row]) + _settle_stub("ended"))
    )
    (frame,) = judges.frames("batch_results")
    assert len(encode_frame(frame)) <= limit
    assert frame["results"][0]["raw_dropped"] is True
    assert frame["results"][0]["raw_usage"] == row["raw_usage"]


async def test_paid_errored_row_has_per_item_history_too(judges):
    await judges.run(
        judges.job(
            count=2,
            judge="luna",
            script={
                "rules": [{"match": "^i2$", "status": "errored"}, OK],
                "submit_errors": [None, FREE, FREE],
            },
        )
    )
    (terminal,) = judges.frames("item_terminal")
    assert (terminal["item_id"], terminal["attempt"]) == ("i2", 3)


@pytest.mark.parametrize("boundary", ["worker", "host"])
@pytest.mark.parametrize(
    "kind", ["errored", "canceled", "expired", "4xx", "batch_expired", "scripted"]
)
async def test_oversized_free_evidence_survives_worker_and_host_staging(
    judges, monkeypatch, boundary, kind
):
    # A small cap exercises the real wire/staging boundaries without megabyte fixtures.
    if kind == "scripted":
        raw = {"scripted_rule": {"match": ".*", "status": "expired", "padding": "ü" * 4096}}
    elif kind in ("4xx", "batch_expired"):
        raw = {"custom_id": "i1", "error": {"message": "ü" * 4096}}
        if kind == "4xx":
            raw["response"] = {"status_code": 429}
        else:
            raw["error"]["code"] = "batch_expired"
    else:
        raw = {"custom_id": "i1", "result": {"type": kind, "error": {"message": "ü" * 4096}}}
    judge = "luna" if kind in ("4xx", "batch_expired") else "haiku"
    job = judges.job(judge=judge)
    # Generate provider-shaped results in the real worker, with no SDK, key or network.
    extra = f"""
from benchmarks.grading.batch import ScriptedBatchClient, anthropic_result, openai_result
def make(*a, **kw):
    rule = {raw.get("scripted_rule", {"match": ".*"})!r}
    client = ScriptedBatchClient(
        {job.family.provider!r}, {job.family.model_id!r}, {{'rules':[rule]}})
    async def results(batch_id, ids):
        parsers = {{'anthropic':anthropic_result, 'openai':openai_result}}
        normalize = parsers[{job.family.provider!r}]
        return [normalize({raw!r})]
    if {kind != "scripted"!r}:
        client.results = results
    return client
task.make_batch_client = make
"""
    if boundary == "worker":
        limit = 1024
    else:
        if kind == "scripted":
            client = ScriptedBatchClient("anthropic", "unused", {"rules": [raw["scripted_rule"]]})
            result = client._result("i1")
        else:
            result = (openai_result if judge == "luna" else anthropic_result)(raw)
        limit = len(
            encode_frame(
                {
                    "type": "batch_results",
                    "unit_id": "anthropic-" + "0" * 32,
                    "results": [asdict(result)],
                }
            )
        )
    monkeypatch.setattr("benchmarks.agentic.host.RESULT_CHUNK_BYTES", limit)
    extra += f"\ntask.RESULT_CHUNK_BYTES = {limit}\n"
    await judges.run(job, launcher=ScriptLauncher(extra=extra))
    assert len(judges.events("reserve")) == len(judges.events("settle")) == 3
    assert all(e["outcome"] == "batch" and D(e["booked"]) == 0 for e in judges.events("settle"))
    assert judges.ledger.spent(job.family.provider) == 0
    assert [(r["attempt"], r["terminal"]) for r in judges.results()] == [
        (1, False),
        (2, False),
        (3, True),
    ]
    for frame in judges.frames("batch_results"):
        assert len(encode_frame(frame)) <= limit
        (row,) = frame["results"]
        assert row["raw"] is None and row["raw_dropped"] is True
        assert "billing_evidence" in row
        assert "ü" not in json.dumps(row, ensure_ascii=False)
        assert row["raw_usage"] is row["buckets"] is row["reported_model"] is row["text"] is None


@pytest.mark.parametrize(
    "judge,raw",
    [
        ("haiku", {"custom_id": "i1", "result": {"type": "unknown"}}),
        ("haiku", {"custom_id": "other", "result": {"type": "expired"}}),
        (
            "luna",
            {
                "custom_id": "i1",
                "error": {"code": "batch_expired"},
                "response": {"status_code": 200},
            },
        ),
        (
            "luna",
            {
                "custom_id": "i1",
                "error": {"code": "batch_expired"},
                "response": {"status_code": 500},
            },
        ),
        (
            "luna",
            {
                "custom_id": "i1",
                "error": {"code": "batch_expired"},
                "response": {"status_code": "429"},
            },
        ),
        ("luna", {"custom_id": "i1", "response": {"status_code": 500}}),
    ],
)
async def test_oversized_provider_contradictions_cannot_become_free(judges, judge, raw):
    result = (openai_result if judge == "luna" else anthropic_result)(
        {**raw, "padding": "x" * 4096}
    )
    # Even a worker claiming a free row must supply checkable provider facts.
    (rows,) = list(chunk_results([replace(result, item_id="i1", unbilled=True)], limit=1024))
    with pytest.raises(BenchmarkValidationError):
        await judges.run(judges.job(judge=judge), launcher=StubLauncher(_results_stub(rows)))
    assert not judges.results()
    (settle,) = judges.events("settle")
    assert settle["outcome"] == "worker_lost"
    assert D(settle["booked"]) == 2 * D(judges.events("reserve")[0]["bound"])


@pytest.mark.parametrize(
    "change",
    [
        {"status": "canceled"},
        {"item_id": "unassigned"},
        {"attempt": True},
        {"reported_model": "different-model"},
        {
            "raw_usage": {"input_tokens": 1, "output_tokens": 0},
            "buckets": asdict(Buckets(1, 0, 0, 0)),
        },
        {"unbilled": False},
        {"retryable": False},
        {"raw_dropped": False},
        {"raw": {"custom_id": "i1", "result": {"type": "expired"}}},
        {"billing_evidence": None},
        {"billing_evidence": {"unbilled": True}},
        {
            "billing_evidence": {
                "custom_id": "i1",
                "result": {"type": "expired"},
                "padding": "x" * 4096,
            }
        },
        {"billing_evidence": {"custom_id": "i1", "response": {"status_code": 429}}},
    ],
)
async def test_compact_evidence_never_overrides_result_or_assignment_checks(judges, change):
    result = anthropic_result(
        {"custom_id": "i1", "result": {"type": "expired"}, "padding": "x" * 4096}
    )
    (rows,) = list(chunk_results([result], limit=1024))
    rows[0].update(change)
    with pytest.raises(BenchmarkValidationError):
        await judges.run(judges.job(), launcher=StubLauncher(_results_stub(rows)))
    assert not judges.results()
    assert judges.events("settle")[0]["outcome"] == "worker_lost"


@pytest.mark.parametrize("field", ["text", "raw_usage"])
def test_compact_billing_cannot_make_an_oversized_answer_or_usage_fit(field):
    result = _provider_success("haiku")
    value = "ü" * 4096 if field == "text" else {"input_tokens": 1, "padding": "ü" * 4096}
    with pytest.raises(FrameTooLarge):
        list(chunk_results([replace(result, **{field: value})], limit=1024))


def test_normal_sized_provider_and_failed_openai_rows_are_unchanged():
    results = [
        _provider_success("haiku"),
        anthropic_result({"custom_id": "i1", "result": {"type": "expired"}}),
        openai_result({"custom_id": "i1", "response": {"status_code": 429}}),
        openai_failed_result("i1"),
    ]
    assert [r for chunk in chunk_results(results, limit=1024) for r in chunk] == [
        asdict(r) for r in results
    ]


def _failed_response(usage):
    return openai_result(
        {
            "custom_id": "i1",
            "response": {
                "status_code": 200,
                "body": {
                    "status": "failed",
                    "error": {"code": "server_error"},
                    "output": [],
                    "usage": usage,
                },
            },
        }
    )


@pytest.mark.parametrize(
    "usage,booked",
    [
        ({"input_tokens": 100, "output_tokens": 2}, ".000060"),
        ({"input_tokens": 0, "output_tokens": 0}, "0"),
        (None, None),
        ({"input_tokens": 25}, None),
    ],
)
async def test_failed_response_retries_with_its_actual_batch_booking(judges, usage, booked):
    result = _failed_response(usage)
    body = _results_stub([asdict(result)]) + _settle_stub("completed")
    # The same item is released to the next batch before a successful second take.
    body += _results_stub([asdict(_provider_success("luna"))]) + _settle_stub("completed")
    job = judges.job(judge="luna")
    await judges.run(job, launcher=StubLauncher(body))
    assert job.source.paid_no_result("i1") == (booked != "0")
    first, second = judges.results()
    assert (first["status"], first["attempt"], first["terminal"]) == ("errored", 1, False)
    assert first["raw_usage"] == usage and first["raw"] == result.raw
    assert second["status"] == "succeeded" and second["attempt"] == 2
    item = judges.events("settle")[0]["items"][0]
    assert item["status"] == ("ok" if booked is not None else "no_usage")
    assert D(item["booked"]) == (
        D(booked) if booked is not None else 2 * D(judges.events("reserve")[0]["bound"])
    )


@pytest.mark.parametrize(
    "tamper", ["drop", "usage", "buckets", "free", "identity", "vote", "http", "state"]
)
async def test_failed_response_pricing_requires_exact_raw_proof(judges, tamper):
    row = asdict(_failed_response({"input_tokens": 100, "output_tokens": 2}))
    if tamper == "drop":
        row.update(raw=None, raw_dropped=True)
    elif tamper == "usage":
        row["raw"]["response"]["body"]["usage"]["input_tokens"] = 12
    elif tamper == "buckets":
        row["buckets"]["output"] = 1000
    elif tamper == "free":
        row["unbilled"] = True
    elif tamper == "http":
        del row["raw"]["response"]["status_code"]
    elif tamper == "state":
        del row["raw"]["response"]["body"]["status"]
    elif tamper == "identity":
        row["raw"]["custom_id"] = "foreign"
    else:
        row["text"] = "a grade"
    with pytest.raises(BenchmarkValidationError):
        await judges.run(
            judges.job(judge="luna"),
            launcher=StubLauncher(_results_stub([row]) + _settle_stub("completed")),
        )
    assert not judges.results() and judges.events("settle")[0]["outcome"] == "worker_lost"


def test_failed_response_cannot_drop_its_usage_receipt_at_the_frame_limit():
    row = _failed_response({"input_tokens": 100, "output_tokens": 2})
    row.raw["response"]["body"]["error"]["message"] = "x" * 4000
    with pytest.raises(FrameTooLarge, match="receipt"):
        list(chunk_results([row], limit=1024))


async def test_compacting_a_4xx_cannot_hide_known_usage_to_claim_zero(judges):
    raw = {
        "custom_id": "i1",
        "padding": "x" * 4096,
        "response": {
            "status_code": 429,
            "body": {"usage": {"input_tokens": 90, "output_tokens": 1}},
        },
    }
    result = replace(openai_result(raw), unbilled=True, raw_usage=None, buckets=None)
    (rows,) = list(chunk_results([result], limit=1024))
    with pytest.raises(BenchmarkValidationError):
        await judges.run(judges.job(judge="luna"), launcher=StubLauncher(_results_stub(rows)))
    assert not judges.results()
