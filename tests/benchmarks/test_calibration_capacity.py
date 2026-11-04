"""Calibration transport must fit real cohorts without relaxing ordinary frames."""

import hashlib
import json
import random
from pathlib import Path

import pytest

from benchmarks.agentic.protocol import MAX_FRAME_BYTES
from benchmarks.grading import calibration_history
from benchmarks.grading.calibration import g1_entries
from benchmarks.grading.calibration_history import CalibrationSource
from benchmarks.grading.calibration_storage import PackedFiles, pack_files
from benchmarks.runner import BenchmarkValidationError


def test_complete_variable_cohort_round_trips_without_frame_limit_change(monkeypatch):
    source = CalibrationSource(Path("/synthetic-calibration"))
    rng = random.Random(20261104)
    expected = {}
    current = b""
    monkeypatch.setattr(calibration_history, "artifact_bytes", lambda *args: current)
    for index in range(4550):
        # Different high-entropy, valid explanation on every result, repeated in
        # the parsed vote and raw reply as in the registered capture contract.
        explanation = "".join(rng.choices("abcdefghijklmnopqrstuvwxyz0123456789 ", k=4096))
        reply = json.dumps({"explanation": explanation, "attempted": True})
        for kind in ("votes", "executions"):
            name = f"{kind}/synthetic-{index}.json"
            current = json.dumps(
                {
                    "text": reply,
                    "vote": {"explanation": explanation},
                    "attempt": 1,
                    "item_id": str(index),
                }
            ).encode()
            expected[name] = hashlib.sha256(current).hexdigest()
            source.read(source.root, source.root / name)
    assert source.size > MAX_FRAME_BYTES
    proof = source.export({})
    body = json.dumps({"entries": {"synthetic": {"source": proof}}}).encode()
    entry = g1_entries(body)["synthetic"]
    portable, _ = CalibrationSource.restore(entry["source"])
    assert portable.portable
    assert len(portable.files) == 9100
    assert {
        name: hashlib.sha256(portable.read(portable.root, portable.root / name)).hexdigest()
        for name in portable.files
    } == expected
    # This is a storage boundary, never a change to worker transport.
    assert MAX_FRAME_BYTES == 67_108_864


@pytest.mark.parametrize(
    "damage", ["size", "hash", "trailing", "truncated", "flood", "aggregate", "padding"]
)
def test_packed_members_are_bounded_before_becoming_evidence(damage):
    import base64

    proof = pack_files({"votes/test.json": json.dumps({"text": "a" * 100_000})})
    digest = next(iter(proof["blobs"]))
    record = proof["blobs"][digest]
    if damage == "size":
        record["size"] = 2  # Actual expansion must stop at 3 bytes.
    elif damage == "hash":
        proof["blobs"]["0" * 64] = proof["blobs"].pop(digest)
        proof["files"]["votes/test.json"] = "0" * 64
    elif damage in ("trailing", "truncated"):
        compressed = base64.b64decode(record["bz2"])
        compressed = compressed + compressed if damage == "trailing" else compressed[:-1]
        record["bz2"] = base64.b64encode(compressed).decode()
    elif damage == "flood":
        proof["files"] = {f"votes/{i}.json": digest for i in range(32769)}
    elif damage == "padding":
        record["bz2"] += "\n" * (2 * record["size"] + 1024)
    else:
        record["size"] = MAX_FRAME_BYTES
        proof["files"] = {f"votes/{i}.json": digest for i in range(33)}
    with pytest.raises(BenchmarkValidationError):
        g1_entries(json.dumps({"entries": {"unselected": {"source": proof}}}).encode())


def test_opaque_member_encoding_does_not_trip_literal_scan_and_deduplicates():
    # Random bytes matter here: repeating a single letter is not a compression
    # capacity test. JSON UTF-8 input is kept exact, including whitespace.
    rng = random.Random(17)
    alphabet = "".join(chr(i) for i in range(32, 127) if chr(i) not in {'"', "\\"})
    text = json.dumps({"text": "".join(rng.choices(alphabet, k=200_000))}, indent=2)
    proof = pack_files({"votes/first.json": text, "votes/retry.json": text})
    assert len(json.dumps(proof).encode()) > len(text.encode())
    assert len(proof["blobs"]) == 1
    files = PackedFiles(proof["files"], proof["blobs"])
    assert files["votes/first.json"] == text == files["votes/retry.json"]


def test_individual_and_ordinary_artifacts_keep_the_frame_bound(tmp_path):
    from benchmarks.grading.calibration_storage import calibration_bytes
    from benchmarks.report_files import artifact_bytes

    large = tmp_path / "ordinary.json"
    with large.open("wb") as stream:
        stream.truncate(MAX_FRAME_BYTES + 1)
    with pytest.raises(BenchmarkValidationError):
        artifact_bytes(tmp_path, large)
    with pytest.raises(BenchmarkValidationError):
        CalibrationSource(tmp_path).read(tmp_path, large)
    # The G1 transport bound is finite too; a sparse file avoids a large fixture.
    with large.open("wb") as stream:
        stream.truncate(1024 * 1024 * 1024 + 1)
    with pytest.raises(BenchmarkValidationError):
        calibration_bytes(tmp_path, large)


