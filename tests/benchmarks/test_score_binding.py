"""Replay the public scoring command against portable, synthetic grading bundles."""

import json
from pathlib import Path

import pytest

from benchmarks.__main__ import main
from tests.benchmarks import test_grading_score as runs

# Reuse the scripted voting bundle at the same command boundary as the scoring tests.
scripted_graded_run = runs.scripted_graded_run


def _files(run: Path) -> dict[Path, bytes]:
    return {path.relative_to(run): path.read_bytes() for path in run.rglob("*") if path.is_file()}


def _score(run: Path, g1: Path, *args: str) -> int:
    return main(["agentic-score", "--run-dir", str(run), "--g1", str(g1), *args])


def _change_weight(path: Path) -> None:
    original = path.read_bytes()
    anchor = b"States the fixture source that SYN-EX-002 cites.\n      weight: nuance"
    assert original.count(anchor) == 1
    path.write_bytes(original.replace(anchor, anchor.replace(b"nuance", b"core")))


def test_cli_replays_the_frozen_bundle(scripted_graded_run: Path, tmp_path: Path) -> None:
    run = scripted_graded_run
    g1 = runs._g1_file(run, tmp_path / "g1.json")
    assert _score(run, g1) == 0
    split = runs._record(run, "python-docs", "SYN-EX-002")
    summary = json.loads((run / "grading/summary.json").read_bytes())
    assert split["score"] == 0.5
    assert summary["claims"]["C2"][runs.FAMILY]["interval"]["point"] == pytest.approx(2 / 3)
    assert summary["gates"]["G1"]["passed"] is None
    assert summary["gates"]["G1"]["details"]["reason"]
    assert summary["gates"]["G1"]["details"]["overall"]["accuracy"] == 0.97
    assert summary["resamples"] == 10_000


