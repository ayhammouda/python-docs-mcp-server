"""Live-run safety: the one authorization gate, reference rates and the full-run gate.

No test makes a live call: the gate's refusals are the subject, and every run is scripted.
"""

from __future__ import annotations

import io
import json
import shutil
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from benchmarks.agentic.host import WorkerJob
from benchmarks.agentic.ledger import PROVIDER_CAPS, Ledger
from benchmarks.agentic.live import (
    PILOT_TRAJECTORIES_PER_PROVIDER,
    SAFETY,
    WINDOW_S,
    ProviderPlan,
    ReferenceRate,
    authorize_live,
    confirmation_text,
    full_run_gate,
    interactive_confirm,
    provider_plans,
    reference_rates,
)
from benchmarks.agentic.loop import HOSTED_CAPS
from benchmarks.model_matrix import METHODOLOGY_TOKEN_LABEL
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks.test_agentic_sandbox import FAMILIES, SYSTEMS

D = Decimal
SLOW_ANSWER = {"text": "ok", "delay_s": 0.05, "usage": {"input_tokens": 1000, "output_tokens": 100}}


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def _jobs(**counts: int) -> list[WorkerJob]:
    """One no-docs job per family id, with ``count`` trajectories each."""
    jobs = []
    for family_id, count in counts.items():
        family = FAMILIES[family_id]
        trajectories = tuple(
            {"corpus_id": f"Q{i}", "prompt": "q", "repeat": 1} for i in range(count)
        )
        job = WorkerJob(
            f"{family.id}--no-docs", family, SYSTEMS["no-docs"], trajectories, HOSTED_CAPS
        )
        jobs.append(job)
    return jobs


def _rate(family_id: str, provider: str, usd: str, seconds: float = 10.0) -> ReferenceRate:
    family = FAMILIES[family_id]
    return ReferenceRate(family_id, provider, 90, D(usd), seconds, family.model_id, family.api)


