"""Report generator for benchmark run artifacts (issue #74).

Reads the raw artifact tree written by ``benchmarks.runner.run_benchmark``
(``run-summary.json``, ``environment.json``, ``snapshots/``, and the
per-cell ``transcripts|tokens|latency|scoring|failures/<competitor>/<qid>.json``
files) plus ``docs/benchmarks/model-matrix.yml`` (the model axis, see
``benchmarks.model_matrix``) and writes two Markdown files:

- A full raw report (``REPORT.md``): methodology link, corpus hash, repo
  commit, model/client matrix, competitor manifest, correctness by category,
  token counts after client rewrap (with honest ``None`` placeholders),
  success latency (median/p95/min/max over succeeded cells) beside a
  failed-cell latency median and count, failures, manifest exclusions, and
  environment metadata. Token records marked ``approximation: true`` (issue
  #89 -- the fixed-flow envelope has name-only tool stubs and no system
  prompt; see ``benchmarks.adapters.claude_tokens``) and exact counts
  recorded for failed cells are counted and shown separately, never folded
  into the headline-eligible median (see ``_basic_stats``).
- A compact README-safe summary template: strict tool+model
  (``tool_model_key``) pairings only -- never tool-only rows -- with an
  error/timeout-rate column, plus links to the methodology and the raw
  result bundle. This file is a generated template, not a README edit;
  publishing any claim from it is a separate maintainer act gated on real
  data (roadmap decision 5.17).

Both generators refuse (``BenchmarkValidationError``, the same exception
type used across the benchmark's YAML/JSON config and artifact surface) when
required run metadata, the corpus-hash source file, or raw per-cell result
files are missing, so a README-ready block can never be produced from an
incomplete run.

Confirmed design decision (issue #86, 2026-07-08), narrowing the prior
issue #73/#74 known gap: cell composition stays competitor x question, not
competitor x model x question, so the model matrix is intentionally not
wired into ``benchmarks.runner._execute_cell`` dispatch -- a given run's
``tool_model_key`` values come from the competitor manifest (one manifest
entry per tool x model pairing), not from an automatic per-cell lookup
against ``docs/benchmarks/model-matrix.yml``. Issue #86 adds
``benchmarks.model_matrix.validate_manifest_against_matrix`` so a
manifest's declared provider/model pairings can be checked against this
matrix before a run, but that check is not invoked automatically by
``run_benchmark``. This module treats the model matrix as descriptive
context (the benchmark's target model axis) and never assumes an observed
``tool_model_key`` corresponds to a matrix entry.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import statistics
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from benchmarks.model_matrix import METHODOLOGY_TOKEN_LABEL, ModelMatrix, load_model_matrix
from benchmarks.runner import BenchmarkValidationError

METHODOLOGY_DISCLAIMER = (
    "This is a generated raw report, not a publication-ready claim. Per roadmap "
    "decision 5.17, no comparative or benchmark claim may enter README, PyPI, or "
    "launch copy until reproducible public data exists and a maintainer has "
    "reviewed it against the methodology linked below."
)

README_SUMMARY_DISCLAIMER = (
    "**Template only -- not a README edit.** Publishing any table below requires "
    "a separate maintainer decision gated on real, reproducible data (roadmap "
    "decision 5.17). Do not copy this content into README/PyPI/launch copy "
    "without that review."
)

_RUN_SUMMARY_REQUIRED_FIELDS = (
    "run_id",
    "repo_commit",
    "artifact_root",
    "competitors",
    "corpus_ids",
    "planned_cells",
    "scored_cells",
    "succeeded_cells",
    "failed_cells",
)
_ENVIRONMENT_REQUIRED_FIELDS = (
    "run_id",
    "created_at",
    "repo_commit",
    "python_version",
    "platform",
    "system",
    "machine",
)
_SCORING_REQUIRED_FIELDS = ("competitor_id", "corpus_id", "status")


@dataclass(frozen=True)
class CellRecord:
    """One merged competitor/question result, joined across artifact kinds."""

    competitor_id: str
    corpus_id: str
    tool_model_key: str
    category: str
    status: str
    error_category: str | None
    score: float | None
    requires_manual_scoring: bool
    included_in_correctness_denominator: bool
    latency_ms: float | None
    client_wrapped_tokens: int | None
    raw_payload_tokens: int | None
    token_approximation: bool
    """True when the token record's ``client_wrapped_tokens`` count (if any) is an
    approximation -- issue #89 / methodology amendment of 2026-10-30: the fixed-flow
    envelope has name-only tool stubs and no system prompt. Approximate counts must
    never be used for headline claims (see ``_basic_stats``, which excludes them)."""
    token_status: str | None
    """The token record's ``status`` (``counted``, ``placeholder``, ``failed``), or
    ``None`` when the cell has no token record or the record has no status."""


@dataclass(frozen=True)
class RunBundle:
    """Loaded and validated raw artifacts for one benchmark run."""

    run_dir: Path
    run_summary: dict[str, Any]
    environment: dict[str, Any]
    corpus_hash: str
    manifest_hash: str
    competitors: dict[str, dict[str, Any]]
    exclusions: list[dict[str, Any]]
    cells: list[CellRecord]
    failure_records: list[dict[str, Any]]
    token_failure_records: list[dict[str, Any]]


def generate_report(
    run_dir: Path,
    *,
    model_matrix_path: Path,
    methodology_path: Path,
    out_path: Path | None = None,
) -> Path:
    """Write the full raw ``REPORT.md`` for ``run_dir`` and return its path.

    Raises ``BenchmarkValidationError`` if required run metadata, the
    corpus-hash source file, or raw per-cell result files are missing.
    """
    bundle = _load_run_bundle(run_dir)
    matrix = load_model_matrix(model_matrix_path)
    _require_file(methodology_path, "methodology file")

    report_path = out_path or (run_dir / "REPORT.md")
    content = _render_report(bundle, matrix, methodology_path, report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(content, encoding="utf-8")
    return report_path


def generate_readme_summary(
    run_dir: Path,
    *,
    methodology_path: Path,
    out_path: Path | None = None,
) -> Path:
    """Write the compact README-safe summary template and return its path.

    Refuses (``BenchmarkValidationError``) if required run metadata, the
    corpus hash source file, or raw per-cell result files are missing -- the
    same validation gate ``generate_report`` uses, since a README-ready block
    requires exactly the same audit trail as the full report.
    """
    bundle = _load_run_bundle(run_dir)
    _require_file(methodology_path, "methodology file")

    summary_path = out_path or (run_dir / "README-SUMMARY.md")
    content = _render_readme_summary(bundle, methodology_path, summary_path)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(content, encoding="utf-8")
    return summary_path


# --- Artifact loading and validation ----------------------------------------


def _load_run_bundle(run_dir: Path) -> RunBundle:
    if not run_dir.is_dir():
        raise BenchmarkValidationError(f"run artifact directory does not exist: {run_dir}")

    run_summary = _load_required_json(run_dir / "run-summary.json")
    _require_fields(run_summary, _RUN_SUMMARY_REQUIRED_FIELDS, "run-summary.json")

    environment = _load_required_json(run_dir / "environment.json")
    _require_fields(environment, _ENVIRONMENT_REQUIRED_FIELDS, "environment.json")

    corpus_snapshot_path = run_dir / "snapshots" / "corpus.yml"
    manifest_snapshot_path = run_dir / "snapshots" / "competitor-manifest.yml"
    _require_file(corpus_snapshot_path, "corpus snapshot (needed to compute the corpus hash)")
    _require_file(manifest_snapshot_path, "competitor manifest snapshot")

    corpus_hash = _sha256_of(corpus_snapshot_path)
    manifest_hash = _sha256_of(manifest_snapshot_path)
    corpus_categories = _load_corpus_categories(corpus_snapshot_path)
    competitors, exclusions = _load_competitor_manifest(manifest_snapshot_path)

    scoring_entries = _load_cell_records(run_dir / "scoring")
    if not scoring_entries:
        raise BenchmarkValidationError(
            "no raw scoring result files found under "
            f"{run_dir / 'scoring'}; required raw result files are missing "
            "(this run directory may be a --dry-run plan with no executed cells)"
        )
    latency_records = _load_cell_records(run_dir / "latency")
    token_records = _load_cell_records(run_dir / "tokens")
    latency_by_key = {_cell_key(record): record for _, record in latency_records}
    tokens_by_key = {_cell_key(record): record for _, record in token_records}

    cells: list[CellRecord] = []
    for path, record in scoring_entries:
        missing = [field for field in _SCORING_REQUIRED_FIELDS if field not in record]
        if missing:
            raise BenchmarkValidationError(f"{path} is missing required field(s): {missing}")
        key = _cell_key(record)
        latency_record = latency_by_key.get(key, {})
        token_record = tokens_by_key.get(key, {})
        cells.append(
            CellRecord(
                competitor_id=record["competitor_id"],
                corpus_id=record["corpus_id"],
                tool_model_key=record.get("tool_model_key") or record["competitor_id"],
                category=corpus_categories.get(record["corpus_id"], "uncategorized"),
                status=record["status"],
                error_category=record.get("error_category"),
                score=record.get("score"),
                requires_manual_scoring=bool(record.get("requires_manual_scoring", False)),
                included_in_correctness_denominator=bool(
                    record.get("included_in_correctness_denominator", True)
                ),
                latency_ms=latency_record.get("latency_ms"),
                client_wrapped_tokens=token_record.get("client_wrapped_tokens"),
                raw_payload_tokens=token_record.get("raw_payload_tokens"),
                token_approximation=bool(token_record.get("approximation", False)),
                token_status=token_record.get("status"),
            )
        )

    failure_records = [record for _, record in _load_cell_records(run_dir / "failures")]
    token_failure_records = [
        record for _, record in token_records if record.get("status") == "failed"
    ]

    return RunBundle(
        run_dir=run_dir,
        run_summary=run_summary,
        environment=environment,
        corpus_hash=corpus_hash,
        manifest_hash=manifest_hash,
        competitors=competitors,
        exclusions=exclusions,
        cells=cells,
        failure_records=failure_records,
        token_failure_records=token_failure_records,
    )


def _cell_key(record: dict[str, Any]) -> tuple[Any, Any]:
    return (record.get("competitor_id"), record.get("corpus_id"))


def _require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise BenchmarkValidationError(f"required {label} is missing: {path}")


def _load_required_json(path: Path) -> dict[str, Any]:
    _require_file(path, f"run artifact ({path.name})")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BenchmarkValidationError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise BenchmarkValidationError(f"{path} must contain a JSON object")
    return data


def _require_fields(data: dict[str, Any], fields: tuple[str, ...], label: str) -> None:
    missing = [field for field in fields if field not in data]
    if missing:
        raise BenchmarkValidationError(f"{label} is missing required field(s): {missing}")


def _sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_yaml_mapping(path: Path, label: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise BenchmarkValidationError(f"{label} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise BenchmarkValidationError(f"{label} must contain a YAML mapping")
    return data


def _load_corpus_categories(path: Path) -> dict[str, str]:
    data = _load_yaml_mapping(path, "corpus snapshot")
    items = data.get("questions")
    if not isinstance(items, list):
        raise BenchmarkValidationError("corpus snapshot must contain a 'questions' list")
    categories: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        corpus_id = item.get("id")
        if not isinstance(corpus_id, str):
            continue
        category = item.get("category")
        categories[corpus_id] = (
            category if isinstance(category, str) and category else "uncategorized"
        )
    return categories


def _load_competitor_manifest(
    path: Path,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    data = _load_yaml_mapping(path, "competitor manifest snapshot")
    items = data.get("competitors")
    if not isinstance(items, list):
        raise BenchmarkValidationError(
            "competitor manifest snapshot must contain a 'competitors' list"
        )
    competitors: dict[str, dict[str, Any]] = {}
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            competitors[item["id"]] = item
    exclusions = data.get("exclusions")
    if exclusions is None:
        exclusions = []
    if not isinstance(exclusions, list):
        raise BenchmarkValidationError("competitor manifest snapshot 'exclusions' must be a list")
    if not all(isinstance(entry, dict) for entry in exclusions):
        raise BenchmarkValidationError(
            "competitor manifest snapshot 'exclusions' entries must be mappings"
        )
    return competitors, exclusions


def _load_cell_records(base_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    if not base_dir.is_dir():
        return []
    records: list[tuple[Path, dict[str, Any]]] = []
    for competitor_dir in sorted(p for p in base_dir.iterdir() if p.is_dir()):
        for cell_file in sorted(competitor_dir.glob("*.json")):
            try:
                data = json.loads(cell_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise BenchmarkValidationError(f"{cell_file} is not valid JSON: {exc}") from exc
            if not isinstance(data, dict):
                raise BenchmarkValidationError(f"{cell_file} must contain a JSON object")
            records.append((cell_file, data))
    return records


# --- Aggregation --------------------------------------------------------------


def _group_by(
    cells: list[CellRecord], key_fn: Callable[[CellRecord], str]
) -> dict[str, list[CellRecord]]:
    groups: dict[str, list[CellRecord]] = {}
    for cell in cells:
        groups.setdefault(key_fn(cell), []).append(cell)
    return groups


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[int(rank)]
    return ordered[lower] * (upper - rank) + ordered[upper] * (rank - lower)


def _basic_stats(cells: list[CellRecord]) -> dict[str, Any]:
    total = len(cells)
    failed = sum(1 for cell in cells if cell.status == "failed")
    timeouts = sum(1 for cell in cells if cell.error_category == "timeout")
    scored = [cell.score for cell in cells if cell.score is not None]
    pending = sum(1 for cell in cells if cell.requires_manual_scoring)
    # A failed cell has no latency to a final answer, so success latency covers
    # succeeded cells only; failed cells get their own median and count, so a
    # tool can neither look fast by failing fast nor hide a slow failure.
    success_latencies = [
        cell.latency_ms for cell in cells if cell.status != "failed" and cell.latency_ms is not None
    ]
    failed_latencies = [
        cell.latency_ms for cell in cells if cell.status == "failed" and cell.latency_ms is not None
    ]
    # Headline-eligible token counts exclude approximations (issue #89 /
    # methodology "Token Measurement": approximate counts must never be used
    # for headline claims) and exact counts recorded for failed cells. Each
    # excluded bucket is counted so a reader can see the gap isn't silently
    # dropped. A failed count call has its own bucket, never the placeholder
    # one. The buckets partition the cells; the rest are placeholders.
    tokens = [
        cell.client_wrapped_tokens
        for cell in cells
        if cell.client_wrapped_tokens is not None
        and not cell.token_approximation
        and cell.status != "failed"
    ]
    tokens_failed_cell = sum(
        1
        for cell in cells
        if cell.client_wrapped_tokens is not None
        and not cell.token_approximation
        and cell.status == "failed"
    )
    tokens_approximated = sum(
        1
        for cell in cells
        if cell.client_wrapped_tokens is not None and cell.token_approximation
    )
    tokens_count_failed = sum(
        1 for cell in cells if cell.client_wrapped_tokens is None and cell.token_status == "failed"
    )
    return {
        "total_cells": total,
        "succeeded_cells": total - failed,
        "failed_cells": failed,
        "error_rate": (failed / total) if total else None,
        "timeout_cells": timeouts,
        "timeout_rate": (timeouts / total) if total else None,
        "scored_cells": len(scored),
        "pending_manual_scoring": pending,
        "mean_correctness": (sum(scored) / len(scored)) if scored else None,
        "success_latency_median_ms": (
            statistics.median(success_latencies) if success_latencies else None
        ),
        "success_latency_p95_ms": (
            _percentile(success_latencies, 0.95) if success_latencies else None
        ),
        "success_latency_min_ms": min(success_latencies) if success_latencies else None,
        "success_latency_max_ms": max(success_latencies) if success_latencies else None,
        "failed_latency_median_ms": (
            statistics.median(failed_latencies) if failed_latencies else None
        ),
        "failed_latency_cells": len(failed_latencies),
        "tokens_present": len(tokens),
        "tokens_approximated": tokens_approximated,
        "tokens_failed_cell": tokens_failed_cell,
        "tokens_count_failed": tokens_count_failed,
        "tokens_placeholder": (
            total - len(tokens) - tokens_approximated - tokens_failed_cell - tokens_count_failed
        ),
        "median_client_wrapped_tokens": statistics.median(tokens) if tokens else None,
    }


def _fmt_pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.1f}%"


def _fmt_ms(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.1f}"


def _fmt_score(scored: int, total: int, mean: float | None) -> str:
    if scored == 0:
        return f"N/A (0/{total} scored)"
    return f"{mean:.2f} ({scored}/{total} scored)"


def _fmt_tokens(stats: dict[str, Any]) -> str:
    placeholder = stats["tokens_placeholder"]
    approximated = stats["tokens_approximated"]
    failed_cell = stats["tokens_failed_cell"]
    count_failed = stats["tokens_count_failed"]
    if stats["tokens_present"] == 0:
        excluded = []
        if approximated:
            excluded.append(f"approximation:true excludes {approximated} cell(s)")
        if failed_cell:
            excluded.append(f"{failed_cell} failed-cell count(s) excluded")
        if count_failed:
            excluded.append(f"{count_failed} count(s) failed")
        if not excluded:
            return f"None (placeholder -- {placeholder} cell(s) pending token-count integration)"
        return f"None headline-eligible ({'; '.join(excluded)}; {placeholder} placeholder)"
    suffixes = []
    if placeholder:
        suffixes.append(f"{placeholder} placeholder")
    if approximated:
        suffixes.append(f"{approximated} approximation:true excluded")
    if failed_cell:
        suffixes.append(f"{failed_cell} failed-cell count(s) excluded")
    if count_failed:
        suffixes.append(f"{count_failed} count(s) failed")
    suffix = f", {', '.join(suffixes)}" if suffixes else ""
    return f"{stats['median_client_wrapped_tokens']:.0f}{suffix}"


def _fmt_failed_latency(stats: dict[str, Any]) -> str:
    return f"{_fmt_ms(stats['failed_latency_median_ms'])} (n={stats['failed_latency_cells']})"


def _fmt_field(value: object) -> str:
    return "" if value is None else str(value)


def _relative_link(target: Path, from_dir: Path) -> str:
    return os.path.relpath(target.resolve(), start=from_dir.resolve())


#: A URL run first (GFM extended autolinks are built from the raw text, before
#: backslash escapes resolve), then the characters that change rendering inside an
#: inline table cell. The run stops at link, image and code-span syntax and at "\",
#: so those stay escaped even for a renderer that linkifies after inline parsing.
#: "_" counts only at a word edge: between letters or digits it cannot open or close
#: emphasis. "#", "+", "-", "." and the like matter only at the start of a block,
#: which a cell never is.
_CELL_MARKDOWN = re.compile(
    r"(?P<url>(?:https?://|www\.)[^\s<>\[\]`\\]+)|[\\`*\[\]<|~&]|(?<![^\W_])_|_(?![^\W_])"
)


def _escape_cell_match(match: re.Match[str]) -> str:
    if match["url"] is not None:
        return match["url"].replace("|", "\\|")  # the table splitter resolves "\|"
    return "\\" + match[0]


def _cell(text: str) -> str:
    """Render ``text`` as one literal plain-text table cell.

    Line breaks become spaces (a newline would end the row), then every significant
    character gets a backslash, in one linear pass, so a proxy error body such as
    "Error | Gateway" cannot add a column and ``__init__`` stays ``__init__``. URLs
    keep their raw text, with only "|" escaped.
    """
    return _CELL_MARKDOWN.sub(_escape_cell_match, re.sub(r"\r\n|[\r\n]", " ", text))


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    lines.extend("| " + " | ".join(_cell(cell) for cell in row) + " |" for row in rows)
    return lines


# --- Rendering -----------------------------------------------------------------


def _render_report(
    bundle: RunBundle, matrix: ModelMatrix, methodology_path: Path, report_path: Path
) -> str:
    run_summary = bundle.run_summary
    methodology_link = _relative_link(methodology_path, report_path.parent)
    by_tool_model = _group_by(bundle.cells, lambda cell: cell.tool_model_key)
    by_competitor = _group_by(bundle.cells, lambda cell: cell.competitor_id)
    manifest_hash_line = (
        "- **Competitor manifest hash (SHA-256 of `snapshots/competitor-manifest.yml`):** "
        f"`{bundle.manifest_hash}`"
    )

    lines: list[str] = [
        f"# Benchmark Report -- {run_summary.get('run_id', 'unknown-run')}",
        "",
        f"> {METHODOLOGY_DISCLAIMER}",
        "",
        f"- **Methodology:** [{methodology_path.name}]({methodology_link})",
        f"- **Repo commit:** `{run_summary.get('repo_commit', 'unknown')}`",
        f"- **Corpus hash (SHA-256 of `snapshots/corpus.yml`):** `{bundle.corpus_hash}`",
        manifest_hash_line,
        f"- **Run artifact directory:** `{run_summary.get('artifact_root', str(bundle.run_dir))}`",
        "",
        "## Environment Metadata",
        "",
    ]
    lines.extend(
        _table(
            ["Field", "Value"],
            [[str(key), str(value)] for key, value in sorted(bundle.environment.items())],
        )
    )
    lines.append("")

    lines.append("## Model / Client Matrix")
    lines.append("")
    lines.append(
        "Defines the benchmark's target model axis (`docs/benchmarks/model-matrix.yml`, "
        f"token label `{matrix.methodology_token_label}`). **Design note (issue #86, "
        "confirmed 2026-07-08):** cell composition stays competitor x question, so this "
        "matrix is intentionally not wired into the runner's per-cell dispatch -- "
        "`benchmarks.model_matrix.validate_manifest_against_matrix` can check a manifest's "
        "declared provider/model pairings against this matrix before a run, but the "
        "`tool_model_key` values observed below still come from the competitor manifest, "
        "not from an automatic lookup against this matrix."
    )
    lines.append("")
    lines.extend(
        _table(
            [
                "Model Family",
                "Provider",
                "Model ID",
                "Client",
                "Token Count Method",
                "Headline Eligible",
                "Ineligibility Reason",
            ],
            [
                [
                    family.id,
                    family.provider,
                    family.model_id,
                    family.client,
                    family.token_count_method,
                    "yes" if family.headline_eligible else "no",
                    family.headline_ineligibility_reason or "",
                ]
                for family in matrix.model_families
            ],
        )
    )
    lines.append("")

    lines.append("## Competitor Manifest")
    lines.append("")
    lines.extend(
        _table(
            ["Competitor ID", "Name", "Adapter", "Provider", "Model"],
            [
                [
                    competitor_id,
                    str(raw.get("name", "")),
                    str(raw.get("adapter", "")),
                    str(raw.get("provider", "")),
                    str(raw.get("model", "")),
                ]
                for competitor_id, raw in sorted(bundle.competitors.items())
            ],
        )
    )
    lines.append("")

    lines.append("## Correctness By Category (Per Tool + Model)")
    lines.append("")
    lines.append(
        "Each table below is scoped to exactly one `tool_model_key` (a strict tool+model "
        "pairing). Rows are never averaged across model families; see 'Aggregate Across "
        "Model Families' further down for any cross-model rollup, explicitly labeled as "
        "an aggregate."
    )
    lines.append("")
    for tool_model_key in sorted(by_tool_model):
        cells = by_tool_model[tool_model_key]
        lines.append(f"### `{tool_model_key}`")
        lines.append("")
        rows = []
        by_category = _group_by(cells, lambda cell: cell.category)
        for category in sorted(by_category):
            stats = _basic_stats(by_category[category])
            rows.append(
                [
                    category,
                    _fmt_score(
                        stats["scored_cells"], stats["total_cells"], stats["mean_correctness"]
                    ),
                    str(stats["pending_manual_scoring"]),
                    _fmt_pct(stats["error_rate"]),
                    _fmt_pct(stats["timeout_rate"]),
                ]
            )
        overall = _basic_stats(cells)
        rows.append(
            [
                "**Overall**",
                _fmt_score(
                    overall["scored_cells"], overall["total_cells"], overall["mean_correctness"]
                ),
                str(overall["pending_manual_scoring"]),
                _fmt_pct(overall["error_rate"]),
                _fmt_pct(overall["timeout_rate"]),
            ]
        )
        lines.extend(
            _table(
                ["Category", "Correctness", "Pending Manual Scoring", "Error Rate", "Timeout Rate"],
                rows,
            )
        )
        lines.append("")

    lines.append("## Token Counts (After Client Rewrap)")
    lines.append("")
    lines.append(
        f"Primary metric label: `{METHODOLOGY_TOKEN_LABEL}` (see the methodology's 'Token "
        "Measurement' section). `None` placeholders are surfaced honestly below, never "
        "reported as zero. Records marked `approximation: true` (the fixed-flow envelope "
        "has name-only tool stubs and no system prompt; see the methodology amendment of "
        "2026-10-30) are excluded from this "
        "headline-eligible median; the excluded count is shown alongside it. The median "
        "covers succeeded cells only; exact counts recorded for failed cells are excluded "
        "and counted alongside it. A failed count is shown as failed, never as pending, and "
        "listed under 'Failures And Exclusions'."
    )
    lines.append("")
    lines.extend(
        _table(
            ["Tool + Model", "Client-Wrapped Tokens (median)"],
            [
                [tool_model_key, _fmt_tokens(_basic_stats(by_tool_model[tool_model_key]))]
                for tool_model_key in sorted(by_tool_model)
            ],
        )
    )
    lines.append("")

    lines.append("## Latency (Median / p95)")
    lines.append("")
    lines.append(
        "Success latency (median, p95, min, max) covers succeeded cells only: the "
        "methodology measures latency from dispatch to the final answer, and a failed cell "
        "has none. Failed cells are shown separately, as a median with their count, next to "
        "the error rate, so a tool cannot look fast by failing fast, and a tool that fails "
        "slowly stays visible."
    )
    lines.append("")
    lines.extend(
        _table(
            [
                "Tool + Model",
                "Success Median (ms)",
                "Success p95 (ms)",
                "Success Min (ms)",
                "Success Max (ms)",
                "Failed-Cell Median (ms)",
                "Error Rate",
                "Timeout Rate",
            ],
            [
                [
                    tool_model_key,
                    _fmt_ms(
                        (stats := _basic_stats(by_tool_model[tool_model_key]))[
                            "success_latency_median_ms"
                        ]
                    ),
                    _fmt_ms(stats["success_latency_p95_ms"]),
                    _fmt_ms(stats["success_latency_min_ms"]),
                    _fmt_ms(stats["success_latency_max_ms"]),
                    _fmt_failed_latency(stats),
                    _fmt_pct(stats["error_rate"]),
                    _fmt_pct(stats["timeout_rate"]),
                ]
                for tool_model_key in sorted(by_tool_model)
            ],
        )
    )
    lines.append("")

    lines.append("## Aggregate Across Model Families")
    lines.append("")
    any_aggregate = False
    for competitor_id in sorted(by_competitor):
        tool_model_keys = sorted({cell.tool_model_key for cell in by_competitor[competitor_id]})
        if len(tool_model_keys) < 2:
            continue
        any_aggregate = True
        lines.append(
            f"### `{competitor_id}` -- AGGREGATE across {len(tool_model_keys)} model families"
        )
        lines.append("")
        lines.append(
            "> **AGGREGATE.** This rolls up multiple `tool_model_key` rows for the same "
            "competitor. Per-model rows above are authoritative; do not cite this rollup as "
            "a single-model result."
        )
        lines.append("")
        per_model_means = [
            stats["mean_correctness"]
            for key in tool_model_keys
            if (stats := _basic_stats(by_tool_model[key]))["mean_correctness"] is not None
        ]
        per_model_error_rates = [
            stats["error_rate"]
            for key in tool_model_keys
            if (stats := _basic_stats(by_tool_model[key]))["error_rate"] is not None
        ]
        agg_mean_display = (
            _fmt_score(len(per_model_means), len(tool_model_keys), statistics.mean(per_model_means))
            if per_model_means
            else "N/A (no scored model family)"
        )
        agg_error_display = (
            _fmt_pct(statistics.mean(per_model_error_rates)) if per_model_error_rates else "N/A"
        )
        lines.extend(
            _table(
                ["Metric", "Value"],
                [
                    ["Model families included", ", ".join(tool_model_keys)],
                    ["Mean correctness (avg of per-model means)", agg_mean_display],
                    ["Mean error rate (avg of per-model rates)", agg_error_display],
                ],
            )
        )
        lines.append("")
    if not any_aggregate:
        lines.append(
            "No cross-model aggregation applies to this run: every competitor present exactly "
            "one `tool_model_key` (cell composition stays competitor x question by design; "
            "see 'Model / Client Matrix' above)."
        )
        lines.append("")

    lines.append("## Failures And Exclusions")
    lines.append("")
    if bundle.failure_records:
        lines.extend(
            _table(
                [
                    "Competitor",
                    "Tool + Model",
                    "Corpus ID",
                    "Error Category",
                    "Message",
                    "Included In Denominator",
                ],
                [
                    [
                        str(record.get("competitor_id")),
                        str(record.get("tool_model_key")),
                        str(record.get("corpus_id")),
                        str((record.get("error") or {}).get("category")),
                        str((record.get("error") or {}).get("message")),
                        str(record.get("included_in_correctness_denominator")),
                    ]
                    for record in bundle.failure_records
                ],
            )
        )
    else:
        lines.append("No recorded failures in this run.")
    lines.append("")

    lines.append("### Excluded Competitors")
    lines.append("")
    if bundle.exclusions:
        rows = []
        for entry in bundle.exclusions:
            terms = entry.get("terms_check")
            terms = terms if isinstance(terms, dict) else {}
            rows.append(
                [
                    _fmt_field(entry.get("id")) or "(unnamed)",
                    _fmt_field(entry.get("reason")),
                    _fmt_field(terms.get("verdict")),
                    _fmt_field(entry.get("decided_on")),
                    _fmt_field(terms.get("source_url")),
                ]
            )
        lines.extend(
            _table(["Competitor", "Reason", "Terms Verdict", "Decided On", "Terms Source"], rows)
        )
    else:
        lines.append("No competitors were excluded from this run.")
    lines.append("")

    lines.append("### Token-Count Failures")
    lines.append("")
    if bundle.token_failure_records:
        lines.extend(
            _table(
                ["Competitor", "Tool + Model", "Corpus ID", "Notes"],
                [
                    [
                        str(record.get("competitor_id")),
                        str(record.get("tool_model_key")),
                        str(record.get("corpus_id")),
                        str(record.get("notes")),
                    ]
                    for record in bundle.token_failure_records
                ],
            )
        )
    else:
        lines.append("No token-count failures in this run.")
    lines.append("")

    excluded = [cell for cell in bundle.cells if not cell.included_in_correctness_denominator]
    lines.append("### Excluded From Correctness Denominator")
    lines.append("")
    if excluded:
        lines.extend(
            _table(
                ["Tool + Model", "Corpus ID"],
                [[cell.tool_model_key, cell.corpus_id] for cell in excluded],
            )
        )
    else:
        lines.append("No cells were excluded from the correctness denominator.")
    lines.append("")

    return "\n".join(lines) + "\n"


def _render_readme_summary(bundle: RunBundle, methodology_path: Path, summary_path: Path) -> str:
    run_summary = bundle.run_summary
    methodology_link = _relative_link(methodology_path, summary_path.parent)
    by_tool_model = _group_by(bundle.cells, lambda cell: cell.tool_model_key)
    raw_bundle_line = (
        "- **Raw result bundle:** "
        f"`{run_summary.get('artifact_root', str(bundle.run_dir))}` (see `REPORT.md` in that "
        "directory for the full raw report)"
    )
    pairings_intro = (
        "Every row below is one `tool_model_key` (a single tool paired with a single model "
        "family) -- never a tool-only row, and never averaged across model families. Per-model "
        "results only; see the full report for category breakdowns and any explicitly labeled "
        "cross-model aggregate."
    )
    excluded_line = (
        "; ".join(
            f"{_fmt_field(entry.get('id')) or '(unnamed)'} ({entry['reason']})"
            if entry.get("reason")
            else _fmt_field(entry.get("id")) or "(unnamed)"
            for entry in bundle.exclusions
        )
        if bundle.exclusions
        else "none"
    )

    lines: list[str] = [
        f"# Benchmark Summary (README-Safe Template) -- {run_summary.get('run_id', 'unknown-run')}",
        "",
        f"> {README_SUMMARY_DISCLAIMER}",
        "",
        f"- **Methodology:** [{methodology_path.name}]({methodology_link})",
        raw_bundle_line,
        f"- **Corpus hash (SHA-256):** `{bundle.corpus_hash}`",
        f"- **Repo commit:** `{run_summary.get('repo_commit', 'unknown')}`",
        f"- **Excluded competitors:** {excluded_line}",
        "",
        "## Results (Strict Tool + Model Pairings)",
        "",
        pairings_intro,
        "",
    ]
    lines.extend(
        _table(
            [
                "Tool + Model",
                "Correctness",
                "Error Rate",
                "Timeout Rate",
                "Success Median (ms)",
                "Success p95 (ms)",
                "Failed-Cell Median (ms)",
                f"Tokens ({METHODOLOGY_TOKEN_LABEL})",
            ],
            [
                [
                    tool_model_key,
                    _fmt_score(
                        (stats := _basic_stats(by_tool_model[tool_model_key]))["scored_cells"],
                        stats["total_cells"],
                        stats["mean_correctness"],
                    ),
                    _fmt_pct(stats["error_rate"]),
                    _fmt_pct(stats["timeout_rate"]),
                    _fmt_ms(stats["success_latency_median_ms"]),
                    _fmt_ms(stats["success_latency_p95_ms"]),
                    _fmt_failed_latency(stats),
                    _fmt_tokens(stats),
                ]
                for tool_model_key in sorted(by_tool_model)
            ],
        )
    )
    lines.append("")

    return "\n".join(lines) + "\n"
