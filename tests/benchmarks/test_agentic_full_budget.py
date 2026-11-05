"""Full-run financial preflight using local workers and explicitly synthetic evidence.

Artifacts use mock transports and synthetic prices and keys, never paid calls.
Full-run confirmation always declines; ordinary production writers retain every
input, vote, receipt and settlement without relabeling scripted evidence.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import date
from decimal import Decimal as D

import pytest
import yaml

from benchmarks.agentic.run import run_agentic
from benchmarks.grading import run as grading_run
from benchmarks.grading.panel import PANEL_PATH
from benchmarks.grading.preflight import reference_grading_rates
from benchmarks.grading.run import run_grading
from benchmarks.grading.score import _ledger_spend
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks.grading_pilot_fixture import (
    NoLaunch,
    answer_mock_pilot,
    grade_mock_pilot,
    read,
)
from tests.benchmarks.test_agentic_live import _transcript_inventory
from tests.benchmarks.test_agentic_run import _live_config
from tests.benchmarks.test_grading_financial_preflight import run_grading_jobs, without_signals
from tests.benchmarks.test_grading_run import FIX, panel_file
from tests.benchmarks.test_grading_run import config as grading_config


def write(path, value):
    path.write_text(json.dumps(value))


def bind_fixture_transcripts(root):
    summary = read(root / "run-summary.json")
    summary["transcript_inventory"] = _transcript_inventory(root)
    write(root / "run-summary.json", summary)


def make_pilot(tmp_path, *, live, no_grade=False):
    panel_path = panel_file(tmp_path)
    panel = yaml.safe_load(panel_path.read_text())
    production = yaml.safe_load(PANEL_PATH.read_text())
    aliases = {}
    for name, judge in panel["judges"].items():
        aliases[judge["model_id"]] = production["judges"][name]["model_id"]
        judge["model_id"] = aliases[judge["model_id"]]
        if live:
            judge["api"] = production["judges"][name]["api"]
    panel_path.write_text(yaml.safe_dump(panel))
    prices = yaml.safe_load((FIX / "prices.yml").read_text())
    for model in prices["models"]:
        model["model_id"] = aliases[model["model_id"]]
        if model["model_id"] == "gpt-6.1-sol":
            # The normal answering mock reports 1,000 total input tokens. Flat
            # synthetic rates make that $0.10 without editing any ledger event.
            model["tiers"] = [
                {
                    "up_to_input_tokens": None,
                    "input": "100",
                    "cache_write": "100",
                    "cached_input": "100",
                    "output": "0",
                }
            ]
    prices["models"] = list({m["model_id"]: m for m in prices["models"]}.values())
    prices_path = tmp_path / "synthetic-prices.yml"
    prices_path.write_text(yaml.safe_dump(prices))
    cfg = grading_config(
        tmp_path,
        grading_run_id="pilot-grades",
        panel_path=panel_path,
        prices_path=prices_path,
        live=live,
        confirm_max_spend=D("1") if live else None,
    )
    with pytest.MonkeyPatch.context() as patch:
        root = answer_mock_pilot(
            tmp_path,
            patch,
            prices_path=prices_path,
            answer="An answer with </answer> needs reference adjudication." if no_grade else None,
        )
        if live:
            # Genuine API-shaped replies, produced through the same mocked SDK path
            # as the normal preflight regression. 100 input + 2 output tokens at
            # fixture prices book $0.000060 per provider, including batch discount.
            grade_mock_pilot(cfg, patch, small_usage=True)
        else:
            patch.setattr(grading_run, "run_jobs", run_grading_jobs)
            patch.setattr(grading_run, "_until_signalled", without_signals)
            run_grading(cfg)
            write(
                root / "grading/summary.json",
                {
                    "graded_cells": 1,
                    "pending_cells": [],
                    "spend": _ledger_spend(root / "grading", 1),
                },
            )
    return root


@pytest.fixture(scope="module")
def mock_pilot(tmp_path_factory):
    return make_pilot(tmp_path_factory.mktemp("full-budget-mock-pilot"), live=True)


@pytest.fixture(scope="module")
def scripted_pilot(tmp_path_factory):
    return make_pilot(tmp_path_factory.mktemp("full-budget-scripted-pilot"), live=False)


@pytest.fixture
def pilot(tmp_path, mock_pilot):
    root = shutil.copytree(mock_pilot, tmp_path / "run")
    # Every corruption case starts from a portable, valid bundle. A rejection
    # from stale fixture identity must never satisfy a downstream financial test.
    assert reference_grading_rates(root) == dict.fromkeys(
        ("anthropic", "openai", "google"), D("0.000060")
    )
    return root


def attempt(tmp_path, pilot, shown):
    before = {p: p.read_bytes() for p in pilot.rglob("*") if p.is_file()}
    ledger = tmp_path / "ledger"
    durable = {p: p.read_bytes() for p in ledger.glob("*.jsonl")}
    try:
        return run_agentic(
            _live_config(
                tmp_path,
                out_dir=tmp_path / "full",
                models=("gpt-6.1-sol",),
                repeats=91,
                reference_run=pilot,
            ),
            launcher=NoLaunch(),
            today=date(2026, 11, 4),
            confirm=lambda text: shown.append(text) or False,
        )
    finally:
        assert {p: p.read_bytes() for p in pilot.rglob("*") if p.is_file()} == before
        assert {p: p.read_bytes() for p in ledger.glob("*.jsonl")} == durable


def test_missing_grading_is_refused_before_confirmation(tmp_path, pilot):
    shutil.rmtree(pilot / "grading")
    shown = []
    with pytest.raises(BenchmarkValidationError, match="grading"):
        attempt(tmp_path, pilot, shown)
    assert shown == []


def test_generator_cannot_stand_in_for_a_registered_panel_seat(tmp_path, pilot):
    inputs = pilot / "grading/inputs"
    path = inputs / "panel.yml"
    panel = yaml.safe_load(path.read_bytes())
    # The votes and bookings still agree on haiku, but the frozen panel assigns
    # the Anthropic seat to sonnet. Matching the provider is insufficient.
    panel["judges"]["haiku"]["role"] = "generator"
    panel["judges"]["sonnet"]["role"] = "panel"
    path.write_text(yaml.safe_dump(panel))
    manifest = read(inputs / "inputs.json")
    manifest["panel"] = hashlib.sha256(path.read_bytes()).hexdigest()
    write(inputs / "inputs.json", manifest)
    shown = []
    with pytest.raises(BenchmarkValidationError, match="unregistered pass-1"):
        attempt(tmp_path, pilot, shown)
    assert shown == []


@pytest.mark.parametrize("provider", ["google", "anthropic"])
def test_grading_only_provider_over_remaining_durable_cap(tmp_path, pilot, provider):
    from benchmarks.agentic.ledger import Ledger

    # Answering: $9.10 x 1.25 < $65. This provider only grades, with $0.001 left.
    ledger = Ledger.open(tmp_path / "ledger", run_id="earlier")
    try:
        unit = ledger.reserve(
            provider,
            model_id="earlier",
            kind="batch",
            bound=D("179.999"),
            input_bound=1,
            output_bound=1,
            job_id="earlier",
        )
        assert unit is not None
        ledger.settle(unit, outcome="ok", actual=D("179.999"), buckets=None)
    finally:
        ledger.close()
    shown = []
    with pytest.raises(BenchmarkValidationError, match=f"{provider}.*cap"):
        attempt(tmp_path, pilot, shown)
    assert shown == []


def test_acceptable_plan_shows_answering_and_all_grading_providers(tmp_path, pilot):
    shown = []
    with pytest.raises(BenchmarkValidationError, match="not confirmed"):
        attempt(tmp_path, pilot, shown)
    assert len(shown) == 1
    lines = shown[0].splitlines()
    assert len(lines) == 3
    assert "anthropic: 0 trajectories" in lines[0]
    assert "google: 0 trajectories" in lines[1]
    assert "openai: 91 trajectories" in lines[2]
    assert "answering $9.10 + grading <$0.01 for 91 answers" in lines[2]
    assert "estimate $9.11 (x 1.25 = $11.38)" in lines[2]
    assert all("grading <$0.01 for 91 answers" in line for line in lines)
    outcome = read(tmp_path / "full/run-summary.json")
    assert outcome["status"] == "aborted" and outcome["live_confirmation"] == shown[0]
    assert not (tmp_path / "full/transcripts").exists()


def test_combined_cost_exceeds_cap_when_answering_alone_fits(tmp_path, pilot):
    from benchmarks.agentic.ledger import Ledger

    ledger = Ledger.open(tmp_path / "ledger", run_id="earlier")
    try:
        unit = ledger.reserve(
            "openai",
            model_id="earlier",
            kind="batch",
            bound=D("53.62"),
            input_bound=1,
            output_bound=1,
            job_id="earlier",
        )
        assert unit is not None
        ledger.settle(unit, outcome="ok", actual=D("53.62"), buckets=None)
    finally:
        ledger.close()
    # $11.38 remains: answering $11.375 fits; combined $11.381825 does not.
    shown = []
    with pytest.raises(BenchmarkValidationError, match="openai.*11.381825.*cap"):
        attempt(tmp_path, pilot, shown)
    assert shown == []


def test_cost_equality_is_allowed_and_all_planned_answers_are_priced(pilot):
    from benchmarks.agentic.ledger import PROVIDER_CAPS
    from benchmarks.agentic.live import full_run_gate, provider_plans
    from benchmarks.grading.preflight import reference_grading_rates
    from tests.benchmarks.test_agentic_live import _jobs, _rate

    jobs = _jobs(**{"gpt-6.1-sol": 91, "claude-sonnet-5-5": 2, "scripted-google": 3})
    rates = {
        "gpt-6.1-sol": _rate("gpt-6.1-sol", "openai", "0.10"),
        "claude-sonnet-5-5": _rate("claude-sonnet-5-5", "anthropic", "0.20"),
    }
    grading_rates = reference_grading_rates(pilot)
    # 96 answers are graded by every judge; Google answers none of the live cells.
    plans = provider_plans(jobs, {}, PROVIDER_CAPS, rates, grading_rates)
    google = next(p for p in plans if p.provider == "google")
    assert google.trajectories == 0 and google.grading_answers == 96
    assert google.estimate == D("0.005760")
    caps = PROVIDER_CAPS | {"google": D("0.007200")}
    assert full_run_gate(provider_plans(jobs, {}, caps, rates, grading_rates), jobs, rates) == []
    caps["google"] -= D("0.000001")
    assert any(
        "google" in p
        for p in full_run_gate(provider_plans(jobs, {}, caps, rates, grading_rates), jobs, rates)
    )


def test_per_model_time_gate_still_refuses_before_confirmation(tmp_path, pilot):
    path = next((pilot / "transcripts").glob("*/*.json"))
    row = read(path)
    row["timing"]["wall_ms"] = 1_000_000_000
    write(path, row)
    bind_fixture_transcripts(pilot)
    shown = []
    with pytest.raises(BenchmarkValidationError, match="gpt-6.1-sol.*24-hour"):
        attempt(tmp_path, pilot, shown)
    assert shown == []


@pytest.mark.parametrize(
    "damage",
    [
        "scripted",
        "mixed",
        "relabelled",
        "pending",
        "no-votes",
        "no-runs",
        "dry",
        "aborted",
        "not-run",
        "no-events",
        "unsettled",
        "missing-provider",
        "average",
        "zero",
        "negative",
        "nan",
        "infinity",
        "boolean",
        "denominator",
        "fractional-count",
        "transplanted-answer",
        "transplanted-vote",
        "unit",
        "model",
        "usage",
        "empty-usage",
        "missing-report",
        "scripted-report",
        "balance",
        "grade-only-dollars",
        "grade-only-actual",
        "grade-only-item",
        "grade-only-ledger-total",
    ],
)
def test_unusable_grading_never_reaches_confirmation(tmp_path, pilot, scripted_pilot, damage):
    if damage == "scripted":
        pilot = shutil.copytree(scripted_pilot, tmp_path / "scripted-run")
    grading = pilot / "grading"
    summary_path = grading / "summary.json"
    summary = read(summary_path)
    invocation_path = grading / "runs/pilot-grades.json"
    invocation = read(invocation_path)
    events_path = grading / "ledger-events.jsonl"
    events = [json.loads(line) for line in events_path.read_text().splitlines()]
    vote_path = next((grading / "votes/answers/flash-lite").rglob("*.json"))
    vote = read(vote_path)
    if damage == "mixed":
        path = grading / "inputs/panel.yml"
        panel = yaml.safe_load(path.read_text())
        panel["judges"]["flash-lite"]["api"] = "scripted"
        path.write_text(yaml.safe_dump(panel))
        manifest_path = grading / "inputs/inputs.json"
        manifest = read(manifest_path)
        manifest["panel"] = hashlib.sha256(path.read_bytes()).hexdigest()
        write(manifest_path, manifest)
    elif damage == "relabelled":
        vote["provider_batch_id"] = "scripted-batch-1"
    elif damage == "pending":
        vote.update(status="missing", withheld="missing", terminal=False, vote=None)
    elif damage == "no-votes":
        shutil.rmtree(grading / "votes")
    elif damage == "no-runs":
        shutil.rmtree(grading / "runs")
    elif damage in ("dry", "aborted", "not-run"):
        invocation.update(
            {"dry_run": True}
            if damage == "dry"
            else {"status": "aborted"}
            if damage == "aborted"
            else {"not_run": [{}]}
        )
    elif damage == "no-events":
        events = []
    elif damage == "unsettled":
        events = [e for e in events if not (e["event"] == "settle" and e["provider"] == "google")]
    elif damage == "missing-provider":
        for field in ("by_provider", "per_graded_answer"):
            summary["spend"][field].pop("google")
    elif damage in ("average", "zero", "negative", "nan", "infinity", "boolean"):
        summary["spend"]["per_graded_answer"]["google"] = {
            "average": "0.000001",
            "zero": "0",
            "negative": "-1",
            "nan": "NaN",
            "infinity": "Infinity",
            "boolean": True,
        }[damage]
    elif damage in ("denominator", "fractional-count"):
        summary["spend"]["graded_answers"] = 100 if damage == "denominator" else 1.0
    elif damage == "transplanted-answer":
        path = next((pilot / "transcripts").glob("*/*.json"))
        write(path, read(path) | {"answer": "An unrelated pilot's answer."})
    elif damage == "transplanted-vote":
        vote["input_sha256"] = "0" * 64
    elif damage == "unit":
        vote["unit_id"] = "another-pilot"
    elif damage == "model":
        vote["model_id"] = "another-model"
    elif damage == "usage":
        vote["buckets"]["output"] += 1
    elif damage == "empty-usage":
        vote["raw_usage"] = {"prompt_token_count": 0, "candidates_token_count": 0}
        vote["buckets"] = dict.fromkeys(vote["buckets"], 0)
    elif damage == "missing-report":
        vote["reported_model"] = None
    elif damage == "scripted-report":
        vote["reported_model"] = "scripted"
    elif damage == "balance":
        invocation["spent_after"]["google"] = "0"
    elif damage.startswith("grade-only-"):
        settlement = next(e for e in events if e["event"] == "settle" and e["provider"] == "google")
        if damage == "grade-only-dollars":
            summary["spend"]["by_provider"]["google"] = "0.000001"
        elif damage == "grade-only-actual":
            settlement["actual"] = "0"
        elif damage == "grade-only-item":
            settlement["items"][0]["item_id"] = "another-pilot"
        else:
            settlement["booked"] = "0.000001"
    write(summary_path, summary)
    if invocation_path.parent.exists():
        write(invocation_path, invocation)
    if vote_path.parent.exists():
        write(vote_path, vote)
    events_path.write_text("".join(json.dumps(e) + "\n" for e in events))
    shown = []
    with pytest.raises(BenchmarkValidationError, match="grading"):
        attempt(tmp_path, pilot, shown)
    assert shown == []


@pytest.mark.parametrize("artifact", ["summary", "ledger", "vote", "panel"])
def test_contaminated_grading_evidence_refuses_authorization(tmp_path, pilot, artifact):
    from benchmarks.agentic.protocol import KeyLeakError

    root = pilot / "grading"
    marker = "AIza" + "q" * 35
    escaped = marker.replace("A", r"\u0041", 1)
    if artifact == "panel":
        path = root / "inputs/panel.yml"
        path.write_text(path.read_text() + f'\nnotes: "{escaped}"\n')
    else:
        path = {
            "summary": root / "summary.json",
            "ledger": root / "ledger-events.jsonl",
            "vote": next((root / "votes").rglob("*.json")),
        }[artifact]
        # Normal parsing drops the first value of this duplicate metadata field.
        path.write_text(path.read_text().replace("{", f'{{"note":"{escaped}","note":null,', 1))
    shown = []
    with pytest.raises(KeyLeakError) as refusal:
        attempt(tmp_path, pilot, shown)
    assert marker not in str(refusal.value) and not shown


def legacy_grading(pilot):
    """Old honest writers omitted execution_results but retained stop/reply evidence."""
    path = pilot / "grading/runs/pilot-grades.json"
    invocation = read(path)
    invocation.pop("execution_results")
    write(path, invocation)


def test_older_honest_archive_keeps_its_financial_estimate(tmp_path, pilot):
    legacy_grading(pilot)
    assert reference_grading_rates(pilot) == dict.fromkeys(
        ("anthropic", "openai", "google"), D("0.000060")
    )
    shown = []
    with pytest.raises(BenchmarkValidationError, match="not confirmed"):
        attempt(tmp_path, pilot, shown)
    assert len(shown) == 1


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("batch_id", ["", "   ", "scripted-foreign-batch"])
def test_empty_or_scripted_batch_identity_cannot_authorize(tmp_path, pilot, legacy, batch_id):
    if legacy:
        legacy_grading(pilot)
    path = next((pilot / "grading/votes/answers/flash-lite").rglob("*.json"))
    write(path, read(path) | {"provider_batch_id": batch_id})
    shown = []
    error = "vote lacks matching live batch execution" if legacy else "receipt identity differs"
    with pytest.raises(BenchmarkValidationError, match=error):
        attempt(tmp_path, pilot, shown)
    assert shown == []


@pytest.mark.parametrize("legacy", [False, True])
def test_foreign_invocation_filename_cannot_downgrade_receipt_checks(tmp_path, pilot, legacy):
    if legacy:
        legacy_grading(pilot)
    path = pilot / "grading/runs/pilot-grades.json"
    path.rename(path.with_name("foreign-invocation.json"))
    shown = []
    with pytest.raises(BenchmarkValidationError, match="grading invocation identity differs"):
        attempt(tmp_path, pilot, shown)
    assert shown == []


@pytest.mark.parametrize(
    "field", ["unit_id", "grading_run_id", "item_id", "judge_id", "model_id", "provider_batch_id"]
)
@pytest.mark.parametrize("identity", ["", "foreign-pilot"])
def test_empty_or_foreign_vote_identity_cannot_authorize(tmp_path, pilot, field, identity):
    path = next((pilot / "grading/votes/answers/flash-lite").rglob("*.json"))
    write(path, read(path) | {field: identity})
    shown = []
    with pytest.raises(BenchmarkValidationError, match="grading"):
        attempt(tmp_path, pilot, shown)
    assert shown == []


@pytest.mark.parametrize("unit_id", ["", "   "])
def test_empty_legacy_ledger_identity_cannot_authorize(tmp_path, pilot, unit_id):
    legacy_grading(pilot)
    root = pilot / "grading"
    path = next((root / "votes/answers/flash-lite").rglob("*.json"))
    vote = read(path)
    old = vote["unit_id"]
    write(path, vote | {"unit_id": unit_id})
    events_path = root / "ledger-events.jsonl"
    events = [json.loads(line) for line in events_path.read_text().splitlines()]
    for event in events:
        if event.get("unit_id") == old:
            event["unit_id"] = unit_id
    events_path.write_text("".join(json.dumps(e) + "\n" for e in events))
    invocation_path = root / "runs/pilot-grades.json"
    invocation = read(invocation_path)
    invocation["units"] = [unit_id if u == old else u for u in invocation["units"]]
    write(invocation_path, invocation)
    shown = []
    with pytest.raises(BenchmarkValidationError, match="invalid grading ledger identity"):
        attempt(tmp_path, pilot, shown)
    assert shown == []


def test_normal_receipts_without_a_final_grade_cannot_authorize(tmp_path):
    pilot = make_pilot(tmp_path, live=True, no_grade=True)
    summary = read(pilot / "grading/summary.json")
    assert summary["graded_cells"] == 0 and len(summary["pending_cells"]) == 1
    assert read(pilot / "grading/runs/pilot-grades.json")["records_written"] == 3
    # Intact successful SDK replies do not settle a suspect answer without the
    # reference grader. This reaches the real grade check, not a damaged receipt.
    shown = []
    with pytest.raises(BenchmarkValidationError, match="grading awaits the reference"):
        attempt(tmp_path, pilot, shown)
    assert shown == []
