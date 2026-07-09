from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from benchmarks.report import generate_readme_summary, generate_report
from benchmarks.runner import BenchmarkConfig, BenchmarkValidationError, run_benchmark

REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_MATRIX_PATH = REPO_ROOT / "docs" / "benchmarks" / "model-matrix.yml"
METHODOLOGY_PATH = REPO_ROOT / "docs" / "benchmarks" / "PUBLIC-BENCHMARK-METHODOLOGY.md"


def _write_yaml(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
    return path


def _write_json(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
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
                    "prompt": "What does pathlib.Path.read_text return?",
                },
                {
                    "id": "q002",
                    "category": "concept",
                    "prompt": "What is a context manager?",
                },
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
                    "id": "no-mcp-baseline",
                    "name": "No MCP baseline",
                    "adapter": "no-mcp-baseline",
                    "model": "gpt-4o-mini",
                },
                {
                    "id": "python-docs-mcp-server",
                    "name": "python-docs-mcp-server",
                    "adapter": "no-mcp-baseline",
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                },
            ]
        },
    )


def _generate_report(run_dir: Path, **overrides: Path) -> Path:
    kwargs = {"model_matrix_path": MODEL_MATRIX_PATH, "methodology_path": METHODOLOGY_PATH}
    kwargs.update(overrides)
    return generate_report(run_dir, **kwargs)


def _run(
    tmp_path: Path,
    out_dir: Path,
    *,
    competitors: list[dict] | None = None,
    run_id: str = "r1",
) -> dict:
    return run_benchmark(
        BenchmarkConfig(
            corpus_path=_corpus(tmp_path),
            manifest_path=_manifest(tmp_path, competitors),
            out_dir=out_dir,
            run_id=run_id,
        )
    )


# --- Report generation from a fixture artifact tree -------------------------


def test_generate_report_includes_all_required_sections(tmp_path: Path) -> None:
    out_dir = tmp_path / "results" / "run-report"
    _run(
        tmp_path,
        out_dir,
        competitors=[
            {
                "id": "no-mcp-baseline",
                "adapter": "no-mcp-baseline",
                "model": "gpt-4o-mini",
            },
            {
                "id": "python-docs-mcp-server",
                "adapter": "no-mcp-baseline",
                "provider": "openai",
                "model": "gpt-4o-mini",
            },
            {
                "id": "flaky-competitor",
                "adapter": "no-mcp-baseline",
                "provider": "openai",
                "model": "gpt-4o",
                "force_failure": "timeout",
            },
        ],
        run_id="run-report",
    )

    report_path = generate_report(
        out_dir, model_matrix_path=MODEL_MATRIX_PATH, methodology_path=METHODOLOGY_PATH
    )

    assert report_path == out_dir / "REPORT.md"
    text = report_path.read_text(encoding="utf-8")

    # Methodology link + repo commit + corpus hash.
    assert "PUBLIC-BENCHMARK-METHODOLOGY.md" in text
    expected_hash = hashlib.sha256((out_dir / "snapshots" / "corpus.yml").read_bytes()).hexdigest()
    assert expected_hash in text
    assert "Repo commit" in text

    # Model/client matrix (from the real docs/benchmarks/model-matrix.yml).
    assert "Model / Client Matrix" in text
    assert "openai-gpt-4o-mini" in text
    assert "google-gemini-1.5-flash" in text

    # Competitor manifest.
    assert "Competitor Manifest" in text
    assert "flaky-competitor" in text
    assert "python-docs-mcp-server" in text

    # Correctness by category, split per tool_model_key -- never tool-only.
    assert "Correctness By Category" in text
    assert "exact-symbol" in text
    assert "concept" in text
    assert "no-mcp-baseline:gpt-4o-mini" in text
    assert "python-docs-mcp-server:openai/gpt-4o-mini" in text

    # Token counts: placeholders surfaced honestly, never as zero.
    assert "Token Counts" in text
    assert "None (placeholder" in text

    # Latency median/p95.
    assert "Latency (Median / p95)" in text

    # Environment metadata.
    assert "Environment Metadata" in text
    assert "python_version" in text

    # Failures/exclusions -- failed competitor disclosed, not dropped silently.
    assert "Failures And Exclusions" in text
    assert "flaky-competitor:openai/gpt-4o" in text
    assert "timeout" in text


