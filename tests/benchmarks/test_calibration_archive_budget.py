"""Original archive admission must agree with scoring before public replay."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from benchmarks.agentic.protocol import KeyLeakError
from benchmarks.grading import calibration_storage, publication, score
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks.test_agentic_report import (
    SCHEMA,
    _readme,
    _render,
)
from tests.benchmarks.test_agentic_report import validated_bundle as validated_bundle
from tests.benchmarks.test_agentic_report import validated_template as validated_template


def _originals(run):
    summary = json.loads((run / "grading/summary.json").read_bytes())
    (original,) = summary["disclosure"]["g1_files"]
    path = run / "grading" / original["path"]
    body = path.read_bytes()
    # Exact same evidence, distinct original UTF-8 bytes and digest.
    other = body + b"\n"
    digest = hashlib.sha256(other).hexdigest()
    second = {"sha256": digest, "path": f"disclosure/g1/{digest}.json"}
    sibling = run / "grading" / second["path"]
    sibling.write_bytes(other)
    return summary, path, sibling, original, second


def _limit(monkeypatch, limit):
    for module in (score, calibration_storage, publication):
        monkeypatch.setattr(module, "MAX_CALIBRATION_BYTES", limit, raising=False)


@pytest.mark.parametrize("projection", [True, False])
def test_two_distinct_originals_exceed_publication_budget(
    validated_bundle, monkeypatch, projection,
):
    run = validated_bundle
    summary, first, second, original, sibling = _originals(run)
    limit = max(first.stat().st_size, second.stat().st_size)
    _limit(monkeypatch, limit)
    before = (run / "grading/summary.json").read_bytes()
    with pytest.raises(BenchmarkValidationError, match="combined G1"):
        score.score_run_dir(run, schema_path=SCHEMA, g1_paths=[first, second])
    assert (run / "grading/summary.json").read_bytes() == before
    summary["disclosure"]["g1_files"] = [original, sibling]
    (run / "grading/summary.json").write_text(json.dumps(summary))
    if not projection:
        (run / "grading/disclosure/g1-entries.json").unlink()
    report = _render(run)
    readme = _readme(run)
    assert "Grading outcome: Judge not validated" in report
    assert "G1 for this run's composition: not evaluable." in report
    assert "G2 panel reliability: pass" in report
    assert "G3 reference agreement: not evaluated" in report
    assert "G4 claim robustness: pass" in report
    assert "completed LLM-graded answers: 300" in report
    assert "fewer normalized tokens" not in readme


@pytest.mark.parametrize("distinct", [False, True])
def test_exact_budget_and_duplicate_originals_publish_normally(
    validated_bundle, monkeypatch, distinct,
):
    run = validated_bundle
    _, first, second, _, _ = _originals(run)
    paths = [first, second, first] if distinct else [first, first]
    limit = first.stat().st_size + (second.stat().st_size if distinct else 0)
    if distinct:
        _limit(monkeypatch, limit - 1)
        with pytest.raises(BenchmarkValidationError, match="combined G1"):
            score.score_run_dir(run, schema_path=SCHEMA, g1_paths=paths)
    _limit(monkeypatch, limit)
    summary = score.score_run_dir(run, schema_path=SCHEMA, g1_paths=paths)
    assert summary["tier"] == "Validated"
    assert summary["graded_cells"] == 300
    assert summary["spend"]["graded_answers"] == 300
    assert all(g["passed"] is True for g in summary["gates"].values())
    # The declaration's annotation is never an additional read target.
    summary["disclosure"]["g1_files"][0]["source"] = "/unread/original.json"
    (run / "grading/summary.json").write_text(json.dumps(summary))
    report = _render(run)
    readme = _readme(run)
    assert "Grading outcome: Validated" in report
    assert "completed LLM-graded answers: 300" in report
    assert "| google | 3.00 | 0.010000 |" in report
    assert readme.count("fewer normalized tokens") == 2
    assert "ratio 0.500" in readme


@pytest.mark.parametrize("damage", ["missing", "symlink", "corrupt"])
def test_incomplete_original_set_cannot_reuse_positive_projection(
    validated_bundle, monkeypatch, damage,
):
    run = validated_bundle
    summary, first, second, original, sibling = _originals(run)
    _limit(monkeypatch, first.stat().st_size + second.stat().st_size)
    body = second.read_bytes()
    second.unlink()
    if damage == "symlink":
        target = run.parent / "redirected-original.json"
        target.write_bytes(body)  # Digest-correct if a reader incorrectly follows it.
        second.symlink_to(target)
    elif damage == "corrupt":
        second.write_bytes(b"{}")
    summary["disclosure"]["g1_files"] = [original, sibling, original]
    (run / "grading/summary.json").write_text(json.dumps(summary))
    report = _render(run)
    assert "Grading outcome: Judge not validated" in report
    assert "G1 for this run's composition: not evaluable." in report
    assert "G2 panel reliability: pass" in report
    assert "G4 claim robustness: pass" in report
    assert "fewer normalized tokens" not in _readme(run)


def test_credential_error_in_admitted_original_remains_fatal(validated_bundle):
    run = validated_bundle
    summary, _, second, original, _ = _originals(run)
    marker = "A" + "Iza" + "g" * 35
    value = json.loads(second.read_bytes())
    value["annotation"] = marker
    body = json.dumps(value).replace(marker, "\\u0041" + marker[1:]).encode()
    digest = hashlib.sha256(body).hexdigest()
    second = second.with_name(f"{digest}.json")
    second.write_bytes(body)
    summary["disclosure"]["g1_files"] = [original, {
        "sha256": digest, "path": f"disclosure/g1/{digest}.json",
    }]
    (run / "grading/summary.json").write_text(json.dumps(summary))
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in run.rglob("*") if p.is_file()}
    with pytest.raises(KeyLeakError):
        score.score_run_dir(run, schema_path=SCHEMA, g1_paths=[second])
    for render in (_render, _readme):
        with pytest.raises(KeyLeakError):
            render(run)
    assert {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before} == before


def test_over_budget_original_is_refused_before_read_or_parse(tmp_path, monkeypatch):
    # A sparse regular file has no retained large raw fixture and cannot reach
    # the allocating parser, even when its digest-shaped declaration is valid.
    digest = "0" * 64
    path = tmp_path / f"grading/disclosure/g1/{digest}.json"
    path.parent.mkdir(parents=True)
    with path.open("wb") as stream:
        stream.truncate(101)
    _limit(monkeypatch, 100)
    run = SimpleNamespace(bundle=SimpleNamespace(run_dir=tmp_path), summary={
        "disclosure": {"g1_files": [{
            "sha256": digest, "path": f"disclosure/g1/{digest}.json",
        }]},
    })

    def no_parse(*args, **kwargs):
        pytest.fail("inadmissible original reached the allocating parser")

    # Refusal must happen before even the digest read, not just at JSON replay.
    from benchmarks.agentic import egress

    with monkeypatch.context() as guard:
        guard.setattr(publication, "g1_entries", no_parse)
        guard.setattr(egress.os, "read", no_parse)
        archives, problem = publication._calibration_archives(run)
    assert archives == {} and problem is not None
    path.unlink()


@pytest.mark.parametrize("fallback", [False, True])
def test_remaining_budget_stops_second_original_before_body_allocation(
    tmp_path, monkeypatch, fallback,
):
    from benchmarks.agentic import egress

    raw = tmp_path / "grading/disclosure/g1"
    raw.mkdir(parents=True)
    bodies = (b'{"entries":{}}', b'{"entries":{}}\n')
    copies = []
    for body in bodies:
        digest = hashlib.sha256(body).hexdigest()
        (raw / f"{digest}.json").write_bytes(body)
        copies.append({"sha256": digest, "path": f"disclosure/g1/{digest}.json"})
    # Both files fit individually; together they exceed admission by one byte.
    _limit(monkeypatch, sum(map(len, bodies)) - 1)
    run = SimpleNamespace(bundle=SimpleNamespace(run_dir=tmp_path), summary={
        "disclosure": {"g1_files": copies + copies},
    })
    second = raw / (sorted(c["sha256"] for c in copies)[1] + ".json")
    inode = second.stat().st_ino
    real_read, real_parse = egress.os.read, publication.g1_entries
    parsed = []

    def bounded_read(fd, size):
        assert egress.os.fstat(fd).st_ino != inode, "over-budget original body was read"
        return real_read(fd, size)

    def parse_once(body):
        assert not parsed, "over-budget original reached the allocating parser"
        parsed.append(body)
        return real_parse(body)

    with monkeypatch.context() as guard:
        guard.setattr(egress.os, "read", bounded_read)
        guard.setattr(publication, "g1_entries", parse_once)
        if fallback:
            guard.setattr(egress.os, "supports_dir_fd", set())
        archives, problem = publication._calibration_archives(run)
    assert len(parsed) == 1
    assert archives == {} and problem is not None
