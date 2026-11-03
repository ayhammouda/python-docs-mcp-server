"""Usage buckets, the price snapshot and the spend ledger (design §8, §12)."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from benchmarks.agentic.ledger import (
    PROVIDER_CAPS,
    PROVIDER_MARGINS,
    Ledger,
    LedgerService,
    ModelPrice,
    Rates,
    Tier,
    actual_cost,
    booked_cost,
    bound_cost,
    clamp_input_bound,
    load_price_snapshot,
    refuse_stale_snapshot,
)
from benchmarks.agentic.types import Buckets
from benchmarks.agentic.usage import anthropic_buckets, gemini_buckets, openai_buckets
from benchmarks.model_matrix import ModelFamily, load_model_matrix, model_family_from_raw
from benchmarks.runner import BenchmarkValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_MATRIX_PATH = REPO_ROOT / "docs" / "benchmarks" / "model-matrix.yml"
PRICE_SNAPSHOT_PATH = REPO_ROOT / "docs" / "benchmarks" / "price-snapshot.yml"
RUN_ID = "run-1"
D = Decimal


def _rates(input_: str, cache_write: str, cached: str, output: str) -> Rates:
    return Rates(D(input_), D(cache_write), D(cached), D(output))


def _flat(
    input_: str, cache_write: str, cached: str, output: str, *, provider: str = "anthropic"
) -> ModelPrice:
    return ModelPrice(
        model_id="m",
        provider=provider,
        tiers=(Tier(None, _rates(input_, cache_write, cached, output)),),
        source_url="https://example.invalid/pricing",
    )


def _tiered(
    *, up_to: int, low: tuple[str, str], high: tuple[str, str], provider: str = "anthropic"
) -> ModelPrice:
    return ModelPrice(
        model_id="m",
        provider=provider,
        tiers=(
            Tier(up_to, _rates(low[0], low[0], "0", low[1])),
            Tier(None, _rates(high[0], high[0], "0", high[1])),
        ),
        source_url="https://example.invalid/pricing",
    )


def _event(ledger: Ledger, kind: str, unit_id: str) -> dict[str, Any]:
    (found,) = [
        e for e in ledger.events(RUN_ID) if e["event"] == kind and e.get("unit_id") == unit_id
    ]
    return found


def _open(tmp_path: Path, **caps: str) -> Ledger:
    return Ledger.open(tmp_path, run_id=RUN_ID, caps={p: D(c) for p, c in caps.items()})


def _reserve(ledger: Ledger, provider: str, bound: str) -> str | None:
    return ledger.reserve(
        provider, model_id="m", kind="model_call", bound=D(bound), input_bound=1, output_bound=1
    )


def _family(model_id: str, provider: str, max_input: int) -> ModelFamily:
    return model_family_from_raw(
        {
            "id": f"{provider}-test",
            "provider": provider,
            "model_id": model_id,
            "client": "test",
            "token_count_method": "claude_normalized_payload",
            "latency_scope": "request_dispatch_to_final_answer",
            "headline_eligible": False,
            "headline_ineligibility_reason": "test",
            "api": "scripted",
            "arm": "hosted",
            "max_input_tokens": max_input,
            "max_output_tokens": 32_768,
            "output_cap_includes_reasoning": True,
            "framing_allowance_tokens": 256,
        }
    )


# --- usage buckets ---------------------------------------------------------------------------


def test_anthropic_buckets() -> None:
    assert anthropic_buckets(
        {
            "input_tokens": 100,
            "cache_creation_input_tokens": None,
            "cache_read_input_tokens": 20,
            "output_tokens": 30,
        }
    ) == Buckets(100, 0, 20, 30, 0)


def test_openai_buckets_subtract_cached_and_cache_write_from_input() -> None:
    assert openai_buckets(
        {
            "input_tokens": 1000,
            "input_tokens_details": {"cached_tokens": 200, "cache_write_tokens": 300},
            "output_tokens": 50,
            "output_tokens_details": {"reasoning_tokens": 40},
        }
    ) == Buckets(500, 300, 200, 50, 40)


def test_gemini_buckets_add_tool_use_prompt_and_thoughts() -> None:
    assert gemini_buckets(
        {
            "prompt_token_count": 1000,
            "cached_content_token_count": 100,
            "tool_use_prompt_token_count": 50,
            "candidates_token_count": 30,
            "thoughts_token_count": 20,
        }
    ) == Buckets(950, 0, 100, 50, 20)


def test_buckets_refuse_a_negative_uncached_input() -> None:
    with pytest.raises(BenchmarkValidationError, match="negative"):
        openai_buckets(
            {
                "input_tokens": 100,
                "input_tokens_details": {"cached_tokens": 200},
                "output_tokens": 1,
            }
        )


# --- price snapshot --------------------------------------------------------------------------


def test_committed_snapshot_has_the_researched_prices() -> None:
    snap = load_price_snapshot(PRICE_SNAPSHOT_PATH)  # values: research note §§A-C

    def tiers(model_id: str) -> list[tuple[int | None, Rates]]:
        return [(t.up_to_input_tokens, t.rates) for t in snap.models[model_id].tiers]

    assert tiers("claude-sonnet-5-5") == [(None, Rates(D("2"), D("4"), D("0.10"), D("10")))]
    assert tiers("gpt-6.1-sol") == [
        (272_000, Rates(D("2"), D("2.50"), D("0.10"), D("10"))),
        (None, Rates(D("4"), D("5"), D("0.20"), D("15"))),
    ]
    assert tiers("gemini-3.8-flash") == [(None, Rates(D("0.75"), D("0"), D("0.075"), D("3.75")))]
    assert snap.expires_on == date(2026, 12, 31)
    assert all(p.source_url.startswith("https://") for p in snap.models.values())


def test_stale_snapshot_is_refused_from_its_expiry_day() -> None:
    snap = load_price_snapshot(PRICE_SNAPSHOT_PATH)
    refuse_stale_snapshot(snap, today=date(2026, 12, 30))  # no error
    with pytest.raises(BenchmarkValidationError, match="2026-12-31"):
        refuse_stale_snapshot(snap, today=date(2026, 12, 31))


_GOOD_MODEL = (
    "  - model_id: m\n"
    "    provider: anthropic\n"
    "    source_url: https://example.invalid\n"
    "    tiers:\n"
    "      - up_to_input_tokens: null\n"
    "        input: '1'\n"
    "        cache_write: '1'\n"
    "        cached_input: '1'\n"
    "        output: '1'\n"
)


@pytest.mark.parametrize(
    "models,match",
    [
        (
            _GOOD_MODEL.replace("up_to_input_tokens: null", "up_to_input_tokens: 10"),
            "last tier",
        ),
        (_GOOD_MODEL.replace("output: '1'", "output: '-1'"), "negative"),
        (_GOOD_MODEL.replace("provider: anthropic", "provider: mistral"), "mistral"),
        (_GOOD_MODEL + _GOOD_MODEL, "duplicate"),
        (
            _GOOD_MODEL.replace(
                "    tiers:\n",
                "    tiers:\n      - up_to_input_tokens: 20\n        input: '1'\n"
                "        cache_write: '1'\n        cached_input: '1'\n        output: '1'\n"
                "      - up_to_input_tokens: 10\n        input: '1'\n"
                "        cache_write: '1'\n        cached_input: '1'\n        output: '1'\n",
            ),
            "ascending",
        ),
    ],
)
def test_load_price_snapshot_refuses_a_bad_entry(tmp_path: Path, models: str, match: str) -> None:
    path = tmp_path / "prices.yml"
    path.write_text(
        "schema_version: 1\ncurrency: USD\nunit: per_million_tokens\n"
        f"captured_on: '2026-10-31'\nexpires_on: null\nmodels:\n{models}",
        encoding="utf-8",
    )
    with pytest.raises(BenchmarkValidationError, match=match):
        load_price_snapshot(path)


# --- pricing ---------------------------------------------------------------------------------


def test_actual_cost_prices_each_bucket_at_its_own_rate() -> None:
    price = _flat("2", "2.5", "0.2", "10")  # input, cache_write, cached, output
    assert actual_cost(price, Buckets(1_000_000, 1_000_000, 1_000_000, 1_000_000, 500_000)) == D(
        "14.7"
    )


def test_tier_is_chosen_by_total_input_and_applies_to_output_too() -> None:
    price = _tiered(up_to=200_000, low=("2", "10"), high=("4", "15"))
    assert actual_cost(price, Buckets(200_000, 0, 0, 1_000_000)) == D("10.4")
    assert actual_cost(price, Buckets(200_001, 0, 0, 1_000_000)) == D("15.800004")


def test_bound_cost_uses_openai_cache_write_rate_and_uncached_rate_elsewhere() -> None:
    openai = _flat("2", "2.5", "0.2", "10", provider="openai")
    anthropic = _flat("2", "2.5", "0.2", "10", provider="anthropic")
    assert bound_cost(openai, input_bound=1_000_000, output_bound=0) == D("2.5")
    assert bound_cost(anthropic, input_bound=1_000_000, output_bound=0) == D("2")


def test_input_bound_is_clamped_to_the_model_maximum() -> None:
    assert clamp_input_bound(2_000_000, 1_048_576) == 1_048_576
    assert clamp_input_bound(10, 1_048_576) == 10


@pytest.mark.parametrize(
    "outcome,expected",
    [
        ("ok", "1.2"),
        ("rejected", "0"),
        ("rate_limited", "0"),
        ("server", "4"),
        ("timeout", "4"),
        ("connection", "4"),
        ("no_usage", "4"),
        ("worker_lost", "4"),
        ("unsettled_at_load", "4"),
    ],
)
def test_booked_cost_by_outcome(outcome: Any, expected: str) -> None:
    actual = D("1.2") if outcome == "ok" else None
    assert booked_cost(outcome, bound=D("2"), actual=actual) == D(expected)


# --- ledger ----------------------------------------------------------------------------------


def test_reserve_refuses_when_spend_plus_bound_exceeds_cap_and_stops_provider(
    tmp_path: Path,
) -> None:
    ledger = _open(tmp_path, anthropic="10")
    unit = _reserve(ledger, "anthropic", "6")
    assert unit is not None
    ledger.settle(unit, outcome="ok", actual=D("5"), buckets=Buckets(1, 0, 0, 1))
    unit = _reserve(ledger, "anthropic", "5")  # 5 + 5 == 10: at the cap is allowed
    assert unit is not None
    ledger.settle(unit, outcome="ok", actual=D("5"), buckets=Buckets(1, 0, 0, 1))
    assert _reserve(ledger, "anthropic", "0.01") is None
    assert ledger.stopped("anthropic") == "cap"
    assert _reserve(ledger, "anthropic", "0") is None  # sticky
    assert [e["event"] for e in ledger.events(RUN_ID)].count("stop") == 1
    ledger.close()


def test_reserve_raises_when_a_unit_is_already_in_flight(tmp_path: Path) -> None:
    ledger = _open(tmp_path, anthropic="10")
    assert _reserve(ledger, "anthropic", "1") is not None
    with pytest.raises(RuntimeError, match="in flight"):
        _reserve(ledger, "anthropic", "1")
    ledger.close()


def test_actual_above_bound_stops_the_provider_until_acknowledged(tmp_path: Path) -> None:
    ledger = _open(tmp_path, anthropic="10")
    unit = _reserve(ledger, "anthropic", "2")
    assert unit is not None
    booked = ledger.settle(unit, outcome="ok", actual=D("3"), buckets=Buckets(1, 0, 0, 1))
    assert booked == D("3") and ledger.stopped("anthropic") == "overrun"
    assert _reserve(ledger, "anthropic", "0") is None
    ledger.close()
    ledger = _open(tmp_path, anthropic="10")
    assert ledger.stopped("anthropic") == "overrun"
    ledger.acknowledge_overrun("anthropic")
    assert ledger.stopped("anthropic") is None
    assert [e["event"] for e in ledger.events(RUN_ID)][-1] == "resume"
    assert _reserve(ledger, "anthropic", "1") is not None
    ledger.close()


def test_ledger_files_hold_money_as_decimal_strings(tmp_path: Path) -> None:
    ledger = _open(tmp_path, anthropic="10")
    unit = _reserve(ledger, "anthropic", "2")
    assert unit is not None
    ledger.settle(
        unit, outcome="ok", actual=D("1.5"), buckets=Buckets(1, 0, 0, 1), input_bound_exceeded=True
    )
    reserve, settle = _event(ledger, "reserve", unit), _event(ledger, "settle", unit)
    assert reserve["bound"] == "2" and settle["booked"] == "1.5" and settle["actual"] == "1.5"
    assert settle["input_bound_exceeded"] is True
    assert (tmp_path / "anthropic.jsonl").read_text(encoding="utf-8").count("\n") == 2
    ledger.close()


def test_reopening_books_an_unsettled_unit_at_twice_its_bound(tmp_path: Path) -> None:
    ledger = _open(tmp_path, anthropic="10")
    assert _reserve(ledger, "anthropic", "3") is not None
    ledger.close()
    ledger = _open(tmp_path, anthropic="10")
    assert ledger.spent("anthropic") == D("6")
    assert [e["outcome"] for e in ledger.events(RUN_ID) if e["event"] == "settle"] == [
        "unsettled_at_load"
    ]
    ledger.close()


def test_a_second_open_on_the_same_directory_is_refused(tmp_path: Path) -> None:
    ledger = _open(tmp_path, anthropic="10")
    with pytest.raises(BenchmarkValidationError, match=r"\.lock"):
        _open(tmp_path, anthropic="10")
    ledger.close()
    _open(tmp_path, anthropic="10").close()  # released by close()


def test_the_lock_file_is_private_to_its_owner(tmp_path: Path) -> None:
    _open(tmp_path, anthropic="10").close()
    assert (tmp_path / ".lock").stat().st_mode & 0o077 == 0


def test_a_killed_ledger_holder_releases_the_lock_and_its_unit_is_booked_at_twice_its_bound(
    tmp_path: Path,
) -> None:
    script = (
        "import sys, time\n"
        "from decimal import Decimal\n"
        "from pathlib import Path\n"
        "from benchmarks.agentic.ledger import Ledger\n"
        "ledger = Ledger.open(Path(sys.argv[1]), run_id='holder',"
        " caps={'anthropic': Decimal(10)})\n"
        "ledger.reserve('anthropic', model_id='m', kind='model_call', bound=Decimal(3),"
        " input_bound=1, output_bound=1)\n"
        "print('reserved', flush=True)\n"
        "time.sleep(60)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path)],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "reserved"
        with pytest.raises(BenchmarkValidationError, match=r"\.lock"):
            _open(tmp_path, anthropic="10")
    finally:
        proc.kill()  # SIGKILL: no cleanup runs in the holder
        proc.wait()
    ledger = _open(tmp_path, anthropic="10")
    assert ledger.spent("anthropic") == D("6")
    ledger.close()


@pytest.mark.parametrize("provider", ["ollama", "mistral"])
def test_provider_without_a_cap_is_refused(tmp_path: Path, provider: str) -> None:
    ledger = _open(tmp_path, anthropic="10")
    with pytest.raises(BenchmarkValidationError, match=provider):
        _reserve(ledger, provider, "1")
    ledger.close()


# --- service ---------------------------------------------------------------------------------


def _service(tmp_path: Path, **caps: str) -> tuple[Ledger, LedgerService]:
    ledger = _open(tmp_path, **caps)
    prices = {
        "a": _flat("1", "1", "0", "1", provider="anthropic"),
        "o": _flat("1", "1", "0", "1", provider="openai"),
    }
    families = {"a": _family("a", "anthropic", 1_000_000), "o": _family("o", "openai", 1_000_000)}
    return ledger, LedgerService(ledger, prices, families)


async def test_service_allows_one_unit_in_flight_per_provider_and_runs_providers_in_parallel(
    tmp_path: Path,
) -> None:
    ledger, service = _service(tmp_path, anthropic="10", openai="10")
    first = await service.reserve(provider="anthropic", model_id="a", input_bound=1, output_bound=1)
    assert first is not None
    second = asyncio.ensure_future(
        service.reserve(provider="anthropic", model_id="a", input_bound=1, output_bound=1)
    )
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(asyncio.shield(second), timeout=0.1)
    other = await asyncio.wait_for(
        service.reserve(provider="openai", model_id="o", input_bound=1, output_bound=1),
        timeout=0.5,
    )
    assert other is not None
    await service.settle(first, outcome="ok", buckets=Buckets(1, 0, 0, 1))
    assert await asyncio.wait_for(second, timeout=0.5) is not None
    ledger.close()


async def test_service_releases_the_provider_when_a_reserve_is_refused(tmp_path: Path) -> None:
    ledger, service = _service(tmp_path, anthropic="0", openai="10")
    for _ in range(2):
        unit = await asyncio.wait_for(
            service.reserve(provider="anthropic", model_id="a", input_bound=1, output_bound=1),
            timeout=0.5,
        )
        assert unit is None
    ledger.close()


async def test_service_prices_a_bound_above_the_tier_threshold_at_the_high_tier(
    tmp_path: Path,
) -> None:
    ledger = _open(tmp_path, openai="65")
    price = ModelPrice(
        model_id="m",
        provider="openai",
        tiers=(
            Tier(272_000, _rates("2", "2.5", "0.1", "10")),
            Tier(None, _rates("4", "5", "0.2", "15")),
        ),
        source_url="https://example.invalid/pricing",
    )
    service = LedgerService(ledger, {"m": price}, {"m": _family("m", "openai", 1_017_232)})
    unit = await service.reserve(
        provider="openai", model_id="m", input_bound=300_000, output_bound=1_000
    )
    assert unit is not None
    assert _event(ledger, "reserve", unit)["bound"] == "1.515"  # 300_000*5 + 1_000*15, per 1M
    await service.settle(unit, outcome="ok", buckets=Buckets(310_000, 0, 0, 10))
    assert _event(ledger, "settle", unit)["input_bound_exceeded"] is True
    assert ledger.spent("openai") == D("1.24015")  # actual, at the high tier
    ledger.close()


async def test_service_clamps_the_input_bound_to_the_family_maximum(tmp_path: Path) -> None:
    ledger = _open(tmp_path, openai="65")
    service = LedgerService(
        ledger,
        {"m": _flat("1", "1", "0", "0", provider="openai")},
        {"m": _family("m", "openai", 1_000)},
    )
    unit = await service.reserve(provider="openai", model_id="m", input_bound=5_000, output_bound=0)
    assert unit is not None
    assert _event(ledger, "reserve", unit)["input_bound"] == 1_000
    await service.settle(unit, outcome="server", buckets=None)
    assert ledger.spent("openai") == D("0.002")  # 2x the clamped bound
    ledger.close()


def test_committed_matrix_largest_single_call_bound_is_below_every_margin() -> None:
    matrix, snap = load_model_matrix(MODEL_MATRIX_PATH), load_price_snapshot(PRICE_SNAPSHOT_PATH)
    hosted = [f for f in matrix.model_families if f.arm == "hosted"]
    assert len(hosted) == 3
    for f in hosted:
        assert f.max_input_tokens is not None and f.max_output_tokens is not None
        cost = bound_cost(
            snap.models[f.model_id],
            input_bound=f.max_input_tokens,
            output_bound=f.max_output_tokens,
        )
        assert cost < PROVIDER_MARGINS[f.provider]  # about $2.33, $5.58 and $0.91


def test_caps_and_margins_add_up_to_the_budget() -> None:
    assert {p: PROVIDER_CAPS[p] + PROVIDER_MARGINS[p] for p in PROVIDER_CAPS} == {
        "anthropic": D(200),
        "openai": D(75),
        "google": D(200),
    }
