"""Known-answer G1 evidence, with recording fixtures and no provider calls."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from benchmarks.grading.gates import g1
from benchmarks.grading.probes import load_probe_file
from benchmarks.grading.score import score_run_dir, write_g1
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks import test_grading_score as runs
from tests.benchmarks.test_grading_probes import index_db as index_db


def _files(root: Path) -> dict[Path, bytes]:
    return {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_full_g1_replay_requires_scores_and_consistent_aggregates(tmp_path, index_db):
    run, held, entry, criteria = runs._full_calibration(tmp_path, index_db, False)
    key = "haiku+luna+flash"
    path = tmp_path / "replay.json"

    def replay(value):
        runs._json(path, {"entries": {key: value}})
        return score_run_dir(run, schema_path=runs.SCHEMA, g1_paths=[path])

    def refused(value):
        before = _files(run)
        with pytest.raises(BenchmarkValidationError):
            replay(value)
        assert _files(run) == before

    # This is the independent reviewer's exact zero-hit/no-judge reproduction.
    forged = copy.deepcopy(entry)
    details = forged["gate"]["details"]
    details["overall"].update(hits=0, accuracy=0)
    details["by_judge"] = {}
    for row in details["by_type"].values():
        row.update(hits=0, accuracy=0)
    refused(forged)

    # Export the actual pass-1 scores: 700 panel scores and every registered judge.
    scores = entry["scores"]
    assert len(scores["panel"]) == 700
    assert set(scores["judges"]) == {"haiku", "luna", "flash"}
    assert all(len(s) == 700 for s in scores["judges"].values())

    for field in ("overall", "by_type", "by_judge", "inj_credit"):
        forged = copy.deepcopy(entry)
        del forged["gate"]["details"][field]
        refused(forged)
    for target in ("panel", "haiku"):
        retained = scores["panel"]
        sparse = dict(retained)
        del sparse[next(k for k in retained if k.startswith("INJ--"))]
        for invalid in ({}, {"foreign": 1.0}, [], sparse):
            forged = copy.deepcopy(entry)
            dest = forged["scores"] if target == "panel" else forged["scores"]["judges"]
            dest[target] = invalid
            refused(forged)
        for invalid_score in (True, "1.0", float("nan"), float("inf"), -1, 2):
            forged = copy.deepcopy(entry)
            dest = (
                forged["scores"]["panel"]
                if target == "panel"
                else forged["scores"]["judges"][target]
            )
            dest[next(iter(dest))] = invalid_score
            refused(forged)
    forged = copy.deepcopy(entry)
    del forged["scores"]["judges"]["luna"]
    refused(forged)
    forged = copy.deepcopy(entry)
    forged["reference_check"]["details"]["scores"] = [0]
    refused(forged)
    forged = copy.deepcopy(entry)
    forged["reference_check"]["details"]["overall"]["hits"] = 0
    refused(forged)

    suite = load_probe_file(
        held / "handoff/probes.yml", criteria_path=criteria, tuning_path=runs.TUNING_PATH
    )
    probes = [p for p in suite.probes if p.set == "held_out"]
    # Independent literal expectations: six KEY misses violate per-type (44/50),
    # 71 judge misses violate 90% (629/700), and one INJ credit always fails.
    for case in ("type", "judge", "panel-inj", "judge-inj", "all-miss"):
        failing = copy.deepcopy(entry)
        scored = failing["scores"]
        if case == "type":
            for p in [p for p in probes if p.type == "KEY"][:6]:
                scored["panel"][p.id] = 0.0
        elif case == "judge":
            for p in probes[:71]:
                scored["judges"]["haiku"][p.id] = None
        elif case == "all-miss":
            scored["panel"] = dict.fromkeys(scored["panel"])
        else:
            inj = next(p.id for p in probes if p.type == "INJ")
            dest = scored["panel"] if case == "panel-inj" else scored["judges"]["haiku"]
            dest[inj] = 0.5
        refused(failing)  # Saved PASS cannot override the recorded scores.
        result = g1(
            probes,
            scored["panel"],
            scored["judges"],
            dropped=[d for d in suite.dropped if d["set"] == "held_out"],
        )
        assert result.passed is False
        failing["gate"] = {"passed": False, "details": result.details}
        assert replay(failing)["gates"]["G1"]["passed"] is False

    # Incomplete older exports stay readable, but cannot establish a passing gate.
    legacy = copy.deepcopy(entry)
    del legacy["scores"]
    summary = replay(legacy)
    assert summary["gates"]["G1"]["passed"] is None
    assert "evidence" in summary["gates"]["G1"]["details"]["reason"]
    assert summary["reference_check"]["passed"] is True
    unpinned = copy.deepcopy(entry)
    del unpinned["binding"]["judge_settings"]
    summary = replay(unpinned)
    assert summary["gates"]["G1"]["passed"] is None
    assert summary["reference_check"]["passed"] is None
    assert replay(entry)["gates"]["G1"]["passed"] is True

    # The imported composition is insufficient to bind real runtime controls.
    panel_path = run / "grading/inputs/panel.yml"
    manifest_path = panel_path.parent / "inputs.json"
    original_panel, original_manifest = panel_path.read_bytes(), manifest_path.read_bytes()
    for control in ("max_output_tokens", "max_input_tokens", "framing_allowance_tokens"):
        panel = yaml.safe_load(original_panel)
        panel["judges"]["haiku"][control] //= 2
        panel_path.write_text(yaml.safe_dump(panel))
        manifest = json.loads(original_manifest)
        manifest["panel"] = hashlib.sha256(panel_path.read_bytes()).hexdigest()
        runs._json(manifest_path, manifest)
        refused(entry)
        unverified = replay(unpinned)
        assert unverified["gates"]["G1"]["passed"] is None
        assert unverified["reference_check"]["passed"] is None
    panel_path.write_bytes(original_panel)
    manifest_path.write_bytes(original_manifest)

    # Real workspace pass 2 cannot replace missing pass 1 in the handoff evidence.
    missing = next(p for p in probes if p.type == "KEY")
    runs._vote(held, "haiku", missing.id, runs._vote_text(), target="probes", pass_number=2)
    for p in (held / "votes/probes/haiku/low/pass1" / missing.id).glob("*.json"):
        p.unlink()
    exported = json.loads(write_g1(held).read_bytes())["entries"][key]
    assert exported["scores"]["judges"]["haiku"][missing.id] is None
    assert exported["gate"]["details"]["held_out_test_retest"]["haiku"]["items"] == 0
    assert replay(exported)["gates"]["G1"]["passed"] == exported["gate"]["passed"]
    # A judge with no pass-1 votes is still counted, even if the other two agree.
    import shutil

    shutil.rmtree(held / "votes/probes/haiku/low/pass1")
    exported = json.loads(write_g1(held).read_bytes())["entries"][key]
    assert exported["gate"]["passed"] is False
    assert exported["gate"]["details"]["by_judge"]["haiku"]["hits"] == 0
    assert exported["gate"]["details"]["by_judge"]["haiku"]["n"] == 700
    assert replay(exported)["gates"]["G1"]["passed"] is False


@pytest.mark.parametrize("invalid", ["list", "overflow"])
def test_malformed_reference_scores_exit_2_before_writes(tmp_path, invalid):
    from benchmarks.__main__ import main

    run = runs._build_run(tmp_path)
    path = runs._g1_file(run, tmp_path / "g1.json")
    data = json.loads(path.read_bytes())
    check = data["entries"]["haiku+luna+flash-lite"]["reference_check"]
    if invalid == "list":
        check["passed"] = True
        check["details"]["scores"] = [0]
    else:
        scores = check["details"]["scores"]
        scores[next(iter(scores))] = 10**500
    runs._json(path, data)
    before = _files(run)
    with pytest.raises(SystemExit) as refused:
        main(["agentic-score", "--run-dir", str(run), "--g1", str(path)])
    assert refused.value.code == 2
    assert _files(run) == before


@pytest.mark.parametrize(
    "control,value", [("max_output_tokens", 8192), ("max_input_tokens", 100000)]
)
def test_legacy_g1_without_runtime_pins_cannot_pass(tmp_path, control, value):
    run = runs._build_run(tmp_path)
    path = runs._g1_file(run, tmp_path / "legacy.json")
    data = json.loads(path.read_bytes())
    entry = data["entries"]["haiku+luna+flash-lite"]
    del entry["binding"]["judge_settings"], entry["scores"]
    entry["gate"]["passed"] = True
    runs._json(path, data)
    panel_path = run / "grading/inputs/panel.yml"
    panel = yaml.safe_load(panel_path.read_bytes())
    panel["judges"]["haiku"][control] = value
    panel_path.write_text(yaml.safe_dump(panel))
    manifest_path = panel_path.parent / "inputs.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["panel"] = hashlib.sha256(panel_path.read_bytes()).hexdigest()
    runs._json(manifest_path, manifest)
    result = score_run_dir(run, schema_path=runs.SCHEMA, g1_paths=[path])
    assert result["gates"]["G1"]["passed"] is None
    assert result["reference_check"]["passed"] is None


def test_g1_archive_mismatch_is_refused_before_any_derived_write(tmp_path):
    run = runs._build_run(tmp_path)
    path = runs._g1_file(run, tmp_path / "g1.json")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    archive = run / "grading/disclosure/g1" / f"{digest}.json"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"different benign evidence")
    before = _files(run)
    with pytest.raises(BenchmarkValidationError, match="archived G1"):
        score_run_dir(run, schema_path=runs.SCHEMA, g1_paths=[path])
    assert _files(run) == before
