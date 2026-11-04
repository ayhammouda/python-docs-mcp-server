"""Tests for the competitor manifest eligibility gates (issue #87).

The four fixed-flow competitor adapters and their tests are gone (design
§11.1): round 2 runs the competitors through the agentic MCP bridge, and
brings the keyless competitors' allowlist latch back with its caller. What
stays, and is covered here: per-entry and manifest-level validation (by
adapter or id), the runner's manifest-load-time refusal (including the CLI
exit-2 path), the ``exclusions:`` block's byte-for-byte snapshot survival,
and the committed template manifest.

Zero network calls anywhere in this file, by construction.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from benchmarks.adapters.eligibility import (
    COMPETITOR_ADAPTER_IDS,
    validate_competitor_eligibility,
    validate_manifest_eligibility,
)
from benchmarks.runner import (
    BenchmarkConfig,
    BenchmarkValidationError,
    run_benchmark,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE_MANIFEST_PATH = (
    _REPO_ROOT / "docs" / "benchmarks" / "competitor-manifest.template.yml"
)


# =============================================================================
# Eligibility / exclusion screening
# =============================================================================


def _valid_competitor_entry(adapter: str = "gitmcp", **overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": adapter,
        "adapter": adapter,
        "pin": {
            "kind": "endpoint-date",
            "value": "https://example.test",
            "access_date": "2026-07-09",
        },
        "terms_check": {
            "verdict": "permitted",
            "checked_on": "2026-07-09",
            "source_url": "https://example.test/terms",
        },
        "eligibility": {"status": "eligible"},
    }
    entry.update(overrides)
    return entry


def test_validate_competitor_eligibility_accepts_a_well_formed_entry() -> None:
    validate_competitor_eligibility(_valid_competitor_entry())  # must not raise


def test_validate_competitor_eligibility_accepts_conditional_status_with_conditions() -> None:
    validate_competitor_eligibility(
        _valid_competitor_entry(
            eligibility={"status": "conditional", "conditions": ["needs permission"]}
        )
    )  # must not raise


def test_validate_competitor_eligibility_rejects_missing_pin() -> None:
    entry = _valid_competitor_entry()
    del entry["pin"]

    with pytest.raises(BenchmarkValidationError, match="pin"):
        validate_competitor_eligibility(entry)


def test_validate_competitor_eligibility_rejects_unrecognized_pin_kind() -> None:
    with pytest.raises(BenchmarkValidationError, match=r"pin\.kind"):
        validate_competitor_eligibility(
            _valid_competitor_entry(
                pin={"kind": "made-up", "value": "x", "access_date": "2026-07-09"}
            )
        )


def test_validate_competitor_eligibility_rejects_missing_terms_check() -> None:
    entry = _valid_competitor_entry()
    del entry["terms_check"]

    with pytest.raises(BenchmarkValidationError, match="terms_check"):
        validate_competitor_eligibility(entry)


def test_validate_competitor_eligibility_rejects_unrecognized_terms_verdict() -> None:
    with pytest.raises(BenchmarkValidationError, match=r"terms_check\.verdict"):
        validate_competitor_eligibility(
            _valid_competitor_entry(
                terms_check={
                    "verdict": "maybe",
                    "checked_on": "2026-07-09",
                    "source_url": "https://example.test",
                }
            )
        )


def test_validate_competitor_eligibility_rejects_missing_eligibility() -> None:
    entry = _valid_competitor_entry()
    del entry["eligibility"]

    with pytest.raises(BenchmarkValidationError, match=r"eligibility\.status"):
        validate_competitor_eligibility(entry)


def test_validate_competitor_eligibility_rejects_excluded_status_points_to_exclusions() -> None:
    with pytest.raises(BenchmarkValidationError, match="exclusions"):
        validate_competitor_eligibility(
            _valid_competitor_entry(eligibility={"status": "excluded"})
        )


def test_validate_manifest_eligibility_skips_non_competitor_adapter_entries() -> None:
    manifest = {"competitors": [{"id": "no-mcp", "adapter": "no-mcp-baseline"}]}

    validate_manifest_eligibility(manifest)  # must not raise despite no pin/terms metadata


def test_validate_manifest_eligibility_validates_only_competitor_adapter_entries() -> None:
    manifest = {
        "competitors": [
            {"id": "no-mcp", "adapter": "no-mcp-baseline"},
            _valid_competitor_entry("gitmcp"),
        ]
    }

    validate_manifest_eligibility(manifest)  # must not raise


def test_validate_manifest_eligibility_raises_for_excluded_competitor_entry() -> None:
    manifest = {
        "competitors": [_valid_competitor_entry("gitmcp", eligibility={"status": "excluded"})]
    }

    with pytest.raises(BenchmarkValidationError, match="gitmcp"):
        validate_manifest_eligibility(manifest)


def test_validate_manifest_eligibility_screens_a_competitor_by_its_id_too() -> None:
    # Round 2 runs competitors through the agentic bridge (adapter mcp-stdio), so the id alone
    # must bring the pin and terms gate.
    manifest = {"competitors": [{"id": "context7", "adapter": "mcp-stdio", "command": ["x"]}]}

    with pytest.raises(BenchmarkValidationError, match="context7"):
        validate_manifest_eligibility(manifest)


# --- Integration: runner refuses at manifest-load time, before any cell ----


def _write_yaml(path: Path, data: dict[str, Any]) -> Path:
    path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
    return path


def _corpus(tmp_path: Path) -> Path:
    return _write_yaml(
        tmp_path / "corpus.yml",
        {
            "questions": [
                {
                    "id": "q001",
                    "prompt": "What does pathlib.Path.read_text return?",
                }
            ]
        },
    )


def test_run_benchmark_refuses_manifest_with_excluded_competitor_entry_and_writes_nothing(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "results" / "excluded"
    manifest_path = _write_yaml(
        tmp_path / "competitors.yml",
        {"competitors": [_valid_competitor_entry("gitmcp", eligibility={"status": "excluded"})]},
    )

    with pytest.raises(BenchmarkValidationError, match="gitmcp"):
        run_benchmark(
            BenchmarkConfig(
                corpus_path=_corpus(tmp_path), manifest_path=manifest_path, out_dir=out_dir
            )
        )

    assert not out_dir.exists()


def test_run_benchmark_refuses_manifest_with_incomplete_pin_metadata(tmp_path: Path) -> None:
    out_dir = tmp_path / "results" / "missing-pin"
    entry = _valid_competitor_entry("ref-tools")
    del entry["pin"]
    manifest_path = _write_yaml(tmp_path / "competitors.yml", {"competitors": [entry]})

    with pytest.raises(BenchmarkValidationError, match="ref-tools"):
        run_benchmark(
            BenchmarkConfig(
                corpus_path=_corpus(tmp_path), manifest_path=manifest_path, out_dir=out_dir
            )
        )

    assert not out_dir.exists()


def test_cli_exits_2_for_manifest_with_excluded_competitor_entry(tmp_path: Path) -> None:
    out_dir = tmp_path / "results" / "excluded-cli"
    manifest_path = _write_yaml(
        tmp_path / "competitors.yml",
        {"competitors": [_valid_competitor_entry("gitmcp", eligibility={"status": "excluded"})]},
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks",
            "run",
            "--corpus",
            str(_corpus(tmp_path)),
            "--manifest",
            str(manifest_path),
            "--out",
            str(out_dir),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        cwd=_REPO_ROOT,
    )

    assert result.returncode == 2
    assert "gitmcp" in result.stderr
    assert not out_dir.exists()


def test_exclusions_block_passes_validation_and_survives_snapshot_byte_for_byte(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "results" / "exclusions-block"
    manifest_bytes = (
        b"competitors:\n"
        b"  - id: no-mcp\n"
        b"    adapter: fake\n"
        b"exclusions:\n"
        b"  - id: some-excluded-competitor\n"
        b'    reason: "vendor denied benchmarking permission"\n'
        b"    terms_check:\n"
        b"      verdict: forbidden-without-permission\n"
        b'      checked_on: "2026-07-09"\n'
        b'      source_url: "https://example.test/terms"\n'
        b'    decided_on: "2026-07-09"\n'
    )
    manifest_path = tmp_path / "competitors.yml"
    manifest_path.write_bytes(manifest_bytes)

    summary = run_benchmark(
        BenchmarkConfig(
            corpus_path=_corpus(tmp_path),
            manifest_path=manifest_path,
            out_dir=out_dir,
            run_id="exclusions-block",
        )
    )

    assert summary["succeeded_cells"] == 1
    assert (out_dir / "snapshots" / "competitor-manifest.yml").read_bytes() == manifest_bytes


# --- The committed template manifest ----------------------------------------


def test_template_states_the_ref_tools_session_requirement_for_round_2() -> None:
    data = yaml.safe_load(_TEMPLATE_MANIFEST_PATH.read_text(encoding="utf-8"))
    (ref,) = [c for c in data["competitors"] if c["id"] == "ref-tools"]
    conditions = " ".join(ref["eligibility"]["conditions"])
    assert "satisfied by construction" not in conditions


def test_template_manifest_parses_and_validates() -> None:
    data = yaml.safe_load(_TEMPLATE_MANIFEST_PATH.read_text(encoding="utf-8"))

    validate_manifest_eligibility(data)  # must not raise

    competitors = data["competitors"]
    assert {competitor["adapter"] for competitor in competitors} == COMPETITOR_ADAPTER_IDS
    assert data["exclusions"] == []


def test_template_manifest_dry_run_plans_all_four_competitor_cells(tmp_path: Path) -> None:
    out_dir = tmp_path / "results" / "template-dry-run"

    summary = run_benchmark(
        BenchmarkConfig(
            corpus_path=_corpus(tmp_path),
            manifest_path=_TEMPLATE_MANIFEST_PATH,
            out_dir=out_dir,
            run_id="template-dry-run",
            dry_run=True,
        )
    )

    assert summary["dry_run"] is True
    assert summary["planned_cells"] == 4
    assert not (out_dir / "transcripts").exists()

