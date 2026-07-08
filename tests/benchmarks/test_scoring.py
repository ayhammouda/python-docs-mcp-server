"""Tests for benchmarks/scoring.py (issue #88, refs #63).

Covers the correctness scorer's plumbing-only contract: failed-cell
passthrough at 0.0, the narrow automatic decision (empty/blank answer only),
adjudication-queue emission with answer-key context, human-verdict ingest
(including the never-overwrite-a-human-verdict guarantee), the
correct-but-ungrounded flag, per-category rollups, and denominator
invariance (every scoring placeholder file is accounted for).

Uses the reused synthetic fixture corpus
(tests/benchmarks/fixtures/corpus.sample.yml, from issue #94/#97) as the
answer-key source and hand-authored scoring/transcript records (mirroring
tests/benchmarks/test_report.py's `_minimal_valid_run_dir` pattern) as the
run artifacts -- no real corpus questions or LLM-as-judge calls are used
anywhere in this file.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from benchmarks.runner import BenchmarkValidationError
from benchmarks.scoring import ingest_adjudication_verdicts, score_run

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_SCHEMA_PATH = REPO_ROOT / "docs" / "benchmarks" / "corpus.schema.json"
REAL_FIXTURE_PATH = REPO_ROOT / "tests" / "benchmarks" / "fixtures" / "corpus.sample.yml"


def _scoring_record(
    *,
    competitor_id: str,
    corpus_id: str,
    tool_model_key: str | None = None,
    status: str = "placeholder",
    score: float | None = None,
    requires_manual_scoring: bool = True,
    error_category: str | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Mirror benchmarks.runner._scoring_record's placeholder/failed shapes."""
    return {
        "competitor_id": competitor_id,
        "tool_model_key": tool_model_key or competitor_id,
        "corpus_id": corpus_id,
        "included_in_correctness_denominator": True,
        "denominator_unit": "corpus_query",
        "error_category": error_category,
        "error": error,
        "status": status,
        "score": score,
        "requires_manual_scoring": requires_manual_scoring,
        "notes": "test fixture placeholder",
    }


def _transcript_record(
    *,
    competitor_id: str,
    corpus_id: str,
    answer: str = "",
    tool_calls: list[dict[str, Any]] | None = None,
    status: str = "succeeded",
) -> dict[str, Any]:
    """Mirror benchmarks.runner._execute_cell's transcript record shape."""
    return {
        "competitor_id": competitor_id,
        "tool_model_key": competitor_id,
        "corpus_id": corpus_id,
        "adapter": "fake",
        "status": status,
        "started_at": "2026-07-08T00:00:00Z",
        "completed_at": "2026-07-08T00:00:01Z",
        "messages": [{"role": "user", "content": "test prompt"}],
        "answer": answer,
        "tool_calls": tool_calls,
        "error": None,
        "external_provider_calls": False,
    }


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _build_run_dir(
    tmp_path: Path, cells: list[tuple[dict[str, Any], dict[str, Any]]]
) -> Path:
    run_dir = tmp_path / "run"
    for scoring, transcript in cells:
        competitor_id = scoring["competitor_id"]
        corpus_id = scoring["corpus_id"]
        _write_json(run_dir / "scoring" / competitor_id / f"{corpus_id}.json", scoring)
        _write_json(run_dir / "transcripts" / competitor_id / f"{corpus_id}.json", transcript)
    return run_dir


def _write_verdicts(path: Path, verdicts: list[dict[str, Any]]) -> Path:
    path.write_text(json.dumps({"verdicts": verdicts}), encoding="utf-8")
    return path


def _score(run_dir: Path) -> dict[str, Any]:
    return score_run(run_dir, corpus_path=REAL_FIXTURE_PATH, schema_path=REAL_SCHEMA_PATH)


def _adjudicate(run_dir: Path, verdicts_path: Path) -> dict[str, Any]:
    return ingest_adjudication_verdicts(
        run_dir, verdicts_path, corpus_path=REAL_FIXTURE_PATH, schema_path=REAL_SCHEMA_PATH
    )


def _read_scoring(run_dir: Path, competitor_id: str, corpus_id: str) -> dict[str, Any]:
    path = run_dir / "scoring" / competitor_id / f"{corpus_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _read_queue(run_dir: Path) -> dict[str, Any]:
    return json.loads((run_dir / "adjudication" / "queue.json").read_text(encoding="utf-8"))


# --- Failed-cell passthrough --------------------------------------------------


