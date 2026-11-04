"""Companion controls for the other derived outputs reachable from agentic-score."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from benchmarks.grading.score import (
    assemble_workspace_probes,
    score_run_dir,
    write_g1,
    write_tuning,
)
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks import test_grading_score as runs
from tests.benchmarks.test_grading_probes import index_db as index_db
from tests.benchmarks.test_scoring_confinement import _snapshot


def test_real_local_producers_refuse_redirects_and_preserve_hardlink_targets(
    tmp_path: Path, index_db: Path
) -> None:
    # This fixture invokes the actual probe, tuning, G1 and scoring producers with
    # recorded synthetic votes. No fabricated summary/gate overrides are used.
    run, held, _, criteria = runs._full_calibration(tmp_path, index_db, False)
    producers: list[tuple[Path, Callable[[], object]]] = [
        (
            tmp_path / "build/probes.yml",
            lambda: assemble_workspace_probes(
                tmp_path / "build",
                criteria_path=criteria,
                tuning_path=runs.TUNING_PATH,
                panel_path=runs.PANEL_PATH,
                index_path=index_db,
                corpus_path=tmp_path / "corpus.yml",
            ),
        ),
        (tmp_path / "tune/tuning.json", lambda: write_tuning(tmp_path / "tune")),
        (held / "g1.json", lambda: write_g1(held)),
    ]
    for path, produce in producers:
        outside = tmp_path / "outside"
        path.rename(outside)
        original = outside.read_bytes()
        path.symlink_to(outside)
        before = _snapshot(tmp_path)
        with pytest.raises(BenchmarkValidationError):
            produce()
        assert _snapshot(tmp_path) == before
        path.unlink()
        os.link(outside, path)
        produce()
        assert outside.read_bytes() == original
        assert path.stat().st_ino != outside.stat().st_ino
        assert path.read_bytes() == original
        outside.unlink()
    # Archived G1 bytes and every disclosure leaf are covered before scoring.
    for path in [
        run / "grading/disclosure/g1-entries.json",
        *sorted((run / "grading/disclosure/g1").glob("*.json")),
    ]:
        outside = tmp_path / "outside"
        path.rename(outside)
        path.symlink_to(outside)
        before = _snapshot(tmp_path)
        with pytest.raises(BenchmarkValidationError):
            score_run_dir(run, schema_path=runs.SCHEMA, g1_paths=[held / "g1.json"])
        assert _snapshot(tmp_path) == before
        path.unlink()
        outside.rename(path)
    result = score_run_dir(run, schema_path=runs.SCHEMA, g1_paths=[held / "g1.json"])
    assert result["gates"]["G1"]["passed"] is True
