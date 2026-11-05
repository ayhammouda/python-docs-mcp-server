"""Local production stage outputs joined by the shared P1 evidence consumer.

SDK HTTP is recorded in process; these unsigned fixtures establish consistency,
never hosted execution or real spending. No generated evidence is repaired.
"""

import hashlib
import json
import socket
from dataclasses import asdict, replace
from datetime import date
from decimal import Decimal

import httpx
import httpx2
import pytest
import yaml

from benchmarks.agentic import count, host
from benchmarks.agentic.clients import anthropic_sdk
from benchmarks.agentic.ledger import Ledger
from benchmarks.agentic.run import run_agentic
from benchmarks.grading import batch, run, worker_task
from benchmarks.grading.panel import PANEL_PATH, load_panel
from benchmarks.report_measurements import grading_receipt_matches, live_count_matches
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks import test_grading_run as grading_fixtures
from tests.benchmarks import test_grading_sdk_preflight as sdk_fixtures
from tests.benchmarks.grading_pilot_fixture import transports
from tests.benchmarks.test_agentic_run import COMMITTED_PRICES, _isolated_index
from tests.benchmarks.test_agentic_run import _config as answering_config
from tests.benchmarks.test_grading_batch import _anthropic_ok, _gemini_response, _openai_body, _text
from tests.benchmarks.test_grading_financial_preflight import WorkerPort, without_signals
from tests.benchmarks.test_grading_run import (
    config,
    digest,
    read,
)
from tests.benchmarks.test_grading_score import _vote_text

scripted_run = grading_fixtures.scripted_run
sdk_pilot = sdk_fixtures.sdk_pilot


def events(root):
    body = (root / "grading/ledger-events.jsonl").read_bytes()
    return [json.loads(line) for line in body.splitlines()]


def matches(root, path):
    vote = read(path)
    try:
        invocation = read(root / "grading/runs" / f"{vote['grading_run_id']}.json")
        panel = yaml.safe_load((root / "grading/inputs/panel.yml").read_text())
        judge = panel["judges"][vote["judge_id"]]
        return grading_receipt_matches(root, vote, digest(path), judge, invocation, events(root))
    except (FileNotFoundError, KeyError):
        # The P1 report caller treats missing artifacts/ledger joins as unknown.
        return False


