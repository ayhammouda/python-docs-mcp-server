"""Per-item pricing, durable settlement and batch admission (design §12)."""

import asyncio
import json
import os
from decimal import Decimal as D
from pathlib import Path

import pytest
import yaml

from benchmarks.agentic.ledger import (
    BatchItemSettle,
    BookedItem,
    Ledger,
    LedgerService,
    ModelPrice,
    PlannedItem,
    Rates,
    Tier,
    load_price_snapshot,
    split_batch,
)
from benchmarks.agentic.types import Buckets
from benchmarks.grading.panel import as_model_family, load_panel
from benchmarks.runner import BenchmarkValidationError

SNAPSHOT = Path("docs/benchmarks/price-snapshot.yml")
FLAT = ModelPrice("m", "anthropic", (Tier(None, Rates(D(1), D(0), D(0), D(0))),), "", D(".5"))


def test_committed_snapshot_prices_the_judges():
    snap = load_price_snapshot(SNAPSHOT)
    for model, expected in {
        "claude-haiku-5-5": [(100000, ".10 .20 .01 .50"), (None, ".50 1 .05 2.50")],
        "claude-opus-5-5": [(None, "4 8 .20 20")],
        "gpt-6-luna": [(272000, ".10 .125 .01 .50"), (None, ".20 .25 .02 .75")],
        "gemini-3.1-flash-lite": [(None, ".25 0 .025 1.50")],
    }.items():
        assert snap.models[model].tiers == tuple(
            Tier(limit, Rates(*map(D, rates.split()))) for limit, rates in expected
        )
    assert {p.batch_multiplier for p in snap.models.values()} == {D(".5")}


@pytest.mark.parametrize("value", ["0", "1.5", "-0.5", "NaN", "Infinity", 0.5, True])
def test_batch_multiplier_must_be_in_zero_one(tmp_path, value):
    data = yaml.safe_load(SNAPSHOT.read_text())
    data["models"][0]["batch_multiplier"] = value
    path = tmp_path / "prices.yml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(BenchmarkValidationError, match="batch_multiplier"):
        load_price_snapshot(path)


def test_split_batch_groups_contiguous_items_under_every_limit():
    assert split_batch([D(3)] * 4, [1] * 4, limit=D(6), max_items=10, max_bytes=100) == [
        [0, 1],
        [2, 3],
    ]
    assert split_batch([D(1)] * 3, [1] * 3, limit=D(6), max_items=1, max_bytes=100) == [
        [0],
        [1],
        [2],
    ]
    assert split_batch([D(1)] * 3, [10] * 3, limit=D(6), max_items=10, max_bytes=25) == [
        [0, 1],
        [2],
    ]


@pytest.mark.parametrize("cost,size", [(7, 1), (1, 30)])
def test_split_batch_refuses_a_single_item_over_a_limit(cost, size):
    with pytest.raises(BenchmarkValidationError, match="1"):
        split_batch([D(1), D(cost)], [1, size], limit=D(6), max_items=2, max_bytes=25)


@pytest.fixture
def ledger(tmp_path):
    ledger = Ledger.open(tmp_path, run_id="r", caps={"anthropic": D(37)})
    yield ledger
    ledger.close()


def reserve(ledger):
    return ledger.reserve(
        "anthropic",
        model_id="m",
        kind="batch",
        bound=D("1.5"),
        input_bound=100,
        output_bound=10,
        job_id="j",
    )


def test_batch_settle_books_actual_unbilled_and_unknown_items(ledger):
    items = [
        BookedItem(k, D(".5"), actual, free, False)
        for k, actual, free in [("a", D(".2"), False), ("b", None, True), ("c", None, False)]
    ]
    assert ledger.settle_batch(reserve(ledger), items=items) == D("1.2")
    event = ledger.events("r")[-1]
    assert event["outcome"] == "batch" and D(event["actual"]) == D(".2")
    assert [i["status"] for i in event["items"]] == ["ok", "unbilled", "no_usage"]


