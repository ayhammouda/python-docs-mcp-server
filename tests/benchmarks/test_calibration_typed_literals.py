"""Declared schema regexes retain source semantics at every export boundary."""

import copy
import hashlib
import json
import re
from pathlib import Path

import pytest

from benchmarks.__main__ import main
from benchmarks.agentic.protocol import KeyLeakError
from benchmarks.grading import calibration_history
from benchmarks.grading.calibration import g1_entries
from benchmarks.grading.calibration_history import CalibrationSource
from benchmarks.grading.calibration_storage import pack_files, source_files
from benchmarks.grading.inputs import frozen_judge_document, judge_schema
from benchmarks.grading.score import _evidence_bytes
from tests.benchmarks import test_agentic_report as reports
from tests.benchmarks.test_agentic_report import validated_bundle as validated_bundle
from tests.benchmarks.test_agentic_report import validated_template as validated_template
from tests.benchmarks.test_calibration_attachments import uri
from tests.benchmarks.test_calibration_export_scan import _snapshot
from tests.benchmarks.test_calibration_metadata_boundary import MARKER, concealed

PATTERN = r'^"[A-Z]\d+"$'


@pytest.mark.parametrize("packed", [False, True])
@pytest.mark.parametrize("escape", ["unicode", "hex"])
@pytest.mark.parametrize("slot", ["pattern", "patternProperties"])
def test_regex_character_escapes_cannot_hide_a_literal_credential(tmp_path, packed, escape, slot):
    code = "\\u%04x" if escape == "unicode" else "\\x%02x"
    pattern = r'^\s*"' + "".join(code % ord(char) for char in MARKER) + '"$'
    assert re.fullmatch(pattern, '"' + MARKER + '"')
    schema = {"type": "string", slot: pattern if slot == "pattern" else {pattern: {}}}
    proof = proof_for(response(schema), packed)
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps({"entries": {"other": {"source": proof}}}).encode())
    with pytest.raises(KeyLeakError):
        CalibrationSource.restore(proof)
    with pytest.raises(KeyLeakError):
        CalibrationSource(tmp_path, dict(source_files(proof))).export({})


def schema_with_regex(pattern=PATTERN):
    return {"type": "object", "pattern": pattern, "properties": {
        "explanation": {"type": "string", "pattern": pattern},
    }, "patternProperties": {pattern: {"type": "string"}}}


def response(schema):
    return {"result": {"raw": {"response": {"body": {
        "text": {"format": {"type": "json_schema", "schema": schema}},
        "output": [{"content": [{"type": "output_text", "text": '{"explanation":"ok"}'}]}],
    }}}}}


def proof_for(record, packed=False, inputs=None):
    files = {"executions/run/test.json": json.dumps(record, ensure_ascii=True)}
    return {**(pack_files(files) if packed else {"version": 1, "files": files}),
            "inputs": inputs or {}}


def assert_boundaries(proof, tmp_path, monkeypatch, packed):
    entries = {"other": {"source": proof}}
    body = json.dumps({"entries": entries}).encode()
    assert g1_entries(body) == entries
    assert g1_entries(json.dumps(entries).encode(), projection=True) == entries
    assert json.loads(_evidence_bytes(Path("g1.json"), {"entries": entries},
                                     calibration=True)) == {"entries": entries}
    assert json.loads(_evidence_bytes(Path("g1-entries.json"), entries,
                                     calibration=True)) == entries
    restored, inputs = CalibrationSource.restore(copy.deepcopy(proof))
    files = dict(restored.files)
    assert inputs == {k: v.encode() for k, v in proof["inputs"].items()}
    monkeypatch.setattr(calibration_history, "PACK_SOURCE_BYTES", 0 if packed else 10**9)
    produced = CalibrationSource(tmp_path, files).export(inputs)
    assert produced == proof  # Exact source text, not a normalized regex/schema.
    assert restored.export(inputs) == proof


@pytest.mark.parametrize("packed", [False, True])
def test_exact_verified_quoted_letter_digits_source(tmp_path, monkeypatch, packed):
    proof = proof_for(response({"type": "string", "pattern": PATTERN}), packed)
    assert_boundaries(proof, tmp_path, monkeypatch, packed)


@pytest.mark.parametrize("packed", [False, True])
@pytest.mark.parametrize("pattern", [
    PATTERN, r'^\"[A-Z]\d+\"$', r'^\\\"[A-Z]\d+\\\"$',
    r'^"[A-Z]\u0041+"$', r'^"[A-Z]\\d+"$',
])
def test_responses_regexes_survive_real_producer_restore_and_archives(
    tmp_path, monkeypatch, packed, pattern,
):
    assert_boundaries(proof_for(response(schema_with_regex(pattern)), packed),
                      tmp_path, monkeypatch, packed)


