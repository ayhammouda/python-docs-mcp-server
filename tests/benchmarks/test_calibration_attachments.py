"""Explicit attachments share the retained metadata boundary, not reply parsing."""

import base64
import json
from pathlib import Path

import pytest

from benchmarks.agentic.protocol import KeyLeakError
from benchmarks.grading.calibration import g1_entries
from benchmarks.grading.calibration_history import CalibrationSource
from benchmarks.grading.calibration_storage import pack_files
from benchmarks.grading.score import _evidence_bytes
from tests.benchmarks.test_calibration_metadata_boundary import MARKER, archive, inject


def uri(text, *, percent=False, media="text/plain"):
    if percent:
        return f"data:{media};charset=utf-8," + "".join(f"%{b:02X}" for b in text.encode())
    return f"data:{media};base64," + base64.b64encode(text.encode()).decode()


@pytest.mark.parametrize("slot", ["outer", "entry", "tuning", "binding", "gate", "nested"])
@pytest.mark.parametrize("key", [False, True])
@pytest.mark.parametrize("percent", [False, True])
def test_attachment_in_any_retained_metadata_key_or_value_is_inspected(slot, key, percent):
    data = archive()
    inject(data, slot, uri(MARKER, percent=percent), key=key)
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps(data).encode())
    with pytest.raises(KeyLeakError):
        _evidence_bytes(Path("g1.json"), data, calibration=True)


@pytest.mark.parametrize("packed", [False, True])
@pytest.mark.parametrize("slot", [
    "source", "source-key", "nested", "file", "input", "literal-input", "legacy",
])
def test_attachment_inspection_reaches_all_source_layers(packed, slot):
    files = {}
    proof = {"version": 1, "inputs": {}, "files": files}
    text = uri(MARKER)
    if slot == "source":
        proof["support_attachment"] = text
    elif slot == "source-key":
        proof[text] = "ordinary note"
    elif slot == "nested":
        proof["extension"] = [{"notes": json.dumps({"attachment": text})}]
    elif slot == "file":
        files["runs/old.json"] = json.dumps({"notes": text})
    elif slot == "input":
        proof["inputs"]["panel"] = json.dumps({"notes": text})
    elif slot == "literal-input":
        proof["inputs"]["judge_prompt"] = text
    if packed:
        proof.update(pack_files(files))
    data = {"entries": {"other": {"source": text if slot == "legacy" else proof}}}
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps(data).encode())
    if slot != "legacy":
        with pytest.raises(KeyLeakError):
            CalibrationSource.restore(proof)
    if slot in ("file", "input", "literal-input"):
        with pytest.raises(KeyLeakError):
            CalibrationSource(Path("/unopened"), files).export(
                {k: v.encode() for k, v in proof["inputs"].items()},
            )


@pytest.mark.parametrize("kind", ["key", "value", "shadowed", "nested-attachment"])
@pytest.mark.parametrize("percent", [False, True])
def test_supported_json_attachments_inspect_recoverable_strings(kind, percent):
    escaped = "\\u0041" + MARKER[1:]
    text = {
        "key": '{"' + escaped + '":null}',
        "value": '{"note":"' + escaped + '"}',
        "shadowed": '{"note":"' + escaped + '","note":"ok"}',
        "nested-attachment": json.dumps({"file": uri(MARKER)}),
    }[kind]
    data = archive()
    inject(data, "entry", uri(text, percent=percent, media="application/json"))
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps(data).encode())


@pytest.mark.parametrize("text", [
    uri("harmless café 雪"), uri("harmless café 雪", percent=True),
    uri(json.dumps({"note": 'C:\\Users\\history; literal \\u0041; "quoted"'}),
        media="application/json"),
    "(data:text/plain;base64,aGVsbG8=)", "data:,hello%20world", "DATA:TEXT/PLAIN,hello",
    "Notes about data: URIs, https://example.invalid/ and literal base64, not attachments.",
    r"Schema pattern ^[A-Z][a-z]+\d+$; data:[^,]+,.* is a regex example.",
])
def test_safe_attachments_and_prose_preserve_exact_metadata(text):
    data = archive()
    inject(data, "entry", text)
    data["entries"]["unselected"]["source"]["support_attachment"] = text
    assert g1_entries(json.dumps(data).encode()) == data["entries"]
    assert json.loads(_evidence_bytes(Path("g1.json"), data, calibration=True)) == data


