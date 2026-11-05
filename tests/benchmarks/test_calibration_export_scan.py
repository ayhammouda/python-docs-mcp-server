"""Every archived calibration composition crosses the same credential boundary."""

import base64
import copy
import hashlib
import json
import secrets
import socket

import pytest

from benchmarks.__main__ import main
from benchmarks.agentic.protocol import KeyLeakError, assert_no_key_shaped
from benchmarks.grading import calibration, score
from benchmarks.grading.calibration import g1_entries
from benchmarks.grading.score import score_run_dir
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks import test_grading_score as runs
from tests.benchmarks.test_grading_score import scripted_graded_run as scripted_graded_run

SELECTED = "haiku+luna+flash-lite"
FALLBACK = "haiku+luna+flash"


def _marker():
    return "A" + "Iza" + secrets.token_hex(18)[:35]


def _encoded(value):
    return json.dumps({"reported_model": value}).replace(value, "\\u0041" + value[1:])


def _snapshot(root):
    return {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _fallback():
    judges = ("haiku", "luna", "flash")
    return {
        "binding": {
            "composition": {j: {"model_id": runs.PANEL.judges[j].model_id, "effort": "low"}
                            for j in judges},
        },
        "gate": {"passed": None, "details": {"reason": "historic summary"}},
        "source": {"version": 1, "inputs": {}, "files": {}},
    }


def test_unselected_nested_source_is_refused_before_any_score_write(
    scripted_graded_run, tmp_path, monkeypatch, capsys,
):
    def offline(*args, **kwargs):
        pytest.fail("scoring attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", offline)
    run = scripted_graded_run
    archive = runs._g1_file(run, tmp_path / "g1.json")
    summary = score_run_dir(run, schema_path=runs.SCHEMA, g1_paths=[archive])
    assert summary["composition"] == SELECTED
    before = _snapshot(run)
    data = json.loads(archive.read_bytes())
    marker = _marker()
    data["entries"][FALLBACK] = _fallback()
    data["entries"][FALLBACK]["source"]["files"]["votes/unused.json"] = _encoded(marker)
    archive.write_text(json.dumps(data))
    original = archive.read_bytes()
    with pytest.raises(KeyLeakError) as caught:
        score_run_dir(run, schema_path=runs.SCHEMA, g1_paths=[archive])
    assert "unused.json" not in str(caught.value) and marker not in str(caught.value)
    assert before == _snapshot(run)
    assert archive.read_bytes() == original
    with pytest.raises(SystemExit) as exited:
        main(["agentic-score", "--run-dir", str(run), "--g1", str(archive)])
    assert exited.value.code == 2
    output = capsys.readouterr()
    assert marker not in output.err + output.out
    assert "unused.json" not in output.err + output.out
    assert before == _snapshot(run) and archive.read_bytes() == original


@pytest.mark.parametrize("slot", ["selected", "fallback", "separate-archive"])
@pytest.mark.parametrize("layer", ["vote", "receipt", "input-json", "input-yaml", "tuning"])
def test_all_sources_are_checked_before_grading(
    scripted_graded_run, tmp_path, monkeypatch, slot, layer,
):
    run = scripted_graded_run
    archive = runs._g1_file(run, tmp_path / "g1.json")
    data = json.loads(archive.read_bytes())
    data["entries"][FALLBACK] = _fallback()
    entry = data["entries"][SELECTED if slot == "selected" else FALLBACK]
    proof = {"version": 1, "inputs": {}, "files": {}}
    marker = _marker()
    if layer == "input-json":
        proof["inputs"]["judge_schema"] = _encoded(marker)
    elif layer == "input-yaml":
        proof["inputs"]["panel"] = 'note: "\\u0041' + marker[1:] + '"\n'
    else:
        name = "executions/run/unused.json" if layer == "receipt" else "votes/unused.json"
        proof["files"][name] = _encoded(marker)
    if layer == "tuning":
        entry["tuning"] = [{"source": proof}]
    else:
        entry["source"] = proof
    archives = [archive]
    if slot == "separate-archive":
        separate = tmp_path / "alternate.json"
        separate.write_text(json.dumps({"entries": {FALLBACK: data["entries"].pop(FALLBACK)}}))
        archives.append(separate)
    archive.write_text(json.dumps(data))
    before = _snapshot(tmp_path)

    def no_grade(*args, **kwargs):
        pytest.fail("grading began before the complete export scan")

    monkeypatch.setattr(score, "final_grade", no_grade)
    with pytest.raises(KeyLeakError):
        score_run_dir(run, schema_path=runs.SCHEMA, g1_paths=archives)
    assert before == _snapshot(tmp_path)


@pytest.mark.parametrize("name", [
    "criteria", "panel", "probes", "judge_schema", "corpus", "tuning",
])
@pytest.mark.parametrize("history", [False, True])
def test_all_frozen_structured_inputs_are_scanned(name, history):
    marker = _marker()
    # YAML !!binary and overridden duplicate scalars both remain inspected.
    text = 'note: "\\x41' + marker[1:] + '"\nnote: harmless\n'
    if name == "judge_schema":
        text = '{"note":"\\u0041' + marker[1:] + '","note":"harmless"}'
    proof = {"inputs": {name: text}, "files": {}}
    entry = {"tuning": [{"source": proof}]} if history else {"source": proof}
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps({"entries": {FALLBACK: entry}}).encode())


