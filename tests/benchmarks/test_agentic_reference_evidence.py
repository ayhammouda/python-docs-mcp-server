"""Reference evidence is scanned before diagnostics and proves the executed client.

All authorization runs stop before launching; adapter traffic uses mock transports.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import date
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
