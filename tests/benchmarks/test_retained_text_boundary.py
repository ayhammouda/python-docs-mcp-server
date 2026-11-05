"""Capture and archive replay share finite inspection, independent of prose quotes."""

import base64
import json
from urllib.request import DataHandler, Request

import pytest

from benchmarks.agentic.inputs import CapturedInput
from benchmarks.agentic.protocol import KeyLeakError
from benchmarks.grading.calibration import g1_entries
from benchmarks.grading.calibration_history import CalibrationSource
from benchmarks.grading.calibration_storage import pack_files
from benchmarks.grading.score import _write
from tests.benchmarks.test_captured_binary_json import binary_fragment

MARKER = "AIza" + "R" * 35
PAYLOAD = base64.b64encode(MARKER.encode()).decode()
ATTACHMENTS = [
    "data:" + header + "," + PAYLOAD for header in (
        "\v;base64", "text/plain\u2003;base64", "text;base64",
        "text/plain@x;base64", "text/(plain);base64", "text/[plain];base64",
        'text/"plain";base64', "text/\\plain;base64",
    )
] + [
    "data:text/plain;base64," + PAYLOAD[:4] + separator + PAYLOAD[4:]
    for separator in (" -", " .", " '", ' "', " (", "\u2003-", " \x00-")
] + ["data:x,%41" + MARKER[1:]]
OBJECT = json.dumps({"note": MARKER}).replace(MARKER, r"\u0041" + MARKER[1:])


@pytest.mark.parametrize("text", ATTACHMENTS)
@pytest.mark.parametrize("packed", [False, True])
def test_complete_attachments_refuse_before_any_export_write(tmp_path, text, packed):
    try:
        assert DataHandler().data_open(Request(text)).read() == MARKER.encode()
    except ValueError as error:
        # Patched CPython rejects header controls before decoding the payload.
        assert "\v" in text.partition(",")[0]
        assert str(error) == "Control characters not allowed in data: mediatype"
        assert base64.b64decode(text.partition(",")[2]) == MARKER.encode()
    files = {"votes/old.json": json.dumps({"text": text})}
    proof = {**(pack_files(files) if packed else {"version": 1, "files": files}), "inputs": {}}
    entries = {"other": {"source": proof, "notes": text}}
    wire = json.dumps({"entries": entries}).encode()
    for projection in (False, True):
        body = json.dumps(entries).encode() if projection else wire
        with pytest.raises(KeyLeakError) as caught:
            g1_entries(body, projection=projection)
        assert MARKER not in str(caught.value) and text not in str(caught.value)
        dest = tmp_path / ("g1-entries.json" if projection else "g1.json")
        dest.write_bytes(b"prior evidence")
        with pytest.raises(KeyLeakError):
            _write(tmp_path, dest, entries if projection else {"entries": entries},
                   calibration=True)
        assert dest.read_bytes() == b"prior evidence"
    with pytest.raises(KeyLeakError):
        CalibrationSource.restore(proof)
    with pytest.raises(KeyLeakError):
        CalibrationSource(tmp_path, files).export({})
    assert json.dumps({"entries": entries}).encode() == wire


@pytest.mark.parametrize("payload", [
    ('unmatched" ' + OBJECT).encode(),
    ('unmatched" ' + OBJECT.replace(r"\u0041", r"\x41")).encode(),
    (r"\u0041" + MARKER[1:]).encode(),
    *(text.encode() for text in ATTACHMENTS),
])
@pytest.mark.parametrize("tag", ["!!binary", "!<tag:yaml.org,2002:binary>"])
def test_binary_capture_and_both_archive_formats_refuse_the_same_text(payload, tag):
    body = binary_fragment(payload, "merge", tag)
    if payload.startswith(b'unmatched"') and b"\\u0041" in payload:
        # Extract an already complete JSON object; no quote/delimiter is added.
        assert json.loads(payload[payload.index(b"{"):])["note"] == MARKER
    with pytest.raises(KeyLeakError):
        CapturedInput.from_bytes(body, "panel", "panel")
    for packed in (False, True):
        proof = {**(pack_files({}) if packed else {"version": 1, "files": {}}),
                 "inputs": {"panel": body.decode()}}
        entries = {"other": {"source": proof}}
        for projection in (False, True):
            with pytest.raises(KeyLeakError):
                g1_entries(json.dumps(entries if projection else {"entries": entries}).encode(),
                           projection=projection)


@pytest.mark.parametrize("text", [
    r"Schema data:[^,]+,.* is a regex example.",
    "Notes about data: URIs, without an attachment.",
    "data:x,%68ello", "data:text/plain@x,%68ello", "data:\u2003,%68ello",
    "data:text/[plain],%68ello",
])
def test_percent_text_is_inspected_without_interpreting_its_media_label(text):
    entries = {"other": {"notes": text}}
    assert g1_entries(json.dumps({"entries": entries}).encode()) == entries
    # The exact same unsupported header cannot hide an encoded marker.
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps({"entries": {"other": {
            "notes": text[:text.index(",") + 1] + "%41" + MARKER[1:],
        }}}).encode())


def test_regex_and_percent_notes_inspect_complete_interpretations():
    regex, percent = "data:[^,]+,.*", "data:x,%68ello"
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps({"entries": {"other": {
            "notes": regex + " data:x,%41" + MARKER[1:],
        }}}).encode())
    assert MARKER.encode() in DataHandler().data_open(Request(
        regex + " data:x,%41" + MARKER[1:],
    )).read()
    for notes in (regex + " " + percent, [regex, percent], '"' + regex + '" and "' + percent + '"'):
        entries = {"other": {"notes": notes}}
        assert g1_entries(json.dumps({"entries": entries}).encode()) == entries


@pytest.mark.parametrize("text", [
    'log.info("data: %s, %s", first, second)',
    'Inspect data: count, 20% remaining.',
    'Schema data:[^,]+,.* and data:x,%68ello',
])
@pytest.mark.parametrize("packed", [False, True])
def test_logging_placeholders_and_percent_prose_keep_exact_content(tmp_path, text, packed):
    files = {"items/old.json": json.dumps({"user": text})}
    binary = binary_fragment(text.encode(), "merge", "!!binary")
    assert CapturedInput.from_bytes(binary, "panel", "panel").content == binary
    proof = {**(pack_files(files) if packed else {"version": 1, "files": files}),
             "inputs": {"judge_prompt": text, "panel": binary.decode()}}
    entries = {"other": {"source": proof, "notes": text}}
    assert g1_entries(json.dumps({"entries": entries}).encode()) == entries
    restored, inputs = CalibrationSource.restore(proof)
    assert dict(restored.files) == files
    assert CalibrationSource(tmp_path, files).export(inputs)["inputs"] == proof["inputs"]
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps({"entries": {"other": {
            "notes": text + " data:x,%41" + MARKER[1:],
        }}}).encode())
