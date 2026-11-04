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

from benchmarks.agentic.inputs import json_mapping, read_json
from benchmarks.agentic.protocol import assert_no_key_shaped_json, scanned_json_bytes
from benchmarks.agentic.run import OUTPUT_CAP_STOPS
from benchmarks.grading.batch import BatchItemResult
from benchmarks.grading.inputs import VOTE_FIELDS, GradingQuestion, JudgeInput, safe_id
from benchmarks.grading.mapping import Vote, grade, panel_fields, parse_object, parse_vote
from benchmarks.grading.panel import JudgeSpec, Panel
from benchmarks.report_files import artifact_bytes, artifact_paths
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
    return read_json(path, "grading vote")


VoteFile = tuple[Path, dict[str, Any], str]


def _records(root: Path, **where: Any) -> list[VoteFile]:
    found = []
    for path in artifact_paths(root, vote_dir(root, **where), "*.json"):
        body = artifact_bytes(root, path)
        found.append((path, json_mapping(body, "grading vote"), hashlib.sha256(body).hexdigest()))
    return found


def record_order(path: Path, record: Mapping[str, Any]) -> tuple[bool, str, str, int]:
    when, run, attempt = (
        record.get("graded_at"),
        record.get("grading_run_id"),
        record.get("attempt"),
    )
    if (
        not isinstance(when, str)
        or not isinstance(run, str)
        or type(attempt) is not int
        or attempt < 1
    ):
        raise BenchmarkValidationError("grading vote lacks its selection coordinates")
    if path.name != f"{run}--a{attempt}.json":
        raise BenchmarkValidationError("grading vote differs from its run or attempt coordinate")
    return is_final(record), when, run, attempt


def _chosen(records: list[VoteFile]) -> VoteFile | None:
    return max(records, key=lambda pr: record_order(pr[0], pr[1]), default=None)


def chosen_record(
    root: Path, *, target: str, judge_id: str, effort: str | None, pass_number: int, item_id: str
) -> dict[str, Any] | None:
    records = _records(
        root,
        target=target,
        judge_id=judge_id,
        effort=effort,
        pass_number=pass_number,
        item_id=item_id,
    )
    chosen = _chosen(records)
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
    return any(is_final(record) for _, record, _ in records)


def total_attempts(
    root: Path, *, target: str, judge_id: str, effort: str | None, pass_number: int, item_id: str
) -> int:
    """Takes across invocations: per run id, the highest ``attempt`` from its vote records
    and its ``runs/<id>.json`` ``not_run[]`` entries (takes that left no vote file)."""
    where = {
        "target": target,
        "judge_id": judge_id,
        "effort": effort,
        "pass_number": pass_number,
        "item_id": item_id,
    }
    return _attempts(root, _records(root, **where), **where)


def _attempts(
    root: Path,
    records: list[VoteFile],
    *,
    target: str,
    judge_id: str,
    effort: str | None,
    pass_number: int,
    item_id: str,
) -> int:
    highest: dict[str, int] = {}
    for _, record, _ in records:
        run = record["grading_run_id"]
        highest[run] = max(highest.get(run, 0), int(record["attempt"]))
    for path in artifact_paths(root, root / "runs", "*.json"):
        run_file = json_mapping(artifact_bytes(root, path), "grading invocation")
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
    root: Path,
    path: Path,
    record: dict[str, Any],
    record_sha: str,
    candidates: list[VoteFile],
    *,
    judge: JudgeSpec,
    request: JudgeInput | None,
    **where: Any,
) -> dict[str, Any]:
    expected = {
        "judge_id": judge.id,
        "model_id": judge.model_id,
        "effort": where["effort"],
        "pass": where["pass_number"],
        "target": where["target"],
        "item_id": where["item_id"],
    }
    if request is not None:
        expected["input_sha256"] = request.sha256()
    for field, value in expected.items():
        if field not in record or type(record[field]) is not type(value) or record[field] != value:
            raise BenchmarkValidationError(
                f"{where['item_id']} {where['target']} {judge.id} pass {where['pass_number']} "
                f"vote differs from scoring inputs at {field}"
            )
    return {
        "path": str(path),
        "sha256": record_sha,
        "final": is_final(record),
        "model_id": record["model_id"],
        "reported_model": record["reported_model"],
        "effort": record["effort"],
        "withheld": record["withheld"],
        "terminal": record["terminal"],
        "input_sha256": record["input_sha256"],
        "attempts": _attempts(root, candidates, **where),
    }


