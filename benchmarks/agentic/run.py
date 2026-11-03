"""Agentic run orchestration: plan, artifacts, run summary and ``run_agentic``.

The run writes the fixed-flow artifact layout (``transcripts|tokens|latency|
scoring|failures/<cell>/<qid>.json``), so ``score`` and ``report`` read it
unchanged. A cell is one repeat of one (system, model): ``<system>--<family>--r<k>``
(owner ruling 7). Every JSON artifact is scanned for key-shaped strings first.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path
from typing import Any, Protocol

import benchmarks
from benchmarks.adapters.claude_tokens import ANTHROPIC_API_VERSION, DEFAULT_COUNT_MODEL
from benchmarks.agentic.bridge import SystemSpec, load_systems
from benchmarks.agentic.host import Launcher, LocalLauncher, WorkerJob, refuse_host_keys, run_jobs
from benchmarks.agentic.ledger import (
    PROVIDER_CAPS,
    Ledger,
    LedgerService,
    load_price_snapshot,
    refuse_stale_snapshot,
)
from benchmarks.agentic.loop import AIR_GAPPED_CAPS, HOSTED_CAPS
from benchmarks.agentic.prompt import SYSTEM_PROMPT
from benchmarks.agentic.protocol import assert_no_key_shaped
from benchmarks.agentic.worker import MAX_RERUNS
from benchmarks.model_matrix import METHODOLOGY_TOKEN_LABEL, ModelFamily, load_model_matrix
from benchmarks.runner import (
    BenchmarkCell,
    BenchmarkValidationError,
    Competitor,
    Question,
    _default_run_id,
    _environment_metadata,
    _load_questions,
    _load_yaml_mapping,
    _scoring_record,
    _utc_now,
    _write_json,
    _write_snapshot,
)

#: Answer-turn stop reasons that mean the output cap cut the answer; the pilot's 1% rule
#: reads their count. A Gemini CONTINUATION stop counts (owner ruling, round 3).
OUTPUT_CAP_STOPS = frozenset({"max_tokens", "max_output_tokens", "MAX_TOKENS", "CONTINUATION"})

_REPO_ROOT = Path(benchmarks.__file__).resolve().parent.parent
_CLIENT_PACKAGES = ("anthropic", "openai", "google-genai", "mcp")
_HOSTED_FIELDS = (
    "max_input_tokens",
    "max_output_tokens",
    "output_cap_includes_reasoning",
    "framing_allowance_tokens",
)
_TIMING_SUMS = ("grant_wait", "failed_attempt", "retry_wait")


@dataclass(frozen=True)
class AgenticConfig:
    corpus_path: Path
    matrix_path: Path
    manifest_path: Path
    prices_path: Path
    ledger_dir: Path
    out_dir: Path
    models: tuple[str, ...] | None = None  # family ids; None = every family the launcher supports
    systems: tuple[str, ...] | None = None
    questions: tuple[str, ...] | None = None
    repeats: int = 3
    seed: int | None = None  # None = int(sha256(run_id)[:8], 16)
    run_id: str | None = None
    dry_run: bool = False
    acknowledge_overrun: tuple[str, ...] = ()


def cell_id(system_id: str, family_id: str, repeat: int) -> str:
    return f"{system_id}--{family_id}--r{repeat}"


def tool_model_key(system_id: str, provider: str, model_id: str) -> str:
    return f"{system_id}:{provider}/{model_id}"


def plan_jobs(
    questions: Sequence[Question],
    families: Sequence[ModelFamily],
    systems: Sequence[SystemSpec],
    *,
    repeats: int,
    seed: int,
    matrix_dir: Path,
) -> list[WorkerJob]:
    """One job per (family, system), its trajectories shuffled by a seed per job."""
    jobs: list[WorkerJob] = []
    for family in families:
        if family.api == "scripted":
            script = family.raw.get("script")
            if not isinstance(script, str) or not script:
                raise BenchmarkValidationError(
                    f"scripted model family {family.id!r} needs 'script'"
                )
            # Absolute here, so the worker's make_client never depends on its cwd (ruling B9).
            raw = {**family.raw, "script": str((matrix_dir / script).resolve())}
            family = replace(family, raw=raw)
        caps = AIR_GAPPED_CAPS if family.arm == "air-gapped" else HOSTED_CAPS
        for system in systems:
            order = [(q, r) for q in questions for r in range(1, repeats + 1)]
            random.Random(f"{seed}/{family.id}/{system.id}").shuffle(order)
            trajectories = tuple(
                {"corpus_id": q.id, "prompt": q.prompt, "repeat": r} for q, r in order
            )
            jobs.append(WorkerJob(f"{family.id}--{system.id}", family, system, trajectories, caps))
    return jobs


def _write(path: Path, data: dict[str, Any]) -> None:
    assert_no_key_shaped(json.dumps(data, sort_keys=True), where=str(path))
    _write_json(path, data)


def _client_versions() -> dict[str, str]:
    return {name: version(name) for name in _CLIENT_PACKAGES}


class _HasId(Protocol):
    @property
    def id(self) -> str: ...


def _select[T: _HasId](items: Sequence[T], wanted: tuple[str, ...] | None, label: str) -> list[T]:
    if wanted is None:
        return list(items)
    known = {item.id for item in items}
    unknown = [w for w in wanted if w not in known]
    if unknown:
        raise BenchmarkValidationError(f"unknown {label} id(s): {unknown}")
    return [item for item in items if item.id in wanted]


@dataclass
class _FamilyStats:
    records: list[dict[str, Any]] = field(default_factory=list)
    retries: Counter[str] = field(default_factory=Counter)
    reruns: int = 0
    answer_turns: int = 0
    output_cap_stops: int = 0

    def add(self, record: dict[str, Any]) -> None:
        self.records.append(record)
        for call in record["model_calls"]:
            for attempt in call["attempts"][:-1]:  # every attempt but the last was retried
                self.retries[attempt["outcome"]] += 1

    def summary(self) -> dict[str, Any]:
        starts = [r["started_at"] for r in self.records]
        ends = [r["completed_at"] for r in self.records]
        first, last = (min(starts), max(ends)) if self.records else (None, None)
        wall = (
            (datetime.fromisoformat(last) - datetime.fromisoformat(first)).total_seconds()
            if first and last
            else 0.0
        )
        sums = {
            f"{name}_s": round(sum(r["timing"][f"{name}_ms"] for r in self.records) / 1000, 3)
            for name in _TIMING_SUMS
        }
        return {
            "retries": dict(self.retries),
            "reruns": self.reruns,
            "answer_turns": self.answer_turns,
            "output_cap_stops": self.output_cap_stops,
            "wall_s": round(wall, 3),
            **sums,
            "first_started_at": first,
            "last_completed_at": last,
        }


class _Collector:
    """Turns worker messages into the run's artifacts and summary counts."""

    def __init__(
        self, out: Path, questions: Mapping[str, Question], families: Sequence[ModelFamily]
    ):
        self.out = out
        self.questions = questions
        self.families = {f.id: _FamilyStats() for f in families}
        self.cells: set[str] = set()
        self.succeeded = 0
        self.failed = 0
        self.discarded: list[dict[str, Any]] = []
        self.not_run: list[dict[str, Any]] = []

    async def on_message(self, job: WorkerJob, message: dict[str, Any]) -> None:
        kind = message["type"]
        if kind == "system_info":
            info = {k: v for k, v in message.items() if k != "type"}
            _write(self.out / "systems" / f"{job.job_id}.json", info)
            return
        if kind not in ("trajectory", "discarded", "not_run"):
            return
        cell = cell_id(job.system.id, job.family.id, message["repeat"])
        corpus_id = message["corpus_id"]
        if kind == "not_run":
            self.not_run.append(
                {
                    "cell": cell,
                    "corpus_id": corpus_id,
                    "reason": message["reason"],
                    "attempt": message.get("attempt"),
                }
            )
            return
        record = message["record"]
        stats = self.families[job.family.id]
        stats.add(record)
        if kind == "discarded":
            reason, attempt = message["reason"], message["attempt"]
            self.discarded.append(
                {"cell": cell, "corpus_id": corpus_id, "reason": reason, "attempt": attempt}
            )
            if reason == "retries_exhausted" and attempt <= MAX_RERUNS:
                stats.reruns += 1
            path = self.out / "discarded" / cell / f"{corpus_id}--attempt{attempt}.json"
            _write(path, {**record, **self._ident(job, cell, message), "reason": reason})
            return
        self._write_cell(job, cell, message, record, stats)

    def _ident(self, job: WorkerJob, cell: str, message: dict[str, Any]) -> dict[str, Any]:
        family, system = job.family, job.system
        return {
            "competitor_id": cell,
            "corpus_id": message["corpus_id"],
            "system_id": system.id,
            "model_family_id": family.id,
            "provider": family.provider,
            "model_id": family.model_id,
            "repeat": message["repeat"],
            "attempt": message.get("attempt"),
            "tool_model_key": tool_model_key(system.id, family.provider, family.model_id),
        }

    def _write_cell(
        self,
        job: WorkerJob,
        cell: str,
        message: dict[str, Any],
        record: dict[str, Any],
        stats: _FamilyStats,
    ) -> None:
        ident = self._ident(job, cell, message)
        ident.pop("attempt")
        corpus_id, key = ident["corpus_id"], ident["tool_model_key"]
        status, error = record["status"], record["error"]
        question = self.questions[corpus_id]
        base = {"competitor_id": cell, "tool_model_key": key, "corpus_id": corpus_id}
        _write(self.out / "transcripts" / cell / f"{corpus_id}.json", {**record, **ident})
        _write(
            self.out / "scoring" / cell / f"{corpus_id}.json",
            _scoring_record(
                BenchmarkCell(Competitor(cell, "agentic", {}), question),
                status=status,
                tool_model_key=key,
                error=error,
            ),
        )
        _write(
            self.out / "tokens" / cell / f"{corpus_id}.json",
            {
                **base,
                "token_label": METHODOLOGY_TOKEN_LABEL,
                "input_characters": len(question.prompt),
                "output_characters": len(record["answer"]),
                "count_model": DEFAULT_COUNT_MODEL,
                "anthropic_version": ANTHROPIC_API_VERSION,
                "status": "placeholder",
                "client_wrapped_tokens": None,
                "raw_payload_tokens": None,
                "approximation": False,
                "notes": "counted after the run (design §8)",
            },
        )
        _write(
            self.out / "latency" / cell / f"{corpus_id}.json",
            {
                **base,
                "status": status,
                "error_category": None if error is None else error["category"],
                "latency_ms": record["timing"]["wall_ms"],
                "started_at": record["started_at"],
                "completed_at": record["completed_at"],
                "timing": record["timing"],
            },
        )
        if status == "failed":
            self.failed += 1
            _write(
                self.out / "failures" / cell / f"{corpus_id}.json",
                {
                    **base,
                    "status": "failed",
                    "error": error,
                    "included_in_correctness_denominator": True,
                    "correctness_score": 0.0,
                },
            )
        else:
            self.succeeded += 1
            stats.answer_turns += 1
            if record["model_calls"][-1]["stop_reason"] in OUTPUT_CAP_STOPS:
                stats.output_cap_stops += 1
        self.cells.add(cell)