def test_batch_item_above_its_bound_stops_the_provider_and_survives_reopen(ledger, tmp_path):
    ledger.settle_batch(
        reserve(ledger),
        items=[
            BookedItem("a", D(".5"), D(".6"), False, True),
            BookedItem("b", D(1), None, True, False),
        ],
    )
    assert ledger.stopped("anthropic") == "overrun"
    ledger.close()
    path = tmp_path / "anthropic.jsonl"
    events = [json.loads(line) for line in path.read_text().splitlines()]
    assert events[-2]["overrun"] is True
    path.write_text("".join(json.dumps(e) + "\n" for e in events if e["event"] != "stop"))
    reopened = Ledger.open(tmp_path, run_id="next")
    try:
        assert reopened.stopped("anthropic") == "overrun"
        assert reopened.spent("anthropic") == D(".6")
    finally:
        reopened.close()


async def test_reserve_batch_takes_items_up_to_the_margin_then_the_remaining_cap(ledger):
    service = LedgerService(ledger, {"m": FLAT}, {}, margins={"anthropic": D(20)})
    items = [PlannedItem(str(i), 8_000_000, 0, 10) for i in range(12)]
    for count, usage, spent in [
        (2, 10_000_000, "10"),
        (2, 15_000_000, "25"),
        (1, 8_020_000, "29.01"),
    ]:
        unit, n = await service.reserve_batch(
            provider="anthropic", model_id="m", items=items, job_id="j"
        )
        assert n == count
        await service.settle_batch(
            unit,
            results=[BatchItemSettle(i.item_id, Buckets(usage, 0, 0, 0), False) for i in items[:n]],
        )
        assert ledger.spent("anthropic") == D(spent)
        items = items[n:]
    assert (
        await service.reserve_batch(provider="anthropic", model_id="m", items=items, job_id="j")
        is None
    )
    assert ledger.stopped("anthropic") == "cap"


async def test_stopped_invocation_denies_new_and_queued_batch_grants(ledger):
    service = LedgerService(ledger, {"m": FLAT}, {})
    items = [PlannedItem("i", 100, 10, 10)]

    async def request():
        return await service.reserve_batch(
            provider="anthropic", model_id="m", items=items, job_id="j"
        )

    unit, _ = await request()
    queued = asyncio.create_task(request())
    await asyncio.sleep(0)
    assert not queued.done()
    service.stop_grants()
    await service.settle_batch(unit, results=[BatchItemSettle("i", Buckets(50, 0, 0, 0), False)])
    assert await asyncio.wait_for(queued, 1) is None
    assert await request() is None
    assert [e["event"] for e in ledger.invocation_events()] == ["reserve", "settle"]


async def test_reserve_batch_refuses_an_item_bound_above_the_margin(ledger):
    service = LedgerService(ledger, {"m": FLAT}, {})

    async def take(items):
        return await service.reserve_batch(
            provider="anthropic", model_id="m", items=items, job_id="j"
        )

    with pytest.raises(ValueError):
        await take([])
    with pytest.raises(BenchmarkValidationError, match="margin"):
        await take([PlannedItem("a", 21_000_000, 0, 1)])
    assert ledger.events("r") == []


async def test_a_batch_is_reserved_at_the_standard_rate_and_booked_at_half(ledger):
    price = load_price_snapshot(SNAPSHOT).models["claude-haiku-5-5"]
    service = LedgerService(ledger, {"m": price}, {})
    items = [PlannedItem("a", 100000, 1000, 10), PlannedItem("b", 100001, 1000, 10)]
    unit, n = await service.reserve_batch(
        provider="anthropic", model_id="m", items=items, job_id="haiku"
    )
    event = ledger.events("r")[-1]
    assert n == 2 and D(event["bound"]) == D("0.0630005") and event["job_id"] == "haiku"
    await service.settle_batch(
        unit,
        results=[
            BatchItemSettle("a", Buckets(90000, 0, 0, 800), False),
            BatchItemSettle("b", Buckets(100500, 0, 0, 900), False),
        ],
    )
    assert ledger.spent("anthropic") == D(".03095")
    assert ledger.events("r")[-1]["input_bound_exceeded"] is True
    assert ledger.stopped("anthropic") is None


