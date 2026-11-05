"""Binary input capture must inspect recoverable JSON before physical publication."""

import base64
import hashlib
import json

import pytest
import yaml
from yaml.constructor import SafeConstructor

from benchmarks import text_inspection
from benchmarks.agentic.inputs import CapturedInput, json_mapping
from benchmarks.agentic.protocol import KeyLeakError
from benchmarks.grading import inputs as grading_inputs
from benchmarks.grading.panel import PANEL_PATH

MARKER = "AIza" + "S" * 35  # Synthetic; never supplied by the environment.
ESCAPED = r"\u0041" + MARKER[1:]


def concealed(kind):
    encoded = '"' + ESCAPED + '"'
    ordinary = '{"note":' + encoded + '}'
    return {
        "object": ordinary.encode(),
        "key": ('{' + encoded + ':null}').encode(),
        "duplicate": ('{"note":' + encoded + ',"note":"safe"}').encode(),
        "nested": json.dumps({"note": ordinary}).encode(),
        "nested-duplicate": json.dumps('{"note":' + encoded + ',"note":"safe"}').encode(),
        "incomplete": ('old reply: {"note":' + encoded[:-1]).encode(),
        "incomplete-nested": json.dumps('old reply: {"note":' + encoded[:-1]).encode(),
        "prose": (r'Use "C:\Users" and "\d+"; old reply: ' + ordinary).encode(),
        "broken-prefix": ('{"note":"literal\\d+ ' + ESCAPED + '"}').encode(),
        "broken-suffix": ('{"note":"' + ESCAPED + '\\u00').encode(),
        "non-utf8": b"\xff" + ordinary.encode() + b"\x80",
        "stray-quote": ('prefix" ' + ordinary).encode(),
        "bare-escape": ESCAPED.encode(),
        "hex-prose": ('prefix" ' + ordinary.replace(r'\u0041', r'\x41')).encode(),
    }[kind]


def binary_fragment(payload: bytes, syntax: str, tag: str) -> bytes:
    scalar = tag + " " + base64.b64encode(payload).decode("ascii")
    return {
        "visible": f"comment: {scalar}\n",
        "duplicate": f"comment: {scalar}\ncomment: safe\n",
        "merge": f"<<: {{comment: {scalar}}}\ncomment: safe\n",
        "merge-list": f"<<: [{{comment: safe}}, {{comment: {scalar}}}]\n",
        "alias": f"comment: &hidden {scalar}\ncomment: safe\ncopy: *hidden\ncopy: safe\n",
    }[syntax].encode()


def assert_snapshot_files(dest, manifest, panel_body):
    expected = {
        "criteria": grading_inputs.CRITERIA_PATH.read_bytes(),
        "judge_prompt": grading_inputs.JUDGE_PROMPT_PATH.read_bytes(),
        "judge_schema": grading_inputs.JUDGE_SCHEMA_PATH.read_bytes(),
        "key_audit_prompt": grading_inputs.KEY_AUDIT_PROMPT_PATH.read_bytes(),
        "probe_spec": grading_inputs.PROBE_SPEC_PATH.read_bytes(),
        "panel": panel_body,
        "mapping_table": grading_inputs.MAPPING_TABLE.encode(),
        "mask_rules": grading_inputs.MASK_RULES.encode(),
    }
    assert {path.name for path in dest.iterdir()} == {
        *(grading_inputs.SNAPSHOT_FILES[name] for name in expected), "inputs.json",
    }
    assert len(list(dest.iterdir())) == 9
    assert manifest == {
        name: hashlib.sha256(body).hexdigest() for name, body in expected.items()
    }
    assert (dest / "inputs.json").read_bytes() == (
        json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n"
    )
    for name, body in expected.items():
        assert (dest / grading_inputs.SNAPSHOT_FILES[name]).read_bytes() == body


@pytest.mark.parametrize("tag", ["!!binary", "!<tag:yaml.org,2002:binary>"])
@pytest.mark.parametrize("syntax", ["visible", "duplicate", "merge", "merge-list", "alias"])
@pytest.mark.parametrize("kind", [
    "object", "key", "duplicate", "nested", "nested-duplicate", "incomplete",
    "incomplete-nested", "prose", "broken-prefix", "broken-suffix", "non-utf8",
    "stray-quote", "bare-escape", "hex-prose",
])
def test_snapshot_rejects_binary_json_before_physical_writes(
    tmp_path, monkeypatch, syntax, tag, kind,
):
    payload = concealed(kind)
    if kind == "object":
        assert json.loads(payload)["note"] == MARKER
    body = PANEL_PATH.read_bytes() + b"\n" + binary_fragment(payload, syntax, tag)
    panel = tmp_path / "panel.yml"
    panel.write_bytes(body)
    dest = tmp_path / "snapshot"
    flattened = []
    original = SafeConstructor.flatten_mapping

    def observe(loader, node):
        flattened.append(node)
        return original(loader, node)

    monkeypatch.setattr(SafeConstructor, "flatten_mapping", observe)
    try:
        manifest = grading_inputs.snapshot_inputs(
            dest, criteria_path=grading_inputs.CRITERIA_PATH, panel_path=panel, probes_path=None,
        )
    except KeyLeakError as caught:
        message = str(caught)
        assert all(value not in message for value in (
            MARKER, ESCAPED, payload.decode(errors="replace"), base64.b64encode(payload).decode(),
        ))
        assert all(not any(key.value == "judges" for key, _ in node.value) for node in flattened)
        assert not dest.exists()
        assert panel.read_bytes() == body
    else:
        # RED evidence must reach the real filesystem writer and verify every
        # frozen byte/hash, rather than infer persistence from an in-memory sink.
        assert_snapshot_files(dest, manifest, body)
        pytest.fail("capture physically wrote all nine exact frozen files containing binary JSON")