@pytest.mark.parametrize("packed", [False, True])
def test_custom_frozen_json_schema_uses_the_same_regex_semantics(tmp_path, monkeypatch, packed):
    schema = judge_schema()
    schema["properties"]["explanation"]["pattern"] = PATTERN
    schema["patternProperties"] = {PATTERN: {"type": "string"}}
    text = json.dumps(schema, indent=2)
    assert frozen_judge_document(b"literal prompt", text.encode())[1] == schema
    proof = proof_for(response(schema_with_regex()), packed, {
        "judge_schema": text, "judge_prompt": 'Match ' + PATTERN,
        "mask_rules": PATTERN,
    })
    assert_boundaries(proof, tmp_path, monkeypatch, packed)


@pytest.mark.parametrize("keyword", [
    "properties", "$defs", "definitions", "dependentSchemas", "patternProperties",
    "additionalProperties", "items", "contains", "not", "if", "then", "else",
    "allOf", "anyOf", "oneOf", "prefixItems",
])
def test_nested_schema_positions_keep_regex_text(keyword):
    child = {"type": "string", "pattern": PATTERN}
    if keyword in ("properties", "$defs", "definitions", "dependentSchemas", "patternProperties"):
        child = {PATTERN if keyword == "patternProperties" else "named": child}
    elif keyword in ("allOf", "anyOf", "oneOf", "prefixItems"):
        child = [child]
    proof = proof_for(response({keyword: child}))
    entries = {"other": {"source": proof}}
    assert g1_entries(json.dumps({"entries": entries}).encode()) == entries


@pytest.mark.parametrize("slot", [
    "unknown-pattern", "fake-schema", "schema-extension", "schema-annotation", "raw-text",
    "regex-literal", "regex-attachment", "regex-key", "property-name", "const-object",
])
@pytest.mark.parametrize("packed", [False, True])
def test_literal_boundary_never_hides_other_retained_metadata(tmp_path, monkeypatch, packed, slot):
    schema = schema_with_regex()
    record = response(schema)
    if slot == "unknown-pattern":
        record["extension"] = {"pattern": concealed("key")}
    elif slot == "fake-schema":
        record["extension"] = {"text": {"format": {"type": "json_schema", "schema": {
            "pattern": concealed("key"),
        }}}}
    elif slot == "schema-extension":
        schema["extension"] = {"pattern": concealed("key")}
    elif slot == "schema-annotation":
        schema["description"] = concealed("value")
    elif slot == "raw-text":
        record["result"]["raw"]["response"]["body"]["output"][0]["content"][0]["text"] = {
            "format": {"type": "json_schema", "schema": {"pattern": concealed("key")}},
        }
    elif slot == "regex-literal":
        schema["pattern"] = '^"' + MARKER + '"$'
    elif slot == "regex-attachment":
        schema["pattern"] = uri(concealed("key"), media="application/json")
    elif slot == "regex-key":
        schema["patternProperties"] = {uri(MARKER): {"type": "string"}}
    elif slot == "property-name":
        schema["properties"][concealed("key")] = {"type": "string"}
    else:
        schema["const"] = {"pattern": concealed("key")}
    proof = proof_for(record, packed)
    body = json.dumps({"entries": {"other": {"source": proof}}}).encode()
    with pytest.raises(KeyLeakError):
        g1_entries(body)
    with pytest.raises(KeyLeakError):
        CalibrationSource.restore(proof)
    monkeypatch.setattr(calibration_history, "PACK_SOURCE_BYTES", 0 if packed else 10**9)
    with pytest.raises(KeyLeakError):
        CalibrationSource(tmp_path, dict(source_files(proof))).export({})


@pytest.mark.parametrize("wrapped", [False, True])
def test_normalized_vote_and_receipt_configuration_boundaries(wrapped):
    record = response(schema_with_regex())
    if not wrapped:
        record = record["result"]
    files = {"votes/test.json": json.dumps(record)}
    entries = {"other": {"source": {"version": 1, "inputs": {}, "files": files}}}
    assert g1_entries(json.dumps({"entries": entries}).encode()) == entries


