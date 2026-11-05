"""Gate and report rendering must use the same captured grading evidence."""

import json
import shutil
from collections import Counter

import pytest

from benchmarks import agentic_report, report_files
from benchmarks.agentic_report import _RunMetadata
from benchmarks.grading.publication import reconcile_gates
from benchmarks.report import _load_run_bundle
from tests.benchmarks import test_agentic_report as fixtures
from tests.benchmarks.test_agentic_report import _json, _render

# Share the full cohort, with a fresh writable copy for each mutation.
validated_template = fixtures.validated_template
captured_run = fixtures.validated_bundle


@pytest.mark.parametrize("changing", ["votes", "items"])
def test_publication_never_reopens_captured_vote_or_item_bytes(captured_run, monkeypatch, changing):
    original = report_files.artifact_bytes
    seen = Counter()

    def read(root, path):
        relative = path.relative_to(root).as_posix()
        body = original(root, path)
        if relative.startswith(("grading/votes/", "grading/items/", "grading/inputs/")) or (
            relative == "grading/reference-sample.json"
        ):
            seen[relative] += 1
            if seen[relative] > 1 and relative.startswith(f"grading/{changing}/"):
                raw = json.loads(body)
                if changing == "items":
                    raw["meta"]["suspect"] = True
                elif raw["vote"]:
                    raw["vote"]["attempted"] = False
                return json.dumps(raw).encode()
        return body

    monkeypatch.setattr(report_files, "artifact_bytes", read)
    monkeypatch.setattr(agentic_report, "artifact_bytes", read)
    run = _RunMetadata(_load_run_bundle(captured_run))
    assert run.grading == "live"
    assert run.tier == "Validated", run.summary["gates"]
    key, av = next(iter(run.grade_capture.answers.items()))
    request = run.grade_capture.requests[key]
    assert av.suspect is request.suspect
    assert all(binding["input_sha256"] == request.sha256() for binding in av.records.values())
    assert run.grade_capture.finals[key]["grading"]["input_sha256"] == request.sha256()
    # A subsequent reconciliation may consult G1, but it must not reopen any
    # chosen-grade evidence or the reference sample.
    for base in ("grading/votes", "grading/items"):
        shutil.rmtree(captured_run / base)
    (captured_run / "grading/reference-sample.json").unlink()
    assert all(g.passed is True for g in reconcile_gates(run).values())
    assert seen and max(seen.values()) == 1, seen.most_common(3)


def test_complete_control_keeps_registered_model_effort_and_sample(captured_run):
    run = _RunMetadata(_load_run_bundle(captured_run))
    assert run.judges["haiku"]["model_id"] == "claude-haiku-5-5"
    assert run.summary["efforts"]["haiku"] == "low"
    sample = json.loads((captured_run / "grading/reference-sample.json").read_bytes())
    assert len(sample["ids"]) == 300
    assert run.grading == "live"
    assert run.tier == "Validated", run.summary["gates"]


@pytest.mark.parametrize("eligible", [2, 3])
def test_pending_cells_keep_eligible_g2_pairs_without_observed_scores(
    captured_run, monkeypatch, eligible
):
    # Even complete raw votes cannot stand in for absent chosen-grade bindings.
    # The independent G2 observations are still eligible while scoring is pending.
    for path in (captured_run / "scoring").glob("*/*.json"):
        record = json.loads(path.read_bytes())
        record.update(score=None, grading=None, scoring_method=None)
        _json(path, record)
    if eligible == 2:
        receipt = agentic_report.grading_receipt_matches
        monkeypatch.setattr(
            agentic_report, "grading_receipt_matches",
            lambda root, vote, *args: (
                vote["judge_id"] != "flash-lite" and receipt(root, vote, *args)
            ),
        )
    run = _RunMetadata(_load_run_bundle(captured_run))
    assert run.grading == "unknown"
    assert run.summary["gates"]["G2"]["passed"] is True
    assert run.summary["gates"]["G3"]["passed"] is None
    assert run.summary["gates"]["G4"]["passed"] is None
    assert not run.grade_capture.scores
    assert not run.grade_capture.finals
    assert all(
        sum(v is not None for v in av.votes.values()) == eligible
        for av in run.grade_capture.answers.values()
    )


def test_unverified_first_capture_cannot_supply_public_gate_scores(captured_run, monkeypatch):
    monkeypatch.setattr(agentic_report, "grading_receipt_matches", lambda *a: False)
    run = _RunMetadata(_load_run_bundle(captured_run))
    assert run.grading == "unknown"
    assert not run.grade_capture.scores
    assert not run.grade_capture.finals
    assert all(run.summary["gates"][g]["passed"] is None for g in ("G2", "G3", "G4"))


def test_g3_uses_captured_sample_bound_to_the_verified_pairs(captured_run):
    sample = captured_run / "grading/reference-sample.json"
    data = json.loads(sample.read_bytes())
    data["ids"][0] = "foreign-answer"
    _json(sample, data)
    run = _RunMetadata(_load_run_bundle(captured_run))
    assert run.grading == "live"
    assert run.summary["gates"]["G2"]["passed"] is True
    assert run.summary["gates"]["G3"]["passed"] is None
    assert run.tier != "Validated"


def test_normal_renderer_discloses_the_same_captured_reference_sample(captured_run, monkeypatch):
    original = report_files.artifact_bytes
    reads = 0

    def read(root, path):
        nonlocal reads
        if path == root / "grading/reference-sample.json":
            reads += 1
        return original(root, path)

    monkeypatch.setattr(report_files, "artifact_bytes", read)
    report = _render(captured_run)
    assert "Grading outcome: Validated" in report
    assert reads == 1
