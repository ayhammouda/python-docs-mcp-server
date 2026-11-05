"""Denominators and disclosures on ordinary local scoring and report artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.grading.gates import SampleCandidate, c2, c3, g3_sample
from benchmarks.grading.inputs import safe_id
from tests.benchmarks import test_agentic_report as reports
from tests.benchmarks import test_grading_score as runs

scripted_graded_run = runs.scripted_graded_run


def test_g2_keeps_independent_votes_when_reference_settlement_is_missing(
    scripted_graded_run: Path, tmp_path: Path
) -> None:
    run = scripted_graded_run
    settled = runs._score(run, tmp_path)
    expected = settled["gates"]["G2"]
    assert expected["details"]["units"] == 6
    assert expected["details"]["alpha"] == pytest.approx(0.9375)
    item = safe_id(runs._cell("python-docs"), "SYN-EX-002")
    votes = run / "grading" / "votes"
    next((votes / "reference/reference/high/pass1" / item).glob("*.json")).unlink()
    pending = runs._score(run, tmp_path)
    assert pending["gates"]["G2"] == expected
    assert pending["breakdowns"]["G2"] == settled["breakdowns"]["G2"]

    # Two independent grades still count if the third judge never finished.
    next((votes / "answers/flash-lite/low/pass1" / item).glob("*.json")).unlink()
    assert runs._score(run, tmp_path)["gates"]["G2"] == expected
    next((votes / "answers/luna/low/pass1" / item).glob("*.json")).unlink()
    assert runs._score(run, tmp_path)["gates"]["G2"]["details"]["units"] == 5


@pytest.mark.parametrize("system", runs.SYSTEMS)
@pytest.mark.parametrize("changed_pair", [("other-question", 1), ("q", 2), None])
def test_paired_claims_require_identical_question_repeat_sets(system, changed_pair) -> None:
    planned = {(s, "model", "q", 1) for s in runs.SYSTEMS}
    if changed_pair is not None:
        planned.remove((system, "model", "q", 1))
        planned.add((system, "model", *changed_pair))
    scores = {key: float(key[0] != "no-docs") for key in planned}
    tokens = {key: 10 if key[0] == "python-docs" else 100 for key in planned}
    if changed_pair is None:
        # Matching observed rows cannot establish the scope of a missing system plan.
        planned.remove((system, "model", "q", 1))
    claims = (
        (c2(tokens, scores, planned, model="model", seed="1", resamples=20), "docs-fetch"),
        (c3(scores, planned, model="model", seed="1", offline_ok=True, resamples=20), "no-docs"),
    )
    for result, peer in claims:
        if system in ("python-docs", peer):
            assert result.passed is None and result.interval is None
            assert system in result.reason
        else:
            assert result.passed is True and result.interval is not None


def test_unplanned_scored_repeat_blocks_only_its_own_comparison() -> None:
    planned = {(s, "model", "q", 1) for s in runs.SYSTEMS}
    scores = {key: float(key[0] != "no-docs") for key in planned}
    scores[("no-docs", "model", "q", 2)] = 0
    tokens = {key: 10 if key[0] == "python-docs" else 100 for key in planned}
    assert c2(tokens, scores, planned, model="model", seed="1", resamples=20).passed is True
    offline = c3(scores, planned, model="model", seed="1", offline_ok=True, resamples=20)
    assert offline.passed is None and offline.interval is None


@pytest.mark.parametrize("missing", ["file", "cells", "peer"])
def test_scorer_reports_unverifiable_claim_with_missing_plan(tmp_path: Path, missing: str) -> None:
    run = runs._build_run(tmp_path)
    plan = run / "planned-cells.json"
    if missing == "file":
        plan.unlink()
    elif missing == "cells":
        runs._json(plan, {})
    else:
        cells = json.loads(plan.read_text())["cells"]
        runs._json(plan, {"cells": [c for c in cells if c["system_id"] != "docs-fetch"]})
    result = runs._score(run, tmp_path)["claims"]["C2"][runs.FAMILY]
    assert result["passed"] is None and result["interval"] is None
    assert "planned" in result["reason"]


@pytest.mark.parametrize("stage", ["ungraded", "partial", "complete", "zero"])
def test_cost_shows_known_spend_and_waits_for_each_models_grades(
    tmp_path: Path, stage: str
) -> None:
    run = reports._build(tmp_path)
    if stage != "ungraded":
        reports._score(run, tmp_path)
    first = reports.FAMILIES[0]
    if stage == "partial":
        (run / "scoring" / runs._cell("python-docs", first) / "SYN-EX-001.json").unlink()
    if stage == "zero":
        for path in (run / "scoring").glob(f"*--{first}--r1/*.json"):
            reports._json(path, {**json.loads(path.read_text()), "score": 0.0})
    text = reports._render(run)
    cost = reports._section(text, "## Cost per correct answer")
    first_ratio = {
        "ungraded": "pending",
        "partial": "pending",
        "complete": "0.0030",
        "zero": "no correct answer",
    }[stage]
    second_ratio = "pending" if stage == "ungraded" else "0.0034"
    assert f"| {first} | 0.0121 | {first_ratio} |" in cost
    assert f"| {reports.FAMILIES[1]} | 0.0121 | {second_ratio} |" in cost
    count = {"ungraded": 0, "partial": 11, "complete": 12, "zero": 12}[stage]
    assert f"completed trajectories: 12; scored cells: {count}" in text


@pytest.mark.parametrize("retry_time", ["2026-11-02T12:00:00Z", "2026-11-01T12:00:00Z", None])
def test_failure_disclosure_uses_recorded_time_and_names_affected_item(
    tmp_path: Path, retry_time: str | None
) -> None:
    run = reports._build(tmp_path)
    item = safe_id(runs._cell("python-docs"), "SYN-EX-001")
    grading = run / "grading"
    next((grading / "votes/answers/luna/low/pass1" / item).glob("*.json")).unlink()
    for name, timestamp, reason in [
        ("z-first", "2026-11-01T12:00:00Z", "worker_lost"),
        ("a-retry", retry_time, "budget_stop"),
    ]:
        reports._json(
            grading / "runs" / f"{name}.json",
            {
                "grading_run_id": name,
                "completed_at": timestamp,
                "not_run": [
                    {
                        "target": "answers",
                        "pass": 1,
                        "judge_id": "luna",
                        "effort": "low",
                        "reason": reason,
                        "item_id": item,
                    }
                ],
            },
        )
    reports._score(run, tmp_path)
    line = next(s for s in reports._render(run).splitlines() if "last not_run reason" in s)
    assert "budget_stop" in line and item in line
    if retry_time == "2026-11-02T12:00:00Z":
        assert "worker_lost" not in line
    else:
        assert "chronology unknown" in line and "worker_lost" in line


def test_short_g3_sample_reports_recorded_borrow_and_remaining_shortfall(tmp_path: Path) -> None:
    run = reports._build(tmp_path)
    candidates = [
        SampleCandidate(
            safe_id(runs._cell(s), q), runs.FAMILY, s, category if s == "docs-fetch" else "applied"
        )
        for s in ("docs-fetch", "python-docs")
        for q, category in zip(runs.QUESTIONS, ("applied", "lookup"), strict=True)
    ]
    excluded = {c.item_id for c in candidates if c.category == "applied"}
    ids, info = g3_sample(candidates, excluded=excluded, seed="7", size=2)
    assert info["borrowed"] == {f"{runs.FAMILY}/docs-fetch": {"lookup": 1}}
    assert info["shortfall"] == {f"{runs.FAMILY}/python-docs": 1}
    reports._json(run / "grading/reference-sample.json", {**info, "ids": ids})
    text = reports._render(reports._score(run, tmp_path))
    assert "1 of 300 answers" in text
    assert f"| {runs.FAMILY}/docs-fetch | 1 | 1 | 1 from lookup | 0 |" in text
    assert f"| {runs.FAMILY}/python-docs | 1 | 0 | none | 1 |" in text
