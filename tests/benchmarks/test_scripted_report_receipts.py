"""Technical report replay consumes the same recorded script evidence as scoring."""

from collections import Counter
from dataclasses import replace

import pytest

from benchmarks import agentic_report, report_files, report_measurements
from benchmarks.agentic_report import _RunMetadata
from benchmarks.grading import aggregate, publication
from benchmarks.grading.aggregate import write_vote_record
from benchmarks.grading.batch import ScriptedBatchClient, scripted_result
from benchmarks.grading.inputs import judge_schema, safe_id
from benchmarks.report import _load_run_bundle
from tests.benchmarks import test_agentic_report as reports
from tests.benchmarks.test_grading_refusal_integrity import _read, _receipt


def _build(tmp_path):
    return reports._build(
        tmp_path, families_run=("scripted-anthropic",), questions=("SYN-EX-001",),
        suspect_answer=False,
    )


def _script_vote(
    run, judge_id, *, pass_number=1, refusal=True, legacy=False, proof="raw", outcome=None,
):
    root = run / "grading"
    item = safe_id("python-docs--scripted-anthropic--r1", "SYN-EX-001")
    old_path = next((root / "votes/answers" / judge_id).glob(f"*/pass1/{item}/*.json"))
    old = _read(old_path)
    judge = reports.PANEL.judges[judge_id]
    # Deliberately invalid selector: readers must normalize only this captured rule,
    # without compiling or dispatching any pattern declared by the export.
    result = scripted_result(item, {"match": "[", "text": old["text"], "refusal": refusal},
                             provider=judge.provider, model_id=judge.model_id)
    if outcome == "refusal_stop":
        result = replace(result, refusal=True, stop_reason="refusal")
    elif outcome == "errored":
        result = replace(result, status="errored", refusal=False, text=None)
    if pass_number == 1:
        old_path.unlink()
    path = write_vote_record(
        root, target="answers", judge=judge, effort=old["effort"], pass_number=pass_number,
        grading_run_id=f"script-{judge_id}-p{pass_number}", attempt=1, result=result,
        terminal=result.status != "succeeded", unit_id=old["unit_id"],
        provider_batch_id="scripted-batch-1",
        input_sha256=old["input_sha256"], schema=judge_schema(),
    )
    if legacy:
        record = _read(path)
        record.pop("refusal")
        reports._json(path, record)
    if proof == "forged":
        result = scripted_result(item, {"match": "[", "text": old["text"], "refusal": False},
                                 provider=judge.provider, model_id=judge.model_id)
        result = replace(result, refusal=True)
    elif proof == "compact":
        result = replace(result, raw=None)
    if proof != "absent":
        _receipt(root, path, result, panel=reports.PANEL)
    return path


def _capture_reads(monkeypatch):
    seen = Counter()
    reader = report_files.artifact_bytes

    def read(root, path):
        if "/grading/votes/" in str(path) or "/grading/executions/" in str(path):
            seen[path] += 1
        return reader(root, path)

    for module in (agentic_report, report_files, report_measurements, aggregate, publication):
        monkeypatch.setattr(module, "artifact_bytes", read)

    def no_dispatch(*args, **kwargs):
        raise AssertionError("an exported script must never be compiled or executed")

    monkeypatch.setattr(ScriptedBatchClient, "__init__", no_dispatch)
    return seen


@pytest.mark.parametrize("count", [1, 2])
@pytest.mark.parametrize("legacy", [False, True])
def test_report_preserves_receipted_scripted_majority_and_pending(
    tmp_path, monkeypatch, count, legacy,
):
    run = _build(tmp_path)
    for judge in ("haiku", "luna")[:count]:
        _script_vote(run, judge, legacy=legacy)
    reports._score(run, tmp_path)
    key = ("python-docs--scripted-anthropic--r1", "SYN-EX-001")
    scored = _read(run / "scoring" / key[0] / f"{key[1]}.json")
    assert scored["score"] == (1.0 if count == 1 else None)
    if count == 1:
        assert scored["grading"]["valid_votes"] == 2
    seen = _capture_reads(monkeypatch)
    metadata = _RunMetadata(_load_run_bundle(run))
    assert metadata.grading == "scripted"
    assert metadata.grade_capture.reconciled
    av = metadata.grade_capture.answers[key]
    assert sum(v is not None for v in av.votes.values()) == 3 - count
    assert (key in metadata.grade_capture.finals) == (count == 1)
    assert metadata.summary["diagnostics"]["per_judge"]["haiku"]["withheld"] == {"refusal": 1}
    assert metadata.grade_capture.graded_answers is None
    assert not metadata.retained
    assert seen and set(seen.values()) == {1}
    seen.clear()
    text = reports._render(run)
    assert r'haiku: 3 records; withheld {"refusal"\: 1}' in text
    assert "scripted grading" in text
    assert seen and set(seen.values()) == {1}


