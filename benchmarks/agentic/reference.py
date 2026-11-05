"""Join captured pilot attempts to the invocation's spend events.

Unit IDs are the producer's link between channels. This checks consistency of saved
evidence, not authenticity: neither transcripts nor ledger exports are signed.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from benchmarks.agentic.ledger import UNMETERED_PROVIDERS, booked_cost
from benchmarks.agentic.types import Buckets
from benchmarks.model_matrix import ModelFamily

_CALL_OUTCOMES = frozenset(
    {"ok", "no_usage", "rejected", "rate_limited", "server", "timeout", "connection"}
)

_COORDINATES = (
    "competitor_id",
    "corpus_id",
    "system_id",
    "model_family_id",
    "provider",
    "model_id",
    "repeat",
    "attempt",
)


class ReferenceTranscripts:
    """Match every captured transcript to the host's publication-time inventory."""

    def __init__(self, summary: dict[str, Any]) -> None:
        items = summary.get("transcript_inventory")
        if not isinstance(items, list) or not items:
            raise ValueError(
                "missing transcript inventory; regenerate the pilot with the current runner"
            )
        self.pending: dict[str, dict[str, Any]] = {}
        for item in items:
            if not isinstance(item, dict) or any(field not in item for field in _COORDINATES):
                raise ValueError("invalid transcript inventory coordinates")
            if any(not isinstance(item[f], str) or not item[f] for f in _COORDINATES[:-2]):
                raise ValueError("invalid transcript inventory coordinates")
            if any(type(item[f]) is not int or item[f] < 1 for f in ("repeat", "attempt")):
                raise ValueError("invalid transcript inventory attempt/repeat")
            cell = f"{item['system_id']}--{item['model_family_id']}--r{item['repeat']}"
            path = item.get("path")
            if item["competitor_id"] != cell or path not in (
                f"transcripts/{cell}/{item['corpus_id']}.json",
                f"discarded/{cell}/{item['corpus_id']}--attempt{item['attempt']}.json",
            ):
                raise ValueError("transcript inventory path contradicts its coordinates")
            digest = item.get("sha256")
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest)
            ):
                raise ValueError("transcript inventory lacks a valid SHA256")
            if path in self.pending:
                raise ValueError("duplicate transcript inventory path")
            self.pending[path] = item

    def verify(self, path: str, content: bytes, record: dict[str, Any]) -> None:
        item = self.pending.pop(path, None)
        if item is None:
            raise ValueError(f"transcript absent from the host inventory: {path}")
        if hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise ValueError(f"transcript SHA256 disagrees with the host inventory: {path}")
        if any(
            type(record.get(f)) is not type(item[f]) or record.get(f) != item[f]
            for f in _COORDINATES
        ):
            raise ValueError(f"transcript coordinates disagree with the host inventory: {path}")

    def finish(self) -> None:
        if self.pending:
            raise ValueError(
                "missing transcripts from the host inventory: " + ", ".join(sorted(self.pending))
            )


def nonnegative_decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("invalid reference amount")
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError("invalid reference amount")
    return amount


def _buckets(value: Any) -> Buckets | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("invalid reference usage")
    buckets = Buckets(**value)
    if any(type(n) is not int or n < 0 for n in vars(buckets).values()):
        raise ValueError("invalid reference usage")
    return buckets


@dataclass(frozen=True)
class _Attempt:
    family: ModelFamily
    job_id: str
    outcome: str
    buckets: Buckets | None
    input_bound: int | None
    output_bound: int | None


def scripted_response(call: dict[str, Any]) -> bool:
    raw = call.get("raw_response")
    return isinstance(raw, dict) and "scripted_step" in raw


