"""Live-run safety: the one authorization gate, reference rates and the full-run gate.

``authorize_live`` is the one host-side check every live entry point calls (round-2 ruling
D9). It runs before any key is read and before any keyed container starts: it refuses under
``CI``, then needs an interactive ``yes`` to a plan that shows each provider's spend, cap,
remaining budget and estimate.
"""

from __future__ import annotations

import json
import math
import os
import sys
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, DecimalException
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

from benchmarks.agentic.ledger import PROVIDER_MARGINS, UNMETERED_PROVIDERS
from benchmarks.model_matrix import load_model_matrix
from benchmarks.runner import BenchmarkValidationError

if TYPE_CHECKING:
    from benchmarks.agentic.host import WorkerJob

#: Design §12: 10 questions x 3 systems x k = 3. More per provider needs a reference run.
PILOT_TRAJECTORIES_PER_PROVIDER = 90
SAFETY = Decimal("1.25")
WINDOW_S = 86_400
_NOT_CI = frozenset({"", "0", "false", "no"})


@dataclass(frozen=True)
class ReferenceRate:
    family_id: str
    provider: str
    cells: int  # logical cells: one (cell, corpus_id), every attempt included
    usd_per_cell: Decimal
    seconds_per_cell: float
    model_id: str | None = None
    api: str | None = None


@dataclass(frozen=True)
class ProviderPlan:
    provider: str
    trajectories: int
    spent: Decimal | None  # the money fields are None for an unmetered provider
    cap: Decimal | None
    estimate: Decimal | None  # None without a reference run


def reference_rates(run_dir: Path) -> dict[str, ReferenceRate]:
    """Per family, a finished run's cost and time per logical cell (round-2 ruling D8).

    A logical cell is one (cell, corpus_id) over the family's scored and discarded records,
    every attempt included, so a retry-heavy pilot never halves the estimate.
    """
    try:
        return _read_reference_rates(run_dir)
    except (OSError, ValueError, KeyError, TypeError, DecimalException, OverflowError) as exc:
        raise BenchmarkValidationError(f"invalid reference run {run_dir}: {exc}") from exc