@pytest.mark.parametrize("slot", ["pattern", "pattern-key"])
def test_source_json_escapes_are_decoded_before_literal_regex_inspection(slot):
    schema = {"pattern": MARKER} if slot == "pattern" else {"patternProperties": {MARKER: {}}}
    proof = proof_for(response(schema))
    # A JSON unicode escape is transport, unlike a backslash in the decoded regex.
    proof["files"]["executions/run/test.json"] = proof["files"][
        "executions/run/test.json"
    ].replace(MARKER, "\\u0041" + MARKER[1:])
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps({"entries": {"other": {"source": proof}}}).encode())


@pytest.mark.parametrize("packed", [False, True])
def test_healthy_score_report_readme_and_atomic_refusals(validated_bundle, packed, capsys):
    run = validated_bundle
    summary = json.loads((run / "grading/summary.json").read_bytes())
    assert summary["tier"] == "Validated"
    assert all(g["passed"] is True for g in summary["gates"].values())
    original = run / "grading/disclosure/g1" / (summary["disclosure"]["g1_files"][0]["sha256"]
                                               + ".json")
    data = json.loads(original.read_bytes())
    data["entries"]["other"] = {"source": proof_for(response(schema_with_regex()), packed)}
    data["entries"]["other"]["notes"] = 'old "broken\\q" passage'
    archive = run.parent / "literal-g1.json"
    archive.write_bytes(json.dumps(data, ensure_ascii=True).encode())
    args = ["agentic-score", "--run-dir", str(run), "--g1", str(archive)]
    assert main(args) == 0
    scored = json.loads((run / "grading/summary.json").read_bytes())
    assert scored["tier"] == summary["tier"]
    assert scored["gates"] == summary["gates"]
    assert main(["report", "--run-dir", str(run)]) == 0
    assert "Grading outcome: Validated" in (run / "REPORT.md").read_text()
    assert "fewer normalized tokens" in (run / "README-SUMMARY.md").read_text()
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    exported = run / f"grading/disclosure/g1/{digest}.json"
    assert exported.read_bytes() == archive.read_bytes()
    assert json.loads((run / "grading/disclosure/g1-entries.json").read_bytes()) == data["entries"]
    capsys.readouterr()
    # Keep harmless unknown prose escapes; refuse their hostile escaped-key twin
    # and opaque raw replies. Source document validation remains independent.
    def corrupt(entries, kind):
        if kind == "escaped-prose":
            entries["other"]["notes"] = 'old "broken\\q" passage \\u0041' + MARKER[1:]
        else:
            record = response(schema_with_regex())
            record["result"]["raw"]["response"]["body"]["output"][0]["content"][0]["text"] = {
                "opaque": concealed("key"),
            }
            entries["other"]["source"] = proof_for(record, packed)

    before = _snapshot(run)
    for kind in ("escaped-prose", "raw-secret"):
        hostile = copy.deepcopy(data)
        corrupt(hostile["entries"], kind)
        archive.write_bytes(json.dumps(hostile).encode())
        with pytest.raises(SystemExit) as caught:
            main(args)
        assert caught.value.code == 2
        assert _snapshot(run) == before
        # Both physical publication inputs are independently guarded.
        for boundary in ("original", "projection"):
            summary_path = run / "grading/summary.json"
            clean_summary = summary_path.read_bytes()
            target = (exported if boundary == "original"
                      else run / "grading/disclosure/g1-entries.json")
            clean = target.read_bytes()
            hostile = json.loads(clean)
            corrupt(hostile["entries"] if boundary == "original" else hostile, kind)
            hostile_body = json.dumps(hostile).encode()
            if boundary == "original":
                # Declare the exact bytes so digest rejection cannot mask the scan.
                hostile_digest = hashlib.sha256(hostile_body).hexdigest()
                target = run / f"grading/disclosure/g1/{hostile_digest}.json"
                changed = json.loads(clean_summary)
                changed["disclosure"]["g1_files"].append({
                    "sha256": hostile_digest, "path": f"disclosure/g1/{hostile_digest}.json",
                })
                summary_path.write_text(json.dumps(changed))
            target.write_bytes(hostile_body)
            frozen = _snapshot(run)
            for renderer in (reports._render, reports._readme):
                with pytest.raises(KeyLeakError):
                    renderer(run)
                assert _snapshot(run) == frozen
            with pytest.raises(SystemExit) as caught:
                main(["report", "--run-dir", str(run)])
            assert caught.value.code == 2
            assert _snapshot(run) == frozen
            if boundary == "projection":
                target.write_bytes(clean)
            summary_path.write_bytes(clean_summary)
        before = _snapshot(run)
    output = capsys.readouterr()
    assert MARKER not in output.out + output.err
