"""Byte binding at the public, offline workspace and run scoring entry points."""

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from benchmarks.__main__ import main
from tests.benchmarks import test_grading_score as runs


def _cli(workspace, step, *args):
    return main(["agentic-score", "--workspace", str(workspace), "--step", step, *args])


def _workspace(tmp_path: Path, step: str):
    handoff = {"probes.yml": runs.PROBES}
    if step == "g1":
        evidence = tmp_path / "tuning.json"
        manifest = runs.snapshot_inputs(
            tmp_path / "tuning-inputs",
            criteria_path=runs.CRITERIA,
            panel_path=runs.PANEL_PATH,
            probes_path=runs.PROBES,
        )
        runs._json(
            evidence,
            {
                "inputs": manifest,
                "prompt_sha256": manifest["judge_prompt"],
                "judges": {
                    j: {"chosen": "low", "efforts": {"low": {"meets": True}}} for j in runs.JUDGES
                },
            },
        )
        handoff["tuning-1.json"] = evidence
    ws = runs._workspace(tmp_path / "workspace", "held-out" if step == "g1" else step, handoff)
    meta = json.loads((ws / "workspace.json").read_bytes())
    meta["tuning_sha256"] = hashlib.sha256(runs.TUNING_PATH.read_bytes()).hexdigest()
    if step == "g1":
        meta["judges"] = list(runs.JUDGES)
        runs._json(
            ws / "runs/probes.json",
            {
                "target": "probes",
                "pass": 1,
                "composition": runs.COMPOSITION,
            },
        )
    runs._json(ws / "workspace.json", meta)
    return ws


MARKER = "private-replacement-content"


def _refused(ws, step, capsys, *args):
    with pytest.raises(SystemExit) as error:
        _cli(ws, step, *args)
    assert error.value.code == 2 and MARKER not in capsys.readouterr().err
    assert not (ws / f"{step}.json").exists()


@pytest.mark.parametrize(
    ("step", "source"),
    [
        ("g1", "handoff/probes.yml"),
        ("tuning", "handoff/probes.yml"),
        ("g1", "inputs/probes.yml"),
        ("tuning", "inputs/panel.yml"),
        ("g1", "inputs/judge-prompt.md"),
        ("tuning", "inputs/grading-criteria.yml"),
        ("g1", "handoff/tuning-1.json"),
    ],
)
def test_workspace_refuses_replaced_sources_before_publication(tmp_path, capsys, step, source):
    ws = _workspace(tmp_path, step)
    path = ws / source
    if path.suffix == ".json":
        runs._json(path, {"replacement": MARKER})
    else:
        path.write_bytes(path.read_bytes() + f"\n# {MARKER}\n".encode())
    _refused(ws, step, capsys, "--panel", str(runs.PANEL_PATH))


@pytest.mark.parametrize(
    ("step", "flag", "source"),
    [
        ("tuning", "--panel", runs.PANEL_PATH),
        ("g1", "--panel", runs.PANEL_PATH),
        ("tuning", "--tuning", runs.TUNING_PATH),
        ("g1", "--criteria", runs.CRITERIA),
    ],
)
def test_workspace_refuses_a_different_input_override(tmp_path, capsys, step, flag, source):
    ws = _workspace(tmp_path, step)
    path = tmp_path / "override.yml"
    path.write_bytes(source.read_bytes() + f"\n# {MARKER}\n".encode())
    _refused(ws, step, capsys, flag, str(path))


