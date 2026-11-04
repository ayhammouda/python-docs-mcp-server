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
from typing import Any, Literal, cast

from benchmarks.agentic.inputs import json_mapping, read_json
from benchmarks.agentic.protocol import assert_no_key_shaped_json, scanned_json_bytes
from benchmarks.agentic.run import OUTPUT_CAP_STOPS
from benchmarks.grading.batch import (
    _GEMINI_REFUSALS,
    BatchItemResult,
    ItemStatus,
    anthropic_result,
    gemini_result,
    openai_result,
)
from benchmarks.grading.inputs import (
    VOTE_FIELDS,
    GradingQuestion,
    JudgeInput,
    Weight,
    judge_schema,
    safe_id,
)
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
        "refusal": result.refusal,
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


def chosen_vote_file(
    root: Path, *, target: str, judge_id: str, effort: str | None, pass_number: int, item_id: str
) -> VoteFile | None:
    """Select once, retaining the digest of the exact parsed vote bytes."""
    records = _records(
        root,
        target=target,
        judge_id=judge_id,
        effort=effort,
        pass_number=pass_number,
        item_id=item_id,
    )
    return _chosen(records)


def chosen_record(
    root: Path, *, target: str, judge_id: str, effort: str | None, pass_number: int, item_id: str
) -> dict[str, Any] | None:
    chosen = chosen_vote_file(
        root, target=target, judge_id=judge_id, effort=effort,
        pass_number=pass_number, item_id=item_id,
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


def _result_matches_record(
    result: BatchItemResult | Mapping[str, Any], record: Mapping[str, Any]
) -> bool:
    normalized = asdict(result) if isinstance(result, BatchItemResult) else result
    fields = ("item_id", "status", "text", "stop_reason", "raw_usage", "buckets", "reported_model")
    if "refusal" in record:
        fields += ("refusal",)
    return all(
        json.dumps(record.get(k), sort_keys=True) == json.dumps(normalized.get(k), sort_keys=True)
        for k in fields
    )


def recorded_result(
    root: Path, record: Mapping[str, Any], judge: JudgeSpec, *, vote_sha256: str | None = None
) -> BatchItemResult | None:
    """Normalize this selected record's receipt, if recorded, without a run cache.

    This checks internal continuity of unsigned artifacts, not provider contact
    or billing. Historical records without receipts use their retained flags
    and stop/reply evidence in ``vote_from_record``. Selection callers pass the
    captured vote digest so receipt binding never reopens their selected file.
    """
    run, item, attempt = record.get("grading_run_id"), record.get("item_id"), record.get("attempt")
    if (
        not all(isinstance(v, str) and v and Path(v).name == v for v in (run, item, judge.id))
        or type(attempt) is not int or attempt < 1
    ):
        raise BenchmarkValidationError("invalid judge receipt coordinates")
    try:
        invocation = json_mapping(artifact_bytes(root, root / "runs" / f"{run}.json"),
                                  "grading invocation")
    except FileNotFoundError:
        return None
    manifest = invocation.get("execution_results")
    if manifest is None:
        return None
    relative = f"{judge.id}/{item}--a{attempt}.json"
    if not isinstance(manifest, dict) or relative not in manifest:
        raise BenchmarkValidationError("selected judge receipt is not in its invocation")
    try:
        body = artifact_bytes(root, root / "executions" / str(run) / relative)
    except FileNotFoundError:
        raise BenchmarkValidationError("missing selected judge receipt") from None
    if hashlib.sha256(body).hexdigest() != manifest[relative]:
        raise BenchmarkValidationError("selected judge receipt digest differs")
    history = json_mapping(body, "grading execution")
    # Find the selected file from its coordinates, never a recorded external path.
    path = vote_dir(
        root, target=record["target"], judge_id=judge.id, effort=record["effort"],
        pass_number=record["pass"], item_id=str(item),
    ) / f"{run}--a{attempt}.json"
    if vote_sha256 is None:
        vote_sha256 = hashlib.sha256(artifact_bytes(root, path)).hexdigest()
    return receipt_result(record, vote_sha256, vars(judge), history)


def receipt_result(
    record: Mapping[str, Any], digest: str, judge: Mapping[str, Any], history: Mapping[str, Any]
) -> BatchItemResult | None:
    """Bind and normalize a captured receipt without I/O or execution claims.

    Both score replay and public receipt eligibility consume this same result.
    Compact/scripted receipts retain their historical flag fallback only.
    """
    expected = {k: record.get(k) for k in (
        "grading_run_id", "unit_id", "item_id", "judge_id", "target", "pass", "attempt",
        "provider_batch_id", "input_sha256", "model_id", "effort",
    )}
    expected.update(provider=judge["provider"], api=judge["api"], model_id=judge["model_id"])
    if any(type(history.get(k)) is not type(v) or history.get(k) != v for k, v in expected.items()):
        raise BenchmarkValidationError("selected judge receipt identity differs")
    if digest != history.get("vote_sha256"):
        raise BenchmarkValidationError("selected judge receipt vote digest differs")
    try:
        normalized = history["result"]
        raw = normalized["raw"]
        if raw is None or (judge["api"] == "scripted" and "scripted_rule" in raw):
            # Compact receipts lack raw output; scripted receipts have no
            # provider line. Replay retained flags/status without inventing
            # provider evidence (or a vote for an absent terminal reply).
            if not _result_matches_record(normalized, record):
                raise BenchmarkValidationError("selected judge receipt differs from saved reply")
            return None
        if not isinstance(raw, dict):
            raise ValueError("no raw result")
        result = {"anthropic": anthropic_result, "openai": openai_result,
                  "google": gemini_result}[judge["provider"]](raw)
    except (KeyError, TypeError, ValueError, AttributeError, IndexError):
        raise BenchmarkValidationError("invalid selected judge receipt result") from None
    if (
        json.dumps(normalized, sort_keys=True) != json.dumps(asdict(result), sort_keys=True)
        or not _result_matches_record(result, record)
    ):
        raise BenchmarkValidationError("selected judge receipt differs from normalized reply")
    return result


def vote_from_record(
    record: Mapping[str, Any], *, request: JudgeInput | None, result: BatchItemResult | None = None
) -> Vote | None:
    """Replay the producer's withholding rules, never inventing an absent vote.

    Old records lack the normalized refusal bit. A refusal-specific stop is
    evidence; an ambiguous OpenAI completed reply needs its recorded result.
    The annotation alone is never evidence of a refusal.
    """
    status, withheld, stop = record.get("status"), record.get("withheld"), record.get("stop_reason")
    if (
        type(record.get("terminal")) is not bool
        or status not in ("succeeded", "errored", "expired", "canceled", "missing")
        or withheld not in (
            None, "parse_failure", "refusal", "output_cap", "errored", "expired",
            "canceled", "missing",
        )
        or (stop is not None and not isinstance(stop, str))
    ):
        raise BenchmarkValidationError("invalid judge vote flags")
    text = record.get("text")
    if text is not None and not isinstance(text, str):
        raise BenchmarkValidationError("invalid judge reply text")
    refusal_stop = stop in (_GEMINI_REFUSALS | {"refusal", "content_filter"})
    refusal = record.get("refusal", refusal_stop)
    if type(refusal) is not bool or (refusal_stop and not refusal):
        raise BenchmarkValidationError("invalid judge refusal flag")
    if result is not None:
        if not _result_matches_record(result, record):
            raise BenchmarkValidationError("judge result differs from recorded reply")
    else:
        result = BatchItemResult(
            item_id=str(record.get("item_id")), status=cast(ItemStatus, status), text=text,
            refusal=refusal, stop_reason=stop, raw=None, raw_usage=None, buckets=None,
            reported_model=None, unbilled=False,
        )
    schema = judge_schema() if request is None else request.schema
    try:
        expected = withheld_reason(result, schema)
        parsed = parse_object(text, schema) if expected is None else None
    except (TypeError, KeyError, AttributeError):
        raise BenchmarkValidationError("invalid frozen judge schema") from None
    if withheld != expected:
        raise BenchmarkValidationError("withheld reason differs from judge reply")
    vote = record.get("vote")
    # JSON equality distinguishes booleans from 0/1 in persisted fields.
    if json.dumps(parsed, sort_keys=True) != json.dumps(vote, sort_keys=True):
        raise BenchmarkValidationError("stored vote differs from its judge reply")
    if withheld is not None:
        return None
    if not isinstance(vote, dict):
        raise BenchmarkValidationError("invalid stored judge vote")
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
                None if chosen is None else vote_from_record(
                    chosen[1], request=request,
                    result=recorded_result(
                        root, chosen[1], panel.judges[judge_id], vote_sha256=chosen[2]
                    ),
                )
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
            reference=None if chosen is None else vote_from_record(
                chosen[1], request=request,
                result=recorded_result(root, chosen[1], reference, vote_sha256=chosen[2]),
            ),
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


def own_grade(av: AnswerVotes, judge_id: str, weights: tuple[Weight, Weight]) -> float | None:
    """A judge's own vote grade, unavailable when that judge withheld."""
    vote = av.votes.get(judge_id)
    return None if vote is None else grade(vote.fields(), weights)[0]


def judge_grade(
    av: AnswerVotes, judge_id: str, question: GradingQuestion, final: Mapping[str, Any]
) -> float:
    """One judge's own grade (G4); the final grade where it withheld or the answer is suspect."""
    if av.suspect:
        return float(final["score"])
    score = own_grade(av, judge_id, question.weights)
    if score is None:
        return float(final["score"])
    return score
