"""Benchmark runner artifact plumbing.

This module intentionally contains only local fake/baseline execution. Real
provider adapters and report generation are follow-up work packages.
"""

from __future__ import annotations

import json
import platform
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_EXECUTABLE_ADAPTERS = {"fake", "no-mcp-baseline", "no_mcp_baseline"}


class BenchmarkValidationError(ValueError):
    """Raised when corpus or manifest input is not runnable."""


class BenchmarkCellFailure(RuntimeError):
    """A recorded failure for one competitor/question cell."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


@dataclass(frozen=True)
class BenchmarkConfig:
    """Benchmark runner configuration."""

    corpus_path: Path
    manifest_path: Path
    out_dir: Path
    run_id: str | None = None
    dry_run: bool = False


@dataclass(frozen=True)
class Question:
    """Validated corpus question."""

    id: str
    prompt: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class Competitor:
    """Validated competitor manifest entry."""

    id: str
    adapter: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class BenchmarkCell:
    """One competitor/question execution cell."""

    competitor: Competitor
    question: Question

    @property
    def cell_id(self) -> str:
        return f"{self.competitor.id}/{self.question.id}"


def run_benchmark(config: BenchmarkConfig) -> dict[str, Any]:
    """Run or dry-run a benchmark and write the stable artifact layout."""
    corpus_data = _load_yaml_mapping(config.corpus_path, "corpus")
    manifest_data = _load_yaml_mapping(config.manifest_path, "manifest")
    questions = _load_questions(corpus_data)
    competitors = _load_competitors(manifest_data)
    cells = [
        BenchmarkCell(competitor=competitor, question=question)
        for competitor in competitors
        for question in questions
    ]

    run_id = config.run_id or _default_run_id()
    run_dir = config.out_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_artifact(run_dir / "snapshots" / "competitor-manifest.yml", manifest_data)
    _write_artifact(run_dir / "snapshots" / "corpus.yml", corpus_data)

    started_at = _utc_now()
    environment = _environment_metadata(run_id=run_id, dry_run=config.dry_run)
    _write_json(run_dir / "environment.json", environment)

    planned_cells = [
        {"competitor_id": cell.competitor.id, "corpus_id": cell.question.id}
        for cell in cells
    ]
    _write_json(run_dir / "planned-cells.json", {"cells": planned_cells})

    succeeded = 0
    failed = 0
    scored_cells = 0
    if not config.dry_run:
        for cell in cells:
            result = _execute_cell(cell)
            _write_cell_artifacts(run_dir, cell, result)
            scored_cells += 1
            if result["status"] == "succeeded":
                succeeded += 1
            else:
                failed += 1

    summary = {
        "run_id": run_id,
        "dry_run": config.dry_run,
        "started_at": started_at,
        "completed_at": _utc_now(),
        "corpus_path": str(config.corpus_path),
        "manifest_path": str(config.manifest_path),
        "artifact_root": str(run_dir),
        "repo_commit": environment["repo_commit"],
        "external_provider_calls": False,
        "planned_cells": len(cells),
        "correctness_denominator_cells": len(cells),
        "scored_cells": scored_cells,
        "succeeded_cells": succeeded,
        "failed_cells": failed,
        "failed_cells_included_in_correctness_denominator": True,
        "competitors": [competitor.id for competitor in competitors],
        "corpus_ids": [question.id for question in questions],
    }
    _write_json(run_dir / "run-summary.json", summary)
    return summary


def _load_yaml_mapping(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        raise BenchmarkValidationError(f"{label} file does not exist: {path}")
    with path.open("r", encoding="utf-8") as file:
        try:
            data = yaml.safe_load(file)
        except yaml.YAMLError as exc:
            raise BenchmarkValidationError(f"{label} file is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise BenchmarkValidationError(f"{label} file must contain a YAML mapping")
    return data


def _load_questions(data: dict[str, Any]) -> list[Question]:
    items = data.get("questions")
    if not isinstance(items, list) or not items:
        raise BenchmarkValidationError("corpus must contain a non-empty 'questions' list")

    questions: list[Question] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise BenchmarkValidationError(f"corpus question at index {index} must be a mapping")
        question_id = _required_safe_id(item, "id", f"corpus question at index {index}")
        if question_id in seen:
            raise BenchmarkValidationError(f"duplicate corpus id: {question_id}")
        seen.add(question_id)
        prompt = item.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise BenchmarkValidationError(f"corpus question {question_id} must include prompt")
        questions.append(Question(id=question_id, prompt=prompt, raw=item))
    return questions


def _load_competitors(data: dict[str, Any]) -> list[Competitor]:
    items = data.get("competitors")
    if not isinstance(items, list) or not items:
        raise BenchmarkValidationError("manifest must contain a non-empty 'competitors' list")

    competitors: list[Competitor] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise BenchmarkValidationError(f"competitor at index {index} must be a mapping")
        competitor_id = _required_safe_id(item, "id", f"competitor at index {index}")
        if competitor_id in seen:
            raise BenchmarkValidationError(f"duplicate competitor id: {competitor_id}")
        seen.add(competitor_id)
        adapter = item.get("adapter")
        if not isinstance(adapter, str) or not adapter.strip():
            raise BenchmarkValidationError(f"competitor {competitor_id} must include adapter")
        competitors.append(Competitor(id=competitor_id, adapter=adapter, raw=item))
    return competitors


def _required_safe_id(item: dict[str, Any], key: str, label: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BenchmarkValidationError(f"{label} is missing required {key!r}")
    if not _SAFE_ID.fullmatch(value):
        raise BenchmarkValidationError(
            f"{label} has unsafe {key!r}: {value!r}; use letters, numbers, dots, dashes, "
            "or underscores"
        )
    return value


def _execute_cell(cell: BenchmarkCell) -> dict[str, Any]:
    started = time.perf_counter()
    started_at = _utc_now()
    status = "succeeded"
    error: dict[str, str] | None = None
    answer = ""

    try:
        answer = _fake_provider_answer(cell)
    except BenchmarkCellFailure as exc:
        status = "failed"
        error = {"category": exc.category, "type": type(exc).__name__, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001 - failures are benchmark artifacts
        status = "failed"
        error = {"category": "tool_failure", "type": type(exc).__name__, "message": str(exc)}

    latency_ms = round((time.perf_counter() - started) * 1000, 3)
    completed_at = _utc_now()
    tool_model_key = _tool_model_key(cell.competitor)
    transcript = {
        "competitor_id": cell.competitor.id,
        "tool_model_key": tool_model_key,
        "corpus_id": cell.question.id,
        "adapter": cell.competitor.adapter,
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "messages": [{"role": "user", "content": cell.question.prompt}],
        "answer": answer,
        "error": error,
        "external_provider_calls": False,
    }
    token_record = {
        "competitor_id": cell.competitor.id,
        "tool_model_key": tool_model_key,
        "corpus_id": cell.question.id,
        "status": "placeholder",
        "input_characters": len(cell.question.prompt),
        "output_characters": len(answer),
        "client_wrapped_tokens": None,
        "raw_payload_tokens": None,
        "notes": "Token counting integration is intentionally out of scope for issue #72.",
    }
    latency_record = {
        "competitor_id": cell.competitor.id,
        "tool_model_key": tool_model_key,
        "corpus_id": cell.question.id,
        "status": status,
        "error_category": None if error is None else error["category"],
        "latency_ms": latency_ms,
        "started_at": started_at,
        "completed_at": completed_at,
    }
    scoring_record = _scoring_record(
        cell,
        status=status,
        tool_model_key=tool_model_key,
        error=error,
    )
    return {
        "status": status,
        "transcript": transcript,
        "tokens": token_record,
        "latency": latency_record,
        "scoring": scoring_record,
        "failure": error,
    }


def _fake_provider_answer(cell: BenchmarkCell) -> str:
    adapter = cell.competitor.adapter
    if adapter not in _EXECUTABLE_ADAPTERS:
        raise BenchmarkCellFailure(
            "tool_failure", f"adapter {adapter!r} is not implemented in issue #72 runner"
        )
    forced_failure = cell.competitor.raw.get("force_failure")
    if forced_failure:
        category = _forced_failure_category(forced_failure)
        raise BenchmarkCellFailure(category, f"forced fake provider {category}")
    answer = cell.competitor.raw.get("fake_answer")
    if isinstance(answer, str):
        return answer
    return f"[fake:{cell.competitor.id}] {cell.question.prompt}"


def _forced_failure_category(value: object) -> str:
    if value is True:
        return "tool_failure"
    if value in {"tool_failure", "timeout", "mcp_protocol_crash"}:
        return str(value)
    return "tool_failure"


def _tool_model_key(competitor: Competitor) -> str:
    provider = competitor.raw.get("provider")
    model = competitor.raw.get("model")
    if isinstance(provider, str) and provider and isinstance(model, str) and model:
        return f"{competitor.id}:{provider}/{model}"
    if isinstance(model, str) and model:
        return f"{competitor.id}:{model}"
    return competitor.id


def _scoring_record(
    cell: BenchmarkCell,
    *,
    status: str,
    tool_model_key: str,
    error: dict[str, str] | None,
) -> dict[str, Any]:
    base = {
        "competitor_id": cell.competitor.id,
        "tool_model_key": tool_model_key,
        "corpus_id": cell.question.id,
        "included_in_correctness_denominator": True,
        "denominator_unit": "corpus_query",
        "error_category": None if error is None else error["category"],
        "error": error,
    }
    if status == "failed":
        return {
            **base,
            "status": "failed",
            "score": 0.0,
            "requires_manual_scoring": False,
            "notes": (
                "Failed query is explicitly scored as 0.0 and remains in the "
                "correctness denominator."
            ),
        }
    return {
        **base,
        "status": "placeholder",
        "score": None,
        "requires_manual_scoring": True,
        "notes": "Correctness scoring automation is intentionally out of scope for issue #72.",
    }


def _write_cell_artifacts(run_dir: Path, cell: BenchmarkCell, result: dict[str, Any]) -> None:
    competitor_id = cell.competitor.id
    corpus_id = cell.question.id
    _write_json(run_dir / "transcripts" / competitor_id / f"{corpus_id}.json", result["transcript"])
    _write_json(run_dir / "tokens" / competitor_id / f"{corpus_id}.json", result["tokens"])
    _write_json(run_dir / "latency" / competitor_id / f"{corpus_id}.json", result["latency"])
    _write_json(run_dir / "scoring" / competitor_id / f"{corpus_id}.json", result["scoring"])
    if result["failure"] is not None:
        _write_json(
            run_dir / "failures" / competitor_id / f"{corpus_id}.json",
            {
                "competitor_id": competitor_id,
                "corpus_id": corpus_id,
                "status": "failed",
                "tool_model_key": result["transcript"]["tool_model_key"],
                "error": result["failure"],
                "included_in_correctness_denominator": True,
                "correctness_score": 0.0,
            },
        )


def _environment_metadata(*, run_id: str, dry_run: bool) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "created_at": _utc_now(),
        "dry_run": dry_run,
        "repo_commit": _repo_commit_sha(),
        "python_version": sys.version,
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "system": platform.system(),
        "machine": platform.machine(),
        "benchmark_runner": "benchmarks",
        "external_provider_calls": False,
    }


def _repo_commit_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return "unknown"
    return result.stdout.strip() or "unknown"


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_artifact(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")


def _default_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
