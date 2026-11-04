"""Retained metadata must not escape the common import/export guard."""

import base64
import copy
import hashlib
import json
from pathlib import Path

import pytest

from benchmarks.agentic.protocol import KeyLeakError
from benchmarks.grading.calibration import g1_entries
from benchmarks.grading.calibration_storage import pack_files
from benchmarks.grading.score import _evidence_bytes
from tests.benchmarks import test_agentic_report as reports
from tests.benchmarks.test_agentic_report import validated_bundle as validated_bundle
from tests.benchmarks.test_agentic_report import validated_template as validated_template

MARKER = "A" + "Iza" + "synthetic" * 3 + "q" * 8


def concealed(kind):
    escaped = "\\u0041" + MARKER[1:]
    return {
        "value": '{"reported_model":"' + escaped + '"}',
        "key": '{"' + escaped + '":"harmless"}',
        "shadowed": '{"note":"' + escaped + '","note":"harmless"}',
        "malformed": 'old reply: {"note":"' + escaped,
        "quoted": 'old reply: "' + escaped + '" trailing prose',
        "nested-string": json.dumps('{"note":"' + escaped + '"}'),
    }[kind]


def inject(data, slot, text, *, key=False):
    entry = data["entries"]["unselected"]
    targets = {
        "outer": data,
        "entry": entry,
        "tuning": entry["tuning"][0],
        "binding": entry["binding"],
        "gate": entry["gate"]["details"],
        "nested": entry["extension"][0]["nested"],
    }
    target = targets[slot]
    target[text if key else "notes"] = "ordinary history" if key else text


def archive():
    return {"entries": {"unselected": {
        "source": {"version": 1, "inputs": {}, "files": {}},
        "binding": {}, "gate": {"details": {}}, "tuning": [{}],
        "extension": [{"nested": {}}],
    }}}


@pytest.mark.parametrize("slot", ["outer", "entry", "tuning", "binding", "gate", "nested"])
@pytest.mark.parametrize("kind", [
    "value", "key", "shadowed", "malformed", "quoted", "nested-string",
])
@pytest.mark.parametrize("key", [False, True])
def test_retained_metadata_keys_and_values_are_inspected(slot, kind, key):
    data = archive()
    inject(data, slot, concealed(kind), key=key)
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps(data).encode())
    with pytest.raises(KeyLeakError):
        _evidence_bytes(Path("g1.json"), data, calibration=True)
    if slot != "outer":
        with pytest.raises(KeyLeakError):
            g1_entries(json.dumps(data["entries"]).encode(), projection=True)
        with pytest.raises(KeyLeakError):
            _evidence_bytes(Path("g1-entries.json"), data["entries"], calibration=True)


@pytest.mark.parametrize("text", [
    'Unicode café 雪; C:\\Users\\history; literal \\u0041; "quoted"',
    json.dumps({"history": 'Unicode café 雪; C:\\Users\\history; literal \\u0041; "quoted"'}),
    'Use [brackets], {braces}, and `code` in historical notes.',
    'old reply: {"note":"café 雪","note":"harmless"}',
    'An unfinished "ordinary quotation',
])
def test_harmless_anonymous_metadata_survives_exactly(text):
    data = archive()
    for slot in ("outer", "entry", "tuning", "binding", "gate", "nested"):
        inject(data, slot, text)
    assert g1_entries(json.dumps(data, ensure_ascii=True).encode()) == data["entries"]
    result = _evidence_bytes(Path("g1.json"), data, calibration=True)
    assert json.loads(result) == data


@pytest.mark.parametrize("text", [
    'old "broken\\q" passage',
    'old "unfinished\\u00',
])
def test_malformed_quoted_escapes_are_not_exported(text):
    data = archive()
    inject(data, "entry", text)
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps(data).encode())


def test_serialization_depth_is_finite():
    text = concealed("value")
    for _ in range(12):
        text = json.dumps(text)
    data = archive()
    inject(data, "entry", text)
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps(data).encode())


def test_metadata_walk_does_not_expand_packed_members_twice(monkeypatch):
    from benchmarks.grading.calibration_storage import PackedFiles

    files = {"votes/old.json": json.dumps({"text": '{"explanation":"harmless"}'})}
    data = archive()
    data["entries"]["unselected"]["source"] = {**pack_files(files), "inputs": {}}
    reads = []
    original = PackedFiles.__getitem__

    def once(self, name):
        reads.append(name)
        return original(self, name)

    monkeypatch.setattr(PackedFiles, "__getitem__", once)
    assert g1_entries(json.dumps(data).encode()) == data["entries"]
    assert reads == ["votes/old.json"]


