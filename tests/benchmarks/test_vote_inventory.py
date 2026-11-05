"""Missing execution-issued records are corruption, not unsubmitted work."""


import hashlib
import json
from collections import Counter
from dataclasses import asdict
from decimal import Decimal

import pytest

from benchmarks.agentic_report import _RunMetadata
from benchmarks.grading.aggregate import write_vote_record
from benchmarks.grading.batch import anthropic_result, gemini_result, openai_result
from benchmarks.grading.inputs import judge_schema
from benchmarks.grading.panel import load_panel
from benchmarks.grading.score import score_run_dir
from benchmarks.grading.vote_capture import VoteCapture
from benchmarks.report import _load_run_bundle
from benchmarks.report_files import artifact_bytes
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks import test_grading_score as runs
from tests.benchmarks.accepted_report_fixture import read, rebuild_grading, write
from tests.benchmarks.test_agentic_report import (
    _readme,
    _render,
)
from tests.benchmarks.test_agentic_report import (
    validated_bundle as validated_bundle,
)
from tests.benchmarks.test_agentic_report import (
    validated_template as validated_template,
)


def _issued_vote(run, target="answers"):
    path = next((run / f"grading/votes/{target}").glob("*/*/pass1/*/*.json"))
    row = read(path)
    invocation = read(run / "grading/runs" / f"{row['grading_run_id']}.json")
    relative = f"{row['judge_id']}/{row['item_id']}--a{row['attempt']}.json"
    assert relative in invocation["execution_results"]
    assert (run / "grading/executions" / row["grading_run_id"] / relative).is_file()
    return path