@pytest.fixture
def producer(scripted_run, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("external access attempted")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(run, "_until_signalled", without_signals)
    async def grading_jobs(jobs, *, ledger, on_message, **kwargs):
        for job in jobs:
            port = WorkerPort(host._GradingJob(job, ledger, on_message))
            try:
                await worker_task.run_batch_task(
                    {"family": job.family.raw, "payload": job.payload, "caps": asdict(job.caps)},
                    port, port,
                )
                await port.flush()
            finally:
                await port.grading.finish()

    monkeypatch.setattr(run, "run_jobs", grading_jobs)
    monkeypatch.setattr(run.live, "authorize_live", lambda *a, **kw: None)
    monkeypatch.setattr(batch, "live_key", lambda family: (family.provider, "test-key"))
    panel = yaml.safe_load(PANEL_PATH.read_text())
    for judge in panel["judges"].values():
        if judge["effort"] is None and judge["effort_levels"]:
            judge["effort"] = judge["effort_levels"][0]
    panel_path = scripted_run.parent / "recording-panel.yml"
    panel_path.write_text(yaml.safe_dump(panel))

    def setup(variant="normal", judge="haiku"):
        cfg = config(
            scripted_run.parent,
            panel_path=panel_path,
            prices_path=COMMITTED_PRICES,
            grading_run_id="producer",
            live=True,
            confirm_max_spend=Decimal("1"),
        )
        ids = sorted({i.item_id for i in run.build_items(cfg, load_panel(panel_path))})
        routers = transports(ids[0])
        for provider, current in routers.items():
            lines = []
            for item_id in ids:
                if provider == "openai":
                    raw = _openai_body(item_id)
                    raw["response"]["body"]["output"][0]["content"][0]["text"] = _vote_text()
                    if variant == "paid-failure":
                        raw["response"]["body"].update(status="failed", output=[])
                elif provider == "google":
                    raw = _gemini_response(item_id, text=_vote_text())
                elif variant == "compact-free":
                    raw = {
                        "custom_id": item_id,
                        "result": {"type": "expired", "padding": "x" * 20000},
                    }
                else:
                    raw = _anthropic_ok(item_id, _vote_text())
                    if variant == "raw-dropped":
                        raw["padding"] = "x" * 20000
                lines.append(json.dumps(raw))
            method, pattern, _ = current.routes[-1]
            current.routes[-1] = (
                method, pattern,
                _text(httpx if provider == "google" else httpx2, "\n".join(lines) + "\n"),
            )
        router = routers["openai" if judge == "luna" else "anthropic"]
        if variant in ("raw-dropped", "compact-free"):
            monkeypatch.setattr(host, "RESULT_CHUNK_BYTES", 8000)
            monkeypatch.setattr(worker_task, "RESULT_CHUNK_BYTES", 8000)

        def client(family, **kwargs):
            http = httpx if family.provider == "google" else httpx2
            return batch.make_batch_client(
                family, **kwargs, transport=http.MockTransport(routers[family.provider])
            )

        monkeypatch.setattr(worker_task, "make_batch_client", client)
        return cfg, router

    return scripted_run, setup


def invoke(cfg):
    return run.run_grading(cfg, today=date(2026, 11, 4))


def test_all_recorded_sdk_votes_join_durable_execution_receipts(sdk_pilot):
    root, _ = sdk_pilot
    reference = run.run_grading(config(
        root.parent,
        target="reference",
        panel_path=root.parent / "frozen-panel.yml",
        prices_path=COMMITTED_PRICES,
        grading_run_id="reference-grades",
        live=True,
        confirm_max_spend=Decimal("1"),
    ))
    assert reference["records_written"] == 1
    votes = list((root / "grading/votes/answers").rglob("*.json"))
    assert len(votes) == 3
    votes += list((root / "grading/votes/reference").rglob("*.json"))
    assert len(votes) == 4
    for path in votes:
        assert matches(root, path)
        vote = read(path)
        history = read(
            root / "grading/executions" / vote["grading_run_id"] / vote["judge_id"]
            / f"{vote['item_id']}--a{vote['attempt']}.json"
        )
        assert history["vote_sha256"] == digest(path)
        reservation = next(e for e in events(root) if e["event"] == "reserve"
                           and e["unit_id"] == vote["unit_id"])
        assert reservation["job_id"] == f"{vote['judge_id']}--grading"
        assert reservation["run_id"] == vote["grading_run_id"]
        assert history["provider_batch_id"] == vote["provider_batch_id"]
        # The consumer must use the captured bytes, not a reserialized vote.
        path.write_bytes(path.read_bytes() + b" ")
        assert not matches(root, path)


@pytest.mark.parametrize("variant,judge", [
    ("raw-dropped", "haiku"), ("compact-free", "haiku"), ("paid-failure", "luna"),
])
def test_non_public_results_keep_correct_settlement_and_retry_behavior(producer, variant, judge):
    root, setup = producer
    cfg, router = setup(variant, judge)
    summary = invoke(cfg)
    assert summary["status"] == "completed"
    rows = [read(p) for p in (root / "grading/executions/producer" / judge).glob("*.json")]
    provider = "openai" if judge == "luna" else "anthropic"
    settlements = [e for e in events(root) if e["event"] == "settle" and e["provider"] == provider]
    assert len(rows) == (2 if variant == "raw-dropped" else 6)
    assert summary["records_written"] == len(rows) + 4
    assert len(settlements) == (1 if variant == "raw-dropped" else 3)
    assert all(Decimal(e["booked"]) == 0 for e in settlements) == (variant == "compact-free")
    assert all(e["outcome"] == "batch" for e in settlements)
    assert summary["spent_after"]["openai" if judge == "luna" else "anthropic"] == str(
        sum((Decimal(e["booked"]) for e in settlements), Decimal(0))
    )
    votes = list((root / "grading/votes/answers" / judge).rglob("*.json"))
    assert all(not matches(root, p) for p in votes)
    assert all(row["result"]["raw"] is None for row in rows) == (variant != "paid-failure")
    if variant != "raw-dropped":
        assert sum(read(p)["terminal"] for p in votes) == 2
        assert all(read(p)["withheld"] in ("expired", "errored") for p in votes)
    before = {p: p.read_bytes() for p in cfg.ledger_dir.glob("*") if p.is_file()}
    requests = len(router.seen)
    retried = invoke(replace(cfg, grading_run_id="retry"))
    assert retried["records_written"] == 0 and len(router.seen) == requests
    # A new invocation may update SQLite metadata, but adds no charged event.
    assert all(p.read_bytes() == data for p, data in before.items() if p.suffix == ".jsonl")


@pytest.mark.parametrize(
    "where", ["execution", "partial-execution", "journal", "partial-journal", "run-record"]
)
def test_late_storage_failure_cannot_claim_live_or_spend_again(producer, monkeypatch, where):
    root, setup = producer
    cfg, router = setup()
    immutable = run._immutable
    from pathlib import Path

    opening = Path.open
    writing = Path.write_bytes

    def partial_execution(path, body):
        if where == "partial-execution" and "executions" in path.parts:
            writing(path, body[:8])
            raise OSError("synthetic interrupted staged write")
        return writing(path, body)

    def fail_write(path, value):
        if (where == "execution" and "executions" in path.parts
                or where == "run-record" and path.parent.name == "runs"):
            raise OSError("synthetic full storage")
        return immutable(path, value)

    def fail_journal(path, *args, **kwargs):
        if where == "journal" and path == root / "grading/ledger-events.jsonl":
            raise OSError("synthetic full storage")
        if where == "partial-journal" and path == root / "grading/ledger-events.jsonl":
            class PartialJournal:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def write(self, body):
                    # Complete first unit, then storage fails: its own receipt must
                    # still fail the whole-invocation unit/spend reconciliation.
                    with opening(path, "ab") as file:
                        file.write(b"\n".join(body.splitlines()[:2]) + b"\n")
                    raise OSError("synthetic partial journal write")

            return PartialJournal()
        return opening(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(run, "_immutable", fail_write)
        patch.setattr(Path, "open", fail_journal)
        patch.setattr(Path, "write_bytes", partial_execution)
        with pytest.raises(BenchmarkValidationError):
            invoke(cfg)
    ledger = Ledger.open(cfg.ledger_dir, run_id="inspection")
    try:
        booked = ledger.spent("anthropic")
    finally:
        ledger.close()
    assert booked > 0
    votes = list((root / "grading/votes/answers").rglob("*.json"))
    assert votes
    if where in ("execution", "partial-execution"):
        assert read(root / "grading/runs/producer.json")["status"] == "aborted"
        assert not list((root / "grading/executions").rglob("*.json"))
    if where == "journal":
        assert not (root / "grading/ledger-events.jsonl").exists()
    if where == "partial-journal":
        assert all(not matches(root, path) for path in votes)
    if where == "run-record":
        assert not (root / "grading/runs/producer.json").exists()
    assert all(not matches(root, path) for path in votes)
    requests = len(router.seen)
    # Reusing an interrupted invocation is refused before any provider activity.
    with pytest.raises(BenchmarkValidationError, match="already used"):
        invoke(cfg)
    assert len(router.seen) == requests
    # Every successfully published vote remains final even if its receipt failed.
    # A new invocation must not buy those already-settled items again.
    retry = invoke(replace(cfg, grading_run_id="retry"))
    assert sum(j["skipped"] for j in retry["judges"].values()) == len(votes)
    assert len(router.seen) == requests
    ledger = Ledger.open(cfg.ledger_dir, run_id="inspection-after")
    try:
        assert ledger.spent("anthropic") == booked
    finally:
        ledger.close()


@pytest.mark.parametrize(
    "system,retry_once", [("no-docs", False), ("python-docs", False), ("python-docs", True)]
)
def test_normal_count_stage_outputs_join_per_call_inputs_and_receipts(
    producer, monkeypatch, tmp_path, system, retry_once
):
    root, _ = producer
    if system == "python-docs":
        _isolated_index(tmp_path, monkeypatch)
        run_agentic(answering_config(
            tmp_path,
            out_dir=tmp_path / "tool-run",
            systems=(system,), models=("scripted-anthropic",), repeats=1,
        ))
        root = tmp_path / "tool-run"
    requests = []
    fail_endpoint = False

    def record(request):
        assert request.url.path == "/v1/messages/count_tokens"
        requests.append(json.loads(request.content))
        if retry_once and len(requests) == 1:
            return httpx2.Response(
                429, headers={"retry-after": "0"},
                json={"error": {"type": "rate_limit_error", "message": "recorded retry"}},
            )
        if fail_endpoint:
            return httpx2.Response(400, json={"error": {"message": "synthetic rejection"}})
        return httpx2.Response(200, json={"input_tokens": 123})

    client = count.AnthropicCountClient(
        anthropic_sdk("test-key", 5, transport=httpx2.MockTransport(record)),
        count.DEFAULT_COUNT_MODEL,
    )
    monkeypatch.setattr(count, "make_count_client", lambda *a, **kw: client)
    monkeypatch.setattr("benchmarks.agentic.run._until_signalled", without_signals)

    async def counting_jobs(jobs, *, ledger, on_message, **kwargs):
        assert ledger is None
        for job in jobs:
            port = WorkerPort(host._GradingJob(job, ledger, on_message))
            await count.run_count_task(
                {"family": job.family.raw, "caps": asdict(job.caps)}, port, port
            )
            await port.flush()
            await port.grading.finish()

    monkeypatch.setattr(host, "run_jobs", counting_jobs)
    monkeypatch.setattr(Ledger, "open", lambda *a, **kw: pytest.fail("free counts opened ledger"))

    class RecordingLauncher:
        def supports(self, family):
            return True

    summary = count.run_counting(root, live=True, launcher=RecordingLauncher())
    assert summary["status"] == "completed" and summary["units"] == []
    _, items = count._count_inputs(root)
    planned = sum(count.planned_count_calls(item) for item in items.values())
    assert len(requests) == summary["count_calls"] == planned + int(retry_once)
    if system == "python-docs":
        uses = [
            block for request in requests for message in request["messages"]
            if isinstance(message["content"], list) for block in message["content"]
            if block.get("type") == "tool_use"
        ]
        assert uses and {block["id"] for block in uses} == {"c1_1"}
    for path in (root / "tokens").glob("*/*.json"):
        token = read(path)
        transcript = read(root / "transcripts" / path.parent.name / path.name)
        assert token["per_call"] == [
            {"index": i, "input_tokens": 123, "output_tokens": 0}
            for i in range(1 if system == "no-docs" else 2)
        ]
        assert token["input_sha256"] and token["count_run_id"] == summary["count_run_id"]
        assert live_count_matches(root, transcript, token)
        archive = root / "grading/counts" / summary["count_run_id"] / path.parent.name / path.name
        assert archive.read_bytes() == path.read_bytes()
        assert summary["results"][f"{path.parent.name}/{path.name}"] == hashlib.sha256(
            archive.read_bytes()
        ).hexdigest()

    saved = {p: p.read_bytes() for p in (root / "tokens").glob("*/*.json")}
    fail_endpoint = True
    failed = count.run_counting(
        root, live=True, launcher=RecordingLauncher(), only_missing=False,
        count_run_id="failed-recount",
    )
    assert failed["records_written"] == 2 and failed["units"] == []
    assert all(p.read_bytes() == body for p, body in saved.items())
    for path in (root / "grading/counts/failed-recount").glob("*/*.json"):
        token = read(path)
        assert token["status"] == "failed" and token["per_call"] == []
        transcript = read(root / "transcripts" / path.parent.name / path.name)
        assert not live_count_matches(root, transcript, token)

    fail_endpoint = False
    publish = count._publish

    def fail_projection(path, *args, **kwargs):
        if root / "tokens" in path.parents:
            raise OSError("synthetic projection write failure")
        return publish(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(count, "_publish", fail_projection)
        with pytest.raises(BenchmarkValidationError):
            count.run_counting(
                root, live=True, launcher=RecordingLauncher(), only_missing=False,
                count_run_id="interrupted-recount",
            )
    receipt = read(root / "grading/count-runs/interrupted-recount.json")
    assert receipt["status"] == "aborted" and receipt["records_written"] == 1
    archived = next((root / "grading/counts/interrupted-recount").glob("*/*.json"))
    transcript = read(root / "transcripts" / archived.parent.name / archived.name)
    assert not live_count_matches(root, transcript, read(archived))
    assert all(p.read_bytes() == body for p, body in saved.items())
    for path in saved:
        transcript = read(root / "transcripts" / path.parent.name / path.name)
        assert live_count_matches(root, transcript, read(path))
    calls = len(requests)
    assert count.run_counting(root, live=True, launcher=RecordingLauncher())["records_written"] == 0
    assert len(requests) == calls
