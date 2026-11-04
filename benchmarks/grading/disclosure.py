"""Current exported audit and settled-cost evidence shared by scorer and report.

Receipts establish continuity within an unsigned bundle, never provider authenticity.
No repository defaults, provider calls or report/scorer imports belong here.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from decimal import Decimal, DecimalException
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from benchmarks.agentic.reference import nonnegative_decimal
from benchmarks.grading.aggregate import (
    AnswerVotes,
    _chosen,
    _records,
    final_grade,
    is_final,
    judge_grade,
    vote_from_record,
)
from benchmarks.grading.batch import BatchItemResult
from benchmarks.grading.execution_evidence import ledger_units
from benchmarks.grading.gates import c2, c3, g4
from benchmarks.grading.inputs import GradingQuestion, JudgeInput, key_audit_schema, safe_id
from benchmarks.grading.mapping import parse_object
from benchmarks.report_files import optional_json
from benchmarks.report_measurements import (
    comparison_identities_match,
    evidence_cell,
    grading_receipt_matches,
)
from benchmarks.runner import BenchmarkValidationError

if TYPE_CHECKING:
    from benchmarks.report_contracts import PublicationRun


def ledger_spend(
    events: Sequence[Mapping[str, Any]] | None, graded: int | None,
) -> dict[str, Any]:
    """Book settlements exactly once as recorded, including conservative host charges.

    A missing/unverified denominator is distinct from zero completed LLM finals.
    Neither reservations nor cached summaries are charges.
    """
    booked: dict[str, Decimal] = defaultdict(Decimal)
    verified, reason = False, "missing ledger"
    if events is not None:
        try:
            reserves, settlements = ledger_units(events)
            for event in settlements.values():
                booked[event["provider"]] += nonnegative_decimal(event["booked"])
            verified = reserves.keys() == settlements.keys()
            reason = None if verified else "unsettled reservations; confirmed bookings only"
        except (BenchmarkValidationError, KeyError, TypeError, ValueError, DecimalException):
            reason = "inconsistent ledger"
    return {
        "verified": verified, "reason": reason,
        "by_provider": {p: str(v) for p, v in sorted(booked.items())},
        "graded_answers": graded,
        "per_graded_answer": {
            p: str((v / graded).quantize(Decimal("0.000001"))) for p, v in sorted(booked.items())
        } if graded and verified else {},
    }


def completed_answers(
    requests: Mapping[tuple[str, str], JudgeInput],
    questions: Mapping[str, GradingQuestion],
    inputs: Mapping[str, str],
    efforts: Mapping[str, Any],
    reference: str,
    selected: Mapping[tuple[str, str], tuple[dict[str, Any], BatchItemResult] | None],
) -> int | None:
    """Count final grades from the receipt-admitted capture, independently of projections.

    Absence is pending; a selected but unverified/contradictory reply makes the
    denominator unknown. The caller already bound each item to its request.
    No grade, summary, vote or receipt file is reopened here.
    """
    count = 0
    for key, request in requests.items():
        item = safe_id(*key)
        parsed, records = {}, {}
        for judge in (*efforts, reference):
            coord = judge, item
            if coord not in selected:
                continue
            chosen = selected[coord]
            if chosen is None:
                return None
            raw, result = chosen
            if raw.get("input_sha256") != request.sha256():
                return None
            try:
                parsed[judge] = vote_from_record(raw, request=request, result=result)
            except BenchmarkValidationError:
                return None
            records[judge] = raw
        if not all(j in records and is_final(records[j]) for j in efforts):
            continue
        if reference in records and not is_final(records[reference]):
            continue
        av = AnswerVotes(
            item, key[0], key[1], {j: parsed[j] for j in efforts},
            {j: records[j] for j in efforts}, request.suspect,
            parsed.get(reference), records.get(reference),
        )
        if final_grade(av, questions[key[1]], inputs=inputs) is not None:
            count += 1
    return count


def audit_request(
    question: GradingQuestion, sections: Any, *, prompt: bytes
) -> JudgeInput:
    """Replay a key-audit request from frozen question/key and exported cited text."""
    def matches(section: Any, url: str) -> bool:
        # Keep the production CitedSection shape, including a citation's own
        # version on cross-version questions. Compact exports may omit headings.
        if not isinstance(section, dict):
            return False
        required = {"url", "version", "text"}
        allowed = required | {"page", "anchor", "heading"}
        url_version = urlsplit(url).path.lstrip("/").split("/", 1)[0]
        return (
            required <= section.keys() <= allowed
            and section["url"] == url
            and section["version"] in question.version.split(" and ")
            and (not re.fullmatch(r"\d+\.\d+", url_version)
                 or section["version"] == url_version)
            and isinstance(section["text"], str) and bool(section["text"].strip())
            and all(isinstance(section[k], str) for k in ("page", "heading") if k in section)
            and (section.get("anchor") is None or isinstance(section["anchor"], str))
        )

    if (
        not isinstance(sections, list) or not sections
        or len(sections) != len(question.citations)
        or any(
            not matches(s, url)
            for s, url in zip(sections, question.citations, strict=True)
        )
        or sum(len(s["text"]) for s in sections) > 20_000
    ):
        raise BenchmarkValidationError("missing or inconsistent cited sections for key audit")
    user = json.dumps({
        "question_id": question.id, "version": question.version, "question": question.prompt,
        "answer_key": question.answer_key, "cited_sections": sections,
    }, sort_keys=True, separators=(",", ":"))
    return JudgeInput(prompt.decode("utf-8").replace("\r\n", "\n"), user, key_audit_schema(), False)


def verified_key_audit(
    root: Path, *, questions: Mapping[str, GradingQuestion], reference: str,
    judge: dict[str, Any], inputs: Mapping[str, str], prompt: bytes,
    events: list[dict[str, Any]], runs: Mapping[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Require every frozen key's selected final reply and current execution receipt.

    Raise on unavailable evidence; callers retain an explicit unverified diagnostic.
    Paths saved by another machine are never followed.
    """
    if not questions or hashlib.sha256(prompt).hexdigest() != inputs.get("key_audit_prompt"):
        raise BenchmarkValidationError("unbound key-audit inputs")
    grading = root / "grading"
    audited = {}
    captured_runs = dict(runs or {})
    for qid, question in questions.items():
        item = safe_id(qid)
        evidence = optional_json(root, grading / "items/key-audit" / f"{item}.json") or {}
        meta = evidence.get("meta") or {}
        if not isinstance(meta, dict):
            raise BenchmarkValidationError("invalid key-audit metadata")
        sections = meta.get("cited_sections")
        request = audit_request(question, sections, prompt=prompt)
        if evidence != request.item_evidence({
            "corpus_id": qid, "cited_sections": sections, "inputs": dict(inputs),
        }):
            raise BenchmarkValidationError("key-audit item differs from current inputs")
        chosen = _chosen(_records(
            grading, target="key-audit", judge_id=reference, effort=judge["effort"],
            pass_number=1, item_id=item,
        ))
        if chosen is None:
            raise BenchmarkValidationError("missing key-audit reply")
        _path, record, digest = chosen
        identity = {
            "target": "key-audit", "judge_id": reference, "model_id": judge["model_id"],
            "effort": judge["effort"], "pass": 1, "item_id": item,
            "input_sha256": request.sha256(),
        }
        parsed = parse_object(record.get("text"), request.schema)
        if (
            any(type(record.get(k)) is not type(v) or record.get(k) != v
                for k, v in identity.items())
            or not is_final(record) or record.get("status") != "succeeded"
            or record.get("withheld") is not None or record.get("vote") is not None
            or parsed is None or record.get("parsed") != parsed
            or type((record.get("parsed") or {}).get("key_supported")) is not bool
        ):
            raise BenchmarkValidationError("key-audit reply differs from captured request")
        run_id = record["grading_run_id"]
        if runs is None and run_id not in captured_runs:
            captured_runs[run_id] = optional_json(root, grading / "runs" / f"{run_id}.json") or {}
        invocation = captured_runs.get(run_id, {})
        if (
            invocation.get("target") != "key-audit"
            or invocation.get("composition") != {
                reference: {"model_id": judge["model_id"], "effort": judge["effort"]}
            }
            or not grading_receipt_matches(root, record, digest, judge, invocation, events)
        ):
            raise BenchmarkValidationError("unverified key-audit execution")
        audited[qid] = parsed["key_supported"]
    return {"verified": True, "audited": len(audited),
            "flagged": sorted(q for q, supported in audited.items() if not supported)}


