"""Every frozen input format must reject recoverable credentials before writes."""
import base64
import hashlib
import json

import pytest
import yaml

from benchmarks import text_inspection
from benchmarks.agentic import inputs as captured_inputs
from benchmarks.agentic.inputs import inspect_input_bytes, json_mapping
from benchmarks.agentic.protocol import KeyLeakError
from benchmarks.grading import inputs
from benchmarks.grading.calibration import g1_entries
from benchmarks.grading.calibration_history import CalibrationSource
from benchmarks.grading.calibration_storage import pack_files
from benchmarks.grading.panel import PANEL_PATH
from benchmarks.grading.score import _frozen_inputs, score_run_dir
from benchmarks.report import generate_readme_summary, generate_report
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks import test_agentic_report as reports
from tests.benchmarks.test_agentic_report import validated_bundle as validated_bundle
from tests.benchmarks.test_agentic_report import validated_template as validated_template
from tests.benchmarks.test_calibration_export_scan import _snapshot
from tests.benchmarks.test_captured_binary_json import assert_snapshot_files

MARKER = "AIza" + "U" * 35
ENCODED = json.dumps({"note": MARKER}, separators=(",", ":")).replace(
    MARKER, r"\u0041" + MARKER[1:],
)

@pytest.mark.parametrize("kind", [
    "yaml-block", "yaml-single", "yaml-plain", "yaml-key", "yaml-uri",
    "json-value", "json-key", "literal-prompt",
])
def test_frozen_input_capture_inspects_all_declared_text(tmp_path, monkeypatch, kind):
    panel = PANEL_PATH
    if kind.startswith("yaml"):
        value = ENCODED
        if kind == "yaml-block":
            extra = "comment: |\n  " + value + "\n"
        elif kind == "yaml-single":
            extra = "comment: '" + value + "'\n"
        elif kind == "yaml-key":
            extra = "'" + value + "': safe\n"
        elif kind == "yaml-uri":
            value = "data:text/plain;base64," + base64.b64encode(MARKER.encode()).decode()
            extra = "comment: " + value + "\n"
        else:
            extra = "comment: prefix " + value + "\n"
        original = PANEL_PATH.read_bytes() + b"\n" + extra.encode()
        panel = tmp_path / "panel.yml"
        panel.write_bytes(original)
        parsed = yaml.safe_load(original)
        if kind not in ("yaml-key", "yaml-uri"):
            retained = parsed["comment"]
            assert json.loads(retained[retained.index("{"):])["note"] == MARKER
        path = panel
    elif kind.startswith("json"):
        data = json.loads(inputs.JUDGE_SCHEMA_PATH.read_bytes())
        if kind == "json-value":
            data["$comment"] = ENCODED
        else:
            data.setdefault("$defs", {})[ENCODED] = {}
        original = json.dumps(data).encode()
        path = tmp_path / "schema.json"
        path.write_bytes(original)
        monkeypatch.setattr(inputs, "JUDGE_SCHEMA_PATH", path)
    else:
        original = inputs.JUDGE_PROMPT_PATH.read_bytes() + b"\n" + ENCODED.encode()
        path = tmp_path / "prompt.md"
        path.write_bytes(original)
        monkeypatch.setattr(inputs, "JUDGE_PROMPT_PATH", path)

    destination = tmp_path / "snapshot"
    try:
        manifest = inputs.snapshot_inputs(
            destination, criteria_path=inputs.CRITERIA_PATH, panel_path=panel, probes_path=None,
        )
    except KeyLeakError as error:
        assert MARKER not in str(error)
        assert not destination.exists()
        assert path.read_bytes() == original
    else:
        assert len(list(destination.iterdir())) == 9
        for name, digest in manifest.items():
            body = (destination / inputs.SNAPSHOT_FILES[name]).read_bytes()
            assert hashlib.sha256(body).hexdigest() == digest
        copied = destination / ("panel.yml" if kind.startswith("yaml") else
                               inputs.SNAPSHOT_FILES["judge_schema" if kind.startswith("json")
                                                     else "judge_prompt"])
        assert copied.read_bytes() == original
        pytest.fail(
            "normal capture physically wrote nine hash-bound files retaining the encoded marker"
        )


