"""Correctness scorer with manual-adjudication hooks (issue #88, refs #63).

Turns a run's scoring placeholders (written by ``benchmarks.runner``, issue
#75: ``scoring/<competitor_id>/<corpus_id>.json`` with ``score: None`` +
``requires_manual_scoring: True`` for succeeded cells, ``score: 0.0`` +
``requires_manual_scoring: False`` for failed cells) into finalized records
by applying PUBLIC-BENCHMARK-METHODOLOGY.md's "Correctness Scoring" rubric
against the answer-key corpus (``benchmarks.corpus.validate_corpus``, issue
#94/#97 -- reused here rather than duplicated).

**This module is plumbing only.** Real answers still require a human to
judge whether a candidate answer is actually correct against the answer key
-- that judgment is explicitly out of scope for an autonomous agent (see
issue #88: "Actual scoring of real answers stays human-adjudicated"). The
automatic pass (:func:`score_run`) therefore only ever decides the two
cases that require no semantic judgment at all:

1. A failed cell (the runner already fixed this at ``score: 0.0``,
   "materially incomplete" under the rubric by definition of having failed).
2. A succeeded cell whose answer is empty or whitespace-only -- also
   "materially incomplete" under the rubric with no interpretation needed.

Every other succeeded cell is left undecided and written to an adjudication
queue file (``<run-dir>/adjudication/queue.json``) with the answer, any
recorded tool calls, and the corpus answer key/citations/expected
properties, so a human can apply the rubric and score it ``{1.0, 0.5,
0.0}``. :func:`ingest_adjudication_verdicts` reads a human-authored verdicts
file and writes those scores back as finalized records -- once a record
carries ``scoring_method: "human"``, a later :func:`score_run` call always
skips it (a human verdict is never overwritten by the automatic pass).

Grounding: the methodology also asks that an answer which "appears correct
but lacks evidence from the supplied docs" be flagged and reported
separately from its score. This module computes a purely structural
``grounding_evidence_present`` fact (did the transcript record at least one
successful tool call?) for every cell, and combines it with a decided score
to set ``correct_but_ungrounded`` (true only when the score is > 0.0 and no
grounding evidence was recorded). That flag never changes the score itself;
it is reported as a separate field, exactly as the methodology asks.

Denominator: failed cells stay in every rollup at ``score: 0.0`` and every
scoring placeholder file already written by the runner is processed here --
none are filtered out -- so the per-category/per-tool-model denominators
computed by this module (and by ``benchmarks.report``, which reads the same
``scoring/`` files directly) always match the runner's full corpus count
per ``run-summary.json``'s ``correctness_denominator_cells`` /
``failed_cells_included_in_correctness_denominator: True``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from benchmarks.corpus import CorpusQuestion, validate_corpus
from benchmarks.runner import BenchmarkValidationError

#: The methodology's closed correctness score vocabulary
#: (PUBLIC-BENCHMARK-METHODOLOGY.md "Correctness Scoring"). No finalized
#: record's ``score`` is ever set to any other numeric value.
VALID_SCORES = (1.0, 0.5, 0.0)

#: ``scoring_method`` provenance tags. ``score_run`` never overwrites a
#: record already tagged ``SCORING_METHOD_HUMAN``.
SCORING_METHOD_AUTOMATIC = "automatic"
SCORING_METHOD_HUMAN = "human"

_SCORING_REQUIRED_FIELDS = ("competitor_id", "corpus_id", "status")
_TRANSCRIPT_REQUIRED_FIELDS = ("competitor_id", "corpus_id", "status")
_VERDICT_REQUIRED_FIELDS = ("competitor_id", "corpus_id", "score")

_QUEUE_FILENAME = "queue.json"
_ROLLUPS_FILENAME = "scoring-rollups.json"


@dataclass(frozen=True)
class ScoringPaths:
    """Well-known artifact paths this module reads/writes under a run dir."""

    run_dir: Path

    @property
    def scoring_dir(self) -> Path:
        return self.run_dir / "scoring"

    @property
    def transcripts_dir(self) -> Path:
        return self.run_dir / "transcripts"

    @property
    def queue_path(self) -> Path:
        return self.run_dir / "adjudication" / _QUEUE_FILENAME

    @property
    def rollups_path(self) -> Path:
        return self.run_dir / _ROLLUPS_FILENAME


@dataclass(frozen=True)
class _AutomaticDecision:
    score: float
    reason: str


def score_run(run_dir: Path, *, corpus_path: Path, schema_path: Path) -> dict[str, Any]:
    """Apply the automatic correctness-scoring pass to one run directory.

    Reads every ``scoring/<competitor_id>/<corpus_id>.json`` placeholder
    plus the matching ``transcripts/`` record, and the answer-key corpus
    (validated via :func:`benchmarks.corpus.validate_corpus`). For each
    cell:

    - A record already carrying a human verdict (``scoring_method:
      "human"``) is left untouched.
    - A failed cell is re-emitted with its existing ``score: 0.0`` (see
      module docstring; this is a passthrough, not a new decision).
    - A succeeded cell with an empty/blank answer is automatically scored
      ``0.0``.
    - Every other succeeded cell is left with ``score: None`` /
      ``requires_manual_scoring: True`` and added to the adjudication
      queue file for a human (:func:`ingest_adjudication_verdicts`).

    Writes the (possibly updated) scoring records back in place, the
    adjudication queue file, and a per-category/per-tool-model rollup file.
    Raises ``BenchmarkValidationError`` (clean message, no traceback) if the
    run has no scoring files yet, a scoring record has no matching
    transcript, or a scoring record references a corpus id absent from the
    answer-key corpus (the issue #88 recovery clause: this never invents a
    missing answer key, it refuses instead).

    Idempotent: re-running after a partial adjudication ingest recomputes
    the same automatic decisions and leaves every human-scored record
    exactly as ingested.
    """
    answer_keys = _load_answer_keys(corpus_path, schema_path)
    paths = ScoringPaths(run_dir)

    scoring_records = _load_cell_dir(paths.scoring_dir, _SCORING_REQUIRED_FIELDS, "scoring")
    if not scoring_records:
        raise BenchmarkValidationError(
            f"no scoring placeholder files found under {paths.scoring_dir}; run the "
            "benchmark runner first (`python -m benchmarks run`)"
        )
    transcript_records = _load_cell_dir(
        paths.transcripts_dir, _TRANSCRIPT_REQUIRED_FIELDS, "transcript"
    )

    finalized: list[dict[str, Any]] = []
    queue_entries: list[dict[str, Any]] = []
    automatic_decided = 0
    already_human = 0
    queued = 0

    for key in sorted(scoring_records):
        path, record = scoring_records[key]
        competitor_id, corpus_id = key

        if record.get("scoring_method") == SCORING_METHOD_HUMAN:
            already_human += 1
            finalized.append(record)
            continue

        if record.get("status") == "failed":
            updated = _finalize_failed(record)
            _write_json(path, updated)
            finalized.append(updated)
            automatic_decided += 1
            continue

        transcript_entry = transcript_records.get(key)
        if transcript_entry is None:
            raise BenchmarkValidationError(
                f"scoring record {path} has no matching transcript at "
                f"{paths.transcripts_dir / competitor_id / f'{corpus_id}.json'}"
            )
        _, transcript = transcript_entry
        answer = transcript.get("answer") or ""
        grounding_evidence_present = _has_grounding_evidence(transcript.get("tool_calls"))

        if corpus_id not in answer_keys:
            raise BenchmarkValidationError(
                f"scoring record {path} references corpus id {corpus_id!r}, which is not "
                f"present in the answer-key corpus {corpus_path} -- if the corpus schema "
                "is missing a field the rubric needs, stop and comment on issue #88 rather "
                "than inventing one (see the issue's recovery clause); otherwise re-run "
                "against the correct answer-key corpus file"
            )

        decision = _automatic_decision(answer)
        if decision is not None:
            updated = _finalize_automatic(
                record,
                score=decision.score,
                reason=decision.reason,
                grounding_evidence_present=grounding_evidence_present,
            )
            _write_json(path, updated)
            finalized.append(updated)
            automatic_decided += 1
            continue

        pending = _annotate_pending(record, grounding_evidence_present=grounding_evidence_present)
        _write_json(path, pending)
        finalized.append(pending)
        queued += 1
        queue_entries.append(_build_queue_entry(pending, answer_keys, paths.transcripts_dir))

    _write_json(
        paths.queue_path,
        {"run_dir": str(run_dir), "generated_at": _utc_now(), "cells": queue_entries},
    )
    rollups = _compute_rollups(finalized, answer_keys)
    _write_json(paths.rollups_path, rollups)

    return {
        "run_dir": str(run_dir),
        "total_cells": len(scoring_records),
        "automatic_decided_cells": automatic_decided,
        "already_human_scored_cells": already_human,
        "queued_for_adjudication_cells": queued,
        "queue_path": str(paths.queue_path),
        "rollups_path": str(paths.rollups_path),
        "rollups": rollups,
    }


def ingest_adjudication_verdicts(
    run_dir: Path, verdicts_path: Path, *, corpus_path: Path, schema_path: Path
) -> dict[str, Any]:
    """Ingest human verdicts for queued cells and re-emit final scoring records.

    ``verdicts_path`` is a JSON or YAML file shaped like::

        verdicts:
          - competitor_id: some-tool
            corpus_id: Q-001
            score: 1.0                # required, must be one of {1.0, 0.5, 0.0}
            correct_but_ungrounded: false  # optional; auto-derived if omitted
            adjudicator: "human-reviewer-handle"  # optional
            notes: "matches answer key, cites the right section"  # optional

    Human verdicts always win: applying a verdict overwrites whatever
    :func:`score_run` previously wrote for that cell (including a
    still-pending placeholder), and the resulting record is tagged
    ``scoring_method: "human"`` so a later :func:`score_run` call will never
    touch it again. Refuses (``BenchmarkValidationError``) a verdict for a
    cell with no existing scoring record, an invalid score, or a failed
    cell (failed cells are already fixed at ``0.0`` by the runner and are
    not adjudicated).

    After applying every verdict, recomputes and rewrites the adjudication
    queue file (dropping now-resolved cells) and the rollup file, so both
    stay consistent with the latest finalized records.
    """
    answer_keys = _load_answer_keys(corpus_path, schema_path)
    paths = ScoringPaths(run_dir)
    verdicts = _load_verdicts(verdicts_path)

    applied = 0
    for verdict in verdicts:
        competitor_id = verdict["competitor_id"]
        corpus_id = verdict["corpus_id"]
        score = verdict["score"]
        if score not in VALID_SCORES:
            raise BenchmarkValidationError(
                f"verdict for {competitor_id}/{corpus_id} has invalid score {score!r}; "
                f"must be one of {VALID_SCORES}"
            )

        record_path = paths.scoring_dir / competitor_id / f"{corpus_id}.json"
        if not record_path.is_file():
            raise BenchmarkValidationError(
                f"verdict references {competitor_id}/{corpus_id}, but no scoring record "
                f"exists at {record_path}"
            )
        record = _read_json(record_path)
        if record.get("status") == "failed":
            raise BenchmarkValidationError(
                f"cannot adjudicate {competitor_id}/{corpus_id}: it is a failed cell, "
                "already fixed at score 0.0 by the runner"
            )

        transcript_path = paths.transcripts_dir / competitor_id / f"{corpus_id}.json"
        grounding_evidence_present = bool(record.get("grounding_evidence_present"))
        if transcript_path.is_file():
            transcript = _read_json(transcript_path)
            grounding_evidence_present = _has_grounding_evidence(transcript.get("tool_calls"))

        correct_but_ungrounded = verdict.get("correct_but_ungrounded")
        if correct_but_ungrounded is None:
            correct_but_ungrounded = score > 0.0 and not grounding_evidence_present

        updated = {
            **record,
            "status": "scored",
            "score": float(score),
            "requires_manual_scoring": False,
            "scoring_method": SCORING_METHOD_HUMAN,
            "grounding_evidence_present": grounding_evidence_present,
            "correct_but_ungrounded": bool(correct_but_ungrounded),
            "adjudicator": verdict.get("adjudicator"),
            "adjudication_notes": verdict.get("notes"),
            "queued_for_adjudication": False,
            "scored_at": _utc_now(),
        }
        _write_json(record_path, updated)
        applied += 1

    scoring_records = _load_cell_dir(paths.scoring_dir, _SCORING_REQUIRED_FIELDS, "scoring")
    finalized = [record for _, record in scoring_records.values()]
    remaining_queue = [
        record
        for record in finalized
        if record.get("score") is None and record.get("scoring_method") != SCORING_METHOD_HUMAN
    ]
    queue_entries = [
        _build_queue_entry(record, answer_keys, paths.transcripts_dir) for record in remaining_queue
    ]
    _write_json(
        paths.queue_path,
        {"run_dir": str(run_dir), "generated_at": _utc_now(), "cells": queue_entries},
    )

    rollups = _compute_rollups(finalized, answer_keys)
    _write_json(paths.rollups_path, rollups)

    return {
        "run_dir": str(run_dir),
        "verdicts_applied": applied,
        "remaining_queued_cells": len(remaining_queue),
        "queue_path": str(paths.queue_path),
        "rollups_path": str(paths.rollups_path),
        "rollups": rollups,
    }


# --- Automatic decision logic -------------------------------------------------


def _automatic_decision(answer: str) -> _AutomaticDecision | None:
    """Attempt to decide a succeeded cell's score without human judgment.

    Deliberately narrow (see module docstring, "plumbing only"): real
    correctness against an answer key requires semantic judgment that stays
    human. The only case decided here is structurally unambiguous -- an
    empty or whitespace-only answer, which the rubric's 0.0 tier
    ("materially incomplete") already covers with no interpretation
    required. Every other, non-empty answer is left undecided.
    """
    if not answer or not answer.strip():
        return _AutomaticDecision(score=0.0, reason="empty_or_blank_answer")
    return None


def _has_grounding_evidence(tool_calls: list[dict[str, Any]] | None) -> bool:
    """True if the transcript recorded at least one successful tool call.

    This is a structural fact about the transcript (a tool call happened
    and did not report an error, with a non-empty result payload) -- not a
    semantic judgment about whether that call's result actually supports
    the answer text. That judgment stays with the human adjudicator.
    """
    if not tool_calls:
        return False
    return any(
        isinstance(call, dict) and not call.get("is_error") and call.get("result")
        for call in tool_calls
    )


def _finalize_failed(record: dict[str, Any]) -> dict[str, Any]:
    """Re-emit a failed cell's record: passthrough at 0.0, tagged automatic."""
    return {
        **record,
        "status": "failed",
        "score": 0.0,
        "requires_manual_scoring": False,
        "scoring_method": SCORING_METHOD_AUTOMATIC,
        "grounding_evidence_present": False,
        "correct_but_ungrounded": False,
        "scored_at": _utc_now(),
    }


