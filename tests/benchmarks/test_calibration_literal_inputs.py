"""Literal frozen inputs retain their syntax while exposing escaped credentials."""

import json
from pathlib import Path

import pytest

from benchmarks.agentic.protocol import KeyLeakError
from benchmarks.grading import calibration_scan
from benchmarks.grading.calibration import g1_entries
from benchmarks.grading.calibration_history import CalibrationSource
from benchmarks.grading.calibration_storage import pack_files
from benchmarks.grading.score import _evidence_bytes
from tests.benchmarks.test_calibration_metadata_boundary import MARKER, concealed
from tests.benchmarks.test_calibration_typed_literals import PATTERN


@pytest.mark.parametrize("slot", ["judge_prompt", "mask_rules", "mapping_table"])
@pytest.mark.parametrize("layers", [0, 1, 2, 3, 4])
@pytest.mark.parametrize("packed", [False, True])
def test_literal_source_inputs_cannot_hide_quoted_credentials(tmp_path, slot, layers, packed):
    text = concealed("value")
    for _ in range(layers):
        text = json.dumps(text)
    proof = {
        **(pack_files({}) if packed else {"version": 1, "files": {}}),
        "inputs": {slot: "Recorded example\n" + text},
    }
    entries = {"other": {"source": proof}}
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps({"entries": entries}).encode())
    with pytest.raises(KeyLeakError):
        _evidence_bytes(Path("g1-entries.json"), entries, calibration=True)
    with pytest.raises(KeyLeakError):
        CalibrationSource.restore(proof)
    with pytest.raises(KeyLeakError):
        CalibrationSource(tmp_path, {}).export({slot: proof["inputs"][slot].encode()})


@pytest.mark.parametrize("slot", ["judge_prompt", "mask_rules", "mapping_table"])
def test_literal_input_quotes_and_escape_examples_are_preserved(tmp_path, slot):
    text = PATTERN + '\nWindows C:\\Users\\history, literal \\u0041 and "quoted"'
    inputs = {slot: text.encode()}
    source = CalibrationSource(tmp_path, {})
    proof = source.export(inputs)
    restored, supplied = CalibrationSource.restore(proof)
    assert supplied == inputs
    assert restored.export(inputs) == proof
    assert MARKER not in text


def test_literal_character_decoding_shares_the_existing_byte_budget(monkeypatch):
    monkeypatch.setattr(calibration_scan, "MAX_FRAME_BYTES", 64)
    proof = {"version": 1, "files": {}, "inputs": {"judge_prompt": "A" * 30 + r"\\u0041"}}
    with pytest.raises(KeyLeakError, match="size limit"):
        g1_entries(json.dumps({"entries": {"other": {"source": proof}}}).encode())
    proof["inputs"]["judge_prompt"] = "A" * 16 + r"\u0041"
    assert g1_entries(json.dumps({"entries": {"other": {"source": proof}}}).encode())


def test_escaped_newline_cannot_hide_a_literal_credential():
    proof = {
        "version": 1, "files": {},
        "inputs": {"judge_prompt": json.dumps({"note": "\n" + MARKER})},
    }
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps({"entries": {"other": {"source": proof}}}).encode())
