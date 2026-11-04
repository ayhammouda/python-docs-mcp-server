"""Vote records, record selection and final grades with provenance (design §9.1; ruling 6).

Every vote record is a new file, created exclusively, under
``votes/<target>/<judge_id>/<effort or "default">/pass<N>/<item_id>/``, named
``<grading_run_id>--a<attempt>.json``: efforts, passes, retries and reruns never
overwrite each other. The record chosen for a grade is the last final one by
``graded_at``, then run id, then attempt.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from benchmarks.agentic.protocol import assert_no_key_shaped_json, scanned_json_bytes
from benchmarks.agentic.run import OUTPUT_CAP_STOPS
from benchmarks.grading.batch import BatchItemResult
from benchmarks.grading.inputs import VOTE_FIELDS, GradingQuestion
from benchmarks.grading.mapping import Vote, grade, panel_fields, parse_object, parse_vote
from benchmarks.grading.panel import JudgeSpec, Panel
from benchmarks.runner import BenchmarkValidationError

Withheld = Literal["parse_failure", "refusal", "output_cap", "errored", "expired", "canceled",
                   "missing"]  # fmt: skip
FINAL_WITHHELD = frozenset({"parse_failure", "refusal", "output_cap"})  # never retried (ruling 6)
#: Targets whose records carry a judge vote; the others carry ``parsed``.
JUDGE_TARGETS = frozenset({"tuning", "probes", "reference-probes", "answers", "reference"})
LLM_PANEL, LLM_REFERENCE = "llm_panel", "llm_reference"


def withheld_reason(result: BatchItemResult, schema: Mapping[str, Any]) -> Withheld | None:
    if result.refusal:
        return "refusal"
    if result.stop_reason in OUTPUT_CAP_STOPS:
        return "output_cap"
    if result.status != "succeeded":
        return result.status
    if parse_object(result.text, schema) is None:
        return "parse_failure"
    return None


def vote_dir(
    root: Path, *, target: str, judge_id: str, effort: str | None, pass_number: int, item_id: str
) -> Path:
    return (
        root / "votes" / target / judge_id / (effort or "default") / f"pass{pass_number}" / item_id
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def write_vote_record(
    root: Path,
    *,
    target: str,
    judge: JudgeSpec,
    effort: str | None,
    pass_number: int,
    grading_run_id: str,
    attempt: int,
    result: BatchItemResult,
    terminal: bool,
    unit_id: str,
    provider_batch_id: str | None,
    input_sha256: str,
    schema: Mapping[str, Any],
) -> Path:
    """Write one record as a new file (exclusive create: an existing name raises)."""
    withheld = withheld_reason(result, schema)
    vote = parse_vote(result.text) if withheld is None and target in JUDGE_TARGETS else None
    parsed = None
    if withheld is None and target not in JUDGE_TARGETS:
        parsed = parse_object(result.text, schema)
    record = {
        "item_id": result.item_id,
        "target": target,
        "judge_id": judge.id,
        "model_id": judge.model_id,
        "reported_model": result.reported_model,
        "effort": effort,
        "pass": pass_number,
        "grading_run_id": grading_run_id,
        "attempt": attempt,
        "status": result.status,
        "withheld": withheld,
        "terminal": terminal,
        "text": result.text,
        "vote": None if vote is None else asdict(vote),
        "parsed": parsed,
        "stop_reason": result.stop_reason,
        "raw_usage": result.raw_usage,
        "buckets": None if result.buckets is None else asdict(result.buckets),
        "unit_id": unit_id,
        "provider_batch_id": provider_batch_id,
        "input_sha256": input_sha256,
        "graded_at": _now(),
    }
    # The original reply is evidence even when the vote is withheld. Retain all
    # object pairs for the scan, including fields shadowed by a duplicate name.
    if result.text is not None:
        try:
            reply = json.loads(result.text, object_pairs_hook=list)
        except ValueError:
            pass  # A non-JSON reply is scanned as plain text in the record below.
        else:
            assert_no_key_shaped_json(reply, where="judge reply")
    body = scanned_json_bytes(record, where="vote record", indent=2, sort_keys=True) + b"\n"
    path = vote_dir(
        root,
        target=target,
        judge_id=judge.id,
        effort=effort,
        pass_number=pass_number,
        item_id=result.item_id,
    )
    path = path / f"{grading_run_id}--a{attempt}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as file:
        file.write(body)
    return path


def is_final(record: Mapping[str, Any]) -> bool:
    """A vote, a withheld vote that is never retried, or a terminal record."""
    return (
        record.get("status") == "succeeded"
        or record.get("withheld") in FINAL_WITHHELD
        or bool(record.get("terminal"))
    )


def read_vote_record(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _records(root: Path, **where: Any) -> list[tuple[Path, dict[str, Any]]]:
    found = [(p, read_vote_record(p)) for p in vote_dir(root, **where).glob("*.json")]
    found.sort(key=lambda pr: (pr[1]["graded_at"], pr[1]["grading_run_id"], pr[1]["attempt"]))
    return found


def _chosen(root: Path, **where: Any) -> tuple[Path, dict[str, Any]] | None:
    records = _records(root, **where)
    finals = [pr for pr in records if is_final(pr[1])]
    return (finals or records or [None])[-1]


def chosen_record(
    root: Path, *, target: str, judge_id: str, effort: str | None, pass_number: int, item_id: str
) -> dict[str, Any] | None:
    chosen = _chosen(
        root,
        target=target,
        judge_id=judge_id,
        effort=effort,
        pass_number=pass_number,
        item_id=item_id,
    )
    return None if chosen is None else chosen[1]


def has_final_record(
    root: Path, *, target: str, judge_id: str, effort: str | None, pass_number: int, item_id: str
) -> bool:
    records = _records(
        root,
        target=target,
        judge_id=judge_id,
        effort=effort,
        pass_number=pass_number,
        item_id=item_id,
    )
    return any(is_final(record) for _, record in records)


def total_attempts(
    root: Path, *, target: str, judge_id: str, effort: str | None, pass_number: int, item_id: str
) -> int:
    """Takes across invocations: per run id, the highest ``attempt`` from its vote records
    and its ``runs/<id>.json`` ``not_run[]`` entries (takes that left no vote file)."""
    highest: dict[str, int] = {}
    where = {
        "target": target,
        "judge_id": judge_id,
        "effort": effort,
        "pass_number": pass_number,
        "item_id": item_id,
    }
    for _, record in _records(root, **where):
        run = record["grading_run_id"]
        highest[run] = max(highest.get(run, 0), int(record["attempt"]))
    for path in sorted((root / "runs").glob("*.json")):
        run_file = json.loads(path.read_text(encoding="utf-8"))
        for entry in run_file.get("not_run") or []:
            if (
                entry.get("item_id") == item_id
                and entry.get("target") == target
                and entry.get("judge_id") == judge_id
                and (entry.get("effort") or "default") == (effort or "default")
                and entry.get("pass") == pass_number
            ):
                run = run_file.get("grading_run_id", path.stem)
                highest[run] = max(highest.get(run, 0), int(entry.get("attempt") or 0))
    return sum(highest.values())


@dataclass(frozen=True)
class AnswerVotes:
    item_id: str  # safe_id(competitor_id, corpus_id)
    competitor_id: str
    corpus_id: str
    votes: dict[str, Vote | None]  # panel judge id -> vote
    records: dict[str, dict[str, Any]]  # judge id -> provenance of its chosen record
    suspect: bool
    reference: Vote | None
    reference_record: dict[str, Any] | None


def _summary(
    root: Path, path: Path, record: dict[str, Any], *, judge: JudgeSpec, **where: Any
) -> dict[str, Any]:
    expected = {
        "judge_id": judge.id,
        "model_id": judge.model_id,
        "effort": where["effort"],
        "pass": where["pass_number"],
        "target": where["target"],
        "item_id": where["item_id"],
    }
    for field, value in expected.items():
        if record.get(field) != value:
            raise BenchmarkValidationError(
                f"{judge.id} vote differs from scoring inputs at {field}"
            )
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "model_id": record["model_id"],
        "reported_model": record["reported_model"],
        "effort": record["effort"],
        "withheld": record["withheld"],
        "terminal": record["terminal"],
        "input_sha256": record["input_sha256"],
        "attempts": total_attempts(root, **where),
    }


def _vote_of(record: Mapping[str, Any]) -> Vote | None:
    vote = record.get("vote")
    return None if record.get("withheld") is not None or vote is None else Vote(**vote)


def load_answer_votes(
    root: Path, *, panel: Panel, judges: Mapping[str, str | None], pass_number: int = 1
) -> dict[tuple[str, str], AnswerVotes]:
    """Every answer item under ``items/answers/``, with each judge's chosen record.

    ``judges`` maps the panel judge ids that graded (fallback included) to their effort.
    The reference vote is its pass-1 ``reference`` record.
    """
    reference = panel.judges[panel.reference]
    loaded: dict[tuple[str, str], AnswerVotes] = {}
    for item_path in sorted((root / "items" / "answers").glob("*.json")):
        meta = json.loads(item_path.read_text(encoding="utf-8"))["meta"]
        item_id = item_path.stem
        votes: dict[str, Vote | None] = {}
        records: dict[str, dict[str, Any]] = {}
        for judge_id, effort in judges.items():
            where = {
                "target": "answers",
                "judge_id": judge_id,
                "effort": effort,
                "pass_number": pass_number,
                "item_id": item_id,
            }
            chosen = _chosen(root, **where)
            votes[judge_id] = None if chosen is None else _vote_of(chosen[1])
            if chosen is not None:
                records[judge_id] = _summary(root, *chosen, judge=panel.judges[judge_id], **where)
        where = {
            "target": "reference",
            "judge_id": reference.id,
            "effort": reference.effort,
            "pass_number": 1,
            "item_id": item_id,
        }
        chosen = _chosen(root, **where)
        loaded[(meta["competitor_id"], meta["corpus_id"])] = AnswerVotes(
            item_id=item_id,
            competitor_id=meta["competitor_id"],
            corpus_id=meta["corpus_id"],
            votes=votes,
            records=records,
            suspect=bool(meta.get("suspect")),
            reference=None if chosen is None else _vote_of(chosen[1]),
            reference_record=None
            if chosen is None
            else _summary(root, *chosen, judge=reference, **where),
        )
    return loaded


def reference_needed(answer_votes: Mapping[tuple[str, str], AnswerVotes]) -> list[str]:
    """Suspect answers and answers with a field the panel left open."""
    return sorted(
        av.item_id
        for av in answer_votes.values()
        if av.suspect or panel_fields(av.votes).needs_reference
    )


def final_grade(
    av: AnswerVotes, question: GradingQuestion, *, inputs: Mapping[str, str]
) -> dict[str, Any] | None:
    """The answer's grade with its provenance; ``None`` while it waits for the reference."""
    pf = panel_fields(av.votes)
    settled = list(VOTE_FIELDS) if av.suspect else [f for f, v in pf.fields.items() if v is None]
    if settled and av.reference is None:
        return None
    reference = av.reference.fields() if av.reference is not None else {}
    fields = {f: reference[f] if f in settled else bool(pf.fields[f]) for f in VOTE_FIELDS}
    score, label = grade(fields, question.weights)
    adjudicator = []
    if len(settled) < len(VOTE_FIELDS):  # some fields came from the panel
        adjudicator = [av.records[j]["model_id"] for j, v in av.votes.items() if v is not None]
    if settled and av.reference_record is not None:
        adjudicator.append(av.reference_record["model_id"])
    input_sha256 = next((r["input_sha256"] for r in av.records.values()), None)
    return {
        "score": score,
        "label": label,
        "method": LLM_REFERENCE if settled else LLM_PANEL,
        "adjudicator": adjudicator,
        "grading": {
            "item_id": av.item_id,
            "pass": 1,
            "input_sha256": input_sha256,
            "votes": av.records,
            "panel_fields": pf.fields,
            "split_fields": list(pf.split),
            "valid_votes": pf.valid_votes,
            "injection_suspect": av.suspect,
            "reference": av.reference_record,
            "settled_fields": settled,
            "inputs": dict(inputs),
        },
    }


def judge_grade(
    av: AnswerVotes, judge_id: str, question: GradingQuestion, final: Mapping[str, Any]
) -> float:
    """One judge's own grade (G4); the final grade where it withheld or the answer is suspect."""
    vote = av.votes.get(judge_id)
    if av.suspect or vote is None:
        return float(final["score"])
    return grade(vote.fields(), question.weights)[0]
