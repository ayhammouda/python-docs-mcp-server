"""G1 evidence reaches grades and disclosure only through the current score invocation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from benchmarks.__main__ import main
from tests.benchmarks import test_grading_score as runs

PRIMARY = "haiku+luna+flash-lite"
FALLBACK = "haiku+luna+flash"


@pytest.fixture
def run(tmp_path: Path) -> Path:
    return runs._build_run(tmp_path, families={runs.FAMILY: {"python-docs": runs.QUESTIONS[:1]}})


def _score(run: Path, *paths: Path) -> int:
    return main(
        [
            "agentic-score",
            "--run-dir",
            str(run),
            "--criteria",
            str(runs.CRITERIA),
            *(arg for path in paths for arg in ("--g1", str(path))),
        ]
    )


def _files(run: Path) -> dict[Path, bytes]:
    return {p.relative_to(run): p.read_bytes() for p in run.rglob("*") if p.is_file()}


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("conflict", ["verdict", "provenance"])
def test_conflicting_entries_refused_before_any_write(
    run: Path, tmp_path: Path, reverse: bool, conflict: str, capsys: pytest.CaptureFixture[str]
) -> None:
    first = runs._g1_file(run, tmp_path / "first.json")
    second = runs._g1_file(run, tmp_path / "second.json", passed=conflict != "verdict")
    if conflict == "provenance":
        data = json.loads(second.read_bytes())
        data["entries"][PRIMARY]["binding"]["inputs"]["probes"] = "0" * 64
        runs._json(second, data)
    before = _files(run)
    with pytest.raises(SystemExit) as refused:
        _score(run, *((second, first) if reverse else (first, second)))
    assert refused.value.code == 2
    assert PRIMARY in capsys.readouterr().err
    assert _files(run) == before


@pytest.mark.parametrize("invalid", ["extra judge", "missing digest"])
def test_inexact_evidence_is_refused(run: Path, tmp_path: Path, invalid: str) -> None:
    path = runs._g1_file(run, tmp_path / "g1.json")
    data = json.loads(path.read_bytes())
    binding = data["entries"][PRIMARY]["binding"]
    if invalid == "extra judge":
        binding["composition"]["reference"] = {"model_id": "another", "effort": "high"}
    else:
        del binding["inputs"]["probes"]
    runs._json(path, data)
    before = _files(run)
    with pytest.raises(SystemExit) as refused:
        _score(run, path)
    assert refused.value.code == 2
    assert _files(run) == before


@pytest.mark.parametrize("entry_is_pinned", [False, True])
def test_unpinned_legacy_probes_cannot_validate_either_gate(
    run: Path, tmp_path: Path, entry_is_pinned: bool
) -> None:
    manifest = run / "grading/inputs/inputs.json"
    path = runs._g1_file(run, tmp_path / "legacy.json")
    inputs = json.loads(manifest.read_bytes())
    del inputs["probes"]
    runs._json(manifest, inputs)
    (manifest.parent / "probes.yml").unlink()
    if not entry_is_pinned:
        runs._g1_file(run, path)
    before = _files(run)
    with pytest.raises(SystemExit) as refused:
        _score(run, path)
    assert refused.value.code == 2
    assert _files(run) == before
    assert _score(run) == 0
    summary = json.loads((run / "grading/summary.json").read_bytes())
    assert summary["gates"]["G1"]["passed"] is None
    assert summary["reference_check"]["passed"] is None
    assert not (run / "grading/disclosure/g1-entries.json").exists()


def test_rescore_publishes_only_current_g1_evidence(run: Path, tmp_path: Path) -> None:
    grading = run / "grading"
    disclosure = grading / "disclosure"
    primary = runs._g1_file(run, tmp_path / "g1.json")
    fallback = tmp_path / "fallback.json"
    entry = json.loads(primary.read_bytes())["entries"][PRIMARY]
    del entry["binding"]["composition"]["flash-lite"]
    entry["binding"]["composition"]["flash"] = {
        "model_id": runs.PANEL.judges["flash"].model_id,
        "effort": "low",
    }
    entry["gate"]["passed"] = False
    runs._json(fallback, {"entries": {FALLBACK: entry}})
    assert _score(run, fallback, primary) == 0
    summary = json.loads((grading / "summary.json").read_bytes())
    assert summary["gates"]["G1"]["passed"] is True
    assert summary["reference_check"]["passed"] is True
    entries_file = disclosure / "g1-entries.json"
    assert set(json.loads(entries_file.read_bytes())) == {PRIMARY, FALLBACK}
    copies = [grading / copy["path"] for copy in summary["disclosure"]["g1_files"]]
    assert [p.read_bytes() for p in copies] == [fallback.read_bytes(), primary.read_bytes()]
    assert (disclosure / "tuning.json").is_file()

    primary.unlink()
    before = _files(run)
    with pytest.raises(SystemExit) as refused:
        _score(run, fallback, primary)
    assert refused.value.code == 2
    assert _files(run) == before

    assert _score(run, fallback) == 0
    summary = json.loads((grading / "summary.json").read_bytes())
    assert summary["gates"]["G1"]["passed"] is None
    assert summary["reference_check"]["passed"] is None
    assert set(json.loads(entries_file.read_bytes())) == {FALLBACK}
    assert set((disclosure / "g1").glob("*.json")) == set(copies)
    assert not (disclosure / "tuning.json").exists()

    assert _score(run) == 0
    summary = json.loads((grading / "summary.json").read_bytes())
    assert summary["gates"]["G1"]["passed"] is None and summary["disclosure"]["g1_files"] == []
    assert not entries_file.exists() or json.loads(entries_file.read_bytes()) == {}
    assert set((disclosure / "g1").glob("*.json")) == set(copies)


def test_fallback_uses_recorded_efforts_and_its_own_reference_check(
    run: Path, tmp_path: Path
) -> None:
    # The fallback effort is frozen before held-out grading.
    frozen = run / "grading/inputs"
    panel_path = frozen / "panel.yml"
    panel = yaml.safe_load(panel_path.read_bytes())
    panel["judges"]["flash"]["effort"] = "medium"
    panel_path.write_text(yaml.safe_dump(panel), encoding="utf-8")
    manifest = json.loads((frozen / "inputs.json").read_bytes())
    manifest["panel"] = hashlib.sha256(panel_path.read_bytes()).hexdigest()
    runs._json(frozen / "inputs.json", manifest)
    primary = runs._g1_file(run, tmp_path / "primary.json")
    entry = json.loads(primary.read_bytes())["entries"][PRIMARY]
    composition = entry["binding"]["composition"]
    del composition["flash-lite"]
    composition["flash"] = {"model_id": runs.PANEL.judges["flash"].model_id, "effort": "medium"}
    entry["gate"]["passed"] = False
    fallback = tmp_path / "fallback.json"
    runs._json(fallback, {"entries": {FALLBACK: entry}})

    grading = run / "grading"
    invocation = grading / "runs/run-1.json"
    # JSON object order is immaterial. The source of the bound effort is answers pass 1.
    data = json.loads(invocation.read_bytes())
    runs._json(invocation, {**data, "composition": dict(sorted(composition.items()))})
    item = next((grading / "items/answers").glob("*.json"))
    runs._vote(grading, "flash", item.stem, runs._vote_text(), effort="medium")
    assert _score(run, primary, fallback) == 0
    summary = json.loads((grading / "summary.json").read_bytes())
    assert summary["composition"] == FALLBACK
    assert summary["efforts"]["flash"] == "medium" and summary["pending_cells"] == []
    assert summary["gates"]["G1"]["passed"] is False
    assert summary["reference_check"]["passed"] is True


def test_unregistered_pass1_composition_cannot_register_itself(run: Path, tmp_path: Path) -> None:
    path = runs._g1_file(run, tmp_path / "g1.json")
    entry = json.loads(path.read_bytes())["entries"][PRIMARY]
    composition = entry["binding"]["composition"]
    del composition["luna"]
    runs._json(path, {"entries": {"haiku+flash-lite": entry}})
    invocation = run / "grading/runs/run-1.json"
    runs._json(invocation, {**json.loads(invocation.read_bytes()), "composition": composition})
    before = _files(run)
    with pytest.raises(SystemExit) as refused:
        _score(run, path)
    assert refused.value.code == 2
    assert _files(run) == before


def test_duplicate_json_member_cannot_hide_a_second_verdict(run: Path, tmp_path: Path) -> None:
    path = runs._g1_file(run, tmp_path / "g1.json")
    path.write_bytes(
        path.read_bytes().replace(b'"passed": true', b'"passed": false, "passed": true', 1)
    )
    before = _files(run)
    with pytest.raises(SystemExit) as refused:
        _score(run, path)
    assert refused.value.code == 2
    assert _files(run) == before