@pytest.mark.parametrize("fragment", [
    "comment: '" + ENCODED + "'\ncomment: safe\n",
    "<<: {comment: '" + ENCODED + "'}\ncomment: safe\n",
    "<<: [{comment: safe}, {comment: '" + ENCODED + "'}]\n",
    "comment: &hidden '" + ENCODED + "'\ncomment: safe\ncopy: *hidden\ncopy: safe\n",
    "'" + ENCODED + "': old\n'" + ENCODED + "': safe\n",
])
def test_discarded_yaml_text_refuses_before_replacing_snapshots(tmp_path, fragment):
    dest = tmp_path / "snapshot"
    manifest = inputs.snapshot_inputs(
        dest, criteria_path=inputs.CRITERIA_PATH, panel_path=PANEL_PATH, probes_path=None,
    )
    assert_snapshot_files(dest, manifest, PANEL_PATH.read_bytes())
    before = {p.name: p.read_bytes() for p in dest.iterdir()}
    panel = tmp_path / "panel.yml"
    body = PANEL_PATH.read_bytes() + b"\n" + fragment.encode()
    panel.write_bytes(body)
    with pytest.raises(KeyLeakError):
        inputs.snapshot_inputs(
            dest, criteria_path=inputs.CRITERIA_PATH, panel_path=panel, probes_path=None,
        )
    assert {p.name: p.read_bytes() for p in dest.iterdir()} == before
    assert panel.read_bytes() == body


@pytest.mark.parametrize("member", [
    '"$comment":' + json.dumps(ENCODED) + ',"$comment":"safe"',
    '"$defs":{' + json.dumps(ENCODED) + ':0,' + json.dumps(ENCODED) + ':1}',
    '"$comment":[' + json.dumps(ENCODED) + '],"$comment":"safe"',
])
def test_discarded_json_strings_and_keys_refuse_before_writes(tmp_path, monkeypatch, member):
    body = inputs.JUDGE_SCHEMA_PATH.read_bytes().rstrip()[:-1] + b"," + member.encode() + b"}"
    json.loads(body)  # The owning format is valid even though construction loses text.
    schema = tmp_path / "schema.json"
    schema.write_bytes(body)
    monkeypatch.setattr(inputs, "JUDGE_SCHEMA_PATH", schema)
    dest = tmp_path / "snapshot"
    with pytest.raises(KeyLeakError):
        inputs.snapshot_inputs(
            dest, criteria_path=inputs.CRITERIA_PATH, panel_path=PANEL_PATH, probes_path=None,
        )
    assert not dest.exists() and schema.read_bytes() == body


@pytest.mark.parametrize("slot", ["MAPPING_TABLE", "MASK_RULES"])
@pytest.mark.parametrize("entry", ["snapshot", "manifest"])
def test_programmatic_frozen_slots_cross_the_capture_boundary(tmp_path, monkeypatch, slot, entry):
    monkeypatch.setattr(inputs, slot, getattr(inputs, slot) + "\n" + ENCODED)
    kwargs = dict(criteria_path=inputs.CRITERIA_PATH, panel_path=PANEL_PATH, probes_path=None)
    with pytest.raises(KeyLeakError):
        if entry == "snapshot":
            inputs.snapshot_inputs(tmp_path / "snapshot", **kwargs)
        else:
            inputs.inputs_manifest(**kwargs)
    assert not (tmp_path / "snapshot").exists()


@pytest.mark.parametrize("slot", ["KEY_AUDIT_PROMPT_PATH", "PROBE_SPEC_PATH"])
def test_other_literal_input_files_cross_the_capture_boundary(tmp_path, monkeypatch, slot):
    path = tmp_path / "literal.md"
    body = getattr(inputs, slot).read_bytes() + b"\n" + ENCODED.encode()
    path.write_bytes(body)
    monkeypatch.setattr(inputs, slot, path)
    with pytest.raises(KeyLeakError):
        inputs.snapshot_inputs(
            tmp_path / "snapshot", criteria_path=inputs.CRITERIA_PATH,
            panel_path=PANEL_PATH, probes_path=None,
        )
    assert not (tmp_path / "snapshot").exists() and path.read_bytes() == body


