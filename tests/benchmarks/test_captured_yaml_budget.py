"""Frozen YAML must be bounded during construction, not just credential scanning."""

import base64
import hashlib
import json
from pathlib import Path

import pytest
from yaml.constructor import SafeConstructor

from benchmarks.__main__ import main
from benchmarks.agentic import inputs
from benchmarks.agentic.inputs import CapturedInput
from benchmarks.agentic.protocol import KeyLeakError
from benchmarks.grading.calibration_history import CalibrationSource
from benchmarks.grading.calibration_scan import scan_calibration_source
from benchmarks.grading.calibration_storage import pack_files
from benchmarks.grading.inputs import load_criteria, snapshot_inputs
from benchmarks.grading.panel import load_panel
from benchmarks.grading.score import _model_families
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks import test_grading_score as runs


def merge_graph(levels: int = 14) -> bytes:
    lines = ["a0: &a0 {value: ordinary}"]
    for level in range(1, levels + 1):
        lines.append(f"a{level}: &a{level} {{<<: [*a{level - 1}, *a{level - 1}]}}")
    return ("\n".join(lines) + "\n").encode()


@pytest.mark.parametrize("packed", [False, True])
def test_selected_source_rejects_merge_amplification_before_replay(monkeypatch, packed):
    stored = pack_files({"votes/example.json": "{}"}) if packed else {"version": 1, "files": {}}
    proof = {**stored, "inputs": {"panel": merge_graph().decode()}}
    # This representation is bounded to inspect: the dangerous step is construction.
    scan_calibration_source(proof)
    flattened = []
    original = SafeConstructor.flatten_mapping

    def observe(loader, node):
        original(loader, node)
        flattened.append(len(node.value))

    monkeypatch.setattr(SafeConstructor, "flatten_mapping", observe)
    with pytest.raises(BenchmarkValidationError, match="YAML complexity limit"):
        CalibrationSource.restore(proof)
    assert flattened == []  # A post-construction limit would already be too late.


def test_producer_refuses_amplifying_input_before_snapshot_writes(tmp_path: Path):
    panel = tmp_path / "panel.yml"
    panel.write_bytes(merge_graph())
    dest = tmp_path / "snapshot"
    with pytest.raises(BenchmarkValidationError, match="YAML complexity limit"):
        snapshot_inputs(
            dest, criteria_path=Path("tests/benchmarks/fixtures/grading/criteria.sample.yml"),
            panel_path=panel, probes_path=None,
        )
    assert not dest.exists()


def test_scorer_model_snapshot_also_uses_bounded_construction(tmp_path: Path):
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    (snapshots / "model-matrix.yml").write_bytes(merge_graph())
    with pytest.raises(BenchmarkValidationError, match="YAML complexity limit"):
        _model_families(tmp_path)


@pytest.mark.parametrize("consumer", ["panel", "criteria"])
def test_grading_producer_loaders_share_construction_budget(tmp_path: Path, consumer: str):
    path = tmp_path / "input.yml"
    path.write_bytes(merge_graph())
    with pytest.raises(BenchmarkValidationError, match="YAML complexity limit"):
        if consumer == "panel":
            load_panel(path)
        else:
            load_criteria(path, corpus_path=path, schema_path=runs.SCHEMA)


def test_ordinary_score_refuses_selected_source_before_any_writes(tmp_path: Path, capsys):
    run = runs._build_run(tmp_path)
    path = runs._g1_file(run, tmp_path / "g1.json")
    evidence = json.loads(path.read_bytes())
    entry = next(iter(evidence["entries"].values()))
    entry["source"] = {"version": 1, "inputs": {"panel": merge_graph().decode()}, "files": {}}
    path.write_text(json.dumps(evidence))
    before = {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}
    with pytest.raises(SystemExit) as caught:
        main(["agentic-score", "--run-dir", str(run), "--g1", str(path)])
    assert caught.value.code == 2
    assert "YAML complexity limit" in capsys.readouterr().err
    assert {p: p.read_bytes() for p in run.rglob("*") if p.is_file()} == before


