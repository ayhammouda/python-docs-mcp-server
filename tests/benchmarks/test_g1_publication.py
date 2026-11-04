"""Publication must replay raw calibration, using only captured export inputs."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest
import yaml

from benchmarks.agentic.inputs import CapturedInput
from benchmarks.agentic_report import _GradeCapture
from benchmarks.grading.calibration import judge_settings, replay_g1
from benchmarks.grading.gates import G1Binding, g1, reference_probe_check
from benchmarks.grading.inputs import SNAPSHOT_FILES
from benchmarks.grading.probes import Probe, reference_subset
from benchmarks.grading.publication import reconcile_gates
from benchmarks.grading.score import score_run_dir
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks import test_grading_score as runs
from tests.benchmarks.test_grading_probes import index_db as index_db


def test_publication_replays_authentic_calibration_and_refuses_summary_only(
    tmp_path, index_db, monkeypatch
):
    root, _, entry, _ = runs._full_calibration(tmp_path, index_db, False)
    summary = json.loads((root / "grading/summary.json").read_bytes())
    # Focus reconciliation on G1. Other gates remain unevaluable in this small
    # answer run; no saved tier is used to manufacture a passing publication.
    summary["claims"] = {"C2": {}, "C3": {}}
    summary["claims_by_judge"] = {j: summary["claims"] for j in summary["efforts"]}
    summary["judge_sensitive"] = []
    frozen = {
        name: (root / "grading/inputs" / filename).read_bytes()
        for name, filename in SNAPSHOT_FILES.items()
    }
    run = SimpleNamespace(
        summary=summary,
        bundle=SimpleNamespace(run_dir=root),
        inputs=json.loads((root / "grading/inputs/inputs.json").read_bytes()),
        frozen=frozen,
        judges=yaml.safe_load(frozen["panel"])["judges"],
        grade_capture=_GradeCapture(),
        reference_sample={},
        transcripts={},
        scoring={},
        tokens={},
        plan={"cells": []},
        families={},
        seed="1234",
        offline_ok=lambda: None,
    )
    assert len(entry["scores"]["panel"]) == 700
    assert all(len(s) == 700 for s in entry["scores"]["judges"].values())
    assert reconcile_gates(run)["G1"].passed is True

    key = summary["composition"]
    disclosure = root / "grading/disclosure/g1-entries.json"

    def publish(value):
        runs._json(disclosure, {key: value})
        return reconcile_gates(run)["G1"].passed

    def refused(value):
        assert publish(value) is not True

    # Keep every bound hash, runtime pin, verdict, count and Wilson interval.
    # Coherent aggregate evidence alone cannot establish calibration.
    forged = copy.deepcopy(entry)
    del forged["scores"]
    refused(forged)

    for invalid in (None, True, [], {}, {"judges": {}}):
        forged = copy.deepcopy(entry)
        forged["scores"] = invalid
        refused(forged)
    for target in ("panel", *entry["scores"]["judges"]):
        for invalid in (None, True, [], {}, {"foreign": 1.0}):
            forged = copy.deepcopy(entry)
            dest = forged["scores"] if target == "panel" else forged["scores"]["judges"]
            dest[target] = invalid
            refused(forged)
        forged = copy.deepcopy(entry)
        dest = forged["scores"] if target == "panel" else forged["scores"]["judges"]
        dest[target].popitem()
        refused(forged)
        for invalid in (True, False, "1", float("nan"), float("inf"), -1, 2, 10**500):
            forged = copy.deepcopy(entry)
            dest = (
                forged["scores"]["panel"] if target == "panel"
                else forged["scores"]["judges"][target]
            )
            dest[next(iter(dest))] = invalid
            refused(forged)
    for name in (
        "overall", "by_type", "by_judge", "inj_credit", "confusion", "misses_by_generator"
    ):
        forged = copy.deepcopy(entry)
        del forged["gate"]["details"][name]
        refused(forged)
    for value in (0, True):
        forged = copy.deepcopy(entry)
        forged["gate"]["details"]["overall"]["accuracy"] = value
        refused(forged)
    forged = copy.deepcopy(entry)
    forged["gate"]["details"]["reason"] = "contradictory failure annotation"
    refused(forged)
    forged = copy.deepcopy(entry)
    forged["gate"]["details"]["composition"] = "contradictory+composition"
    refused(forged)
    forged = copy.deepcopy(entry)
    del forged["binding"]["judge_settings"]
    refused(forged)
    for judge in (*entry["scores"]["judges"], "reference"):
        forged = copy.deepcopy(entry)
        del forged["binding"]["judge_settings"][judge]
        refused(forged)
    for invalid in (True, [], {"passed": True, "details": []}):
        forged = copy.deepcopy(entry)
        forged["reference_check"] = invalid
        refused(forged)
    for invalid in (True, [], {}, {"foreign": 0.0}):
        forged = copy.deepcopy(entry)
        forged["reference_check"]["details"]["scores"] = invalid
        refused(forged)
    forged = copy.deepcopy(entry)
    scores = forged["reference_check"]["details"]["scores"]
    scores[next(iter(scores))] = True
    refused(forged)
    forged = copy.deepcopy(entry)
    forged["reference_check"]["details"]["overall"]["hits"] = 0
    refused(forged)
    forged = copy.deepcopy(entry)
    forged["reference_check"]["passed"] = False
    refused(forged)

    # Replay uses captured run bytes, even if the disk snapshot changes afterward.
    on_disk = root / "grading/inputs/probes.yml"
    original = on_disk.read_bytes()
    on_disk.write_bytes(b"not the captured probe file")
    assert publish(entry) is True
    on_disk.write_bytes(original)
    for name in ("probes", "criteria", "panel"):
        captured = run.frozen.pop(name)
        refused(entry)
        run.frozen[name] = captured
    changed = yaml.safe_load(run.frozen["probes"])
    changed["probes"][0]["expected_score"] = 0.5
    run.frozen["probes"] = yaml.safe_dump(changed).encode()
    refused(entry)
    run.frozen["probes"] = original

    # Neither the shared replay nor publication can consult a repository tuning
    # file, an archive, an absolute source path, or a newly read snapshot.
    runs._json(disclosure, {key: entry})
    with monkeypatch.context() as confined:
        def no_path_read(*args, **kwargs):
            pytest.fail("G1 replay attempted an unbounded input read")

        confined.setattr(CapturedInput, "read", no_path_read)
        assert reconcile_gates(run)["G1"].passed is True

    # Missing reference evidence affects G3, never a separately supported G1.
    binding = G1Binding(
        {j: entry["binding"]["composition"][j] for j in key.split("+")}, run.inputs
    )
    captured = {
        name: CapturedInput.from_bytes(run.frozen[name], name, name)
        for name in ("panel", "probes", "criteria")
    }
    no_reference = copy.deepcopy(entry)
    no_reference["reference_check"] = None
    del no_reference["binding"]["judge_settings"]["reference"]
    gate, check = replay_g1({key: no_reference}, binding, **captured)
    assert gate.passed is True and check.passed is None
    # The scorer must preserve null reference participation on re-export.
    path = tmp_path / "no-reference.json"
    runs._json(path, {"entries": {key: no_reference}})
    rescored = score_run_dir(root, schema_path=runs.SCHEMA, g1_paths=[path])
    assert rescored["gates"]["G1"]["passed"] is True
    assert json.loads(disclosure.read_bytes())[key]["reference_check"] is None
    gate, check = replay_g1(json.loads(disclosure.read_bytes()), binding, **captured)
    assert gate.passed is True and check.passed is None

    # Genuine passing arrays can contain unresolved scores within thresholds.
    probes = [Probe(**p) for p in captured["probes"].mapping["probes"] if p["set"] == "held_out"]
    imperfect = copy.deepcopy(entry)
    missing = next(p for p in probes if p.type == "KEY")
    imperfect["scores"]["panel"][missing.id] = None
    imperfect["scores"]["judges"]["haiku"][missing.id] = None
    actual = g1(probes, imperfect["scores"]["panel"], imperfect["scores"]["judges"])
    assert actual.passed is True
    imperfect["gate"] = {"passed": True, "details": actual.details}
    gate, check = replay_g1({key: imperfect}, binding, **captured)
    assert gate.passed is True and check.passed is True
    # Reference misses and unresolved scores retain their genuine verdicts.
    subset = reference_subset(probes, seed=captured["probes"].mapping["header"]["corpus_sha256"])
    for score, expected in ((None, None), (0.0, True)):
        imperfect = copy.deepcopy(entry)
        scores = imperfect["reference_check"]["details"]["scores"]
        scores[next(p.id for p in subset if p.type == "KEY")] = score
        actual = reference_probe_check(subset, scores)
        imperfect["reference_check"] = {"passed": actual.passed, "details": actual.details}
        gate, check = replay_g1({key: imperfect}, binding, **captured)
        assert gate.passed is True and check.passed is expected
    failing_reference = copy.deepcopy(entry)
    scores = failing_reference["reference_check"]["details"]["scores"]
    for p in [p for p in subset if p.type == "KEY"][:18]:
        scores[p.id] = 0.0
    actual = reference_probe_check(subset, scores)
    assert actual.passed is False
    failing_reference["reference_check"] = {"passed": False, "details": actual.details}
    gate, check = replay_g1({key: failing_reference}, binding, **captured)
    assert gate.passed is True and check.passed is False

    # The frozen participating reference's runtime settings are also mandatory.
    altered = yaml.safe_load(run.frozen["panel"])
    altered["judges"]["reference"]["max_output_tokens"] //= 2
    captured["panel"] = CapturedInput.from_bytes(yaml.safe_dump(altered).encode(), "panel", "panel")
    assert judge_settings(captured["panel"], list(binding.composition), reference="reference") != (
        entry["binding"]["judge_settings"]
    )
    with pytest.raises(BenchmarkValidationError, match="judge settings"):
        replay_g1({key: entry}, binding, **captured)