def test_score_run_passes_failed_cells_through_at_zero(tmp_path: Path) -> None:
    scoring = _scoring_record(
        competitor_id="tool-a",
        corpus_id="SYN-EX-001",
        status="failed",
        score=0.0,
        requires_manual_scoring=False,
        error_category="timeout",
        error={"category": "timeout", "type": "TimeoutError", "message": "boom"},
    )
    transcript = _transcript_record(
        competitor_id="tool-a", corpus_id="SYN-EX-001", answer="", status="failed"
    )
    run_dir = _build_run_dir(tmp_path, [(scoring, transcript)])

    result = _score(run_dir)

    updated = _read_scoring(run_dir, "tool-a", "SYN-EX-001")
    assert updated["status"] == "failed"
    assert updated["score"] == 0.0
    assert updated["requires_manual_scoring"] is False
    assert updated["scoring_method"] == "automatic"
    assert updated["correct_but_ungrounded"] is False
    assert updated["included_in_correctness_denominator"] is True
    assert updated["error_category"] == "timeout"
    assert result["automatic_decided_cells"] == 1
    assert result["queued_for_adjudication_cells"] == 0
    assert _read_queue(run_dir)["cells"] == []


# --- Narrow automatic decision: empty/blank answer only ----------------------


def test_score_run_auto_decides_blank_answer_as_zero(tmp_path: Path) -> None:
    scoring = _scoring_record(competitor_id="tool-a", corpus_id="SYN-EX-001")
    transcript = _transcript_record(competitor_id="tool-a", corpus_id="SYN-EX-001", answer="   ")
    run_dir = _build_run_dir(tmp_path, [(scoring, transcript)])

    result = _score(run_dir)

    updated = _read_scoring(run_dir, "tool-a", "SYN-EX-001")
    assert updated["score"] == 0.0
    assert updated["requires_manual_scoring"] is False
    assert updated["scoring_method"] == "automatic"
    assert updated["automatic_decision_reason"] == "empty_or_blank_answer"
    assert updated["correct_but_ungrounded"] is False
    assert result["automatic_decided_cells"] == 1
    assert result["queued_for_adjudication_cells"] == 0


# --- Adjudication-queue emission ----------------------------------------------


def test_score_run_queues_nonempty_answers_with_answer_key_context(tmp_path: Path) -> None:
    scoring = _scoring_record(competitor_id="tool-a", corpus_id="SYN-EX-001")
    transcript = _transcript_record(
        competitor_id="tool-a", corpus_id="SYN-EX-001", answer="A candidate answer."
    )
    run_dir = _build_run_dir(tmp_path, [(scoring, transcript)])

    result = _score(run_dir)

    assert result["queued_for_adjudication_cells"] == 1
    updated = _read_scoring(run_dir, "tool-a", "SYN-EX-001")
    assert updated["score"] is None
    assert updated["requires_manual_scoring"] is True
    assert updated["queued_for_adjudication"] is True
    assert updated["grounding_evidence_present"] is False

    queue = _read_queue(run_dir)
    assert len(queue["cells"]) == 1
    entry = queue["cells"][0]
    assert entry["competitor_id"] == "tool-a"
    assert entry["corpus_id"] == "SYN-EX-001"
    assert entry["category"] == "exact_symbol"
    assert entry["answer"] == "A candidate answer."
    assert "SYNTHETIC FIXTURE" in entry["answer_key"]
    assert entry["citations"] == ["https://example.invalid/fixture-docs/ex/1#synthstdlib"]
    assert entry["expected_properties"] == ["mentions-synthetic-symbol", "cites-fixture-source"]
    assert entry["grounding_evidence_present"] is False


def test_score_run_detects_grounding_evidence_from_tool_calls(tmp_path: Path) -> None:
    scoring = _scoring_record(competitor_id="tool-a", corpus_id="SYN-EX-001")
    transcript = _transcript_record(
        competitor_id="tool-a",
        corpus_id="SYN-EX-001",
        answer="Grounded candidate answer.",
        tool_calls=[
            {"tool": "search_docs", "arguments": {}, "result": "some docs", "is_error": False}
        ],
    )
    run_dir = _build_run_dir(tmp_path, [(scoring, transcript)])

    _score(run_dir)

    updated = _read_scoring(run_dir, "tool-a", "SYN-EX-001")
    assert updated["grounding_evidence_present"] is True
    assert _read_queue(run_dir)["cells"][0]["grounding_evidence_present"] is True