def test_bounded_merges_and_shared_aliases_keep_bytes_and_precedence():
    body = b"""base: &base {x: 1, y: 2}
other: &other {x: 9, z: 3}
merged: {<<: [*base, *other], y: 4}
alias: *base
override: old
override: new
"""
    captured = CapturedInput.from_bytes(body, "panel", "panel")
    assert captured.content == body
    assert hashlib.sha256(captured.content).digest() == hashlib.sha256(body).digest()
    assert captured.mapping["merged"] == {"x": 1, "y": 4, "z": 3}
    assert captured.mapping["alias"] is captured.mapping["base"]
    assert captured.mapping["override"] == "new"


def test_bounded_nested_merges_retain_normal_safe_loader_result():
    captured = CapturedInput.from_bytes(merge_graph(12), "panel", "panel")
    assert captured.mapping["a12"] == {"value": "ordinary"}


@pytest.mark.parametrize("packed", [False, True])
def test_restored_bounded_yaml_keeps_exact_archived_input_bytes(packed):
    body = b"base: &base {value: ordinary}\ncopy: {<<: *base}\n"
    stored = pack_files({"votes/example.json": "{}"}) if packed else {"version": 1, "files": {}}
    _, restored = CalibrationSource.restore({
        **stored, "inputs": {"panel": body.decode()},
    })
    assert restored["panel"] == body


@pytest.mark.parametrize("body", [
    b"value: &self {<<: *self}\n",
    b"value: " + b"[" * 70 + b"0" + b"]" * 70,
])
def test_recursive_merges_and_deep_graphs_have_safe_errors(body: bytes):
    with pytest.raises(BenchmarkValidationError, match="^YAML complexity limit exceeded$"):
        CapturedInput.from_bytes(body, "untrusted-name", "untrusted-label")


def test_many_alias_edges_are_bounded_without_banning_shared_values(monkeypatch):
    monkeypatch.setattr(inputs, "_MAX_YAML_NODES", 100)
    body = b"base: &base [0]\nvalues: [" + b", ".join([b"*base"] * 110) + b"]"
    with pytest.raises(BenchmarkValidationError, match="YAML complexity limit"):
        CapturedInput.from_bytes(body, "panel", "panel")
    normal = CapturedInput.from_bytes(b"value: &v [*v]", "panel", "panel")
    assert normal.mapping["value"][0] is normal.mapping["value"]


def test_merge_budget_is_cumulative_across_independent_mappings(monkeypatch):
    monkeypatch.setattr(inputs, "_MAX_YAML_MERGE_PAIRS", 100)
    body = b"base: &base {a: 1, b: 2}\n" + b"".join(
        f"v{i}: {{<<: *base}}\n".encode() for i in range(51)
    )
    with pytest.raises(BenchmarkValidationError, match="YAML complexity limit"):
        CapturedInput.from_bytes(body, "panel", "panel")


def test_cached_merge_sources_cannot_hide_dependency_depth():
    lines = ["a0: &a0 {value: ordinary}"]
    lines.extend(f"a{i}: &a{i} {{<<: *a{i - 1}}}" for i in range(1, 70))
    # Preflight visits these aliases shallowest-first. A cached width alone
    # cannot prove the depth of the dependency chain is within the limit.
    lines.append("aliases: [" + ", ".join(f"*a{i}" for i in reversed(range(70))) + "]")
    with pytest.raises(BenchmarkValidationError, match="YAML complexity limit"):
        CapturedInput.from_bytes("\n".join(lines).encode(), "panel", "panel")


@pytest.mark.parametrize("syntax", ["duplicate", "merge", "binary"])
def test_discarded_credentials_still_refuse_safely(syntax):
    marker = "AIza" + "S" * 35  # synthetic; no credential access
    encoded = '"' + "".join(f"\\u{ord(c):04x}" for c in marker) + '"'
    body = {
        "duplicate": f"value: {encoded}\nvalue: safe\n",
        "merge": f"value: {{<<: {{hidden: {encoded}}}, hidden: safe}}\n",
        "binary": "value: !!binary " + base64.b64encode(marker.encode()).decode(),
    }[syntax].encode()
    with pytest.raises(KeyLeakError) as caught:
        CapturedInput.from_bytes(body, "panel", "panel")
    assert marker not in str(caught.value)


def test_input_read_stops_at_frame_bound(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(inputs, "MAX_FRAME_BYTES", 32)
    path = tmp_path / "large.yml"
    path.write_bytes(b"value: " + b"x" * 32)
    with pytest.raises(BenchmarkValidationError, match="artifact size limit"):
        CapturedInput.read(path, "panel", "panel")
