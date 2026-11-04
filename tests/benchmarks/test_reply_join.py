"""Replay the same archived reply for receipt admission, grades and calibration."""

import hashlib
import json
from collections import Counter
from dataclasses import replace

import pytest

from benchmarks import agentic_report, report_files, report_measurements
from benchmarks.agentic_report import _RunMetadata
from benchmarks.grading import aggregate
from benchmarks.grading.aggregate import write_vote_record
from benchmarks.grading.batch import anthropic_result, gemini_result, openai_result
from benchmarks.grading.inputs import judge_schema
from benchmarks.grading.panel import load_panel
from benchmarks.grading.score import write_g1
from benchmarks.report import _load_run_bundle
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks import test_grading_score as runs
from tests.benchmarks.accepted_report_fixture import raw_result
from tests.benchmarks.test_agentic_report import _json, _readme, _render, _score
from tests.benchmarks.test_grading_refusal_integrity import _receipt
from tests.benchmarks.test_report_grade_evidence import (
    _read,
    _record,
    _vote,
)
from tests.benchmarks.test_report_grade_evidence import (
    recorded_template as recorded_template,
)
from tests.benchmarks.test_report_grade_evidence import (
    recording as recording,
)


def _archive(root, path, panel, *, mixed=True, legacy=True):
    old = _read(path)
    provider = panel.judges[old["judge_id"]].provider
    raw = raw_result(provider, old["item_id"], old["model_id"], old["text"])
    if mixed:
        raw["response"]["body"]["output"][0]["content"].append({
            "type": "refusal", "refusal": "Benign archived refusal.",
        })
    result = {"anthropic": anthropic_result, "google": gemini_result,
              "openai": openai_result}[provider](raw)
    path.unlink()
    path = write_vote_record(
        root, target=old["target"], judge=panel.judges[old["judge_id"]],
        effort=old["effort"], pass_number=old["pass"], grading_run_id=old["grading_run_id"],
        attempt=old["attempt"], result=result, terminal=old["terminal"],
        unit_id=old["unit_id"], provider_batch_id=old["provider_batch_id"],
        input_sha256=old["input_sha256"], schema=judge_schema(),
    )
    if legacy:
        record = _read(path)
        record.pop("refusal")
        _json(path, record)
    _receipt(root, path, result, panel=panel)
    return result


def _rescore(run):
    grading = run / "grading"
    return _score(run, run.parent, panel_path=grading / "inputs/panel.yml",
                  composition=_read(grading / "runs/run-1.json")["composition"])


@pytest.mark.parametrize("pending", [False, True])
def test_report_replays_historical_mixed_refusal_from_one_capture(recording, monkeypatch, pending):
    root = recording / "grading"
    panel = load_panel(root / "inputs/panel.yml")
    path = _vote(recording, "luna")
    result = _archive(root, path, panel)
    assert result.refusal and result.text and result.stop_reason == "completed"
    _rescore(recording)
    assert _read(_record(recording))["grading"]["valid_votes"] == 2
    if pending:
        record = _read(_record(recording))
        record.update(score=None, grading=None, scoring_method=None)
        _json(_record(recording), record)
    original = report_files.artifact_bytes
    seen = Counter()

    def read(base, chosen):
        if "grading" in chosen.parts and any(
            part in chosen.parts for part in ("votes", "executions", "runs", "items")
        ):
            seen[chosen] += 1
            assert seen[chosen] == 1, f"reopened captured reply: {chosen}"
        return original(base, chosen)

    monkeypatch.setattr(report_files, "artifact_bytes", read)
    monkeypatch.setattr(agentic_report, "artifact_bytes", read)
    monkeypatch.setattr(report_measurements, "artifact_bytes", read)
    monkeypatch.setattr(aggregate, "artifact_bytes", read)
    replay = agentic_report.vote_from_record
    mixed = []

    def vote_from_capture(record, *, request, result=None):
        assert result is not None
        if record.get("withheld") == "refusal":
            mixed.append(result)
        return replay(record, request=request, result=result)

    monkeypatch.setattr(agentic_report, "vote_from_record", vote_from_capture)
    run = _RunMetadata(_load_run_bundle(recording))
    key = (_read(path)["item_id"].rsplit("--", 1)[0], "SYN-EX-001")
    assert run.grading == ("unknown" if pending else "live")
    av = run.grade_capture.answers[key]
    assert av.votes["luna"] is None
    assert sum(v is not None for v in av.votes.values()) == 2
    assert bool(run.grade_capture.finals.get(key)) is not pending
    assert result in mixed
    monkeypatch.undo()
    report = _render(recording)
    assert ("grading provenance unknown" in report) is pending


