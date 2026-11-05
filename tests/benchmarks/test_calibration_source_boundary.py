"""Whole retained sources and recoverable withheld replies cross the guard."""

import base64
import copy
import hashlib
import json
from pathlib import Path

import pytest

from benchmarks.__main__ import main
from benchmarks.agentic.protocol import KeyLeakError
from benchmarks.grading.calibration import g1_entries
from benchmarks.grading.calibration_history import CalibrationSource
from benchmarks.grading.calibration_storage import pack_files
from benchmarks.grading.score import _evidence_bytes, score_run_dir
from tests.benchmarks import test_grading_score as runs
from tests.benchmarks.test_calibration_export_scan import FALLBACK, SELECTED, _fallback, _snapshot
from tests.benchmarks.test_grading_score import scripted_graded_run as scripted_graded_run


def _escaped(marker):
    return json.dumps({"reported_model": marker}).replace(marker[0], "\\u%04x" % ord(marker[0]))


@pytest.mark.parametrize("packed", [False, True])
@pytest.mark.parametrize("tuning", [False, True])
@pytest.mark.parametrize("selected", [False, True])
@pytest.mark.parametrize("member", ["original_files", "extra_history", "version", "tagged_binary"])
def test_retained_unknown_source_members_cannot_conceal_serialized_content(
    packed, tuning, selected, member,
):
    proof = {**(pack_files({}) if packed else {"version": 1, "files": {}}), "inputs": {}}
    concealed = _escaped("AIza" + "s" * 35)
    proof[member] = {"votes/unused.json": concealed} if member == "original_files" else concealed
    if member == "tagged_binary":
        proof[member] = "!<tag:yaml.org,2002:binary> " + base64.b64encode(
            ("AIza" + "s" * 35).encode()
        ).decode()
    entry = {"tuning": [{"source": proof}]} if tuning else {"source": proof}
    entries = {SELECTED if selected else FALLBACK: entry}
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps({"entries": entries}).encode())
    with pytest.raises(KeyLeakError):
        _evidence_bytes(Path("g1-entries.json"), entries, calibration=True)
    if member != "version":  # Invalid versions are rejected structurally by restore.
        with pytest.raises(KeyLeakError):
            CalibrationSource.restore(proof)


@pytest.mark.parametrize("marker", [
    "AIza" + "s" * 35, "sk-proj-" + "s" * 40, "sk-ant-api03-" + "s" * 80,
])
@pytest.mark.parametrize("wrapper", ["```json\n{}\n```", "provider returned: {}", "{} trailing"])
@pytest.mark.parametrize("provider", ["vote", "anthropic", "openai", "google"])
@pytest.mark.parametrize("packed", [False, True])
@pytest.mark.parametrize("tuning", [False, True])
def test_withheld_provider_replies_scan_recoverable_strings(
    marker, wrapper, provider, packed, tuning,
):
    text = wrapper.format(_escaped(marker))
    block = {"text": text}
    record = {
        "vote": {"status": "parse_failure", "text": text},
        "anthropic": {"raw": {"result": {"message": {"content": [block]}}}},
        "openai": {"raw": {"response": {"body": {"output": [{"content": [block]}]}}}},
        "google": {"raw": {"response": {"candidates": [{"content": {"parts": [block]}}]}}},
    }[provider]
    name = "votes/test.json" if provider == "vote" else "executions/run/test.json"
    files = {name: json.dumps(record)}
    proof = {**(pack_files(files) if packed else {"version": 1, "files": files}), "inputs": {}}
    entry = {"tuning": [{"source": proof}]} if tuning else {"source": proof}
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps({"entries": {FALLBACK: entry}}).encode())


@pytest.mark.parametrize("shape", ["original_files", "extra_history", "fenced_reply"])
def test_boundary_refusal_precedes_scoring_and_keeps_both_exports(
    scripted_graded_run, tmp_path, capsys, shape,
):
    root = scripted_graded_run
    path = runs._g1_file(root, tmp_path / "g1.json")
    score_run_dir(root, schema_path=runs.SCHEMA, g1_paths=[path])
    before = _snapshot(root)
    data = json.loads(path.read_bytes())
    row = _fallback()
    concealed = _escaped("AIza" + "s" * 35)
    if shape == "original_files":
        row["source"][shape] = {"votes/unused.json": concealed}
    elif shape == "extra_history":
        row["source"][shape] = concealed
    else:
        row["source"]["files"]["votes/unused.json"] = json.dumps({
            "status": "parse_failure", "text": "```json\n" + concealed + "\n```",
        })
    data["entries"][FALLBACK] = row
    path.write_text(json.dumps(data))
    body = path.read_bytes()
    with pytest.raises(KeyLeakError):
        score_run_dir(root, schema_path=runs.SCHEMA, g1_paths=[path])
    assert _snapshot(root) == before
    assert path.read_bytes() == body
    archived = root / "grading/disclosure/g1" / (hashlib.sha256(body).hexdigest() + ".json")
    assert not archived.exists()
    with pytest.raises(SystemExit) as caught:
        main(["agentic-score", "--run-dir", str(root), "--g1", str(path)])
    assert caught.value.code == 2
    output = capsys.readouterr()
    assert "AIza" + "s" * 35 not in output.out + output.err
    assert "unused.json" not in output.out + output.err
    assert _snapshot(root) == before


