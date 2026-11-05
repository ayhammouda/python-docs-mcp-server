"""Recorded comparisons survive as labelled history, never publication evidence."""

import json
import shutil

import pytest

from benchmarks.agentic_report import _RunMetadata
from benchmarks.grading.publication import reconcile_gates
from benchmarks.report import _load_run_bundle
from tests.benchmarks.accepted_report_fixture import rebuild_grading
from tests.benchmarks.test_agentic_report import _json, _live_like_run, _readme, _render, _score
from tests.benchmarks.test_agentic_report import current_offline_export as current_offline_export

MODEL = "scripted-anthropic"


@pytest.fixture(scope="module")
def diagnostic_template(tmp_path_factory):
    return _live_like_run(tmp_path_factory.mktemp("claim-diagnostics"))


@pytest.fixture
def diagnostic_run(tmp_path, diagnostic_template):
    run = shutil.copytree(diagnostic_template, tmp_path / "run")
    # A valid numeric projection contradicts the saved vote. This is historical
    # data to display, not independently replayed final-grade evidence.
    path = next((run / "scoring").glob("docs-fetch--scripted-anthropic*/*.json"))
    row = json.loads(path.read_bytes())
    row["score"] = 0.5
    _json(path, row)
    return run


def _c2_row(report):
    section = report.split("### C2:", 1)[1].split("### C3:", 1)[0]
    return next(row for row in section.splitlines() if row.startswith(f"| {MODEL} |"))


def test_recorded_interval_is_labelled_without_replacing_replayed_claims(diagnostic_run):
    run = diagnostic_run
    before = (run / "grading/summary.json").read_bytes()
    metadata = _RunMetadata(_load_run_bundle(run))
    assert metadata.grading == "unknown"
    assert metadata.summary["claims"]["C2"][MODEL]["interval"] is None
    row = _c2_row(_render(run))
    assert "0.500, 95% CI 0.500 to 0.500" in row
    assert r"recorded diagnostic \(unverified\)" in row
    assert "not evaluable" in row and "| pass |" not in row
    assert "grading gates do not support publication" in row
    assert "fewer normalized tokens" not in _readme(run)
    assert (run / "grading/summary.json").read_bytes() == before


@pytest.mark.parametrize("change", ["missing", "altered"])
def test_current_measurements_survive_missing_or_altered_cached_claims(
    tmp_path, diagnostic_template, change,
):
    run = shutil.copytree(diagnostic_template, tmp_path / "run")
    path = run / "grading/summary.json"
    summary = json.loads(path.read_bytes())
    if change == "missing":
        del summary["claims"]["C2"][MODEL]
    else:
        summary["claims"]["C2"][MODEL]["interval"] = {"point": 0.25, "low": 0.25, "high": 0.25}
    _json(path, summary)
    before = path.read_bytes()
    metadata = _RunMetadata(_load_run_bundle(run))
    assert metadata.grading == "live"
    assert metadata.summary["gates"]["G4"]["passed"] is None
    assert metadata.summary["claims"]["C2"][MODEL]["interval"]["point"] == 0.5
    row = _c2_row(_render(run))
    assert "0.500, 95% CI 0.500 to 0.500" in row and "0.250" not in row
    assert "replayed from captured grades" in row and "recorded diagnostic" not in row
    assert "grading gates do not support publication" in row
    assert "fewer normalized tokens" not in _readme(run)
    assert (run / "grading/summary.json").read_bytes() == before


def test_failed_reconciliation_cannot_reuse_previous_measurements(diagnostic_template):
    metadata = _RunMetadata(_load_run_bundle(diagnostic_template))
    assert metadata.replayed_claims["C2"][MODEL]["interval"]["point"] == 0.5
    del metadata.summary["composition"]
    gates = reconcile_gates(metadata)
    assert all(gate.passed is None for gate in gates.values())
    assert metadata.replayed_claims == {}


def test_unregistered_resamples_preserve_only_receipt_backed_history(diagnostic_run):
    run = diagnostic_run
    path = run / "grading/summary.json"
    summary = json.loads(path.read_bytes())
    summary["resamples"] = 2000
    _json(path, summary)
    metadata = _RunMetadata(_load_run_bundle(run))
    assert metadata.tier != "Validated" and metadata.replayed_claims == {}
    assert not metadata.summary["claims"]
    row = _c2_row(_render(run))
    assert "0.500, 95% CI" in row and "recorded diagnostic" in row
    assert "fewer normalized tokens" not in _readme(run)
    token = json.loads((run / "tokens" / f"docs-fetch--{MODEL}--r1/SYN-EX-002.json").read_bytes())
    (run / "grading/count-runs" / f"{token['count_run_id']}.json").unlink()
    assert "0.500" not in _c2_row(_render(run))


def test_replayed_unbounded_comparison_takes_precedence_over_saved_history(
    diagnostic_template, tmp_path
):
    run = shutil.copytree(diagnostic_template, tmp_path / "run")
    grading = run / "grading"
    stale = json.loads((grading / "summary.json").read_bytes())["claims"]["C2"][MODEL]
    for path in (grading / "votes").glob("*/*/*/pass1/python-docs*/*.json"):
        vote = json.loads(path.read_bytes())
        if vote["item_id"].endswith("SYN-EX-001"):
            continue
        vote["vote"]["contradicts_key"] = True
        vote["text"] = json.dumps(vote["vote"])
        _json(path, vote)
    rebuild_grading(run)
    _score(
        run, tmp_path, panel_path=grading / "inputs/panel.yml",
        composition=json.loads((grading / "runs/run-1.json").read_bytes())["composition"],
    )
    summary = json.loads((grading / "summary.json").read_bytes())
    fresh = summary["claims"]["C2"][MODEL]
    assert fresh["passed"] is False and fresh["interval"]["high"] == "inf"
    for saved in (fresh, stale):
        summary["claims"]["C2"][MODEL] = saved
        _json(grading / "summary.json", summary)
        row = _c2_row(_render(run))
        assert "to inf" in row and "replayed from captured grades" in row
        assert "recorded diagnostic" not in row