def vote_from_record(record: Mapping[str, Any], *, request: JudgeInput | None) -> Vote | None:
    vote = record.get("vote")
    if request is not None:
        withheld = record.get("withheld")
        status = record.get("status")
        if (
            type(record.get("terminal")) is not bool
            or status not in ("succeeded", "errored", "expired", "canceled", "missing")
            or withheld not in (
                None, "parse_failure", "refusal", "output_cap", "errored", "expired",
                "canceled", "missing",
            )
        ):
            raise BenchmarkValidationError("invalid judge vote flags")
        parsed = None
        text = record.get("text")
        if text is not None and not isinstance(text, str):
            raise BenchmarkValidationError("invalid judge reply text")
        try:
            reply = parse_object(text, request.schema)
        except (TypeError, KeyError, AttributeError):
            raise BenchmarkValidationError("invalid frozen judge schema") from None
        if withheld is None:
            if status != "succeeded" or record.get("stop_reason") in OUTPUT_CAP_STOPS:
                raise BenchmarkValidationError("missing successful judge reply")
            parsed = reply
            if parsed is None:
                raise BenchmarkValidationError("invalid judge reply")
        elif (
            (withheld == "parse_failure" and (status != "succeeded" or reply is not None))
            or (withheld == "output_cap" and record.get("stop_reason") not in OUTPUT_CAP_STOPS)
            or (withheld in ("errored", "expired", "canceled", "missing") and status != withheld)
        ):
            raise BenchmarkValidationError("withheld reason differs from judge reply")
        # JSON equality distinguishes booleans from 0/1 in persisted fields.
        if json.dumps(parsed, sort_keys=True) != json.dumps(vote, sort_keys=True):
            raise BenchmarkValidationError("stored vote differs from its judge reply")
        vote = parsed
    if record.get("withheld") is not None or vote is None:
        return None
    try:
        return Vote(**vote)
    except TypeError:
        raise BenchmarkValidationError("invalid stored judge vote") from None


def load_answer_votes(
    root: Path,
    *,
    panel: Panel,
    judges: Mapping[str, str | None],
    pass_number: int = 1,
    requests: Mapping[tuple[str, str], JudgeInput] | None = None,
    include_reference: bool = True,
) -> dict[tuple[str, str], AnswerVotes]:
    """Every answer item under ``items/answers/``, with each judge's chosen record.

    ``judges`` maps the panel judge ids that graded (fallback included) to their effort.
    The reference vote is its pass-1 ``reference`` record.
    """
    reference = panel.judges[panel.reference]
    loaded: dict[tuple[str, str], AnswerVotes] = {}
    for item_path in artifact_paths(root, root / "items/answers", "*.json"):
        evidence = json_mapping(artifact_bytes(root, item_path), "answer item")
        meta = evidence.get("meta")
        if not isinstance(meta, dict):
            raise BenchmarkValidationError("missing answer item metadata")
        competitor, corpus = meta.get("competitor_id"), meta.get("corpus_id")
        item_id = item_path.stem
        if not isinstance(competitor, str) or not isinstance(corpus, str):
            raise BenchmarkValidationError(f"{item_id} has invalid answer coordinates")
        key = competitor, corpus
        request = None
        if requests is not None:
            request = requests.get(key)
            try:
                expected_id = safe_id(*key)
            except ValueError:
                raise BenchmarkValidationError("invalid grading item ID") from None
            if request is None or key in loaded or item_id != expected_id:
                raise BenchmarkValidationError(f"{item_id} differs from its answer coordinates")
            if not request.matches_answer_item(key, evidence):
                raise BenchmarkValidationError(f"{item_id} item differs from rendered answer input")
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
            candidates = _records(root, **where)
            chosen = _chosen(candidates)
            votes[judge_id] = (
                None if chosen is None else vote_from_record(chosen[1], request=request)
            )
            if chosen is not None:
                records[judge_id] = _summary(
                    root,
                    *chosen,
                    candidates,
                    judge=panel.judges[judge_id],
                    request=request,
                    **where,
                )
        where = {
            "target": "reference",
            "judge_id": reference.id,
            "effort": reference.effort,
            "pass_number": 1,
            "item_id": item_id,
        }
        candidates = _records(root, **where) if include_reference else []
        chosen = _chosen(candidates)
        if chosen is not None and request is not None:
            reference_path = root / "items/reference" / item_path.name
            try:
                reference_item = json_mapping(
                    artifact_bytes(root, reference_path), "reference item"
                )
            except FileNotFoundError:
                raise BenchmarkValidationError(f"{item_id} missing reference input") from None
            if reference_item != evidence:
                raise BenchmarkValidationError(f"{item_id} reference input differs from the answer")
        loaded[key] = AnswerVotes(
            item_id=item_id,
            competitor_id=meta["competitor_id"],
            corpus_id=meta["corpus_id"],
            votes=votes,
            records=records,
            suspect=bool(meta.get("suspect")),
            reference=None if chosen is None else vote_from_record(chosen[1], request=request),
            reference_record=None
            if chosen is None
            else _summary(root, *chosen, candidates, judge=reference, request=request, **where),
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