# --- Per-category rollups + denominator invariance ----------------------------


def test_score_run_produces_per_category_rollups(tmp_path: Path) -> None:
    cells = [
        (
            _scoring_record(competitor_id="tool-a", corpus_id="SYN-EX-001"),
            _transcript_record(competitor_id="tool-a", corpus_id="SYN-EX-001", answer=""),
        ),
        (
            _scoring_record(competitor_id="tool-a", corpus_id="SYN-EX-002"),
            _transcript_record(
                competitor_id="tool-a", corpus_id="SYN-EX-002", answer="non-empty answer"
            ),
        ),
        (
            _scoring_record(
                competitor_id="tool-a",
                corpus_id="SYN-CN-001",
                status="failed",
                score=0.0,
                requires_manual_scoring=False,
                error_category="tool_failure",
            ),
            _transcript_record(
                competitor_id="tool-a", corpus_id="SYN-CN-001", answer="", status="failed"
            ),
        ),
    ]
    run_dir = _build_run_dir(tmp_path, cells)

    result = _score(run_dir)
    rollups = result["rollups"]

    assert rollups["overall"]["total_cells"] == 3
    assert rollups["overall"]["scored_cells"] == 2  # blank-answer 0.0 + failed 0.0
    assert rollups["overall"]["pending_manual_scoring_cells"] == 1
    assert rollups["overall"]["failed_cells"] == 1

    assert rollups["by_category"]["exact_symbol"]["total_cells"] == 2
    assert rollups["by_category"]["exact_symbol"]["scored_cells"] == 1
    assert rollups["by_category"]["exact_symbol"]["pending_manual_scoring_cells"] == 1
    assert rollups["by_category"]["concept"]["total_cells"] == 1
    assert rollups["by_category"]["concept"]["failed_cells"] == 1

    on_disk = json.loads((run_dir / "scoring-rollups.json").read_text(encoding="utf-8"))
    assert on_disk == rollups


def test_score_run_denominator_includes_every_scoring_file(tmp_path: Path) -> None:
    cells = [
        (
            _scoring_record(
                competitor_id="tool-a",
                corpus_id="SYN-EX-001",
                status="failed",
                score=0.0,
                requires_manual_scoring=False,
            ),
            _transcript_record(
                competitor_id="tool-a", corpus_id="SYN-EX-001", answer="", status="failed"
            ),
        ),
        (
            _scoring_record(competitor_id="tool-a", corpus_id="SYN-EX-002"),
            _transcript_record(competitor_id="tool-a", corpus_id="SYN-EX-002", answer=""),
        ),
        (
            _scoring_record(competitor_id="tool-a", corpus_id="SYN-CN-001"),
            _transcript_record(
                competitor_id="tool-a", corpus_id="SYN-CN-001", answer="pending answer one"
            ),
        ),
        (
            _scoring_record(competitor_id="tool-a", corpus_id="SYN-XV-001"),
            _transcript_record(
                competitor_id="tool-a", corpus_id="SYN-XV-001", answer="pending answer two"
            ),
        ),
    ]
    run_dir = _build_run_dir(tmp_path, cells)

    result = _score(run_dir)

    assert result["total_cells"] == 4
    assert (
        result["automatic_decided_cells"]
        + result["queued_for_adjudication_cells"]
        + result["already_human_scored_cells"]
        == 4
    )
    assert result["rollups"]["overall"]["total_cells"] == 4


# --- Human-verdict ingest + correct-but-ungrounded flag -----------------------


def test_ingest_adjudication_verdicts_applies_score_and_derives_ungrounded_flag(
    tmp_path: Path,
) -> None:
    scoring = _scoring_record(competitor_id="tool-a", corpus_id="SYN-EX-001")
    transcript = _transcript_record(
        competitor_id="tool-a", corpus_id="SYN-EX-001", answer="Looks plausible."
    )
    run_dir = _build_run_dir(tmp_path, [(scoring, transcript)])
    _score(run_dir)

    verdicts_path = _write_verdicts(
        tmp_path / "verdicts.json",
        [
            {
                "competitor_id": "tool-a",
                "corpus_id": "SYN-EX-001",
                "score": 1.0,
                "adjudicator": "reviewer-1",
                "notes": "matches answer key",
            }
        ],
    )

    result = _adjudicate(run_dir, verdicts_path)

    updated = _read_scoring(run_dir, "tool-a", "SYN-EX-001")
    assert updated["score"] == 1.0
    assert updated["requires_manual_scoring"] is False
    assert updated["scoring_method"] == "human"
    # No tool_calls were recorded -- the answer "looks correct" but has no
    # grounding evidence, so the ungrounded flag is derived True without
    # changing the score itself.
    assert updated["correct_but_ungrounded"] is True
    assert updated["adjudicator"] == "reviewer-1"
    assert updated["adjudication_notes"] == "matches answer key"
    assert result["verdicts_applied"] == 1
    assert result["remaining_queued_cells"] == 0
    assert _read_queue(run_dir)["cells"] == []


