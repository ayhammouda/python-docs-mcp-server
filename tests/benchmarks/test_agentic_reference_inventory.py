"""Bind exact published pilot bytes, including zero-dollar local timing, to the host."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from benchmarks.agentic import clients, worker
from benchmarks.agentic.live import reference_rates
from benchmarks.agentic.run import run_agentic
from benchmarks.agentic.types import Buckets
from benchmarks.runner import BenchmarkValidationError
from benchmarks.scoring import score_run
from tests.benchmarks import test_agentic_clients as adapters
from tests.benchmarks.test_agentic_run import CORPUS_SCHEMA, SAMPLE_CORPUS, _container, _live_config


def _pilot(root, monkeypatch, *, family, seconds=2, discard=False, failed=False, run_id="reused"):
    """Real orchestration/worker/collector/ledger, synthetic HTTP and a local clock."""
    root.mkdir(parents=True)
    clock = [1000.0]
    count = 0

    def respond(request):
        nonlocal count
        count += 1
        clock[0] += seconds if count == 1 else 2
        if discard and count == 1:
            return adapters._ok(adapters.ERROR_BODY, 429, {"retry-after": "121"})
        if failed:
            return adapters._ok(adapters.ERROR_BODY, 400)
        if family == "gemma4-12b":
            return adapters._ok(
                adapters._chat_completion({"role": "assistant", "content": "answer"})
            )
        return adapters._ok(adapters._openai_response([adapters.OPENAI_MESSAGE]))

    transport = adapters.Recorder(*([respond] * (1 + int(discard))))
    client = adapters._ollama(transport) if family == "gemma4-12b" else adapters._openai(transport)

    async def run_jobs(jobs, *, ledger, on_message, **kwargs):
        for job in jobs:

            async def request(self, message):
                if message["type"] == "reserve":
                    unit = await ledger.reserve(
                        **{k: v for k, v in message.items() if k != "type"}, job_id=job.job_id
                    )
                    return {"type": "grant", "unit_id": unit}
                if message["type"] == "settle":
                    await ledger.settle(
                        message["unit_id"],
                        outcome=message["outcome"],
                        buckets=Buckets(**message["buckets"]) if message["buckets"] else None,
                    )
                else:
                    await on_message(job, message)
                return {"type": "ack"}

            with monkeypatch.context() as patch:
                patch.setattr(worker._HostLedger, "request", request)
                patch.setattr(clients, "make_client", lambda *_a, **_kw: client)
                patch.setattr(clients, "fetch_model_limits", AsyncMock(return_value=None))
                patch.setattr(
                    "benchmarks.agentic.loop.time", SimpleNamespace(perf_counter=lambda: clock[0])
                )
                await worker._run_job(
                    Mock(),
                    asyncio.StreamReader(),
                    bootstrapped=True,
                    first={
                        "type": "job",
                        "family": job.family.raw,
                        "system": job.system.raw,
                        "caps": asdict(job.caps),
                        "trajectories": list(job.trajectories),
                    },
                )

    launcher, docker = _container(root)
    with monkeypatch.context() as patch:
        patch.delenv("CI", raising=False)
        patch.setattr("benchmarks.agentic.run.run_jobs", run_jobs)
        summary = run_agentic(
            _live_config(root, models=(family,), repeats=1, run_id=run_id),
            launcher=launcher,
            today=date(2026, 11, 4),
            confirm=lambda _: True,
            offline_checks=lambda: [],
            preflight=lambda _: {},
        )
    assert summary["status"] == "completed"
    assert summary["scored_cells"] == summary["planned_cells"] == 1
    assert summary["failed_cells"] == int(failed)
    assert len(summary["discarded"]) == int(discard)
    assert count == 1 + int(discard) and docker.calls == []
    return root / "run"


def _gate(root, pilot, monkeypatch, family, error):
    launcher, docker = _container(root)
    shown = []
    monkeypatch.delenv("CI", raising=False)
    with pytest.raises(BenchmarkValidationError, match=error):
        run_agentic(
            _live_config(root, models=(family,), repeats=300, reference_run=pilot),
            launcher=launcher,
            today=date(2026, 11, 4),
            confirm=lambda text: bool(shown.append(text)),
        )
    assert docker.calls == []
    return shown


@pytest.mark.parametrize("family", ["gemma4-12b", "gpt-6.1-sol"])
@pytest.mark.parametrize("kind", ["transcripts", "discarded"])
@pytest.mark.parametrize("reuse_id", [False, True])
def test_partial_restore_from_another_real_pilot_refuses_before_authorization(
    tmp_path, monkeypatch, family, kind, reuse_id
):
    slow = _pilot(
        tmp_path / "slow", monkeypatch, family=family, seconds=300, discard=kind == "discarded"
    )
    fast = _pilot(
        tmp_path / "fast",
        monkeypatch,
        family=family,
        discard=kind == "discarded",
        run_id="reused" if reuse_id else "other",
    )
    slow_rate, fast_rate = reference_rates(slow)[family], reference_rates(fast)[family]
    assert slow_rate.seconds_per_cell == 300 + (2 if kind == "discarded" else 0)
    assert fast_rate.seconds_per_cell == 2 + (2 if kind == "discarded" else 0)
    if family == "gemma4-12b":
        assert slow_rate.usd_per_cell == fast_rate.usd_per_cell == Decimal(0)
    assert _gate(tmp_path / "slow-gate", slow, monkeypatch, family, "24-hour window") == []
    assert len(_gate(tmp_path / "fast-gate", fast, monkeypatch, family, "not confirmed")) == 1
    mixed = shutil.copytree(slow, tmp_path / "mixed")
    source = next((fast / kind).glob("*/*.json"))
    shutil.copyfile(source, mixed / source.relative_to(fast))
    assert _gate(tmp_path / "mixed-gate", mixed, monkeypatch, family, "reference.*SHA256") == []


@pytest.mark.parametrize("kind", ["transcripts", "discarded"])
def test_metered_timing_edit_preserving_all_units_and_usage_is_refused(tmp_path, monkeypatch, kind):
    family = "gpt-6.1-sol"
    pilot = _pilot(
        tmp_path / "pilot", monkeypatch, family=family, seconds=300, discard=kind == "discarded"
    )
    path = next((pilot / kind).glob("*/*.json"))
    record = json.loads(path.read_bytes())
    record["timing"]["wall_ms"] = 1
    path.write_text(json.dumps(record))
    assert _gate(tmp_path / "gate", pilot, monkeypatch, family, "reference.*SHA256") == []


@pytest.mark.parametrize("failed,discard", [(False, False), (True, False), (False, True)])
def test_producer_retains_exact_published_bytes_and_coordinates(
    tmp_path, monkeypatch, failed, discard
):
    pilot = _pilot(
        tmp_path / "pilot", monkeypatch, family="gemma4-12b", failed=failed, discard=discard
    )
    summary = json.loads((pilot / "run-summary.json").read_bytes())
    inventory = summary["transcript_inventory"]
    paths = {
        p.relative_to(pilot).as_posix()
        for kind in ("transcripts", "discarded")
        for p in (pilot / kind).glob("*/*.json")
    }
    assert {item["path"] for item in inventory} == paths
    assert len(inventory) == 1 + int(discard)
    for item in inventory:
        content = (pilot / item["path"]).read_bytes()
        record = json.loads(content)
        assert item["sha256"] == hashlib.sha256(content).hexdigest()
        for field in (
            "competitor_id",
            "corpus_id",
            "system_id",
            "model_family_id",
            "provider",
            "model_id",
            "repeat",
            "attempt",
        ):
            assert item[field] == record[field]
        assert record["attempt"] == (
            2 if discard and item["path"].startswith("transcripts/") else 1
        )
    assert reference_rates(pilot)["gemma4-12b"].usd_per_cell == 0


@pytest.mark.parametrize(
    "mutation",
    [
        "legacy",
        "empty",
        "duplicate",
        "missing_hash",
        "path",
        "corpus_id",
        "repeat",
        "attempt",
        "model_id",
        "missing_file",
        "extra_file",
        "whitespace",
    ],
)
def test_incomplete_or_unmatched_inventory_refuses_before_authorization(
    tmp_path, monkeypatch, mutation
):
    pilot = _pilot(tmp_path / "pilot", monkeypatch, family="gemma4-12b")
    summary_path = pilot / "run-summary.json"
    summary = json.loads(summary_path.read_bytes())
    inventory = summary["transcript_inventory"]
    if mutation == "legacy":
        del summary["transcript_inventory"]
    elif mutation == "empty":
        summary["transcript_inventory"] = []
    elif mutation == "duplicate":
        inventory.append(inventory[0])
    elif mutation == "missing_hash":
        del inventory[0]["sha256"]
    elif mutation in {"path", "corpus_id", "model_id"}:
        inventory[0][mutation] = "other"
    elif mutation in {"repeat", "attempt"}:
        inventory[0][mutation] = 2
    else:
        path = pilot / inventory[0]["path"]
        if mutation == "missing_file":
            path.unlink()
        elif mutation == "extra_file":
            shutil.copyfile(path, path.with_name("extra.json"))
        else:
            path.write_bytes(path.read_bytes() + b"\n")
    summary_path.write_text(json.dumps(summary))
    assert (
        _gate(tmp_path / "gate", pilot, monkeypatch, "gemma4-12b", "reference.*(inventory|SHA256)")
        == []
    )


def test_intact_prepaid_pilot_survives_copy_and_scoring_regeneration(tmp_path, monkeypatch):
    pilot = _pilot(tmp_path / "pilot", monkeypatch, family="gpt-6.1-sol", discard=True)
    expected = reference_rates(pilot)
    moved = shutil.copytree(pilot, tmp_path / "moved")
    assert (
        score_run(moved, corpus_path=SAMPLE_CORPUS, schema_path=CORPUS_SCHEMA)["total_cells"] == 1
    )
    # These derived artifacts do not change the already-paid transcript evidence.
    token_path = next((moved / "tokens").glob("*/*.json"))
    token_path.write_text(json.dumps({"status": "regenerated"}))
    assert reference_rates(moved) == expected
    assert len(_gate(tmp_path / "gate", moved, monkeypatch, "gpt-6.1-sol", "not confirmed")) == 1
