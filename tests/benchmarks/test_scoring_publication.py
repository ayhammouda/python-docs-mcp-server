"""Scoring refuses synthetic key shapes before changing a previously good export."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from benchmarks.__main__ import main
from benchmarks.agentic.protocol import KeyLeakError
from benchmarks.grading.score import score_run_dir
from benchmarks.scoring import apply_llm_grades, ingest_adjudication_verdicts, score_run
from tests.benchmarks import test_grading_score as runs
from tests.benchmarks import test_scoring_llm as cells

# Repository-style dummy Google-key marker, never a credential or a host input.
MARKER = "AIza" + "g" * 35
FIRST, SECOND = runs.QUESTIONS


def _snapshot(run: Path) -> dict[str, tuple[bytes, int]]:
    return {
        p.relative_to(run).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in run.rglob("*")
        if p.is_file()
    }


def _good_run(tmp_path: Path) -> Path:
    run = cells._run(tmp_path, {FIRST: "ordinary answer", SECOND: "another answer"})
    score_run(run, schema_path=cells.SCHEMA)
    apply_llm_grades(
        run, {(cells.CELL, FIRST): cells._grade(1.0)}, grounded={}, schema_path=cells.SCHEMA
    )
    return run


@pytest.mark.parametrize("field", ["adjudicator", "grading", "label", "method", "score"])
def test_direct_grade_refusal_preserves_all_previous_files(
    tmp_path: Path, field: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run = _good_run(tmp_path)
    grades = {(cells.CELL, q): cells._grade(0.5) for q in runs.QUESTIONS}
    grades[cells.CELL, SECOND][field] = MARKER
    before = _snapshot(run)
    for _ in range(2):
        with pytest.raises(KeyLeakError) as caught:
            apply_llm_grades(run, grades, grounded={}, schema_path=cells.SCHEMA)
        assert MARKER not in str(caught.value)
        assert _snapshot(run) == before
    assert MARKER not in str(capsys.readouterr())
    grades[cells.CELL, SECOND] = cells._grade(0.5)
    result = apply_llm_grades(run, grades, grounded={}, schema_path=cells.SCHEMA)
    assert result["llm_graded_cells"] == 2 and result["pending_cells"] == 0
    assert cells._record(run, FIRST)["score"] == cells._record(run, SECOND)["score"] == 0.5


def _legacy_bytes(record: dict[str, Any], encoding: str) -> bytes:
    record = {**record, "adjudicator": MARKER}
    text = json.dumps(record)
    if encoding != "plain":
        text = text.replace(MARKER, r"\u0041" + MARKER[1:])
    if encoding == "duplicate":
        # The dangerous first value disappears under ordinary dict construction.
        text = text[:-1] + ', "adjudicator": "ordinary legacy reviewer"}'
    elif encoding == "malformed":
        text = "broken\n" + text
    return text.encode()


@pytest.mark.parametrize("entry", ["score", "apply"])
@pytest.mark.parametrize("encoding", ["plain", "escaped", "duplicate", "malformed"])
@pytest.mark.parametrize("method", ["human", "llm_reference", None])
def test_legacy_scoring_refusal_precedes_every_write(
    tmp_path: Path,
    entry: str,
    encoding: str,
    method: str | None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run = _good_run(tmp_path)
    path = run / "scoring" / cells.CELL / f"{SECOND}.json"
    original = path.read_bytes()
    record = json.loads(original)
    if method is not None:
        record.update(scoring_method=method, score=0.5, status="scored")
    path.write_bytes(_legacy_bytes(record, encoding))
    before = _snapshot(run)
    for _ in range(2):
        with pytest.raises(KeyLeakError) as caught:
            if entry == "score":
                score_run(run, schema_path=cells.SCHEMA, current_llm_cells=set())
            else:
                apply_llm_grades(
                    run,
                    {(cells.CELL, FIRST): cells._grade(0.5)},
                    grounded={},
                    schema_path=cells.SCHEMA,
                )
        assert MARKER not in str(caught.value)
        assert _snapshot(run) == before
    assert MARKER not in str(capsys.readouterr())
    path.write_bytes(original)
    score_run(run, schema_path=cells.SCHEMA)
    assert cells._record(run, FIRST)["score"] == 1.0
    assert cells._record(run, SECOND)["score"] is None


@pytest.mark.parametrize("encoding", ["plain", "escaped", "duplicate", "malformed"])
def test_agentic_score_refusal_preserves_summary_and_replay(
    tmp_path: Path, encoding: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run = runs._build_run(tmp_path)
    complete = score_run_dir(run, schema_path=runs.SCHEMA)
    path = run / "scoring" / runs._cell("python-docs") / f"{SECOND}.json"
    original = path.read_bytes()
    path.write_bytes(_legacy_bytes(json.loads(original), encoding))
    before = _snapshot(run)
    argv = ["agentic-score", "--run-dir", str(run)]
    for _ in range(2):
        with pytest.raises(SystemExit) as caught:
            main(argv)
        assert caught.value.code == 2
        output = capsys.readouterr()
        assert "key-shaped" in output.err and MARKER not in str(output)
        assert _snapshot(run) == before
    path.write_bytes(original)
    assert score_run_dir(run, schema_path=runs.SCHEMA) == complete
    assert json.loads((run / "adjudication/queue.json").read_bytes())["cells"] == []


def test_fixed_flow_manual_metadata_and_verdict_preservation(tmp_path: Path) -> None:
    run = cells._run(tmp_path, {FIRST: "ordinary answer"}, mode="fixed")
    score_run(run, schema_path=cells.SCHEMA)
    verdicts = tmp_path / "verdicts.json"
    cells._json(
        verdicts,
        {
            "verdicts": [
                {
                    "competitor_id": cells.CELL,
                    "corpus_id": FIRST,
                    "score": 1.0,
                    "adjudicator": MARKER,
                    "notes": "synthetic legacy manual metadata",
                }
            ]
        },
    )
    ingest_adjudication_verdicts(run, verdicts, schema_path=cells.SCHEMA)
    path = run / "scoring" / cells.CELL / f"{FIRST}.json"
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    score_run(run, schema_path=cells.SCHEMA)
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert cells._record(run, FIRST)["adjudicator"] == MARKER
    assert cells._record(run, FIRST)["scoring_method"] == "human"
