"""Reference evidence is scanned before diagnostics and proves the executed client.

All authorization runs stop before launching; adapter traffic uses mock transports.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
import yaml

from benchmarks.__main__ import main
from benchmarks.agentic import clients, worker
from benchmarks.agentic.ledger import PROVIDER_CAPS
from benchmarks.agentic.live import full_run_gate, provider_plans, reference_rates
from benchmarks.agentic.protocol import encode_frame
from benchmarks.agentic.run import run_agentic
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks import test_agentic_clients as adapters
from tests.benchmarks.test_agentic_live import (
    _bind_reference_transcripts,
    _jobs,
    _pilot_summary,
    _single_cell_reference,
    _write_record,
    _write_snapshot,
)
from tests.benchmarks.test_agentic_run import (
    COMMITTED_MATRIX,
    COMMITTED_PRICES,
    _cli_args,
    _config,
    _container,
    _live_config,
    _matrix_with,
)
from tests.benchmarks.test_agentic_sandbox import FAMILIES, SYSTEMS

KEY = "AIza" + "g" * 35


def _record(run: Path) -> Path:
    return next((run / "transcripts").glob("*/*.json"))


@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize(
    "source,encoding",
    [
        ("matrix", "raw"),
        ("matrix", "escaped"),
        ("matrix", "merge"),
        ("matrix", "alias"),
        ("summary", "raw"),
        ("summary", "escaped"),
        ("summary", "duplicate"),
        ("transcript", "raw"),
        ("transcript", "escaped"),
        ("transcript", "duplicate"),
        ("discarded", "escaped"),
        ("ledger", "raw"),
        ("ledger", "escaped"),
        ("ledger", "duplicate"),
    ],
)
def test_cli_scans_all_reference_inputs_before_diagnostics_or_authorization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    live: bool,
    source: str,
    encoding: str,
) -> None:
    pilot = tmp_path / "pilot"
    _single_cell_reference(pilot, "gpt-6.1-sol", "openai-responses")
    encoded = KEY.replace("A", r"\u0041", 1)
    if source == "matrix":
        path = pilot / "snapshots/model-matrix.yml"
        text = path.read_text()
        if encoding in {"merge", "alias"}:
            text = (
                f'defaults: &base {{api: "{encoded}"}}\n'
                + ("unused: *base\n" if encoding == "alias" else "")
                + text.replace("- api: openai-responses", "- <<: *base\n  api: openai-responses", 1)
            )
        else:
            text = text.replace(
                "api: openai-responses", f'api: "{KEY if encoding == "raw" else encoded}"', 1
            )
    else:
        path = {
            "summary": pilot / "run-summary.json",
            "transcript": _record(pilot),
            "discarded": pilot / "discarded/no-docs--gpt-6.1-sol--r1/Q1--attempt1.json",
            "ledger": pilot / "ledger-events.jsonl",
        }[source]
        text = _record(pilot).read_text() if source == "discarded" else path.read_text()
        marker = KEY if encoding == "raw" else encoded
        # The contaminated first value is lost by ordinary JSON duplicate-key decoding.
        injected = f'"hidden": "{marker}", ' + (
            '"hidden": "safe", ' if encoding == "duplicate" else ""
        )
        text = text.replace("{", "{" + injected, 1)
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    launcher, docker = _container(tmp_path)
    monkeypatch.setattr("benchmarks.__main__._container_launcher", lambda *_: launcher)
    monkeypatch.setattr("sys.stdin", Mock(isatty=lambda: pytest.fail("prompted")))
    monkeypatch.delenv("CI", raising=False)
    args = [
        *_cli_args(tmp_path),
        "--launcher",
        "container",
        "--image",
        "fake",
        "--models",
        "gpt-6.1-sol",
        "--systems",
        "no-docs",
        "--matrix",
        str(COMMITTED_MATRIX),
        "--prices",
        str(COMMITTED_PRICES),
        "--reference-run",
        str(pilot),
    ]
    with pytest.raises(SystemExit) as stopped:
        main([*args, "--live" if live else "--dry-run"])
    output = capsys.readouterr()
    assert stopped.value.code == 3
    assert "key leak" in output.err and KEY not in output.err and not output.out
    assert docker.calls == []
    for artifact in (tmp_path / "run").rglob("*"):
        if artifact.is_file():
            assert KEY.encode() not in artifact.read_bytes()


@pytest.mark.parametrize("kind", ["transcripts", "discarded"])
@pytest.mark.parametrize(
    "identity", [None, {}, {"api": "scripted"}, {"provider": "google"}, {"model_id": "other"}]
)
def test_unproven_or_mismatched_execution_never_reaches_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, identity: dict[str, str] | None
) -> None:
    pilot = tmp_path / "pilot"
    _single_cell_reference(pilot, "gpt-6.1-sol", "openai-responses")
    path = _record(pilot)
    record = json.loads(path.read_text())
    if identity is None:
        record.pop("execution")  # Legacy evidence cannot establish the API.
    elif not identity:
        record["execution"] = {}
    else:
        record["execution"].update(identity)
    if kind == "discarded":
        path = pilot / "discarded" / path.parent.name / "Q1--attempt1.json"
        path.parent.mkdir(parents=True)
        summary_path = pilot / "run-summary.json"
        summary = json.loads(summary_path.read_text())
        summary["discarded"] = [{"cell": path.parent.name, "corpus_id": "Q1", "attempt": 1}]
        summary_path.write_text(json.dumps(summary))
    path.write_text(json.dumps(record))
    _bind_reference_transcripts(pilot)
    launcher, docker = _container(tmp_path)
    monkeypatch.delenv("CI", raising=False)
    with pytest.raises(BenchmarkValidationError, match="reference"):
        run_agentic(
            _live_config(tmp_path, models=("gpt-6.1-sol",), repeats=91, reference_run=pilot),
            launcher=launcher,
            today=date(2026, 11, 4),
            confirm=lambda _: pytest.fail("unproven reference reached confirmation"),
        )
    assert docker.calls == []


def test_scripted_response_cannot_claim_live_execution(tmp_path: Path) -> None:
    _single_cell_reference(tmp_path, "gpt-6.1-sol", "openai-responses")
    path = _record(tmp_path)
    record = json.loads(path.read_text())
    record["model_calls"][0]["raw_response"] = {"scripted_step": {"text": "ok"}}
    path.write_text(json.dumps(record))
    _bind_reference_transcripts(tmp_path)
    with pytest.raises(BenchmarkValidationError, match="scripted"):
        reference_rates(tmp_path)


def test_actual_owner_r13_pilot_is_rejected_before_a_paid_estimate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = Path(__file__).parent / "fixtures/agentic/r13-scripted-live"
    pilot = shutil.copytree(fixture, tmp_path / "pilot")
    matrix = pilot / "snapshots/model-matrix.yml"
    prices = tmp_path / "prices.yml"
    # The owner used this same family and snapshot, refreshing prices only for authorization.
    from tests.benchmarks.test_agentic_run import FIX

    price_data = yaml.safe_load((FIX / "prices.yml").read_bytes())
    price_data["expires_on"] = "2026-11-30"
    prices.write_text(yaml.safe_dump(price_data))
    launcher, docker = _container(tmp_path)
    monkeypatch.delenv("CI", raising=False)
    with pytest.raises(BenchmarkValidationError, match="reference"):
        run_agentic(
            _config(
                tmp_path,
                matrix_path=matrix,
                prices_path=prices,
                models=("scripted-anthropic",),
                systems=("no-docs",),
                questions=("SYN-EX-001",),
                repeats=91,
                reference_run=pilot,
            ),
            launcher=launcher,
            today=date(2026, 11, 4),
            confirm=lambda _: pytest.fail("owner's scripted pilot reached paid confirmation"),
        )
    assert docker.calls == []


def test_legitimate_reference_reaches_authorization_with_measured_estimate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pilot = tmp_path / "pilot"
    _single_cell_reference(pilot, "gpt-6.1-sol", "openai-responses")
    launcher, docker = _container(tmp_path)
    shown: list[str] = []
    monkeypatch.delenv("CI", raising=False)
    # This fixture measures answering. The grading reader is exercised with full pilot artifacts
    # in test_agentic_full_budget; use a known separate judge cost here.
    monkeypatch.setattr(
        "benchmarks.grading.preflight.reference_grading_rates",
        lambda _: {"google": Decimal("0.01")},
    )
    with pytest.raises(BenchmarkValidationError, match="not confirmed"):
        run_agentic(
            _live_config(tmp_path, models=("gpt-6.1-sol",), repeats=91, reference_run=pilot),
            launcher=launcher,
            today=date(2026, 11, 4),
            confirm=lambda text: bool(shown.append(text)),
        )
    assert len(shown) == 1 and "91 trajectories" in shown[0] and "estimate $9.10" in shown[0]
    assert "x 1.25 = $11.38" in shown[0] and "$65" in shown[0]
    assert docker.calls == []


@pytest.mark.parametrize("wall_ms,allowed", [(3000, True), (1_000_000, False)])
def test_proven_local_reference_preserves_unmetered_timing_gate(
    tmp_path: Path, wall_ms: float, allowed: bool
) -> None:
    family = FAMILIES["gemma4-12b"]
    _write_snapshot(tmp_path, family.raw, FAMILIES["gpt-6.1-sol"].raw)
    _write_record(
        tmp_path / "transcripts/no-docs--gemma4-12b--r1/Q1.json",
        family.id,
        "ollama",
        wall_ms,
        500,
        model_id=family.model_id,
        api="openai-chat-completions",
    )
    _pilot_summary(tmp_path, "completed", 1, 1, "0")
    (tmp_path / "ledger-events.jsonl").write_bytes(b"")
    rates = reference_rates(tmp_path)
    assert rates[family.id].cells == 1 and rates[family.id].usd_per_cell == 0
    assert rates[family.id].seconds_per_cell == (wall_ms - 500) / 1000
    jobs = _jobs(**{family.id: 90})
    plans = provider_plans(jobs, {}, PROVIDER_CAPS, rates)
    assert plans[0].cap is None and plans[0].estimate is None
    problems = full_run_gate(plans, jobs, rates)
    assert (problems == []) == allowed
    if not allowed:
        assert "24-hour window" in problems[0]


def test_reference_keeps_benign_yaml_alias_and_override_behavior(tmp_path: Path) -> None:
    _single_cell_reference(tmp_path, "gpt-6.1-sol", "openai-responses")
    matrix = tmp_path / "snapshots/model-matrix.yml"
    matrix.write_text(
        "defaults: &base {api: scripted}\nunused: *base\n"
        + matrix.read_text().replace(
            "- api: openai-responses", "- <<: *base\n  api: openai-responses", 1
        )
    )
    assert reference_rates(tmp_path)["gpt-6.1-sol"].api == "openai-responses"


@pytest.mark.parametrize(
    "family_id",
    ["claude-sonnet-5-5", "gpt-6.1-sol", "gemini-3.8-flash", "gemma4-12b", "scripted-openai"],
)
async def test_worker_records_actual_client_identity_on_success_and_discard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, family_id: str
) -> None:
    family = FAMILIES[family_id]
    if family_id == "claude-sonnet-5-5":
        client = adapters._anthropic(adapters.Recorder(adapters._ok(adapters.ANTHROPIC_ANSWER)))
    elif family_id == "gpt-6.1-sol":
        client = adapters._openai(
            adapters.Recorder(adapters._ok(adapters._openai_response([adapters.OPENAI_MESSAGE])))
        )
    elif family_id == "gemini-3.8-flash":
        client = adapters._gemini(
            adapters.GeminiRecorder(
                adapters._gemini_ok(adapters._gemini_response([{"text": "ok"}]))
            )
        )
    elif family_id == "gemma4-12b":
        client = adapters._ollama(
            adapters.Recorder(
                adapters._ok(adapters._chat_completion({"role": "assistant", "content": "ok"}))
            )
        )
    else:
        client = clients.ScriptedClient(
            "openai",
            "scripted-2",
            [{"text": "ok", "usage": {"input_tokens": 10, "output_tokens": 1}}],
        )
    expected = {"provider": family.provider, "model_id": family.model_id, "api": family.api}
    monkeypatch.setattr(clients, "make_client", lambda *_args, **_kwargs: client)
    monkeypatch.setattr(clients, "fetch_model_limits", AsyncMock(return_value=None))
    # Plan a scripted client with a different model: evidence must describe the instance
    # that really ran, even when the caller has supplied an inconsistent family.
    planned = FAMILIES["scripted-openai"].raw | {"model_id": "planned-only"}
    job = {
        "type": "job",
        "family": planned,
        "system": SYSTEMS["no-docs"].raw,
        "caps": {},
        "trajectories": [
            {"corpus_id": "Q1", "repeat": 1, "prompt": "q"},
            {"corpus_id": "Q2", "repeat": 1, "prompt": "q"},
        ],
    }
    reader = asyncio.StreamReader()
    for reply in (
        {"type": "grant", "unit_id": "u1"},
        {"type": "ack"},
        {"type": "ack"},
        {"type": "stop"},
        {"type": "ack"},
    ):
        reader.feed_data(encode_frame(reply))
    reader.feed_eof()
    channel = Mock()
    assert await worker._run_job(channel, reader, first=job) == 0
    records = [
        call.args[0]
        for call in channel.send.call_args_list
        if call.args[0]["type"] in {"trajectory", "discarded"}
    ]
    assert [message["type"] for message in records] == ["trajectory", "discarded"]
    assert all(message["record"]["execution"] == expected for message in records)


def test_scripted_reruns_save_execution_in_every_attempt(tmp_path: Path) -> None:
    matrix = _matrix_with(
        tmp_path, {"scripted-openai": [{"error": {"kind": "rate_limited", "retry_after_s": 121}}]}
    )
    config = _config(
        tmp_path,
        matrix_path=matrix,
        models=("scripted-openai",),
        systems=("no-docs",),
        questions=("SYN-EX-001",),
        repeats=1,
    )
    summary = run_agentic(config)
    records = list((config.out_dir / "discarded").glob("*/*.json"))
    assert len(records) == len(summary["discarded"]) == 3
    for path in records:
        assert json.loads(path.read_text())["execution"] == {
            "provider": "openai",
            "model_id": "scripted-2",
            "api": "scripted",
        }


@pytest.mark.parametrize(
    "mutation",
    [
        "foreign_units",
        "foreign_run",
        "request_model",
        "missing_request_model",
        "duplicate_attempt",
        "missing_attempt",
        "missing_unit",
        "duplicate_reserve",
        "duplicate_settle",
        "dropped_unit",
        "orphan_unit",
        "wrong_job",
        "wrong_provider",
        "wrong_kind",
        "wrong_outcome",
        "wrong_actual",
        "wrong_buckets",
        "negative_bound",
        "negative_before",
        "negative_after",
        "nonfinite_balance",
        "legacy_run",
        "malformed_call",
        "missing_calls",
        "boolean_cells",
        "negative_cap",
        "negative_attempt_time",
        "contradictory_input_bound",
    ],
)
def test_unbound_or_inconsistent_units_cannot_authorize_full_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    pilot = tmp_path / "pilot"
    _single_cell_reference(pilot, "gpt-6.1-sol", "openai-responses")
    transcript = _record(pilot)
    record = json.loads(transcript.read_bytes())
    call = record["model_calls"][0]
    ledger_path = pilot / "ledger-events.jsonl"
    events = [json.loads(line) for line in ledger_path.read_bytes().splitlines()]
    summary_path = pilot / "run-summary.json"
    summary = json.loads(summary_path.read_bytes())
    if mutation == "foreign_units":
        for event in events:
            event["unit_id"] = "another-valid-pilot-unit"
    elif mutation == "foreign_run":
        for event in events:
            event["run_id"] = "another-pilot"
    elif mutation == "request_model":
        call["request_params"]["model"] = "other-model"
    elif mutation == "missing_request_model":
        del call["request_params"]["model"]
    elif mutation == "duplicate_attempt":
        call["attempts"] *= 2
    elif mutation == "missing_attempt":
        call["attempts"] = []
    elif mutation == "missing_unit":
        call["attempts"][0].pop("unit_id")
    elif mutation == "duplicate_reserve":
        events.insert(1, events[0].copy())
    elif mutation == "duplicate_settle":
        events.append(events[1].copy())
        summary["ledger"]["openai"]["spent_after"] = "4.20"
    elif mutation == "dropped_unit":
        events = []
        summary["ledger"]["openai"]["spent_after"] = "4"
    elif mutation == "orphan_unit":
        events.extend([event | {"unit_id": "orphan"} for event in events[:2]])
        summary["ledger"]["openai"]["spent_after"] = "4.20"
    elif mutation == "wrong_job":
        events[0]["job_id"] = "gpt-6.1-sol--python-docs"
    elif mutation == "wrong_provider":
        events[0]["provider"] = "google"
    elif mutation == "wrong_kind":
        events[0]["kind"] = "batch"
    elif mutation == "wrong_outcome":
        events[1]["outcome"] = "timeout"
    elif mutation == "wrong_actual":
        events[1]["actual"] = "0.001"
    elif mutation == "wrong_buckets":
        events[1]["buckets"]["output"] = -1
    elif mutation == "negative_bound":
        events[0]["bound"] = "-1"
    elif mutation == "negative_before":
        summary["ledger"]["openai"] = {"spent_before": "-1", "spent_after": "-0.90"}
    elif mutation == "negative_after":
        summary["ledger"]["openai"] = {"spent_before": "-0.20", "spent_after": "-0.10"}
    elif mutation == "nonfinite_balance":
        summary["ledger"]["openai"]["spent_before"] = "NaN"
    elif mutation == "legacy_run":
        summary.pop("run_id")
    elif mutation == "malformed_call":
        record["model_calls"] = [None]
    elif mutation == "missing_calls":
        record.pop("model_calls")
    elif mutation == "boolean_cells":
        summary["scored_cells"] = summary["planned_cells"] = True
    elif mutation == "negative_cap":
        summary["ledger"]["openai"]["cap"] = "-1"
    elif mutation == "negative_attempt_time":
        call["attempts"][0]["wall_ms"] = -1
    elif mutation == "contradictory_input_bound":
        call["input_bound"] = 1

    transcript.write_text(json.dumps(record))
    ledger_path.write_text("".join(json.dumps(event) + "\n" for event in events))
    summary_path.write_text(json.dumps(summary))
    _bind_reference_transcripts(pilot)
    launcher, docker = _container(tmp_path)
    monkeypatch.delenv("CI", raising=False)
    shown = []
    with pytest.raises(BenchmarkValidationError, match="reference"):
        run_agentic(
            _live_config(tmp_path, models=("gpt-6.1-sol",), repeats=91, reference_run=pilot),
            launcher=launcher,
            today=date(2026, 11, 4),
            confirm=lambda text: bool(shown.append(text)),
        )
    assert shown == [] and docker.calls == []


def _hosted_mock_pilot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, retries=False) -> Path:
    """Run the real worker, loop, collector and ledger; replace only host I/O and HTTP."""
    from benchmarks.agentic.types import Buckets

    tmp_path.mkdir(parents=True, exist_ok=True)
    responses = []
    if retries:
        responses = [
            adapters._ok(adapters.ERROR_BODY, 500, {"retry-after": "121"}),
            adapters._ok(adapters.ERROR_BODY, 429, {"retry-after": "0"}),
        ]
    transport = adapters.Recorder(
        *responses, adapters._ok(adapters._openai_response([adapters.OPENAI_MESSAGE]))
    )
    client = adapters._openai(transport)

    async def run_jobs(jobs, *, ledger, on_message, **kwargs):
        for job in jobs:

            async def request(self, message):
                if message["type"] == "reserve":
                    unit = await ledger.reserve(
                        **{k: v for k, v in message.items() if k != "type"}, job_id=job.job_id
                    )
                    return {"type": "grant", "unit_id": unit}
                if message["type"] == "settle":
                    await ledger.settle(
                        message["unit_id"],
                        outcome=message["outcome"],
                        buckets=Buckets(**message["buckets"]) if message["buckets"] else None,
                    )
                else:
                    await on_message(job, message)
                return {"type": "ack"}

            with monkeypatch.context() as patch:
                patch.setattr(worker._HostLedger, "request", request)
                patch.setattr(clients, "make_client", lambda *_args, **_kwargs: client)
                patch.setattr(clients, "fetch_model_limits", AsyncMock(return_value=None))
                first = {
                    "type": "job",
                    "family": job.family.raw,
                    "system": job.system.raw,
                    "caps": asdict(job.caps),
                    "trajectories": list(job.trajectories),
                }
                await worker._run_job(
                    Mock(), asyncio.StreamReader(), first=first, bootstrapped=True
                )

    launcher, _ = _container(tmp_path)
    with monkeypatch.context() as patch:
        patch.delenv("CI", raising=False)
        patch.setattr("benchmarks.agentic.run.run_jobs", run_jobs)
        summary = run_agentic(
            _live_config(tmp_path, models=("gpt-6.1-sol",), repeats=1, run_id=tmp_path.name),
            launcher=launcher,
            today=date(2026, 11, 4),
            confirm=lambda _: True,
        )
    assert summary["scored_cells"] == summary["planned_cells"] == 1
    assert len(transport.seen) == (3 if retries else 1)
    return tmp_path / "run"


@pytest.mark.parametrize("channel", ["summary_ledger", "transcripts", "attempt_id", "ledger_id"])
def test_two_valid_pilots_cannot_mix_identity_and_charges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, channel: str
) -> None:
    live = _hosted_mock_pilot(tmp_path / "hosted", monkeypatch)
    cheap_root = tmp_path / "scripted"
    cheap_root.mkdir()
    family = FAMILIES["gpt-6.1-sol"]
    alias = FAMILIES["scripted-openai"].raw | {"id": family.id, "model_id": family.model_id}
    matrix = _matrix_with(
        cheap_root,
        {family.id: [{"text": "ok", "usage": {"input_tokens": 1, "output_tokens": 1}}]},
        extra=[alias],
    )
    config = _config(
        cheap_root,
        matrix_path=matrix,
        prices_path=COMMITTED_PRICES,
        models=(family.id,),
        systems=("no-docs",),
        questions=("SYN-EX-001",),
        repeats=1,
        run_id="scripted",
    )
    run_agentic(config)
    cheap = config.out_dir
    assert reference_rates(live)[family.id].api == family.api
    assert reference_rates(cheap)[family.id].api == "scripted"
    assert (
        reference_rates(cheap)[family.id].usd_per_cell
        < reference_rates(live)[family.id].usd_per_cell
    )
    mixed = shutil.copytree(live, tmp_path / "mixed")
    if channel == "summary_ledger":
        for name in ("run-summary.json", "ledger-events.jsonl"):
            shutil.copyfile(cheap / name, mixed / name)
    elif channel == "transcripts":
        other = _hosted_mock_pilot(tmp_path / "other-hosted", monkeypatch)
        assert reference_rates(other)[family.id].api == family.api
        shutil.copyfile(_record(other), _record(mixed))
    else:
        path = _record(mixed)
        record = json.loads(path.read_bytes())
        cheap_record = json.loads(_record(cheap).read_bytes())
        unit = cheap_record["model_calls"][0]["attempts"][0]["unit_id"]
        if channel == "attempt_id":
            record["model_calls"][0]["attempts"][0]["unit_id"] = unit
            path.write_text(json.dumps(record))
        else:
            ledger = mixed / "ledger-events.jsonl"
            ledger.write_text(
                "".join(
                    json.dumps(json.loads(line) | {"unit_id": unit}) + "\n"
                    for line in ledger.read_bytes().splitlines()
                )
            )
    launcher, docker = _container(tmp_path)
    monkeypatch.delenv("CI", raising=False)
    shown = []
    with pytest.raises(BenchmarkValidationError, match="reference"):
        run_agentic(
            _live_config(tmp_path, models=(family.id,), repeats=91, reference_run=mixed),
            launcher=launcher,
            today=date(2026, 11, 4),
            confirm=lambda text: bool(shown.append(text)),
        )
    assert shown == [] and docker.calls == []


def test_produced_hosted_discard_retry_reference_keeps_all_charges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pilot = _hosted_mock_pilot(tmp_path / "hosted", monkeypatch, retries=True)
    summary = json.loads((pilot / "run-summary.json").read_bytes())
    assert len(summary["discarded"]) == 1
    events = [
        json.loads(line) for line in (pilot / "ledger-events.jsonl").read_bytes().splitlines()
    ]
    assert [e["outcome"] for e in events if e["event"] == "settle"] == [
        "server",
        "rate_limited",
        "ok",
    ]
    rate = reference_rates(pilot)["gpt-6.1-sol"]
    assert rate.cells == 1
    assert rate.usd_per_cell == Decimal(summary["ledger"]["openai"]["spent_after"])
    assert rate.usd_per_cell > Decimal(events[-1]["booked"])
    launcher, docker = _container(tmp_path)
    shown = []
    monkeypatch.delenv("CI", raising=False)
    # This fixture measures answering. The grading reader is exercised with full pilot artifacts
    # in test_agentic_full_budget; use a known separate judge cost here.
    monkeypatch.setattr(
        "benchmarks.grading.preflight.reference_grading_rates",
        lambda _: {"google": Decimal("0.01")},
    )
    with pytest.raises(BenchmarkValidationError, match="not confirmed"):
        run_agentic(
            _live_config(tmp_path, models=("gpt-6.1-sol",), repeats=1, reference_run=pilot),
            launcher=launcher,
            today=date(2026, 11, 4),
            confirm=lambda text: bool(shown.append(text)),
        )
    assert len(shown) == 1 and "estimate $" in shown[0] and docker.calls == []


@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("mutation", ["unit", "request_model", "malformed_call"])
def test_cli_refuses_invalid_reference_before_output_ledger_or_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    live: bool,
    mutation: str,
) -> None:
    pilot = tmp_path / "pilot"
    _single_cell_reference(pilot, "gpt-6.1-sol", "openai-responses")
    path = _record(pilot)
    record = json.loads(path.read_bytes())
    if mutation == "unit":
        record["model_calls"][0]["attempts"][0]["unit_id"] = "foreign"
    elif mutation == "request_model":
        record["model_calls"][0]["request_params"]["model"] = "other"
    else:
        record["model_calls"] = [None]
    path.write_text(json.dumps(record))
    _bind_reference_transcripts(pilot)
    launcher, docker = _container(tmp_path)
    monkeypatch.setattr("benchmarks.__main__._container_launcher", lambda *_: launcher)
    monkeypatch.setattr("sys.stdin", Mock(isatty=lambda: pytest.fail("prompted")))
    monkeypatch.delenv("CI", raising=False)
    with pytest.raises(SystemExit) as caught:
        main(
            [
                *_cli_args(tmp_path),
                "--launcher",
                "container",
                "--image",
                "fake",
                "--models",
                "gpt-6.1-sol",
                "--systems",
                "no-docs",
                "--repeats",
                "91",
                "--matrix",
                str(COMMITTED_MATRIX),
                "--prices",
                str(COMMITTED_PRICES),
                "--reference-run",
                str(pilot),
                "--live" if live else "--dry-run",
            ]
        )
    output = capsys.readouterr()
    assert caught.value.code == 2 and "reference" in output.err and not output.out
    assert docker.calls == []
    assert not (tmp_path / "run").exists() and not (tmp_path / "ledger").exists()


def test_reference_validates_each_scanned_source_buffer_without_reopening(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.agentic import inputs

    _single_cell_reference(tmp_path, "gpt-6.1-sol", "openai-responses")
    read = inputs.read_input_bytes
    counts: dict[Path, int] = {}

    def replace_after_capture(path, *args, **kwargs):
        counts[path] = counts.get(path, 0) + 1
        data = read(path, *args, **kwargs)
        # A second open sees a different identity and an unsafe value.
        path.write_text(json.dumps({"unit_id": "foreign", "credential": KEY}))
        return data

    monkeypatch.setattr(inputs, "read_input_bytes", replace_after_capture)
    rate = reference_rates(tmp_path)["gpt-6.1-sol"]
    assert rate.usd_per_cell == Decimal("0.10") and rate.api == "openai-responses"
    assert len(counts) == 4 and set(counts.values()) == {1}


def test_real_offline_pilot_retains_unmetered_reference_timing(
    tmp_path: Path,
) -> None:
    from tests.benchmarks.test_agentic_airgap import _counting, _local_config

    config = _local_config(tmp_path)
    checks, rounds = _counting()
    summary = run_agentic(config, offline_checks=checks)
    assert summary["status"] == "completed" and len(rounds) == 4
    assert (config.out_dir / "ledger-events.jsonl").read_bytes() == b""
    rate = reference_rates(config.out_dir)["scripted-local"]
    assert rate.cells == 2 and rate.usd_per_cell == 0 and rate.seconds_per_cell > 0


@pytest.mark.parametrize(
    "outcome,booked",
    [
        ("timeout", "0.20"),
        ("server", "0.20"),
        ("connection", "0.20"),
        ("no_usage", "0.20"),
        ("rejected", "0"),
        ("rate_limited", "0"),
    ],
)
def test_every_attempt_outcome_uses_ledger_booking_rules(
    tmp_path: Path, outcome: str, booked: str
) -> None:
    from tests.benchmarks.test_agentic_live import _settled_unit

    _single_cell_reference(tmp_path, "gpt-6.1-sol", "openai-responses")
    path = _record(tmp_path)
    record = json.loads(path.read_bytes())
    extra = {
        "request_params": {"model": "gpt-6.1-sol"},
        "attempts": [{"unit_id": "u2", "outcome": outcome}],
        "buckets": None,
        "raw_response": {} if outcome == "no_usage" else None,
    }
    record["model_calls"].append(extra)
    path.write_text(json.dumps(record))
    ledger_path = tmp_path / "ledger-events.jsonl"
    events = _settled_unit("u2", "gpt-6.1-sol", booked, outcome)
    events[0]["bound"] = "0.10"
    ledger_path.write_text(ledger_path.read_text() + "".join(json.dumps(e) + "\n" for e in events))
    summary_path = tmp_path / "run-summary.json"
    summary = json.loads(summary_path.read_bytes())
    summary["ledger"]["openai"]["spent_after"] = str(Decimal("4.10") + Decimal(booked))
    summary_path.write_text(json.dumps(summary))
    _bind_reference_transcripts(tmp_path)
    assert reference_rates(tmp_path)["gpt-6.1-sol"].usd_per_cell == Decimal("0.10") + Decimal(
        booked
    )
    # Keep the summary coherent with the forged booked total: only the unit rule catches it.
    events[-1]["booked"] = str(Decimal(booked) + Decimal("0.01"))
    ledger_path.write_text(
        ledger_path.read_text().splitlines()[0]
        + "\n"
        + ledger_path.read_text().splitlines()[1]
        + "\n"
        + "".join(json.dumps(e) + "\n" for e in events)
    )
    summary["ledger"]["openai"]["spent_after"] = str(Decimal("4.11") + Decimal(booked))
    summary_path.write_text(json.dumps(summary))
    with pytest.raises(BenchmarkValidationError, match="reference"):
        reference_rates(tmp_path)


def test_same_model_families_cannot_swap_billed_unit_ownership(tmp_path: Path) -> None:
    from tests.benchmarks.test_agentic_live import _settled_unit

    family = FAMILIES["gpt-6.1-sol"]
    _write_snapshot(tmp_path, *(family.raw | {"id": name} for name in ("first", "second")))
    events = []
    for name in ("first", "second"):
        _write_record(
            tmp_path / "transcripts" / f"no-docs--{name}--r1" / "Q1.json",
            name,
            "openai",
            200,
            100,
            model_id=family.model_id,
            unit_id=name,
        )
        events.extend(_settled_unit(name, name, "0.10", "ok", model_id=family.model_id))
    _pilot_summary(tmp_path, "completed", 2, 2, "0.20")
    ledger = tmp_path / "ledger-events.jsonl"
    ledger.write_text("".join(json.dumps(e) + "\n" for e in events))
    assert set(reference_rates(tmp_path)) == {"first", "second"}
    for event in events:
        event["unit_id"] = "second" if event["unit_id"] == "first" else "first"
    ledger.write_text("".join(json.dumps(e) + "\n" for e in events))
    with pytest.raises(BenchmarkValidationError, match="reference"):
        reference_rates(tmp_path)