def _write_record(
    path: Path,
    family_id: str,
    provider: str,
    wall_ms: float,
    wait_ms: float,
    *,
    model_id: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cell, corpus = path.parent.name, path.name.split("--")[0].removesuffix(".json")
    record = {
        "competitor_id": cell,
        "system_id": "no-docs",
        "corpus_id": corpus,
        "model_family_id": family_id,
        "model_id": model_id or family_id,
        "provider": provider,
        "timing": {"wall_ms": wall_ms, "grant_wait_ms": wait_ms},
    }
    path.write_text(json.dumps(record), encoding="utf-8")


def _pilot_summary(run: Path, status: str, planned: int, scored: int, openai_spend: str) -> None:
    summary = {
        "status": status,
        "planned_cells": planned,
        "scored_cells": scored,
        "discarded": [
            {"cell": path.parent.name, "corpus_id": path.name.split("--")[0], "attempt": 1}
            for path in (run / "discarded").glob("*/*.json")
        ],
        "ledger": {"openai": {"spent_before": "4", "spent_after": str(D(4) + D(openai_spend))}},
    }
    (run / "run-summary.json").write_text(json.dumps(summary))


def _settled_unit(
    unit_id: str, family: str, booked: str, outcome: str, *, model_id: str | None = None
) -> list[dict[str, str]]:
    unit = {"provider": "openai", "unit_id": unit_id}
    return [
        {
            **unit,
            "event": "reserve",
            "job_id": f"{family}--no-docs",
            "model_id": model_id or family,
        },
        {**unit, "event": "settle", "booked": booked, "outcome": outcome},
    ]


def _write_snapshot(run: Path, *families: dict[str, Any]) -> None:
    path = run / "snapshots" / "model-matrix.yml"
    path.parent.mkdir(parents=True)
    path.write_text(
        yaml.safe_dump(
            {
                "methodology_token_label": METHODOLOGY_TOKEN_LABEL,
                "model_families": [*families, FAMILIES["scripted-google"].raw],
            }
        )
    )


def test_same_model_scripted_pilot_is_refused_before_confirmation(tmp_path: Path) -> None:
    from benchmarks.agentic.run import run_agentic
    from tests.benchmarks.test_agentic_run import (
        COMMITTED_PRICES,
        _config,
        _container,
        _live_config,
        _matrix_with,
    )

    family = FAMILIES["gpt-6.1-sol"]
    alias = FAMILIES["scripted-openai"].raw | {"id": family.id, "model_id": family.model_id}
    matrix = _matrix_with(tmp_path, {family.id: [SLOW_ANSWER]}, extra=[alias])
    pilot = tmp_path / "pilot"
    config = _config(
        tmp_path,
        matrix_path=matrix,
        prices_path=COMMITTED_PRICES,
        out_dir=pilot,
        models=(family.id,),
        systems=("no-docs",),
        questions=("SYN-EX-001",),
        repeats=1,
    )
    summary = run_agentic(config)
    assert summary["scored_cells"] == summary["planned_cells"] == 1
    assert reference_rates(pilot)[family.id].usd_per_cell > 0
    matrix.unlink()  # Only the pilot's snapshot is available at authorization.
    launcher, docker = _container(tmp_path)
    with pytest.raises(BenchmarkValidationError, match="reference"):
        run_agentic(
            _live_config(tmp_path, models=(family.id,), repeats=91, reference_run=pilot),
            launcher=launcher,
            today=date(2026, 11, 4),
            confirm=lambda _: pytest.fail("scripted pilot reached confirmation"),
        )
    assert docker.calls == []


def _single_cell_reference(
    run: Path,
    model_id: str,
    api: str,
    *,
    transcript_model: str | None = None,
    reserved_model: str | None = None,
) -> None:
    family = FAMILIES["gpt-6.1-sol"]
    _write_snapshot(run, family.raw | {"model_id": model_id, "api": api})
    record = run / "transcripts" / f"no-docs--{family.id}--r1" / "Q1.json"
    _write_record(
        record, family.id, family.provider, 200, 100, model_id=transcript_model or model_id
    )
    _pilot_summary(run, "completed", 1, 1, "0.10")
    events = _settled_unit("u1", family.id, "0.10", "ok", model_id=reserved_model or model_id)
    (run / "ledger-events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))


@pytest.mark.parametrize(
    ("model_id", "api", "usable"),
    [
        ("other-model", "openai-responses", False),
        ("gpt-6.1-sol", "openai-chat-completions", False),
        ("gpt-6.1-sol", "openai-responses", True),
    ],
)
def test_only_matching_live_measurements_estimate_a_full_run(
    tmp_path: Path, model_id: str, api: str, usable: bool
) -> None:
    _single_cell_reference(tmp_path, model_id, api)
    rates = reference_rates(tmp_path)
    jobs = _jobs(**{"gpt-6.1-sol": 91})
    plans = provider_plans(jobs, {}, PROVIDER_CAPS, rates)
    assert plans[0].estimate == (D("9.10") if usable else None)
    assert (full_run_gate(plans, jobs, rates) == []) == usable


@pytest.mark.parametrize("overrides", [{"transcript_model": "other"}, {"reserved_model": "other"}])
def test_reference_measurements_must_belong_to_the_snapshot_model(
    tmp_path: Path, overrides: dict[str, str]
) -> None:
    _single_cell_reference(tmp_path, "gpt-6.1-sol", "openai-responses", **overrides)
    with pytest.raises(BenchmarkValidationError, match="reference"):
        reference_rates(tmp_path)


def test_reference_spend_excludes_recovery_of_an_earlier_run(tmp_path: Path) -> None:
    from benchmarks.agentic.run import run_agentic
    from tests.benchmarks.test_agentic_run import _config, _matrix_with

    ledger = Ledger.open(tmp_path / "ledger", run_id="interrupted")
    try:
        unit = ledger.reserve(
            "openai",
            model_id="scripted-2",
            kind="model_call",
            bound=D("1"),
            input_bound=1000,
            output_bound=100,
            job_id="previous--no-docs",
        )
        assert unit is not None
    finally:
        ledger.close()
    config = _config(
        tmp_path,
        models=("scripted-openai",),
        systems=("no-docs",),
        questions=("SYN-EX-001",),
        repeats=1,
        run_id="pilot",
        matrix_path=_matrix_with(tmp_path, {"scripted-openai": [SLOW_ANSWER]}),
    )
    summary = run_agentic(config)
    assert D(summary["ledger"]["openai"]["spent_before"]) == 2
    assert D(summary["ledger"]["openai"]["spent_after"]) == D("2.002")
    events_path = config.out_dir / "ledger-events.jsonl"
    events = [json.loads(line) for line in events_path.read_text().splitlines()]
    assert events[0]["unit_id"] == unit and events[0]["outcome"] == "unsettled_at_load"
    assert reference_rates(config.out_dir)["scripted-openai"].usd_per_cell == D("0.002")

    # An unmatched ordinary settlement is invalid even if provider balances still reconcile.
    corrupt = shutil.copytree(config.out_dir, tmp_path / "corrupt")
    events[0]["outcome"] = "timeout"
    (corrupt / events_path.name).write_text("".join(json.dumps(e) + "\n" for e in events))
    with pytest.raises(BenchmarkValidationError, match="reference"):
        reference_rates(corrupt)


def test_reference_rates_aggregate_every_attempt_of_a_cell(tmp_path: Path) -> None:
    run = tmp_path / "pilot"
    _write_snapshot(run, FAMILIES["gpt-6.1-sol"].raw)
    for r in range(1, 31):
        cell = f"no-docs--gpt-6.1-sol--r{r}"
        for q in ("Q1", "Q2", "Q3"):
            _write_record(
                run / "transcripts" / cell / f"{q}.json", "gpt-6.1-sol", "openai", 2000, 500
            )
            discarded = run / "discarded" / cell / f"{q}--attempt1.json"
            _write_record(discarded, "gpt-6.1-sol", "openai", 1000, 0)
    _pilot_summary(run, "completed", 90, 90, "9")
    events = []
    for i in range(180):
        outcome = "ok" if i % 2 else "timeout"
        events.extend(_settled_unit(f"u{i}", "gpt-6.1-sol", "0.05", outcome))
    (run / "ledger-events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    (rate,) = reference_rates(run).values()
    assert (rate.family_id, rate.provider, rate.cells) == ("gpt-6.1-sol", "openai", 90)
    assert rate.usd_per_cell == D("0.10")  # $9 over 90 logical cells, not over 180 records
    assert rate.seconds_per_cell == pytest.approx(2.5)  # (2.0 - 0.5) + 1.0, per cell
    # A partial copy still has all scored cells and spend, but loses some retry time.
    (run / "discarded/no-docs--gpt-6.1-sol--r1/Q1--attempt1.json").unlink()
    with pytest.raises(BenchmarkValidationError, match="discarded"):
        reference_rates(run)


@pytest.mark.parametrize(
    ("status", "scored", "booked", "outcome", "wall_ms"),
    [
        ("aborted", False, None, None, 200),
        ("aborted", True, "0.20", "ok", 200),
        ("completed", False, "0.20", "timeout", 200),
        ("completed", True, None, None, 200),
        ("completed", True, "0", "rejected", 200),
        ("completed", True, "0.20", "timeout", 200),
        ("completed", True, "0.20", "ok", 100),
    ],
)
def test_unusable_reference_is_refused_before_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    scored: bool,
    booked: str | None,
    outcome: str | None,
    wall_ms: int,
) -> None:
    from benchmarks.agentic.run import run_agentic
    from tests.benchmarks.test_agentic_run import _container, _live_config

    family = "gpt-6.1-sol"
    pilot = tmp_path / "pilot"
    _write_snapshot(pilot, FAMILIES[family].raw)
    kind, filename = ("transcripts", "Q1.json") if scored else ("discarded", "Q1--attempt1.json")
    _write_record(
        pilot / kind / f"no-docs--{family}--r1" / filename, family, "openai", wall_ms, 100
    )
    _pilot_summary(pilot, status, 1, int(scored), booked or "0")
    events = []
    if booked is not None and outcome is not None:
        events = _settled_unit("u1", family, booked, outcome)
    (pilot / "ledger-events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    launcher, docker = _container(tmp_path)
    config = _live_config(tmp_path, models=(family,), repeats=91, reference_run=pilot)
    monkeypatch.delenv("CI", raising=False)
    with pytest.raises(BenchmarkValidationError, match="reference"):
        run_agentic(
            config,
            launcher=launcher,
            today=date(2026, 11, 4),
            confirm=lambda text: pytest.fail(f"unexpected confirmation: {text}"),
        )
    assert docker.calls == []


def test_reference_spend_and_time_follow_the_family_that_incurred_them(tmp_path: Path) -> None:
    _write_snapshot(
        tmp_path,
        *(FAMILIES["gpt-6.1-sol"].raw | {"id": f, "model_id": f} for f in ("cheap", "expensive")),
    )
    events = []
    for family, price, wall in [("cheap", "0.01", 200), ("expensive", "3.00", 4000)]:
        record = tmp_path / "transcripts" / f"no-docs--{family}--r1" / "Q1.json"
        _write_record(record, family, "openai", wall, 100)
        events.extend(_settled_unit(family, family, price, "ok"))
    (tmp_path / "ledger-events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    _pilot_summary(tmp_path, "completed", 2, 2, "3.01")
    rates = reference_rates(tmp_path)
    assert rates["cheap"] == ReferenceRate(
        "cheap", "openai", 1, D("0.01"), 0.1, "cheap", "openai-responses"
    )
    assert rates["expensive"] == ReferenceRate(
        "expensive", "openai", 1, D(3), 3.9, "expensive", "openai-responses"
    )
    # Losing even a suffix of complete ledger lines can leave positive but understated rates.
    _pilot_summary(tmp_path, "completed", 2, 2, "3.02")
    with pytest.raises(BenchmarkValidationError, match="reference"):
        reference_rates(tmp_path)


def test_positive_subcent_estimate_does_not_display_as_zero() -> None:
    plan = ProviderPlan("openai", 1, D(0), D(65), D("0.001"))
    text = confirmation_text([plan])
    assert "estimate <$0.01" in text and "$0.00" not in text


@pytest.mark.parametrize("rates", [{}, {"gpt-6.1-sol": _rate("gpt-6.1-sol", "google", "1")}])
def test_explicit_reference_must_cover_the_requested_metered_family(
    rates: dict[str, ReferenceRate],
) -> None:
    jobs = _jobs(**{"gpt-6.1-sol": 1})
    plans = provider_plans(jobs, {}, PROVIDER_CAPS, rates)
    assert any("gpt-6.1-sol" in reason for reason in full_run_gate(plans, jobs, rates))


def test_reference_rates_from_a_finished_scripted_run(tmp_path: Path) -> None:
    from benchmarks.agentic.run import run_agentic
    from tests.benchmarks.test_agentic_run import _config

    config = _config(
        tmp_path,
        models=("scripted-anthropic", "scripted-openai"),
        systems=("no-docs",),
        questions=("SYN-EX-001",),
        repeats=2,
    )
    summary = run_agentic(config)
    rates = reference_rates(tmp_path / "run")
    assert set(rates) == {"scripted-anthropic", "scripted-openai"}
    anthropic = rates["scripted-anthropic"]
    assert anthropic.cells == 2 and anthropic.provider == "anthropic"
    spent = D(summary["ledger"]["anthropic"]["spent_after"])
    assert anthropic.usd_per_cell == spent / 2 and anthropic.seconds_per_cell > 0


def test_the_gate_refuses_a_provider_whose_scaled_cost_exceeds_its_remaining_cap() -> None:
    jobs = _jobs(**{"gpt-6.1-sol": 90})
    rates = {"gpt-6.1-sol": _rate("gpt-6.1-sol", "openai", "0.05")}  # $4.50 x 1.25 = $5.625
    plans = provider_plans(jobs, {"openai": D(60)}, PROVIDER_CAPS, rates)
    assert plans == [ProviderPlan("openai", 90, D(60), D(65), D("4.50"))]
    problems = full_run_gate(plans, jobs, rates)
    assert len(problems) == 1 and "openai" in problems[0] and "5.625" in problems[0]
    assert (
        full_run_gate(provider_plans(jobs, {"openai": D(0)}, PROVIDER_CAPS, rates), jobs, rates)
        == []
    )


def test_the_gate_refuses_a_model_whose_scaled_time_exceeds_24_hours() -> None:
    jobs = _jobs(**{"claude-sonnet-5-5": 90})
    seconds = WINDOW_S / 90 / float(SAFETY) + 1  # just over the window once scaled
    rates = {"claude-sonnet-5-5": _rate("claude-sonnet-5-5", "anthropic", "0.01", seconds)}
    plans = provider_plans(jobs, {"anthropic": D(0)}, PROVIDER_CAPS, rates)
    problems = full_run_gate(plans, jobs, rates)
    assert len(problems) == 1 and "claude-sonnet-5-5" in problems[0] and "24" in problems[0]


def test_a_live_run_larger_than_the_pilot_needs_a_reference_run() -> None:
    jobs = _jobs(**{"gpt-6.1-sol": PILOT_TRAJECTORIES_PER_PROVIDER + 1})
    plans = provider_plans(jobs, {"openai": D(0)}, PROVIDER_CAPS, None)
    assert plans[0].estimate is None
    problems = full_run_gate(plans, jobs, None)
    assert len(problems) == 1 and "--reference-run" in problems[0] and "91" in problems[0]
    pilot = _jobs(**{"gpt-6.1-sol": PILOT_TRAJECTORIES_PER_PROVIDER})
    assert (
        full_run_gate(provider_plans(pilot, {"openai": D(0)}, PROVIDER_CAPS, None), pilot, None)
        == []
    )


def test_confirmation_lists_every_provider_with_its_remaining_cap_and_estimate() -> None:
    jobs = _jobs(**{"claude-sonnet-5-5": 90, "gpt-6.1-sol": 30, "gemma4-12b": 30})
    rates = {"claude-sonnet-5-5": _rate("claude-sonnet-5-5", "anthropic", "0.05")}
    plans = provider_plans(jobs, {"anthropic": D(10), "openai": D(1)}, PROVIDER_CAPS, rates)
    assert [p.provider for p in plans] == ["anthropic", "ollama", "openai"]
    lines = confirmation_text(plans).splitlines()
    (anthropic,) = [line for line in lines if line.startswith("anthropic")]
    for part in ("90 trajectories", "spent $10", "cap $180", "$170 remaining", "estimate $4.50"):
        assert part in anthropic
    assert "the ledger stops at the cap; one overrun is at most the $20 margin" in anthropic
    (openai,) = [line for line in lines if line.startswith("openai")]
    assert "no reference run" in openai and "$64 remaining" in openai
    (ollama,) = [line for line in lines if line.startswith("ollama")]
    assert "30 trajectories" in ollama and "local, unmetered" in ollama
    assert ProviderPlan("ollama", 30, None, None, None) in plans


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", " on "])
def test_ci_refuses_a_live_run_even_with_a_terminal(value: str) -> None:
    asked: list[str] = []

    def confirm(text: str) -> bool:
        asked.append(text)
        return True

    with pytest.raises(BenchmarkValidationError, match="refused under CI"):
        authorize_live("plan", environ={"CI": value}, confirm=confirm)
    assert asked == []


@pytest.mark.parametrize("value", [None, "", "0", "false", "no", " FALSE "])
def test_a_ci_value_that_is_not_truthy_reaches_the_confirmation(value: str | None) -> None:
    environ = {} if value is None else {"CI": value}
    authorize_live("plan", environ=environ, confirm=lambda text: True)


def test_a_non_tty_stdin_refuses_a_live_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CI", raising=False)
    stdin, stderr = io.StringIO("yes\n"), io.StringIO()
    assert interactive_confirm("plan", stdin=stdin, stderr=stderr) is False
    with pytest.raises(BenchmarkValidationError, match="not confirmed"):
        authorize_live(
            "plan",
            environ={},
            confirm=lambda text: interactive_confirm(
                text, stdin=io.StringIO("yes\n"), stderr=stderr
            ),
        )


@pytest.mark.parametrize(
    "answer,accepted",
    [("y\n", False), ("YES \n", False), ("\n", False), ("", False), ("yes\n", True), ("yes", True)],
)
def test_interactive_confirm_accepts_only_yes(answer: str, accepted: bool) -> None:
    assert interactive_confirm("plan", stdin=_Tty(answer), stderr=io.StringIO()) is accepted


def test_the_confirmation_prompt_never_reaches_stdout(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdin", _Tty("yes\n"))
    assert interactive_confirm("anthropic: 90 trajectories") is True
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "anthropic: 90 trajectories" in captured.err
    assert "Type yes to start this live run: " in captured.err


def test_local_fallback_requires_its_own_timing_reference() -> None:
    jobs = _jobs(**{"gemma4-12b": 300})
    original = jobs[0]
    jobs[0] = replace(original, family=replace(original.family, model_id="gemma4:e4b"))
    rate = _rate(original.family.id, "ollama", "0")
    rates = {rate.family_id: rate}
    plans = provider_plans(jobs, {}, PROVIDER_CAPS, rates)
    problems = full_run_gate(plans, jobs, rates)
    assert len(problems) == 1 and "gemma4-12b" in problems[0]
    smoke = _jobs(**{"gemma4-12b": 10})
    assert full_run_gate(provider_plans(smoke, {}, PROVIDER_CAPS, None), smoke, None) == []
