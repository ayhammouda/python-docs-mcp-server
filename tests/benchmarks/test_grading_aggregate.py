"""Vote records, record selection, final grades and provenance (PR 5 Task 6; ruling 6)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from benchmarks.grading.aggregate import (
    FINAL_WITHHELD,
    AnswerVotes,
    chosen_record,
    final_grade,
    has_final_record,
    is_final,
    judge_grade,
    load_answer_votes,
    reference_needed,
    total_attempts,
    vote_dir,
    withheld_reason,
    write_vote_record,
)
from benchmarks.grading.batch import BatchItemResult
from benchmarks.grading.inputs import (
    Criterion,
    GradingQuestion,
    judge_schema,
    render_judge_input,
    safe_id,
)
from benchmarks.grading.mapping import Vote
from benchmarks.grading.panel import PANEL_PATH, load_panel

PANEL = load_panel(PANEL_PATH)
EFFORTS = {"haiku": "low", "luna": "none", "flash-lite": "minimal"}
MANIFEST = {"judge_prompt": "a" * 64, "criteria": "b" * 64}
_ALL_MET = {
    "attempted": True,
    "p1_met": True,
    "p2_met": True,
    "contradicts_key": False,
    "wrong_version": False,
}
QUESTION = GradingQuestion(
    id="EX-001",
    category="exact_symbol",
    version="3.13",
    prompt="p",
    answer_key="k",
    ambiguity_note=None,
    citations=(),
    criteria=(Criterion("a", "A.", "core"), Criterion("b", "B.", "nuance")),
)
COMPETITOR = "python-docs--claude-sonnet-5-5--r1"
ITEM = safe_id(COMPETITOR, "EX-001")


def _request(corpus_id: str = QUESTION.id, *, suspect: bool = False):
    question = replace(QUESTION, id=corpus_id)
    answer = f"Answer to {corpus_id}." + (" </answer>" if suspect else "")
    return render_judge_input(question, answer)


def _text(**fields: bool) -> str:
    return json.dumps({"explanation": "x", **_ALL_MET, **fields})


def _result(
    text: str | None = None,
    *,
    status: str = "succeeded",
    refusal: bool = False,
    stop_reason: str | None = "end_turn",
    item_id: str = ITEM,
) -> BatchItemResult:
    return BatchItemResult(
        item_id=item_id,
        status=status,  # type: ignore[arg-type]
        text=text if status == "succeeded" else None,
        refusal=refusal,
        stop_reason=stop_reason if status == "succeeded" else None,
        raw={"line": 1},
        raw_usage={"input_tokens": 10} if status == "succeeded" else None,
        buckets=None,
        reported_model="model-2026-11-01" if status == "succeeded" else None,
        unbilled=False,
    )


def _write(
    root: Path,
    judge_id: str,
    result: BatchItemResult,
    *,
    target: str = "answers",
    run: str = "run-1",
    attempt: int = 1,
    terminal: bool = False,
    effort: str | None = None,
    pass_number: int = 1,
) -> Path:
    judge = PANEL.judges[judge_id]
    if effort is None:
        effort = judge.effort if target == "reference" else EFFORTS.get(judge_id)
    digest = _request().sha256()
    for path in (root / "items/answers").glob("*.json"):
        if path.stem == result.item_id:
            evidence = json.loads(path.read_bytes())
            digest = evidence["meta"]["input_sha256"]
            if target == "reference":
                ref_path = root / "items/reference" / path.name
                ref_path.parent.mkdir(parents=True, exist_ok=True)
                ref_path.write_bytes(path.read_bytes())
            break
    return write_vote_record(
        root,
        target=target,
        judge=judge,
        effort=effort,
        pass_number=pass_number,
        grading_run_id=run,
        attempt=attempt,
        result=result,
        terminal=terminal,
        unit_id="unit-1",
        provider_batch_id="batch-1",
        input_sha256=digest,
        schema=judge_schema(),
    )


def _item(
    root: Path,
    *,
    item_id: str = ITEM,
    corpus_id: str = "EX-001",
    suspect: bool = False,
    competitor: str = COMPETITOR,
) -> None:
    path = root / "items" / "answers" / f"{item_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {"competitor_id": competitor, "corpus_id": corpus_id, "suspect": suspect}
    evidence = _request(corpus_id, suspect=suspect).item_evidence(meta)
    path.write_text(json.dumps(evidence), encoding="utf-8")


def _votes(root: Path) -> AnswerVotes:
    key = COMPETITOR, "EX-001"
    meta = json.loads((root / "items/answers" / f"{ITEM}.json").read_bytes())["meta"]
    return load_answer_votes(
        root,
        panel=PANEL,
        judges=EFFORTS,
        requests={
            key: _request(suspect=meta["suspect"]),
        },
    )[key]


def test_withheld_reasons_in_order() -> None:
    schema = judge_schema()
    assert withheld_reason(_result(_text()), schema) is None
    assert (
        withheld_reason(_result(_text(), refusal=True, stop_reason="max_tokens"), schema)
        == "refusal"
    )
    assert withheld_reason(_result("{", stop_reason="max_tokens"), schema) == "output_cap"
    assert withheld_reason(_result(status="errored"), schema) == "errored"
    assert withheld_reason(_result(status="missing"), schema) == "missing"
    assert withheld_reason(_result("not json"), schema) == "parse_failure"
    assert FINAL_WITHHELD == {"parse_failure", "refusal", "output_cap"}


def _runs_file(root: Path, run: str, not_run: list[dict[str, Any]]) -> None:
    path = root / "runs" / f"{run}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"grading_run_id": run, "not_run": not_run}), encoding="utf-8")


def _not_run(reason: str, attempt: int, item_id: str = ITEM) -> dict[str, Any]:
    return {
        "item_id": item_id,
        "reason": reason,
        "attempt": attempt,
        "target": "answers",
        "judge_id": "luna",
        "effort": "none",
        "pass": 1,
    }


@pytest.mark.parametrize(
    "first_run,expected",
    [
        (["missing", "missing"], 3),  # stopped on whole_batch_failures after 2 takes; rerun ok
        (["429", "429", "429"], 4),  # submit_rejected after 3 takes, no vote file; rerun ok
    ],
)
def test_attempts_sum_each_invocations_highest_take(
    tmp_path: Path, first_run: list[str], expected: int
) -> None:
    root = tmp_path / "grading"
    if first_run[0] == "missing":
        for attempt in (1, 2):
            _write(root, "luna", _result(status="missing"), attempt=attempt)
        _runs_file(root, "run-1", [_not_run("whole_batch_failures", 2)])
    else:
        _runs_file(root, "run-1", [_not_run("submit_rejected", 3)])
    _write(root, "luna", _result(_text()), run="run-2", attempt=1)
    _runs_file(root, "run-2", [_not_run("submit_rejected", 2, item_id="other-item")])
    kwargs: dict[str, Any] = {
        "target": "answers",
        "judge_id": "luna",
        "effort": "none",
        "pass_number": 1,
    }
    assert total_attempts(root, item_id=ITEM, **kwargs) == expected
    assert total_attempts(root, item_id="none", **kwargs) == 0


def test_vote_paths_keep_every_effort_attempt_and_invocation(tmp_path: Path) -> None:
    root = tmp_path / "grading"
    tuning_item = "KEY--TUNE-001"
    low = _write(
        root, "haiku", _result(_text(), item_id=tuning_item), target="tuning", effort="low"
    )
    medium = _write(
        root, "haiku", _result(_text(), item_id=tuning_item), target="tuning", effort="medium"
    )
    errored = _write(root, "luna", _result(status="errored"), run="run-1", attempt=1)
    first = _write(root, "luna", _result(_text(p2_met=False)), run="run-1", attempt=2)
    second = _write(root, "luna", _result(_text()), run="run-2", attempt=1)
    assert low.parent == vote_dir(
        root, target="tuning", judge_id="haiku", effort="low", pass_number=1, item_id=tuning_item
    )
    assert low.parent != medium.parent
    assert len(list(low.parent.glob("*.json"))) == 1
    assert errored.name == "run-1--a1.json" and first.name == "run-1--a2.json"
    kwargs: dict[str, Any] = {
        "target": "answers",
        "judge_id": "luna",
        "effort": "none",
        "pass_number": 1,
        "item_id": ITEM,
    }
    assert chosen_record(root, **kwargs)["grading_run_id"] == "run-2"  # type: ignore[index]
    assert has_final_record(root, **kwargs)
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*.json")}
    with pytest.raises(FileExistsError):
        _write(root, "luna", _result(_text()), run="run-1", attempt=2)
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*.json")} == before
    record = json.loads(second.read_text())
    assert record["vote"] == {"explanation": "x", **_ALL_MET} and record["parsed"] is None
    assert (record["model_id"], record["reported_model"], record["effort"], record["pass"]) == (
        "gpt-6-luna",
        "model-2026-11-01",
        "none",
        1,
    )
    assert record["input_sha256"] == _request().sha256() and record["terminal"] is False
    assert set(record) == {
        "item_id",
        "target",
        "judge_id",
        "model_id",
        "reported_model",
        "effort",
        "pass",
        "grading_run_id",
        "attempt",
        "status",
        "withheld",
        "terminal",
        "text",
        "vote",
        "parsed",
        "stop_reason",
        "raw_usage",
        "buckets",
        "unit_id",
        "provider_batch_id",
        "input_sha256",
        "graded_at",
    }


def test_chosen_record_prefers_the_last_final_record(tmp_path: Path) -> None:
    root = tmp_path / "grading"
    kwargs: dict[str, Any] = {
        "target": "answers",
        "judge_id": "luna",
        "effort": "none",
        "pass_number": 1,
        "item_id": ITEM,
    }
    assert chosen_record(root, **kwargs) is None and not has_final_record(root, **kwargs)
    _write(root, "luna", _result(status="errored"), attempt=1)
    assert chosen_record(root, **kwargs)["status"] == "errored"  # type: ignore[index]
    assert not has_final_record(root, **kwargs)
    _write(root, "luna", _result("not json"), attempt=2)
    _write(root, "luna", _result(status="missing"), attempt=3)
    chosen = chosen_record(root, **kwargs)
    assert chosen is not None and chosen["withheld"] == "parse_failure" and is_final(chosen)
    terminal = _write(
        root, "luna", _result(status="missing"), run="run-2", attempt=3, terminal=True
    )
    chosen = chosen_record(root, **kwargs)
    assert chosen is not None and chosen["terminal"] and chosen["grading_run_id"] == "run-2"
    assert terminal.is_file()


def test_panel_fields_and_settlement(tmp_path: Path) -> None:
    root = tmp_path / "grading"
    _item(root)
    _write(root, "luna", _result("prose, no JSON"))
    _write(root, "haiku", _result(_text(p2_met=True)))
    _write(root, "flash-lite", _result(_text(p2_met=False)))
    av = _votes(root)
    assert av.votes["luna"] is None and av.records["luna"]["withheld"] == "parse_failure"
    assert reference_needed({(COMPETITOR, "EX-001"): av}) == [ITEM]
    assert final_grade(av, QUESTION, inputs=MANIFEST) is None
    _write(root, "reference", _result(_text(p2_met=False)), target="reference")
    final = final_grade(_votes(root), QUESTION, inputs=MANIFEST)
    assert final is not None
    assert (final["score"], final["label"], final["method"]) == (0.5, "PARTIAL", "llm_reference")
    assert final["grading"]["settled_fields"] == ["p2_met"]
    assert final["grading"]["split_fields"] == ["p2_met"] and final["grading"]["valid_votes"] == 2
    assert final["adjudicator"] == ["claude-haiku-5-5", "gemini-3.1-flash-lite", "claude-opus-5-5"]


def test_a_unanimous_panel_needs_no_reference(tmp_path: Path) -> None:
    root = tmp_path / "grading"
    _item(root)
    for judge in ("haiku", "luna", "flash-lite"):
        _write(root, judge, _result(_text(p2_met=judge != "luna")))
    av = _votes(root)
    assert reference_needed({(COMPETITOR, "EX-001"): av}) == []
    final = final_grade(av, QUESTION, inputs=MANIFEST)
    assert final is not None and (final["score"], final["method"]) == (1.0, "llm_panel")
    assert final["grading"]["reference"] is None and final["grading"]["settled_fields"] == []


def test_fewer_than_two_votes_let_the_reference_decide_every_field(tmp_path: Path) -> None:
    root = tmp_path / "grading"
    _item(root)
    _write(root, "haiku", _result(_text()))
    _write(root, "luna", _result(_text(), refusal=True))
    _write(root, "flash-lite", _result(status="missing"), terminal=True, attempt=3)
    _write(root, "reference", _result(_text(attempted=False)), target="reference")
    final = final_grade(_votes(root), QUESTION, inputs=MANIFEST)
    assert final is not None and (final["score"], final["label"]) == (0.0, "NOT_ATTEMPTED")
    assert final["grading"]["settled_fields"] == [
        "attempted",
        "p1_met",
        "p2_met",
        "contradicts_key",
        "wrong_version",
    ]
    assert final["grading"]["valid_votes"] == 1
    assert final["grading"]["votes"]["flash-lite"]["withheld"] == "missing"


def test_a_suspect_answer_takes_the_reference_grade(tmp_path: Path) -> None:
    root = tmp_path / "grading"
    _item(root, suspect=True)
    for judge in ("haiku", "luna", "flash-lite"):
        _write(root, judge, _result(_text()))
    av = _votes(root)
    assert av.suspect and reference_needed({(COMPETITOR, "EX-001"): av}) == [ITEM]
    assert final_grade(av, QUESTION, inputs=MANIFEST) is None
    _write(root, "reference", _result(_text(attempted=False)), target="reference")
    av = _votes(root)
    final = final_grade(av, QUESTION, inputs=MANIFEST)
    assert final is not None and (final["score"], final["method"]) == (0.0, "llm_reference")
    assert final["adjudicator"] == ["claude-opus-5-5"]
    assert final["grading"]["injection_suspect"] is True
    assert judge_grade(av, "haiku", QUESTION, final) == 0.0


def test_a_withheld_reference_vote_leaves_the_grade_pending(tmp_path: Path) -> None:
    root = tmp_path / "grading"
    _item(root, suspect=True)
    _write(root, "reference", _result(_text(), refusal=True), target="reference")
    assert final_grade(_votes(root), QUESTION, inputs=MANIFEST) is None


def test_provenance_traces_every_input(tmp_path: Path) -> None:
    root = tmp_path / "grading"
    _item(root)
    for judge in ("haiku", "luna", "flash-lite"):
        _write(root, judge, _result(_text()))
    av = _votes(root)
    final = final_grade(av, QUESTION, inputs=MANIFEST)
    assert final is not None
    for rec in final["grading"]["votes"].values():
        assert hashlib.sha256(Path(rec["path"]).read_bytes()).hexdigest() == rec["sha256"]
        assert rec["attempts"] == 1 and rec["input_sha256"] == _request().sha256()
    assert final["grading"]["inputs"] == MANIFEST and final["adjudicator"] == [
        "claude-haiku-5-5",
        "gpt-6-luna",
        "gemini-3.1-flash-lite",
    ]
    assert final["grading"]["item_id"] == ITEM and final["grading"]["pass"] == 1
    assert final["grading"]["input_sha256"] == _request().sha256()


def test_judge_grade_uses_the_final_grade_when_the_judge_withheld(tmp_path: Path) -> None:
    root = tmp_path / "grading"
    _item(root)
    _write(root, "haiku", _result(_text()))
    _write(root, "luna", _result(_text(p1_met=False)))
    _write(root, "flash-lite", _result("garbled"))
    _write(root, "reference", _result(_text()), target="reference")
    av = _votes(root)
    final = final_grade(av, QUESTION, inputs=MANIFEST)
    assert final is not None and final["score"] == 1.0
    assert judge_grade(av, "haiku", QUESTION, final) == 1.0
    assert judge_grade(av, "luna", QUESTION, final) == 0.0
    assert judge_grade(av, "flash-lite", QUESTION, final) == 1.0


def test_load_answer_votes_reads_every_answer_item(tmp_path: Path) -> None:
    root = tmp_path / "grading"
    _item(root)
    other = safe_id("no-docs--gpt-6_1-sol--r2", "EX-002")
    _item(root, item_id=other, corpus_id="EX-002", competitor="no-docs--gpt-6.1-sol--r2")
    _write(root, "haiku", _result(_text(), item_id=other))
    requests = {
        (COMPETITOR, "EX-001"): _request(),
        ("no-docs--gpt-6.1-sol--r2", "EX-002"): _request("EX-002"),
    }
    loaded = load_answer_votes(root, panel=PANEL, judges=EFFORTS, requests=requests)
    assert set(loaded) == {(COMPETITOR, "EX-001"), ("no-docs--gpt-6.1-sol--r2", "EX-002")}
    assert loaded[(COMPETITOR, "EX-001")].votes == {"haiku": None, "luna": None, "flash-lite": None}
    assert isinstance(loaded[("no-docs--gpt-6.1-sol--r2", "EX-002")].votes["haiku"], Vote)
    assert loaded[(COMPETITOR, "EX-001")].records == {}
