"""Explicit YAML metadata cannot bypass whole-archive credential inspection."""

import base64
import copy
import hashlib
import json
from pathlib import Path

import pytest

from benchmarks.agentic.protocol import KeyLeakError
from benchmarks.grading.calibration import g1_entries
from benchmarks.grading.calibration_history import CalibrationSource
from benchmarks.grading.calibration_storage import pack_files
from benchmarks.grading.score import _evidence_bytes, _write, score_run_dir
from tests.benchmarks import test_agentic_report as reports
from tests.benchmarks.test_agentic_report import validated_bundle as validated_bundle
from tests.benchmarks.test_agentic_report import validated_template as validated_template
from tests.benchmarks.test_calibration_metadata_boundary import MARKER
from tests.benchmarks.test_grading_score import SCHEMA

TAGS = ("!!binary", "!<tag:yaml.org,2002:binary>")


def binary(tag=TAGS[0], text=MARKER):
    return tag + " " + base64.b64encode(text.encode()).decode()


def archive(packed=False):
    proof = {**(pack_files({}) if packed else {"version": 1, "files": {}}), "inputs": {}}
    return {"entries": {"other": {
        "source": copy.deepcopy(proof), "binding": {},
        "tuning": [{"source": copy.deepcopy(proof), "binding": {}}],
    }}}


def inject(data, text, slot, tuning=False, key=False):
    entry = data["entries"]["other"]
    if tuning:
        entry = entry["tuning"][0]
    target = {"outer": data, "entry": entry, "binding": entry["binding"],
              "source": entry["source"]}.get(slot)
    if slot == "nested":
        target = entry["binding"].setdefault("extension", [{"nested": {}}])[0]["nested"]
    assert target is not None
    target[text if key else "notes"] = "ordinary history" if key else text


@pytest.mark.parametrize("packed", [False, True])
@pytest.mark.parametrize("tuning", [False, True])
@pytest.mark.parametrize("slot", ["outer", "entry", "binding", "nested", "source"])
@pytest.mark.parametrize("key", [False, True])
@pytest.mark.parametrize("form", ["short", "full", "json-string", "nested-json", "yaml-string"])
def test_binary_metadata_refuses_import_export_and_preserves_original(
    tmp_path, packed, tuning, slot, key, form,
):
    text = binary(TAGS[1] if form == "full" else TAGS[0])
    if form == "json-string":
        text = json.dumps(text)
    elif form == "nested-json":
        text = json.dumps({"notes": json.dumps(text)})
    elif form == "yaml-string":
        text = "'" + text + "'"
    data = archive(packed)
    inject(data, text, slot, tuning, key)
    original = json.dumps(data).encode()
    with pytest.raises(KeyLeakError) as caught:
        g1_entries(original)
    assert MARKER not in str(caught.value) and text not in str(caught.value)
    path = tmp_path / "g1.json"
    path.write_bytes(b"previous evidence")
    with pytest.raises(KeyLeakError):
        _write(tmp_path, path, data, calibration=True)
    assert path.read_bytes() == b"previous evidence"
    assert json.dumps(data).encode() == original
    if slot != "outer":
        with pytest.raises(KeyLeakError):
            g1_entries(json.dumps(data["entries"]).encode(), projection=True)
        with pytest.raises(KeyLeakError):
            _evidence_bytes(Path("g1-entries.json"), data["entries"], calibration=True)


@pytest.mark.parametrize("packed", [False, True])
@pytest.mark.parametrize("tag", TAGS)
def test_actual_benign_typed_binary_inputs_remain_byte_exact(tmp_path, packed, tag):
    text = "notes: " + binary(tag, "ordinary input bytes") + "\n"
    proof = archive(packed)["entries"]["other"]["source"]
    proof["inputs"]["panel"] = text
    original = json.dumps({"entries": {"other": {"source": proof}}}).encode()
    assert g1_entries(original)["other"]["source"] == proof
    restored, inputs = CalibrationSource.restore(proof)
    assert inputs == {"panel": text.encode()}
    assert restored.export(inputs)["inputs"]["panel"] == text


@pytest.mark.parametrize("packed", [False, True])
@pytest.mark.parametrize("slot", ["entry-name", "source-name", "input-name", "file-name"])
def test_binary_metadata_member_names_share_the_same_boundary(packed, slot):
    data = archive(packed)
    entry = data["entries"]["other"]
    text = binary(TAGS[1])
    if slot == "entry-name":
        data["entries"][text] = data["entries"].pop("other")
    elif slot == "source-name":
        entry["source"][text] = "ordinary history"
    elif slot == "input-name":
        entry["source"]["inputs"][text] = "ordinary history"
    else:
        files = {"runs/note: " + text + ".json": "{}"}
        entry["source"].update(pack_files(files) if packed else {"files": files})
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps(data).encode())
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps(data["entries"]).encode(), projection=True)