@pytest.mark.parametrize("tag", ["!!binary", "!<tag:yaml.org,2002:binary>"])
@pytest.mark.parametrize("syntax", ["visible", "duplicate", "merge", "merge-list", "alias"])
@pytest.mark.parametrize("payload", [
    b"ordinary binary note",
    b"\xff\x80\x00\xfe harmless binary",
    rb'Use "C:\Users\example" and "\d+" or "\w{2,4}"; trailing ' + b"\\",
    rb'Incomplete quoted path "C:\Users\example' + b"\\",
    rb'Use "\\d+" and "\\\\d+" and "\\u0041" literally',
    json.dumps({"path": r"C:\Users\example", "regex": r"\d+"}).encode(),
    json.dumps(json.dumps({"path": r"C:\Users\example", "regex": r"\d+"})).encode(),
    b'\xff "C:\\Users" "\\d+" \x80',
])
def test_benign_binary_text_keeps_every_physical_snapshot_byte(tmp_path, payload, syntax, tag):
    body = PANEL_PATH.read_bytes() + b"\n" + binary_fragment(payload, syntax, tag)
    panel = tmp_path / "panel.yml"
    panel.write_bytes(body)
    captured = CapturedInput.read(panel, "panel", "panel")
    assert captured.mapping == yaml.safe_load(body)
    assert captured.content == body
    dest = tmp_path / "snapshot"
    manifest = grading_inputs.snapshot_inputs(
        dest, criteria_path=grading_inputs.CRITERIA_PATH, panel_path=panel, probes_path=None,
    )
    assert_snapshot_files(dest, manifest, body)


@pytest.mark.parametrize("name", [
    "corpus.yml", "model-matrix.yml", "competitor-manifest.yml", "price-snapshot.yml",
    "reference model-matrix.yml", "corpus", "panel", "criteria", "probes", "tuning",
    "grading input",
])
def test_every_yaml_capture_label_inspects_encoded_binary_keys(name):
    scalar = "!!binary " + base64.b64encode(concealed("nested")).decode()
    with pytest.raises(KeyLeakError):
        CapturedInput.from_bytes(f"{scalar}: old\n{scalar}: safe\n".encode(), name, "input")


def test_rejection_preserves_existing_snapshot_files(tmp_path):
    dest = tmp_path / "snapshot"
    grading_inputs.snapshot_inputs(
        dest, criteria_path=grading_inputs.CRITERIA_PATH, panel_path=PANEL_PATH, probes_path=None,
    )
    before = {path.name: path.read_bytes() for path in dest.iterdir()}
    panel = tmp_path / "panel.yml"
    panel.write_bytes(PANEL_PATH.read_bytes() + binary_fragment(
        concealed("nested-duplicate"), "merge", "!!binary",
    ))
    with pytest.raises(KeyLeakError):
        grading_inputs.snapshot_inputs(
            dest, criteria_path=grading_inputs.CRITERIA_PATH, panel_path=panel, probes_path=None,
        )
    assert {path.name: path.read_bytes() for path in dest.iterdir()} == before


@pytest.mark.parametrize("payload,limit", [
    (b'"' + b"x" * 63 + b'"', 64),  # Original text exceeds the frame bound.
    ('"éééé"'.encode(), 8),  # Bound bytes, not just code points.
    (json.dumps("x" * 40 + r"\u0041").encode(), 64),  # Two finite decoding steps.
])
def test_binary_json_inspection_has_bounded_bytes(monkeypatch, payload, limit):
    monkeypatch.setattr(text_inspection, "MAX_FRAME_BYTES", limit)
    with pytest.raises(KeyLeakError, match="size limit"):
        CapturedInput.from_bytes(
            binary_fragment(payload, "duplicate", "!!binary"), "panel", "panel",
        )


def test_binary_json_inspection_has_bounded_decoding_depth():
    # Four character decoding steps, independent of surrounding quote parity.
    text = r"\u0041"
    for _ in range(3):
        text = json.dumps(text)
    body = binary_fragment(text.encode(), "alias", "!!binary")
    assert CapturedInput.from_bytes(body, "panel", "panel").mapping == yaml.safe_load(body)
    with pytest.raises(KeyLeakError, match="decoding depth"):
        CapturedInput.from_bytes(
            binary_fragment(json.dumps(text).encode(), "alias", "!!binary"), "panel", "panel",
        )


@pytest.mark.parametrize("content", [rb'{"path":"C:\Users"}', rb'{"regex":"\d+"}'])
def test_embedded_binary_prose_does_not_relax_required_json_validity(content):
    # Actual JSON input boundaries keep their native syntax requirement.
    with pytest.raises(ValueError):
        json.loads(content)
    with pytest.raises(KeyLeakError, match="uninspectable JSON string"):
        json_mapping(content, "required JSON input")