class ReferenceUnits:
    """Every actual call appears once in transcripts and, if metered, in the ledger."""

    def __init__(self) -> None:
        self.attempts: dict[str, _Attempt] = {}

    def add(self, record: dict[str, Any], family: ModelFamily) -> None:
        execution = record.get("execution")
        if not isinstance(execution, dict) or any(
            not isinstance(execution.get(field), str) or not execution[field]
            for field in ("provider", "model_id", "api")
        ):
            raise ValueError(
                f"reference transcript lacks executed provider/model/API for {family.id}"
            )
        if (execution["provider"], execution["model_id"], execution["api"]) != (
            family.provider,
            family.model_id,
            family.api,
        ):
            raise ValueError(f"executed client disagrees with the model snapshot for {family.id}")
        if (record["provider"], record["model_id"]) != (family.provider, family.model_id):
            raise ValueError(f"transcript disagrees with the model snapshot for {family.id}")
        calls = record["model_calls"]
        if not isinstance(calls, list):
            raise ValueError("reference model calls must be a list")
        for call in calls:
            if not isinstance(call, dict):
                raise ValueError("invalid reference model call")
            for field in ("input_bound", "output_bound"):
                if field in call and (type(call[field]) is not int or call[field] < 0):
                    raise ValueError("invalid reference call bound")
            params = call["request_params"]
            if not isinstance(params, dict) or params.get("model") != family.model_id:
                raise ValueError("reference request model contradicts executed identity")
            raw = call.get("raw_response")
            if raw is not None and not isinstance(raw, dict):
                raise ValueError("invalid reference response")
            if family.api != "scripted" and scripted_response(call):
                raise ValueError("scripted response contradicts live reference identity")
            attempts = call["attempts"]
            if not isinstance(attempts, list):
                raise ValueError("invalid reference attempts")
            usage = _buckets(call["buckets"])
            if not attempts and (raw is not None or usage is not None):
                raise ValueError("reference response lacks its call attempt")
            for index, attempt in enumerate(attempts):
                if not isinstance(attempt, dict):
                    raise ValueError("invalid reference attempt")
                for field in ("wall_ms", "wait_ms"):
                    if field in attempt:
                        nonnegative_decimal(attempt[field])
                unit, outcome = attempt["unit_id"], attempt["outcome"]
                if not isinstance(unit, str) or not unit or unit in self.attempts:
                    raise ValueError("missing or duplicate reference unit")
                if outcome not in _CALL_OUTCOMES:
                    raise ValueError("invalid reference call outcome")
                terminal = index == len(attempts) - 1
                if outcome in {"ok", "no_usage"} and not terminal:
                    raise ValueError("reference retries a successful call")
                buckets = usage if terminal else None
                if outcome == "ok":
                    if buckets is None or buckets.is_empty or raw is None:
                        raise ValueError("successful reference call lacks usage or response")
                elif outcome == "no_usage":
                    if (buckets is not None and not buckets.is_empty) or raw is None:
                        raise ValueError("reference no-usage call contradicts response")
                    buckets = None  # the loop sends no buckets when all counters are zero
                elif terminal and (buckets is not None or raw is not None):
                    raise ValueError("failed reference call contradicts response")
                self.attempts[unit] = _Attempt(
                    family,
                    f"{family.id}--{record['system_id']}",
                    outcome,
                    buckets,
                    call.get("input_bound"),
                    call.get("output_bound"),
                )

    def reconcile(
        self, events: list[dict[str, Any]], summary: dict[str, Any]
    ) -> tuple[dict[str, Decimal], set[str]]:
        run_id = summary["run_id"]
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("reference lacks run identity")
        pending: dict[str, tuple[_Attempt, Decimal]] = {}
        seen: set[str] = set()
        settled: set[str] = set()
        started = False
        booked: dict[str, Decimal] = defaultdict(Decimal)
        measured: set[str] = set()
        recovery: dict[str, Decimal] = defaultdict(Decimal)
        for event in events:
            provider, kind = event["provider"], event["event"]
            if event["run_id"] != run_id or provider in UNMETERED_PROVIDERS:
                raise ValueError("ledger event contradicts reference run/provider")
            if kind in {"stop", "resume"}:
                continue
            unit = event["unit_id"]
            if not isinstance(unit, str) or not unit:
                raise ValueError("reference ledger lacks unit identity")
            if kind == "reserve":
                started = True
                if unit in seen:
                    raise ValueError("duplicate reference reservation")
                seen.add(unit)
                attempt = self.attempts[unit]
                family = attempt.family
                if (provider, event["job_id"], event["model_id"], event["kind"]) != (
                    family.provider,
                    attempt.job_id,
                    family.model_id,
                    "model_call",
                ) or event.get("api", family.api) != family.api:
                    raise ValueError("reservation contradicts reference attempt identity")
                for field in ("input_bound", "output_bound"):
                    if type(event[field]) is not int or event[field] < 0:
                        raise ValueError("invalid reference token bound")
                    expected = getattr(attempt, field)
                    if field == "input_bound" and family.max_input_tokens is not None:
                        expected = (
                            min(expected, family.max_input_tokens) if expected is not None else None
                        )
                    if expected is not None and event[field] != expected:
                        raise ValueError("reservation contradicts reference call bound")
                pending[unit] = attempt, nonnegative_decimal(event["bound"])
            elif kind == "settle":
                amount = nonnegative_decimal(event["booked"])
                actual = None if event["actual"] is None else nonnegative_decimal(event["actual"])
                buckets = _buckets(event["buckets"])
                # Ledger.open recovers old pending units before spent_before is captured.
                # Their reserve is intentionally absent, even when run labels are reused.
                if event["outcome"] == "unsettled_at_load" and unit not in self.attempts:
                    if started or unit in seen or actual is not None or buckets is not None:
                        raise ValueError("invalid reference recovery settlement")
                    seen.add(unit)
                    recovery[provider] += amount
                    continue
                attempt, bound = pending.pop(unit)
                if provider != attempt.family.provider or event["outcome"] != attempt.outcome:
                    raise ValueError("settlement contradicts reference attempt identity/outcome")
                if buckets != attempt.buckets or (attempt.outcome != "ok" and actual is not None):
                    raise ValueError("settlement contradicts reference usage")
                if amount != booked_cost(event["outcome"], bound=bound, actual=actual):
                    raise ValueError("reference settlement booking contradicts its outcome")
                settled.add(unit)
                booked[attempt.family.id] += amount
                if attempt.outcome == "ok" and amount > 0:
                    measured.add(attempt.family.id)
            else:
                raise ValueError("unknown reference ledger event")
        metered = {
            u for u, a in self.attempts.items() if a.family.provider not in UNMETERED_PROVIDERS
        }
        if pending or settled != metered:
            raise ValueError("reference attempts and settled reservations do not match")
        balances = summary["ledger"]
        providers = {a.family.provider for a in self.attempts.values()} - UNMETERED_PROVIDERS
        if not isinstance(balances, dict) or (providers | recovery.keys()) - balances.keys():
            raise ValueError("reference summary lacks provider spend")
        for provider, balance in balances.items():
            before = nonnegative_decimal(balance["spent_before"])
            after = nonnegative_decimal(balance["spent_after"])
            if "cap" in balance:
                nonnegative_decimal(balance["cap"])
            # Each family's total is counted once, regardless of its number of attempts.
            family_ids = {
                a.family.id for a in self.attempts.values() if a.family.provider == provider
            }
            spend = sum((booked[f] for f in family_ids), start=Decimal(0))
            if after - before != spend or before < recovery[provider]:
                raise ValueError("reference settlements disagree with the run's spend")
        return booked, measured