async def test_a_lost_batch_is_booked_at_twice_its_total_bound(ledger):
    service = LedgerService(ledger, {"m": FLAT}, {})

    async def take():
        return await service.reserve_batch(
            provider="anthropic",
            model_id="m",
            items=[PlannedItem("a", 1_000_000, 0, 1)],
            job_id="j",
        )

    first, _ = await take()
    waiting = asyncio.create_task(take())
    await asyncio.sleep(0)
    assert not waiting.done()
    await service.settle(first, outcome="worker_lost", buckets=None)
    second, _ = await waiting
    with pytest.raises(BenchmarkValidationError):
        await service.settle_batch(second, results=[BatchItemSettle("foreign", None, True)])
    await service.settle_batch(second, results=[])
    assert ledger.spent("anthropic") == D(4)


@pytest.mark.parametrize("actual", [D("-1"), D("Infinity"), D("NaN"), 0.25])
def test_invalid_batch_amount_cannot_change_balance_or_close_unit(ledger, actual):
    unit = reserve(ledger)
    with pytest.raises(BenchmarkValidationError):
        ledger.settle_batch(unit, items=[BookedItem("a", D("1.5"), actual, False, False)])
    assert [e["event"] for e in ledger.events("r")] == ["reserve"]
    assert ledger.settle_batch(unit, items=[BookedItem("a", D("1.5"), None, False, False)]) == 3


async def test_failed_persistence_keeps_batch_open_for_lost_worker_settlement(
    ledger, tmp_path, monkeypatch
):
    service = LedgerService(ledger, {"m": FLAT}, {})
    unit, _ = await service.reserve_batch(
        provider="anthropic", model_id="m", items=[PlannedItem("a", 1_000_000, 0, 1)], job_id="j"
    )
    original = os.fsync

    def fail(fd):
        monkeypatch.setattr(os, "fsync", original)
        raise OSError("disk unavailable")

    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(OSError, match="disk unavailable"):
        await service.settle(unit, outcome="timeout", buckets=None)
    await service.settle(unit, outcome="worker_lost", buckets=None)
    assert ledger.spent("anthropic") == 2
    path = tmp_path / "anthropic.jsonl"
    assert [json.loads(line)["event"] for line in path.read_text().splitlines()] == [
        "reserve",
        "settle",
    ]


async def test_unbilled_is_a_boolean_not_truthy_text(ledger):
    service = LedgerService(ledger, {"m": FLAT}, {})
    unit, _ = await service.reserve_batch(
        provider="anthropic", model_id="m", items=[PlannedItem("a", 1_000_000, 0, 1)], job_id="j"
    )
    with pytest.raises(BenchmarkValidationError):
        await service.settle_batch(unit, results=[BatchItemSettle("a", None, "false")])
    await service.settle_batch(unit, results=[])
    assert ledger.spent("anthropic") == 2


async def test_legacy_multiplier_and_per_item_input_clamp(ledger, tmp_path):
    from dataclasses import replace

    fixtures = Path("tests/benchmarks/fixtures/grading")
    data = yaml.safe_load((fixtures / "prices.yml").read_text())
    del data["models"][0]["batch_multiplier"]
    path = tmp_path / "legacy.yml"
    path.write_text(yaml.safe_dump(data))
    price = load_price_snapshot(path).models["scripted-haiku"]
    family = replace(
        as_model_family(load_panel(fixtures / "panel.yml").judges["haiku"]), max_input_tokens=100
    )
    service = LedgerService(ledger, {family.model_id: price}, {family.model_id: family})
    unit, n = await service.reserve_batch(
        provider="anthropic",
        model_id=family.model_id,
        items=[PlannedItem("a", 150, 0, 100), PlannedItem("b", 75, 0, 75)],
        job_id="clamped",
    )
    assert n == 2 and ledger.events("r")[-1]["input_bound"] == 175
    assert D(ledger.events("r")[-1]["bound"]) == D(".327855")
    await service.settle_batch(unit, results=[BatchItemSettle("a", Buckets(100, 0, 0, 0), False)])
    assert ledger.spent("anthropic") == D(".327930")


