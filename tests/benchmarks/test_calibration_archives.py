"""Declared originals must control publication, including older score-only exports."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from benchmarks.agentic.inputs import CapturedInput
from benchmarks.agentic_report import _GradeCapture
from benchmarks.grading.calibration import judge_settings
from benchmarks.grading.gates import g1
from benchmarks.grading.inputs import SNAPSHOT_FILES
from benchmarks.grading.probes import probe_file_from_inputs
from benchmarks.grading.publication import reconcile_disclosures
from benchmarks.grading.score import score_run_dir, write_g1
from benchmarks.report import generate_readme_summary, generate_report
from tests.benchmarks import test_grading_score as runs


def archive(root, entries):
    return archive_bytes(root, json.dumps({"entries": entries}, sort_keys=True).encode())


def archive_bytes(root, body):
    digest = hashlib.sha256(body).hexdigest()
    path = f"disclosure/g1/{digest}.json"
    (root / "grading" / path).write_bytes(body)
    return {"path": path, "sha256": digest}


def test_declared_archives_control_primary_results(tmp_path, full_calibration, monkeypatch):
    root, _, _, _ = full_calibration
    summary_path = root / "grading/summary.json"
    summary = json.loads(summary_path.read_bytes())
    key = summary["composition"]
    projection = root / "grading/disclosure/g1-entries.json"
    modern = json.loads(projection.read_bytes())
    entries = copy.deepcopy(modern)
    # Score-only historical originals can independently establish G1. The second
    # original has genuinely failing scores and a matching, recomputed verdict.
    for entry in entries.values():
        entry.pop("source", None)
        entry.pop("tuning", None)
    first = archive(root, entries)
    other = copy.deepcopy(entries)
    frozen = root / "grading/inputs"
    suite = probe_file_from_inputs(
        CapturedInput.read(frozen / "probes.yml", "probes", "probes"),
        CapturedInput.read(frozen / "grading-criteria.yml", "criteria", "criteria"), None,
    )
    held = [p for p in suite.probes if p.set == "held_out"]
    failing = other[key]
    for p in held:
        failing["scores"]["panel"][p.id] = 0.0 if p.expected_score == 1 else 1.0
        failing["scores"]["judges"]["haiku"][p.id] = failing["scores"]["panel"][p.id]
    failing["gate"] = asdict(g1(held, failing["scores"]["panel"], failing["scores"]["judges"],
                                dropped=[d for d in suite.dropped if d["set"] == "held_out"]))
    assert failing["gate"]["passed"] is False
    second = archive(root, other)
    summary["disclosure"]["g1_files"] = [first, second]
    runs._json(summary_path, summary)
    published = SimpleNamespace(
        root=root, summary=summary, g1_replays={},
        bundle=SimpleNamespace(run_dir=root),
        inputs=json.loads((frozen / "inputs.json").read_bytes()),
        frozen={name: (frozen / file).read_bytes() for name, file in SNAPSHOT_FILES.items()},
        grade_capture=_GradeCapture(),
    )
    reconcile_disclosures(published)
    assert published.g1_replays[key][0].passed is None
    assert published.g1_replays[key][1].passed is None

    from benchmarks.grading import publication

    real_read = publication.artifact_bytes

    def publish(copies, projection_entries=entries):
        published.summary = copy.deepcopy(summary)
        if copies == "absent":
            published.summary.pop("disclosure")
        else:
            published.summary["disclosure"]["g1_files"] = copies
        runs._json(projection, projection_entries)
        published.g1_replays = {}
        reads = set()

        def once(run_root, path):
            assert path not in reads, f"reopened mutable archive: {path}"
            reads.add(path)
            return real_read(run_root, path)

        with monkeypatch.context() as capture:
            capture.setattr(publication, "artifact_bytes", once)
            reconcile_disclosures(published)
        return published.g1_replays[key]

    # Order and identical duplicates cannot select a winning original.
    for copies in ([second, first], [first, first, second], [second, second, first]):
        assert [g.passed for g in publish(copies)] == [None, None]
    assert [g.passed for g in publish([first, first])] == [True, True]
    annotated = {**first, "source": "/unread/arbitrary.json"}
    assert [g.passed for g in publish([annotated])] == [True, True]
    assert [g.passed for g in publish([second], other)] == [False, True]
    # A projection contradicting a single valid original is also stale.
    assert [g.passed for g in publish([second])] == [None, None]

    # No archive declaration in older exports is different from a broken one.
    assert [g.passed for g in publish("absent")] == [True, True]
    gate, _ = publish([first])
    assert gate.passed is True
    assert gate.details["held_out_test_retest"]["haiku"]["items"] == "unknown"
    for copies in ([], None, {}, [None], [True], ["invalid"], [{}], [first, None],
                   [{**first, "path": "/arbitrary/source.json"}],
                   [{**first, "sha256": []}],
                   [{"sha256": "0" * 64, "path": f"disclosure/g1/{'0' * 64}.json"}]):
        assert [g.passed for g in publish(copies)] == [None, None]
    archive_path = root / "grading" / first["path"]
    before = archive_path.read_bytes()
    archive_path.write_bytes(b"{}")
    assert [g.passed for g in publish([first])] == [None, None]
    archive_path.write_bytes(before)

    # Missing composition and malformed entries cannot reuse the projection.
    for original in ({}, {key: []}, {key: {}}):
        assert [g.passed for g in publish([archive(root, original)])] == [None, None]
    for body in (b"null", b'{"entries":[]}', b"not JSON", b"\xff",
                 b'{"entries":{},"entries":' + json.dumps(entries).encode() + b'}'):
        assert [g.passed for g in publish([archive_bytes(root, body)])] == [None, None]
    boolean_scores = copy.deepcopy(entries)
    scores = boolean_scores[key]["scores"]["panel"]
    probe_id = next(p for p, score in scores.items() if score == 1.0)
    scores[probe_id] = True
    assert [g.passed for g in publish([archive(root, boolean_scores)])] == [None, None]
    # Reference-only score contradictions withhold reference eligibility too.
    wrong_reference = copy.deepcopy(entries)
    ref = wrong_reference[key]["reference_check"]["details"]["scores"]
    ref[next(iter(ref))] = 0.25
    assert [g.passed for g in publish([archive(root, wrong_reference)])] == [None, None]

    # New writer exports must replay their embedded pass-1 attempts even if a
    # differing disclosure copy would otherwise disable repeat verification.
    modern_copy = archive(root, modern)
    gate, ref_gate = publish([modern_copy], modern)
    assert (gate.passed, ref_gate.passed) == (True, True)
    assert gate.details["held_out_test_retest"]["haiku"]["pass2_records"] == 0
    poisoned = copy.deepcopy(modern)
    poisoned[key]["scores"] = other[key]["scores"]
    poisoned[key]["gate"] = other[key]["gate"]
    assert [g.passed for g in publish([archive(root, poisoned)], other)] == [None, None]

    # Changes confined to the optional disclosure history cannot erase primary
    # scores independently supported by an intact original.
    auxiliary = copy.deepcopy(modern)
    auxiliary[key]["source"] = {}
    auxiliary[key]["tuning"] = []
    gate, ref_gate = publish([modern_copy], auxiliary)
    assert (gate.passed, ref_gate.passed) == (True, True)
    assert gate.details["held_out_test_retest"]["haiku"]["items"] == "unknown"
    assert gate.details["calibration_unknown"]["tuning"]

    # A contradiction for the selected fallback must not erase a separately
    # supported registered primary composition in the same export.
    primary_key = "haiku+luna+flash-lite"
    primary = copy.deepcopy(entries[key])
    primary["binding"]["composition"]["flash-lite"] = {
        "model_id": runs.PANEL.judges["flash-lite"].model_id, "effort": "low",
    }
    del primary["binding"]["composition"]["flash"]
    primary["binding"]["judge_settings"] = judge_settings(
        CapturedInput.from_bytes(published.frozen["panel"], "panel", "panel"),
        ["haiku", "luna", "flash-lite"], reference="reference",
    )
    primary["scores"]["judges"]["flash-lite"] = primary["scores"]["judges"].pop("flash")
    by_judge = primary["gate"]["details"]["by_judge"]
    by_judge["flash-lite"] = by_judge.pop("flash")
    primary["gate"]["details"].pop("composition", None)
    combined = {**entries, primary_key: primary}
    for copies in ([archive(root, combined), second], [second, archive(root, combined)]):
        assert [g.passed for g in publish(copies, combined)] == [None, None]
        assert [g.passed for g in published.g1_replays[primary_key]] == [True, True]
    # Invalid entry shapes are local when their composition can be identified.
    publish([archive(root, {key: [], primary_key: primary})], combined)
    assert [g.passed for g in published.g1_replays[primary_key]] == [True, True]
    # A derived projection may omit the primary. Its original still establishes it.
    publish([archive(root, combined)], entries)
    assert set(published.g1_replays) == {key, primary_key}
    for composition in (key, primary_key):
        assert [g.passed for g in published.g1_replays[composition]] == [True, True]


@pytest.mark.parametrize("partial", [False, True])
def test_new_writer_empty_and_partial_controls_remain_unknown(tmp_path, partial):
    from tests.benchmarks.test_agentic_report import MATRIX, METHODOLOGY, _build

    held = runs._workspace(tmp_path / "held", "held-out", {"probes.yml": runs.PROBES})
    runs._json(held / "runs/run-1.json", {
        "target": "probes", "pass": 1, "composition": runs.COMPOSITION,
    })
    if partial:
        runs._vote(held, "haiku", "KEY--SYN-EX-001", runs._vote_text(), target="probes")
    evidence = write_g1(held)
    run = _build(tmp_path / "answering", families_run=("scripted-anthropic",))
    summary = score_run_dir(run, schema_path=runs.SCHEMA, g1_paths=[evidence])
    assert summary["gates"]["G1"]["passed"] is None
    assert summary["reference_check"]["passed"] is None
    expected_hits = 1 if partial else 0
    assert summary["gates"]["G1"]["details"]["by_judge"]["haiku"]["hits"] == expected_hits
    before = {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}
    report = generate_report(run, model_matrix_path=MATRIX, methodology_path=METHODOLOGY)
    readme = generate_readme_summary(run, methodology_path=METHODOLOGY)
    assert "G1 for this run's composition: not evaluable." in report.read_text()
    assert "fewer normalized tokens per correct answer" not in readme.read_text()
    assert {p: p.read_bytes() for p in before} == before