@pytest.mark.parametrize("proof", ["raw", "compact", "forged", "absent"])
@pytest.mark.parametrize("legacy", [False, True])
def test_scripted_second_pass_requires_actual_mixed_refusal_proof(
    tmp_path, monkeypatch, proof, legacy,
):
    run = _build(tmp_path)
    reports._score(run, tmp_path)
    for judge in reports.JUDGES:
        _script_vote(run, judge, pass_number=2, refusal=judge == "haiku", proof=proof,
                     legacy=legacy)
    seen = _capture_reads(monkeypatch)
    second = {}
    replay = publication._second_pass

    def capture_second(*args):
        second.update(replay(*args))
        return second

    monkeypatch.setattr(publication, "_second_pass", capture_second)
    metadata = _RunMetadata(_load_run_bundle(run))
    assert metadata.grading == "scripted"
    assert metadata.grade_capture.reconciled
    assert metadata.retest_unknown is (proof != "raw")
    retest = metadata.summary["diagnostics"]["test_retest"]
    if proof == "raw":
        key = ("python-docs--scripted-anthropic--r1", "SYN-EX-001")
        assert second[key].votes["haiku"] is None
        assert second[key].votes["luna"] is not None
        assert retest["haiku"]["alpha"] is None
        assert retest["luna"]["items"] == 1
    else:
        assert retest is None
    assert seen and set(seen.values()) == {1}
    seen.clear()
    text = reports._render(run)
    assert ("Real-answer test–retest: unknown (unverified pass-2 evidence)" in text) == (
        proof != "raw"
    )
    assert "scripted grading" in text
    assert seen and set(seen.values()) == {1}


@pytest.mark.parametrize("proof", ["compact", "forged", "absent"])
def test_scripted_pass1_flags_without_proof_cannot_supply_diagnostics(tmp_path, proof):
    run = _build(tmp_path)
    path = _script_vote(run, "haiku")
    reports._score(run, tmp_path)
    # Preserve the honest saved two-judge outcome, but remove its response proof.
    record = _read(path)
    judge = reports.PANEL.judges["haiku"]
    result = scripted_result(record["item_id"], {
        "match": "[", "text": record["text"], "refusal": proof != "forged",
    }, provider=judge.provider, model_id=judge.model_id)
    if proof == "compact":
        result = replace(result, raw=None)
    elif proof == "forged":
        result = replace(result, refusal=True)
    if proof == "absent":
        (run / "grading/runs" / f"{record['grading_run_id']}.json").unlink()
    else:
        _receipt(run / "grading", path, result, panel=reports.PANEL)
    metadata = _RunMetadata(_load_run_bundle(run))
    assert metadata.grading == "unknown"
    assert not metadata.grade_capture.reconciled
    assert not metadata.summary["diagnostics"]
    assert r'haiku: 3 records; withheld {"refusal"\: 1}' not in reports._render(run)


def test_historical_receiptless_scripted_statistics_remain_available(tmp_path):
    run = _build(tmp_path)
    reports._score(run, tmp_path)
    metadata = _RunMetadata(_load_run_bundle(run))
    assert metadata.grading == "scripted"
    assert metadata.grade_capture.reconciled
    assert metadata.summary["diagnostics"]["per_judge"]["haiku"]["records"] == 3
    assert metadata.grade_capture.graded_answers is None
    assert "haiku: 3 records; withheld none" in reports._render(run)


@pytest.mark.parametrize("pass_number", [1, 2])
@pytest.mark.parametrize("outcome", ["success", "refusal_stop", "errored"])
def test_unambiguous_receiptless_scripted_history(tmp_path, pass_number, outcome):
    run = _build(tmp_path)
    reports._score(run, tmp_path)
    _script_vote(run, "haiku", pass_number=pass_number, refusal=False, proof="absent",
                 outcome=outcome)
    if pass_number == 1:
        reports._score(run, tmp_path)
    metadata = _RunMetadata(_load_run_bundle(run))
    assert metadata.grading == "scripted"
    assert metadata.grade_capture.reconciled
    assert not metadata.retest_unknown
    assert metadata.summary["diagnostics"]["per_judge"]["haiku"]["records"] == 3
    assert "haiku: 3 records; withheld" in reports._render(run)


def test_scripted_reference_with_hosted_panel_keeps_only_technical_statistics(tmp_path):
    import yaml

    run = reports._live_like_run(tmp_path)
    panel_path = run / "grading/inputs/panel.yml"
    panel = yaml.safe_load(panel_path.read_bytes())
    panel["judges"]["reference"].update(api="scripted", script="unused.json")
    panel_path.write_text(yaml.safe_dump(panel))
    reports._refresh_manifest(run)
    # This intact, unambiguous reference reply is historical technical evidence.
    (run / "grading/runs/ref-1.json").unlink()
    composition = _read(run / "grading/runs/run-1.json")["composition"]
    reports._score(run, tmp_path, panel_path=panel_path, composition=composition)
    metadata = _RunMetadata(_load_run_bundle(run))
    assert metadata.grading == "scripted"
    assert metadata.grade_capture.reconciled
    assert metadata.summary["diagnostics"]["per_judge"]["haiku"]["records"] == 12
    assert metadata.grade_capture.graded_answers is None
    assert not metadata.retained
    assert metadata.grading_spend["per_graded_answer"] == {}
    assert "scripted grading" in reports._render(run)
    assert "fewer normalized tokens" not in reports._readme(run)