def _read_reference_rates(run_dir: Path) -> dict[str, ReferenceRate]:
    summary = json.loads((run_dir / "run-summary.json").read_text(encoding="utf-8"))
    if summary["status"] != "completed":
        raise ValueError("the run did not complete")
    # Budget stops and worker errors can leave status "completed" with cells unscored.
    if not summary["scored_cells"] or summary["scored_cells"] != summary["planned_cells"]:
        raise ValueError("the run did not score every planned cell")
    seconds: dict[str, dict[tuple[str, str], float]] = defaultdict(lambda: defaultdict(float))
    matrix = load_model_matrix(run_dir / "snapshots" / "model-matrix.yml")
    families = {family.id: family for family in matrix.model_families}
    job_families: dict[tuple[str, str], str] = {}
    scored: set[tuple[str, str]] = set()
    discarded = {
        (d["cell"], f"{d['corpus_id']}--attempt{d['attempt']}.json") for d in summary["discarded"]
    }
    for kind in ("transcripts", "discarded"):
        for path in sorted((run_dir / kind).glob("*/*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            family_id = record["model_family_id"]
            provider = record["provider"]
            family = families[family_id]
            if (provider, record["model_id"]) != (family.provider, family.model_id):
                raise ValueError(f"transcript disagrees with the model snapshot for {family_id}")
            job_families[provider, f"{family_id}--{record['system_id']}"] = family_id
            timing = record["timing"]
            cell = (record["competitor_id"], record["corpus_id"])
            elapsed = (timing["wall_ms"] - timing["grant_wait_ms"]) / 1000
            if not math.isfinite(elapsed) or elapsed < 0 or timing["grant_wait_ms"] < 0:
                raise ValueError(f"invalid timing for {family_id} {cell}")
            seconds[family_id][cell] += elapsed
            if kind == "transcripts":
                scored.add(cell)
            else:
                discarded.remove((path.parent.name, path.name))
    if discarded:
        raise ValueError("missing discarded attempts from the reference run")
    if len(scored) != summary["scored_cells"] or any(
        cells.keys() - scored for cells in seconds.values()
    ):
        raise ValueError("scored transcripts do not cover the reference cells")

    booked: dict[str, Decimal] = defaultdict(Decimal)  # includes charges for failed attempts
    measured: set[str] = set()
    reserved: dict[str, str] = {}
    events = (run_dir / "ledger-events.jsonl").read_text(encoding="utf-8").splitlines()
    for event in map(json.loads, filter(None, events)):
        provider = event["provider"]
        if event["event"] == "reserve":
            family_id = job_families[provider, event["job_id"]]
            if event["model_id"] != families[family_id].model_id:
                raise ValueError(f"reservation disagrees with the model snapshot for {family_id}")
            reserved[event["unit_id"]] = family_id
        elif event["event"] == "settle":
            # Ledger.open books an older run's pending units before recording spent_before.
            if event["outcome"] == "unsettled_at_load" and event["unit_id"] not in reserved:
                continue
            family_id = reserved.pop(event["unit_id"])
            amount = Decimal(event["booked"])
            if provider != families[family_id].provider or not amount.is_finite() or amount < 0:
                raise ValueError(f"invalid settlement for {family_id}")
            booked[family_id] += amount
            if event["outcome"] == "ok" and amount > 0:
                measured.add(family_id)
    if reserved:
        raise ValueError("the ledger has unsettled reservations")
    balances = summary["ledger"]
    if (
        not isinstance(balances, dict)
        or {families[f].provider for f in seconds} - UNMETERED_PROVIDERS - balances.keys()
    ):
        raise ValueError("the run summary is missing a metered provider's spend")
    for provider, balance in balances.items():
        spend = sum(booked[f] for f in booked if families[f].provider == provider)
        if spend != Decimal(balance["spent_after"]) - Decimal(balance["spent_before"]):
            raise ValueError(f"{provider} settlements disagree with the run's spend")
    rates = {
        family_id: ReferenceRate(
            family_id=family_id,
            provider=families[family_id].provider,
            model_id=families[family_id].model_id,
            api=families[family_id].api,
            cells=len(cells),
            usd_per_cell=booked[family_id] / len(cells),
            seconds_per_cell=sum(cells.values()) / len(cells),
        )
        for family_id, cells in seconds.items()
        if families[family_id].provider in UNMETERED_PROVIDERS or family_id in measured
        if sum(cells.values()) > 0
    }
    if not rates:
        raise ValueError("no family has usable cost and timing measurements")
    return rates


def _live_counts(jobs: Sequence[WorkerJob]) -> dict[str, dict[str, int]]:
    """Planned live trajectories, per provider then per family."""
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for job in jobs:
        if job.family.api != "scripted":
            counts[job.family.provider][job.family.id] += len(job.trajectories)
    return counts


def _matching_rates(
    jobs: Sequence[WorkerJob], rates: Mapping[str, ReferenceRate] | None
) -> dict[str, ReferenceRate]:
    matching = {}
    for job in jobs:
        family = job.family
        rate = rates.get(family.id) if rates is not None else None
        if (
            rate is not None
            and family.api not in (None, "scripted")
            and (rate.provider, rate.model_id, rate.api)
            == (family.provider, family.model_id, family.api)
        ):
            matching[family.id] = rate
    return matching


def provider_plans(
    jobs: Sequence[WorkerJob],
    spent: Mapping[str, Decimal],
    caps: Mapping[str, Decimal],
    rates: Mapping[str, ReferenceRate] | None,
) -> list[ProviderPlan]:
    """One plan per provider with live trajectories, sorted by provider."""
    plans = []
    matching = _matching_rates(jobs, rates)
    for provider, families in sorted(_live_counts(jobs).items()):
        trajectories = sum(families.values())
        if provider not in caps:  # local and unmetered
            plans.append(ProviderPlan(provider, trajectories, None, None, None))
            continue
        estimate = None
        if families.keys() <= matching.keys():
            estimate = sum(
                (n * matching[f].usd_per_cell for f, n in families.items()), start=Decimal(0)
            )
        plans.append(
            ProviderPlan(
                provider, trajectories, spent.get(provider, Decimal(0)), caps[provider], estimate
            )
        )
    return plans


def full_run_gate(
    plans: Sequence[ProviderPlan],
    jobs: Sequence[WorkerJob],
    rates: Mapping[str, ReferenceRate] | None,
) -> list[str]:
    """Why this live run must not start: cost or time over budget, or no reference run.

    The per-graded-answer term of design §12 comes from PR 5's grading/summary.json.
    """
    problems = []
    counts = _live_counts(jobs)
    for plan in plans:
        if plan.cap is None or plan.spent is None:
            continue
        if plan.estimate is None:
            if rates is not None:
                families = ", ".join(counts[plan.provider])
                problems.append(
                    f"{plan.provider}: the reference needs measurements from the same model ID "
                    f"and live API for each requested family ({families})"
                )
            elif plan.trajectories > PILOT_TRAJECTORIES_PER_PROVIDER:
                problems.append(
                    f"{plan.provider}: {plan.trajectories} trajectories is more than the "
                    f"{PILOT_TRAJECTORIES_PER_PROVIDER}-trajectory pilot; pass --reference-run "
                    "<pilot dir> for an estimate"
                )
            continue
        remaining = plan.cap - plan.spent
        if plan.estimate * SAFETY > remaining:
            problems.append(
                f"{plan.provider}: the estimate ${plan.estimate} x {SAFETY} = "
                f"${plan.estimate * SAFETY} is over the ${remaining} left under its cap"
            )
    matching = _matching_rates(jobs, rates)
    for provider_families in counts.values():
        for family_id, trajectories in provider_families.items():
            rate = matching.get(family_id)
            if rate is not None and trajectories * rate.seconds_per_cell * float(SAFETY) > WINDOW_S:
                hours = trajectories * rate.seconds_per_cell * float(SAFETY) / 3600
                problems.append(
                    f"{family_id}: {trajectories} trajectories x {rate.seconds_per_cell:.1f} s "
                    f"x {SAFETY} is {hours:.1f} h, over the 24-hour window"
                )
    return problems


def confirmation_text(plans: Sequence[ProviderPlan]) -> str:
    """One line per provider: trajectories, spent, cap, remaining and the estimate."""
    lines = []
    for plan in plans:
        head = f"{plan.provider}: {plan.trajectories} trajectories"
        if plan.cap is None or plan.spent is None:
            lines.append(f"{head}; local, unmetered")
            continue
        estimate = "no reference run (the remaining cap is the worst case)"
        if plan.estimate is not None:
            scaled = _dollars(plan.estimate * SAFETY)
            estimate = f"estimate {_dollars(plan.estimate)} (x {SAFETY} = {scaled})"
        margin = PROVIDER_MARGINS.get(plan.provider)
        lines.append(
            f"{head}; spent ${plan.spent} of cap ${plan.cap}, ${plan.cap - plan.spent} "
            f"remaining; {estimate}; the ledger stops at the cap; one overrun is at most the "
            f"${margin} margin"
        )
    return "\n".join(lines)


def _dollars(amount: Decimal) -> str:
    return "<$0.01" if 0 < amount < Decimal("0.01") else f"${amount:.2f}"


def interactive_confirm(
    text: str, *, stdin: TextIO | None = None, stderr: TextIO | None = None
) -> bool:
    """Ask on stderr, read stdin's next line; only an exact ``yes`` from a terminal agrees.

    stdout carries only the summary JSON, so nothing is written to it (round-3 item 6).
    """
    reader = sys.stdin if stdin is None else stdin
    writer = sys.stderr if stderr is None else stderr
    if reader is None or writer is None or not reader.isatty():
        return False
    writer.write(f"{text}\nType yes to start this live run: ")
    writer.flush()
    return reader.readline().rstrip("\r\n") == "yes"


def authorize_live(
    text: str,
    *,
    environ: Mapping[str, str] = os.environ,
    confirm: Callable[[str], bool] = interactive_confirm,
) -> None:
    """The one gate for every live entry point: refused under CI, then confirmed by a person."""
    if environ.get("CI", "").strip().lower() not in _NOT_CI:
        raise BenchmarkValidationError("live calls are refused under CI")
    if not confirm(text):
        raise BenchmarkValidationError("live run not confirmed")