@pytest.fixture
def scripted_prices():
    fixtures = Path("tests/benchmarks/fixtures/grading")
    family = as_model_family(load_panel(fixtures / "panel.yml").judges["haiku"])
    return family, load_price_snapshot(fixtures / "prices.yml").models


async def test_stale_output_plan_cannot_pass_the_remaining_cap(tmp_path, scripted_prices):
    family, prices = scripted_prices
    ledger = Ledger.open(tmp_path, run_id="r", caps={"anthropic": D(".0002")})
    try:
        service = LedgerService(ledger, prices, {family.model_id: family})
        item = PlannedItem("a", 100, 0, 5)
        grant = await service.reserve_batch(
            provider="anthropic", model_id=family.model_id, items=[item], job_id="j"
        )
        assert grant is None
        assert service.item_bound(family.model_id, item) == D(".16394")
        assert [e["event"] for e in ledger.events("r")] == ["stop"]
        assert ledger.stopped("anthropic") == "cap"
    finally:
        ledger.close()


async def test_effective_output_bound_per_item_drives_split_and_booking(ledger, scripted_prices):
    family, prices = scripted_prices
    service = LedgerService(ledger, prices, {family.model_id: family}, {"anthropic": D(".4")})
    items = [PlannedItem(str(i), 100, cap, 1) for i, cap in enumerate([0, 20000, 1])]
    unit, count = await service.reserve_batch(
        provider="anthropic", model_id=family.model_id, items=items, job_id="j"
    )
    assert count == 2
    event = ledger.events("r")[-1]
    assert event["output_bound"] == 16384 + 20000
    assert D(event["bound"]) == D(".36404")
    await service.settle_batch(unit, results=[])
    assert ledger.spent("anthropic") == 2 * D(".36404")


@pytest.mark.parametrize("batch", [True, False])
async def test_durable_settle_releases_only_its_provider_after_stop_write_failure(
    tmp_path, monkeypatch, batch
):
    ledger = Ledger.open(tmp_path, run_id="r")
    service = LedgerService(ledger, {"m": FLAT, "o": ModelPrice("o", "openai", FLAT.tiers, "")}, {})
    item = PlannedItem("a", 100, 0, 10)

    async def take():
        return await service.reserve_batch(
            provider="anthropic", model_id="m", items=[item], job_id="j"
        )

    async def other():
        return await service.reserve(provider="openai", model_id="o", input_bound=1, output_bound=1)

    try:
        unit, _ = await take()
        other_unit = await other()
        waiting = asyncio.create_task(take())
        other_waiting = asyncio.create_task(other())
        await asyncio.sleep(0)
        assert not waiting.done() and not other_waiting.done()

        append = ledger._append

        def fail_stop(provider, fields):
            if fields["event"] == "stop":
                raise OSError("stop marker unavailable")
            return append(provider, fields)

        monkeypatch.setattr(ledger, "_append", fail_stop)
        usage = Buckets(1000, 0, 0, 0)
        if batch:
            await service.settle_batch(unit, results=[BatchItemSettle("a", usage, False)])
        else:
            await service.settle(unit, outcome="ok", buckets=usage)
        assert await asyncio.wait_for(waiting, 1) is None
        assert ledger.stopped("anthropic") == "overrun"
        assert not other_waiting.done()
        await service.settle(other_unit, outcome="rejected", buckets=None)
        await service.settle(
            await asyncio.wait_for(other_waiting, 1), outcome="rejected", buckets=None
        )
        assert [e["event"] for e in ledger.events("r") if e.get("unit_id") == unit] == [
            "reserve",
            "settle",
        ]
    finally:
        ledger.close()
    reopened = Ledger.open(tmp_path, run_id="next")
    try:
        assert reopened.stopped("anthropic") == "overrun"
        assert reopened.spent("anthropic") == (D(".0005") if batch else D(".001"))
        assert reopened.invocation_events() == []
    finally:
        reopened.close()