def test_generate_readme_summary_uses_strict_tool_model_pairings(tmp_path: Path) -> None:
    out_dir = tmp_path / "results" / "run-summary-block"
    _run(tmp_path, out_dir, run_id="run-summary-block")

    summary_path = generate_readme_summary(out_dir, methodology_path=METHODOLOGY_PATH)

    assert summary_path == out_dir / "README-SUMMARY.md"
    text = summary_path.read_text(encoding="utf-8")

    assert "not a README edit" in text
    assert "PUBLIC-BENCHMARK-METHODOLOGY.md" in text
    assert "Raw result bundle" in text
    # Every row is a strict tool_model_key pairing (contains ':'), never a
    # tool-only row.
    assert "no-mcp-baseline:gpt-4o-mini" in text
    assert "python-docs-mcp-server:openai/gpt-4o-mini" in text
    assert "Error Rate" in text
    assert "Timeout Rate" in text


def test_cli_report_subcommand_writes_both_files(tmp_path: Path) -> None:
    out_dir = tmp_path / "results" / "cli-report"
    _run(tmp_path, out_dir, run_id="cli-report")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks",
            "report",
            "--run-dir",
            str(out_dir),
            "--model-matrix",
            str(MODEL_MATRIX_PATH),
            "--methodology",
            str(METHODOLOGY_PATH),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert Path(payload["report_path"]) == out_dir / "REPORT.md"
    assert Path(payload["readme_summary_path"]) == out_dir / "README-SUMMARY.md"
    assert (out_dir / "REPORT.md").is_file()
    assert (out_dir / "README-SUMMARY.md").is_file()


# --- Refusal: missing metadata / corpus hash / raw result files -------------


def _minimal_valid_run_dir(tmp_path: Path) -> Path:
    """Build a hand-authored minimal-but-valid run directory under tmp_path."""
    run_dir = tmp_path / "run"
    _write_json(
        run_dir / "run-summary.json",
        {
            "run_id": "minimal",
            "repo_commit": "deadbeef",
            "artifact_root": str(run_dir),
            "competitors": ["tool-a"],
            "corpus_ids": ["q001"],
            "planned_cells": 1,
            "scored_cells": 1,
            "succeeded_cells": 1,
            "failed_cells": 0,
        },
    )
    _write_json(
        run_dir / "environment.json",
        {
            "run_id": "minimal",
            "created_at": "2026-07-08T00:00:00Z",
            "repo_commit": "deadbeef",
            "python_version": "3.12.0",
            "platform": "test-platform",
            "system": "Test",
            "machine": "x86_64",
        },
    )
    _write_yaml(
        run_dir / "snapshots" / "corpus.yml",
        {"questions": [{"id": "q001", "category": "exact-symbol", "prompt": "p"}]},
    )
    _write_yaml(
        run_dir / "snapshots" / "competitor-manifest.yml",
        {"competitors": [{"id": "tool-a", "adapter": "no-mcp-baseline"}]},
    )
    _write_json(
        run_dir / "scoring" / "tool-a" / "q001.json",
        {
            "competitor_id": "tool-a",
            "corpus_id": "q001",
            "tool_model_key": "tool-a",
            "status": "succeeded",
            "score": None,
            "requires_manual_scoring": True,
            "included_in_correctness_denominator": True,
            "error_category": None,
        },
    )
    _write_json(
        run_dir / "latency" / "tool-a" / "q001.json",
        {"competitor_id": "tool-a", "corpus_id": "q001", "latency_ms": 12.5, "status": "succeeded"},
    )
    _write_json(
        run_dir / "tokens" / "tool-a" / "q001.json",
        {
            "competitor_id": "tool-a",
            "corpus_id": "q001",
            "client_wrapped_tokens": None,
            "raw_payload_tokens": None,
        },
    )
    return run_dir


def test_generate_report_refuses_when_run_summary_is_missing(tmp_path: Path) -> None:
    run_dir = _minimal_valid_run_dir(tmp_path)
    (run_dir / "run-summary.json").unlink()

    with pytest.raises(BenchmarkValidationError, match="run-summary.json"):
        _generate_report(run_dir)


def test_generate_report_refuses_when_environment_metadata_is_missing(tmp_path: Path) -> None:
    run_dir = _minimal_valid_run_dir(tmp_path)
    (run_dir / "environment.json").unlink()

    with pytest.raises(BenchmarkValidationError, match="environment.json"):
        _generate_report(run_dir)