def test_scorer_rejects_missing_issued_vote_before_any_write(tmp_path):
    run = runs._build_run(tmp_path)
    rebuild_grading(run)  # Owned provider-shaped recordings; no network.
    intact = score_run_dir(run, schema_path=runs.SCHEMA)
    assert intact["spend"]["graded_answers"] == 6
    _issued_vote(run).unlink()
    before = {p.relative_to(run): p.read_bytes() for p in run.rglob("*") if p.is_file()}
    with pytest.raises(BenchmarkValidationError, match="inventory|missing.*vote"):
        score_run_dir(run, schema_path=runs.SCHEMA)
    assert {p.relative_to(run): p.read_bytes() for p in run.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("target", ["answers", "reference"])
def test_report_withholds_denominator_and_affected_gates_for_issued_omission(
    validated_bundle, target,
):
    run = validated_bundle
    intact = _RunMetadata(_load_run_bundle(run))
    assert intact.grading_spend["graded_answers"] == 300
    assert intact.tier == "Validated"
    assert "fewer normalized tokens" in _readme(run)
    _issued_vote(run, target).unlink()
    current = _RunMetadata(_load_run_bundle(run))
    assert current.grading_spend["graded_answers"] is None
    assert current.grading_spend["by_provider"]["google"] == "3.00"
    assert current.grading_spend["per_graded_answer"] == {}
    assert current.summary["gates"]["G1"]["passed"] is True
    assert current.summary["gates"]["G2"]["passed"] is (True if target == "reference" else None)
    assert all(current.summary["gates"][g]["passed"] is None for g in ("G3", "G4"))
    assert not current.grade_capture.finals
    text = _render(run)
    assert "completed LLM-graded answers: 299" not in text
    assert "| google | 3.00 | n/a |" in text
    assert "fewer normalized tokens" not in _readme(run)
    # Neither the scorer nor publication reconstructs the missing evidence.
    assert len(list((run / f"grading/votes/{target}").rglob("*.json"))) > 0


@pytest.fixture
def recorded_small(tmp_path):
    run = runs._build_run(tmp_path)
    rebuild_grading(run)
    return run


@pytest.mark.parametrize("damage", [
    "all_votes", "receipt", "receipt_digest", "vote_digest", "unlisted_vote",
    "target", "pass", "attempt", "receipt_id", "effort", "run_id", "records_written",
])
def test_inventory_reconciles_both_directions(recorded_small, damage):
    run = recorded_small
    path = _issued_vote(run)
    row = read(path)
    invocation_path = run / "grading/runs" / f"{row['grading_run_id']}.json"
    invocation = read(invocation_path)
    relative = f"{row['judge_id']}/{row['item_id']}--a{row['attempt']}.json"
    receipt_path = run / "grading/executions" / row["grading_run_id"] / relative
    if damage == "all_votes":
        for vote in (run / "grading/votes/answers").rglob("*.json"):
            vote.unlink()
    elif damage == "receipt":
        receipt_path.unlink()
    elif damage == "vote_digest":
        row["graded_at"] = "2026-11-04T00:00:00Z"
        write(path, row)
    elif damage == "unlisted_vote":
        invocation["execution_results"].pop(relative)
        invocation["records_written"] -= 1
        write(invocation_path, invocation)
    elif damage in ("target", "pass", "run_id", "records_written"):
        field = "grading_run_id" if damage == "run_id" else damage
        invocation[field] = {"target": "reference", "pass": 2, "run_id": "different",
                             "records_written": 0}[damage]
        write(invocation_path, invocation)
    else:
        receipt = read(receipt_path)
        if damage == "receipt_id":
            invocation["execution_results"]["haiku/different--a1.json"] = (
                invocation["execution_results"].pop(relative)
            )
        else:
            if damage == "attempt":
                receipt["attempt"] = 2
            elif damage == "effort":
                receipt["effort"] = "high"
            else:
                receipt["unit_id"] = "changed"
            body = write(receipt_path, receipt)
            if damage != "receipt_digest":
                invocation["execution_results"][relative] = hashlib.sha256(body).hexdigest()
        write(invocation_path, invocation)
    with pytest.raises(BenchmarkValidationError, match="inventory"):
        VoteCapture(run / "grading").check("answers", 1)


def test_genuinely_unsubmitted_vote_remains_pending(tmp_path):
    run = runs._build_run(tmp_path)
    # The producer never receives this vote; no invocation claims to have issued it.
    path = next((run / "grading/votes/answers/haiku").glob("*/pass1/*/*.json"))
    path.unlink()
    rebuild_grading(run)
    summary = score_run_dir(run, schema_path=runs.SCHEMA)
    assert summary["graded_cells"] == 5
    assert len(summary["pending_cells"]) == 1
    assert summary["spend"]["graded_answers"] == 5


def test_genuinely_unsubmitted_target_has_zero_finals(tmp_path):
    run = runs._build_run(tmp_path)
    for path in (run / "grading/votes/answers").rglob("*.json"):
        path.unlink()
    rebuild_grading(run)
    summary = score_run_dir(run, schema_path=runs.SCHEMA)
    assert summary["graded_cells"] == 0
    assert len(summary["pending_cells"]) == 6
    assert summary["spend"]["graded_answers"] == 0
    assert summary["spend"]["per_graded_answer"] == {}


def test_completed_zero_grades_still_count_as_answers(tmp_path):
    run = runs._build_run(tmp_path, families={runs.FAMILY: {"no-docs": runs.QUESTIONS}})
    rebuild_grading(run)
    summary = score_run_dir(run, schema_path=runs.SCHEMA)
    assert summary["graded_cells"] == 2
    assert summary["spend"]["graded_answers"] == 2
    assert all(read(path)["score"] == 0 for path in (run / "scoring").glob("*/*.json"))


@pytest.mark.parametrize("target", ["probes", "key-audit"])
def test_other_targets_do_not_change_the_answer_denominator(recorded_small, target):
    run = recorded_small
    # Unavailable evidence for another target cannot enter the answer inventory.
    write(run / "grading/runs/unrelated.json", {
        "grading_run_id": "unrelated", "target": target, "pass": 1,
        "records_written": 1, "execution_results": {"haiku/other--a1.json": "0" * 64},
    })
    summary = score_run_dir(run, schema_path=runs.SCHEMA)
    assert summary["graded_cells"] == 6
    assert summary["spend"]["graded_answers"] == 6


def test_invalid_empty_invocation_cannot_establish_a_zero_inventory(tmp_path):
    write(tmp_path / "runs/bad.json", {
        "grading_run_id": "bad", "target": "answers", "pass": True,
        "records_written": 0, "execution_results": {},
    })
    with pytest.raises(BenchmarkValidationError, match="inventory"):
        VoteCapture(tmp_path).check("answers", 1)


def test_relabelled_pass_cannot_hide_an_entire_issued_target(recorded_small):
    run = recorded_small
    for path in (run / "grading/votes/answers").rglob("*.json"):
        path.unlink()
    for path in (run / "grading/runs").glob("*.json"):
        invocation = read(path)
        invocation["pass"] = 2
        write(path, invocation)
    with pytest.raises(BenchmarkValidationError, match="inventory"):
        VoteCapture(run / "grading").check("answers", 1)


@pytest.mark.parametrize("history", ["no_invocation", "no_manifest", "empty_manifest"])
def test_present_historical_votes_are_distinct_from_an_empty_manifest(recorded_small, history):
    run = recorded_small
    for path in (run / "grading/runs").glob("*.json"):
        if history == "no_invocation":
            path.unlink()
        else:
            invocation = read(path)
            if history == "no_manifest":
                invocation.pop("execution_results")
            else:
                invocation.update(execution_results={}, records_written=0)
            write(path, invocation)
    if history == "no_invocation":
        # Retain a composition selection, without claiming the old votes' run.
        write(run / "grading/runs/composition.json", {
            "target": "answers", "pass": 1, "composition": runs.COMPOSITION,
        })
    if history == "empty_manifest":
        with pytest.raises(BenchmarkValidationError, match="inventory"):
            score_run_dir(run, schema_path=runs.SCHEMA)
    else:
        assert score_run_dir(run, schema_path=runs.SCHEMA)["graded_cells"] == 6


def test_scorer_captures_invocations_votes_and_receipts_once(recorded_small, monkeypatch):
    from benchmarks.grading import vote_capture

    seen = Counter()

    def read_once(root, path):
        if any(part in path.parts for part in ("runs", "votes", "executions", "items")):
            seen[path] += 1
            assert seen[path] == 1, path
        return artifact_bytes(root, path)

    # Capture's reader is explicit so this also covers consumers that retain it.
    original = vote_capture.VoteCapture
    monkeypatch.setattr(
        "benchmarks.grading.score.VoteCapture",
        lambda root: original(root, reader=read_once),
    )
    monkeypatch.setattr("benchmarks.grading.gates.artifact_bytes", read_once)
    monkeypatch.setattr("benchmarks.grading.aggregate.artifact_bytes", read_once)
    assert score_run_dir(recorded_small, schema_path=runs.SCHEMA)["graded_cells"] == 6
    assert seen and max(seen.values()) == 1


def _additional_execution(run, original, *, run_id, attempt=1, pass_number=1):
    """Produce a separate lawful local batch/retry before applying any corruption."""
    grading = run / "grading"
    prior = read(original)
    judge = load_panel(grading / "inputs/panel.yml").judges[prior["judge_id"]]
    relative = f"{judge.id}/{prior['item_id']}--a{prior['attempt']}.json"
    old = read(grading / "executions" / prior["grading_run_id"] / relative)
    result = {"anthropic": anthropic_result, "openai": openai_result,
              "google": gemini_result}[judge.provider](old["result"]["raw"])
    unit = f"{run_id}-{attempt}"
    path = write_vote_record(
        grading, target=prior["target"], judge=judge, effort=prior["effort"],
        pass_number=pass_number, grading_run_id=run_id, attempt=attempt, result=result,
        terminal=prior["terminal"], unit_id=unit, provider_batch_id=f"batch-{unit}",
        input_sha256=prior["input_sha256"], schema=judge_schema(),
    )
    saved = read(path)
    history = {k: saved[k] for k in (
        "grading_run_id", "unit_id", "item_id", "judge_id", "target", "pass", "attempt",
        "provider_batch_id", "input_sha256", "model_id", "effort",
    )}
    history.update(vote_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                   provider=judge.provider, api=judge.api, result=asdict(result))
    relative = f"{judge.id}/{prior['item_id']}--a{attempt}.json"
    body = write(grading / "executions" / run_id / relative, history)
    invocation_path = grading / "runs" / f"{run_id}.json"
    invocation = read(invocation_path) if invocation_path.exists() else {
        "grading_run_id": run_id, "target": prior["target"], "pass": pass_number,
        "composition": read(grading / "runs" / f"{prior['grading_run_id']}.json")["composition"],
        "status": "completed", "dry_run": False, "not_run": [], "units": [],
        "records_written": 0, "execution_results": {},
        "spent_before": {judge.provider: "0"}, "spent_after": {judge.provider: "0"},
    }
    invocation["units"].append(unit)
    invocation["execution_results"][relative] = hashlib.sha256(body).hexdigest()
    invocation["records_written"] += 1
    invocation["spent_after"][judge.provider] = str(
        Decimal(invocation["spent_after"][judge.provider]) + Decimal("0.01")
    )
    write(invocation_path, invocation)
    identity = {"unit_id": unit, "run_id": run_id, "provider": judge.provider}
    events = [
        {**identity, "event": "reserve", "kind": "batch", "model_id": judge.model_id,
         "api": judge.api, "job_id": f"{judge.id}--grading", "bound": "100"},
        {**identity, "event": "settle", "outcome": "batch", "booked": "0.01",
         "actual": "0.01", "items": [{"item_id": prior["item_id"], "status": "ok",
                                      "booked": "0.01"}]},
    ]
    with (grading / "ledger-events.jsonl").open("a") as ledger:
        ledger.writelines(json.dumps(event) + "\n" for event in events)
    return path


def test_multiple_batches_retries_and_invocations_keep_the_same_answer_count(recorded_small):
    run = recorded_small
    original = _issued_vote(run)
    _additional_execution(run, original, run_id="later", attempt=1)
    _additional_execution(run, original, run_id="later", attempt=2)
    summary = score_run_dir(run, schema_path=runs.SCHEMA)
    assert summary["graded_cells"] == 6
    assert summary["spend"]["graded_answers"] == 6
    original.unlink()  # A valid newer selection cannot bless an older omission.
    with pytest.raises(BenchmarkValidationError, match="missing.*vote"):
        score_run_dir(run, schema_path=runs.SCHEMA)


@pytest.mark.parametrize("omitted", ["vote", "receipt"])
def test_report_pass2_omission_only_withholds_retest(validated_bundle, omitted):
    run = validated_bundle
    original = _issued_vote(run)
    path = _additional_execution(run, original, run_id="repeat", pass_number=2)
    complete = _RunMetadata(_load_run_bundle(run))
    assert complete.grading_spend["graded_answers"] == 300
    assert not complete.retest_unknown
    if omitted == "receipt":
        row = read(path)
        path = run / "grading/executions/repeat" / row["judge_id"] / f"{row['item_id']}--a1.json"
    path.unlink()
    current = _RunMetadata(_load_run_bundle(run))
    assert current.retest_unknown
    assert current.grading_spend["graded_answers"] == 300
    assert current.tier == "Validated"
    assert all(current.summary["gates"][g]["passed"] is True for g in ("G1", "G2", "G3", "G4"))


def test_report_captures_declared_history_once(validated_bundle, monkeypatch):
    from benchmarks import agentic_report, report_files

    seen = Counter()

    def read_once(root, path):
        if path.is_relative_to(root / "grading") and any(
            part in path.parts for part in ("runs", "votes", "executions")
        ):
            seen[path] += 1
            assert seen[path] == 1, path
        return artifact_bytes(root, path)

    monkeypatch.setattr(agentic_report, "artifact_bytes", read_once)
    monkeypatch.setattr(report_files, "artifact_bytes", read_once)
    current = _RunMetadata(_load_run_bundle(validated_bundle))
    assert current.tier == "Validated"
    assert current.grading_spend["graded_answers"] == 300
    assert seen and max(seen.values()) == 1
