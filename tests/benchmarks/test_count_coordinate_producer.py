"""R4 producer integration: actual CLI plans and offline recording receipts."""

import asyncio
import hashlib
import json
import shutil
import socket
import subprocess
import sys
from dataclasses import asdict

import httpx2
import pytest

from benchmarks.agentic import count, host
from benchmarks.agentic.clients import anthropic_sdk
from benchmarks.agentic.ledger import Ledger
from benchmarks.report_measurements import live_count_matches
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks.test_count_coordinates import (
    LEGACY_IDS,
    REAL_FAMILIES,
    UNSAFE_IDS,
    recorded_bundle,
)
from tests.benchmarks.test_grading_financial_preflight import WorkerPort, without_signals


def answering(root, family, provider, **kwargs):
    transcript, _ = asyncio.run(recorded_bundle(root, family, provider, **kwargs))
    # Start the producer with answering inputs only. It must create all evidence itself.
    shutil.rmtree(root / "tokens")
    shutil.rmtree(root / "grading")
    return transcript


@pytest.mark.parametrize("family,provider", REAL_FAMILIES)
def test_real_p2_cli_plans_registered_coordinates(tmp_path, family, provider):
    answering(tmp_path, family, provider)
    command = [
        sys.executable,
        "-m",
        "benchmarks",
        "agentic-count-tokens",
        "--run-dir",
        str(tmp_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    print("CLI:", " ".join(command))
    print("exit:", result.returncode, "stdout:", result.stdout, "stderr:", result.stderr)
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["dry_run"] is True
    assert summary["items"] == 1 and summary["count_calls"] == 8
    assert summary["records_written"] == 0 and summary["units"] == []
    assert not (tmp_path / "tokens").exists()


@pytest.mark.parametrize("family,provider", REAL_FAMILIES)
def test_real_p2_recording_producer_joins_p1_receipts(tmp_path, monkeypatch, family, provider):
    transcript = answering(tmp_path, family, provider)

    def forbidden(*args, **kwargs):
        pytest.fail("offline recording accessed network or ledger")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(Ledger, "open", forbidden)
    monkeypatch.setattr(count.live_safety, "authorize_live", lambda *a, **kw: None)
    monkeypatch.setattr("benchmarks.agentic.run._until_signalled", without_signals)
    requests = []

    def record(request):
        assert request.url.path == "/v1/messages/count_tokens"
        requests.append(json.loads(request.content))
        return httpx2.Response(200, json={"input_tokens": 123})

    sdk = anthropic_sdk("test-key", 5, transport=httpx2.MockTransport(record))
    client = count.AnthropicCountClient(sdk, count.DEFAULT_COUNT_MODEL)
    monkeypatch.setattr(count, "make_count_client", lambda *a, **kw: client)

    async def jobs(jobs, *, ledger, on_message, **kwargs):
        assert ledger is None
        for job in jobs:
            port = WorkerPort(host._GradingJob(job, ledger, on_message))
            await count.run_count_task(
                {"family": job.family.raw, "caps": asdict(job.caps)}, port, port
            )
            await port.flush()
            await port.grading.finish()

    monkeypatch.setattr(host, "run_jobs", jobs)

    class RecordingLauncher:
        def supports(self, family):
            return True

    try:
        summary = count.run_counting(tmp_path, live=True, launcher=RecordingLauncher())
        assert summary["status"] == "completed" and summary["units"] == []
        assert summary["count_calls"] == len(requests) == 8
        assert summary["records_written"] == summary["items"] == 1
        cell, qid = transcript["competitor_id"], transcript["corpus_id"]
        path = tmp_path / "tokens" / cell / f"{qid}.json"
        token = json.loads(path.read_bytes())
        assert live_count_matches(tmp_path, transcript, token)
        archive = tmp_path / "grading/counts" / summary["count_run_id"] / cell / f"{qid}.json"
        assert archive.read_bytes() == path.read_bytes()
        assert (
            summary["results"][f"{cell}/{qid}.json"]
            == hashlib.sha256(path.read_bytes()).hexdigest()
        )
        assert count.run_counting(tmp_path, live=False)["records_written"] == 0
        assert len(requests) == 8
        receipt_path = tmp_path / "grading/count-runs" / f"{summary['count_run_id']}.json"
        receipt = json.loads(receipt_path.read_bytes())
        receipt["results"] = {f"{cell}//{qid}.json": hashlib.sha256(path.read_bytes()).hexdigest()}
        receipt_path.write_text(json.dumps(receipt))
        with pytest.raises(BenchmarkValidationError, match="receipt coordinate"):
            count.run_counting(tmp_path, live=False)
    finally:
        asyncio.run(sdk.close())


@pytest.mark.parametrize("legacy", LEGACY_IDS)
def test_real_p2_preserves_legacy_coordinate_inputs(tmp_path, legacy):
    answering(tmp_path, legacy, "openai", system=legacy, qid=legacy)
    summary = count.run_counting(tmp_path, live=False)
    assert summary["dry_run"] and summary["items"] == 1 and summary["count_calls"] == 8


@pytest.mark.parametrize("run", ["run.1", ".hidden", "../escape", "/absolute", "a" * 241])
def test_p2_count_run_id_keeps_strict_grammar(tmp_path, run):
    with pytest.raises(BenchmarkValidationError, match="invalid count coordinate"):
        count.run_counting(tmp_path, live=False, count_run_id=run)


@pytest.mark.parametrize("unsafe", UNSAFE_IDS)
def test_p2_coordinate_wrapper_rejects_unsafe_values(unsafe):
    with pytest.raises(BenchmarkValidationError, match="invalid count coordinate"):
        count._coordinate(unsafe)


def test_p2_dotted_system_coordinate(tmp_path):
    answering(tmp_path, *REAL_FAMILIES[0], system="python.docs")
    summary = count.run_counting(tmp_path, live=False)
    assert summary["dry_run"] and summary["items"] == 1 and summary["count_calls"] == 8