def test_generate_report_refuses_when_environment_metadata_field_is_missing(tmp_path: Path) -> None:
    run_dir = _minimal_valid_run_dir(tmp_path)
    data = json.loads((run_dir / "environment.json").read_text(encoding="utf-8"))
    del data["repo_commit"]
    (run_dir / "environment.json").write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(BenchmarkValidationError, match="repo_commit"):
        _generate_report(run_dir)


def test_generate_report_refuses_when_corpus_hash_source_is_missing(tmp_path: Path) -> None:
    run_dir = _minimal_valid_run_dir(tmp_path)
    (run_dir / "snapshots" / "corpus.yml").unlink()

    with pytest.raises(BenchmarkValidationError, match="corpus snapshot"):
        _generate_report(run_dir)


def test_generate_report_refuses_when_manifest_snapshot_is_missing(tmp_path: Path) -> None:
    run_dir = _minimal_valid_run_dir(tmp_path)
    (run_dir / "snapshots" / "competitor-manifest.yml").unlink()

    with pytest.raises(BenchmarkValidationError, match="competitor manifest snapshot"):
        _generate_report(run_dir)


def test_generate_report_refuses_when_raw_scoring_files_are_missing(tmp_path: Path) -> None:
    run_dir = _minimal_valid_run_dir(tmp_path)
    (run_dir / "scoring" / "tool-a" / "q001.json").unlink()

    with pytest.raises(BenchmarkValidationError, match="required raw result files are missing"):
        _generate_report(run_dir)


def test_generate_report_refuses_on_dry_run_directory(tmp_path: Path) -> None:
    # A --dry-run artifact tree has no per-cell result files at all.
    out_dir = tmp_path / "results" / "dry-run"
    run_benchmark(
        BenchmarkConfig(
            corpus_path=_corpus(tmp_path),
            manifest_path=_manifest(tmp_path),
            out_dir=out_dir,
            run_id="dry-run",
            dry_run=True,
        )
    )

    with pytest.raises(BenchmarkValidationError, match="required raw result files are missing"):
        _generate_report(out_dir)


def test_generate_readme_summary_refuses_same_as_report(tmp_path: Path) -> None:
    # The acceptance criterion is specifically about the README-ready block:
    # it must refuse when required metadata/corpus-hash/raw files are
    # missing. Exercise that refusal directly against generate_readme_summary.
    run_dir = _minimal_valid_run_dir(tmp_path)
    (run_dir / "scoring" / "tool-a" / "q001.json").unlink()

    with pytest.raises(BenchmarkValidationError, match="required raw result files are missing"):
        generate_readme_summary(run_dir, methodology_path=METHODOLOGY_PATH)


def test_generate_report_refuses_when_methodology_file_is_missing(tmp_path: Path) -> None:
    run_dir = _minimal_valid_run_dir(tmp_path)

    with pytest.raises(BenchmarkValidationError, match="methodology file"):
        generate_report(
            run_dir, model_matrix_path=MODEL_MATRIX_PATH, methodology_path=tmp_path / "missing.md"
        )


def test_generate_report_refuses_when_run_dir_does_not_exist(tmp_path: Path) -> None:
    with pytest.raises(BenchmarkValidationError, match="does not exist"):
        _generate_report(tmp_path / "nope")


# --- Failed-competitor disclosure -------------------------------------------


def test_failed_competitor_is_disclosed_not_dropped(tmp_path: Path) -> None:
    out_dir = tmp_path / "results" / "failure-disclosure"
    _run(
        tmp_path,
        out_dir,
        competitors=[
            {
                "id": "crashy",
                "adapter": "no-mcp-baseline",
                "provider": "openai",
                "model": "gpt-4o",
                "force_failure": "mcp_protocol_crash",
            }
        ],
        run_id="failure-disclosure",
    )

    report_text = generate_report(
        out_dir, model_matrix_path=MODEL_MATRIX_PATH, methodology_path=METHODOLOGY_PATH
    ).read_text(encoding="utf-8")
    summary_text = generate_readme_summary(
        out_dir, methodology_path=METHODOLOGY_PATH
    ).read_text(encoding="utf-8")

    assert "crashy:openai/gpt-4o" in report_text
    assert "mcp_protocol_crash" in report_text
    # Error/timeout rate columns exist so a fully-failing tool cannot hide
    # behind an empty correctness table.
    assert "100.0%" in report_text  # error rate for the all-failing tool_model_key
    assert "crashy:openai/gpt-4o" in summary_text
    assert "100.0%" in summary_text