def run_agentic(
    config: AgenticConfig,
    *,
    launcher: Launcher | None = None,
    caps: Mapping[str, Decimal] = PROVIDER_CAPS,
    today: date | None = None,
) -> dict[str, Any]:
    """Validate everything, then run every planned trajectory; return the run summary."""
    refuse_host_keys(os.environ)
    launcher = launcher or LocalLauncher()
    out = config.out_dir
    if out.exists() and any(out.iterdir()):
        raise BenchmarkValidationError(
            f"output directory is not empty: {out}; use a new, empty --out for each run"
        )
    if config.repeats < 1:
        raise BenchmarkValidationError("--repeats must be at least 1")
    corpus = _load_questions(_load_yaml_mapping(config.corpus_path, "corpus"))
    questions = _select(corpus, config.questions, "corpus question")
    matrix = load_model_matrix(config.matrix_path).model_families
    families = (
        _select(matrix, config.models, "model family")
        if config.models is not None
        else [f for f in matrix if launcher.supports(f)]
    )
    if not families:
        raise BenchmarkValidationError("no model family to run")
    unsupported = [f.id for f in families if not launcher.supports(f)]
    if unsupported:
        raise BenchmarkValidationError(
            f"{type(launcher).__name__} cannot run {unsupported}: the local launcher runs only "
            "scripted models; live models need PR 4's container launcher"
        )
    systems = _select(load_systems(config.manifest_path), config.systems, "system")
    snapshot = load_price_snapshot(config.prices_path)
    for family in families:
        if family.output_cap_includes_reasoning is False:
            raise BenchmarkValidationError(
                f"model family {family.id!r} sets output_cap_includes_reasoning: false; the "
                "research note shows thinking inside the cap on every hosted model"
            )
        if family.provider in caps:  # metered
            if family.model_id not in snapshot.models:
                raise BenchmarkValidationError(f"no price snapshot entry for {family.model_id!r}")
            missing = [name for name in _HOSTED_FIELDS if getattr(family, name) is None]
            if missing:
                raise BenchmarkValidationError(f"model family {family.id!r} must set {missing}")
    if any(family.api != "scripted" for family in families):
        refuse_stale_snapshot(snapshot, today=today or datetime.now(UTC).date())

    run_id = config.run_id or _default_run_id()
    seed = (
        config.seed
        if config.seed is not None
        else int(hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:8], 16)
    )
    jobs = plan_jobs(
        questions,
        families,
        systems,
        repeats=config.repeats,
        seed=seed,
        matrix_dir=config.matrix_path.parent,
    )
    out.mkdir(parents=True, exist_ok=True)
    snapshots = out / "snapshots"
    _write_snapshot(config.corpus_path, snapshots / "corpus.yml")
    _write_snapshot(config.manifest_path, snapshots / "competitor-manifest.yml")
    _write_snapshot(config.matrix_path, snapshots / "model-matrix.yml")
    _write_snapshot(config.prices_path, snapshots / "price-snapshot.yml")
    (snapshots / "system-prompt.txt").write_text(SYSTEM_PROMPT, encoding="utf-8")
    environment = {
        **_environment_metadata(run_id=run_id, dry_run=config.dry_run),
        "mode": "agentic",
        "seed": seed,
        "client_versions": _client_versions(),
    }
    _write(out / "environment.json", environment)
    planned = [
        {
            "cell": cell_id(job.system.id, job.family.id, t["repeat"]),
            "competitor_id": cell_id(job.system.id, job.family.id, t["repeat"]),
            "corpus_id": t["corpus_id"],
            "system_id": job.system.id,
            "model_family_id": job.family.id,
            "repeat": t["repeat"],
        }
        for job in jobs
        for t in job.trajectories
    ]
    _write(out / "planned-cells.json", {"cells": planned})
    summary: dict[str, Any] = {
        "run_id": run_id,
        "dry_run": config.dry_run,
        "mode": "agentic",
        "started_at": _utc_now(),
        "corpus_path": str(config.corpus_path),
        "manifest_path": str(config.manifest_path),
        "artifact_root": str(out),
        "repo_commit": environment["repo_commit"],
        "seed": seed,
        "repeats": config.repeats,
        "models": [f.id for f in families],
        "systems": [s.id for s in systems],
        "corpus_ids": [q.id for q in questions],
        "planned_cells": len(planned),
    }
    if config.dry_run:  # never opens the ledger
        return summary

    ledger = Ledger.open(config.ledger_dir, run_id=run_id, caps=caps)
    try:
        for provider in config.acknowledge_overrun:
            ledger.acknowledge_overrun(provider)
        overrun = sorted({f.provider for f in families if ledger.stopped(f.provider) == "overrun"})
        if overrun:
            raise BenchmarkValidationError(
                f"{overrun} stopped on a bound overrun: correct the bound, record the overrun, "
                "then rerun with --acknowledge-overrun"
            )
        spent_before = {provider: ledger.spent(provider) for provider in caps}
        prices = {f.model_id: snapshot.models[f.model_id] for f in families if f.provider in caps}
        service = LedgerService(ledger, prices, {f.model_id: f for f in families})
        collector = _Collector(out, {q.id: q for q in questions}, families)
        asyncio.run(
            run_jobs(
                jobs,
                launcher=launcher,
                ledger=service,
                on_message=collector.on_message,
                log_dir=out / "logs",
                cwd=_REPO_ROOT,
            )
        )
        events = "".join(json.dumps(e, sort_keys=True) + "\n" for e in ledger.events(run_id))
        assert_no_key_shaped(events, where="ledger-events.jsonl")
        (out / "ledger-events.jsonl").write_text(events, encoding="utf-8")
        scored = collector.succeeded + collector.failed
        summary |= {
            "completed_at": _utc_now(),
            "scored_cells": scored,
            "correctness_denominator_cells": scored,
            "succeeded_cells": collector.succeeded,
            "failed_cells": collector.failed,
            "failed_cells_included_in_correctness_denominator": True,
            "competitors": sorted(collector.cells),
            "discarded": collector.discarded,
            "not_run": collector.not_run,
            "families": {fid: stats.summary() for fid, stats in collector.families.items()},
            "ledger": {
                provider: {
                    "spent_before": str(spent_before[provider]),
                    "spent_after": str(ledger.spent(provider)),
                    "cap": str(caps[provider]),
                    "stopped": ledger.stopped(provider),
                }
                for provider in caps
            },
            "price_snapshot": {
                "captured_on": snapshot.captured_on.isoformat(),
                "expires_on": None
                if snapshot.expires_on is None
                else snapshot.expires_on.isoformat(),
            },
        }
        _write(out / "run-summary.json", summary)
        return summary
    finally:
        ledger.close()