def _finalize_automatic(
    record: dict[str, Any], *, score: float, reason: str, grounding_evidence_present: bool
) -> dict[str, Any]:
    correct_but_ungrounded = score > 0.0 and not grounding_evidence_present
    return {
        **record,
        "status": "scored",
        "score": score,
        "requires_manual_scoring": False,
        "scoring_method": SCORING_METHOD_AUTOMATIC,
        "automatic_decision_reason": reason,
        "grounding_evidence_present": grounding_evidence_present,
        "correct_but_ungrounded": correct_but_ungrounded,
        "scored_at": _utc_now(),
    }


def _annotate_pending(
    record: dict[str, Any], *, grounding_evidence_present: bool
) -> dict[str, Any]:
    """Re-emit a still-undecided cell's placeholder for the adjudication queue.

    ``score``/``requires_manual_scoring`` stay exactly as the runner wrote
    them (``None`` / ``True``) so ``benchmarks.report`` keeps treating this
    cell as pending -- only informational fields are added.
    """
    return {
        **record,
        "score": None,
        "requires_manual_scoring": True,
        "scoring_method": None,
        "grounding_evidence_present": grounding_evidence_present,
        "correct_but_ungrounded": None,
        "queued_for_adjudication": True,
        "queued_at": _utc_now(),
    }