@pytest.mark.parametrize("source", ["override", "snapshot", "same-path"])
def test_cli_rejects_a_single_changed_weight_before_writing(
    scripted_graded_run: Path, tmp_path: Path, source: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run = scripted_graded_run
    frozen = run / "grading/inputs/grading-criteria.yml"
    g1 = runs._g1_file(run, tmp_path / "g1.json")
    criteria = tmp_path / "original.yml"
    criteria.write_bytes(frozen.read_bytes())
    if source == "same-path":
        criteria = frozen
    args = ["--criteria", str(criteria), "--panel", str(runs.PANEL_PATH)]
    assert _score(run, g1, *args) == 0
    assert runs._record(run, "python-docs", "SYN-EX-002")["score"] == 0.5
    _change_weight(criteria if source == "override" else frozen)
    before = _files(run)
    with pytest.raises(SystemExit) as error:
        _score(run, g1, *args)
    assert error.value.code == 2 and "criteria" in capsys.readouterr().err
    assert _files(run) == before


@pytest.mark.parametrize("changed", ["panel", "mapping_table", "g1", "model_id", "effort"])
def test_cli_rejects_mismatched_scoring_evidence_without_partial_writes(
    scripted_graded_run: Path, tmp_path: Path, changed: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run = scripted_graded_run
    g1 = runs._g1_file(run, tmp_path / "g1.json")
    if changed in {"model_id", "effort"}:
        path = run / "grading/runs/run-1.json"
        record = json.loads(path.read_bytes())
        record["composition"]["haiku"][changed] = "unexpected"
        runs._json(path, record)
        # Even a matching G1 cannot attest a judge setting outside the frozen panel.
        evidence = json.loads(g1.read_bytes())
        entry = next(iter(evidence["entries"].values()))
        entry["binding"]["composition"] = record["composition"]
        runs._json(g1, evidence)
    elif changed == "g1":
        evidence = json.loads(g1.read_bytes())
        entry = next(iter(evidence["entries"].values()))
        entry["binding"]["inputs"]["criteria"] = "0" * 64
        runs._json(g1, evidence)
    else:
        name = "mapping.txt" if changed == "mapping_table" else "panel.yml"
        path = run / "grading/inputs" / name
        path.write_bytes(path.read_bytes() + b"\n")
    before = _files(run)
    with pytest.raises(SystemExit) as error:
        _score(run, g1, "--criteria", str(runs.CRITERIA), "--panel", str(runs.PANEL_PATH))
    assert error.value.code == 2
    assert ("criteria" if changed == "g1" else changed) in capsys.readouterr().err
    assert _files(run) == before


def test_cli_cannot_publish_a_one_draw_interval(scripted_graded_run: Path, tmp_path: Path) -> None:
    run = scripted_graded_run
    g1 = runs._g1_file(run, tmp_path / "g1.json")
    before = _files(run)
    with pytest.raises(SystemExit) as error:
        _score(
            run,
            g1,
            "--criteria",
            str(runs.CRITERIA),
            "--panel",
            str(runs.PANEL_PATH),
            "--resamples",
            "1",
        )
    assert error.value.code == 2
    assert _files(run) == before


def test_cli_rejects_conflicting_g1_files_and_clears_previous_selection(
    scripted_graded_run: Path, tmp_path: Path
) -> None:
    run = scripted_graded_run
    passed = runs._g1_file(run, tmp_path / "pass.json")
    failed = runs._g1_file(run, tmp_path / "fail.json", passed=False)
    args = ["--criteria", str(runs.CRITERIA), "--panel", str(runs.PANEL_PATH)]
    assert _score(run, passed, *args) == 0
    before = _files(run)
    for first, last in ((passed, failed), (failed, passed)):
        with pytest.raises(SystemExit) as error:
            _score(run, first, "--g1", str(last), *args)
        assert error.value.code == 2
        assert _files(run) == before
    assert main(["agentic-score", "--run-dir", str(run), *args]) == 0
    selected = run / "grading/disclosure"
    assert not (selected / "g1-entries.json").exists()
    assert not (selected / "tuning.json").exists()
    assert list((selected / "g1").glob("*.json"))


@pytest.mark.parametrize("name", ["grading-criteria.yml", "panel.yml"])
def test_cli_uses_the_verified_bytes_even_if_the_path_is_replaced(
    scripted_graded_run: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    run = scripted_graded_run
    g1 = runs._g1_file(run, tmp_path / "g1.json")
    frozen = run / "grading/inputs" / name
    replacement = tmp_path / name
    replacement.write_bytes(b"{}")
    original_open = Path.open

    def replace_after_open(path: Path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        if path == frozen and replacement.exists():
            replacement.replace(path)
        return handle

    monkeypatch.setattr(Path, "open", replace_after_open)
    assert _score(run, g1, "--criteria", str(runs.CRITERIA), "--panel", str(runs.PANEL_PATH)) == 0
    assert not replacement.exists()
    assert runs._record(run, "python-docs", "SYN-EX-002")["score"] == 0.5


@pytest.mark.parametrize("field", ["model_id", "effort"])
def test_cli_binds_chosen_vote_to_its_frozen_judge(
    scripted_graded_run: Path, tmp_path: Path, field: str
) -> None:
    run = scripted_graded_run
    g1 = runs._g1_file(run, tmp_path / "g1.json")
    path = next((run / "grading/votes/answers/haiku").rglob("*.json"))
    vote = json.loads(path.read_bytes())
    vote[field] = "different"
    runs._json(path, vote)
    before = _files(run)
    with pytest.raises(SystemExit) as error:
        _score(run, g1, "--criteria", str(runs.CRITERIA), "--panel", str(runs.PANEL_PATH))
    assert error.value.code == 2
    assert _files(run) == before


def test_report_discloses_gate_draws_separately_from_descriptive_intervals(tmp_path: Path) -> None:
    from tests.benchmarks import test_agentic_report as reports

    run = reports._score(reports._build(tmp_path), tmp_path)
    report = reports._render(run)
    assert "Gate and claim bootstrap resamples: 10000" in report
    assert "2000 resamples" in report
    path = run / "grading/summary.json"
    old_summary = json.loads(path.read_bytes())
    del old_summary["resamples"]
    runs._json(path, old_summary)
    assert "Gate and claim bootstrap resamples: not recorded" in reports._render(run)


@pytest.mark.parametrize("fault", ["extra judge", "missing probes", "missing binding"])
def test_cli_requires_a_complete_matching_g1_binding(
    scripted_graded_run: Path, tmp_path: Path, fault: str
) -> None:
    run = scripted_graded_run
    root = run / "grading/inputs"
    if fault == "missing probes":
        manifest = json.loads((root / "inputs.json").read_bytes())
        del manifest["probes"]
        runs._json(root / "inputs.json", manifest)
        (root / "probes.yml").unlink()
    g1 = runs._g1_file(run, tmp_path / "g1.json")
    evidence = json.loads(g1.read_bytes())
    entry = next(iter(evidence["entries"].values()))
    if fault == "extra judge":
        entry["binding"]["composition"]["unexpected"] = entry["binding"]["composition"]["haiku"]
    elif fault == "missing binding":
        del entry["binding"]
    runs._json(g1, evidence)
    before = _files(run)
    with pytest.raises(SystemExit) as error:
        _score(run, g1)
    assert error.value.code == 2
    assert _files(run) == before
    if fault == "missing probes":
        assert main(["agentic-score", "--run-dir", str(run)]) == 0
        summary = json.loads((run / "grading/summary.json").read_bytes())
        assert summary["gates"]["G1"]["passed"] is None
        assert runs._record(run, "python-docs", "SYN-EX-002")["score"] == 0.5