@pytest.mark.parametrize("packed", [False, True])
def test_source_human_notes_empty_containers_and_exact_files_survive(packed):
    files = {"votes/old.json": json.dumps({"text": 'Unicode café 雪; C:\\Users\\history; '
                                         'literal \\u0041; "quoted"'})}
    if not packed:
        files["votes/readme.txt"] = "{}"
    proof = {**(pack_files(files) if packed else {"version": 1, "files": files}), "inputs": {},
             "notes": 'Human history: café 雪; C:\\Users\\history; literal \\u0041; "quoted"!',
             "old": [], "missing": None}
    entries = {FALLBACK: {"source": proof}}
    assert g1_entries(json.dumps({"entries": entries}).encode()) == entries
    restored, _ = CalibrationSource.restore(copy.deepcopy(proof))
    assert dict(restored.files) == files


@pytest.mark.parametrize("packed", [False, True])
@pytest.mark.parametrize("slot", ["text", "output_text"])
@pytest.mark.parametrize("reply", [
    'prefix "\\u0041Iza' + "s" * 35 + '" suffix',
    'prefix "\\u0041Iza' + "s" * 35,  # Recover even an unfinished string.
    '```json\n{"note":"\\u0041Iza' + "s" * 35 + '"\n```',
    {"opaque": '{"note":"\\u0041Iza' + "s" * 35 + '"}'},
])
def test_recovered_reply_boundary_also_applies_to_producer_and_restore(
    tmp_path, monkeypatch, packed, slot, reply,
):
    from benchmarks.grading import calibration_history

    files = {"votes/old.json": json.dumps({slot: reply})}
    source = CalibrationSource(tmp_path, files)
    monkeypatch.setattr(calibration_history, "PACK_SOURCE_BYTES", 0 if packed else 10**9)
    with pytest.raises(KeyLeakError):
        source.export({})
    proof = {**(pack_files(files) if packed else {"version": 1, "files": files}), "inputs": {}}
    with pytest.raises(KeyLeakError):
        CalibrationSource.restore(proof)


def test_plain_frozen_source_input_cannot_bypass_producer_scan(tmp_path):
    source = CalibrationSource(tmp_path, {})
    with pytest.raises(KeyLeakError):
        source.export({"judge_prompt": ("AIza" + "s" * 35).encode()})


def test_provider_text_configuration_is_not_a_reply_string(tmp_path):
    # Responses retains its text.format configuration next to output blocks.
    # A legal configuration object must not be treated as opaque reply text.
    body = json.dumps({"result": {"raw": {"response": {"body": {
        "text": {"format": {"type": "json_schema", "name": "vote", "schema": {
            "type": "object", "properties": {"explanation": {"type": "string"}},
        }}},
        "output": [{"content": [{"type": "output_text", "text": '{"explanation":"ok"}'}]}],
    }}}}})
    proof = {"version": 1, "files": {"executions/run/test.json": body}, "inputs": {}}
    entries = {FALLBACK: {"source": proof}}
    assert g1_entries(json.dumps({"entries": entries}).encode()) == entries
    source, _ = CalibrationSource.restore(proof)
    assert source.export({}) == proof


@pytest.mark.parametrize("receipt", [False, True])
def test_shadowed_opaque_normalized_reply_is_not_discarded(receipt):
    text = _escaped("AIza" + "s" * 35)
    body = '{"text":{"opaque":' + json.dumps(text) + '},"text":"harmless"}'
    if receipt:
        body = '{"result":' + body + '}'
    name = "executions/run/test.json" if receipt else "votes/test.json"
    proof = {"version": 1, "inputs": {}, "files": {name: body}}
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps({"entries": {FALLBACK: {"source": proof}}}).encode())


@pytest.mark.parametrize("packed", [False, True])
@pytest.mark.parametrize("nested", ["object", "array", "member_name"])
def test_structured_raw_reply_cannot_conceal_an_encoded_string(packed, nested):
    concealed = _escaped("AIza" + "s" * 35)
    value = {"object": {"payload": concealed}, "array": [concealed],
             "member_name": {concealed: None}}[nested]
    body = {"output": [{"content": [{"type": "output_text", "text": value}]}]}
    record = {"result": {"raw": {"response": {"body": body}}}}
    files = {"executions/run/test.json": json.dumps(record)}
    proof = {**(pack_files(files) if packed else {"version": 1, "files": files}), "inputs": {}}
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps({"entries": {FALLBACK: {"source": proof}}}).encode())