@pytest.mark.parametrize(
    ("step", "file", "key", "member", "value"),
    [
        ("g1", "inputs/inputs.json", "panel", None, "0" * 64),
        ("g1", "workspace.json", "inputs", "panel", "0" * 64),
        ("g1", "workspace.json", "handoff", "probes.yml", "0" * 64),
        ("g1", "workspace.json", "handoff", "tuning-1.json", None),
        ("g1", "workspace.json", "judges", None, ["haiku", "luna", "flash"]),
        ("g1", "workspace.json", "judges", None, None),
        ("g1", "workspace.json", "tuning_sha256", None, None),
        ("tuning", "workspace.json", "tuning_sha256", None, "0" * 64),
    ],
)
def test_workspace_refuses_inconsistent_metadata(tmp_path, capsys, step, file, key, member, value):
    ws = _workspace(tmp_path, step)
    path = ws / file
    meta = json.loads(path.read_bytes())
    target, field = (meta[key], member) if member else (meta, key)
    if value is None:
        del target[field]
    else:
        target[field] = value
    runs._json(path, meta)
    _refused(ws, step, capsys, "--panel", str(runs.PANEL_PATH))


def test_workspace_refuses_missing_tuning_handoff(tmp_path, capsys):
    ws = _workspace(tmp_path, "g1")
    (ws / "handoff/tuning-1.json").unlink()
    _refused(ws, "g1", capsys, "--panel", str(runs.PANEL_PATH))


def test_replacing_probe_and_handoff_digest_cannot_override_the_snapshot(tmp_path, capsys):
    ws = _workspace(tmp_path, "g1")
    path = ws / "handoff/probes.yml"
    suite = yaml.safe_load(path.read_bytes())
    suite["probes"][0]["text"] = MARKER
    path.write_text(yaml.safe_dump(suite))
    meta = json.loads((ws / "workspace.json").read_bytes())
    meta["handoff"]["probes.yml"] = hashlib.sha256(path.read_bytes()).hexdigest()
    runs._json(ws / "workspace.json", meta)
    _refused(ws, "g1", capsys, "--panel", str(runs.PANEL_PATH))


@pytest.mark.parametrize(
    ("step", "target", "judge", "item", "effort"),
    [
        ("tuning", "tuning", "haiku", "KEY--TUNE-001", "low"),
        ("g1", "probes", "haiku", "KEY--SYN-EX-001", "low"),
        ("g1", "reference-probes", "reference", "KEY--SYN-EX-001", "high"),
    ],
)
def test_workspace_vote_requires_the_frozen_judge_model(
    tmp_path, step, target, judge, item, effort
):
    ws = _workspace(tmp_path, step)
    runs._vote(ws, judge, item, runs._vote_text(), target=target, effort=effort)
    path = next((ws / "votes").rglob("*.json"))
    runs._json(path, {**json.loads(path.read_bytes()), "model_id": "foreign-model"})
    with pytest.raises(SystemExit) as error:
        _cli(ws, step, "--panel", str(runs.PANEL_PATH))
    assert error.value.code == 2 and not (ws / f"{step}.json").exists()