# --- Aggregate vs per-model separation ---------------------------------------


def _write_multi_model_run_dir(tmp_path: Path) -> Path:
    """Hand-author a run where one competitor has two distinct tool_model_key
    values (simulating a future run where the model matrix is wired into the
    runner's dispatch -- not producible via run_benchmark today, since a
    manifest cannot list the same competitor id twice)."""
    run_dir = tmp_path / "multi-model-run"
    _write_json(
        run_dir / "run-summary.json",
        {
            "run_id": "multi-model",
            "repo_commit": "cafef00d",
            "artifact_root": str(run_dir),
            "competitors": ["python-docs-mcp-server"],
            "corpus_ids": ["q001", "q002"],
            "planned_cells": 2,
            "scored_cells": 2,
            "succeeded_cells": 2,
            "failed_cells": 0,
        },
    )
    _write_json(
        run_dir / "environment.json",
        {
            "run_id": "multi-model",
            "created_at": "2026-07-08T00:00:00Z",
            "repo_commit": "cafef00d",
            "python_version": "3.12.0",
            "platform": "test-platform",
            "system": "Test",
            "machine": "x86_64",
        },
    )
    _write_yaml(
        run_dir / "snapshots" / "corpus.yml",
        {
            "questions": [
                {"id": "q001", "category": "exact-symbol", "prompt": "p1"},
                {"id": "q002", "category": "exact-symbol", "prompt": "p2"},
            ]
        },
    )
    _write_yaml(
        run_dir / "snapshots" / "competitor-manifest.yml",
        {"competitors": [{"id": "python-docs-mcp-server", "adapter": "no-mcp-baseline"}]},
    )
    for corpus_id, tool_model_key, score in (
        ("q001", "python-docs-mcp-server:openai/gpt-4o-mini", 1.0),
        ("q002", "python-docs-mcp-server:google/gemini-1.5-flash", 0.5),
    ):
        _write_json(
            run_dir / "scoring" / "python-docs-mcp-server" / f"{corpus_id}.json",
            {
                "competitor_id": "python-docs-mcp-server",
                "corpus_id": corpus_id,
                "tool_model_key": tool_model_key,
                "status": "succeeded",
                "score": score,
                "requires_manual_scoring": False,
                "included_in_correctness_denominator": True,
                "error_category": None,
            },
        )
        _write_json(
            run_dir / "latency" / "python-docs-mcp-server" / f"{corpus_id}.json",
            {
                "competitor_id": "python-docs-mcp-server",
                "corpus_id": corpus_id,
                "latency_ms": 10.0,
                "status": "succeeded",
            },
        )
        _write_json(
            run_dir / "tokens" / "python-docs-mcp-server" / f"{corpus_id}.json",
            {
                "competitor_id": "python-docs-mcp-server",
                "corpus_id": corpus_id,
                "client_wrapped_tokens": None,
                "raw_payload_tokens": None,
            },
        )
    return run_dir


def test_aggregate_across_model_families_is_labeled_and_kept_separate(tmp_path: Path) -> None:
    run_dir = _write_multi_model_run_dir(tmp_path)

    text = generate_report(
        run_dir, model_matrix_path=MODEL_MATRIX_PATH, methodology_path=METHODOLOGY_PATH
    ).read_text(encoding="utf-8")

    # Per-model rows are visible and distinct.
    assert "### `python-docs-mcp-server:openai/gpt-4o-mini`" in text
    assert "### `python-docs-mcp-server:google/gemini-1.5-flash`" in text
    # A separate, explicitly labeled aggregate section exists.
    assert "AGGREGATE" in text
    assert "python-docs-mcp-server` -- AGGREGATE across 2 model families" in text
    # The aggregate section names both model families it rolled up.
    assert "python-docs-mcp-server:openai/gpt-4o-mini" in text
    assert "python-docs-mcp-server:google/gemini-1.5-flash" in text


def test_no_aggregate_section_claims_when_each_tool_has_one_model(tmp_path: Path) -> None:
    out_dir = tmp_path / "results" / "single-model-run"
    _run(tmp_path, out_dir, run_id="single-model-run")

    text = generate_report(
        out_dir, model_matrix_path=MODEL_MATRIX_PATH, methodology_path=METHODOLOGY_PATH
    ).read_text(encoding="utf-8")

    assert "No cross-model aggregation applies to this run" in text


