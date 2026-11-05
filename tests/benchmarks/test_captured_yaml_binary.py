"""Original binary scalars must survive inspection even when YAML shadows them."""

import base64
import hashlib
import json

import pytest
import yaml
from yaml.constructor import SafeConstructor

from benchmarks.agentic import inputs
from benchmarks.agentic.inputs import CapturedInput
from benchmarks.agentic.protocol import KeyLeakError
from benchmarks.grading.inputs import (
    CRITERIA_PATH,
    SNAPSHOT_FILES,
    inputs_manifest,
    snapshot_inputs,
)
from benchmarks.grading.panel import PANEL_PATH, load_panel
from benchmarks.runner import BenchmarkValidationError


def _binary_fragment(value: bytes, syntax: str) -> bytes:
    scalar = "!!binary " + base64.b64encode(value).decode("ascii")
    return {
        "duplicate": f"comment: {scalar}\ncomment: safe\n",
        "merge": f"<<: {{comment: {scalar}}}\ncomment: safe\n",
        "merge-list": f"<<: [{{comment: safe}}, {{comment: {scalar}}}]\n",
        "alias": f"comment: &hidden {scalar}\ncomment: safe\ncopy: *hidden\ncopy: safe\n",
        "visible": f"comment: {scalar}\n",
    }[syntax].encode()


@pytest.fixture(params=["duplicate", "merge", "merge-list", "alias", "visible"])
def binary_panel(tmp_path, request):
    # The real panel stays otherwise byte-for-byte intact; only synthetic data
    # is appended. Ordinary snapshot_inputs used to publish the hidden variants.
    marker = ("AIza" + "S" * 35).encode()
    body = PANEL_PATH.read_bytes() + b"\n" + _binary_fragment(marker, request.param)
    panel = tmp_path / "panel.yml"
    panel.write_bytes(body)
    return panel, body, marker


def test_snapshot_rejects_original_binary_before_any_mapping_or_write(
    tmp_path, binary_panel, monkeypatch
):
    panel, body, marker = binary_panel
    flattened = []
    original = SafeConstructor.flatten_mapping

    def observe(loader, node):
        flattened.append(node)
        return original(loader, node)

    monkeypatch.setattr(SafeConstructor, "flatten_mapping", observe)
    dest = tmp_path / "snapshot"
    with pytest.raises(KeyLeakError) as caught:
        snapshot_inputs(dest, criteria_path=CRITERIA_PATH, panel_path=panel, probes_path=None)
    assert marker.decode() not in str(caught.value)
    # Earlier inputs may be constructed, but this panel must never be flattened.
    assert all(not any(key.value == "judges" for key, _ in node.value) for node in flattened)
    assert not dest.exists()
    assert panel.read_bytes() == body


@pytest.mark.parametrize("name", [
    "corpus.yml", "model-matrix.yml", "competitor-manifest.yml", "price-snapshot.yml",
    "reference model-matrix.yml",
    "corpus", "panel", "criteria", "probes", "tuning", "grading input",
])
@pytest.mark.parametrize("syntax", ["duplicate", "merge", "merge-list", "alias", "visible"])
def test_all_capture_names_inspect_original_binary_scalars(name, syntax):
    marker = ("AIza" + "S" * 35).encode()
    with pytest.raises(KeyLeakError) as caught:
        CapturedInput.from_bytes(_binary_fragment(marker, syntax), name, "captured input")
    assert marker.decode() not in str(caught.value)


@pytest.mark.parametrize("syntax", [None, "duplicate", "merge", "merge-list", "alias", "visible"])
def test_normal_producer_preserves_real_panel_bytes_and_manifest(tmp_path, syntax):
    body = PANEL_PATH.read_bytes()
    if syntax is not None:
        body += b"\n" + _binary_fragment(b"harmless binary note", syntax)
    panel = tmp_path / "panel.yml"
    panel.write_bytes(body)
    assert load_panel(panel) == load_panel(PANEL_PATH)
    dest = tmp_path / "snapshot"
    manifest = snapshot_inputs(
        dest, criteria_path=CRITERIA_PATH, panel_path=panel, probes_path=None,
    )
    assert len(list(dest.iterdir())) == 9
    assert (dest / "panel.yml").read_bytes() == body
    assert json.loads((dest / "inputs.json").read_bytes()) == manifest
    assert manifest == inputs_manifest(
        criteria_path=CRITERIA_PATH, panel_path=panel, probes_path=None,
    )
    for name, digest in manifest.items():
        assert hashlib.sha256((dest / SNAPSHOT_FILES[name]).read_bytes()).hexdigest() == digest


def test_bounded_binary_aliases_are_decoded_once_and_keep_sharing(monkeypatch):
    tag = "tag:yaml.org,2002:binary"
    original = inputs._CapturedLoader.yaml_constructors[tag]
    decoded = []

    def observe(loader, node):
        decoded.append(node)
        return original(loader, node)

    monkeypatch.setattr(
        inputs._CapturedLoader, "yaml_constructors",
        {**inputs._CapturedLoader.yaml_constructors, tag: observe},
    )
    body = (
        b"base: &base {note: &note !!binary aGFybWxlc3M=}\n"
        b"merged: {<<: *base, extra: ordinary}\n"
        b"notes: [" + b", ".join([b"*note"] * 500) + b"]\n"
    )
    captured = CapturedInput.from_bytes(body, "panel", "panel")
    assert len(decoded) == 1
    assert captured.content == body
    assert captured.mapping == yaml.safe_load(body)
    note = captured.mapping["base"]["note"]
    assert captured.mapping["merged"]["note"] is note
    assert all(value is note for value in captured.mapping["notes"])


@pytest.mark.parametrize("scalar", [
    "!private-source hidden",
    "!!binary private-source",  # invalid padding, even if shadowed
    "!!binary ☃private-source",  # non-ASCII base64
])
@pytest.mark.parametrize("shadow", [False, True])
def test_constructor_errors_remain_sanitized(scalar, shadow):
    body = f"comment: {scalar}\n" + ("comment: safe\n" if shadow else "")
    with pytest.raises(BenchmarkValidationError, match="file is not valid YAML") as caught:
        CapturedInput.from_bytes(body.encode(), "panel", "panel")
    assert "private-source" not in str(caught.value)
    assert "☃" not in str(caught.value)


def test_binary_mapping_keys_are_inspected_before_overrides():
    marker = ("AIza" + "S" * 35).encode()
    scalar = "!!binary " + base64.b64encode(marker).decode("ascii")
    with pytest.raises(KeyLeakError):
        CapturedInput.from_bytes(f"{scalar}: old\n{scalar}: safe\n".encode(), "panel", "panel")
