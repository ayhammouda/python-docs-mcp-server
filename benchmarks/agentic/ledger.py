"""Price snapshot, bound pricing, the persistent spend ledger and its async arbiter.

Design §12: each provider has one billable unit in flight at a time, and a
unit starts only when the provider's recorded spend plus the unit's bound is
at or under the provider's cap. Spend is the design §8 buckets priced from the
committed snapshot; a unit that may have been billed without reporting usage
is booked at twice its bound.
"""

from __future__ import annotations

import asyncio
import errno
import fcntl
import json
import os
import re
import stat
import uuid
from collections.abc import Iterable, Iterator, Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

from benchmarks.agentic.protocol import scanned_json_bytes
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
    batch_multiplier: Decimal = Decimal(1)


@dataclass(frozen=True)
class PriceSnapshot:
    captured_on: date
    expires_on: date | None
    models: dict[str, ModelPrice]  # keyed by model_id


def load_price_snapshot(path: Path) -> PriceSnapshot:
    return price_snapshot_from_mapping(_load_yaml_mapping(path, "price snapshot"))


def price_snapshot_from_mapping(data: dict[str, Any]) -> PriceSnapshot:
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
    multiplier = _rate(item.get("batch_multiplier", 1), model_id, "batch_multiplier")
    if not 0 < multiplier <= 1:
        raise BenchmarkValidationError(
            f"price for {model_id!r}: batch_multiplier must be in (0, 1]"
        )
    return ModelPrice(
        model_id=model_id,
        provider=provider,
        tiers=tuple(tiers),
        source_url=source_url,
        batch_multiplier=multiplier,
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


BATCH_LIMITS = {
    provider: (count, 32 * 1024**2)
    for provider, count in [("anthropic", 100_000), ("openai", 50_000), ("google", 50_000)]
}


@dataclass(frozen=True)
class PlannedItem:
    item_id: str
    input_bound: int
    output_bound: int
    size_bytes: int

    def __post_init__(self) -> None:
        for value in (self.input_bound, self.output_bound, self.size_bytes):
            if type(value) is not int or value < 0:
                raise BenchmarkValidationError("batch bounds must be non-negative integers")


@dataclass(frozen=True)
class BookedItem:
    item_id: str
    bound: Decimal
    actual: Decimal | None
    unbilled: bool
    input_bound_exceeded: bool

    def __post_init__(self) -> None:
        amounts = (self.bound,) if self.actual is None else (self.bound, self.actual)
        if any(not isinstance(v, Decimal) or not v.is_finite() or v < 0 for v in amounts):
            raise BenchmarkValidationError("batch costs must be finite, non-negative decimals")


@dataclass(frozen=True)
class BatchItemSettle:
    item_id: str
    buckets: Buckets | None
    unbilled: bool
    reported_zero: bool = False  # Explicit zero counts verified against a raw provider response.

    def __post_init__(self) -> None:
        if type(self.unbilled) is not bool:
            raise BenchmarkValidationError("unbilled must be boolean")
        if type(self.reported_zero) is not bool or (
            self.reported_zero
            and (self.unbilled or self.buckets is None or not self.buckets.is_empty)
        ):
            raise BenchmarkValidationError("invalid zero-token receipt")


def item_booked(item: BookedItem) -> Decimal:
    if item.actual is not None:
        return item.actual
    return Decimal(0) if item.unbilled else 2 * item.bound


def split_batch(
    costs: Sequence[Decimal],
    sizes: Sequence[int],
    *,
    limit: Decimal,
    max_items: int,
    max_bytes: int,
) -> list[list[int]]:
    pairs = zip(costs, sizes, strict=True)
    return list(_groups(pairs, limit=limit, max_items=max_items, max_bytes=max_bytes))


def _groups(
    pairs: Iterable[tuple[Decimal, int]], *, limit: Decimal, max_items: int, max_bytes: int
) -> Iterator[list[int]]:
    if (
        not isinstance(limit, Decimal)
        or not limit.is_finite()
        or limit < 0
        or type(max_items) is not int
        or max_items < 1
        or type(max_bytes) is not int
        or max_bytes < 0
    ):
        raise BenchmarkValidationError("invalid batch limits")
    group: list[int] = []
    cost, size = Decimal(0), 0
    for i, (amount, length) in enumerate(pairs):
        if (
            not isinstance(amount, Decimal)
            or not amount.is_finite()
            or amount < 0
            or type(length) is not int
            or length < 0
        ):
            raise BenchmarkValidationError(f"batch item at index {i} has invalid cost or size")
        if group and (
            len(group) >= max_items or cost + amount > limit or size + length > max_bytes
        ):
            yield group
            group = []
            cost, size = Decimal(0), 0
        if amount > limit or length > max_bytes:
            raise BenchmarkValidationError(f"batch item at index {i} exceeds a batch limit")
        group.append(i)
        cost += amount
        size += length
    if group:
        yield group


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _journal_name(provider: str) -> str:
    # Historical/custom providers are allowed, but never a path or a dot component.
    if not isinstance(provider, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", provider):
        raise BenchmarkValidationError("spend ledger provider must be a safe file component")
    return f"{provider}.jsonl"


def _open_ledger_file(
    directory: int, name: str, flags: int, *, create: bool = False
) -> tuple[int, bool]:
    """Open one direct child, rejecting links and special files before any mutation."""
    flags |= os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    created = False
    try:
        if create:
            try:
                fd = os.open(name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
                created = True
            except FileExistsError:
                fd = os.open(name, flags, dir_fd=directory)
        else:
            fd = os.open(name, flags, dir_fd=directory)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EMLINK, errno.EISDIR, errno.ENOTDIR, errno.ENXIO):
            raise BenchmarkValidationError(
                "spend ledger file must be regular with one link"
            ) from None
        raise  # Preserve ordinary storage failures; CLI callers sanitize their diagnostics.
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise BenchmarkValidationError("spend ledger file must be regular with one link")
        return fd, created
    except BaseException:
        os.close(fd)
        raise


class Ledger:
    """One JSONL file per provider under ``ledger_dir``, held under an exclusive lock."""

    def __init__(self, directory_fd: int, run_id: str, caps: dict[str, Decimal], lock_fd: int):
        self._dir_fd: int | None = directory_fd
        self._run_id = run_id
        self._caps = caps
        self._lock_fd: int | None = lock_fd
        self._events: list[dict[str, Any]] = []
        self._invocation_ceiling: Decimal | None = None
        self._invocation_start = 0
        self._spent: dict[str, Decimal] = {}
        self._stops: dict[str, str] = {}
        self._units: dict[str, tuple[str, Decimal]] = {}  # unit id -> (provider, bound)
        self._in_flight: dict[str, str] = {}  # provider -> unit id

    @classmethod
    def open(
        cls,
        ledger_dir: Path,
        *,
        run_id: str,
        caps: Mapping[str, Decimal] = PROVIDER_CAPS,
        invocation_budget: Decimal | None = None,
    ) -> Ledger:
        for provider in caps:
            _journal_name(provider)
        try:
            ledger_dir.mkdir(parents=True, exist_ok=True)
            directory = os.open(
                ledger_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            )
        except OSError:
            raise BenchmarkValidationError(
                "spend ledger directory is a link or unavailable"
            ) from None
        fd = None
        try:
            fd, _ = _open_ledger_file(directory, ".lock", os.O_RDWR, create=True)
            os.fchmod(fd, 0o600)  # an existing lock file must also be private
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise BenchmarkValidationError(
                    "spend ledger .lock is locked by another process; one run at a time"
                ) from None
            ledger = cls(directory, run_id, dict(caps), fd)
            ledger._load()
            if invocation_budget is not None:
                if not invocation_budget.is_finite() or invocation_budget < 0:
                    raise BenchmarkValidationError("invalid invocation budget")
                # Recovery settles before this invocation starts spending.
                ledger._invocation_ceiling = (
                    sum(ledger._spent.values(), Decimal(0)) + invocation_budget
                )
            return ledger
        except BaseException:
            try:
                if fd is not None:
                    os.close(fd)  # closing also releases any lock acquired before interruption
            finally:
                os.close(directory)
            raise

    def close(self) -> None:
        fd, self._lock_fd = self._lock_fd, None
        directory, self._dir_fd = self._dir_fd, None
        try:
            if fd is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)
        finally:
            if directory is not None:
                os.close(directory)

    def _active_directory(self) -> int:
        if self._lock_fd is None or self._dir_fd is None:
            raise BenchmarkValidationError("spend ledger is closed")
        try:
            held = os.fstat(self._lock_fd)
            named = os.stat(".lock", dir_fd=self._dir_fd, follow_symlinks=False)
        except OSError:
            raise BenchmarkValidationError("spend ledger .lock is unavailable") from None
        if (
            not stat.S_ISREG(named.st_mode)
            or named.st_nlink != 1
            or (named.st_dev, named.st_ino) != (held.st_dev, held.st_ino)
        ):
            raise BenchmarkValidationError("spend ledger .lock changed while open")
        return self._dir_fd

    def _load(self) -> None:
        directory = self._active_directory()
        pending: dict[str, None] = {}  # ordered set of reserves without a settle
        for name in sorted(n for n in os.listdir(directory) if n.endswith(".jsonl")):
            provider = name.removesuffix(".jsonl")
            _journal_name(provider)
            fd, _ = _open_ledger_file(directory, name, os.O_RDONLY)
            try:
                with os.fdopen(fd, "r", encoding="utf-8", closefd=False) as file:
                    text = file.read()
            finally:
                os.close(fd)
            for number, line in enumerate(text.splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    raise BenchmarkValidationError(
                        f"spend ledger journal line {number} is corrupt"
                    ) from None
                if not isinstance(event, dict) or event.get("provider") != provider:
                    raise BenchmarkValidationError(
                        "spend ledger event provider disagrees with journal"
                    )
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
            if event.get("overrun") or (
                event["outcome"] == "ok" and Decimal(event["booked"]) > bound
            ):
                # Rebuilt from the settle itself: a crash may have lost the stop event.
                self._stops[provider] = "overrun"
        elif kind == "stop" and event["reason"] == "overrun":
            # Only an overrun stop survives a reopen: a cap stop is re-checked against the
            # caps of the new run, so a raised cap lets the provider run again.
            self._stops[provider] = "overrun"
        elif kind == "resume":
            self._stops.pop(provider, None)

    def _append(self, provider: str, fields: dict[str, Any]) -> None:
        directory = self._active_directory()
        name = _journal_name(provider)
        event = {"run_id": self._run_id, "provider": provider, "at": _utc_now(), **fields}
        line = (
            scanned_json_bytes(event, where="spend ledger", sort_keys=True, allow_nan=False) + b"\n"
        )
        fd, created = _open_ledger_file(directory, name, os.O_WRONLY | os.O_APPEND, create=True)
        try:
            start = os.lseek(fd, 0, os.SEEK_END)
            try:
                if os.write(fd, line) != len(line):
                    raise OSError("incomplete ledger append")
                os.fsync(fd)
                if created or start == 0:
                    # An earlier failed first append may have left an empty entry.
                    # Its retry must still make the directory entry durable.
                    os.fsync(directory)
            except BaseException as original:
                # The exclusive ledger lock covers rollback too. A retry sees no partial event.
                try:
                    os.ftruncate(fd, start)
                    os.fsync(fd)
                except BaseException as rollback:
                    original.add_note(f"spend ledger rollback failed ({type(rollback).__name__})")
                raise
        finally:
            os.close(fd)
        self._events.append(event)

    def remaining(self, provider: str) -> Decimal:
        credit = self._caps[provider] - self.spent(provider)
        if self._invocation_ceiling is None:
            return credit
        held = sum(self._units[u][1] for u in self._in_flight.values())
        return min(credit, self._invocation_ceiling - sum(self._spent.values()) - held)

    def spent(self, provider: str) -> Decimal:
        return self._spent.get(provider, Decimal(0))

    def stopped(self, provider: str) -> str | None:
        return self._stops.get(provider)

    def _stop(self, provider: str, reason: str) -> None:
        self._stops[provider] = reason
        self._append(provider, {"event": "stop", "reason": reason})

    def _stop_after_overrun(self, provider: str) -> None:
        # The durable settle already records the overrun, including on replay.
        # Failure of this redundant marker must not turn success into a retry
        # or leave the service holding a unit which has already settled.
        try:
            self._stop(provider, "overrun")
        except OSError:
            pass

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
        if bound > self.remaining(provider):
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
            self._stop_after_overrun(provider)
        return booked

    def settle_batch(self, unit_id: str, *, items: Sequence[BookedItem]) -> Decimal:
        provider, bound = self._units[unit_id]
        if self._in_flight.get(provider) != unit_id:
            raise RuntimeError(f"unit {unit_id} is not in flight")
        if sum(i.bound for i in items) != bound or len({i.item_id for i in items}) != len(items):
            raise BenchmarkValidationError("batch settlement disagrees with its reservation")
        booked = sum((item_booked(i) for i in items), Decimal(0))
        actual = sum((i.actual for i in items if i.actual is not None), Decimal(0))
        overrun = any(i.actual is not None and i.actual > i.bound for i in items)
        self._append(
            provider,
            {
                "event": "settle",
                "unit_id": unit_id,
                "outcome": "batch",
                "booked": str(booked),
                "actual": str(actual),
                "overrun": overrun,
                "input_bound_exceeded": any(i.input_bound_exceeded for i in items),
                "items": [
                    {
                        "item_id": i.item_id,
                        "booked": str(item_booked(i)),
                        "status": "ok"
                        if i.actual is not None
                        else "unbilled"
                        if i.unbilled
                        else "no_usage",
                    }
                    for i in items
                ],
            },
        )
        self._spent[provider] = self.spent(provider) + booked
        del self._in_flight[provider]
        if overrun:
            self._stop_after_overrun(provider)
        return booked

    def acknowledge_overrun(self, provider: str) -> None:
        if self._stops.get(provider) == "overrun":
            self._append(provider, {"event": "resume"})
            del self._stops[provider]

    def invocation_events(self) -> list[dict[str, Any]]:
        """Detached events since this open, including recovery of earlier pending units."""
        return deepcopy(self._events[self._invocation_start :])

    def events(self, run_id: str) -> list[dict[str, Any]]:
        """Return detached event trees; callers cannot mutate the ledger's evidence."""
        return deepcopy([event for event in self._events if event["run_id"] == run_id])


class LedgerService:
    """The host's async arbiter: one unit in flight per provider, providers in parallel."""

    def __init__(
        self,
        ledger: Ledger,
        prices: Mapping[str, ModelPrice],
        families: Mapping[str, ModelFamily],
        margins: Mapping[str, Decimal] = PROVIDER_MARGINS,
    ) -> None:  # both keyed by model_id
        self._ledger = ledger
        self._prices = dict(prices)
        self._families = dict(families)
        self._locks: dict[str, asyncio.Lock] = {}
        self._units: dict[str, tuple[str, ModelPrice, int]] = {}
        self._margins = dict(margins)
        self._batches: dict[str, dict[str, tuple[PlannedItem, Decimal]]] = {}
        self._unmetered: dict[str, str] = {}  # unit id -> provider
        self._grants_stopped = False

    def stop_grants(self) -> None:
        """Latch this invocation's stop without changing settlement or durable provider caps."""
        self._grants_stopped = True

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
        if self._grants_stopped:
            return None
        if provider in UNMETERED_PROVIDERS:  # the Ledger never sees it
            lock = self._locks.setdefault(provider, asyncio.Lock())
            await lock.acquire()
            if self._grants_stopped:
                lock.release()
                return None
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
        if self._grants_stopped:
            lock.release()
            return None
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

    def _clamp(self, model_id: str, item: PlannedItem) -> PlannedItem:
        family = self._families.get(model_id)
        if family is None:
            return item
        # The worker submits with the family's configured output cap. Keep a
        # larger planned bound, but never reserve less than the request can use.
        return replace(
            item,
            input_bound=min(item.input_bound, family.max_input_tokens)
            if family.max_input_tokens is not None
            else item.input_bound,
            output_bound=max(item.output_bound, family.max_output_tokens)
            if family.max_output_tokens is not None
            else item.output_bound,
        )

    def item_bound(self, model_id: str, item: PlannedItem) -> Decimal:
        item = self._clamp(model_id, item)
        return bound_cost(
            self._prices[model_id], input_bound=item.input_bound, output_bound=item.output_bound
        )

    async def reserve_batch(
        self,
        *,
        provider: str,
        model_id: str,
        items: Sequence[PlannedItem],
        job_id: str,
        kind: str = "batch",
    ) -> tuple[str, int] | None:
        if self._grants_stopped:
            return None
        if not items:
            raise ValueError("a batch needs at least one item")
        price = self._prices.get(model_id)
        if not job_id or price is None or price.provider != provider:
            raise BenchmarkValidationError(
                "batch reservation needs a job and a priced provider/model"
            )
        lock = self._locks.setdefault(provider, asyncio.Lock())
        await lock.acquire()
        unit = None
        try:
            if self._grants_stopped:
                return None
            margin = self._margins[provider]
            limit = min(margin, self._ledger.remaining(provider))
            first = self.item_bound(model_id, items[0])
            if first > margin:
                raise BenchmarkValidationError("first batch item exceeds provider margin")
            max_items, max_bytes = BATCH_LIMITS[provider]
            # If the very first item exceeds remaining credit, Ledger.reserve records the cap stop.
            pairs = ((self.item_bound(model_id, i), i.size_bytes) for i in items)
            indices = next(
                _groups(pairs, limit=max(first, limit), max_items=max_items, max_bytes=max_bytes)
            )
            selected = {
                items[i].item_id: (
                    self._clamp(model_id, items[i]),
                    self.item_bound(model_id, items[i]),
                )
                for i in indices
            }
            if len(selected) != len(indices):
                raise BenchmarkValidationError("duplicate batch item")
            amount = sum((cost for _, cost in selected.values()), Decimal(0))
            total_input = sum(item.input_bound for item, _ in selected.values())
            unit = self._ledger.reserve(
                provider,
                model_id=model_id,
                kind=kind,
                bound=amount,
                input_bound=total_input,
                output_bound=sum(item.output_bound for item, _ in selected.values()),
                job_id=job_id,
            )
            if unit is None:
                return None
            self._batches[unit] = selected
            self._units[unit] = (provider, price, total_input)
            return unit, len(selected)
        finally:
            if unit is None:
                lock.release()

    async def settle_batch(self, unit_id: str, *, results: Sequence[BatchItemSettle]) -> None:
        provider, price, _ = self._units[unit_id]
        planned = self._batches[unit_id]
        by_id = {r.item_id: r for r in results}
        if by_id.keys() - planned.keys() or len(results) != len(by_id):
            raise BenchmarkValidationError("unassigned or duplicate batch result")
        booked = []
        for key, (item, bound) in planned.items():
            result = by_id.get(key)
            usage = result.buckets if result else None
            if usage and any(type(v) is not int or v < 0 for v in asdict(usage).values()):
                raise BenchmarkValidationError("invalid batch usage")
            actual = (
                actual_cost(price, usage) * price.batch_multiplier
                if usage and (not usage.is_empty or (result and result.reported_zero))
                else None
            )
            booked.append(
                BookedItem(
                    key,
                    bound,
                    actual,
                    bool(result and result.unbilled),
                    bool(usage and usage.total_input > item.input_bound),
                )
            )
        self._ledger.settle_batch(unit_id, items=booked)
        del self._batches[unit_id], self._units[unit_id]
        self._locks[provider].release()

    async def settle(self, unit_id: str, *, outcome: Outcome, buckets: Buckets | None) -> None:
        if (local := self._unmetered.pop(unit_id, None)) is not None:
            self._locks[local].release()
            return
        provider, price, input_bound = self._units[unit_id]
        if outcome == "ok" and (buckets is None or buckets.is_empty):
            outcome = "no_usage"
        self._ledger.settle(
            unit_id,
            outcome=outcome,
            actual=actual_cost(price, buckets) if outcome == "ok" and buckets else None,
            buckets=buckets,
            input_bound_exceeded=buckets is not None and buckets.total_input > input_bound,
        )
        self._batches.pop(unit_id, None)
        del self._units[unit_id]
        self._locks[provider].release()
