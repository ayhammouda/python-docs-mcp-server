"""Explicit agentic scoring must never downgrade to the legacy writer."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from benchmarks.grading.score import score_run_dir
from benchmarks.runner import BenchmarkValidationError
from benchmarks.scoring import score_run
from tests.benchmarks import test_grading_score as runs
from tests.benchmarks import test_scoring_llm as cells
from tests.benchmarks.test_scoring_confinement import _snapshot


@pytest.mark.parametrize("entry", ["library", "cli"])
@pytest.mark.parametrize("mode", [None, "fixed"])
def test_explicit_agentic_score_refuses_downgrade_before_any_write(
    tmp_path: Path, entry: str, mode: str | None
) -> None:
    run = runs._build_run(tmp_path)
    environment = {"seed": 1234}
    if mode is not None:
        environment["mode"] = mode
    (run / "environment.json").write_text(json.dumps(environment))
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "queue.json"
    sentinel.write_bytes(b"owned external sentinel\n")
    (run / "adjudication").symlink_to(outside, target_is_directory=True)
    before = _snapshot(tmp_path)
    if entry == "cli":
        result = subprocess.run(
            [sys.executable, "-m", "benchmarks", "agentic-score", "--run-dir", str(run)],
            capture_output=True,
            text=True,
            check=False,
        )
        rejected = result.returncode == 2
    else:
        try:
            score_run_dir(run, schema_path=runs.SCHEMA)
        except BenchmarkValidationError:
            rejected = True
        else:
            rejected = False
    assert (rejected, sentinel.read_bytes(), _snapshot(tmp_path)) == (
        True,
        b"owned external sentinel\n",
        before,
    )


@pytest.mark.parametrize(
    "body",
    [b"", b"null", b"[]", b"{}", b'{"mode":null}', b'{"mode":[]}', b'{"mode":"fixed"}'],
)
def test_invalid_agentic_environment_stops_before_reading_grading_inputs(
    tmp_path: Path, body: bytes
) -> None:
    (tmp_path / "environment.json").write_bytes(body)
    before = _snapshot(tmp_path)
    with pytest.raises(BenchmarkValidationError, match="environment.json"):
        score_run_dir(tmp_path, schema_path=runs.SCHEMA)
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("body", [b"null", b"{}", b'{"mode":"fixed"}'])
def test_present_summary_must_agree_with_agentic_environment(tmp_path: Path, body: bytes) -> None:
    run = runs._build_run(tmp_path)
    (run / "run-summary.json").write_bytes(body)
    before = _snapshot(tmp_path)
    with pytest.raises(BenchmarkValidationError, match="run-summary.json"):
        score_run_dir(run, schema_path=runs.SCHEMA)
    assert _snapshot(tmp_path) == before


def test_explicit_replay_handoff_uses_confined_writer_independent_of_mode(tmp_path: Path) -> None:
    run = cells._run(tmp_path, {runs.QUESTIONS[0]: "an answer"}, mode="fixed")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "queue.json"
    sentinel.write_bytes(b"owned sentinel")
    (run / "adjudication").symlink_to(outside, target_is_directory=True)
    before = _snapshot(tmp_path)
    with pytest.raises(BenchmarkValidationError):
        score_run(run, schema_path=cells.SCHEMA, current_llm_cells=set())
    assert _snapshot(tmp_path) == before