def test_ingest_adjudication_verdicts_supports_partial_score_and_grounded_flag(
    tmp_path: Path,
) -> None:
    scoring = _scoring_record(competitor_id="tool-a", corpus_id="SYN-EX-001")
    transcript = _transcript_record(
        competitor_id="tool-a",
        corpus_id="SYN-EX-001",
        answer="Partially right.",
        tool_calls=[{"tool": "search_docs", "arguments": {}, "result": "docs", "is_error": False}],
    )
    run_dir = _build_run_dir(tmp_path, [(scoring, transcript)])
    _score(run_dir)

    verdicts_path = _write_verdicts(
        tmp_path / "verdicts.json",
        [{"competitor_id": "tool-a", "corpus_id": "SYN-EX-001", "score": 0.5}],
    )
    _adjudicate(run_dir, verdicts_path)

    updated = _read_scoring(run_dir, "tool-a", "SYN-EX-001")
    assert updated["score"] == 0.5
    assert updated["correct_but_ungrounded"] is False


def test_score_run_never_overwrites_a_human_verdict(tmp_path: Path) -> None:
    scoring = _scoring_record(competitor_id="tool-a", corpus_id="SYN-EX-001")
    transcript = _transcript_record(
        competitor_id="tool-a", corpus_id="SYN-EX-001", answer="Some answer."
    )
    run_dir = _build_run_dir(tmp_path, [(scoring, transcript)])
    _score(run_dir)

    verdicts_path = _write_verdicts(
        tmp_path / "verdicts.json",
        [
            {
                "competitor_id": "tool-a",
                "corpus_id": "SYN-EX-001",
                "score": 1.0,
                "adjudicator": "reviewer-1",
            }
        ],
    )
    _adjudicate(run_dir, verdicts_path)
    before = _read_scoring(run_dir, "tool-a", "SYN-EX-001")

    result = _score(run_dir)

    after = _read_scoring(run_dir, "tool-a", "SYN-EX-001")
    assert after == before
    assert result["already_human_scored_cells"] == 1
    assert result["automatic_decided_cells"] == 0
    assert result["queued_for_adjudication_cells"] == 0
    assert result["rollups"]["overall"]["human_scored_cells"] == 1


# --- Ingest refusals -----------------------------------------------------------


def test_ingest_rejects_invalid_score(tmp_path: Path) -> None:
    scoring = _scoring_record(competitor_id="tool-a", corpus_id="SYN-EX-001")
    transcript = _transcript_record(competitor_id="tool-a", corpus_id="SYN-EX-001", answer="ans")
    run_dir = _build_run_dir(tmp_path, [(scoring, transcript)])
    _score(run_dir)

    verdicts_path = _write_verdicts(
        tmp_path / "verdicts.json",
        [{"competitor_id": "tool-a", "corpus_id": "SYN-EX-001", "score": 0.7}],
    )

    with pytest.raises(BenchmarkValidationError, match="invalid score"):
        _adjudicate(run_dir, verdicts_path)


def test_ingest_rejects_verdict_for_failed_cell(tmp_path: Path) -> None:
    scoring = _scoring_record(
        competitor_id="tool-a",
        corpus_id="SYN-EX-001",
        status="failed",
        score=0.0,
        requires_manual_scoring=False,
        error_category="timeout",
    )
    transcript = _transcript_record(
        competitor_id="tool-a", corpus_id="SYN-EX-001", answer="", status="failed"
    )
    run_dir = _build_run_dir(tmp_path, [(scoring, transcript)])
    _score(run_dir)

    verdicts_path = _write_verdicts(
        tmp_path / "verdicts.json",
        [{"competitor_id": "tool-a", "corpus_id": "SYN-EX-001", "score": 1.0}],
    )

    with pytest.raises(BenchmarkValidationError, match="failed cell"):
        _adjudicate(run_dir, verdicts_path)


