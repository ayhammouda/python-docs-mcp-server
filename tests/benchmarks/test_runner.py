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
    assert summary["correctness_denominator_cells"] == 1
    assert summary["scored_cells"] == 1
    assert summary["failed_cells_included_in_correctness_denominator"] is True
    failure_path = out_dir / "failures" / "failing-baseline" / "q001.json"
    transcript_path = out_dir / "transcripts" / "failing-baseline" / "q001.json"
    scoring_path = out_dir / "scoring" / "failing-baseline" / "q001.json"
    latency_path = out_dir / "latency" / "failing-baseline" / "q001.json"
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    scoring = json.loads(scoring_path.read_text(encoding="utf-8"))
    latency = json.loads(latency_path.read_text(encoding="utf-8"))
    assert failure["status"] == "failed"
    assert failure["error"]["category"] == "tool_failure"
    assert failure["error"]["message"] == "forced fake provider tool_failure"
    assert failure["correctness_score"] == 0.0
    assert failure["included_in_correctness_denominator"] is True
    assert transcript["status"] == "failed"
    assert transcript["external_provider_calls"] is False
    assert scoring["status"] == "failed"
    assert scoring["score"] == 0.0
    assert scoring["requires_manual_scoring"] is False
    assert scoring["included_in_correctness_denominator"] is True
    assert scoring["error_category"] == "tool_failure"
    assert latency["error_category"] == "tool_failure"


def test_timeout_and_mcp_protocol_crash_failures_are_classified(tmp_path: Path) -> None:
    out_dir = tmp_path / "results" / "classified-failures"

    summary = run_benchmark(
        BenchmarkConfig(
            corpus_path=_corpus(tmp_path),
            manifest_path=_manifest(
                tmp_path,
                [
                    {
                        "id": "timeout-baseline",
                        "adapter": "no-mcp-baseline",
                        "model": "fake-model",
                        "force_failure": "timeout",
                    },
                    {
                        "id": "crash-baseline",
                        "adapter": "no-mcp-baseline",
                        "provider": "fake-provider",
                        "model": "fake-model",
                        "force_failure": "mcp_protocol_crash",
                    },
                ],
            ),
            out_dir=out_dir,
            run_id="classified-failures",
        )
    )

    assert summary["failed_cells"] == 2
    assert summary["correctness_denominator_cells"] == 2

    timeout_scoring = json.loads(
        (out_dir / "scoring" / "timeout-baseline" / "q001.json").read_text(encoding="utf-8")
    )
    crash_failure = json.loads(
        (out_dir / "failures" / "crash-baseline" / "q001.json").read_text(encoding="utf-8")
    )
    assert timeout_scoring["score"] == 0.0
    assert timeout_scoring["error_category"] == "timeout"
    assert timeout_scoring["tool_model_key"] == "timeout-baseline:fake-model"
    assert crash_failure["error"]["category"] == "mcp_protocol_crash"
    assert crash_failure["tool_model_key"] == "crash-baseline:fake-provider/fake-model"
    assert crash_failure["correctness_score"] == 0.0


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
