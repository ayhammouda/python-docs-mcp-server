"""Missing-final reasons must belong to the panel being scored and published."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from benchmarks.grading.aggregate import AnswerVotes
from benchmarks.grading.diagnostics import missing_records
from benchmarks.grading.inputs import safe_id
from tests.benchmarks import test_agentic_report as reports
from tests.benchmarks import test_grading_score as runs


@pytest.mark.parametrize(
    "foreign",
    [
        "effort", "model", "judge", "item", "target", "pass",
        "run-target", "run-pass", "composition-effort", "composition-model",
        "composition-peer", "composition-empty", "composition-null",
    ],
)
def test_score_and_report_ignore_foreign_later_failure(tmp_path: Path, foreign: str) -> None:
    # Production vote writer, normal scorer and normal renderer; only failure
    # invocations below are synthetic. Their timestamps are captured now, never
    # retrofitted onto the historical acceptance recordings.
    run = reports._build(tmp_path, families_run=(runs.FAMILY,), suspect_answer=False)
    grading = run / "grading"
    item = safe_id(runs._cell("python-docs"), "SYN-EX-001")
    next((grading / "votes/answers/haiku/low/pass1" / item).glob("*.json")).unlink()
    entry = {
        "target": "answers", "pass": 1, "judge_id": "haiku", "effort": "low",
        "model_id": reports.COMPOSITION["haiku"]["model_id"],
        "reason": "budget_stop", "item_id": item,
    }
    original = {
        "grading_run_id": "collision", "completed_at": datetime.now(UTC).isoformat(),
        "not_run": [entry],
    }
    reports._json(grading / "runs/z-original.json", original)
    later = copy.deepcopy(original)
    later["completed_at"] = datetime.now(UTC).isoformat()
    later["not_run"][0]["reason"] = "submit_rejected"
    if foreign.startswith("run-"):
        later[foreign.removeprefix("run-")] = "reference" if foreign == "run-target" else 2
    elif foreign.startswith("composition-"):
        composition = copy.deepcopy(reports.COMPOSITION)
        if foreign == "composition-empty":
            composition = {}
        elif foreign == "composition-null":
            composition = None
        else:
            judge = "luna" if foreign == "composition-peer" else "haiku"
            field = "model_id" if foreign == "composition-model" else "effort"
            composition[judge][field] = "foreign-model" if field == "model_id" else "high"
        later["composition"] = composition
    else:
        key, value = {
            "effort": ("effort", "high"), "model": ("model_id", "foreign-model"),
            "judge": ("judge_id", "reference"), "item": ("item_id", "other-item"),
            "target": ("target", "reference"), "pass": ("pass", 2),
        }[foreign]
        later["not_run"][0][key] = value
    # Deliberately colliding IDs in separate captured invocations, and filename
    # order opposite to recorded time. Neither may broaden the coordinate join.
    reports._json(grading / "runs/a-foreign.json", later)
    reports._score(run, tmp_path)
    summary_path = grading / "summary.json"
    summary = json.loads(summary_path.read_text())
    expected = {"haiku": {"budget_stop": [item]}}
    line = next(s for s in reports._render(run).splitlines() if "last not_run reason" in s)
    assert (summary["diagnostics"]["no_final_record"], "budget_stop" in line) == (expected, True)
    assert summary["pending_cells"]
    # A renderer must rederive the same result, even with a dishonest saved cache.
    summary["diagnostics"]["no_final_record"] = {"haiku": {"submit_rejected": [item]}}
    reports._json(summary_path, summary)
    line = next(s for s in reports._render(run).splitlines() if "last not_run reason" in s)
    assert "budget_stop" in line and item in line and "haiku" in line
    assert "submit_rejected" not in line


def test_score_and_report_keep_legitimate_later_failure(tmp_path: Path) -> None:
    run = reports._build(tmp_path, families_run=(runs.FAMILY,), suspect_answer=False)
    grading = run / "grading"
    item = safe_id(runs._cell("python-docs"), "SYN-EX-001")
    next((grading / "votes/answers/haiku/low/pass1" / item).glob("*.json")).unlink()
    for name, reason in [("z-first", "budget_stop"), ("a-later", "submit_rejected")]:
        reports._json(grading / "runs" / f"{name}.json", {
            "grading_run_id": name, "target": "answers", "pass": 1,
            "composition": reports.COMPOSITION, "completed_at": datetime.now(UTC).isoformat(),
            "not_run": [{"target": "answers", "pass": 1, "judge_id": "haiku",
                         "effort": "low", "reason": reason, "item_id": item}],
        })
    reports._score(run, tmp_path)
    summary = json.loads((grading / "summary.json").read_text())
    assert summary["diagnostics"]["no_final_record"] == {"haiku": {"submit_rejected": [item]}}
    line = next(s for s in reports._render(run).splitlines() if "last not_run reason" in s)
    assert "submit_rejected" in line and "budget_stop" not in line


@pytest.mark.parametrize("effort", ["low", None])
@pytest.mark.parametrize("judge", ["haiku", "registered-fallback"])
@pytest.mark.parametrize(
    ("clock", "expected"),
    [
        ({"completed_at": "2026-11-02T12:00:00Z"}, "submit_rejected"),
        ({"started_at": "2026-11-02T12:00:00Z"}, "submit_rejected"),
        ({"completed_at": "2026-11-01T12:00:00Z"},
         "chronology unknown (budget_stop, submit_rejected)"),
        ({}, "chronology unknown (budget_stop, submit_rejected)"),
        ({"duration_s": 999999}, "chronology unknown (budget_stop, submit_rejected)"),
        ({"completed_at": "invalid"}, "chronology unknown (budget_stop, submit_rejected)"),
        ({"completed_at": "2026-11-02T12:00:00"},
         "chronology unknown (budget_stop, submit_rejected)"),
    ],
)
def test_missing_failure_chronology_preserves_legacy_and_fallback(clock, expected, judge, effort):
    composition = {judge: {"model_id": "registered-model", "effort": effort}}
    av = AnswerVotes("item", "cell", "question", {}, {}, False, None, None)
    entry = {"target": "answers", "pass": 1, "judge_id": judge,
             "effort": effort, "item_id": "item", "reason": "budget_stop"}
    original = {"completed_at": "2026-11-01T12:00:00Z", "not_run": [entry]}
    later = {**clock, "not_run": [{**entry, "reason": "submit_rejected"}]}
    saved = copy.deepcopy([original, later])
    for history in ([original, later], [later, original]):
        assert missing_records(history, composition, {"item": av}) == {judge: {expected: ["item"]}}
    assert [original, later] == saved
    # A final withheld record is final too: missing history never reopens it.
    final = AnswerVotes("item", "cell", "question", {}, {judge: {"final": True}},
                        False, None, None)
    assert missing_records([original, later], composition, {"item": final}) == {}


def test_missing_failure_does_not_borrow_another_run_composition():
    av = AnswerVotes("item", "cell", "question", {}, {}, False, None, None)
    composition = {"haiku": {"model_id": "registered-model", "effort": "low"}}
    entry = {"target": "answers", "pass": 1, "judge_id": "haiku",
             "effort": "low", "item_id": "item", "reason": "submit_rejected"}
    # Same id as a compatible run; that run has no failure. No identity metadata
    # may be spliced from one invocation onto another invocation's event.
    history = [
        {"grading_run_id": "collision", "composition": composition, "not_run": []},
        {"grading_run_id": "collision", "composition": {
            "haiku": {"model_id": "foreign-model", "effort": "low"}}, "not_run": [entry]},
    ]
    assert missing_records(history, composition, {"item": av}) == {"haiku": {"no_record": ["item"]}}