@pytest.mark.parametrize("layer", ["vote", "receipt", "input", "tuning"])
def test_packed_sources_use_existing_typed_secret_guard(layer):
    from benchmarks.agentic.protocol import KeyLeakError

    marker = "A" + "Iza" + "synthetic" * 3 + "q" * 8
    assert len(marker) == 39
    nested = json.dumps({"reported_model": marker}).replace(marker, "\\u0041" + marker[1:])
    name = "executions/test.json" if layer == "receipt" else "votes/test.json"
    proof = {**pack_files({name: json.dumps({"text": nested})}), "inputs": {}}
    if layer == "input":
        proof["inputs"] = {"panel": 'note: "\\x41' + marker[1:] + '"'}
    entry = {"tuning": [{"source": proof}]} if layer == "tuning" else {"source": proof}
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps({"entries": {"unselected": entry}}).encode())


def test_bad_packed_export_preserves_previous_output(tmp_path):
    from benchmarks.agentic.protocol import KeyLeakError
    from benchmarks.grading.score import _write

    path = tmp_path / "g1.json"
    path.write_bytes(b"previous valid capture")
    marker = "A" + "Iza" + "s" * 35
    proof = pack_files({"votes/test.json": json.dumps({"reported_model": marker})})
    data = {"entries": {"other": {"source": proof}}}
    with pytest.raises(KeyLeakError):
        _write(tmp_path, path, data, calibration=True)
    assert path.read_bytes() == b"previous valid capture"


@pytest.mark.parametrize("damage", ["float-version", "foreign-path", "untyped-path", "orphan"])
def test_malformed_packed_layout_cannot_hide_unselected_source_content(damage):
    proof = pack_files({"votes/test.json": '{"text":"harmless"}'})
    digest = next(iter(proof["blobs"]))
    if damage == "float-version":
        proof["version"] = 2.0
    elif damage in ("foreign-path", "untyped-path"):
        name = "../outside.json" if damage == "foreign-path" else "votes/opaque.txt"
        proof["files"] = {name: digest}
    else:
        proof["files"] = {}
    with pytest.raises(BenchmarkValidationError):
        g1_entries(json.dumps({"entries": {"unselected": {"source": proof}}}).encode())


def test_budget_covers_every_composition_and_tuning_source(monkeypatch):
    from benchmarks.grading import calibration_scan

    monkeypatch.setattr(calibration_scan, "MAX_SOURCE_BYTES", 200)
    proof = pack_files({"votes/test.json": json.dumps({"text": "z" * 150})})
    with pytest.raises(BenchmarkValidationError, match="aggregate"):
        g1_entries(json.dumps({"entries": {
            "selected": {"source": proof},
            "unselected": {"tuning": [{"source": proof}]},
        }}).encode())


def test_larger_transport_does_not_accept_oversized_metadata(monkeypatch):
    from benchmarks.grading import calibration, calibration_storage

    monkeypatch.setattr(calibration, "MAX_FRAME_BYTES", 1)
    monkeypatch.setattr(calibration_storage, "MAX_METADATA_BYTES", 700)
    proof = pack_files({"votes/test.json": json.dumps({"text": "z" * 1000})})
    assert g1_entries(json.dumps({"entries": {"other": {"source": proof}}}).encode())
    with pytest.raises(BenchmarkValidationError, match="metadata"):
        g1_entries(json.dumps({"entries": {"other": {"notes": "z" * 1000}}}).encode())


def test_concurrent_immutable_archive_publication_and_retry(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from benchmarks.grading.score import _new_g1_archives, _publish_g1_archives

    proof = pack_files({"votes/test.json": json.dumps({"text": "varied original"})})
    body = json.dumps({"entries": {"other": {"source": proof}}}).encode()
    digest = hashlib.sha256(body).hexdigest()
    raw = tmp_path / "grading/disclosure/g1"
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(_publish_g1_archives, tmp_path, raw, {digest: body})
                   for _ in range(3)]
        for future in futures:
            future.result()
    assert (raw / f"{digest}.json").read_bytes() == body
    assert _new_g1_archives(tmp_path, raw, {digest: body}) == {}
    _publish_g1_archives(tmp_path, raw, {digest: body})
    assert [p.name for p in raw.iterdir()] == [f"{digest}.json"]


def test_score_preflights_calibration_export_before_mutating_grades(tmp_path, monkeypatch):
    from benchmarks.grading import score
    from tests.benchmarks import test_grading_score as runs

    run = runs._build_run(tmp_path)
    supplied = runs._g1_file(run, tmp_path / "g1.json")
    before = {p.relative_to(run): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in run.rglob("*") if p.is_file()}
    # Inputs still fit; force only the producer's prepared projection to fail.
    original = score._evidence_bytes

    def limited(path, value, **kwargs):
        if path.name == "g1-entries.json":
            raise BenchmarkValidationError("combined export size limit")
        return original(path, value, **kwargs)

    def no_grading(*args, **kwargs):
        pytest.fail("scoring mutated grades before calibration export preflight")

    monkeypatch.setattr(score, "_evidence_bytes", limited)
    monkeypatch.setattr(score, "score_run", no_grading)
    with pytest.raises(BenchmarkValidationError, match="combined export"):
        score.score_run_dir(run, schema_path=runs.SCHEMA, g1_paths=[supplied])
    assert {p.relative_to(run): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in run.rglob("*") if p.is_file()} == before