def test_producer_rejects_outer_metadata_before_replacing_file(tmp_path):
    from benchmarks.grading.score import _write

    path = tmp_path / "g1.json"
    path.write_bytes(b"previous evidence")
    data = archive()
    inject(data, "outer", concealed("value"))
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(KeyLeakError):
        _write(tmp_path, path, copy.deepcopy(data), calibration=True)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("packed", [False, True])
@pytest.mark.parametrize("slot", ["extension", "file", "input"])
def test_source_metadata_uses_the_same_embedded_json_boundary(packed, slot):
    proof = {**(pack_files({}) if packed else {"version": 1, "files": {}}), "inputs": {}}
    if slot == "extension":
        proof["extension"] = [{"nested": concealed("key")}]
    elif slot == "file":
        files = {"runs/old.json": json.dumps({"extension": {"notes": concealed("key")}})}
        proof.update(pack_files(files) if packed else {"files": files})
    else:
        proof["inputs"]["panel"] = json.dumps({"notes": concealed("key")})
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps({"entries": {"other": {"source": proof}}}).encode())


def test_source_anonymous_extension_preserves_harmless_nested_metadata():
    data = archive()
    data["entries"]["unselected"]["source"]["extension"] = [
        {"notes": json.dumps({"history": 'café 雪; C:\\Users\\history; "quoted"'})},
    ]
    assert g1_entries(json.dumps(data).encode()) == data["entries"]


def test_outer_metadata_shares_the_existing_metadata_budget(monkeypatch):
    from benchmarks.grading import calibration, calibration_storage
    from benchmarks.runner import BenchmarkValidationError

    monkeypatch.setattr(calibration, "MAX_FRAME_BYTES", 1)
    monkeypatch.setattr(calibration_storage, "MAX_METADATA_BYTES", 700)
    data = {"entries": {"other": {}}, "notes": "x" * 701}
    with pytest.raises(BenchmarkValidationError, match="metadata"):
        g1_entries(json.dumps(data).encode())
    with pytest.raises(BenchmarkValidationError, match="metadata"):
        _evidence_bytes(Path("g1.json"), data, calibration=True)


def test_binary_frozen_inputs_inspect_embedded_json():
    text = concealed("value")
    text = "notes: !!binary " + base64.b64encode(text.encode()).decode()
    proof = {"version": 1, "files": {}, "inputs": {"panel": text}}
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps({"entries": {"other": {"source": proof}}}).encode())


def test_literal_masking_regex_is_not_an_embedded_json_string():
    from benchmarks.grading.inputs import MASK_RULES

    data = archive()
    data["entries"]["unselected"]["source"]["inputs"]["mask_rules"] = MASK_RULES
    assert g1_entries(json.dumps(data).encode()) == data["entries"]


@pytest.mark.parametrize("boundary,slot", [
    ("original", "outer"), ("original", "entry"), ("original", "tuning"),
    ("original", "binding"), ("projection", "entry"), ("projection", "tuning"),
    ("projection", "binding"),
])
def test_both_public_readers_refuse_metadata_and_preserve_outputs(validated_bundle, boundary, slot):
    run = validated_bundle
    # Existing published artifacts must survive a failed refresh byte for byte.
    (run / "REPORT.md").write_text("previous public report")
    (run / "README-SUMMARY.md").write_text("previous public summary")
    data = archive()
    inject(data, slot, concealed("key"), key=slot == "binding")
    if boundary == "projection":
        (run / "grading/disclosure/g1-entries.json").write_text(json.dumps(data["entries"]))
    else:
        body = json.dumps(data).encode()
        digest = hashlib.sha256(body).hexdigest()
        path = run / f"grading/disclosure/g1/{digest}.json"
        path.write_bytes(body)
        summary_path = run / "grading/summary.json"
        summary = json.loads(summary_path.read_bytes())
        summary["disclosure"]["g1_files"].append({
            "sha256": digest, "path": f"disclosure/g1/{digest}.json",
        })
        summary_path.write_text(json.dumps(summary))
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in run.rglob("*") if p.is_file()}
    for renderer in (reports._render, reports._readme):
        with pytest.raises(KeyLeakError):
            renderer(run)
        assert {p: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in run.rglob("*") if p.is_file()} == before