def retained_claims(
    run: PublicationRun, flagged: Sequence[str]
) -> dict[str, dict[str, Any]]:
    """Recompute subset comparisons from the admitted, adjudicated answer cohort.

    Derive compared models from the full plan, then reconcile each comparison's
    unflagged arms. Pending retained scores stay pending.
    """
    if run.grading != "live" or run.answering != "live":
        raise BenchmarkValidationError("unverified or ineligible comparison cohort")
    captured = run.grade_capture
    plan = run.plan["cells"]
    if not isinstance(plan, list) or not all(isinstance(c, dict) for c in plan):
        raise BenchmarkValidationError("invalid comparison plan cells")
    efforts = run.summary["efforts"]
    if not isinstance(efforts, dict):
        raise BenchmarkValidationError("invalid judging panel")
    systems: dict[str, set[str]] = defaultdict(set)
    for cell in plan:
        model, system = cell.get("model_family_id"), cell.get("system_id")
        if isinstance(model, str) and isinstance(system, str):
            systems[model].add(system)

    actual: dict[str, dict[str, Any]] = {"C2": {}, "C3": {}}
    single: dict[str, dict[str, dict[str, Any]]] = {j: {"C2": {}, "C3": {}} for j in efforts}
    for cid, peer in (("C2", "docs-fetch"), ("C3", "no-docs")):
        arms = ("python-docs", peer)
        for model, available in systems.items():
            if not set(arms) <= available or (
                cid == "C3" and run.families.get(model, {}).get("arm") != "air-gapped"
            ):
                continue
            if not comparison_identities_match(
                plan, run.transcripts, model=model, systems=arms, excluded=flagged,
            ):
                claim = {"id": cid, "model": model, "passed": None, "interval": None,
                         "reason": "comparison plan differs from captured trajectories"}
                actual[cid][model] = claim
                for own in single.values():
                    own[cid][model] = claim
                continue
            kept, scores, tokens = set(), {}, {}
            by_judge: dict[str, dict[Any, Any]] = {j: {} for j in efforts}
            for key, transcript in run.transcripts.items():
                if (transcript["model_family_id"] != model or transcript["system_id"] not in arms
                        or key[1] in flagged):
                    continue
                coord = evidence_cell(transcript)
                kept.add(coord)
                scores[coord] = captured.scores.get(key)
                # Counts must bind to this same captured retained trajectory.
                if cid == "C2":
                    token = run.tokens.get(key, {})
                    tokens[coord] = (token["client_wrapped_tokens"]
                                     if run.count_capture.matches(transcript, token) else None)
                final = captured.finals.get(key)
                for judge, own in by_judge.items():
                    own[coord] = (
                        judge_grade(captured.answers[key], judge, captured.questions[key[1]], final)
                        if final is not None else scores[coord]
                    )

            def claim_for(grades: dict[Any, Any]) -> dict[str, Any]:
                return asdict(
                    c2(tokens, grades, kept, model=model, seed=run.seed) if cid == "C2" else
                    c3(grades, kept, model=model, seed=run.seed, offline_ok=run.offline_ok())
                )

            actual[cid][model] = claim_for(scores)
            for judge, own in by_judge.items():
                single[judge][cid][model] = (
                    actual[cid][model] if own == scores else claim_for(own)
                )
    robustness = g4({
        judge: {f"{cid}/{m}": c["passed"] for cid, by in cs.items() for m, c in by.items()}
        for judge, cs in {"panel": actual, **single}.items()
    })
    sensitive = robustness.details.get("judge_sensitive") or []
    for cid, by in actual.items():
        for model, claim in by.items():
            if run.tier != "Validated":
                claim.update(
                    passed=None,
                    reason=claim.get("reason") or "grading gates do not support publication",
                )
            if f"{cid}/{model}" in sensitive:
                claim.update(passed=None, reason="judge-sensitive retained cohort")
    return actual