@pytest.mark.parametrize("packed", [False, True])
@pytest.mark.parametrize("slot,text", [
    ("panel", "comment: '" + ENCODED + "'\ncomment: safe\n"),
    ("judge_schema", '{"$comment":' + json.dumps(ENCODED) + ',"$comment":"safe"}'),
    ("judge_prompt", "literal prompt\n" + ENCODED),
    ("mapping_table", "literal mapping\n" + ENCODED),
    ("mask_rules", "literal regex\n" + ENCODED),
])
def test_capture_readers_export_and_both_source_versions_refuse(tmp_path, packed, slot, text):
    dest = tmp_path / "snapshot"
    manifest = inputs.snapshot_inputs(
        dest, criteria_path=inputs.CRITERIA_PATH, panel_path=PANEL_PATH, probes_path=None,
    )
    assert_snapshot_files(dest, manifest, PANEL_PATH.read_bytes())
    path = dest / inputs.SNAPSHOT_FILES[slot]
    body = text.encode()
    path.write_bytes(body)
    manifest[slot] = hashlib.sha256(body).hexdigest()
    (dest / "inputs.json").write_text(json.dumps(manifest))
    before = _snapshot(tmp_path)
    with pytest.raises(KeyLeakError):
        _frozen_inputs(dest, criteria_path=None, panel_path=None)
    assert _snapshot(tmp_path) == before
    files = {"votes/old.json": '{"note":"harmless"}'}
    proof = {**(pack_files(files) if packed else {"version": 1, "files": files}),
             "inputs": {slot: text}}
    for projection in (False, True):
        entries = {"other": {"source": proof}}
        with pytest.raises(KeyLeakError):
            g1_entries(json.dumps(entries if projection else {"entries": entries}).encode(),
                       projection=projection)
    with pytest.raises(KeyLeakError):
        CalibrationSource.restore(proof)
    with pytest.raises(KeyLeakError):
        CalibrationSource(tmp_path, files).export({slot: body})
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("packed", [False, True])
def test_harmless_capture_keeps_nine_files_and_both_source_versions(tmp_path, monkeypatch, packed):
    # These are typed source bytes, not anonymous YAML encoding declarations.
    prose = ('Use "C:\\Users\\example" and "\\d+". '
             'log.info("data: %s, %s", first, second); 20% remaining. '
             'Schema data:[^,]+,.* and data:x,%68ello')
    binary = "!!binary " + base64.b64encode(prose.encode()).decode()
    body = PANEL_PATH.read_bytes() + (
        "\n# original comment\ncomment: '" + prose + "'\ncomment: safe\n"
        "<<: {note: &note '" + prose + "'}\nnote: safe\ncopy: *note\n"
        "binary: " + binary + "\n"
    ).encode()
    panel = tmp_path / "panel.yml"
    panel.write_bytes(body)
    prompt = tmp_path / "prompt.md"
    prompt.write_bytes(inputs.JUDGE_PROMPT_PATH.read_bytes() + b"\n" + prose.encode())
    monkeypatch.setattr(inputs, "JUDGE_PROMPT_PATH", prompt)
    schema = tmp_path / "schema.json"
    schema.write_bytes(inputs.JUDGE_SCHEMA_PATH.read_bytes().rstrip()[:-1]
                       + b',"$comment":' + json.dumps(prose).encode()
                       + b',"$comment":"safe"}')
    monkeypatch.setattr(inputs, "JUDGE_SCHEMA_PATH", schema)
    dest = tmp_path / "snapshot"
    manifest = inputs.snapshot_inputs(
        dest, criteria_path=inputs.CRITERIA_PATH, panel_path=panel, probes_path=None,
    )
    assert_snapshot_files(dest, manifest, body)
    frozen = {
        name: (dest / filename).read_bytes() for name, filename in inputs.SNAPSHOT_FILES.items()
        if name in manifest
    }
    assert yaml.safe_load(frozen["panel"])["binary"] == prose.encode()
    source = CalibrationSource(tmp_path, {})
    proof = {**(pack_files({}) if packed else {"version": 1, "files": {}}),
             "inputs": {name: value.decode() for name, value in frozen.items()}}
    restored, replayed = CalibrationSource.restore(proof)
    assert replayed == frozen
    assert source.export(frozen)["inputs"] == proof["inputs"]
    assert restored.export(replayed)["inputs"] == proof["inputs"]
    entries = {"other": {"source": proof}}
    assert g1_entries(json.dumps({"entries": entries}).encode()) == entries
    assert g1_entries(json.dumps(entries).encode(), projection=True) == entries


