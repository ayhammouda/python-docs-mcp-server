"""Optional calibration history cannot erase independently replayed gates."""

import copy
import json

from benchmarks.agentic.inputs import CapturedInput
from benchmarks.agentic_report import _RunMetadata
from benchmarks.grading.calibration import replay_g1
from benchmarks.grading.gates import G1Binding
from benchmarks.grading.publication import reconcile_gates
from benchmarks.grading.score import score_run_dir
from benchmarks.report import _load_run_bundle
from tests.benchmarks import test_agentic_report as reports
from tests.benchmarks import test_grading_score as runs
from tests.benchmarks.test_calibration_history import history as history
from tests.benchmarks.test_calibration_history import replay


def test_absent_g1_preserves_answer_gate_measurements(tmp_path):
    root = reports._live_like_run(tmp_path, full=True)
    scored = score_run_dir(root, schema_path=reports.SCHEMA)
    assert [scored["gates"][g]["passed"] for g in ("G1", "G2", "G3", "G4")] == [
        None, True, None, True,
    ]
    published = _RunMetadata(_load_run_bundle(root))
    assert [published.summary["gates"][g]["passed"] for g in ("G1", "G2", "G3", "G4")] == [
        None, True, None, True,
    ]
    for name in ("G2", "G4"):
        assert published.summary["gates"][name] == scored["gates"][name]
    assert published.grading_spend["graded_answers"] == 300
    text = reports._render(root)
    assert "G2 panel reliability: pass;" in text
    assert "G4 claim robustness: pass;" in text
    assert "fewer normalized tokens" not in reports._readme(root)


def test_missing_projection_recovers_captured_originals(tmp_path):
    root = reports._live_like_run(tmp_path, full=True)
    baseline = _RunMetadata(_load_run_bundle(root))
    (root / "grading/disclosure/g1-entries.json").unlink()
    published = _RunMetadata(_load_run_bundle(root))
    assert published.summary["gates"] == baseline.summary["gates"]
    assert published.tier == "Validated"
    assert published.grading_spend == baseline.grading_spend
    assert "fewer normalized tokens" in reports._readme(root)
    summary = copy.deepcopy(published.summary)
    # A claim-only failure cannot reset agreement/calibration. Reuse the capture.
    first = next(iter(published.tokens))
    original = published.tokens[first]
    published.tokens[first] = {"client_wrapped_tokens": "invalid"}
    gates = reconcile_gates(published)
    assert [gates[g].passed for g in ("G1", "G2", "G3", "G4")] == [True, True, True, None]
    published.tokens[first] = original
    published.summary = copy.deepcopy(summary)
    published.reference_sample = {}
    gates = reconcile_gates(published)
    assert [gates[g].passed for g in ("G1", "G2", "G3", "G4")] == [True, True, None, True]


def test_missing_tuning_request_preserves_primary_and_reference(history, tmp_path):
    held, entry = history
    # The source remains syntactically valid and fully scanned. Only one request
    # from a separate tuning run is unavailable; held-out proof is intact.
    files = entry["tuning"][0]["source"]["files"]
    name = next(n for n in files if n.startswith("items/tuning/"))
    del files[name]
    gate = replay(held, entry)
    assert gate.details["overall"]["n"] == 28
    assert gate.details["calibration_unknown"]["tuning"]
    assert gate.details["held_out_test_retest"]["haiku"]["items"] == 1
    inputs = held / "inputs"
    selected, reference = replay_g1(
        {"haiku+luna+flash-lite": entry},
        G1Binding(
            {j: entry["binding"]["composition"][j] for j in runs.JUDGES},
            entry["binding"]["inputs"],
        ),
        **{name: CapturedInput.from_bytes((inputs / file).read_bytes(), name, name)
           for name, file in (("panel", "panel.yml"), ("probes", "probes.yml"),
                              ("criteria", "grading-criteria.yml"))},
    )
    assert selected.passed is gate.passed
    assert selected.details["overall"] == gate.details["overall"]
    assert reference.passed is None  # This small control issued no reference run.
    path = tmp_path / "missing-tuning.json"
    runs._json(path, {"entries": {"haiku+luna+flash-lite": entry}})
    root = reports._build(
        tmp_path / "answering", families_run=("scripted-anthropic",), suspect_answer=False
    )
    scored = score_run_dir(root, schema_path=runs.SCHEMA, g1_paths=[path])
    assert scored["gates"]["G1"]["details"]["overall"]["n"] == 28
    text = reports._render(root)
    assert "Calibration tuning: unknown" in text
    assert "1/28 paired, 1 pass-2 records" in text


def test_missing_tuning_request_keeps_700_probe_and_350_reference_scores(
    tmp_path, full_calibration
):
    root, _held, entry, _ = full_calibration
    original = json.loads((root / "grading/summary.json").read_bytes())
    files = entry["tuning"][0]["source"]["files"]
    del files[next(n for n in files if n.startswith("items/tuning/"))]
    path = tmp_path / "missing-tuning-request.json"
    runs._json(path, {"entries": {"haiku+luna+flash": entry}})
    scored = score_run_dir(root, schema_path=runs.SCHEMA, g1_paths=[path])
    assert scored["gates"]["G1"]["passed"] is True
    assert scored["gates"]["G1"]["details"]["overall"] == {
        **original["gates"]["G1"]["details"]["overall"], "n": 700, "hits": 700,
    }
    assert scored["reference_check"] == original["reference_check"]
    assert scored["reference_check"]["details"]["overall"]["hits"] == 350
    assert scored["gates"]["G1"]["details"]["calibration_unknown"]["tuning"]
