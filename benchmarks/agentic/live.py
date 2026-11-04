"""Live-run safety: the one authorization gate, reference rates and the full-run gate.

``authorize_live`` is the one host-side check every live entry point calls (round-2 ruling
D9). It runs before any key is read and before any keyed container starts: it refuses under
``CI``, then needs an interactive ``yes`` to a plan that shows each provider's spend, cap,
remaining budget and estimate.
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

from benchmarks.agentic.ledger import PROVIDER_MARGINS
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
    seconds: dict[str, dict[tuple[str, str], float]] = defaultdict(lambda: defaultdict(float))
    providers: dict[str, str] = {}
    for kind in ("transcripts", "discarded"):
        for path in sorted((run_dir / kind).glob("*/*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            family_id = record["model_family_id"]
            providers[family_id] = record["provider"]
            timing = record["timing"]
            cell = (record["competitor_id"], record["corpus_id"])
            seconds[family_id][cell] += (timing["wall_ms"] - timing["grant_wait_ms"]) / 1000
    booked: dict[str, Decimal] = defaultdict(Decimal)
    events = (run_dir / "ledger-events.jsonl").read_text(encoding="utf-8").splitlines()
    for event in map(json.loads, filter(None, events)):
        if event["event"] == "settle":
            booked[event["provider"]] += Decimal(event["booked"])
    provider_cells: dict[str, int] = defaultdict(int)
    for family_id, cells in seconds.items():
        provider_cells[providers[family_id]] += len(cells)
    return {
        family_id: ReferenceRate(
            family_id=family_id,
            provider=providers[family_id],
            cells=len(cells),
            usd_per_cell=booked[providers[family_id]] / provider_cells[providers[family_id]],
            seconds_per_cell=sum(cells.values()) / len(cells),
        )
        for family_id, cells in seconds.items()
    }


def _live_counts(jobs: Sequence[WorkerJob]) -> dict[str, dict[str, int]]:
    """Planned live trajectories, per provider then per family."""
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for job in jobs:
        if job.family.api != "scripted":
            counts[job.family.provider][job.family.id] += len(job.trajectories)
    return counts


def provider_plans(
    jobs: Sequence[WorkerJob],
    spent: Mapping[str, Decimal],
    caps: Mapping[str, Decimal],
    rates: Mapping[str, ReferenceRate] | None,
) -> list[ProviderPlan]:
    """One plan per provider with live trajectories, sorted by provider."""
    plans = []
    for provider, families in sorted(_live_counts(jobs).items()):
        trajectories = sum(families.values())
        if provider not in caps:  # local and unmetered
            plans.append(ProviderPlan(provider, trajectories, None, None, None))
            continue
        estimate = None
        if rates is not None and all(f in rates for f in families):
            estimate = sum(
                (n * rates[f].usd_per_cell for f, n in families.items()), start=Decimal(0)
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
    for plan in plans:
        if plan.cap is None or plan.spent is None:
            continue
        if plan.estimate is None:
            if plan.trajectories > PILOT_TRAJECTORIES_PER_PROVIDER:
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
    for provider_families in _live_counts(jobs).values():
        for family_id, trajectories in provider_families.items():
            rate = (rates or {}).get(family_id)
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
        estimate = (
            "no reference run (the remaining cap is the worst case)"
            if plan.estimate is None
            else f"estimate ${plan.estimate:.2f} (x {SAFETY} = ${plan.estimate * SAFETY:.2f})"
        )
        margin = PROVIDER_MARGINS.get(plan.provider)
        lines.append(
            f"{head}; spent ${plan.spent} of cap ${plan.cap}, ${plan.cap - plan.spent} "
            f"remaining; {estimate}; the ledger stops at the cap; one overrun is at most the "
            f"${margin} margin"
        )
    return "\n".join(lines)


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
