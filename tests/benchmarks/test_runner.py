from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from benchmarks.runner import BenchmarkConfig, BenchmarkValidationError, run_benchmark


def _write_yaml(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
    return path


def _corpus(tmp_path: Path, questions: list[dict] | None = None) -> Path:
    return _write_yaml(
        tmp_path / "corpus.yml",
        {
            "questions": questions
            or [
                {
                    "id": "q001",
                    "category": "exact-symbol",
                    "python_version": "3.13",
                    "prompt": "What does pathlib.Path.read_text return?",
                }
            ]
        },
    )


def _manifest(tmp_path: Path, competitors: list[dict] | None = None) -> Path:
    return _write_yaml(
        tmp_path / "competitors.yml",
        {
            "competitors": competitors
            or [
                {
                    "id": "no-mcp",
                    "name": "No MCP baseline",
                    "adapter": "no-mcp-baseline",
                }
            ]
        },
    )


def test_runner_writes_stable_artifact_paths(tmp_path: Path) -> None:
    out_dir = tmp_path / "results" / "run-1"

    summary = run_benchmark(
        BenchmarkConfig(
            corpus_path=_corpus(tmp_path),
            manifest_path=_manifest(tmp_path),
            out_dir=out_dir,
            run_id="run-1",
        )
    )

    assert summary["planned_cells"] == 1
    assert summary["succeeded_cells"] == 1
    assert (out_dir / "snapshots" / "competitor-manifest.yml").is_file()
    assert (out_dir / "snapshots" / "corpus.yml").is_file()
    assert (out_dir / "environment.json").is_file()
    assert (out_dir / "planned-cells.json").is_file()
    assert (out_dir / "run-summary.json").is_file()
    assert (out_dir / "transcripts" / "no-mcp" / "q001.json").is_file()
    assert (out_dir / "tokens" / "no-mcp" / "q001.json").is_file()
    assert (out_dir / "latency" / "no-mcp" / "q001.json").is_file()
    assert (out_dir / "scoring" / "no-mcp" / "q001.json").is_file()


def test_environment_metadata_captures_repo_commit_and_python(tmp_path: Path) -> None:
    out_dir = tmp_path / "results" / "metadata"

    run_benchmark(
        BenchmarkConfig(
            corpus_path=_corpus(tmp_path),
            manifest_path=_manifest(tmp_path),
            out_dir=out_dir,
            run_id="metadata",
        )
    )

    metadata = json.loads((out_dir / "environment.json").read_text(encoding="utf-8"))
    assert metadata["run_id"] == "metadata"
    assert metadata["repo_commit"]
    assert metadata["repo_commit"] != "unknown"
    assert metadata["python_version"].startswith(f"{sys.version_info.major}.")
    assert metadata["external_provider_calls"] is False


def test_dry_run_writes_plan_without_cell_execution(tmp_path: Path) -> None:
    out_dir = tmp_path / "results" / "dry-run"

    summary = run_benchmark(
        BenchmarkConfig(
            corpus_path=_corpus(tmp_path),
            manifest_path=_manifest(tmp_path),
            out_dir=out_dir,
            run_id="dry-run",
            dry_run=True,
        )
    )

    assert summary["dry_run"] is True
    assert summary["planned_cells"] == 1
    assert summary["succeeded_cells"] == 0
    assert summary["failed_cells"] == 0
    assert (out_dir / "planned-cells.json").is_file()
    assert not (out_dir / "transcripts").exists()


def test_duplicate_corpus_ids_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(BenchmarkValidationError, match="duplicate corpus id: q001"):
        run_benchmark(
            BenchmarkConfig(
                corpus_path=_corpus(
                    tmp_path,
                    [
                        {"id": "q001", "prompt": "first"},
                        {"id": "q001", "prompt": "second"},
                    ],
                ),
                manifest_path=_manifest(tmp_path),
                out_dir=tmp_path / "out",
            )
        )


def test_missing_corpus_id_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(BenchmarkValidationError, match="missing required 'id'"):
        run_benchmark(
            BenchmarkConfig(
                corpus_path=_corpus(tmp_path, [{"prompt": "missing id"}]),
                manifest_path=_manifest(tmp_path),
                out_dir=tmp_path / "out",
            )
        )


def test_competitor_cell_failure_is_recorded(tmp_path: Path) -> None:
    out_dir = tmp_path / "results" / "failure"

    summary = run_benchmark(
        BenchmarkConfig(
            corpus_path=_corpus(tmp_path),
            manifest_path=_manifest(
                tmp_path,
                [
                    {
                        "id": "failing-baseline",
                        "adapter": "no-mcp-baseline",
                        "force_failure": True,
                    }
                ],
            ),
            out_dir=out_dir,
            run_id="failure",
        )
    )

    assert summary["failed_cells"] == 1
    failure_path = out_dir / "failures" / "failing-baseline" / "q001.json"
    transcript_path = out_dir / "transcripts" / "failing-baseline" / "q001.json"
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    assert failure["status"] == "failed"
    assert failure["error"]["message"] == "forced fake provider failure"
    assert transcript["status"] == "failed"
    assert transcript["external_provider_calls"] is False


def test_cli_dry_run_outputs_summary(tmp_path: Path) -> None:
    out_dir = tmp_path / "results" / "cli"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks",
            "run",
            "--corpus",
            str(_corpus(tmp_path)),
            "--manifest",
            str(_manifest(tmp_path)),
            "--out",
            str(out_dir),
            "--run-id",
            "cli",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0
    summary = json.loads(result.stdout)
    assert summary["dry_run"] is True
    assert summary["planned_cells"] == 1
    assert (out_dir / "run-summary.json").is_file()
