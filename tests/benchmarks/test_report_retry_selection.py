"""Unselected retries retain history without overriding the selected reply."""

import hashlib

import pytest

from benchmarks.agentic_report import _RunMetadata
from benchmarks.grading.score import score_run_dir
from benchmarks.report import _load_run_bundle
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks import test_agentic_report as reports
from tests.benchmarks.test_grading_refusal_integrity import _read
from tests.benchmarks.test_report_grade_evidence import (
    recorded_template as recorded_template,
)
from tests.benchmarks.test_report_grade_evidence import (
    recording as recording,
)
from tests.benchmarks.test_scripted_report_receipts import (
    _build,
    _capture_reads,
    _script_vote,
)


def _retry(run, selected, *, missing=True, terminal=False):
    row = _read(selected)
    row.update(
        grading_run_id="unselected-retry", attempt=1,
        graded_at="1900-01-01T00:00:00Z" if terminal else "9999-01-01T00:00:00Z",
        status="errored", terminal=terminal, withheld="errored", vote=None, text=None,
        reported_model="historical-script-version",
    )
    path = selected.with_name("unselected-retry--a1.json")
    reports._json(path, row)
    # A current invocation can explicitly lack this nonselected receipt. It
    # must not be treated as missing proof for the final selected in another run.
    invocation = {"grading_run_id": "unselected-retry", "not_run": []}
    if missing:
        invocation["execution_results"] = {}
    reports._json(run / "grading/runs/unselected-retry.json", invocation)
    return path


@pytest.mark.parametrize("pass_number", [1, 2])
@pytest.mark.parametrize("history", ["missing", "compact", "terminal", "terminal-missing"])
def test_scripted_retry_is_selected_before_receipt_replay(
    tmp_path, monkeypatch, pass_number, history,
):
    run = _build(tmp_path)
    selected = _script_vote(run, "haiku", pass_number=pass_number, refusal=False)
    if pass_number == 2:
        for judge in ("luna", "flash-lite"):
            _script_vote(run, judge, pass_number=2, refusal=False)
    _retry(run, selected, missing="missing" in history, terminal="terminal" in history)
    reports._score(run, tmp_path)
    key = ("python-docs--scripted-anthropic--r1", "SYN-EX-001")
    scored = _read(run / "scoring" / key[0] / f"{key[1]}.json")
    assert scored["score"] == 1.0
    if pass_number == 1:
        assert scored["grading"]["votes"]["haiku"]["attempts"] == 2
    seen = _capture_reads(monkeypatch)
    metadata = _RunMetadata(_load_run_bundle(run))
    assert metadata.grading == "scripted"
    assert metadata.grade_capture.reconciled
    assert metadata.summary["diagnostics"]["per_judge"]["haiku"]["records"] == 3
    assert "historical-script-version" in metadata.versions["haiku"]
    assert not metadata.retest_unknown
    assert metadata.grade_capture.graded_answers is None
    assert not metadata.retained
    assert seen and max(seen.values()) == 1
    for render in (reports._render, reports._readme):
        seen.clear()
        text = render(run)
        assert "demonstration" in text.lower()
        assert "fewer normalized tokens" not in text
        assert "hosted batch APIs" not in text
        if render is reports._render:
            assert "haiku: 3 records; withheld none" in text
            assert "historical-script-version" in text
        assert seen and max(seen.values()) == 1


@pytest.mark.parametrize("failure", ["entry", "file", "digest", "reply"])
def test_selected_scripted_receipt_failure_still_withholds_diagnostics(tmp_path, failure):
    run = _build(tmp_path)
    selected = _script_vote(run, "haiku", refusal=False)
    _retry(run, selected, missing=False, terminal=True)
    reports._score(run, tmp_path)
    row = _read(selected)
    invocation_path = run / "grading/runs" / f"{row['grading_run_id']}.json"
    invocation = _read(invocation_path)
    relative = next(iter(invocation["execution_results"]))
    receipt = run / "grading/executions" / row["grading_run_id"] / relative
    if failure == "entry":
        invocation["execution_results"] = {}
    elif failure == "file":
        receipt.unlink()
    elif failure == "digest":
        invocation["execution_results"][relative] = "0" * 64
    else:
        history = _read(receipt)
        history["result"]["raw"]["scripted_rule"]["refusal"] = True
        reports._json(receipt, history)
        invocation["execution_results"][relative] = hashlib.sha256(receipt.read_bytes()).hexdigest()
    reports._json(invocation_path, invocation)
    metadata = _RunMetadata(_load_run_bundle(run))
    assert not metadata.grade_capture.reconciled
    assert not metadata.summary["diagnostics"]
    assert metadata.grading != "live"
    assert not metadata.retained
    for render in (reports._render, reports._readme):
        text = render(run)
        assert "fewer normalized tokens" not in text
        assert "haiku: 3 records; withheld none" not in text