def _build_queue_entry(
    record: dict[str, Any], answer_keys: dict[str, CorpusQuestion], transcripts_dir: Path
) -> dict[str, Any]:
    competitor_id = record["competitor_id"]
    corpus_id = record["corpus_id"]
    transcript_path = transcripts_dir / competitor_id / f"{corpus_id}.json"
    answer = ""
    tool_calls: list[dict[str, Any]] | None = None
    if transcript_path.is_file():
        transcript = _read_json(transcript_path)
        answer = transcript.get("answer") or ""
        tool_calls = transcript.get("tool_calls")
    question = answer_keys.get(corpus_id)
    return {
        "competitor_id": competitor_id,
        "corpus_id": corpus_id,
        "tool_model_key": record.get("tool_model_key") or competitor_id,
        "category": question.category if question is not None else "uncategorized",
        "answer": answer,
        "tool_calls": tool_calls,
        "grounding_evidence_present": bool(record.get("grounding_evidence_present")),
        "answer_key": question.answer_key if question is not None else None,
        "citations": question.citations if question is not None else None,
        "expected_properties": question.expected_properties if question is not None else None,
        "ambiguity_notes": question.ambiguity_notes if question is not None else None,
        "reason": "non_empty_answer_requires_human_correctness_judgment",
    }