@pytest.mark.parametrize("name", [
    "votes/unused.json", "executions/run/unused.json", "items/probes/unused.json",
    "runs/unused.json",
])
def test_all_typed_json_files_are_scanned(name):
    proof = {"files": {name: _encoded(_marker())}}
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps({"entries": {FALLBACK: {"source": proof}}}).encode())

@pytest.mark.parametrize("text", [
    "description: ordinary YAML\n",
    '{"description":"ordinary trailing comma",}',
    '{"description":"\\uD83D"}',
])
def test_declared_json_input_refuses_invalid_text_before_replacing_export(tmp_path, text):
    entries = {FALLBACK: {"source": {"inputs": {"judge_schema": text}, "files": {}}}}
    destination = tmp_path / "g1.json"
    destination.write_bytes(b"prior evidence")
    before = _snapshot(tmp_path)
    with pytest.raises(BenchmarkValidationError) as caught:
        score._write(tmp_path, destination, {"entries": entries}, calibration=True)
    assert text not in str(caught.value)
    assert _snapshot(tmp_path) == before



@pytest.mark.parametrize("receipt", [False, True])
def test_structured_reply_text_is_one_more_declared_layer(receipt):
    reply = {"text": _encoded(_marker())}
    record = {"result": reply} if receipt else reply
    name = "executions/run/unused.json" if receipt else "votes/unused.json"
    proof = {"files": {name: json.dumps(record)}}
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps({"entries": {FALLBACK: {"source": proof}}}).encode())


@pytest.mark.parametrize("kind", ["anthropic", "openai", "google", "duplicate"])
def test_original_receipt_reply_blocks_and_shadowed_replies_are_scanned(kind):
    text = _encoded(_marker())
    if kind == "duplicate":
        body = '{"text":' + json.dumps(text) + ',"text":"harmless"}'
    else:
        block = {"text": text}
        raw = {
            "anthropic": {"result": {"message": {"content": [block]}}},
            "openai": {"response": {"body": {"output": [{"content": [block]}]}}},
            "google": {"response": {"candidates": [{"content": {"parts": [block]}}]}},
        }[kind]
        body = json.dumps({"result": {"raw": raw}})
    proof = {"files": {"executions/run/unused.json": body}}
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps({"entries": {FALLBACK: {"source": proof}}}).encode())


def test_binary_yaml_scalars_are_scanned():
    text = "note: !!binary " + base64.b64encode(_marker().encode()).decode()
    proof = {"inputs": {"panel": text}}
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps({"entries": {FALLBACK: {"source": proof}}}).encode())


def test_yaml_aliases_merges_and_cycles_need_no_expansion(monkeypatch):
    # A small representation graph may describe enormous repeated merges.
    # The scan must visit the representation, never construct these mappings.
    import yaml

    def no_mapping_expansion(*args, **kwargs):
        pytest.fail("credential inspection constructed a YAML mapping")

    monkeypatch.setattr(yaml.SafeLoader, "flatten_mapping", no_mapping_expansion)
    lines = ['base: &n0 {note: "café 雪"}', 'cycle: &self [*self]']
    for i in range(1, 45):
        lines.append(f"n{i}: &n{i} {{<<: [*n{i-1}, *n{i-1}]}}")
    entry = {"source": {"inputs": {"panel": "\n".join(lines)}}}
    expected = {FALLBACK: entry}
    assert g1_entries(json.dumps({"entries": expected}).encode()) == expected


def test_unknown_optional_history_stays_unknown():
    entries = {FALLBACK: {"source": None, "tuning": [None, {"judges": {}}]}}
    assert g1_entries(json.dumps({"entries": entries}).encode()) == entries


def test_escaped_binding_values_are_scanned_for_unselected_compositions():
    marker = _marker()
    body = json.dumps({"entries": {FALLBACK: {"binding": {"note": marker}}}})
    body = body.replace(marker, "\\u0041" + marker[1:])
    with pytest.raises(KeyLeakError):
        g1_entries(body.encode())


def test_archive_limit_precedes_parsing_or_nested_inspection(monkeypatch):
    monkeypatch.setattr(calibration, "MAX_CALIBRATION_BYTES", 10)
    with pytest.raises(BenchmarkValidationError, match="size limit"):
        g1_entries(b'{"entries": {}}')


