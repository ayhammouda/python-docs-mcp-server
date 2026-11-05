"""Disposable-file controls for standalone scoring publication boundaries."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

import pytest

from benchmarks.grading.score import score_run_dir
from benchmarks.runner import BenchmarkValidationError
from benchmarks.scoring import apply_llm_grades, score_run
from tests.benchmarks import test_grading_score as runs
from tests.benchmarks import test_scoring_llm as cells

FIRST, SECOND = runs.QUESTIONS


def _score(run: Path, entry: str) -> None:
    if entry == "score":
        score_run(run, schema_path=cells.SCHEMA, current_llm_cells=set())
    elif entry == "apply":
        apply_llm_grades(
            run,
            {(cells.CELL, FIRST): cells._grade(0.5)},
            grounded={(cells.CELL, FIRST): True},
            schema_path=cells.SCHEMA,
        )
    else:
        score_run_dir(run, schema_path=runs.SCHEMA)


def _run(tmp_path: Path, entry: str) -> Path:
    if entry == "agentic":
        run = runs._build_run(tmp_path)
    else:
        run = cells._run(tmp_path, {FIRST: "answer", SECOND: "another answer"})
    _score(run, entry)
    return run


def _snapshot(root: Path) -> dict[str, tuple[bytes | str, int, int]]:
    """Record links themselves; never traverse an adversarial directory."""
    result = {}
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in [*dirs, *files]:
            path = Path(base) / name
            info = path.lstat()
            value = (
                os.readlink(path)
                if path.is_symlink()
                else (path.read_bytes() if path.is_file() else b"directory")
            )
            result[str(path.relative_to(root))] = (value, info.st_mtime_ns, info.st_mode)
    return result


@pytest.mark.parametrize("entry", ["score", "apply", "agentic"])
@pytest.mark.parametrize(
    "target",
    [
        "cell",
        "queue",
        "rollups",
        "scoring-dir",
        "queue-dir",
        "scoring-root",
        "transcript",
        "transcripts-dir",
    ],
)
def test_redirect_refusal_preserves_entire_run_and_retry(
    tmp_path: Path, entry: str, target: str
) -> None:
    run = _run(tmp_path, entry)
    if entry == "apply":
        apply_llm_grades(
            run,
            {(cells.CELL, FIRST): cells._grade(1.0)},
            grounded={},
            schema_path=cells.SCHEMA,
        )
        assert cells._record(run, FIRST)["grounded"] is False
    cell = runs._cell("python-docs") if entry == "agentic" else cells.CELL
    path = {
        "cell": run / "scoring" / cell / f"{FIRST}.json",
        "queue": run / "adjudication/queue.json",
        "rollups": run / "scoring-rollups.json",
        "scoring-dir": run / "scoring" / cell,
        "scoring-root": run / "scoring",
        "queue-dir": run / "adjudication",
        "transcript": run / "transcripts" / cell / f"{SECOND}.json",
        "transcripts-dir": run / "transcripts" / cell,
    }[target]
    outside = tmp_path / "outside"
    path.rename(outside)
    path.symlink_to(outside, target_is_directory=outside.is_dir())
    before, external = _snapshot(run), _snapshot(tmp_path)
    for _ in range(2):
        with pytest.raises(BenchmarkValidationError):
            _score(run, entry)
        assert _snapshot(run) == before
        assert _snapshot(tmp_path) == external
    path.unlink()
    outside.rename(path)
    _score(run, entry)
    if entry == "apply":
        assert cells._record(run, FIRST)["grounded"] is True
        assert cells._record(run, FIRST)["score"] == 0.5
    elif entry == "score":
        assert cells._record(run, FIRST)["score"] is None
    else:
        summary = json.loads((run / "grading/summary.json").read_bytes())
        assert summary["graded_cells"] > 0


@pytest.mark.parametrize("entry", ["score", "apply"])
@pytest.mark.parametrize("coordinate", ["../outside", "absolute", ".hidden", "wrong-cell"])
def test_cell_coordinates_are_bound_to_safe_artifact_path(
    tmp_path: Path, entry: str, coordinate: str
) -> None:
    run = _run(tmp_path, entry)
    path = run / "scoring" / cells.CELL / f"{SECOND}.json"
    data = json.loads(path.read_bytes())
    data["competitor_id"] = str(tmp_path / "outside") if coordinate == "absolute" else coordinate
    path.write_text(json.dumps(data))
    before = _snapshot(run)
    with pytest.raises(BenchmarkValidationError):
        _score(run, entry)
    assert _snapshot(run) == before


@pytest.mark.parametrize("entry", ["score", "apply"])
@pytest.mark.parametrize("target", ["cell", "queue", "rollups"])
def test_hardlinked_outputs_replace_name_without_changing_external_inode(
    tmp_path: Path, entry: str, target: str
) -> None:
    run = _run(tmp_path, entry)
    path = {
        "cell": run / "scoring" / cells.CELL / f"{FIRST}.json",
        "queue": run / "adjudication/queue.json",
        "rollups": run / "scoring-rollups.json",
    }[target]
    outside = tmp_path / "outside"
    os.link(path, outside)
    prior = (outside.read_bytes(), outside.stat().st_mtime_ns, outside.stat().st_ino)
    _score(run, entry)
    assert (outside.read_bytes(), outside.stat().st_mtime_ns, outside.stat().st_ino) == prior
    assert path.stat().st_ino != outside.stat().st_ino
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    for output in (path, run / "adjudication/queue.json", run / "scoring-rollups.json"):
        data = json.loads(output.read_bytes())
        assert output.read_bytes() == (json.dumps(data, indent=2, sort_keys=True) + "\n").encode()
    assert not list(run.rglob("*.tmp"))


@pytest.mark.parametrize("entry", ["score", "apply"])
def test_failed_atomic_publication_keeps_old_cell_and_retry_succeeds(
    tmp_path: Path, entry: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _run(tmp_path, entry)
    if entry == "apply":
        apply_llm_grades(
            run,
            {(cells.CELL, FIRST): cells._grade(1.0)},
            grounded={},
            schema_path=cells.SCHEMA,
        )
        assert cells._record(run, FIRST)["score"] == 1.0
        assert cells._record(run, FIRST)["grounded"] is False
    else:
        path = run / "transcripts" / cells.CELL / f"{FIRST}.json"
        data = json.loads(path.read_bytes())
        data["answer"] = ""
        path.write_text(json.dumps(data))
        assert cells._record(run, FIRST)["score"] is None
    before = _snapshot(run)
    replace = os.replace

    def fail_replace(*args: object, **kwargs: object) -> None:
        raise OSError("disposable publication failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="disposable publication failure"):
        _score(run, entry)
    # Atomic staging may change directory mtimes, but no output bytes or modes.
    assert {p: (v[0], v[2]) for p, v in _snapshot(run).items()} == {
        p: (v[0], v[2]) for p, v in before.items()
    }
    monkeypatch.setattr(os, "replace", replace)
    _score(run, entry)
    if entry == "apply":
        assert cells._record(run, FIRST)["grounded"] is True
        assert cells._record(run, FIRST)["score"] == 0.5
    else:
        assert cells._record(run, FIRST)["score"] == 0.0


@pytest.mark.parametrize(
    "target", ["summary.json", "disclosure/breakdowns.json", "disclosure/tuning.json"]
)
def test_grading_destinations_are_preflighted_before_scoring(tmp_path: Path, target: str) -> None:
    run = _run(tmp_path, "agentic")
    path = run / "grading" / target
    outside = tmp_path / "outside"
    outside.write_bytes(path.read_bytes() if path.exists() else b"benign outside file")
    path.unlink(missing_ok=True)
    path.symlink_to(outside)
    before = _snapshot(tmp_path)
    with pytest.raises(BenchmarkValidationError):
        _score(run, "agentic")
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("entry", ["score", "apply"])
def test_nonregular_late_output_is_rejected_before_any_change(tmp_path: Path, entry: str) -> None:
    run = _run(tmp_path, entry)
    path = run / "scoring-rollups.json"
    path.unlink()
    path.mkdir()
    before = _snapshot(run)
    with pytest.raises(BenchmarkValidationError):
        _score(run, entry)
    assert _snapshot(run) == before


def test_queue_parent_rejection_does_not_create_missing_scoring_outputs(tmp_path: Path) -> None:
    run = cells._run(tmp_path, {FIRST: "answer"})
    outside = tmp_path / "outside"
    outside.mkdir()
    (run / "adjudication").symlink_to(outside, target_is_directory=True)
    before = _snapshot(tmp_path)
    with pytest.raises(BenchmarkValidationError):
        _score(run, "score")
    assert _snapshot(tmp_path) == before


def test_selected_run_symlink_is_refused(tmp_path: Path) -> None:
    run = _run(tmp_path, "apply")
    alias = tmp_path / "alias"
    alias.symlink_to(run, target_is_directory=True)
    before = _snapshot(tmp_path)
    with pytest.raises(BenchmarkValidationError):
        _score(alias, "apply")
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("kind", ["dangling", "fifo"])
@pytest.mark.parametrize("entry", ["score", "apply"])
def test_dangling_or_nonregular_output_never_opens_target(
    tmp_path: Path, entry: str, kind: str
) -> None:
    run = _run(tmp_path, entry)
    output = run / "scoring-rollups.json"
    output.unlink()
    if kind == "dangling":
        output.symlink_to(tmp_path / "absent-outside")
    else:
        os.mkfifo(output)
    before = (output.lstat(), (run / "adjudication/queue.json").read_bytes())
    with pytest.raises(BenchmarkValidationError):
        _score(run, entry)
    assert output.lstat() == before[0]
    assert (run / "adjudication/queue.json").read_bytes() == before[1]
    assert not (tmp_path / "absent-outside").exists()


def test_opened_parent_walk_refuses_redirect_installed_after_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import benchmarks.report_files as files

    run = _run(tmp_path, "apply")
    parent = run / "adjudication"
    outside = tmp_path / "outside"
    check = files.check_output_paths
    original = (parent / "queue.json").read_bytes()

    scoring_before = _snapshot(run / "scoring")

    def redirect(root: Path, paths: Iterable[Path]) -> None:
        check(root, paths)
        parent.rename(outside)
        parent.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(files, "check_output_paths", redirect)
    # The descriptor walk, not only the path preflight, must prevent this write.
    with pytest.raises(BenchmarkValidationError):
        _score(run, "apply")
    assert (outside / "queue.json").read_bytes() == original
    assert _snapshot(run / "scoring") == scoring_before


def test_fixed_flow_import_does_not_require_posix_publication_flags() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import os; del os.O_NOFOLLOW; import benchmarks.scoring"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
