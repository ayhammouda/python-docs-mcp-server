"""A historically self-consistent reference aggregate still needs a possible grade."""

import hashlib
from dataclasses import asdict

import pytest

from benchmarks.agentic.inputs import CapturedInput
from benchmarks.agentic_report import _RunMetadata
from benchmarks.grading.gates import reference_probe_check
from benchmarks.grading.probes import probe_file_from_inputs, reference_subset
from benchmarks.grading.score import score_run_dir
from benchmarks.report import _load_run_bundle
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks.accepted_report_fixture import read, write
from tests.benchmarks.test_agentic_report import SCHEMA, _readme, _render
from tests.benchmarks.test_agentic_report import validated_bundle as validated_bundle
from tests.benchmarks.test_agentic_report import validated_template as validated_template


def test_impossible_reference_grade_cannot_authorize_comparisons(validated_bundle, tmp_path):
    run = validated_bundle
    grading = run / "grading"
    summary = read(grading / "summary.json")
    original = grading / summary["disclosure"]["g1_files"][0]["path"]
    archive = read(original)
    entry = archive["entries"][summary["composition"]]
    scores = entry["reference_check"]["details"]["scores"]
    scores["KEY--SYN-APP-002"] = 0.25
    inputs = grading / "inputs"
    suite = probe_file_from_inputs(
        CapturedInput.read(inputs / "probes.yml", "probes", "probes"),
        CapturedInput.read(inputs / "grading-criteria.yml", "criteria", "criteria"),
        None,
    )
    reference = reference_probe_check(
        reference_subset(suite.probes, seed=suite.header["corpus_sha256"]), scores
    )
    assert reference.passed and reference.details["overall"]["hits"] == 349
    entry["reference_check"].update(passed=reference.passed, details=reference.details)
    supplied = tmp_path / "g1.json"
    body = write(supplied, archive)
    with pytest.raises(BenchmarkValidationError, match="invalid probe score"):
        score_run_dir(run, schema_path=SCHEMA, g1_paths=[supplied])

    # The same impossible score in a previously exported, internally consistent bundle.
    digest = hashlib.sha256(body).hexdigest()
    relative = f"disclosure/g1/{digest}.json"
    (grading / relative).write_bytes(body)
    write(grading / "disclosure/g1-entries.json", archive["entries"])
    summary["disclosure"]["g1_files"] = [{"path": relative, "sha256": digest}]
    summary["reference_check"] = asdict(reference)
    write(grading / "summary.json", summary)
    meta = _RunMetadata(_load_run_bundle(run))
    assert meta.summary["gates"]["G3"]["passed"] is not True
    assert "Grading outcome: Validated" not in _render(run)
    assert "fewer normalized tokens" not in _readme(run)