@pytest.mark.parametrize("text", [
    "Prose mentions !!binary and !<tag:yaml.org,2002:binary> tags.",
    "YAML !!binary tags encode bytes; these are ordinary notes.",
    "Mention `!!binary` without declaring an encoding.",
    "An example of the tag is !!binary",
    'The bare tag names are "!!binary" and "!<tag:yaml.org,2002:binary>".',
])
def test_tag_mentions_are_plain_prose_everywhere(text):
    data = archive()
    for slot in ("outer", "entry", "binding", "nested", "source"):
        inject(data, text, slot)
    body = json.dumps(data).encode()
    assert g1_entries(body) == data["entries"]
    assert json.loads(_evidence_bytes(Path("g1.json"), data, calibration=True)) == data


@pytest.mark.parametrize("text", [
    binary(), "note: " + binary(), "note: !!binary |\n  " + binary().split()[1],
    "{? " + binary(TAGS[1]) + ": safe}",
    "note: !!binary '" + binary().split()[1] + "'",
    "note: !!binary not-valid-base64!", binary(text="harmless metadata"),
    binary().replace("!!binary", "!!%62inary"),
    binary().replace("!!binary", "!<tag:yaml.org,2002:%62inary>"),
    "%TAG !b! tag:yaml.org,2002:\n--- !b!binary " + binary().split()[1],
])
def test_explicit_metadata_yaml_is_a_documented_format_limit(text):
    data = archive()
    inject(data, text, "entry")
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps(data).encode())


def test_single_quoted_yaml_wrappers_share_the_metadata_depth_and_byte_budget(monkeypatch):
    from benchmarks.grading import calibration_scan

    text = "'" + binary() + "'"
    with pytest.raises(KeyLeakError):
        calibration_scan._metadata_text(text)
    # A wrapper whose decoded value is plain prose still consumes a layer and
    # bytes; it cannot reset either common metadata bound.
    prose = "YAML !!binary tags encode bytes."
    for _ in range(4):
        prose = "'" + prose.replace("'", "''") + "'"
    calibration_scan._metadata_text(prose)
    with pytest.raises(KeyLeakError, match="depth"):
        calibration_scan._metadata_text("'" + prose.replace("'", "''") + "'")
    monkeypatch.setattr(calibration_scan, "MAX_FRAME_BYTES", 200)
    siblings = "[" + ", ".join(["'YAML !!binary tags encode bytes.'"] * 5) + "]"
    # The YAML scalar strings overlap the JSON quote-recovery budget.
    with pytest.raises(KeyLeakError, match="size"):
        calibration_scan._metadata_text(json.dumps(siblings))


def test_yaml_metadata_tokenization_is_bounded_without_expanding_aliases(monkeypatch):
    from benchmarks.agentic import inputs
    from benchmarks.grading import calibration_scan

    text = "base: &b harmless\nrefs: [" + ", ".join(["*b"] * 100) + "]\n"
    text += "notes: YAML !!binary tags encode bytes.\n"
    calibration_scan._metadata_text(text)
    monkeypatch.setattr(inputs, "_MAX_YAML_NODES", 20)
    with pytest.raises(KeyLeakError, match="limit"):
        calibration_scan._metadata_text(text)


@pytest.mark.parametrize("text", [
    "prefix: [" + binary(),
    "notes: \"YAML !!binary tags encode bytes\\q\"",
])
def test_invalid_explicit_yaml_metadata_has_a_content_free_error(text):
    data = archive()
    inject(data, text, "entry")
    with pytest.raises(KeyLeakError) as caught:
        g1_entries(json.dumps(data).encode())
    assert text not in str(caught.value) and MARKER not in str(caught.value)


def test_long_plain_punctuation_is_still_usable_metadata():
    data = archive()
    inject(data, "Historical notation: " + "!" * 100_000 + " done", "entry")
    assert g1_entries(json.dumps(data).encode()) == data["entries"]


@pytest.mark.parametrize("boundary,slot,key", [
    ("original", "outer", False), ("original", "entry", False),
    ("original", "nested", True), ("projection", "entry", False),
    ("projection", "binding", True),
])
def test_both_report_readers_refuse_digest_correct_binary_metadata_before_writes(
    validated_bundle, boundary, slot, key,
):
    run = validated_bundle
    (run / "REPORT.md").write_text("previous report")
    (run / "README-SUMMARY.md").write_text("previous summary")
    data = archive(True)
    inject(data, json.dumps(binary(TAGS[1])), slot, tuning=True, key=key)

    def snapshot():
        return {p.relative_to(run): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in run.rglob("*") if p.is_file()}

    supplied = run.parent / "supplied-binary.json"
    supplied.write_text(json.dumps(data))
    original = supplied.read_bytes()
    before = snapshot()
    with pytest.raises(KeyLeakError):
        score_run_dir(run, schema_path=SCHEMA, g1_paths=[supplied])
    assert snapshot() == before and supplied.read_bytes() == original
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
    before = snapshot()
    for reader in (reports._render, reports._readme):
        with pytest.raises(KeyLeakError):
            reader(run)
        assert snapshot() == before
