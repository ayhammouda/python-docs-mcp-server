"""Agentic run orchestration: plan, artifacts, run summary and ``run_agentic``.

The run writes the fixed-flow artifact layout (``transcripts|tokens|latency|
scoring|failures/<cell>/<qid>.json``), so ``score`` and ``report`` read it
unchanged. A cell is one repeat of one (system, model): ``<system>--<family>--r<k>``
(owner ruling 7). Every JSON artifact is scanned for key-shaped strings first.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import random
import signal
import sys
from collections import Counter
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

import platformdirs

import benchmarks
from benchmarks.adapters.claude_tokens import ANTHROPIC_API_VERSION, DEFAULT_COUNT_MODEL
from benchmarks.agentic.airgap import (
    AirGapWatch,
    host_offline_checks,
    ollama_preflight,
    write_airgap_evidence,
)
from benchmarks.agentic.bridge import SystemSpec, systems_from_mapping
from benchmarks.agentic.cleanup import add_cleanup_cause
from benchmarks.agentic.egress import write_egress_summary
from benchmarks.agentic.host import Launcher, LocalLauncher, WorkerJob, refuse_host_keys, run_jobs
from benchmarks.agentic.inputs import CapturedInput
from benchmarks.agentic.ledger import (
    PROVIDER_CAPS,
    Ledger,
    LedgerService,
    price_snapshot_from_mapping,
    refuse_stale_snapshot,
)
from benchmarks.agentic.live import (
    authorize_live,
    confirmation_text,
    full_run_gate,
    interactive_confirm,
    provider_plans,
    reference_rates,
)
from benchmarks.agentic.loop import AIR_GAPPED_CAPS, HOSTED_CAPS
from benchmarks.agentic.prompt import SYSTEM_PROMPT
from benchmarks.agentic.protocol import (
    KeyLeakError,
    assert_no_key_shaped_json,
    redact_key_shaped_text,
    scanned_json_bytes,
)
from benchmarks.agentic.protocol import (
    write_json_evidence as _write,
)
from benchmarks.agentic.sandbox import ContainerLauncher
from benchmarks.agentic.worker import _client_versions
from benchmarks.model_matrix import (
    _HOSTED_REQUIRED_FIELDS,
    METHODOLOGY_TOKEN_LABEL,
    ModelFamily,
    model_matrix_from_mapping,
)
from benchmarks.runner import (
    BenchmarkCell,
    BenchmarkValidationError,
    Competitor,
    Question,
    _default_run_id,
    _environment_metadata,
    _load_questions,
    _scoring_record,
    _utc_now,
)

#: Answer-turn stop reasons that mean the output cap cut the answer; the pilot's 1% rule
#: reads their count. A Gemini CONTINUATION stop counts (owner ruling, round 3).
OUTPUT_CAP_STOPS = frozenset({"max_tokens", "max_output_tokens", "MAX_TOKENS", "CONTINUATION"})

_REPO_ROOT = Path(benchmarks.__file__).resolve().parent.parent


def default_ledger_dir() -> Path:
    """One durable per-user ledger, so every run's spend counts against the same caps."""
    return Path(platformdirs.user_data_dir("mcp-python-docs-benchmarks")) / "ledger"


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
    reference_run: Path | None = None  # a finished pilot run, for the full-run gate


async def _until_signalled(run: Coroutine[Any, Any, None]) -> None:
    """Await ``run``; SIGTERM or SIGHUP cancels it, so every worker is torn down as on Ctrl-C.

    Workers run in process groups of their own, so no signal sent to the host's group reaches
    them: only the host's teardown can end them. An ignored signal stays ignored, a thread
    that cannot take signals runs without the handlers, and each disposition the handlers
    replaced is restored afterwards.
    """
    task = asyncio.create_task(run)
    loop = asyncio.get_running_loop()
    signal_seen = False

    def interrupt(signum: signal.Signals) -> None:
        nonlocal signal_seen
        if not signal_seen:
            signal_seen = True
            if not task.cancelling():
                task.cancel(f"signal {signum.name}")

    wanted = [  # an ignored signal stays ignored, as under nohup
        (signum, previous)
        for signum in (signal.SIGTERM, signal.SIGHUP)
        if (previous := signal.getsignal(signum)) is not signal.SIG_IGN
    ]
    installed: list[tuple[signal.Signals, Any]] = []  # each with the disposition it replaced
    try:
        for signum, previous in wanted:
            loop.add_signal_handler(signum, interrupt, signum)
            installed.append((signum, previous))
    except (RuntimeError, NotImplementedError, ValueError) as exc:
        # Not the main thread, or a loop without signal support: run without those handlers.
        skipped = ", ".join(signum.name for signum, _ in wanted[len(installed) :])
        print(
            f"agentic run: signal handlers not installed for {skipped} "
            f"({type(exc).__name__}: {exc}); no teardown on those signals",
            file=sys.stderr,
        )
    try:
        await task
    finally:
        for signum, previous in installed:
            loop.remove_signal_handler(signum)  # leaves SIG_DFL behind
            signal.signal(signum, signal.SIG_DFL if previous is None else previous)


