"""Live-run safety: the one authorization gate, reference rates and the full-run gate.

No test makes a live call: the gate's refusals are the subject, and every run is scripted.
"""

from __future__ import annotations

import io
import json
from decimal import Decimal
from pathlib import Path

import pytest

from benchmarks.agentic.host import WorkerJob
from benchmarks.agentic.ledger import PROVIDER_CAPS
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
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks.test_agentic_sandbox import FAMILIES, SYSTEMS

D = Decimal


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
    return ReferenceRate(family_id, provider, 90, D(usd), seconds)


def _write_record(
    path: Path, family_id: str, provider: str, wall_ms: float, wait_ms: float
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cell, corpus = path.parent.name, path.name.split("--")[0].removesuffix(".json")
    record = {
        "competitor_id": cell,
        "corpus_id": corpus,
        "model_family_id": family_id,
        "provider": provider,
        "timing": {"wall_ms": wall_ms, "grant_wait_ms": wait_ms},
    }
    path.write_text(json.dumps(record), encoding="utf-8")


def test_reference_rates_aggregate_every_attempt_of_a_cell(tmp_path: Path) -> None:
    run = tmp_path / "pilot"
    for r in range(1, 31):
        cell = f"no-docs--gpt-6.1-sol--r{r}"
        for q in ("Q1", "Q2", "Q3"):
            _write_record(
                run / "transcripts" / cell / f"{q}.json", "gpt-6.1-sol", "openai", 2000, 500
            )
            discarded = run / "discarded" / cell / f"{q}--attempt1.json"
            _write_record(discarded, "gpt-6.1-sol", "openai", 1000, 0)
    events = [
        {"event": "settle", "provider": "openai", "booked": "0.05", "unit_id": f"u{i}"}
        for i in range(180)
    ]
    (run / "ledger-events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    (rate,) = reference_rates(run).values()
    assert (rate.family_id, rate.provider, rate.cells) == ("gpt-6.1-sol", "openai", 90)
    assert rate.usd_per_cell == D("0.10")  # $9 over 90 logical cells, not over 180 records
    assert rate.seconds_per_cell == pytest.approx(2.5)  # (2.0 - 0.5) + 1.0, per cell


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