async def test_failed_batch_settle_write_holds_lock_until_teardown(ledger, monkeypatch):
    service = LedgerService(ledger, {"m": FLAT}, {})

    async def take():
        return await service.reserve_batch(
            provider="anthropic", model_id="m", items=[PlannedItem("a", 100, 0, 1)], job_id="j"
        )

    unit, _ = await take()
    with monkeypatch.context() as patch:

        def fail(*args):
            raise OSError("settle unavailable")

        patch.setattr(ledger, "_append", fail)
        with pytest.raises(OSError, match="settle unavailable"):
            await service.settle_batch(unit, results=[])
    waiting = asyncio.create_task(take())
    await asyncio.sleep(0)
    assert not waiting.done()
    assert ledger.spent("anthropic") == 0
    await service.settle(unit, outcome="worker_lost", buckets=None)
    newer, _ = await asyncio.wait_for(waiting, 1)
    await service.settle_batch(newer, results=[BatchItemSettle("a", None, True)])
    assert ledger.spent("anthropic") == D(".0002")


@pytest.mark.parametrize("accessor", ["invocation", "run"])
async def test_exported_settlement_items_are_detached(ledger, accessor):
    service = LedgerService(ledger, {"m": FLAT}, {})
    unit, _ = await service.reserve_batch(
        provider="anthropic", model_id="m", items=[PlannedItem("a", 100, 0, 1)], job_id="j"
    )
    await service.settle_batch(unit, results=[])
    exported = ledger.invocation_events() if accessor == "invocation" else ledger.events("r")
    exported[-1]["items"][0]["booked"] = "500"
    exported[-1]["items"].clear()
    expected = [{"item_id": "a", "booked": "0.0002", "status": "no_usage"}]
    assert ledger.events("r")[-1]["items"] == expected
    assert ledger.invocation_events()[-1]["items"] == expected
    assert ledger.spent("anthropic") == D(".0002")


@pytest.mark.parametrize(
    "change",
    [
        {"costs": [D(-1)]},
        {"costs": [D("NaN")]},
        {"sizes": [-1]},
        {"sizes": [False]},
        {"limit": D("Infinity")},
        {"max_items": True},
        {"max_bytes": 1.5},
    ],
)
def test_split_batch_rejects_invalid_amounts_and_limits(change):
    args = dict(costs=[D(0)], sizes=[0], limit=D(1), max_items=2, max_bytes=2)
    with pytest.raises(BenchmarkValidationError):
        split_batch(**(args | change))


async def test_retry_of_settled_unit_cannot_release_a_new_units_lock(ledger, monkeypatch):
    service = LedgerService(ledger, {"m": FLAT}, {})

    async def take():
        return await service.reserve_batch(
            provider="anthropic", model_id="m", items=[PlannedItem("a", 100, 0, 1)], job_id="j"
        )

    unit, _ = await take()
    append = ledger._append

    def fail_stop(provider, fields):
        if fields["event"] == "stop":
            raise OSError("stop unavailable")
        append(provider, fields)

    monkeypatch.setattr(ledger, "_append", fail_stop)
    await service.settle_batch(unit, results=[BatchItemSettle("a", Buckets(1000, 0, 0, 0), False)])
    ledger.acknowledge_overrun("anthropic")
    newer, _ = await take()
    waiting = asyncio.create_task(take())
    await asyncio.sleep(0)
    for retry in (
        service.settle(unit, outcome="worker_lost", buckets=None),
        service.settle_batch(unit, results=[]),
    ):
        with pytest.raises((KeyError, RuntimeError)):
            await retry
    assert not waiting.done()
    assert ledger.spent("anthropic") == D(".0005")
    await service.settle_batch(newer, results=[BatchItemSettle("a", None, True)])
    last, _ = await asyncio.wait_for(waiting, 1)
    await service.settle_batch(last, results=[BatchItemSettle("a", None, True)])
    assert ledger.spent("anthropic") == D(".0005")


