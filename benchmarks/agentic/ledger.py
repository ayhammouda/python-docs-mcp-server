"""Price snapshot, bound pricing, the persistent spend ledger and its async arbiter.

Design §12: each provider has one billable unit in flight at a time, and a
unit starts only when the provider's recorded spend plus the unit's bound is
at or under the provider's cap. Spend is the design §8 buckets priced from the
committed snapshot; a unit that may have been billed without reporting usage
is booked at twice its bound.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

from benchmarks.agentic.types import Buckets
from benchmarks.model_matrix import ModelFamily
from benchmarks.runner import BenchmarkValidationError, _load_yaml_mapping

#: Hard caps, a margin below each provider's budget (design §12).
PROVIDER_CAPS: dict[str, Decimal] = {
    "anthropic": Decimal(180),
    "openai": Decimal(65),
    "google": Decimal(180),
}
PROVIDER_MARGINS: dict[str, Decimal] = {
    "anthropic": Decimal(20),
    "openai": Decimal(10),
    "google": Decimal(20),
}

#: Local providers: one unit in flight at a time, never priced, capped or booked.
UNMETERED_PROVIDERS = frozenset({"ollama"})

Outcome = Literal[
    "ok",
    "rejected",
    "rate_limited",
    "server",
    "timeout",
    "connection",
    "no_usage",
    "worker_lost",
    "unsettled_at_load",
]

_MILLION = Decimal(1_000_000)
_RATE_FIELDS = ("input", "cache_write", "cached_input", "output")


@dataclass(frozen=True)
class Rates:
    """USD per million tokens."""

    input: Decimal
    cache_write: Decimal
    cached_input: Decimal
    output: Decimal


@dataclass(frozen=True)
class Tier:
    up_to_input_tokens: int | None  # None = no upper limit (last tier)
    rates: Rates


@dataclass(frozen=True)
class ModelPrice:
    model_id: str
    provider: str
    tiers: tuple[Tier, ...]
    source_url: str


@dataclass(frozen=True)
class PriceSnapshot:
    captured_on: date
    expires_on: date | None
    models: dict[str, ModelPrice]  # keyed by model_id


def load_price_snapshot(path: Path) -> PriceSnapshot:
    data = _load_yaml_mapping(path, "price snapshot")
    captured_on = _date(data.get("captured_on"), "captured_on")
    if captured_on is None:
        raise BenchmarkValidationError("price snapshot must set 'captured_on'")
    expires_on = _date(data.get("expires_on"), "expires_on")
    items = data.get("models")
    if not isinstance(items, list) or not items:
        raise BenchmarkValidationError("price snapshot must contain a non-empty 'models' list")
    models: dict[str, ModelPrice] = {}
    for index, item in enumerate(items):
        price = _model_price(item, f"price snapshot model at index {index}")
        if price.model_id in models:
            raise BenchmarkValidationError(
                f"price snapshot has a duplicate model_id {price.model_id!r}"
            )
        models[price.model_id] = price
    return PriceSnapshot(captured_on=captured_on, expires_on=expires_on, models=models)


def _date(value: Any, field: str) -> date | None:
    if isinstance(value, datetime):  # YAML reads an unquoted "2026-12-31 00:00:00" this way
        return value.date()
    if value is None or isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise BenchmarkValidationError(f"price snapshot {field!r} is not an ISO date") from exc


def _model_price(item: Any, label: str) -> ModelPrice:
    if not isinstance(item, Mapping):
        raise BenchmarkValidationError(f"{label} must be a mapping")
    model_id, provider, source_url = (
        item.get("model_id"),
        item.get("provider"),
        item.get("source_url"),
    )
    if not isinstance(model_id, str) or not model_id.strip():
        raise BenchmarkValidationError(f"{label} is missing 'model_id'")
    if provider not in PROVIDER_CAPS:
        raise BenchmarkValidationError(
            f"price for {model_id!r} has provider {provider!r}; "
            f"expected one of {sorted(PROVIDER_CAPS)}"
        )
    if not isinstance(source_url, str) or not source_url.startswith("https://"):
        raise BenchmarkValidationError(f"price for {model_id!r} must cite an https 'source_url'")
    raw_tiers = item.get("tiers")
    if not isinstance(raw_tiers, list) or not raw_tiers:
        raise BenchmarkValidationError(f"price for {model_id!r} must have a non-empty 'tiers' list")
    tiers: list[Tier] = []
    for position, raw in enumerate(raw_tiers):
        if not isinstance(raw, Mapping):
            raise BenchmarkValidationError(f"price for {model_id!r}: each tier must be a mapping")
        limit = raw.get("up_to_input_tokens")
        last = position == len(raw_tiers) - 1
        if last and limit is not None:
            raise BenchmarkValidationError(
                f"price for {model_id!r}: the last tier must have no up_to_input_tokens limit"
            )
        if not last:
            if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
                raise BenchmarkValidationError(
                    f"price for {model_id!r}: only the last tier may omit a positive "
                    "up_to_input_tokens"
                )
            previous = tiers[-1].up_to_input_tokens if tiers else None
            if previous is not None and limit <= previous:
                raise BenchmarkValidationError(
                    f"price for {model_id!r}: tiers must be in ascending up_to_input_tokens order"
                )
        tiers.append(Tier(limit, Rates(*(_rate(raw.get(f), model_id, f) for f in _RATE_FIELDS))))
    return ModelPrice(
        model_id=model_id, provider=provider, tiers=tuple(tiers), source_url=source_url
    )


def _rate(value: Any, model_id: str, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise BenchmarkValidationError(
            f"price for {model_id!r}: {field!r} must be a decimal string"
        )
    try:
        rate = Decimal(str(value))
    except InvalidOperation as exc:
        raise BenchmarkValidationError(
            f"price for {model_id!r}: {field!r} is not a number"
        ) from exc
    if not rate.is_finite() or rate < 0:
        raise BenchmarkValidationError(
            f"price for {model_id!r}: {field!r} is negative or not finite"
        )
    return rate


def refuse_stale_snapshot(snapshot: PriceSnapshot, *, today: date) -> None:
    if snapshot.expires_on is not None and today >= snapshot.expires_on:
        raise BenchmarkValidationError(
            f"price snapshot expires on {snapshot.expires_on.isoformat()} and today is "
            f"{today.isoformat()}: refresh docs/benchmarks/price-snapshot.yml before a live run"
        )


def _tier(price: ModelPrice, input_tokens: int) -> Rates:
    for tier in price.tiers:
        if tier.up_to_input_tokens is None or input_tokens <= tier.up_to_input_tokens:
            return tier.rates
    return price.tiers[-1].rates


def actual_cost(price: ModelPrice, buckets: Buckets) -> Decimal:
    """Each bucket at its own rate, in the tier the total reported input reached."""
    rates = _tier(price, buckets.total_input)
    return (
        buckets.uncached_input * rates.input
        + buckets.cache_write_input * rates.cache_write
        + buckets.cached_input * rates.cached_input
        + buckets.output * rates.output
    ) / _MILLION


def bound_cost(price: ModelPrice, *, input_bound: int, output_bound: int) -> Decimal:
    """Input at the highest rate a harness request can incur, in the tier the bound reaches."""
    rates = _tier(price, input_bound)
    input_rate = rates.cache_write if price.provider == "openai" else rates.input
    return (input_bound * input_rate + output_bound * rates.output) / _MILLION


def booked_cost(outcome: Outcome, *, bound: Decimal, actual: Decimal | None) -> Decimal:
    if outcome == "ok":
        if actual is None:
            raise ValueError("an 'ok' unit needs its actual cost")
        return actual
    if outcome in ("rejected", "rate_limited"):
        return Decimal(0)  # a 4xx is not billed
    return 2 * bound  # may have been billed without usage: booked at twice its bound


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Ledger:
    """One JSONL file per provider under ``ledger_dir``, held under an exclusive lock."""

    def __init__(self, ledger_dir: Path, run_id: str, caps: dict[str, Decimal], lock_fd: int):
        self._dir = ledger_dir
        self._run_id = run_id
        self._caps = caps
        self._lock_fd: int | None = lock_fd
        self._events: list[dict[str, Any]] = []
        self._invocation_start = 0
        self._spent: dict[str, Decimal] = {}
        self._stops: dict[str, str] = {}
        self._units: dict[str, tuple[str, Decimal]] = {}  # unit id -> (provider, bound)
        self._in_flight: dict[str, str] = {}  # provider -> unit id

    @classmethod
    def open(
        cls, ledger_dir: Path, *, run_id: str, caps: Mapping[str, Decimal] = PROVIDER_CAPS
    ) -> Ledger:
        ledger_dir.mkdir(parents=True, exist_ok=True)
        lock_path = ledger_dir / ".lock"
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        os.fchmod(fd, 0o600)  # also an existing lock file, whatever mode it was left with
        try:
            # shortcut: fcntl is POSIX only; the harness runs on macOS and Linux, add msvcrt.locking if Windows runs are ever needed  # noqa: E501
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            raise BenchmarkValidationError(
                f"spend ledger {lock_path} is locked by another process; one run at a time"
            ) from exc
        ledger = cls(ledger_dir, run_id, dict(caps), fd)
        try:
            ledger._load()
        except BaseException:
            ledger.close()
            raise
        return ledger

    def close(self) -> None:
        if self._lock_fd is not None:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            os.close(self._lock_fd)
            self._lock_fd = None

    def _load(self) -> None:
        pending: dict[str, None] = {}  # ordered set of reserves without a settle
        for path in sorted(self._dir.glob("*.jsonl")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise BenchmarkValidationError(
                        f"spend ledger {path}:{number} is corrupt"
                    ) from exc
                self._apply(event)
                if event["event"] == "reserve":
                    pending[event["unit_id"]] = None
                elif event["event"] == "settle":
                    pending.pop(event["unit_id"], None)
        self._invocation_start = len(self._events)
        for unit_id in pending:
            provider, _ = self._units[unit_id]
            self._in_flight[provider] = unit_id
            self.settle(unit_id, outcome="unsettled_at_load", actual=None, buckets=None)

    def _apply(self, event: dict[str, Any]) -> None:
        self._events.append(event)
        provider = event["provider"]
        kind = event["event"]
        if kind == "reserve":
            self._units[event["unit_id"]] = (provider, Decimal(event["bound"]))
        elif kind == "settle":
            self._spent[provider] = self.spent(provider) + Decimal(event["booked"])
            _, bound = self._units[event["unit_id"]]
            if event["outcome"] == "ok" and Decimal(event["booked"]) > bound:
                # Rebuilt from the settle itself: a crash may have lost the stop event.
                self._stops[provider] = "overrun"
        elif kind == "stop" and event["reason"] == "overrun":
            # Only an overrun stop survives a reopen: a cap stop is re-checked against the
            # caps of the new run, so a raised cap lets the provider run again.
            self._stops[provider] = "overrun"
        elif kind == "resume":
            self._stops.pop(provider, None)

    def _append(self, provider: str, fields: dict[str, Any]) -> None:
        event = {"run_id": self._run_id, "provider": provider, "at": _utc_now(), **fields}
        with (self._dir / f"{provider}.jsonl").open("a", encoding="utf-8") as file:
            file.write(json.dumps(event, sort_keys=True) + "\n")
        self._events.append(event)

    def spent(self, provider: str) -> Decimal:
        return self._spent.get(provider, Decimal(0))

    def stopped(self, provider: str) -> str | None:
        return self._stops.get(provider)

    def _stop(self, provider: str, reason: str) -> None:
        self._stops[provider] = reason
        self._append(provider, {"event": "stop", "reason": reason})

    def reserve(
        self,
        provider: str,
        *,
        model_id: str,
        kind: str,
        bound: Decimal,
        input_bound: int,
        output_bound: int,
        job_id: str | None = None,
    ) -> str | None:
        cap = self._caps.get(provider)
        if cap is None:
            raise BenchmarkValidationError(f"provider {provider!r} has no spend cap in the ledger")
        if provider in self._in_flight:
            raise RuntimeError(
                f"a {provider} unit is already in flight: {self._in_flight[provider]}"
            )
        if provider in self._stops:
            return None
        if self.spent(provider) + bound > cap:
            self._stop(provider, "cap")
            return None
        unit_id = f"{provider}-{uuid.uuid4().hex}"
        self._append(
            provider,
            {
                "event": "reserve",
                "unit_id": unit_id,
                "model_id": model_id,
                "kind": kind,
                "bound": str(bound),
                "input_bound": input_bound,
                "output_bound": output_bound,
                "job_id": job_id,  # set by the host; attributes the unit's settle (C1)
            },
        )
        self._units[unit_id] = (provider, bound)
        self._in_flight[provider] = unit_id
        return unit_id

    def settle(
        self,
        unit_id: str,
        *,
        outcome: Outcome,
        actual: Decimal | None,
        buckets: Buckets | None,
        input_bound_exceeded: bool = False,
    ) -> Decimal:
        provider, bound = self._units[unit_id]
        if self._in_flight.get(provider) != unit_id:
            raise RuntimeError(f"unit {unit_id} is not in flight")
        booked = booked_cost(outcome, bound=bound, actual=actual)
        self._append(
            provider,
            {
                "event": "settle",
                "unit_id": unit_id,
                "outcome": outcome,
                "actual": None if actual is None else str(actual),
                "booked": str(booked),
                "buckets": None if buckets is None else asdict(buckets),
                "input_bound_exceeded": input_bound_exceeded,
            },
        )
        self._spent[provider] = self.spent(provider) + booked
        del self._in_flight[provider]
        if outcome == "ok" and booked > bound:
            self._stop(provider, "overrun")
        return booked

    def acknowledge_overrun(self, provider: str) -> None:
        if self._stops.get(provider) == "overrun":
            del self._stops[provider]
            self._append(provider, {"event": "resume"})

    def invocation_events(self) -> list[dict[str, Any]]:
        """Events written since this open, including recovery of earlier pending units."""
        return [dict(event) for event in self._events[self._invocation_start :]]

    def events(self, run_id: str) -> list[dict[str, Any]]:
        return [dict(event) for event in self._events if event["run_id"] == run_id]


class LedgerService:
    """The host's async arbiter: one unit in flight per provider, providers in parallel."""

    def __init__(
        self,
        ledger: Ledger,
        prices: Mapping[str, ModelPrice],
        families: Mapping[str, ModelFamily],
    ) -> None:  # both keyed by model_id
        self._ledger = ledger
        self._prices = dict(prices)
        self._families = dict(families)
        self._locks: dict[str, asyncio.Lock] = {}
        self._units: dict[str, tuple[str, ModelPrice, int]] = {}
        self._unmetered: dict[str, str] = {}  # unit id -> provider

    async def reserve(
        self,
        *,
        provider: str,
        model_id: str,
        input_bound: int,
        output_bound: int,
        kind: str = "model_call",
        job_id: str | None = None,
    ) -> str | None:
        if provider in UNMETERED_PROVIDERS:  # the Ledger never sees it
            await self._locks.setdefault(provider, asyncio.Lock()).acquire()
            unit_id = f"{provider}-{uuid.uuid4().hex}"
            self._unmetered[unit_id] = provider
            return unit_id
        price = self._prices.get(model_id)
        if price is None:
            raise BenchmarkValidationError(f"no price snapshot entry for model {model_id!r}")
        family = self._families.get(model_id)
        if family is not None and family.max_input_tokens is not None:
            input_bound = min(input_bound, family.max_input_tokens)  # a larger request is refused
        bound = bound_cost(price, input_bound=input_bound, output_bound=output_bound)
        lock = self._locks.setdefault(provider, asyncio.Lock())
        await lock.acquire()  # held until this unit's settle
        try:
            unit_id = self._ledger.reserve(
                provider,
                model_id=model_id,
                kind=kind,
                bound=bound,
                input_bound=input_bound,
                output_bound=output_bound,
                job_id=job_id,
            )
        except BaseException:
            lock.release()
            raise
        if unit_id is None:
            lock.release()
            return None
        self._units[unit_id] = (provider, price, input_bound)
        return unit_id

    async def settle(self, unit_id: str, *, outcome: Outcome, buckets: Buckets | None) -> None:
        if (local := self._unmetered.pop(unit_id, None)) is not None:
            self._locks[local].release()
            return
        provider, price, input_bound = self._units.pop(unit_id)
        try:
            if outcome == "ok" and (buckets is None or buckets.is_empty):
                outcome = "no_usage"
            self._ledger.settle(
                unit_id,
                outcome=outcome,
                actual=actual_cost(price, buckets) if outcome == "ok" and buckets else None,
                buckets=buckets,
                input_bound_exceeded=buckets is not None and buckets.total_input > input_bound,
            )
        finally:
            self._locks[provider].release()
