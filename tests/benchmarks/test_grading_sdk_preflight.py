"""Normal production grading with synthetic HTTP, never evidence of live spending.

Only launcher/framing, authorization and provider I/O are test seams. The worker,
SDK adapters, host validation, durable ledger, collector, frozen inputs, scoring
and financial preflight run normally. No produced evidence is repaired in place.
"""

import json
import re
from datetime import date
from decimal import Decimal as D

import pytest
import yaml

from benchmarks.agentic import run as answering
from benchmarks.agentic.ledger import Ledger
from benchmarks.agentic.live import confirmation_text, reference_rates
from benchmarks.grading.panel import PANEL_PATH
from benchmarks.grading.preflight import reference_grading_rates
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks.grading_pilot_fixture import (
    NoLaunch,
    answer_mock_pilot,
    grade_mock_pilot,
    read,
)
from tests.benchmarks.test_agentic_run import COMMITTED_PRICES, _live_config
from tests.benchmarks.test_grading_run import config


@pytest.fixture
def sdk_pilot(tmp_path, monkeypatch, capsys):
    root = answer_mock_pilot(tmp_path, monkeypatch)
    panel = yaml.safe_load(PANEL_PATH.read_text())
    for judge in panel["judges"].values():
        if judge["effort"] is None and judge["effort_levels"]:
            judge["effort"] = judge["effort_levels"][0]
    panel_path = tmp_path / "frozen-panel.yml"
    panel_path.write_text(yaml.safe_dump(panel))

    cfg = config(
        tmp_path,
        panel_path=panel_path,
        prices_path=COMMITTED_PRICES,
        grading_run_id="pilot-grades",
        live=True,
        confirm_max_spend=D("1"),
    )
    routers = grade_mock_pilot(cfg, monkeypatch)
    capsys.readouterr()
    return root, routers


def test_normal_sdk_grading_reconciles_intact_evidence_and_declines_full_run(
    sdk_pilot, tmp_path, monkeypatch
):
    root, routers = sdk_pilot
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    # Independently calculated from the committed prices and fixture token usage,
    # including cached inputs, Gemini thoughts, and the half-rate batch discount.
    expected = {"anthropic": D(".000135"), "openai": D(".0000535"), "google": D(".00021375")}
    summary = read(root / "grading/summary.json")
    assert summary["graded_cells"] == 1 and summary["pending_cells"] == []
    assert summary["tier"] != "Validated"
    assert {p: D(v) for p, v in summary["spend"]["by_provider"].items()} == expected
    assert reference_grading_rates(root) == expected
    ledger = Ledger.open(tmp_path / "ledger", run_id="inspection")
    try:
        for provider, amount in expected.items():
            # The answering fixture uses this same ledger; include its OpenAI call.
            answer = (
                reference_rates(root)["gpt-6.1-sol"].usd_per_cell if provider == "openai" else D(0)
            )
            assert ledger.spent(provider) == amount + answer
    finally:
        ledger.close()

    assert {p: len(r.seen) for p, r in routers.items()} == {
        "anthropic": 3,
        "openai": 4,
        "google": 5,
    }
    assert [r.url.path for r in routers["anthropic"].seen] == [
        "/v1/messages/batches",
        "/v1/messages/batches/msgbatch_1",
        "/v1/messages/batches/msgbatch_1/results",
    ]
    item = read(next((root / "grading/items/answers").glob("*.json")))
    anthropic_request = json.loads(routers["anthropic"].seen[0].content)["requests"][0]
    openai_request = json.loads(
        re.search(rb'\{"custom_id".*\}', routers["openai"].seen[0].content)[0]
    )
    google_request = json.loads(routers["google"].seen[1].content)
    assert anthropic_request["params"]["messages"][0]["content"] == item["user"]
    assert openai_request["body"]["input"][0]["content"] == item["user"]
    assert google_request["request"]["contents"][0]["parts"][0]["text"] == item["user"]

    # Capture exact plans while retaining the production gate and prompt renderer.
    captured = []
    durable = {p: p.read_bytes() for p in (tmp_path / "ledger").glob("*.jsonl")}
    requests = {p: [r.content for r in router.seen] for p, router in routers.items()}

    def capture(plans):
        captured.extend(plans)
        return confirmation_text(plans)

    monkeypatch.setattr(answering, "confirmation_text", capture)
    monkeypatch.delenv("CI", raising=False)
    shown = []
    cfg = _live_config(
        tmp_path, out_dir=tmp_path / "full", models=("gpt-6.1-sol",), repeats=91, reference_run=root
    )
    with pytest.raises(BenchmarkValidationError, match="not confirmed"):
        answering.run_agentic(
            cfg,
            launcher=NoLaunch(),
            today=date(2026, 11, 4),
            confirm=lambda text: shown.append(text) or False,
        )
    assert len(shown) == 1 and len(captured) == 3
    assert shown[0] == confirmation_text(captured)
    assert "anthropic: 0 trajectories" in shown[0]
    assert "google: 0 trajectories" in shown[0]
    assert "openai: 91 trajectories" in shown[0]
    assert "grading $0.02 for 91 answers" in shown[0]
    answer_rate = reference_rates(root)["gpt-6.1-sol"].usd_per_cell
    for plan in captured:
        assert plan.grading_answers == 91
        assert plan.grading_estimate == 91 * expected[plan.provider]
        assert plan.estimate == plan.grading_estimate + (
            91 * answer_rate if plan.provider == "openai" else 0
        )
        assert plan.spent == expected[plan.provider] + (
            answer_rate if plan.provider == "openai" else 0
        )
    outcome = read(tmp_path / "full/run-summary.json")
    assert outcome["status"] == "aborted" and outcome["live_confirmation"] == shown[0]
    assert not (tmp_path / "full/transcripts").exists()
    assert {p: p.read_bytes() for p in (tmp_path / "ledger").glob("*.jsonl")} == durable
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
    assert {p: [r.content for r in router.seen] for p, router in routers.items()} == requests