# --- Token approximation exclusion from headline-eligible tables (#89) ------
#
# The count-tokens integration itself lives in benchmarks/adapters/claude_
# tokens.py (see tests/benchmarks/test_claude_tokens.py). These tests only
# cover the additive report.py contract: a token record marked
# ``approximation: true`` is counted and disclosed, but never folded into a
# headline-eligible median (methodology "Token Measurement": "Approximate
# counts must not be used for headline claims").


def _write_run_dir_with_one_approximated_and_one_exact_token_record(tmp_path: Path) -> Path:
    """One competitor, two corpus questions: one exact count, one approximation.

    Both cells share a ``tool_model_key`` ("tool-a") so they land in the same
    headline-eligible aggregation group, isolating the approximation filter
    as the only variable.
    """
    run_dir = tmp_path / "run"
    _write_json(
        run_dir / "run-summary.json",
        {
            "run_id": "token-approximation",
            "repo_commit": "deadbeef",
            "artifact_root": str(run_dir),
            "competitors": ["tool-a"],
            "corpus_ids": ["q001", "q002"],
            "planned_cells": 2,
            "scored_cells": 2,
            "succeeded_cells": 2,
            "failed_cells": 0,
        },
    )
    _write_json(
        run_dir / "environment.json",
        {
            "run_id": "token-approximation",
            "created_at": "2026-07-08T00:00:00Z",
            "repo_commit": "deadbeef",
            "python_version": "3.12.0",
            "platform": "test-platform",
            "system": "Test",
            "machine": "x86_64",
        },
    )
    _write_yaml(
        run_dir / "snapshots" / "corpus.yml",
        {
            "questions": [
                {"id": "q001", "category": "exact-symbol", "prompt": "p1"},
                {"id": "q002", "category": "exact-symbol", "prompt": "p2"},
            ]
        },
    )
    _write_yaml(
        run_dir / "snapshots" / "competitor-manifest.yml",
        {"competitors": [{"id": "tool-a", "adapter": "no-mcp-baseline"}]},
    )
    for corpus_id in ("q001", "q002"):
        _write_json(
            run_dir / "scoring" / "tool-a" / f"{corpus_id}.json",
            {
                "competitor_id": "tool-a",
                "corpus_id": corpus_id,
                "tool_model_key": "tool-a",
                "status": "succeeded",
                "score": None,
                "requires_manual_scoring": True,
                "included_in_correctness_denominator": True,
                "error_category": None,
            },
        )
        _write_json(
            run_dir / "latency" / "tool-a" / f"{corpus_id}.json",
            {
                "competitor_id": "tool-a",
                "corpus_id": corpus_id,
                "latency_ms": 10.0,
                "status": "succeeded",
            },
        )
    _write_json(
        run_dir / "tokens" / "tool-a" / "q001.json",
        {
            "competitor_id": "tool-a",
            "corpus_id": "q001",
            "status": "counted",
            "client_wrapped_tokens": 100,
            "raw_payload_tokens": 100,
            "approximation": False,
        },
    )
    _write_json(
        run_dir / "tokens" / "tool-a" / "q002.json",
        {
            "competitor_id": "tool-a",
            "corpus_id": "q002",
            "status": "counted",
            # An outlier value: if this were folded into the headline median
            # alongside q001's 100, the median would move. It must not be.
            "client_wrapped_tokens": 999999,
            "raw_payload_tokens": 999999,
            "approximation": True,
        },
    )
    return run_dir


def test_report_excludes_approximated_tokens_from_headline_median(tmp_path: Path) -> None:
    run_dir = _write_run_dir_with_one_approximated_and_one_exact_token_record(tmp_path)

    text = generate_report(
        run_dir, model_matrix_path=MODEL_MATRIX_PATH, methodology_path=METHODOLOGY_PATH
    ).read_text(encoding="utf-8")

    # Only the exact count (100) drives the headline-eligible median -- the
    # approximation (999999) must never move it.
    assert "| tool-a | 100, 1 approximation:true excluded |" in text
    assert "999999" not in text


def test_readme_summary_excludes_approximated_tokens_from_headline_row(tmp_path: Path) -> None:
    run_dir = _write_run_dir_with_one_approximated_and_one_exact_token_record(tmp_path)

    text = generate_readme_summary(
        run_dir, methodology_path=METHODOLOGY_PATH
    ).read_text(encoding="utf-8")

    assert "100, 1 approximation:true excluded" in text
    assert "999999" not in text