def limits_mismatch(reported: Mapping[str, Any] | None, family: ModelFamily) -> list[str]:
    """Each difference between a worker's reported model limits and the model matrix.

    The maintainer corrects model-matrix.yml by hand; the run itself goes on.
    """
    if reported is None:
        return []
    if "error" in reported:
        return [f"model limits could not be read ({reported['error']})"]
    problems = []
    max_input = reported.get("max_input_tokens")
    if max_input is not None and max_input != family.max_input_tokens:
        problems.append(
            f"max_input_tokens {max_input} != max_input_tokens {family.max_input_tokens} "
            "in the model matrix"
        )
    max_tokens, cap = reported.get("max_tokens"), family.max_output_tokens
    if max_tokens is not None and cap is not None and max_tokens < cap:
        problems.append(f"max_tokens {max_tokens} < max_output_tokens {cap} in the model matrix")
    return problems


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


class _HasId(Protocol):
    @property
    def id(self) -> str:
        """The id a selection names."""
        raise NotImplementedError


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
    # Running aggregates only: a record can be megabytes, and a run holds thousands.
    first_started_at: str | None = None
    last_completed_at: str | None = None
    timing_ms: Counter[str] = field(default_factory=Counter)
    retries: Counter[str] = field(default_factory=Counter)
    timeouts: int = 0  # every model attempt that hit its own timeout
    reruns: int = 0
    answer_turns: int = 0
    output_cap_stops: int = 0

    def add(self, record: dict[str, Any]) -> None:
        self.first_started_at = min(filter(None, (self.first_started_at, record["started_at"])))
        self.last_completed_at = max(filter(None, (self.last_completed_at, record["completed_at"])))
        for name in _TIMING_SUMS:
            self.timing_ms[name] += record["timing"][f"{name}_ms"]
        for call in record["model_calls"]:
            for attempt in call["attempts"][:-1]:  # every attempt but the last was retried
                self.retries[attempt["outcome"]] += 1
            self.timeouts += sum(a["outcome"] == "timeout" for a in call["attempts"])
            if call["tool_calls_requested"] == 0:  # an answer turn, whatever the outcome
                self.answer_turns += 1
                self.output_cap_stops += call["stop_reason"] in OUTPUT_CAP_STOPS

    def summary(self) -> dict[str, Any]:
        first, last = self.first_started_at, self.last_completed_at
        wall = (
            (datetime.fromisoformat(last) - datetime.fromisoformat(first)).total_seconds()
            if first and last
            else 0.0
        )
        sums = {f"{name}_s": round(self.timing_ms[name] / 1000, 3) for name in _TIMING_SUMS}
        return {
            "retries": dict(self.retries),
            "timeouts": self.timeouts,
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
        self.worker_errors: list[dict[str, Any]] = []
        self.limits_mismatch: list[str] = []

    async def on_message(self, job: WorkerJob, message: dict[str, Any]) -> None:
        kind = message["type"]
        if kind == "system_info":
            info = {k: v for k, v in message.items() if k != "type"}
            _write(self.out / "systems" / f"{job.job_id}.json", info)
            self.limits_mismatch += [
                f"{job.job_id}: {line}"
                for line in limits_mismatch(message.get("model_limits"), job.family)
            ]
            return
        if kind == "worker_error":
            self.worker_errors.append({"job_id": job.job_id, "reason": message["reason"]})
            return
        if kind not in ("trajectory", "discarded", "not_run"):
            return
        corpus_id, repeat = job.report_coordinates(message)
        cell = cell_id(job.system.id, job.family.id, repeat)
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
        stats.reruns += message["attempt"] > 1  # a rerun counts once it has run
        ident = self._ident(job, cell, corpus_id, repeat)
        if kind == "discarded":
            reason, attempt = message["reason"], message["attempt"]
            self.discarded.append(
                {"cell": cell, "corpus_id": corpus_id, "reason": reason, "attempt": attempt}
            )
            path = self.out / "discarded" / cell / f"{corpus_id}--attempt{attempt}.json"
            _write(path, {**record, **ident, "attempt": attempt, "reason": reason})
            return
        self._write_cell(ident, record)

    def _ident(self, job: WorkerJob, cell: str, corpus_id: str, repeat: int) -> dict[str, Any]:
        family, system = job.family, job.system
        return {
            "competitor_id": cell,
            "corpus_id": corpus_id,
            "system_id": system.id,
            "model_family_id": family.id,
            "provider": family.provider,
            "model_id": family.model_id,
            "repeat": repeat,
            "tool_model_key": tool_model_key(system.id, family.provider, family.model_id),
        }

    def _write_cell(self, ident: dict[str, Any], record: dict[str, Any]) -> None:
        cell = ident["competitor_id"]
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
                # Successful work plus overhead: no grant wait, failed attempt or backoff.
                "latency_ms": round(
                    record["timing"]["used_ms"] + record["timing"]["overhead_ms"], 3
                ),
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
        self.cells.add(cell)


def _finalization_failure(primary: BaseException | None, later: BaseException) -> BaseException:
    if primary is None:
        return later
    if isinstance(primary, Exception):
        # Preserve an operator interrupt even if an earlier ordinary operation failed.
        if not isinstance(later, Exception):
            add_cleanup_cause(later, BenchmarkValidationError(redact_key_shaped_text(str(primary))))
            return later
        if isinstance(later, KeyLeakError) and not isinstance(primary, KeyLeakError):
            return KeyLeakError(
                f"{redact_key_shaped_text(str(later))}; "
                f"earlier failure: {redact_key_shaped_text(str(primary))}"
            )
    detail = f"{type(later).__name__}: {redact_key_shaped_text(str(later))}"
    add_cleanup_cause(primary, BenchmarkValidationError(detail))
    return primary


def run_agentic(
    config: AgenticConfig,
    *,
    launcher: Launcher | None = None,
    caps: Mapping[str, Decimal] = PROVIDER_CAPS,
    today: date | None = None,
    confirm: Callable[[str], bool] = interactive_confirm,
    offline_checks: Callable[[], list[dict[str, Any]]] = host_offline_checks,
    preflight: Callable[[str], dict[str, Any]] = ollama_preflight,
) -> dict[str, Any]:
    """Validate everything, then run every planned trajectory; return the run summary.

    A run with any live family passes the full-run gate and ``authorize_live`` (``confirm``)
    after the ledger opens and before any key is read or any container starts.
    """
    refuse_host_keys(os.environ)
    for value in vars(config).values():
        assert_no_key_shaped_json(
            str(value) if isinstance(value, Path) else value, where="run options"
        )
    corpus_input = CapturedInput.read(config.corpus_path, "corpus.yml", "corpus")
    matrix_input = CapturedInput.read(config.matrix_path, "model-matrix.yml", "model matrix")
    manifest_input = CapturedInput.read(config.manifest_path, "competitor-manifest.yml", "manifest")
    prices_input = CapturedInput.read(config.prices_path, "price-snapshot.yml", "price snapshot")
    launcher = launcher or LocalLauncher()
    out = config.out_dir
    if out.exists() and any(out.iterdir()):
        raise BenchmarkValidationError(
            f"output directory is not empty: {out}; use a new, empty --out for each run"
        )
    if config.repeats < 1:
        raise BenchmarkValidationError("--repeats must be at least 1")
    corpus = _load_questions(corpus_input.mapping)
    questions = _select(corpus, config.questions, "corpus question")
    matrix = model_matrix_from_mapping(matrix_input.mapping).model_families
    families = (
        _select(matrix, config.models, "model family")
        if config.models is not None
        # The air-gapped arm is named with --models, never run by default (round-2 D11).
        else [f for f in matrix if f.arm == "hosted" and launcher.supports(f)]
    )
    if not families:
        raise BenchmarkValidationError("no model family to run")
    unsupported = [f.id for f in families if not launcher.supports(f)]
    if unsupported:
        raise BenchmarkValidationError(
            f"{type(launcher).__name__} cannot run {unsupported}: the local launcher runs only "
            "scripted models; use --launcher container"
        )
    if len({f.arm == "air-gapped" for f in families}) > 1:
        raise BenchmarkValidationError(
            "the air-gapped arm runs alone, with the host offline: name only air-gapped "
            "families, or only others, in --models"
        )
    requested_systems = config.systems
    if families[0].arm == "air-gapped":
        for family in families:
            if family.api != "scripted" and (
                family.provider != "ollama" or family.api != "openai-chat-completions"
            ):
                raise BenchmarkValidationError(
                    f"the air-gapped arm requires local Ollama for live family {family.id!r}"
                )
        offline_systems = ("python-docs", "no-docs")
        if requested_systems is None:
            requested_systems = offline_systems
        elif not requested_systems or not set(requested_systems).issubset(offline_systems):
            raise BenchmarkValidationError(
                f"the air-gapped arm permits only {offline_systems} in --systems, "
                f"got {requested_systems}"
            )
    systems = _select(systems_from_mapping(manifest_input.mapping), requested_systems, "system")
    snapshot = price_snapshot_from_mapping(prices_input.mapping)
    for family in families:
        if family.output_cap_includes_reasoning is False:
            raise BenchmarkValidationError(
                f"model family {family.id!r} sets output_cap_includes_reasoning: false; the "
                "research note shows thinking inside the cap on every hosted model"
            )
        if family.provider in caps:  # metered
            if family.model_id not in snapshot.models:
                raise BenchmarkValidationError(f"no price snapshot entry for {family.model_id!r}")
            missing = [name for name in _HOSTED_REQUIRED_FIELDS if getattr(family, name) is None]
            if missing:
                raise BenchmarkValidationError(f"model family {family.id!r} must set {missing}")
    if any(family.api != "scripted" and family.provider in caps for family in families):
        # Only a live, metered family is priced: the air-gapped arm never reads the snapshot.
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
    rates = reference_rates(config.reference_run) if config.reference_run else None
    out.mkdir(parents=True, exist_ok=True)
    snapshots = out / "snapshots"
    snapshots.mkdir()
    for source in (corpus_input, matrix_input, manifest_input, prices_input):
        (snapshots / source.name).write_bytes(source.content)
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
    spent_before = {provider: ledger.spent(provider) for provider in caps}  # after any recovery
    collector = _Collector(out, {q.id: q for q in questions}, families)
    # The air-gapped arm runs alone (checked above), so either every family is in it or none.
    watch = AirGapWatch(out, offline_checks) if families[0].arm == "air-gapped" else None

    async def on_message(job: WorkerJob, message: dict[str, Any]) -> None:
        await collector.on_message(job, message)
        if watch is not None and message["type"] in ("trajectory", "discarded"):
            # Before the host acknowledges it, so before the worker starts its next trajectory.
            cell = cell_id(job.system.id, job.family.id, message["repeat"])
            await watch.check(f"after {cell}/{message['corpus_id']}")

    async def run_arm() -> None:
        if watch is not None:
            await watch.check("before_first")
        await run_jobs(
            jobs,
            launcher=launcher,
            ledger=service,
            on_message=on_message,
            log_dir=out / "logs",
            cwd=_REPO_ROOT,
        )
        if watch is not None:
            await watch.check("after_last")

    def write_outcome() -> None:
        """ledger-events.jsonl and run-summary.json, for a completed or an aborted run."""
        run_events = ledger.invocation_events()
        events = b"".join(
            scanned_json_bytes(e, where="ledger-events.jsonl", sort_keys=True) + b"\n"
            for e in run_events
        )
        # The pilot reads how often reported input passed its bound (design §12).
        exceeded = Counter(
            e["provider"]
            for e in run_events
            if e["event"] == "settle" and e["input_bound_exceeded"]
        )
        (out / "ledger-events.jsonl").write_bytes(events)
        if isinstance(launcher, ContainerLauncher):  # C1 evidence, "fail" for a stopped run
            write_egress_summary(out, jobs, run_events)
        scored = collector.succeeded + collector.failed
        summary.setdefault("status", "completed")
        if watch is not None:  # design §7.4: how many rounds ran, and whether the arm stopped
            summary["airgap"] = {"checks": watch.rounds, "broken": watch.broken}
            # C3 evidence, after the C1 summary it reads; an aborted arm never passes.
            write_airgap_evidence(
                out,
                rounds=watch.rounds,
                broken=watch.broken or summary["status"] == "aborted",
                job_ids=[job.job_id for job in jobs],
            )
        summary.update(
            {
                "completed_at": _utc_now(),
                "scored_cells": scored,
                "correctness_denominator_cells": scored,
                "succeeded_cells": collector.succeeded,
                "failed_cells": collector.failed,
                "failed_cells_included_in_correctness_denominator": True,
                "competitors": sorted(collector.cells),
                "discarded": collector.discarded,
                "not_run": collector.not_run,
                "worker_errors": collector.worker_errors,
                "model_limits_mismatch": collector.limits_mismatch,
                "families": {fid: stats.summary() for fid, stats in collector.families.items()},
                "ledger": {
                    provider: {
                        "spent_before": str(spent_before[provider]),
                        "spent_after": str(ledger.spent(provider)),
                        "cap": str(caps[provider]),
                        "stopped": ledger.stopped(provider),
                        "input_bound_exceeded": exceeded[provider],
                    }
                    for provider in caps
                },
                "price_snapshot": {
                    "captured_on": snapshot.captured_on.isoformat(),
                    "expires_on": (
                        None if snapshot.expires_on is None else snapshot.expires_on.isoformat()
                    ),
                },
            }
        )
        _write(out / "run-summary.json", summary)

    failure: BaseException | None = None
    try:  # every step after the open, the overrun check included, ends in write_outcome
        for provider in config.acknowledge_overrun:
            ledger.acknowledge_overrun(provider)
        overrun = sorted({f.provider for f in families if ledger.stopped(f.provider) == "overrun"})
        if overrun:
            raise BenchmarkValidationError(
                f"{overrun} stopped on a bound overrun: correct the bound, record the overrun, "
                "then rerun with --acknowledge-overrun"
            )
        for provider, cap in caps.items():  # stderr: stdout carries the summary JSON
            print(
                f"ledger {config.ledger_dir}: {provider} "
                f"spent {spent_before[provider]} of cap {cap}",
                file=sys.stderr,
            )
        if any(f.api != "scripted" for f in families):
            # Before any key is read (bootstrap_frame) or any container starts (prepare).
            plans = provider_plans(jobs, spent_before, caps, rates)
            problems = full_run_gate(plans, jobs, rates)
            if problems:
                raise BenchmarkValidationError("the full-run gate refused: " + "; ".join(problems))
            summary["live_confirmation"] = text = confirmation_text(plans)
            authorize_live(text, confirm=confirm)
        prices = {f.model_id: snapshot.models[f.model_id] for f in families if f.provider in caps}
        service = LedgerService(ledger, prices, {f.model_id: f for f in families})
        if watch is not None:  # local Ollama only, never through a proxy variable
            loaded = {f.id: preflight(f.model_id) for f in families if f.api != "scripted"}
            if loaded:
                _write(out / "ollama.json", loaded)
        asyncio.run(_until_signalled(run_arm()))
    except BaseException as exc:
        failure = exc
        detail = redact_key_shaped_text(str(exc))
        summary |= {"status": "aborted", "abort_reason": f"{type(exc).__name__}: {detail}"}
    finally:
        try:
            write_outcome()
        except BaseException as exc:  # retain the interrupt or leak while closing the ledger
            failure = _finalization_failure(failure, exc)
        finally:
            try:
                ledger.close()
            except BaseException as exc:
                failure = _finalization_failure(failure, exc)
    if failure is not None:
        raise failure
    return summary