def test_ingest_rejects_verdict_with_no_matching_scoring_record(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    verdicts_path = _write_verdicts(
        tmp_path / "verdicts.json",
        [{"competitor_id": "tool-a", "corpus_id": "SYN-EX-001", "score": 1.0}],
    )

    with pytest.raises(BenchmarkValidationError, match="no scoring record exists"):
        _adjudicate(run_dir, verdicts_path)


def test_ingest_rejects_malformed_verdicts_file(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    verdicts_path = tmp_path / "verdicts.json"
    verdicts_path.write_text(
        json.dumps({"verdicts": [{"competitor_id": "tool-a"}]}), encoding="utf-8"
    )

    with pytest.raises(BenchmarkValidationError, match="missing required field"):
        _adjudicate(run_dir, verdicts_path)


# --- score_run refusals --------------------------------------------------------


def test_score_run_refuses_when_no_scoring_files(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    with pytest.raises(BenchmarkValidationError, match="no scoring placeholder files"):
        _score(run_dir)


def test_score_run_raises_when_transcript_missing(tmp_path: Path) -> None:
    scoring = _scoring_record(competitor_id="tool-a", corpus_id="SYN-EX-001")
    run_dir = tmp_path / "run"
    _write_json(run_dir / "scoring" / "tool-a" / "SYN-EX-001.json", scoring)

    with pytest.raises(BenchmarkValidationError, match="no matching transcript"):
        _score(run_dir)


def test_score_run_raises_when_corpus_id_missing_from_answer_key_corpus(tmp_path: Path) -> None:
    scoring = _scoring_record(competitor_id="tool-a", corpus_id="NOT-IN-FIXTURE")
    transcript = _transcript_record(
        competitor_id="tool-a", corpus_id="NOT-IN-FIXTURE", answer="some answer"
    )
    run_dir = _build_run_dir(tmp_path, [(scoring, transcript)])

    with pytest.raises(BenchmarkValidationError, match="not present in the answer-key corpus"):
        _score(run_dir)


# --- CLI subcommands -----------------------------------------------------------


def test_cli_score_subcommand_writes_rollups_and_queue(tmp_path: Path) -> None:
    scoring = _scoring_record(competitor_id="tool-a", corpus_id="SYN-EX-001")
    transcript = _transcript_record(
        competitor_id="tool-a", corpus_id="SYN-EX-001", answer="candidate answer"
    )
    run_dir = _build_run_dir(tmp_path, [(scoring, transcript)])

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks",
            "score",
            "--run-dir",
            str(run_dir),
            "--corpus",
            str(REAL_FIXTURE_PATH),
            "--schema",
            str(REAL_SCHEMA_PATH),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["queued_for_adjudication_cells"] == 1
    assert (run_dir / "adjudication" / "queue.json").is_file()
    assert (run_dir / "scoring-rollups.json").is_file()


def test_cli_adjudicate_subcommand_applies_verdicts(tmp_path: Path) -> None:
    scoring = _scoring_record(competitor_id="tool-a", corpus_id="SYN-EX-001")
    transcript = _transcript_record(
        competitor_id="tool-a", corpus_id="SYN-EX-001", answer="candidate answer"
    )
    run_dir = _build_run_dir(tmp_path, [(scoring, transcript)])
    _score(run_dir)

    verdicts_path = _write_verdicts(
        tmp_path / "verdicts.json",
        [{"competitor_id": "tool-a", "corpus_id": "SYN-EX-001", "score": 1.0}],
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks",
            "adjudicate",
            "--run-dir",
            str(run_dir),
            "--verdicts",
            str(verdicts_path),
            "--corpus",
            str(REAL_FIXTURE_PATH),
            "--schema",
            str(REAL_SCHEMA_PATH),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["verdicts_applied"] == 1
    updated = _read_scoring(run_dir, "tool-a", "SYN-EX-001")
    assert updated["score"] == 1.0
    assert updated["scoring_method"] == "human"


def test_cli_score_exits_nonzero_with_clean_message_on_missing_run_dir(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks",
            "score",
            "--run-dir",
            str(tmp_path / "does-not-exist"),
            "--corpus",
            str(REAL_FIXTURE_PATH),
            "--schema",
            str(REAL_SCHEMA_PATH),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 2
    assert "no scoring placeholder files found" in result.stderr
    assert "Traceback" not in result.stderr