def test_score_and_both_physical_writers_refuse_frozen_text_with_prior_bytes_intact(
    validated_bundle,
):
    run = validated_bundle
    # Establish actual prior physical outputs before testing refusal.
    generate_report(run, model_matrix_path=reports.MATRIX, methodology_path=reports.METHODOLOGY)
    generate_readme_summary(run, methodology_path=reports.METHODOLOGY)
    clean = _snapshot(run)
    frozen = run / "grading/inputs"
    for slot in ("panel", "judge_schema", "judge_prompt", "mapping_table", "mask_rules"):
        path = frozen / inputs.SNAPSHOT_FILES[slot]
        original = path.read_bytes()
        if slot == "panel":
            body = original + ("\ncomment: '" + ENCODED + "'\ncomment: safe\n").encode()
        elif slot == "judge_schema":
            body = original.rstrip()[:-1] + b',"$comment":' + json.dumps(ENCODED).encode() + b"}"
        else:
            body = original + b"\n" + ENCODED.encode()
        path.write_bytes(body)
        manifest_path = frozen / "inputs.json"
        manifest_body = manifest_path.read_bytes()
        manifest = json.loads(manifest_body)
        manifest[slot] = hashlib.sha256(body).hexdigest()
        manifest_path.write_text(json.dumps(manifest))
        before = _snapshot(run)
        with pytest.raises(KeyLeakError):
            score_run_dir(run, schema_path=reports.SCHEMA)
        assert _snapshot(run) == before
        with pytest.raises(KeyLeakError):
            generate_report(run, model_matrix_path=reports.MATRIX,
                            methodology_path=reports.METHODOLOGY)
        assert _snapshot(run) == before
        with pytest.raises(KeyLeakError):
            generate_readme_summary(run, methodology_path=reports.METHODOLOGY)
        assert _snapshot(run) == before
        path.write_bytes(original)
        manifest_path.write_bytes(manifest_body)
        assert _snapshot(run) == clean


def _declared_bytes(text, kind):
    if kind == "yaml":
        return yaml.safe_dump({"n": text}).encode(), ".yml"
    if kind == "json-value":
        return json.dumps({"n": text}).encode(), ".json"
    if kind == "json-key":
        return json.dumps({text: None}).encode(), ".json"
    return text.encode(), ".txt"


@pytest.mark.parametrize("kind", ["yaml", "json-value", "json-key", "literal"])
@pytest.mark.parametrize("text,limit", [
    ("x" * 65, 64),
    ("é" * 5, 8),
    (json.dumps("x" * 40 + r"\u0041"), 64),
])
def test_each_retained_input_string_keeps_original_and_decoded_bounds(
    monkeypatch, kind, text, limit,
):
    monkeypatch.setattr(text_inspection, "MAX_FRAME_BYTES", limit)
    body, suffix = _declared_bytes(text, kind)
    with pytest.raises(KeyLeakError, match="size limit"):
        inspect_input_bytes(body, suffix=suffix, label="frozen input")