def _replace_after_open(monkeypatch, source, replacement):
    original_open = Path.open

    def swap(path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        if path == source and replacement.exists():
            replacement.replace(path)
        return handle

    monkeypatch.setattr(Path, "open", swap)


@pytest.mark.parametrize(
    ("step", "name"),
    [
        ("g1", "inputs/grading-criteria.yml"),
        ("g1", "handoff/probes.yml"),
        ("g1", "handoff/tuning-1.json"),
        ("tuning", "tuning.yml"),
        ("tuning", "inputs/panel.yml"),
    ],
)
def test_portable_workspace_consumes_captured_bytes(tmp_path, monkeypatch, step, name):
    ws = _workspace(tmp_path, step)
    tuning = tmp_path / "tuning.yml"
    tuning.write_bytes(runs.TUNING_PATH.read_bytes())
    replacement = tmp_path / "changed"
    replacement.write_bytes(b"{}")
    _replace_after_open(monkeypatch, tuning if name == "tuning.yml" else ws / name, replacement)
    assert _cli(ws, step, "--tuning", str(tuning)) == 0
    assert not replacement.exists()
    result = json.loads((ws / f"{step}.json").read_bytes())
    if step == "g1":
        entry = next(iter(result["entries"].values()))
        assert entry["gate"]["passed"] is None and entry["gate"]["details"]["overall"]["n"] == 28
        assert entry["tuning"] == [json.loads((tmp_path / "tuning.json").read_bytes())]
    else:
        assert "flash" in result["judges"]


def test_run_replays_captured_probes_when_the_file_is_replaced(tmp_path, monkeypatch):
    run = runs._build_run(tmp_path)
    g1 = runs._g1_file(run, tmp_path / "g1.json")
    replacement = tmp_path / "changed"
    replacement.write_bytes(b"{}")
    _replace_after_open(monkeypatch, run / "grading/inputs/probes.yml", replacement)
    assert main(["agentic-score", "--run-dir", str(run), "--g1", str(g1)]) == 0
    assert not replacement.exists()
    summary = json.loads((run / "grading/summary.json").read_bytes())
    assert summary["gates"]["G1"]["passed"] is None
    assert runs._record(run, "python-docs", runs.QUESTIONS[0])["score"] == 1.0


@pytest.mark.parametrize("change", ["criteria", "effort", "prompt", "settings"])
def test_frozen_held_out_workspace_checks_tuning_record_binding(tmp_path, change):
    ws = _workspace(tmp_path, "g1")
    path = ws / "handoff/tuning-1.json"
    evidence = json.loads(path.read_bytes())
    if change == "criteria":
        evidence["inputs"]["criteria"] = "0" * 64
    elif change == "settings":
        evidence["judge_settings"] = {j: "0" * 64 for j in runs.JUDGES}
    elif change == "prompt":
        evidence["prompt_sha256"] = "0" * 64
    else:
        evidence["judges"]["luna"]["chosen"] = "medium"
    runs._json(path, evidence)
    meta = json.loads((ws / "workspace.json").read_bytes())
    meta["handoff"]["tuning-1.json"] = hashlib.sha256(path.read_bytes()).hexdigest()
    runs._json(ws / "workspace.json", meta)
    with pytest.raises(SystemExit) as error:
        _cli(ws, "g1")
    assert error.value.code == 2 and not (ws / "g1.json").exists()


@pytest.mark.parametrize(
    ("judge", "has_reference"),
    [("haiku", False), ("reference", True), ("flash", True), ("reference", False)],
)
def test_run_checks_settings_for_the_selected_panel_and_reference_only(
    tmp_path, judge, has_reference
):
    ws = _workspace(tmp_path, "g1")
    if has_reference:
        runs._vote(
            ws,
            "reference",
            "KEY--SYN-EX-001",
            runs._vote_text(),
            target="reference-probes",
            effort="high",
        )
    assert _cli(ws, "g1") == 0
    run = runs._build_run(tmp_path)
    root = run / "grading/inputs"
    path = root / "panel.yml"
    panel = yaml.safe_load(path.read_bytes())
    panel["judges"][judge]["max_output_tokens"] //= 2
    path.write_text(yaml.safe_dump(panel))
    manifest = json.loads((root / "inputs.json").read_bytes())
    manifest["panel"] = hashlib.sha256(path.read_bytes()).hexdigest()
    runs._json(root / "inputs.json", manifest)
    args = ["agentic-score", "--run-dir", str(run), "--g1", str(ws / "g1.json")]
    if judge == "flash" or (judge == "reference" and not has_reference):
        assert main(args) == 0
        return
    before = {f: f.read_bytes() for f in (run / "scoring").rglob("*.json")}
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2 and not (run / "grading/summary.json").exists()
    assert all(path.read_bytes() == content for path, content in before.items())


def test_portable_held_out_requires_tuned_efforts_from_a_bound_record(tmp_path, capsys):
    ws = _workspace(tmp_path, "g1")
    path = ws / "handoff/tuning-1.json"
    evidence = json.loads(path.read_bytes())
    del evidence["judges"]["haiku"]
    runs._json(path, evidence)
    meta = json.loads((ws / "workspace.json").read_bytes())
    del meta["judges"], meta["tuning_sha256"]  # Direct part-one workspace layout.
    meta["handoff"][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    runs._json(ws / "workspace.json", meta)
    _refused(ws, "g1", capsys)
