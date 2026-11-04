"""Replaying agentic-score uses only the votes present in this export."""

import json
from pathlib import Path

import pytest

from benchmarks.__main__ import main
from benchmarks.grading.inputs import safe_id
from tests.benchmarks import test_grading_score as runs
from tests.benchmarks import test_scoring_llm as scoring


def test_agentic_score_replays_current_vote_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = runs._build_run(tmp_path)
    grading = run / "grading"
    system, qid = "docs-fetch", runs.QUESTIONS[0]
    cell = runs._cell(system)
    item = safe_id(cell, qid)
    votes = grading / "votes" / "answers"
    haiku = next((votes / "haiku" / "low" / "pass1" / item).glob("*.json"))
    original = haiku.read_bytes()
    # One dissenter: a withheld Haiku vote would require reference settlement.
    runs._vote(grading, "luna", item, runs._vote_text(contradicts_key=True), attempt=2)
    argv = [
        "agentic-score",
        "--run-dir",
        str(run),
        "--criteria",
        str(runs.CRITERIA),
        "--panel",
        str(runs.PANEL_PATH),
    ]

    def replay() -> dict:
        assert main(argv) == 0
        output = capsys.readouterr().out
        assert "Answer from" not in output
        summary = json.loads(output)
        assert summary == json.loads((grading / "summary.json").read_bytes())
        return summary

    complete = replay()
    assert complete["pending_cells"] == []
    assert complete["claims"]["C2"][runs.FAMILY]["passed"] is True
    record = runs._record(run, system, qid)
    assert record["score"] == 1.0
    haiku.unlink()
    missing = replay()
    assert missing["diagnostics"]["no_final_record"] == {"haiku": {"no_record": [item]}}
    pending = runs._record(run, system, qid)
    assert pending["score"] is None
    assert pending["status"] == "placeholder"
    assert pending["requires_manual_scoring"] and pending["queued_for_adjudication"]
    assert all(
        pending.get(key) is None
        for key in (
            "label",
            "grading",
            "adjudicator",
            "scoring_method",
            "scored_at",
            "correct_but_ungrounded",
            "grounded",
        )
    )
    assert missing["pending_cells"] == [f"{cell}/{qid}"]
    assert missing["graded_cells"] == complete["graded_cells"] - 1
    queue = json.loads((run / "adjudication" / "queue.json").read_bytes())["cells"]
    assert "Answer from" not in json.dumps(queue)
    assert [(e["competitor_id"], e["corpus_id"], e["reason"]) for e in queue] == [
        (cell, qid, "non_empty_answer_awaits_llm_grading")
    ]
    assert queue[0]["answer"] is None and queue[0]["tool_calls"] is None
    rollup = json.loads((run / "scoring-rollups.json").read_bytes())["overall"]
    assert (rollup["scored_cells"], rollup["pending_manual_scoring_cells"]) == (5, 1)
    for claims in (missing["claims"], *missing["claims_by_judge"].values()):
        c2 = claims["C2"][runs.FAMILY]
        assert c2["passed"] is None and c2["interval"] is None and qid in c2["reason"]

    # Final withholding counts as a record, but the remaining votes disagree.
    runs._vote(grading, "haiku", item, "not a vote", attempt=2)
    withheld = replay()
    assert withheld["diagnostics"]["no_final_record"] == {}
    assert withheld["diagnostics"]["per_judge"]["haiku"]["withheld"] == {"parse_failure": 1}
    assert withheld["pending_cells"] == missing["pending_cells"]
    assert withheld["claims"]["C2"][runs.FAMILY]["passed"] is None
    haiku.with_name("run-1--a2.json").unlink()

    # A different valid majority changes the grade.
    runs._vote(grading, "haiku", item, runs._vote_text(contradicts_key=True), attempt=2)
    changed = replay()
    assert runs._record(run, system, qid)["score"] == 0.0
    assert changed["pending_cells"] == []
    haiku.with_name("run-1--a2.json").unlink()

    # Restoring the original bytes reproduces the complete summary and provenance.
    haiku.write_bytes(original)
    assert replay() == complete
    assert replay() == complete
    assert runs._record(run, system, qid)["grading"] == record["grading"]
    assert json.loads((run / "adjudication" / "queue.json").read_bytes())["cells"] == []


def test_fixed_flow_partial_readjudication_preserves_other_verdicts(tmp_path: Path) -> None:
    first, second = runs.QUESTIONS
    run = scoring._run(tmp_path, {first: "first answer", second: "second answer"}, mode="fixed")
    args = ["--run-dir", str(run), "--schema", str(runs.SCHEMA)]
    verdicts = tmp_path / "verdicts.json"
    adjudicate = ["adjudicate", *args, "--verdicts", str(verdicts)]
    assert main(["score", *args]) == 0
    rows = [{"competitor_id": scoring.CELL, "corpus_id": q, "score": 1.0} for q in runs.QUESTIONS]
    runs._json(verdicts, {"verdicts": rows})
    assert main(adjudicate) == 0
    other = scoring._record(run, second)
    runs._json(verdicts, {"verdicts": [{**rows[0], "score": 0.5}]})
    assert main(adjudicate) == 0
    assert main(["score", *args]) == 0
    assert scoring._record(run, first)["score"] == 0.5
    assert scoring._record(run, second) == other
    assert other["scoring_method"] == "human"
    assert json.loads((run / "adjudication" / "queue.json").read_bytes())["cells"] == []