# --- Rollups -------------------------------------------------------------------


def _compute_rollups(
    records: list[dict[str, Any]], answer_keys: dict[str, CorpusQuestion]
) -> dict[str, Any]:
    by_category: dict[str, list[dict[str, Any]]] = {}
    by_tool_model: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        corpus_id = record.get("corpus_id")
        question = answer_keys.get(corpus_id) if isinstance(corpus_id, str) else None
        category = question.category if question is not None else "uncategorized"
        by_category.setdefault(category, []).append(record)
        tool_model_key = record.get("tool_model_key") or record.get("competitor_id")
        by_tool_model.setdefault(str(tool_model_key), []).append(record)

    return {
        "overall": _rollup_stats(records),
        "by_category": {
            category: _rollup_stats(cells) for category, cells in sorted(by_category.items())
        },
        "by_tool_model_key": {
            key: _rollup_stats(cells) for key, cells in sorted(by_tool_model.items())
        },
    }


def _rollup_stats(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    scored = [record["score"] for record in records if record.get("score") is not None]
    pending = sum(1 for record in records if record.get("requires_manual_scoring"))
    failed = sum(1 for record in records if record.get("status") == "failed")
    ungrounded = sum(1 for record in records if record.get("correct_but_ungrounded") is True)
    human_scored = sum(
        1 for record in records if record.get("scoring_method") == SCORING_METHOD_HUMAN
    )
    automatic_scored = sum(
        1 for record in records if record.get("scoring_method") == SCORING_METHOD_AUTOMATIC
    )
    return {
        "total_cells": total,
        "scored_cells": len(scored),
        "mean_score": (sum(scored) / len(scored)) if scored else None,
        "pending_manual_scoring_cells": pending,
        "failed_cells": failed,
        "correct_but_ungrounded_cells": ungrounded,
        "human_scored_cells": human_scored,
        "automatic_scored_cells": automatic_scored,
    }


# --- Loading and I/O helpers ---------------------------------------------------


def _load_answer_keys(corpus_path: Path, schema_path: Path) -> dict[str, CorpusQuestion]:
    result = validate_corpus(corpus_path, schema_path)
    return {question.id: question for question in result.questions}


def _load_cell_dir(
    base_dir: Path, required_fields: tuple[str, ...], label: str
) -> dict[tuple[str, str], tuple[Path, dict[str, Any]]]:
    records: dict[tuple[str, str], tuple[Path, dict[str, Any]]] = {}
    if not base_dir.is_dir():
        return records
    for competitor_dir in sorted(p for p in base_dir.iterdir() if p.is_dir()):
        for cell_file in sorted(competitor_dir.glob("*.json")):
            data = _read_json(cell_file)
            missing = [field for field in required_fields if field not in data]
            if missing:
                raise BenchmarkValidationError(
                    f"{label} file {cell_file} is missing required field(s): {missing}"
                )
            records[(data["competitor_id"], data["corpus_id"])] = (cell_file, data)
    return records


def _load_verdicts(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise BenchmarkValidationError(f"adjudication verdicts file does not exist: {path}")
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise BenchmarkValidationError(f"{path} is not valid JSON: {exc}") from exc
    else:
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise BenchmarkValidationError(f"{path} is not valid YAML: {exc}") from exc

    if not isinstance(data, dict) or not isinstance(data.get("verdicts"), list):
        raise BenchmarkValidationError(f"{path} must contain a mapping with a 'verdicts' list")

    verdicts: list[dict[str, Any]] = []
    for index, item in enumerate(data["verdicts"]):
        if not isinstance(item, dict):
            raise BenchmarkValidationError(f"verdict at index {index} must be a mapping")
        missing = [field for field in _VERDICT_REQUIRED_FIELDS if field not in item]
        if missing:
            raise BenchmarkValidationError(
                f"verdict at index {index} is missing required field(s): {missing}"
            )
        if not isinstance(item["competitor_id"], str) or not item["competitor_id"].strip():
            raise BenchmarkValidationError(
                f"verdict at index {index} must have a non-empty string 'competitor_id'"
            )
        if not isinstance(item["corpus_id"], str) or not item["corpus_id"].strip():
            raise BenchmarkValidationError(
                f"verdict at index {index} must have a non-empty string 'corpus_id'"
            )
        score = item["score"]
        is_valid_score = isinstance(score, (int, float)) and not isinstance(score, bool)
        if not is_valid_score:
            raise BenchmarkValidationError(
                f"verdict at index {index} 'score' must be a number, got {score!r}"
            )
        verdicts.append(item)
    return verdicts


def _read_json(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BenchmarkValidationError(f"could not read {path}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BenchmarkValidationError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise BenchmarkValidationError(f"{path} must contain a JSON object")
    return data


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