@pytest.mark.parametrize("text", [
    "data:application/octet-stream;base64,aGVsbG8=",
    "data:text/plain;base85,aGVsbG8=", "data:text/plain;charset=utf-16,hello",
    'data:text/plain;charset="utf-8";base64,aGVsbG8=',
    "data:text/plain;base64,!!!", "data:text/plain,%GG", "data:text/plain,%ff",
])
def test_unsupported_or_malformed_explicit_attachment_is_refused(text):
    data = archive()
    inject(data, "entry", text)
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps(data).encode())


def test_attachment_depth_is_bounded_even_without_a_marker():
    text = "safe"
    for _ in range(6):
        text = uri(text)
    data = archive()
    inject(data, "entry", text)
    with pytest.raises(KeyLeakError, match="depth"):
        g1_entries(json.dumps(data).encode())


def test_attachment_depth_allows_exactly_four_decoding_steps():
    from benchmarks.grading.calibration_scan import _metadata_text

    text = "safe"
    for _ in range(4):
        text = uri(text)
    _metadata_text(text)
    with pytest.raises(KeyLeakError, match="depth"):
        _metadata_text(uri(text))


def test_attachment_decoded_byte_budget_is_shared_with_quoted_layers(monkeypatch):
    from benchmarks.grading import calibration_scan

    text = json.dumps({"attachment": uri("x" * 50)})
    monkeypatch.setattr(calibration_scan, "MAX_FRAME_BYTES", len(text.encode()))
    # The original text fits; repeatedly inspecting overlapping quoted and URI
    # layers must still charge a single finite decoded-byte budget.
    data = archive()
    inject(data, "entry", text)
    with pytest.raises(KeyLeakError, match="size limit"):
        g1_entries(json.dumps(data).encode())


def test_metadata_size_limit_counts_utf8_bytes(monkeypatch):
    from benchmarks.grading import calibration_scan

    monkeypatch.setattr(calibration_scan, "MAX_FRAME_BYTES", 16)
    calibration_scan._metadata_text("é" * 8)
    with pytest.raises(KeyLeakError, match="size limit"):
        calibration_scan._metadata_text("é" * 9)


@pytest.mark.parametrize("packed", [False, True])
@pytest.mark.parametrize("raw_leak", [None, "value", "key"])
def test_responses_schema_regex_and_object_valued_raw_text(tmp_path, monkeypatch, packed, raw_leak):
    from benchmarks.grading import calibration_history

    raw_text = {"format": {"type": "json_schema", "name": "vote", "schema": {
        "type": "object", "properties": {
            "explanation": {"type": "string", "pattern": r"^[A-Z][a-z]+\d+$"},
        },
    }}}
    if raw_leak:
        concealed = '{"note":"\\u0041' + MARKER[1:] + '"}'
        raw_text[concealed if raw_leak == "key" else "extra"] = (
            None if raw_leak == "key" else concealed
        )
    record = {"result": {"raw": {"response": {"body": {
        "text": raw_text,
        "output": [{"content": [{"type": "output_text", "text": '{"explanation":"ok"}'}]}],
    }}}}}
    files = {"executions/run/format.json": json.dumps(record)}
    proof = {**(pack_files(files) if packed else {"version": 1, "files": files}), "inputs": {}}
    source = CalibrationSource(tmp_path, files)
    monkeypatch.setattr(calibration_history, "PACK_SOURCE_BYTES", 0 if packed else 10**9)
    body = json.dumps({"entries": {"other": {"source": proof}}}).encode()
    if raw_leak:
        for action in (lambda: source.export({}), lambda: CalibrationSource.restore(proof),
                       lambda: g1_entries(body)):
            with pytest.raises(KeyLeakError):
                action()
    else:
        assert source.export({}) == proof
        restored, _ = CalibrationSource.restore(proof)
        assert dict(restored.files) == files
        assert g1_entries(body)["other"]["source"] == proof
