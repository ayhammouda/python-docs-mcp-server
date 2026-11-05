"""Complete declared attachments cannot escape through alternate header grammar."""
import base64
import json
from urllib.request import DataHandler, Request

import pytest

from benchmarks.agentic.protocol import KeyLeakError
from benchmarks.grading.calibration import g1_entries

MARKER = "AIza" + "T" * 35
ENCODED = base64.b64encode(MARKER.encode()).decode()

@pytest.mark.parametrize("header", [
    "\v;base64", "text/plain\v;base64", "text/plain\u00a0;base64",
    "text/plain\u2003;base64", "text;base64", "text/plain@x;base64",
    "/;base64",
])
def test_complete_encoded_attachment_headers_are_inspected(header):
    note = "data:" + header + "," + ENCODED
    assert DataHandler().data_open(Request(note)).read() == MARKER.encode()
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps({"entries": {"other": {"notes": note}}}).encode())

@pytest.mark.parametrize("separator", [" -", " .", " '", ' "', " ("])
def test_base64_continuations_cannot_be_hidden_by_non_alphabet_bytes(separator):
    note = "data:text/plain;base64," + ENCODED[:4] + separator + ENCODED[4:]
    assert DataHandler().data_open(Request(note)).read() == MARKER.encode()
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps({"entries": {"other": {"notes": note}}}).encode())

def test_complete_percent_attachment_with_unsupported_header_is_inspected():
    note = "data:x,%41" + MARKER[1:]
    assert DataHandler().data_open(Request(note)).read() == MARKER.encode()
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps({"entries": {"other": {"notes": note}}}).encode())
