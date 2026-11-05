"""Shared evidence stays authentic, portable and private to each consumer."""

import json
import shutil

from benchmarks.__main__ import main
from benchmarks.runner import _environment_metadata
from tests.benchmarks import test_agentic_report as reports


def test_calibration_copy_is_independent_and_replays_normally(
    full_calibration, _calibration_origin
):
    run, held, entry, criteria = full_calibration
    root = run.parent
    originals = {
        path.relative_to(_calibration_origin): path
        for path in _calibration_origin.rglob("*") if path.is_file()
    }
    copies = {path.relative_to(root): path for path in root.rglob("*") if path.is_file()}
    assert originals.keys() == copies.keys()
    for name, original in originals.items():
        assert copies[name].read_bytes() == original.read_bytes()
        assert not copies[name].samefile(original)
    assert criteria == root / "criteria.yml"

    # The default export selects the registered Flash fallback, with full
    # held-out and reference cohorts. Ordinary CLI replay preserves every
    # measurement; only location annotations are allowed to change.
    summary_path = run / "grading/summary.json"
    before = json.loads(summary_path.read_bytes())
    assert before["composition"] == "haiku+luna+flash"
    assert main(["agentic-score", "--run-dir", str(run), "--g1", str(held / "g1.json")]) == 0
    after = json.loads(summary_path.read_bytes())
    assert after["run_dir"] == str(run)
    for name in before.keys() - {"run_dir", "disclosure"}:
        assert after[name] == before[name], name
    for gate, expected in ((after["gates"]["G1"], 700), (after["reference_check"], 350)):
        assert gate["passed"] is True
        assert gate["details"]["overall"]["n"] == expected
        assert gate["details"]["overall"]["hits"] == expected
    # This grading fixture has no answering summary. Derive its report envelope
    # from its own six completed trajectories without declaring hosted execution.
    plan = json.loads((run / "planned-cells.json").read_bytes())["cells"]
    answers = [json.loads(path.read_bytes()) for path in (run / "transcripts").glob("*/*.json")]
    assert {(r["competitor_id"], r["corpus_id"]) for r in answers} == {
        (r["cell"], r["corpus_id"]) for r in plan
    }
    assert len(plan) == len(answers) == 6 and all(r["status"] == "succeeded" for r in answers)
    env = _environment_metadata(run_id="synthetic-calibration", dry_run=False)
    reports._json(run / "environment.json", {
        **env, **json.loads((run / "environment.json").read_bytes()),
    })
    reports._json(run / "run-summary.json", {
        "run_id": env["run_id"], "repo_commit": env["repo_commit"], "artifact_root": str(run),
        "mode": "agentic", "models": sorted({r["model_family_id"] for r in plan}),
        "competitors": sorted({r["cell"] for r in plan}), "corpus_ids": sorted({
            r["corpus_id"] for r in plan
        }),
        "planned_cells": len(plan), "scored_cells": len(answers), "succeeded_cells": len(answers),
        "failed_cells": 0,
    })
    shutil.copyfile(reports.REPO_ROOT / "docs/benchmarks/agentic-manifest.yml",
                    run / "snapshots/competitor-manifest.yml")
    assert "G1 for this run's composition: pass." in reports._render(run)
    assert "fewer normalized tokens" not in reports._readme(run)

    # Writes, removals and nested dictionary mutations cannot reach the origin
    # that supplies the next test, or alter its producer's stored G1 bytes.
    source_g1 = _calibration_origin / "held/g1.json"
    body = source_g1.read_bytes()
    entry["tuning"][0]["judges"]["haiku"]["chosen"] = "consumer-only"
    entry["source"]["files"].clear()
    (held / "g1.json").write_text("consumer-only")
    criteria.unlink()
    assert source_g1.read_bytes() == body
    assert (_calibration_origin / "criteria.yml").is_file()
    (unchanged,) = json.loads(body)["entries"].values()
    assert unchanged["tuning"][0]["judges"]["haiku"]["chosen"] == "low"
    assert unchanged["source"]["files"]
