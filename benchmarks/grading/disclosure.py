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
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from benchmarks.agentic.reference import nonnegative_decimal
from benchmarks.grading.aggregate import _chosen, _records, is_final, judge_grade
from benchmarks.grading.gates import c2, c3, g4
from benchmarks.grading.inputs import GradingQuestion, JudgeInput, key_audit_schema, safe_id
from benchmarks.grading.mapping import parse_object
from benchmarks.report_files import optional_json
from benchmarks.report_measurements import grading_receipt_matches
from benchmarks.runner import BenchmarkValidationError

if TYPE_CHECKING:
    from benchmarks import agentic_report


def ledger_spend(events: Sequence[Mapping[str, Any]], graded: int | None) -> dict[str, Any]:
    """Book settlements exactly once as recorded, including conservative host charges.

    A missing/unverified denominator is distinct from zero completed LLM finals.
    Neither reservations nor cached summaries are charges.
    """
    booked: dict[str, Decimal] = defaultdict(Decimal)
    for event in events:
        if event.get("event") == "settle":
            provider = event.get("provider")
            if not isinstance(provider, str) or not provider:
                raise BenchmarkValidationError("settlement lacks provider")
            booked[provider] += nonnegative_decimal(event["booked"])
    return {
        "by_provider": {p: str(v) for p, v in sorted(booked.items())},
        "graded_answers": graded,
        "per_graded_answer": {
            p: str((v / graded).quantize(Decimal("0.000001"))) for p, v in sorted(booked.items())
        } if graded else {},
    }


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
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Require every frozen key's selected final reply and current execution receipt.

    Raise on unavailable evidence; callers retain an explicit unverified diagnostic.
    Paths saved by another machine are never followed.
    """
    if not questions or hashlib.sha256(prompt).hexdigest() != inputs.get("key_audit_prompt"):
        raise BenchmarkValidationError("unbound key-audit inputs")
    grading = root / "grading"
    audited = {}
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
        invocation = optional_json(
            root, grading / "runs" / f"{record['grading_run_id']}.json"
        ) or {}
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
    run: agentic_report._RunMetadata, flagged: Sequence[str]
) -> dict[str, dict[str, Any]]:
    """Recompute subset comparisons from the admitted, adjudicated answer cohort.

    Derive compared models from the full plan, so removing flagged keys cannot
    quietly drop a missing comparison arm. Pending retained scores stay pending.
    """
    if run.grading != "live" or run.answering != "live":
        raise BenchmarkValidationError("unverified or ineligible comparison cohort")
    captured = run.grade_capture
    plan = run.plan["cells"]
    planned = {
        (c["system_id"], c["model_family_id"], c["corpus_id"], c["repeat"])
        for c in plan
    }
    observed = {
        (t["system_id"], t["model_family_id"], key[1], t["repeat"])
        for key, t in run.transcripts.items()
    }
    if len(planned) != len(plan) or observed != planned:
        raise BenchmarkValidationError("comparison plan differs from captured trajectories")
    kept = {k for k in planned if k[2] not in flagged}
    scores, tokens = {}, {}
    by_judge: dict[str, dict[Any, Any]] = {j: {} for j in run.summary["efforts"]}
    for key, transcript in run.transcripts.items():
        coord = (
            transcript["system_id"], transcript["model_family_id"], key[1], transcript["repeat"]
        )
        if coord not in kept:
            continue
        scores[coord] = captured.scores.get(key)
        # Only counts actually bound to the same retained trajectory can enter C2.
        token = run.tokens.get(key, {})
        tokens[coord] = (
            token["client_wrapped_tokens"]
            if run.count_capture.matches(transcript, token) else None
        )
        final = captured.finals.get(key)
        for judge, own in by_judge.items():
            own[coord] = (
                judge_grade(captured.answers[key], judge, captured.questions[key[1]], final)
                if final is not None else scores[coord]
            )
    systems: dict[str, set[str]] = defaultdict(set)
    for system, model, _qid, _repeat in planned:
        systems[model].add(system)

    def claims(grades: dict[Any, Any]) -> dict[str, dict[str, Any]]:
        return {
            "C2": {
                m: asdict(c2(tokens, grades, kept, model=m, seed=run.seed))
                for m, arms in systems.items() if {"python-docs", "docs-fetch"} <= arms
            },
            "C3": {
                m: asdict(c3(grades, kept, model=m, seed=run.seed, offline_ok=run.offline_ok()))
                for m, arms in systems.items() if {"python-docs", "no-docs"} <= arms
                and run.families.get(m, {}).get("arm") == "air-gapped"
            },
        }

    actual = claims(scores)
    single = {j: actual if own == scores else claims(own) for j, own in by_judge.items()}
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
