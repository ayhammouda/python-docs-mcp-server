"""LLM provenance in the scorer: llm_panel and llm_reference, never human (PR 5 Task 6)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from benchmarks.runner import BenchmarkValidationError
from benchmarks.scoring import (
    LLM_SCORING_METHODS,
    SCORING_METHOD_HUMAN,
    SCORING_METHOD_LLM_PANEL,
    SCORING_METHOD_LLM_REFERENCE,
    apply_llm_grades,
    ingest_adjudication_verdicts,
    score_run,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_CORPUS = REPO_ROOT / "tests" / "benchmarks" / "fixtures" / "corpus.sample.yml"
SCHEMA = REPO_ROOT / "docs" / "benchmarks" / "corpus.schema.json"
CELL = "python-docs--scripted-anthropic--r1"


def _json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _run(tmp_path: Path, answers: dict[str, str], *, mode: str = "agentic") -> Path:
    run = tmp_path / "run"
    (run / "snapshots").mkdir(parents=True)
    shutil.copy(SAMPLE_CORPUS, run / "snapshots" / "corpus.yml")
    _json(run / "environment.json", {"mode": mode})
    for qid, answer in answers.items():
        failed = answer == "<failed>"
        base = {"competitor_id": CELL, "corpus_id": qid, "tool_model_key": "python-docs:a/m"}
        _json(
            run / "scoring" / CELL / f"{qid}.json",
            {
                **base,
                "status": "failed" if failed else "succeeded",
                "score": 0.0 if failed else None,
                "requires_manual_scoring": not failed,
            },
        )
        _json(
            run / "transcripts" / CELL / f"{qid}.json",
            {
                **base,
                "status": "failed" if failed else "succeeded",
                "answer": "" if failed else answer,
                "tool_calls": [],
            },
        )
    return run


def _grade(score: float, method: str = SCORING_METHOD_LLM_PANEL) -> dict[str, Any]:
    label = {1.0: "CORRECT", 0.5: "PARTIAL", 0.0: "INCORRECT"}.get(score, "?")
    return {
        "score": score,
        "label": label,
        "method": method,
        "adjudicator": ["claude-haiku-5-5"],
        "grading": {"item_id": "x", "votes": {}},
    }


def _record(run: Path, qid: str) -> dict[str, Any]:
    return json.loads((run / "scoring" / CELL / f"{qid}.json").read_text())


def test_llm_methods_are_a_closed_vocabulary() -> None:
    assert LLM_SCORING_METHODS == {SCORING_METHOD_LLM_PANEL, SCORING_METHOD_LLM_REFERENCE}
    assert SCORING_METHOD_HUMAN not in LLM_SCORING_METHODS


def test_apply_llm_grades_never_writes_human(tmp_path: Path) -> None:
    run = _run(tmp_path, {"SYN-EX-001": "an answer", "SYN-EX-002": "another"})
    score_run(run, schema_path=SCHEMA)
    queue = json.loads((run / "adjudication" / "queue.json").read_text())
    assert {c["reason"] for c in queue["cells"]} == {"non_empty_answer_awaits_llm_grading"}
    result = apply_llm_grades(
        run,
        {
            (CELL, "SYN-EX-001"): _grade(1.0),
            (CELL, "SYN-EX-002"): _grade(0.5, SCORING_METHOD_LLM_REFERENCE),
        },
        grounded={(CELL, "SYN-EX-001"): True, (CELL, "SYN-EX-002"): True},
        schema_path=SCHEMA,
    )
    first, second = _record(run, "SYN-EX-001"), _record(run, "SYN-EX-002")
    assert (first["scoring_method"], second["scoring_method"]) == ("llm_panel", "llm_reference")
    assert first["status"] == "scored" and first["requires_manual_scoring"] is False
    assert first["label"] == "CORRECT" and first["adjudicator"] == ["claude-haiku-5-5"]
    assert first["grading"] == {"item_id": "x", "votes": {}} and first["scored_at"]
    overall = result["rollups"]["overall"]
    assert (overall["llm_panel_scored_cells"], overall["llm_reference_scored_cells"]) == (1, 1)
    assert overall["human_scored_cells"] == 0
    queue = json.loads((run / "adjudication" / "queue.json").read_text())
    assert queue["cells"] == []
    rollups = json.loads((run / "scoring-rollups.json").read_text())
    assert rollups["overall"]["llm_panel_scored_cells"] == 1
    assert all(
        r.get("scoring_method") != "human"
        for r in map(json.loads, (p.read_text() for p in (run / "scoring").rglob("*.json")))
    )


def test_apply_llm_grades_writes_correct_but_ungrounded_from_grounding(tmp_path: Path) -> None:
    run = _run(
        tmp_path,
        {"SYN-EX-001": "a", "SYN-EX-002": "b", "SYN-EX-003": "<failed>", "SYN-EX-004": " "},
    )
    score_run(run, schema_path=SCHEMA)
    apply_llm_grades(
        run,
        {(CELL, "SYN-EX-001"): _grade(0.5), (CELL, "SYN-EX-002"): _grade(0.0)},
        grounded={
            (CELL, "SYN-EX-001"): False,
            (CELL, "SYN-EX-002"): False,
            (CELL, "SYN-EX-004"): True,
        },
        schema_path=SCHEMA,
    )
    assert _record(run, "SYN-EX-001")["correct_but_ungrounded"] is True
    assert _record(run, "SYN-EX-001")["grounded"] is False
    assert _record(run, "SYN-EX-002")["correct_but_ungrounded"] is False
    failed, blank = _record(run, "SYN-EX-003"), _record(run, "SYN-EX-004")
    assert (failed["grounded"], failed["correct_but_ungrounded"]) == (False, False)
    assert (blank["grounded"], blank["scoring_method"]) == (True, "automatic")


@pytest.mark.parametrize(
    "grade,match",
    [
        (_grade(0.7), "score"),
        (_grade(1.0, "human"), "scoring_method"),
        (_grade(1.0, "automatic"), "scoring_method"),
    ],
)
def test_apply_llm_grades_refuses_a_score_or_method_outside_the_vocabulary(
    tmp_path: Path, grade: dict[str, Any], match: str
) -> None:
    run = _run(tmp_path, {"SYN-EX-001": "a"})
    score_run(run, schema_path=SCHEMA)
    before = _record(run, "SYN-EX-001")
    with pytest.raises(BenchmarkValidationError, match=match):
        apply_llm_grades(run, {(CELL, "SYN-EX-001"): grade}, grounded={}, schema_path=SCHEMA)
    assert _record(run, "SYN-EX-001") == before


def test_apply_llm_grades_refuses_a_failed_or_unknown_cell(tmp_path: Path) -> None:
    run = _run(tmp_path, {"SYN-EX-001": "<failed>"})
    score_run(run, schema_path=SCHEMA)
    with pytest.raises(BenchmarkValidationError, match="failed"):
        apply_llm_grades(run, {(CELL, "SYN-EX-001"): _grade(1.0)}, grounded={}, schema_path=SCHEMA)
    with pytest.raises(BenchmarkValidationError, match="SYN-EX-009"):
        apply_llm_grades(run, {(CELL, "SYN-EX-009"): _grade(1.0)}, grounded={}, schema_path=SCHEMA)


def test_score_run_keeps_llm_grades(tmp_path: Path) -> None:
    run = _run(tmp_path, {"SYN-EX-001": "a", "SYN-EX-002": "b"})
    score_run(run, schema_path=SCHEMA)
    apply_llm_grades(
        run,
        {(CELL, "SYN-EX-001"): _grade(1.0)},
        grounded={(CELL, "SYN-EX-001"): True},
        schema_path=SCHEMA,
    )
    graded = _record(run, "SYN-EX-001")
    result = score_run(run, schema_path=SCHEMA)
    assert _record(run, "SYN-EX-001") == graded
    assert result["queued_for_adjudication_cells"] == 1
    assert result["rollups"]["overall"]["llm_panel_scored_cells"] == 1


def test_adjudicate_refuses_an_agentic_run(tmp_path: Path) -> None:
    run = _run(tmp_path, {"SYN-EX-001": "a"})
    score_run(run, schema_path=SCHEMA)
    verdicts = tmp_path / "verdicts.json"
    verdicts.write_text(
        json.dumps({"verdicts": [{"competitor_id": CELL, "corpus_id": "SYN-EX-001", "score": 1.0}]})
    )
    with pytest.raises(BenchmarkValidationError, match="no human grades an agentic run"):
        ingest_adjudication_verdicts(run, verdicts, schema_path=SCHEMA)
    assert _record(run, "SYN-EX-001")["score"] is None


def test_a_fixed_flow_queue_still_asks_for_a_human(tmp_path: Path) -> None:
    run = _run(tmp_path, {"SYN-EX-001": "a"}, mode="fixed")
    score_run(run, schema_path=SCHEMA)
    queue = json.loads((run / "adjudication" / "queue.json").read_text())
    assert [c["reason"] for c in queue["cells"]] == [
        "non_empty_answer_requires_human_correctness_judgment"
    ]