def test_oversize_archive_is_bounded_at_the_actual_file_reader(tmp_path, monkeypatch):
    from benchmarks.agentic.inputs import read_input_bytes

    archive = tmp_path / "untrusted-name.json"
    archive.write_bytes(b"x" * 100)
    original = type(archive).open
    reads = []

    class BoundedStream:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def read(self, size):
            reads.append(size)
            return self.stream.read(size)

    monkeypatch.setattr(
        type(archive), "open", lambda p, *a, **k: BoundedStream(original(p, *a, **k))
    )
    with pytest.raises(BenchmarkValidationError, match="size limit") as caught:
        read_input_bytes(archive, "G1 evidence", "G1 evidence", max_bytes=20)
    assert reads == [21]
    assert archive.name not in str(caught.value)


def test_malformed_structured_reply_cannot_hide_a_recoverable_value():
    reply = _encoded(_marker())[:-2]  # truncated JSON string, retained as withheld history
    proof = {"files": {"votes/unused.json": json.dumps({"text": reply})}}
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps({"entries": {FALLBACK: {"source": proof}}}).encode())


def test_harmless_history_round_trips_through_real_exports(scripted_graded_run, tmp_path):
    run = scripted_graded_run
    archive = runs._g1_file(run, tmp_path / "g1.json")
    data = json.loads(archive.read_bytes())
    harmless = 'Unicode café 雪; C:\\Users\\history; literal \\u0041; "quoted"'
    fallback = _fallback()
    fallback.update(history_unknown=None, notes=harmless)
    fallback["source"]["inputs"]["panel"] = json.dumps({"note": harmless})
    fallback["source"]["files"]["votes/old.json"] = json.dumps({"text": harmless})
    fallback["tuning"] = [{"judges": {"flash": {"chosen": "low"}}, "notes": harmless}]
    data["entries"][FALLBACK] = fallback
    # Summaries are prose: an escaped example is not another declared JSON file.
    data["entries"][SELECTED]["tuning"].append({"notes": json.dumps({"history": harmless})})
    archive.write_text(json.dumps(data, ensure_ascii=True))
    body = archive.read_bytes()
    summary = score_run_dir(run, schema_path=runs.SCHEMA, g1_paths=[archive])
    assert summary["composition"] == SELECTED
    assert json.loads((run / "grading/disclosure/g1-entries.json").read_bytes()) == data["entries"]
    digest = hashlib.sha256(body).hexdigest()
    exported = run / "grading/disclosure/g1" / f"{digest}.json"
    assert exported.read_bytes() == body
    assert g1_entries(body) == copy.deepcopy(data["entries"])


@pytest.mark.parametrize("as_bytes", [False, True])
@pytest.mark.parametrize("marker, provider", [
    ("sk-ant-api03-" + "s" * 80, "anthropic"),
    ("sk-proj-" + "s" * 40, "openai"),
    ("sk-svcacct-" + "s" * 40, "openai"),
    ("AIza" + "s" * 35, "google"),
])
def test_long_calibration_strings_preserve_provider_boundaries(as_bytes, marker, provider):
    def inspect(value):
        assert_no_key_shaped(value.encode() if as_bytes else value, where="calibration test")

    padding = "x" * 16384
    inspect(padding)
    inspect(padding + " sk- short AIza short")
    with pytest.raises(KeyLeakError, match=provider):
        inspect(padding + "%20" + marker + ".")
    if provider == "anthropic":
        with pytest.raises(KeyLeakError, match=provider):
            inspect(padding + marker)
    else:
        inspect(padding + marker)


@pytest.mark.parametrize("shape", [
    "source", "files", "file", "inputs", "input", "unknown_input", "unexpected_file",
    "tuning", "tuning_member",
])
def test_malformed_source_contents_cannot_bypass_export_inspection(
    scripted_graded_run, tmp_path, shape,
):
    root = scripted_graded_run
    export = runs._g1_file(root, tmp_path / "g1.json")
    score_run_dir(root, schema_path=runs.SCHEMA, g1_paths=[export])
    before = _snapshot(root)
    row = _fallback()
    concealed = _encoded(_marker())
    if shape == "source":
        row["source"] = concealed
    elif shape == "files":
        row["source"]["files"] = [concealed]
    elif shape == "file":
        row["source"]["files"] = {"votes/unused.json": [concealed]}
    elif shape == "inputs":
        row["source"]["inputs"] = [concealed]
    elif shape == "input":
        row["source"]["inputs"] = {"panel": [concealed]}
    elif shape == "unknown_input":
        row["source"]["inputs"] = {"unknown": concealed}
    elif shape == "unexpected_file":
        row["source"]["files"] = {"votes/unused.txt": concealed}
    elif shape == "tuning":
        row["tuning"] = concealed
    else:
        row["tuning"] = [concealed]
    data = json.loads(export.read_bytes())
    data["entries"][FALLBACK] = row
    export.write_text(json.dumps(data))
    with pytest.raises(KeyLeakError, match="uninspectable calibration history"):
        score_run_dir(root, schema_path=runs.SCHEMA, g1_paths=[export])
    assert _snapshot(root) == before