@pytest.mark.parametrize("point,passed", [(0.25, True), (2.0, False)])
def test_changed_history_remains_explicitly_unverified(diagnostic_run, point, passed):
    run = diagnostic_run
    path = run / "grading/summary.json"
    summary = json.loads(path.read_bytes())
    claim = summary["claims"]["C2"][MODEL]
    claim.update(passed=passed, interval={"point": point, "low": point, "high": point})
    _json(path, summary)
    row = _c2_row(_render(run))
    assert f"{point:.3f}, 95% CI {point:.3f} to {point:.3f}" in row
    assert "recorded diagnostic" in row
    assert "| pass |" not in row
    if not passed:
        assert "| fail |" in row  # recorded technical failure, no negative publication claim
    metadata = _RunMetadata(_load_run_bundle(run))
    assert metadata.summary["claims"]["C2"][MODEL]["interval"] is None
    assert metadata.summary["gates"]["G4"]["passed"] is None
    assert "fewer normalized tokens" not in _readme(run)


@pytest.mark.parametrize("offline", ["intact", "missing", "failed"])
def test_recorded_c3_still_requires_current_offline_evidence(current_offline_export, offline):
    run = current_offline_export
    path = next((run / "scoring").glob("python-docs*/*.json"))
    record = json.loads(path.read_bytes())
    record["score"] = 0.5
    _json(path, record)
    if offline == "missing":
        (run / "airgap/checks.json").unlink()
    elif offline == "failed":
        path = run / "egress/summary.json"
        egress = json.loads(path.read_bytes())
        egress["isolation_probe"] = "fail"
        _json(path, egress)
    section = _render(run).split("### C3:", 1)[1].split("### S1:", 1)[0]
    row = next(r for r in section.splitlines() if r.startswith("| gemma4-12b |"))
    if offline == "intact":
        assert "recorded diagnostic" in row and "1.000, 95% CI 1.000 to 1.000" in row
    else:
        assert "| n/a |" in row and "recorded diagnostic" not in row
    assert "| pass |" not in row
    assert "answers the corpus better" not in _readme(run)


@pytest.mark.parametrize("change", [
    "missing-claim", "wrong-model", "wrong-id", "boolean-verdict", "boolean-point",
    "nan", "infinity", "inverted", "inconsistent-verdict", "wrong-plan", "missing-count",
    "boolean-count", "negative-count", "boolean-score", "invalid-score", "missing-score",
])
def test_invalid_history_cannot_supply_a_recorded_interval(diagnostic_run, change):
    run = diagnostic_run
    path = run / "grading/summary.json"
    summary = json.loads(path.read_bytes())
    claim = summary["claims"]["C2"][MODEL]
    if change == "missing-claim":
        del summary["claims"]["C2"][MODEL]
    elif change == "wrong-model":
        claim["model"] = "unrelated"
    elif change == "wrong-id":
        claim["id"] = "C3"
    elif change == "boolean-verdict":
        claim["passed"] = 1
    elif change in {"boolean-point", "nan", "infinity"}:
        claim["interval"]["point"] = {
            "boolean-point": True, "nan": float("nan"), "infinity": float("inf"),
        }[change]
    elif change == "inverted":
        claim["interval"].update(low=0.8, high=0.2)
    elif change == "inconsistent-verdict":
        claim["passed"] = False
    _json(path, summary)
    if change == "wrong-plan":
        path = run / "planned-cells.json"
        plan = json.loads(path.read_bytes())
        row = next(r for r in plan["cells"] if r["model_family_id"] == MODEL)
        row["cell"] = "wrong-coordinate"
        _json(path, plan)
    if change.endswith("count"):
        path = next((run / "tokens").glob("python-docs--scripted-anthropic*/*.json"))
        if change == "missing-count":
            path.unlink()
        else:
            row = json.loads(path.read_bytes())
            row["client_wrapped_tokens"] = True if change == "boolean-count" else -1
            _json(path, row)
    if change.endswith("score"):
        path = next((run / "scoring").glob("python-docs--scripted-anthropic*/*.json"))
        row = json.loads(path.read_bytes())
        row["score"] = {"boolean-score": True, "invalid-score": 0.25, "missing-score": None}[change]
        _json(path, row)
    row = _c2_row(_render(run))
    assert "recorded diagnostic" not in row and "0.500" not in row
    assert "fewer normalized tokens" not in _readme(run)


def test_missing_hosted_count_receipt_withholds_a_recorded_diagnostic(diagnostic_run):
    run = diagnostic_run
    assert "0.500, 95% CI" in _c2_row(_render(run))
    path = run / "tokens" / f"docs-fetch--{MODEL}--r1" / "SYN-EX-002.json"
    token = json.loads(path.read_bytes())
    receipt = run / "grading/count-runs" / f"{token['count_run_id']}.json"
    receipt.unlink()

    metadata = _RunMetadata(_load_run_bundle(run))
    assert metadata.grading == "unknown"
    assert metadata.summary["claims"]["C2"][MODEL]["passed"] is None
    row = _c2_row(_render(run))
    assert "0.500" not in row and "not evaluable" in row
    assert "fewer normalized tokens" not in _readme(run)