@pytest.mark.parametrize("legacy", [False, True])
def test_renderer_and_scorer_quarantine_forged_refusal(recording, legacy):
    root = recording / "grading"
    panel = load_panel(root / "inputs/panel.yml")
    path = _vote(recording, "luna")
    _archive(root, path, panel, mixed=False)
    _rescore(recording)  # genuine historical success remains eligible
    assert _RunMetadata(_load_run_bundle(recording)).grading == "live"
    assert "grading provenance unknown" not in _render(recording)
    record = _read(path)
    record.update(withheld="refusal", vote=None, refusal=True)
    if legacy:
        record.pop("refusal")
    _json(path, record)
    # Rebind unsigned hashes, leaving the actual successful raw reply intact.
    result = openai_result(raw_result(
        "openai", record["item_id"], record["model_id"], record["text"]
    ))
    _receipt(root, path, result, panel=panel)
    before = {p: p.read_bytes() for p in (recording / "scoring").rglob("*.json")}
    with pytest.raises(BenchmarkValidationError):
        _rescore(recording)
    assert {p: p.read_bytes() for p in before} == before
    assert _RunMetadata(_load_run_bundle(recording)).grading == "unknown"
    assert "grading provenance unknown" in _render(recording)
    assert "fewer normalized tokens" not in _readme(recording)


def test_scorer_binds_the_digest_of_its_first_vote_capture(recording, monkeypatch):
    root = recording / "grading"
    _archive(root, _vote(recording, "luna"), load_panel(root / "inputs/panel.yml"))
    original = aggregate.artifact_bytes
    seen = Counter()

    def read(base, chosen):
        if "votes" in chosen.parts or "executions" in chosen.parts:
            seen[chosen] += 1
            assert seen[chosen] == 1, f"reopened selected scorer reply: {chosen}"
        return original(base, chosen)

    monkeypatch.setattr(aggregate, "artifact_bytes", read)
    _rescore(recording)
    assert _read(_record(recording))["grading"]["valid_votes"] == 2


@pytest.mark.parametrize("missing", [False, True])
def test_ambiguous_history_stays_unknown_in_renderer_and_scorer(recording, missing):
    root = recording / "grading"
    panel = load_panel(root / "inputs/panel.yml")
    path = _vote(recording, "luna")
    result = _archive(root, path, panel)
    _rescore(recording)
    _receipt(root, path, replace(result, raw=None), panel=panel)
    if missing:
        record = _read(path)
        (root / "executions" / record["grading_run_id"] / "luna" /
         f"{record['item_id']}--a1.json").unlink()
    with pytest.raises(BenchmarkValidationError):
        _rescore(recording)
    assert _RunMetadata(_load_run_bundle(recording)).grading == "unknown"
    assert "grading provenance unknown" in _render(recording)


@pytest.mark.parametrize("broken", ["contradiction", "missing", "normalization"])
def test_g1_replays_actual_historical_receipt_and_rejects_corruption(tmp_path, monkeypatch, broken):
    held = runs._workspace(tmp_path / "held", "held-out", {"probes.yml": runs.PROBES})
    _json(held / "runs/run-1.json", {
        "target": "probes", "pass": 1, "composition": runs.COMPOSITION,
    })
    item = "KEY--SYN-EX-001"
    for judge in runs.JUDGES:
        runs._vote(held, judge, item, runs._vote_text(), target="probes")
        if judge != "luna":
            ordinary = held / "votes/probes" / judge / "low/pass1" / item / "run-1--a1.json"
            _archive(held, ordinary, runs.PANEL, mixed=False)
    path = held / "votes/probes/luna/low/pass1" / item / "run-1--a1.json"
    result = _archive(held, path, runs.PANEL)
    original = aggregate.artifact_bytes
    seen = Counter()

    def read(root, chosen):
        if "votes" in chosen.parts or "executions" in chosen.parts:
            seen[chosen] += 1
            assert seen[chosen] == 1, f"reopened chosen calibration reply: {chosen}"
        return original(root, chosen)

    monkeypatch.setattr(aggregate, "artifact_bytes", read)
    export = write_g1(held).read_bytes()
    monkeypatch.undo()
    entry = next(iter(json.loads(export)["entries"].values()))
    assert entry["scores"]["judges"]["luna"][item] is None
    assert entry["scores"]["judges"]["haiku"][item] == 1.0
    # Removing the actual block cannot borrow the old annotation or prior export.
    result.raw["response"]["body"]["output"][0]["content"].pop()
    _receipt(held, path, openai_result(result.raw), panel=runs.PANEL)
    history = held / "executions/run-1/luna" / f"{item}--a1.json"
    if broken == "missing":
        history.unlink()
    elif broken == "normalization":
        receipt = _read(history)
        receipt["result"]["refusal"] = True  # raw line says no refusal
        _json(history, receipt)
        invocation = held / "runs/run-1.json"
        record = _read(invocation)
        record["execution_results"][f"luna/{item}--a1.json"] = hashlib.sha256(
            history.read_bytes()
        ).hexdigest()
        _json(invocation, record)
    with pytest.raises(BenchmarkValidationError):
        write_g1(held)
    assert (held / "g1.json").read_bytes() == export
