"""Local normalizer controls for refusal annotations; no hosted execution claims."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import pytest

from benchmarks.grading.aggregate import has_final_record, vote_from_record, write_vote_record
from benchmarks.grading.inputs import judge_schema
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks import test_grading_score as runs
from tests.benchmarks.test_grading_aggregate import ITEM, PANEL, _request, _text
from tests.benchmarks.test_grading_receipt_contract import CASES, NORMALIZE, _outcome


def _saved(root: Path, judge_id: str, reason: str) -> tuple[Path, Any]:
    judge = PANEL.judges[judge_id]
    source = {"item_id": ITEM, "model_id": judge.model_id, "text": _text()}
    result = NORMALIZE[judge.provider](_outcome(source, judge.provider, reason))
    return _save_result(root, judge, result), result


def _save_result(root: Path, judge: Any, result: Any) -> Path:
    path = write_vote_record(
        root, target="answers", judge=judge, effort=judge.effort,
        pass_number=1, grading_run_id="selected", attempt=1, result=result,
        terminal=result.status != "succeeded", unit_id="unit", provider_batch_id="batch",
        input_sha256=_request().sha256(), schema=judge_schema(),
    )
    return path


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_bytes())


def _receipt(root: Path, path: Path, result: Any, *, panel=PANEL) -> None:
    record = _read(path)
    judge = panel.judges[record["judge_id"]]
    run_id = record["grading_run_id"]
    relative = f"{judge.id}/{record['item_id']}--a{record['attempt']}.json"
    history = {
        k: record[k] for k in (
            "grading_run_id", "unit_id", "item_id", "judge_id", "target", "pass", "attempt",
            "provider_batch_id", "input_sha256", "model_id", "effort",
        )
    }
    history.update(
        provider=judge.provider, api=judge.api, result=asdict(result),
        vote_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    history_path = root / "executions" / run_id / relative
    runs._json(history_path, history)
    run_path = root / "runs" / f"{run_id}.json"
    invocation = _read(run_path) if run_path.exists() else {"grading_run_id": run_id}
    invocation.setdefault("execution_results", {})[relative] = hashlib.sha256(
        history_path.read_bytes()
    ).hexdigest()
    runs._json(run_path, invocation)


@pytest.mark.parametrize("judge_id", ["haiku", "luna", "flash-lite"])
@pytest.mark.parametrize("refusal_flag", [None, False, True])
@pytest.mark.parametrize("bound", [False, True])
def test_successful_reply_cannot_be_withheld_as_a_refusal(
    tmp_path: Path, judge_id: str, refusal_flag: bool | None, bound: bool,
) -> None:
    path, _ = _saved(tmp_path, judge_id, "success")
    record = _read(path)
    request = _request() if bound else None
    assert vote_from_record(record, request=request) is not None
    if refusal_flag is None:
        record.pop("refusal", None)
    else:
        record["refusal"] = refusal_flag
    record.update(withheld="refusal", vote=None)
    with pytest.raises(BenchmarkValidationError, match="withheld|refusal"):
        vote_from_record(record, request=request)


@pytest.mark.parametrize("judge_id,reason", CASES)
@pytest.mark.parametrize("bound", [False, True])
def test_actual_normalized_withheld_results_stay_absent(
    tmp_path: Path, judge_id: str, reason: str, bound: bool,
) -> None:
    path, _ = _saved(tmp_path, judge_id, reason)
    record = _read(path)
    assert record["withheld"] is not None and record["vote"] is None
    assert vote_from_record(record, request=_request() if bound else None) is None
    assert has_final_record(
        tmp_path, target="answers", judge_id=judge_id, effort=record["effort"],
        pass_number=1, item_id=ITEM,
    )


@pytest.mark.parametrize("legacy", [False, True])
def test_mixed_openai_refusal_and_valid_text_need_the_actual_result(
    tmp_path: Path, legacy: bool,
) -> None:
    from benchmarks.grading.aggregate import recorded_result
    from benchmarks.grading.batch import openai_result

    path, result = _saved(tmp_path, "luna", "success")
    raw = result.raw
    raw["response"]["body"]["output"][0]["content"].append({
        "type": "refusal", "refusal": "benign refusal",
    })
    result = openai_result(raw)
    assert result.refusal and result.stop_reason == "completed" and result.text == _text()
    path.unlink()
    path = _save_result(tmp_path, PANEL.judges["luna"], result)
    record = _read(path)
    if legacy:
        record.pop("refusal")
    runs._json(path, record)
    with pytest.raises(BenchmarkValidationError, match="refusal|withheld"):
        vote_from_record(record, request=_request())
    _receipt(tmp_path, path, result)
    normalized = recorded_result(tmp_path, record, PANEL.judges["luna"])
    assert normalized == result
    assert vote_from_record(record, request=_request(), result=normalized) is None
    # A new completed ordinary reply cannot borrow that historical refusal.
    ordinary = openai_result(_outcome(record, "openai", "success"))
    with pytest.raises(BenchmarkValidationError, match="withheld|refusal|reply"):
        vote_from_record(record, request=_request(), result=ordinary)


@pytest.mark.parametrize("text", [None, "", "I cannot provide this answer."])
@pytest.mark.parametrize("bound", [False, True])
def test_plain_refusals_keep_retained_flags_without_inventing_a_vote(
    tmp_path: Path, text: str | None, bound: bool,
) -> None:
    path, _ = _saved(tmp_path, "luna", "refusal")
    record = _read(path)
    record["text"] = text
    assert record["refusal"] and record["stop_reason"] == "completed"
    assert vote_from_record(record, request=_request() if bound else None) is None
    record.pop("refusal")
    # No stop, normalized bit or raw block can distinguish refusal from a parse failure.
    with pytest.raises(BenchmarkValidationError, match="withheld"):
        vote_from_record(record, request=_request() if bound else None)


@pytest.mark.parametrize("provider,stop", [
    ("anthropic", "refusal"), ("google", "SAFETY"), ("google", "PROHIBITED_CONTENT"),
    ("google", "BLOCKLIST"), ("google", "SPII"), ("openai", "content_filter"),
])
def test_historical_refusal_specific_stops_retain_the_normalizer_semantics(
    tmp_path: Path, provider: str, stop: str,
) -> None:
    judge_id = {"anthropic": "haiku", "google": "flash-lite", "openai": "luna"}[provider]
    judge = PANEL.judges[judge_id]
    raw = _outcome({"item_id": ITEM, "model_id": judge.model_id, "text": _text()},
                   provider, "success")
    if provider == "anthropic":
        raw["result"]["message"]["stop_reason"] = stop
    elif provider == "google":
        raw["response"]["candidates"][0]["finishReason"] = stop
    else:
        raw["response"]["body"].update(status="incomplete", incomplete_details={"reason": stop})
    result = NORMALIZE[provider](raw)
    assert result.refusal and result.text == _text()
    path = _save_result(tmp_path, judge, result)
    record = _read(path)
    record.pop("refusal")
    assert vote_from_record(record, request=None) is None
    # Without that refusal stop or a recorded result, this is an ordinary
    # parseable reply; the annotation cannot supply its own evidence.
    record["stop_reason"] = {"anthropic": "end_turn", "google": "STOP",
                             "openai": "completed"}[provider]
    with pytest.raises(BenchmarkValidationError, match="withheld"):
        vote_from_record(record, request=None)


def test_scripted_receipt_replay_only_normalizes_the_selected_rule(tmp_path, monkeypatch):
    from benchmarks.grading.aggregate import recorded_result
    from benchmarks.grading.batch import ScriptedBatchClient

    judge = runs.PANEL.judges["luna"]
    client = ScriptedBatchClient("openai", judge.model_id, {"rules": [
        {"match": ".*", "text": _text(), "refusal": True},
    ]})
    result = client._result(ITEM)
    path = _save_result(tmp_path, judge, result)
    _receipt(tmp_path, path, result, panel=runs.PANEL)

    def reject_dispatch(*args, **kwargs):
        raise AssertionError("portable grading replay must not compile a script from the export")

    monkeypatch.setattr(ScriptedBatchClient, "__init__", reject_dispatch)
    assert recorded_result(tmp_path, _read(path), judge) == result


@pytest.mark.parametrize("refusal", [False, True])
def test_scripted_receipts_keep_explicit_flags_without_hosted_claims(
    tmp_path: Path, refusal: bool,
) -> None:
    from benchmarks.grading.aggregate import recorded_result
    from benchmarks.grading.batch import ScriptedBatchClient

    panel = runs.PANEL
    judge = panel.judges["luna"]
    client = ScriptedBatchClient("openai", judge.model_id, {"rules": [
        {"match": ".*", "text": _text(), "refusal": refusal},
    ]})
    result = client._result(ITEM)
    path = _save_result(tmp_path, judge, result)
    _receipt(tmp_path, path, result, panel=panel)
    record = _read(path)
    normalized = recorded_result(tmp_path, record, judge)
    assert normalized == result
    assert (vote_from_record(record, request=_request(), result=normalized) is None) is refusal
    from benchmarks.grading.execution_evidence import grading_execution_matches

    assert not grading_execution_matches(
        record, hashlib.sha256(path.read_bytes()).hexdigest(), vars(judge), {}, [],
        _read(tmp_path / "executions/selected" / judge.id / f"{ITEM}--a1.json"),
    )
    if refusal:
        with pytest.raises(BenchmarkValidationError, match="refusal"):
            vote_from_record(record, request=_request())
    record["refusal"] = not refusal
    runs._json(path, record)
    _receipt(tmp_path, path, result, panel=panel)
    with pytest.raises(BenchmarkValidationError, match="receipt"):
        recorded_result(tmp_path, record, judge)


def test_scripted_refusal_must_match_the_captured_rule(tmp_path: Path) -> None:
    from benchmarks.grading.aggregate import recorded_result
    from benchmarks.grading.batch import ScriptedBatchClient

    judge = runs.PANEL.judges["luna"]
    result = ScriptedBatchClient("openai", judge.model_id, {"rules": [
        {"match": ".*", "text": _text()},
    ]})._result(ITEM)
    path = _save_result(tmp_path, judge, result)
    record = _read(path)
    record.update(refusal=True, withheld="refusal", vote=None)
    runs._json(path, record)
    # Rebind both hashes and the normalized bit; the captured rule still says success.
    _receipt(tmp_path, path, replace(result, refusal=True), panel=runs.PANEL)
    with pytest.raises(BenchmarkValidationError, match="receipt"):
        recorded_result(tmp_path, record, judge)


def test_compact_receipt_cannot_evidence_a_mixed_refusal(tmp_path: Path) -> None:
    from benchmarks.grading.aggregate import recorded_result

    path, result = _saved(tmp_path, "luna", "success")
    record = _read(path)
    record.update(refusal=True, withheld="refusal", vote=None)
    runs._json(path, record)
    _receipt(tmp_path, path, replace(result, refusal=True, raw=None))
    normalized = recorded_result(tmp_path, record, PANEL.judges["luna"])
    assert normalized is None
    with pytest.raises(BenchmarkValidationError, match="refusal"):
        vote_from_record(record, request=_request(), result=normalized)


@pytest.mark.parametrize("judge_id,reason", CASES)
def test_historical_selected_receipt_replays_without_any_unrelated_run(
    tmp_path: Path, judge_id: str, reason: str,
) -> None:
    from benchmarks.grading.aggregate import recorded_result

    path, result = _saved(tmp_path, judge_id, reason)
    record = _read(path)
    record.pop("refusal", None)
    runs._json(path, record)
    _receipt(tmp_path, path, result)
    runs._json(tmp_path / "runs/unrelated.json", {"not": "a valid invocation"})
    normalized = recorded_result(tmp_path, record, PANEL.judges[judge_id])
    assert normalized == result
    assert vote_from_record(record, request=_request(), result=normalized) is None


def test_a_compact_terminal_receipt_does_not_require_raw_provider_output(tmp_path: Path) -> None:
    from benchmarks.grading.aggregate import recorded_result

    path, result = _saved(tmp_path, "haiku", "errored")
    _receipt(tmp_path, path, replace(result, raw=None))
    record = _read(path)
    assert record["terminal"] and record["vote"] is None
    assert recorded_result(tmp_path, record, PANEL.judges["haiku"]) is None
    assert vote_from_record(record, request=_request()) is None


@pytest.mark.parametrize("bad", ["hash", "identity", "vote-hash", "normalization", "refusal"])
def test_selected_receipts_reject_contradictions_even_after_hash_rebinding(
    tmp_path: Path, bad: str,
) -> None:
    from benchmarks.grading.aggregate import recorded_result

    path, result = _saved(tmp_path, "luna", "success")
    _receipt(tmp_path, path, result)
    assert recorded_result(tmp_path, _read(path), PANEL.judges["luna"]) == result
    run_path = tmp_path / "runs/selected.json"
    invocation = _read(run_path)
    relative = next(iter(invocation["execution_results"]))
    history_path = tmp_path / "executions/selected" / relative
    history = _read(history_path)
    if bad == "identity":
        history["unit_id"] = "different-unit"
    elif bad == "vote-hash":
        history["vote_sha256"] = "0" * 64
    elif bad == "normalization":
        history["result"]["refusal"] = True
    elif bad == "refusal":
        record = _read(path)
        record.update(refusal=True, withheld="refusal", vote=None)
        runs._json(path, record)
        history["vote_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    runs._json(history_path, history)
    if bad != "hash":
        digest = hashlib.sha256(history_path.read_bytes()).hexdigest()
        invocation["execution_results"][relative] = digest
    else:
        invocation["execution_results"][relative] = "0" * 64
    runs._json(run_path, invocation)
    with pytest.raises(BenchmarkValidationError, match="receipt"):
        recorded_result(tmp_path, _read(path), PANEL.judges["luna"])


@pytest.mark.parametrize("legacy", [False, True])
def test_score_rejects_two_invented_refusals_before_any_output_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, legacy: bool,
) -> None:
    run = runs._build_run(tmp_path)
    summary = runs._score(run, tmp_path)
    assert runs._record(run, "python-docs", "SYN-EX-001")["score"] == 1.0
    assert summary["claims"]["C2"][runs.FAMILY]["passed"] is True
    item = runs.safe_id(runs._cell("python-docs"), "SYN-EX-001")
    for judge in ("haiku", "luna"):
        path = next((run / "grading/votes/answers" / judge).glob(f"*/pass1/{item}/*.json"))
        record = _read(path)
        if legacy:
            record.pop("refusal", None)
        record.update(withheld="refusal", vote=None)
        runs._json(path, record)
    # Create the external gate input before observing writes under the run.
    g1 = runs._g1_file(run, tmp_path / "g1.json")
    before = {p.relative_to(run): p.read_bytes() for p in run.rglob("*") if p.is_file()}
    attempts = []
    original = Path.open

    def opened(path: Path, mode: str = "r", *args: Any, **kwargs: Any):
        if path.is_relative_to(run) and any(flag in mode for flag in "wax+"):
            attempts.append(path)
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", opened)
    with pytest.raises(BenchmarkValidationError, match="withheld|refusal"):
        runs.score_run_dir(run, schema_path=runs.SCHEMA, g1_paths=[g1])
    assert attempts == []
    assert {p.relative_to(run): p.read_bytes() for p in run.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("missing", ["invocation", "execution_results"])
def test_score_rejects_two_forged_flags_without_response_evidence(
    tmp_path: Path, missing: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.benchmarks.accepted_report_fixture import rebuild_grading

    run = runs._build_run(tmp_path)
    rebuild_grading(run)  # local provider-shaped replies, never provider contact
    grading = run / "grading"
    summary = runs._score(run, tmp_path)
    assert runs._record(run, "python-docs", "SYN-EX-001")["score"] == 1.0
    assert summary["claims"]["C2"][runs.FAMILY]["passed"] is True
    # Ordinary historical replies remain useful without a receipt manifest.
    for path in (grading / "runs").glob("*.json"):
        if missing == "invocation":
            path.unlink()
        else:
            invocation = _read(path)
            invocation.pop("execution_results", None)
            runs._json(path, invocation)
    if missing == "invocation":
        # Retain panel selection from another run, but no selected vote's run.
        runs._json(grading / "runs/composition-only.json", {
            "target": "answers", "pass": 1, "composition": runs.COMPOSITION,
        })
    assert runs._score(run, tmp_path)["claims"]["C2"][runs.FAMILY]["passed"] is True
    item = runs.safe_id(runs._cell("python-docs"), "SYN-EX-001")
    for judge in ("haiku", "luna"):
        path = next((grading / "votes/answers" / judge).glob(f"*/pass1/{item}/*.json"))
        record = _read(path)
        assert record["status"] == "succeeded" and record["vote"] is not None
        record.update(refusal=True, withheld="refusal", vote=None)
        runs._json(path, record)
    g1 = runs._g1_file(run, tmp_path / "g1.json")
    before = {p.relative_to(run): p.read_bytes() for p in run.rglob("*") if p.is_file()}
    attempts = []
    original = Path.open

    def opened(path: Path, mode: str = "r", *args: Any, **kwargs: Any):
        if path.is_relative_to(run) and any(flag in mode for flag in "wax+"):
            attempts.append(path)
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", opened)
    with pytest.raises(BenchmarkValidationError, match="refusal|evidence"):
        wrong = runs.score_run_dir(run, schema_path=runs.SCHEMA, g1_paths=[g1])
        print("wrong technical result:", {
            "score": runs._record(run, "python-docs", "SYN-EX-001")["score"],
            "pending_cells": wrong["pending_cells"],
            "C2": wrong["claims"]["C2"][runs.FAMILY]["passed"],
        })
    assert attempts == []
    assert {p.relative_to(run): p.read_bytes() for p in run.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize(
    "reason,count,reference", [
        ("refusal", 1, False), ("refusal", 2, False), ("refusal", 2, True),
        ("parse_failure", 2, True), ("output_cap", 2, True), ("errored", 2, True),
    ],
)
def test_score_keeps_real_majority_reference_and_pending_cases(
    tmp_path: Path, reason: str, count: int, reference: bool,
) -> None:
    run = runs._build_run(tmp_path)
    grading = run / "grading"
    cell, qid = runs._cell("python-docs"), "SYN-EX-001"
    item = runs.safe_id(cell, qid)
    assert runs._score(run, tmp_path)["claims"]["C2"][runs.FAMILY]["passed"] is True
    assert runs._record(run, "python-docs", qid)["score"] == 1.0
    for judge_id in ("haiku", "luna")[:count]:
        path = next((grading / "votes/answers" / judge_id).glob(f"*/pass1/{item}/*.json"))
        old = _read(path)
        judge = runs.PANEL.judges[judge_id]
        outcome = "paid-error" if reason == "errored" and judge_id == "luna" else reason
        result = NORMALIZE[judge.provider](_outcome(old, judge.provider, outcome))
        path.unlink()
        write_vote_record(
            grading, target="answers", judge=judge, effort=old["effort"], pass_number=1,
            grading_run_id=old["grading_run_id"], attempt=1, result=result,
            terminal=result.status != "succeeded", unit_id=old["unit_id"],
            provider_batch_id=old["provider_batch_id"], input_sha256=old["input_sha256"],
            schema=judge_schema(),
        )
    if reference:
        runs._vote(grading, "reference", item, runs._vote_text(), target="reference", effort="high")
    before_votes = {p: p.read_bytes() for p in (grading / "votes").rglob("*.json")}
    summary = runs._score(run, tmp_path)
    record = runs._record(run, "python-docs", qid)
    if count == 2 and not reference:
        assert record["score"] is None
        assert summary["pending_cells"] == [f"{cell}/{qid}"]
        assert summary["claims"]["C2"][runs.FAMILY]["passed"] is None
    else:
        assert record["score"] == 1.0
        assert record["scoring_method"] == ("llm_reference" if reference else "llm_panel")
        assert summary["claims"]["C2"][runs.FAMILY]["passed"] is True
    assert {p: p.read_bytes() for p in (grading / "votes").rglob("*.json")} == before_votes


@pytest.mark.parametrize("bound", [False, True])
def test_a_saved_absent_vote_is_never_invented_from_its_text(tmp_path: Path, bound: bool) -> None:
    path, _ = _saved(tmp_path, "haiku", "success")
    record = _read(path)
    record["vote"] = None
    with pytest.raises(BenchmarkValidationError, match="stored vote"):
        vote_from_record(record, request=_request() if bound else None)


@pytest.mark.parametrize("legacy", [False, True])
def test_scoring_uses_the_mixed_refusals_actual_receipt(tmp_path: Path, legacy: bool) -> None:
    from benchmarks.grading.batch import openai_result
    from tests.benchmarks.accepted_report_fixture import rebuild_grading

    run = runs._build_run(tmp_path)
    rebuild_grading(run)  # actual provider normalizers and canonical vote writer, all local
    grading = run / "grading"
    item = runs.safe_id(runs._cell("python-docs"), "SYN-EX-001")
    path = next((grading / "votes/answers/luna").glob(f"*/pass1/{item}/*.json"))
    old = _read(path)
    result = openai_result(_outcome(old, "openai", "success"))
    raw = result.raw
    raw["response"]["body"]["output"][0]["content"].append({
        "type": "refusal", "refusal": "benign mixed refusal",
    })
    result = openai_result(raw)
    path.unlink()
    path = write_vote_record(
        grading, target="answers", judge=runs.PANEL.judges["luna"], effort=old["effort"],
        pass_number=1, grading_run_id=old["grading_run_id"], attempt=old["attempt"],
        result=result, terminal=False, unit_id=old["unit_id"],
        provider_batch_id=old["provider_batch_id"], input_sha256=old["input_sha256"],
        schema=judge_schema(),
    )
    if legacy:
        record = _read(path)
        record.pop("refusal")
        runs._json(path, record)
    _receipt(grading, path, result, panel=runs.PANEL)
    summary = runs._score(run, tmp_path)
    assert runs._record(run, "python-docs", "SYN-EX-001")["score"] == 1.0
    assert summary["claims"]["C2"][runs.FAMILY]["passed"] is True
    assert summary["diagnostics"]["per_judge"]["luna"]["withheld"] == {"refusal": 1}
    # Remove only the actual refusal block and rebind the unsigned receipt.
    # The same saved annotation now contradicts an ordinary completed reply.
    raw["response"]["body"]["output"][0]["content"].pop()
    _receipt(grading, path, openai_result(raw), panel=runs.PANEL)
    before = {p.relative_to(run): p.read_bytes() for p in run.rglob("*") if p.is_file()}
    with pytest.raises(BenchmarkValidationError, match="withheld|receipt"):
        runs._score(run, tmp_path)
    assert {p.relative_to(run): p.read_bytes() for p in run.rglob("*") if p.is_file()} == before


def test_writer_retains_the_normalizers_refusal_flag(tmp_path: Path) -> None:
    path, result = _saved(tmp_path, "luna", "refusal")
    assert _read(path)["refusal"] is result.refusal is True
    path, result = _saved(tmp_path / "ordinary", "luna", "success")
    assert _read(path)["refusal"] is result.refusal is False


@pytest.mark.parametrize("count", [1, 2])
@pytest.mark.parametrize("legacy", [False, True])
def test_scripted_mixed_receipts_preserve_technical_majority_and_pending(
    tmp_path: Path, count: int, legacy: bool,
) -> None:
    from benchmarks.grading.batch import ScriptedBatchClient

    run = runs._build_run(tmp_path)
    grading = run / "grading"
    item = runs.safe_id(runs._cell("python-docs"), "SYN-EX-001")
    assert runs._score(run, tmp_path)["claims"]["C2"][runs.FAMILY]["passed"] is True
    for judge_id in ("haiku", "luna")[:count]:
        old_path = next((grading / "votes/answers" / judge_id).glob(f"*/pass1/{item}/*.json"))
        old = _read(old_path)
        judge = runs.PANEL.judges[judge_id]
        result = ScriptedBatchClient(judge.provider, judge.model_id, {"rules": [
            {"match": ".*", "text": old["text"], "refusal": True},
        ]})._result(item)
        old_path.unlink()
        path = write_vote_record(
            grading, target="answers", judge=judge, effort=old["effort"], pass_number=1,
            grading_run_id=f"scripted-{judge_id}", attempt=1, result=result,
            terminal=False, unit_id=old["unit_id"], provider_batch_id="scripted-batch-1",
            input_sha256=old["input_sha256"], schema=judge_schema(),
        )
        if legacy:
            record = _read(path)
            record.pop("refusal")
            runs._json(path, record)
        _receipt(grading, path, result, panel=runs.PANEL)
    summary = runs._score(run, tmp_path)
    record = runs._record(run, "python-docs", "SYN-EX-001")
    if count == 1:
        assert record["score"] == 1.0 and record["grading"]["valid_votes"] == 2
        assert summary["claims"]["C2"][runs.FAMILY]["passed"] is True
    else:
        assert record["score"] is None
        assert summary["pending_cells"] == [f"{runs._cell('python-docs')}/SYN-EX-001"]
        assert summary["claims"]["C2"][runs.FAMILY]["passed"] is None