def test_eligible_scripted_reference_selects_before_receipt_replay(tmp_path, monkeypatch):
    run = reports._build(
        tmp_path, families_run=("scripted-openai",), questions=("SYN-EX-002",),
        suspect_answer=True,
    )
    reports._score(run, tmp_path)
    selected = next((run / "grading/votes/reference").glob("*/*/pass1/*/*.json"))
    _retry(run, selected)
    reports._score(run, tmp_path)
    key = ("python-docs--scripted-openai--r1", "SYN-EX-002")
    score = _read(run / "scoring" / key[0] / f"{key[1]}.json")
    assert score["grading"]["reference"]["attempts"] == 2
    seen = _capture_reads(monkeypatch)
    metadata = _RunMetadata(_load_run_bundle(run))
    assert metadata.grading == "scripted"
    assert metadata.grade_capture.reconciled
    assert metadata.grade_capture.answers[key].reference is not None
    assert metadata.summary["diagnostics"]["per_judge"]["haiku"]["records"] == 3
    assert seen and max(seen.values()) == 1
    for render in (reports._render, reports._readme):
        seen.clear()
        text = render(run)
        assert "demonstration" in text.lower()
        assert "fewer normalized tokens" not in text
        assert seen and max(seen.values()) == 1


@pytest.mark.parametrize("scope", ["coordinates", "count"])
def test_unselected_current_inventory_still_requires_its_vote_receipt(tmp_path, scope):
    run = _build(tmp_path)
    selected = _script_vote(run, "haiku", refusal=False)
    reports._score(run, tmp_path)
    retry = _retry(run, selected)
    invocation_path = run / "grading/runs/unselected-retry.json"
    invocation = _read(invocation_path)
    if scope == "coordinates":
        invocation.update(target="answers", composition=reports.COMPOSITION, **{"pass": 1})
    else:
        invocation["records_written"] = 0
    reports._json(invocation_path, invocation)
    before = (run / "grading/summary.json").read_bytes()
    with pytest.raises(BenchmarkValidationError, match="inventory"):
        score_run_dir(run, schema_path=reports.SCHEMA)
    assert (run / "grading/summary.json").read_bytes() == before
    assert retry.exists()
    metadata = _RunMetadata(_load_run_bundle(run))
    assert not metadata.grade_capture.reconciled
    assert metadata.grading_spend["graded_answers"] is None
    assert not metadata.retained


@pytest.mark.parametrize("terminal", [False, True])
def test_raw_historical_retry_keeps_ordinary_scorer_report_and_readme(recording, terminal):
    run = recording
    selected = next((run / "grading/votes/answers/haiku").glob("*/pass1/*/*.json"))
    _retry(run, selected, terminal=terminal)
    summary = score_run_dir(run, schema_path=reports.SCHEMA)
    row = _read(selected)
    # Inspect the actual selected item's score rather than relying on the cache's tier.
    item = _read(run / "grading/items/answers" / f"{row['item_id']}.json")["meta"]
    evidence = _read(run / "scoring" / item["competitor_id"] / f"{item['corpus_id']}.json")
    assert evidence["grading"]["votes"]["haiku"]["attempts"] == 2
    assert evidence["grading"]["votes"]["haiku"]["sha256"] == hashlib.sha256(
        selected.read_bytes()
    ).hexdigest()
    metadata = _RunMetadata(_load_run_bundle(run))
    assert metadata.grading == "live"
    assert metadata.grade_capture.reconciled
    assert metadata.grading_spend["graded_answers"] == summary["spend"]["graded_answers"] == 12
    assert "historical-script-version" in metadata.versions["haiku"]
    for render in (reports._render, reports._readme):
        text = render(run)
        assert "demonstration" not in text.lower()
        assert "fewer normalized tokens" not in text


def test_malformed_second_pass_is_not_reopened_for_versions(tmp_path, monkeypatch):
    run = _build(tmp_path)
    reports._score(run, tmp_path)
    path = _script_vote(run, "haiku", pass_number=2, refusal=False)
    path.write_bytes(b"{")
    seen = _capture_reads(monkeypatch)
    metadata = _RunMetadata(_load_run_bundle(run))
    assert metadata.grading == "scripted"
    assert metadata.grade_capture.reconciled
    assert metadata.retest_unknown
    assert seen[path] == 1
    assert max(seen.values()) == 1
    for render in (reports._render, reports._readme):
        seen.clear()
        text = render(run)
        assert "demonstration" in text.lower()
        assert "fewer normalized tokens" not in text
        assert seen[path] == 1
        assert max(seen.values()) == 1


def test_two_passes_cannot_reopen_the_same_mutable_receipt(tmp_path, monkeypatch):
    run = _build(tmp_path)
    selected = _script_vote(run, "haiku", refusal=False)
    reports._score(run, tmp_path)
    row = _read(selected)
    row["pass"] = 2
    second = selected.parents[2] / "pass2" / selected.parent.name / selected.name
    reports._json(second, row)
    # The same invocation cannot prove both passes. Read its receipt once and
    # reject pass 2 against that captured identity, even if the file can change.
    seen = _capture_reads(monkeypatch)
    metadata = _RunMetadata(_load_run_bundle(run))
    assert metadata.grading == "scripted"
    assert metadata.grade_capture.reconciled
    assert metadata.retest_unknown
    assert max(seen.values()) == 1, seen.most_common(2)
    for render in (reports._render, reports._readme):
        seen.clear()
        assert "demonstration" in render(run).lower()
        assert max(seen.values()) == 1, seen.most_common(2)