def test_failed_overrun_acknowledgement_keeps_provider_stopped(ledger, monkeypatch):
    unit = reserve(ledger)
    ledger.settle(unit, outcome="ok", actual=D(2), buckets=None)
    append = ledger._append

    def fail_resume(provider, fields):
        if fields["event"] == "resume":
            raise OSError("resume unavailable")
        append(provider, fields)

    with monkeypatch.context() as patch:
        patch.setattr(ledger, "_append", fail_resume)
        with pytest.raises(OSError, match="resume unavailable"):
            ledger.acknowledge_overrun("anthropic")
    assert ledger.stopped("anthropic") == "overrun"
    assert reserve(ledger) is None
    ledger.acknowledge_overrun("anthropic")
    assert reserve(ledger) is not None


@pytest.mark.parametrize("accessor", ["invocation", "run"])
def test_exported_usage_buckets_are_detached(ledger, accessor):
    ledger.settle(reserve(ledger), outcome="ok", actual=D(1), buckets=Buckets(100, 0, 0, 1))
    exported = ledger.invocation_events() if accessor == "invocation" else ledger.events("r")
    exported[-1]["buckets"]["output"] = 999
    assert ledger.events("r")[-1]["buckets"]["output"] == 1
    assert ledger.invocation_events()[-1]["buckets"]["output"] == 1


async def test_family_without_output_cap_preserves_planned_bound(ledger, scripted_prices):
    from dataclasses import replace

    family, prices = scripted_prices
    family = replace(family, max_output_tokens=None)
    service = LedgerService(ledger, prices, {family.model_id: family})
    unit, n = await service.reserve_batch(
        provider="anthropic",
        model_id=family.model_id,
        items=[PlannedItem("a", 100, 200, 1)],
        job_id="j",
    )
    assert n == 1
    assert ledger.events("r")[-1]["output_bound"] == 200
    assert D(ledger.events("r")[-1]["bound"]) == D(".0021")
    await service.settle_batch(unit, results=[])
    assert ledger.spent("anthropic") == D(".0042")


@pytest.mark.parametrize("batch", [True, False])
async def test_stop_fsync_failure_rolls_back_marker_without_losing_settlement(
    tmp_path, monkeypatch, batch
):
    ledger = Ledger.open(tmp_path, run_id="r")
    try:
        service = LedgerService(ledger, {"m": FLAT}, {})
        unit, _ = await service.reserve_batch(
            provider="anthropic", model_id="m", items=[PlannedItem("a", 100, 0, 1)], job_id="j"
        )
        fsync = os.fsync
        calls = 0

        def fail_stop_sync(fd):
            nonlocal calls
            calls += 1
            if calls == 2:  # settle sync succeeds; stop sync fails; rollback sync succeeds
                raise OSError("stop sync unavailable")
            fsync(fd)

        with monkeypatch.context() as patch:
            patch.setattr(os, "fsync", fail_stop_sync)
            if batch:
                await service.settle_batch(
                    unit, results=[BatchItemSettle("a", Buckets(1000, 0, 0, 0), False)]
                )
            else:
                await service.settle(unit, outcome="ok", buckets=Buckets(1000, 0, 0, 0))
        # A failed marker cannot keep the service lock held or grant another unit.
        assert (
            await asyncio.wait_for(
                service.reserve_batch(
                    provider="anthropic",
                    model_id="m",
                    items=[PlannedItem("b", 100, 0, 1)],
                    job_id="j",
                ),
                1,
            )
            is None
        )
        saved = [
            json.loads(line) for line in (tmp_path / "anthropic.jsonl").read_text().splitlines()
        ]
        assert [event["event"] for event in saved] == ["reserve", "settle"]
        assert ledger.events("r") == saved
    finally:
        ledger.close()
    reopened = Ledger.open(tmp_path, run_id="next")
    try:
        assert reopened.stopped("anthropic") == "overrun"
        assert reopened.spent("anthropic") == (D(".0005") if batch else D(".001"))
        assert reopened.invocation_events() == []
    finally:
        reopened.close()