def test_large_transport_rejects_structure_flood_before_json_allocation(monkeypatch):
    from benchmarks.grading import calibration, calibration_storage

    monkeypatch.setattr(calibration_storage, "MAX_FRAME_BYTES", 64)
    monkeypatch.setattr(calibration_storage, "MAX_METADATA_BYTES", 64)
    body = json.dumps({"entries": {"unused": {"notes": [0] * 500}}}).encode()

    def no_parser(*args, **kwargs):
        pytest.fail("large structure flood reached the allocating JSON parser")

    monkeypatch.setattr(calibration.json, "loads", no_parser)
    with pytest.raises(BenchmarkValidationError, match="structure"):
        calibration.g1_entries(body)


def test_transport_preflight_handles_escaped_quotes_and_bounds_depth(monkeypatch):
    from benchmarks.grading import calibration_storage as storage

    monkeypatch.setattr(storage, "MAX_FRAME_BYTES", 1024)
    monkeypatch.setattr(storage, "MAX_METADATA_BYTES", 64)
    text = ("quote\" and slash\\ plus literal [ { } ]\n" * 38)
    body = json.dumps({"entries": {"other": {"text": text}}}).encode()
    assert len(body) > 1024
    storage.check_transport_structure(body)
    monkeypatch.setattr(storage, "MAX_FRAME_BYTES", 64)
    with pytest.raises(BenchmarkValidationError, match="string"):
        storage.check_transport_structure(body)
    monkeypatch.setattr(storage, "MAX_METADATA_BYTES", 4096)
    with pytest.raises(BenchmarkValidationError, match="structure"):
        storage.check_transport_structure(b"[" * 129 + b"0" + b"]" * 129)


def test_invocation_lru_eviction_uses_captured_bytes_without_reopening(tmp_path, monkeypatch):
    monkeypatch.setattr(calibration_history, "MAX_FRAME_BYTES", 100)
    directory = tmp_path / "runs"
    directory.mkdir()
    first, second = directory / "one.json", directory / "two.json"
    first.write_text(json.dumps({"note": "original" + "x" * 60}))
    second.write_text(json.dumps({"note": "another" + "y" * 60}))
    real = calibration_history.artifact_bytes
    observed = []

    def once(root, path):
        assert path not in observed
        observed.append(path)
        return real(root, path)

    monkeypatch.setattr(calibration_history, "artifact_bytes", once)
    source = CalibrationSource(tmp_path)
    expected = source.invocation(first)
    first.write_text('{"note":"replaced"}')
    source.invocation(second)
    assert first not in source.invocations
    assert source.invocation(first) == expected
    assert source.inventory.runs["one"] is source.invocation(first)
    assert source.invocation_bytes <= 100 and observed == [first, second]


@pytest.mark.parametrize("packed", [False, True])
def test_restored_file_mapping_is_a_snapshot_even_if_caller_changes_proof(packed):
    original = '{"text":"original bytes"}'
    proof = {**pack_files({"votes/test.json": original}), "inputs": {}} if packed else {
        "version": 1, "inputs": {}, "files": {"votes/test.json": original},
    }
    source, _ = CalibrationSource.restore(proof)
    proof["files"]["votes/test.json"] = "replaced"
    if packed:
        next(iter(proof["blobs"].values()))["bz2"] = "invalid"
    assert source.read(source.root, source.root / "votes/test.json") == original.encode()


@pytest.mark.parametrize("files", [None, [], "historic files unavailable"])
def test_legacy_unknown_file_section_retains_existing_inspection_semantics(files):
    entry = {"source": {"files": files}, "history_unknown": True}
    assert g1_entries(json.dumps({"entries": {"other": entry}}).encode()) == {"other": entry}


@pytest.mark.parametrize("unsafe", ["symlink", "oversize"])
def test_projection_keeps_unsafe_artifact_reads_fatal(tmp_path, unsafe):
    from types import SimpleNamespace

    from benchmarks.grading.calibration_storage import MAX_CALIBRATION_BYTES
    from benchmarks.grading.publication import _calibration_replays

    projection = tmp_path / "grading/disclosure/g1-entries.json"
    projection.parent.mkdir(parents=True)
    if unsafe == "symlink":
        target = tmp_path / "other.json"
        target.write_text("{}")
        projection.symlink_to(target)
    else:
        with projection.open("wb") as file:
            file.truncate(MAX_CALIBRATION_BYTES + 1)
    run = SimpleNamespace(bundle=SimpleNamespace(run_dir=tmp_path), summary={})
    with pytest.raises(BenchmarkValidationError):
        _calibration_replays(run)
