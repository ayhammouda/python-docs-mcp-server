"""Authentic normalizer shapes in unsigned bundles; no hosted execution is claimed."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import pytest

from benchmarks.agentic.protocol import KeyLeakError
from benchmarks.grading.aggregate import final_grade, load_answer_votes, write_vote_record
from benchmarks.grading.batch import anthropic_result, gemini_result, openai_result
from benchmarks.grading.inputs import judge_schema
from benchmarks.grading.mapping import grade, panel_fields, parse_vote
from benchmarks.grading.panel import load_panel
from tests.benchmarks.accepted_report_fixture import raw_result, write
from tests.benchmarks.test_agentic_measure_evidence import _grading, _receipts_match
from tests.benchmarks.test_grading_aggregate import QUESTION

NORMALIZE = {"anthropic": anthropic_result, "openai": openai_result, "google": gemini_result}
# Deliberately synthetic USD measurements, differing by provider; no current price claim.
AMOUNTS = {"anthropic": "0.0000175", "openai": "0.000022", "google": "0.000002"}
CASES = [
    (judge, reason)
    for judge in ("haiku", "luna", "flash-lite", "reference")
    for reason in ("refusal", "output_cap", "parse_failure")
] + [
    ("haiku", status) for status in ("errored", "expired", "canceled")
] + [
    ("luna", reason)
    for reason in ("rejected", "expired", "paid-error", "partial-usage", "no-usage", "zero-error")
] + [("flash-lite", "errored")] + [
    (judge, "zero-refusal") for judge in ("haiku", "luna", "flash-lite", "reference")
]


def _read(path: Path) -> Any:
    return json.loads(path.read_bytes())


def _outcome(vote: dict[str, Any], provider: str, reason: str) -> dict[str, Any]:
    zero = reason == "zero-refusal"
    reason = "refusal" if zero else reason
    raw = raw_result(provider, vote["item_id"], vote["model_id"], vote["text"])
    if provider == "anthropic":
        if reason in ("errored", "expired", "canceled"):
            raw["result"] = {"type": reason, "error": {"type": "api_error"}}
        else:
            message = raw["result"]["message"]
            message["usage"].update(cache_read_input_tokens=2, cache_creation_input_tokens=1)
            if reason in ("refusal", "output_cap"):
                message["stop_reason"] = "refusal" if reason == "refusal" else "max_tokens"
            elif reason == "parse_failure":
                message["content"][0]["text"] = "benign malformed reply"
    elif provider == "openai":
        body = raw["response"]["body"]
        body["usage"].update(
            total_tokens=15, input_tokens_details={"cached_tokens": 2},
            output_tokens_details={"reasoning_tokens": 1},
        )
        if reason == "rejected":
            raw["response"] = {"status_code": 400, "body": {"error": {"code": "invalid_request"}}}
        elif reason == "expired":
            raw = {"custom_id": vote["item_id"], "error": {"code": "batch_expired"}}
        elif reason in ("paid-error", "partial-usage", "no-usage", "zero-error"):
            body["status"] = "failed"
            if reason == "partial-usage":
                body["usage"] = {"input_tokens": 10}
            elif reason == "no-usage":
                body.pop("usage")
            elif reason == "zero-error":
                body["usage"] = {"input_tokens": 0, "output_tokens": 0}
        elif reason == "refusal":
            body["output"][0]["content"] = [{"type": "refusal", "refusal": "benign refusal"}]
        elif reason == "output_cap":
            body.update(status="incomplete", incomplete_details={"reason": "max_output_tokens"})
        elif reason == "parse_failure":
            body["output"][0]["content"][0]["text"] = "benign malformed reply"
    else:
        response = raw["response"]
        response["usageMetadata"].update(cachedContentTokenCount=2, thoughtsTokenCount=1)
        if reason == "errored":
            raw = {"key": vote["item_id"], "error": {"code": 500, "message": "benign error"}}
        elif reason in ("refusal", "output_cap"):
            response["candidates"][0]["finishReason"] = (
                "SAFETY" if reason == "refusal" else "MAX_TOKENS"
            )
        elif reason == "parse_failure":
            response["candidates"][0]["content"]["parts"][0]["text"] = "benign malformed reply"
    if zero:
        if provider == "anthropic":
            raw["result"]["message"]["usage"] = {"input_tokens": 0, "output_tokens": 0}
        elif provider == "openai":
            raw["response"]["body"]["usage"] = {"input_tokens": 0, "output_tokens": 0}
        else:
            raw["response"]["usageMetadata"] = {"promptTokenCount": 0, "candidatesTokenCount": 0}
    return raw


def _replace(root: Path, judge_id: str, reason: str) -> tuple[Path, Path, Path]:
    """Re-emit a normal provider result through the real vote writer and receipt layout."""
    panel = load_panel(Path("docs/benchmarks/grading/panel.yml"))
    judge = panel.judges[judge_id]
    path = next((root / "grading/votes").glob(f"*/{judge_id}/*/pass1/*/*.json"))
    old = _read(path)
    result = NORMALIZE[judge.provider](_outcome(old, judge.provider, reason))
    path.unlink()  # exclusive production writer, same selected attempt
    path = write_vote_record(
        root / "grading", target=old["target"], judge=judge, effort=old["effort"],
        pass_number=1, grading_run_id=old["grading_run_id"], attempt=1, result=result,
        terminal=result.status != "succeeded", unit_id=old["unit_id"],
        provider_batch_id=old["provider_batch_id"], input_sha256=old["input_sha256"],
        schema=judge_schema(),
    )
    run_path = root / "grading/runs" / f"{old['grading_run_id']}.json"
    run = _read(run_path)
    relative = f"{judge_id}/{old['item_id']}--a1.json"
    history_path = root / "grading/executions" / old["grading_run_id"] / relative
    history = _read(history_path)
    history.update(vote_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), result=asdict(result))
    body = write(history_path, history)
    run["execution_results"][relative] = hashlib.sha256(body).hexdigest()
    events = _read(root / "events.json")["events"]
    settle = next(e for e in events if e["event"] == "settle" and e["unit_id"] == old["unit_id"])
    if result.unbilled:
        status, amount = "unbilled", "0"
    elif result.buckets is None or (result.buckets.is_empty and result.status == "succeeded"):
        status, amount = "no_usage", "2"  # single-item bound=1, host books twice the bound
    else:
        status = "ok"
        amount = "0" if result.buckets.is_empty else AMOUNTS[judge.provider]
    settle.update(actual=amount if status == "ok" else "0", booked=amount)
    settle["items"] = [{"item_id": old["item_id"], "status": status, "booked": amount}]
    run["spent_after"][judge.provider] = amount
    write(root / "events.json", {"events": events})
    write(run_path, run)
    return path, history_path, run_path


def _rebind(root: Path, path: Path, history_path: Path, run_path: Path) -> None:
    """Update unsigned hashes after a deliberate forgery: hashes cannot authenticate a bundle."""
    history, run = _read(history_path), _read(run_path)
    history["vote_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    body = write(history_path, history)
    relative = history_path.relative_to(root / "grading/executions" / history["grading_run_id"])
    run["execution_results"][relative.as_posix()] = hashlib.sha256(body).hexdigest()
    write(run_path, run)


@pytest.mark.parametrize("judge,reason", CASES)
def test_normalized_withheld_receipt_can_support_a_correct_panel(
    tmp_path: Path, judge: str, reason: str,
) -> None:
    _grading(tmp_path, history=True)
    assert _receipts_match(tmp_path)
    path, _, _ = _replace(tmp_path, judge, reason)
    assert _read(path)["withheld"] is not None
    assert _receipts_match(tmp_path)
    votes = {
        j: parse_vote(_read(next((tmp_path / "grading/votes/answers" / j).rglob("*.json")))["text"])
        if j != judge else None
        for j in ("haiku", "luna", "flash-lite")
    }
    fields = panel_fields(votes)
    assert fields.valid_votes >= 2
    assert grade(fields.fields, ("core", "nuance")) == (1.0, "CORRECT")


@pytest.mark.parametrize("judge,reason", CASES)
@pytest.mark.parametrize("bad", ["wrong-reason", "free", "bad-spend", "no-raw", "no-manifest"])
def test_withheld_receipts_still_require_matching_raw_and_money(
    tmp_path: Path, judge: str, reason: str, bad: str,
) -> None:
    _grading(tmp_path, history=True)
    path, history_path, run_path = _replace(tmp_path, judge, reason)
    assert _receipts_match(tmp_path)  # positive control before every mutation
    if bad == "wrong-reason":
        vote = _read(path)
        vote["withheld"] = "refusal" if vote["withheld"] != "refusal" else "parse_failure"
        write(path, vote)
        _rebind(tmp_path, path, history_path, run_path)
    elif bad == "no-raw":
        history = _read(history_path)
        history["result"]["raw"] = None
        write(history_path, history)
        _rebind(tmp_path, path, history_path, run_path)
    elif bad == "no-manifest":
        run = _read(run_path)
        run["execution_results"] = {}
        write(run_path, run)
    elif bad == "bad-spend":
        run = _read(run_path)
        run["spent_after"] = dict.fromkeys(run["spent_after"], "987")
        write(run_path, run)
    else:
        events = _read(tmp_path / "events.json")["events"]
        vote, run = _read(path), _read(run_path)
        settlement = next(
            e for e in events if e["event"] == "settle" and e["unit_id"] == vote["unit_id"]
        )
        # Reconcile every total so only the category/amount mismatch can reject it.
        free = settlement["items"][0]["status"] == "unbilled"
        status, amount = ("ok", "0.1") if free else ("unbilled", "0")
        settlement.update(booked=amount, actual=amount if status == "ok" else "0")
        settlement["items"][0].update(status=status, booked=amount)
        provider = load_panel(Path("docs/benchmarks/grading/panel.yml")).judges[judge].provider
        run["spent_after"][provider] = amount
        write(run_path, run)
        write(tmp_path / "events.json", {"events": events})
    assert not _receipts_match(tmp_path)


@pytest.mark.parametrize("reason", ["refusal", "output_cap", "parse_failure", "errored"])
def test_two_withheld_panel_votes_leave_a_real_reference_grade(tmp_path: Path, reason: str) -> None:
    _grading(tmp_path, history=True)
    assert _receipts_match(tmp_path)
    _replace(tmp_path, "haiku", reason)
    _replace(tmp_path, "luna", "paid-error" if reason == "errored" else reason)
    assert _receipts_match(tmp_path)
    item = next((tmp_path / "grading/votes/answers/haiku").rglob("*.json")).parent.name
    write(tmp_path / "grading/items/answers" / f"{item}.json", {
        "meta": {"competitor_id": "python-docs--model--r1", "corpus_id": "question"},
    })
    panel = load_panel(Path("docs/benchmarks/grading/panel.yml"))
    answers = load_answer_votes(
        tmp_path / "grading", panel=panel,
        judges=dict.fromkeys(("haiku", "luna", "flash-lite"), "low"),
    )
    answer = answers["python-docs--model--r1", "question"]
    result = final_grade(answer, replace(QUESTION, id="question"), inputs={})
    assert result is not None
    assert (result["score"], result["label"], result["method"]) == (1.0, "CORRECT", "llm_reference")


@pytest.mark.parametrize("judge", ["haiku", "luna", "flash-lite", "reference"])
@pytest.mark.parametrize("bad", ["free-success", "fake-withheld", "zero-success", "scripted"])
def test_success_cannot_be_forged_into_free_or_withheld_evidence(
    tmp_path: Path, judge: str, bad: str,
) -> None:
    _grading(tmp_path, history=True)
    path, history_path, run_path = _replace(tmp_path, judge, "success")
    assert _receipts_match(tmp_path)
    vote, history, run = _read(path), _read(history_path), _read(run_path)
    provider = history["provider"]
    if bad == "fake-withheld":
        vote.update(withheld="parse_failure", vote=None)
    elif bad == "scripted":
        history["result"]["raw"]["scripted_rule"] = {"status": "succeeded"}
    else:
        if bad == "zero-success":
            raw = raw_result(provider, vote["item_id"], vote["model_id"], vote["text"])
            if provider == "anthropic":
                raw["result"]["message"]["usage"] = {"input_tokens": 0, "output_tokens": 0}
            elif provider == "openai":
                raw["response"]["body"]["usage"] = {"input_tokens": 0, "output_tokens": 0}
            else:
                raw["response"]["usageMetadata"] = {
                    "promptTokenCount": 0, "candidatesTokenCount": 0,
                }
            result = NORMALIZE[provider](raw)
            history["result"] = asdict(result)
            vote.update(raw_usage=result.raw_usage, buckets=asdict(result.buckets))
        events = _read(tmp_path / "events.json")["events"]
        settlement = next(
            e for e in events if e["event"] == "settle" and e["unit_id"] == vote["unit_id"]
        )
        settlement.update(actual="0", booked="0")
        settlement["items"][0].update(
            status="ok" if bad == "zero-success" else "unbilled", booked="0",
        )
        run["spent_after"][provider] = "0"
        write(tmp_path / "events.json", {"events": events})
    write(path, vote)
    write(history_path, history)
    write(run_path, run)
    _rebind(tmp_path, path, history_path, run_path)
    assert not _receipts_match(tmp_path)


@pytest.mark.parametrize("compact", [False, True])
def test_oversize_free_error_without_retained_raw_remains_unverifiable(
    tmp_path: Path, compact: bool,
) -> None:
    _grading(tmp_path, history=True)
    path, history_path, run_path = _replace(tmp_path, "haiku", "errored")
    assert _receipts_match(tmp_path)
    history = _read(history_path)
    # The producer's drop_batch_raw retains this on the wire, but _Collector
    # strips it before writing history. Do not manufacture a raw provider line.
    evidence = {"custom_id": history["item_id"], "result": {"type": "errored"}}
    history["result"]["raw"] = None
    if compact:
        history["billing_evidence"] = evidence
    write(history_path, history)
    _rebind(tmp_path, path, history_path, run_path)
    before = history_path.read_bytes()
    assert not _receipts_match(tmp_path)
    assert history_path.read_bytes() == before
    assert _read(history_path)["result"]["raw"] is None


def test_receipt_scanner_error_remains_an_error_before_malformed_fallback(tmp_path: Path) -> None:
    _grading(tmp_path, history=True)
    _, history_path, _ = _replace(tmp_path, "haiku", "refusal")
    assert _receipts_match(tmp_path)
    marker = "AIza" + "g" * 35  # synthetic scanner fixture, never a credential
    history_path.write_text('broken {"note":"' + marker + '"}')
    with pytest.raises(KeyLeakError) as error:
        _receipts_match(tmp_path)
    assert marker not in str(error.value)