@pytest.mark.parametrize("kind", ["yaml", "json-value", "json-key", "literal"])
def test_each_input_format_keeps_four_combined_decoding_steps(kind):
    text = r"\u0041"
    for _ in range(3):
        text = json.dumps(text)
    body, suffix = _declared_bytes(text, kind)
    assert inspect_input_bytes(body, suffix=suffix, label="frozen input") == body
    body, suffix = _declared_bytes(json.dumps(text), kind)
    with pytest.raises(KeyLeakError, match="decoding depth"):
        inspect_input_bytes(body, suffix=suffix, label="frozen input")


def test_generic_json_preserves_verified_packed_transport_budget(monkeypatch):
    # Scale the text budget only: packed transport has a separate verified
    # allowance and must not acquire a retained-string ceiling in json_mapping.
    monkeypatch.setattr(text_inspection, "MAX_FRAME_BYTES", 64)
    text = json.dumps({"n": "a" * 40})
    proof = {**pack_files({"votes/old.json": text}), "inputs": {}}
    blob = next(iter(proof["blobs"].values()))["bz2"]
    assert len(blob) > 64
    body = json.dumps({"entries": {"other": {"source": proof}}}).encode()
    assert json_mapping(body, "calibration transport")["entries"]["other"]["source"] == proof
    assert g1_entries(body)["other"]["source"] == proof
    # A name alone never exempts a declared frozen JSON string from inspection.
    with pytest.raises(KeyLeakError, match="size limit"):
        inspect_input_bytes(body, suffix=".json", label="frozen input")


@pytest.mark.parametrize("body,suffix", [
    (b'{"n":"ordinary\\d+"}', ".json"),
    (b"n: [unterminated", ".yml"),
    (b"\xff ordinary literal", ".md"),
])
def test_owning_input_syntax_errors_are_sanitized(body, suffix):
    with pytest.raises((BenchmarkValidationError, KeyLeakError)) as caught:
        inspect_input_bytes(body, suffix=suffix, label="frozen input")
    assert "ordinary" not in str(caught.value) and "unterminated" not in str(caught.value)


def test_frozen_json_total_file_size_is_bounded_before_snapshot_writes(tmp_path, monkeypatch):
    limit = 64 * 1024
    monkeypatch.setattr(captured_inputs, "MAX_FRAME_BYTES", limit)
    schema = json.loads(inputs.JUDGE_SCHEMA_PATH.read_bytes())
    schema["examples"] = ["x" * (limit // 2), "y" * (limit // 2)]
    body = json.dumps(schema).encode()
    assert len(body) > limit
    path = tmp_path / "large-schema.json"
    path.write_bytes(body)
    monkeypatch.setattr(inputs, "JUDGE_SCHEMA_PATH", path)
    destination = tmp_path / "snapshot"
    try:
        manifest = inputs.snapshot_inputs(
            destination, criteria_path=inputs.CRITERIA_PATH, panel_path=PANEL_PATH,
            probes_path=None,
        )
    except BenchmarkValidationError as error:
        assert "size limit" in str(error)
        assert not destination.exists()
        assert path.read_bytes() == body
    else:
        assert_snapshot_files(destination, manifest, PANEL_PATH.read_bytes())
        assert (destination / inputs.SNAPSHOT_FILES["judge_schema"]).read_bytes() == body
        pytest.fail("capture wrote a frozen JSON file exceeding the original-file bound")


@pytest.mark.parametrize("suffix", [".json", ".md"])
def test_non_yaml_frozen_file_reads_use_the_original_file_bound(tmp_path, monkeypatch, suffix):
    path = tmp_path / ("input" + suffix)
    path.write_bytes(b"{}")
    original = inputs.read_input_bytes
    limits = []

    def observe(*args, **kwargs):
        limits.append(kwargs.get("max_bytes"))
        return original(*args, **kwargs)

    monkeypatch.setattr(inputs, "read_input_bytes", observe)
    assert inputs.scanned_input(path, suffix=suffix) == b"{}"
    assert limits == [captured_inputs.MAX_FRAME_BYTES]
