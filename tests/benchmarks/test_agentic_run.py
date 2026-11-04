"""agentic-run: plan, artifacts, run summary and the mocked end-to-end test (design §6-§12).

Every model is scripted and every worker is keyless: no test reaches a network host.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from benchmarks.__main__ import main
from benchmarks.adapters.guard import PROVIDER_API_KEY_ENV
from benchmarks.agentic.bridge import load_systems
from benchmarks.agentic.host import LocalLauncher
from benchmarks.agentic.ledger import Ledger
from benchmarks.agentic.loop import MAX_RETRIES, Caps
from benchmarks.agentic.protocol import KEY_SHAPED, KeyLeakError
from benchmarks.agentic.run import (
    OUTPUT_CAP_STOPS,
    AgenticConfig,
    _Collector,
    _until_signalled,
    cell_id,
    limits_mismatch,
    plan_jobs,
    run_agentic,
    tool_model_key,
)
from benchmarks.agentic.sandbox import ContainerLauncher, Sandbox, SandboxConfig
from benchmarks.agentic.types import Buckets
from benchmarks.model_matrix import load_model_matrix
from benchmarks.report import generate_report
from benchmarks.runner import BenchmarkValidationError, _load_questions, _load_yaml_mapping
from benchmarks.scoring import score_run
from tests.benchmarks.test_agentic_host import _alive
from tests.test_mcp_get_docs_cache_smoke import _create_contentful_json_index
from tests.test_stdio_smoke import _isolated_cache_env

REPO_ROOT = Path(__file__).resolve().parents[2]
FIX = Path(__file__).parent / "fixtures" / "agentic"
SAMPLE_CORPUS = Path(__file__).parent / "fixtures" / "corpus.sample.yml"
CORPUS_SCHEMA = REPO_ROOT / "docs" / "benchmarks" / "corpus.schema.json"
METHODOLOGY_PATH = REPO_ROOT / "docs" / "benchmarks" / "PUBLIC-BENCHMARK-METHODOLOGY.md"
COMMITTED_MATRIX = REPO_ROOT / "docs" / "benchmarks" / "model-matrix.yml"
COMMITTED_MANIFEST = REPO_ROOT / "docs" / "benchmarks" / "agentic-manifest.yml"
COMMITTED_PRICES = REPO_ROOT / "docs" / "benchmarks" / "price-snapshot.yml"
FAKE_SERVER = FIX / "fake_fetch_server.py"
TEMPLATE_MANIFEST = REPO_ROOT / "docs" / "benchmarks" / "competitor-manifest.template.yml"
# 3 systems x 2 questions x 2 repeats: python-docs and docs-fetch make 2 calls, no-docs 1,
# so 20 calls of 1,000 input tokens at $1/1M and 100 output tokens at $10/1M.
EXPECTED_ANTHROPIC_SPEND = Decimal("0.04")
EX1 = "fixture_symbol_ex_1()"  # a phrase only SYN-EX-001's prompt holds
EX2 = "fixture_symbol_ex_2()"


@pytest.fixture(autouse=True)
def _keyless(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in PROVIDER_API_KEY_ENV.values():
        monkeypatch.delenv(name, raising=False)


def _write_manifest(tmp_path: Path) -> Path:
    committed = {
        s.id: s.raw for s in load_systems(COMMITTED_MANIFEST)
    }  # python-docs and no-docs as committed
    fetch = {
        "id": "docs-fetch",
        "adapter": "mcp-stdio",
        "command": ["{python}", str(FAKE_SERVER)],
        "env": {},
        "pass_env": ["HOME"],
    }
    path = tmp_path / "manifest.yml"
    competitors = [committed["python-docs"], fetch, committed["no-docs"]]
    path.write_text(yaml.safe_dump({"competitors": competitors}), encoding="utf-8")
    return path


def _isolated_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    env, cache_dir = _isolated_cache_env(tmp_path)
    _create_contentful_json_index(cache_dir)
    monkeypatch.setenv("XDG_CACHE_HOME", env["XDG_CACHE_HOME"])
    monkeypatch.setenv("HOME", env["HOME"])
    return cache_dir


def _config(tmp_path: Path, **overrides: Any) -> AgenticConfig:
    values: dict[str, Any] = {
        "corpus_path": SAMPLE_CORPUS,
        "matrix_path": FIX / "model-matrix.yml",
        "manifest_path": COMMITTED_MANIFEST,
        "prices_path": FIX / "prices.yml",
        "ledger_dir": tmp_path / "ledger",
        "out_dir": tmp_path / "run",
        "questions": ("SYN-EX-001", "SYN-EX-002"),
        "repeats": 2,
        "seed": 7,
    }
    values.update(overrides)
    return AgenticConfig(**values)


def _matrix_with(tmp_path: Path, scripts: dict[str, list[dict[str, Any]]], extra=()) -> Path:
    """The fixture matrix with some families' scripts replaced, plus extra families."""
    data = yaml.safe_load((FIX / "model-matrix.yml").read_text(encoding="utf-8"))
    families = data["model_families"] + [dict(f) for f in extra]
    for family in families:
        if family["id"] in scripts:
            script = tmp_path / f"script-{family['id']}.yml"
            script.write_text(yaml.safe_dump({"steps": scripts[family["id"]]}), encoding="utf-8")
            family["script"] = str(script)
        else:
            family["script"] = str(FIX / family["script"])
    data["model_families"] = families
    path = tmp_path / "matrix.yml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_agentic_run_end_to_end_with_scripted_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_dir = _isolated_index(tmp_path, monkeypatch)
    manifest = _write_manifest(tmp_path)
    monkeypatch.chdir(tmp_path)  # the relative script paths must not depend on the cwd
    summary = run_agentic(_config(tmp_path, manifest_path=manifest))
    assert summary["planned_cells"] == 36 and summary["succeeded_cells"] == 36
    assert summary["failed_cells"] == 0 and summary["discarded"] == [] and summary["not_run"] == []
    assert summary["scored_cells"] == summary["correctness_denominator_cells"] == 36
    run = tmp_path / "run"

    def tools(system: str) -> list[str]:
        path = run / "transcripts" / f"{system}--scripted-anthropic--r1" / "SYN-EX-001.json"
        return [c["tool"] for c in json.loads(path.read_text())["tool_calls"]]

    assert (tools("python-docs"), tools("docs-fetch"), tools("no-docs")) == (
        ["search_docs"],
        ["fetch"],
        [],
    )
    transcript = json.loads(
        (run / "transcripts" / "no-docs--scripted-openai--r2" / "SYN-EX-002.json").read_text()
    )
    assert transcript["tool_model_key"] == "no-docs:openai/scripted-2"
    assert (transcript["competitor_id"], transcript["model_family_id"]) == (
        "no-docs--scripted-openai--r2",
        "scripted-openai",
    )
    info = json.loads((run / "systems" / "scripted-anthropic--python-docs.json").read_text())
    assert len(info["tools_raw"]) == 7
    assert info["index_sha256"] == hashlib.sha256((cache_dir / "index.db").read_bytes()).hexdigest()
    assert Decimal(summary["ledger"]["anthropic"]["spent_after"]) == EXPECTED_ANTHROPIC_SPEND
    assert Decimal(summary["ledger"]["anthropic"]["spent_before"]) == 0
    assert summary["price_snapshot"] == {"captured_on": "2026-10-31", "expires_on": None}
    assert summary["mode"] == "agentic" and summary["seed"] == 7 and summary["repeats"] == 2
    env = json.loads((run / "environment.json").read_text())
    assert env["mode"] == "agentic" and set(env["client_versions"]) >= {"anthropic", "mcp"}
    for name in ("corpus.yml", "competitor-manifest.yml", "model-matrix.yml"):
        assert (run / "snapshots" / name).is_file()
    assert (run / "snapshots" / "competitor-manifest.yml").read_bytes() == manifest.read_bytes()
    assert (run / "snapshots" / "system-prompt.txt").read_text().startswith("You answer")
    assert (
        len((run / "ledger-events.jsonl").read_text().splitlines()) == 3 * 2 * 20
    )  # reserve+settle
    assert len(list((run / "logs").glob("*.stderr.log"))) == 9
    tokens = json.loads(
        (run / "tokens" / "no-docs--scripted-openai--r2" / "SYN-EX-002.json").read_text()
    )
    assert tokens["status"] == "placeholder" and tokens["count_model"] == "claude-sonnet-5-5"
    assert score_run(run, corpus_path=SAMPLE_CORPUS, schema_path=CORPUS_SCHEMA)["total_cells"] == 36
    report = generate_report(
        run, model_matrix_path=FIX / "model-matrix.yml", methodology_path=METHODOLOGY_PATH
    ).read_text()
    assert "scripted-1" in report
    for path in run.rglob("*"):
        if path.is_file():
            assert KEY_SHAPED.search(path.read_text(errors="replace")) is None


def test_plan_jobs_makes_script_paths_absolute_and_orders_each_job_by_seed(
    tmp_path: Path,
) -> None:
    corpus = _load_yaml_mapping(SAMPLE_CORPUS, "corpus")
    questions = [q for q in _load_questions(corpus) if q.id in ("SYN-EX-001", "SYN-EX-002")]
    families = load_model_matrix(FIX / "model-matrix.yml").model_families
    systems = load_systems(COMMITTED_MANIFEST)
    jobs = plan_jobs(questions, families, systems, repeats=2, seed=7, matrix_dir=FIX)
    assert len(jobs) == 9 and all(Path(j.family.raw["script"]).is_absolute() for j in jobs)
    assert jobs[0].job_id == "scripted-anthropic--python-docs"
    for job in jobs:
        canonical = [(q.id, r) for q in questions for r in (1, 2)]
        random.Random(f"7/{job.family.id}/{job.system.id}").shuffle(canonical)
        assert [(t["corpus_id"], t["repeat"]) for t in job.trajectories] == canonical
    again = plan_jobs(questions, families, systems, repeats=2, seed=7, matrix_dir=FIX)
    assert again == jobs


def test_cell_and_tool_model_keys() -> None:
    assert cell_id("no-docs", "gpt-6.1-sol", 3) == "no-docs--gpt-6.1-sol--r3"
    assert tool_model_key("no-docs", "openai", "gpt-6.1-sol") == "no-docs:openai/gpt-6.1-sol"
    assert OUTPUT_CAP_STOPS == {"max_tokens", "max_output_tokens", "MAX_TOKENS", "CONTINUATION"}


@pytest.mark.parametrize("kind", ["discarded", "trajectory"])
async def test_forged_report_cannot_write_outside_run(tmp_path: Path, kind: str) -> None:
    question = _load_questions(_load_yaml_mapping(SAMPLE_CORPUS, "corpus"))[0]
    family = load_model_matrix(FIX / "model-matrix.yml").model_families[0]
    system = load_systems(COMMITTED_MANIFEST)[0]
    (job,) = plan_jobs([question], [family], [system], repeats=1, seed=7, matrix_dir=FIX)
    collector = _Collector(tmp_path / "run", {question.id: question}, [family])
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / ("proof--attempt1.json" if kind == "discarded" else f"{question.id}.json")
    canary.write_text("untouched")
    record = {
        "status": "discarded" if kind == "discarded" else "succeeded",
        "error": None,
        "answer": "ok",
        "model_calls": [],
        "started_at": "2026-11-04T00:00:00Z",
        "completed_at": "2026-11-04T00:00:01Z",
        "timing": {
            f"{k}_ms": 0 for k in ("grant_wait", "failed_attempt", "retry_wait", "used", "overhead")
        },
    }
    report = {
        "type": kind,
        "reason": "budget_stop",
        "record": record,
        "attempt": 1,
        "corpus_id": str(outside / "proof") if kind == "discarded" else question.id,
        "repeat": 1 if kind == "discarded" else "/../../../outside",
    }
    try:
        with pytest.raises(BenchmarkValidationError):
            await collector.on_message(job, report)
    finally:
        assert canary.read_text() == "untouched"
    assert collector.families[family.id].first_started_at is None
    assert not collector.out.exists()

    await collector.on_message(job, {**report, "corpus_id": question.id, "repeat": 1, "attempt": 3})
    cell = cell_id(system.id, family.id, 1)
    path = (
        collector.out / "discarded" / cell / f"{question.id}--attempt3.json"
        if kind == "discarded"
        else collector.out / "transcripts" / cell / f"{question.id}.json"
    )
    stored = json.loads(path.read_text())
    assert (stored["corpus_id"], stored["repeat"]) == (question.id, 1)


def test_agentic_run_budget_stop_discards_and_lists_cells_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_index(tmp_path, monkeypatch)
    manifest = _write_manifest(tmp_path)
    caps = {"anthropic": Decimal("0"), "openai": Decimal(65), "google": Decimal(180)}
    summary = run_agentic(_config(tmp_path, manifest_path=manifest), caps=caps)
    run = tmp_path / "run"
    assert len(summary["discarded"]) + len(summary["not_run"]) == 12
    assert {d["reason"] for d in summary["discarded"] + summary["not_run"]} == {"budget_stop"}
    assert summary["succeeded_cells"] == 24 and summary["planned_cells"] == 36
    assert not any((run / "scoring").glob("*--scripted-anthropic--*"))
    assert len(list((run / "discarded").rglob("*--attempt1.json"))) == len(summary["discarded"])
    assert summary["ledger"]["anthropic"]["stopped"] == "cap"


def test_a_live_run_refuses_an_expired_price_snapshot(tmp_path: Path) -> None:
    class EveryFamily(LocalLauncher):
        def supports(self, family: Any) -> bool:
            return True

        def command(self, job: Any) -> Any:
            raise AssertionError("no process may start")

    config = _config(
        tmp_path,
        matrix_path=COMMITTED_MATRIX,
        prices_path=COMMITTED_PRICES,
        models=("claude-sonnet-5-5",),
    )
    with pytest.raises(BenchmarkValidationError, match="2026-12-31"):
        run_agentic(config, launcher=EveryFamily(), today=date(2026, 12, 31))
    assert not (tmp_path / "ledger").exists()


class _EveryFamily(LocalLauncher):
    def supports(self, family: Any) -> bool:
        return True

    def command(self, job: Any) -> Any:
        raise AssertionError("no process may start")


def test_an_air_gapped_dry_run_ignores_the_price_snapshot_expiry(tmp_path: Path) -> None:
    def config(models: tuple[str, ...], out: str) -> AgenticConfig:
        return _config(
            tmp_path,
            matrix_path=COMMITTED_MATRIX,
            prices_path=COMMITTED_PRICES,
            models=models,
            out_dir=tmp_path / out,
            dry_run=True,
        )

    late = date(2027, 1, 2)
    summary = run_agentic(config(("gemma4-12b",), "local"), launcher=_EveryFamily(), today=late)
    assert summary["dry_run"] is True and summary["models"] == ["gemma4-12b"]
    with pytest.raises(BenchmarkValidationError, match="expires"):
        run_agentic(config(("claude-sonnet-5-5",), "hosted"), launcher=_EveryFamily(), today=late)


def test_every_reserve_event_names_its_job(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        models=("scripted-anthropic", "scripted-openai"),
        systems=("no-docs",),
        questions=("SYN-EX-001",),
        repeats=1,
    )
    summary = run_agentic(config)
    assert summary["status"] == "completed"
    providers = {"scripted-anthropic": "anthropic", "scripted-openai": "openai"}
    lines = (tmp_path / "run" / "ledger-events.jsonl").read_text().splitlines()
    reserves = [e for e in map(json.loads, lines) if e["event"] == "reserve"]
    assert {e["job_id"] for e in reserves} == {f"{f}--no-docs" for f in providers}
    for event in reserves:
        family_id, system_id = event["job_id"].split("--")
        assert system_id == "no-docs" and providers[family_id] == event["provider"]


def test_limits_mismatch_reports_each_difference() -> None:
    family = {f.id: f for f in load_model_matrix(COMMITTED_MATRIX).model_families}[
        "claude-sonnet-5-5"
    ]
    assert limits_mismatch(None, family) == []
    assert limits_mismatch({"max_input_tokens": 1_000_000, "max_tokens": 128_000}, family) == []
    assert limits_mismatch({"max_input_tokens": 999, "max_tokens": 1_024}, family) == [
        "max_input_tokens 999 != max_input_tokens 1000000 in the model matrix",
        "max_tokens 1024 < max_output_tokens 32768 in the model matrix",
    ]
    assert limits_mismatch({"error": "APIConnectionError"}, family) == [
        "model limits could not be read (APIConnectionError)"
    ]


class _ReportsLimits(LocalLauncher):
    """A worker that reports model limits in its system_info, then finishes."""

    def command(self, job: Any) -> tuple[list[str], dict[str, str]]:
        _, env = super().command(job)
        info = {"type": "system_info", "model_limits": {"max_input_tokens": 999}}
        code = (
            "import json, sys\n"
            "inp, out = sys.stdin.buffer, sys.stdout.buffer\n"
            "inp.read(int.from_bytes(inp.read(8), 'big'))\n"
            f"for frame in ({info!r}, {{'type': 'done'}}):\n"
            "    body = json.dumps(frame).encode()\n"
            "    out.write(len(body).to_bytes(8, 'big') + body)\n"
            "    out.flush()\n"
        )
        return [sys.executable, "-c", code], env


def test_a_mismatch_reaches_the_run_summary(tmp_path: Path) -> None:
    summary = run_agentic(_scripted_config(tmp_path), launcher=_ReportsLimits())
    assert summary["model_limits_mismatch"] == [
        "scripted-anthropic--no-docs: max_input_tokens 999 != max_input_tokens 200000 in the "
        "model matrix"
    ]
    written = json.loads((tmp_path / "run" / "run-summary.json").read_text())
    assert written["model_limits_mismatch"] == summary["model_limits_mismatch"]


def test_a_failed_probe_run_still_writes_fail(tmp_path: Path) -> None:
    from tests.benchmarks.test_agentic_sandbox import FakeDocker, Probes, _verified

    config = _config(
        tmp_path,
        models=("scripted-anthropic",),
        systems=("no-docs", "python-docs"),
        questions=("SYN-EX-001",),
        repeats=1,
    )
    sandbox = Sandbox(
        SandboxConfig(
            image="sha256:" + "ab" * 32,
            run_id="r1",
            out_dir=config.out_dir,
            index_path=tmp_path / "index.db",
            keys_file=None,
            repo_commit="c" * 40,
        ),
        docker=FakeDocker(),
        verify=_verified,
    )
    sandbox.config.index_path.touch()
    probes = Probes(fail_on="scripted-anthropic--python-docs")
    launcher = ContainerLauncher(sandbox, probe=probes, control=probes.control)
    with pytest.raises(BenchmarkValidationError, match="isolation probe failed"):
        run_agentic(config, launcher=launcher)
    summary = json.loads((tmp_path / "run" / "egress" / "summary.json").read_text())
    assert summary["isolation_probe"] == "fail"
    assert set(summary["jobs"]) == {
        "scripted-anthropic--no-docs",
        "scripted-anthropic--python-docs",
    }
    assert json.loads((tmp_path / "run" / "run-summary.json").read_text())["status"] == "aborted"


def test_run_summary_counts_retries_and_reruns(tmp_path: Path) -> None:
    limited = {"error": {"kind": "rate_limited", "status": 429, "retry_after_s": 0}}
    answer = {"text": "ok", "usage": {"input_tokens": 1000, "output_tokens": 100}}
    script = [
        {**limited, "prompt_contains": EX1},
        {**answer, "prompt_contains": EX1},
        *([{**limited, "prompt_contains": EX2}] * 5),
    ]
    matrix = _matrix_with(tmp_path, {"scripted-anthropic": script})
    config = _config(
        tmp_path,
        matrix_path=matrix,
        models=("scripted-anthropic",),
        systems=("no-docs",),
        repeats=1,
    )
    summary = run_agentic(config)
    fam = summary["families"]["scripted-anthropic"]
    assert fam["retries"]["rate_limited"] == 1 + 3 * MAX_RETRIES and fam["reruns"] == 2
    assert [d["attempt"] for d in summary["discarded"]] == [1, 2, 3]
    assert len(summary["not_run"]) == 1 and summary["not_run"][0]["reason"] == "retries_exhausted"
    assert summary["not_run"][0]["cell"] == "no-docs--scripted-anthropic--r1"
    assert summary["succeeded_cells"] == 1 and fam["answer_turns"] == 1


class _LostAfterDiscard(LocalLauncher):
    """A worker that reports one retries_exhausted discard, then dies before its rerun."""

    def command(self, job: Any) -> tuple[list[str], dict[str, str]]:
        _, env = super().command(job)
        timing = {f"{name}_ms": 0 for name in ("grant_wait", "failed_attempt", "retry_wait")}
        record = {
            "status": "discarded",
            "started_at": "2026-11-04T00:00:00.000Z",
            "completed_at": "2026-11-04T00:00:01.000Z",
            "timing": timing,
            "model_calls": [],
        }
        frame = {
            "type": "discarded",
            "record": record,
            "corpus_id": "SYN-EX-001",
            "repeat": 1,
            "reason": "retries_exhausted",
            "attempt": 1,
        }
        code = (
            "import json, sys\n"
            "inp, out = sys.stdin.buffer, sys.stdout.buffer\n"
            "inp.read(int.from_bytes(inp.read(8), 'big'))\n"
            f"body = json.dumps({frame!r}).encode()\n"
            "out.write(len(body).to_bytes(8, 'big') + body)\n"
            "out.flush()\n"
            "inp.read(int.from_bytes(inp.read(8), 'big'))\n"
            "sys.exit(1)\n"
        )
        return [sys.executable, "-c", code], env


def _start_host(tmp_path: Path, prelude: str = "") -> tuple[subprocess.Popen[bytes], Path]:
    """A host in a subprocess this test creates, running ``prelude`` first. Its one worker
    holds a unit, reports its pid and process group, then waits until its host closes stdin."""
    ready = tmp_path / "worker.ids"
    reserve = {"type": "reserve", "input_bound": 1000, "output_bound": 100, "kind": "model_call"}
    worker = (
        "import json, os, sys\n"
        "inp, out = sys.stdin.buffer, sys.stdout.buffer\n"
        "inp.read(int.from_bytes(inp.read(8), 'big'))\n"
        f"body = json.dumps({reserve!r}).encode()\n"
        "out.write(len(body).to_bytes(8, 'big') + body)\n"
        "out.flush()\n"
        "inp.read(int.from_bytes(inp.read(8), 'big'))\n"
        f"open({str(ready) + '.tmp'!r}, 'w').write(f'{{os.getpid()}} {{os.getpgid(0)}}')\n"
        f"os.replace({str(ready) + '.tmp'!r}, {str(ready)!r})\n"
        "inp.read()\n"
    )
    host_code = (
        # Only this child resets inherited ignores; a prelude can install its own handlers.
        "import signal\n"
        "signal.signal(signal.SIGHUP, signal.SIG_DFL)\n"
        "signal.signal(signal.SIGTERM, signal.SIG_DFL)\n" + prelude + "import sys\n"
        "from pathlib import Path\n"
        "from benchmarks.agentic.host import LocalLauncher\n"
        "from benchmarks.agentic.run import AgenticConfig, run_agentic\n"
        "class Launcher(LocalLauncher):\n"
        "    def command(self, job):\n"
        f"        return [sys.executable, '-c', {worker!r}], super().command(job)[1]\n"
        "config = AgenticConfig(\n"
        f"    corpus_path=Path({str(SAMPLE_CORPUS)!r}),\n"
        f"    matrix_path=Path({str(FIX / 'model-matrix.yml')!r}),\n"
        f"    manifest_path=Path({str(COMMITTED_MANIFEST)!r}),\n"
        f"    prices_path=Path({str(FIX / 'prices.yml')!r}),\n"
        f"    ledger_dir=Path({str(tmp_path / 'ledger')!r}),\n"
        f"    out_dir=Path({str(tmp_path / 'run')!r}),\n"
        "    models=('scripted-anthropic',),\n"
        "    systems=('no-docs',),\n"
        "    questions=('SYN-EX-001',),\n"
        "    repeats=1,\n"
        "    seed=7,\n"
        ")\n"
        "run_agentic(config, launcher=Launcher())\n"
    )
    host = subprocess.Popen(
        [sys.executable, "-c", host_code],
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return host, ready


def _when_ready(host: subprocess.Popen[bytes], ready: Path) -> tuple[int, int]:
    """The worker's pid and process group (its anchor's pid), once it holds its unit."""
    deadline = time.monotonic() + 20
    while not ready.exists():  # bounded: a worker that never holds its unit fails loudly
        assert host.poll() is None, "the host ended before its worker got ready"
        assert time.monotonic() < deadline, "the worker never got ready"
        time.sleep(0.01)
    worker, group = map(int, ready.read_text().split())
    return worker, group


def _gone(pid: int) -> bool:
    """Whether ``pid`` has exited within 5 s; a zombie has, whoever has yet to reap it."""
    deadline = time.monotonic() + 5
    while _alive(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP])
def test_a_termination_signal_tears_the_run_down_like_ctrl_c(
    tmp_path: Path, signum: signal.Signals
) -> None:
    # Only the host is signalled. Workers have process groups of their own, so only the
    # host's teardown can end its worker.
    host, ready = _start_host(tmp_path)
    try:
        worker, _ = _when_ready(host, ready)
        host.send_signal(signum)
        host.wait(timeout=20)
    finally:
        if host.poll() is None:
            host.kill()
            host.wait()
    assert _gone(worker), "the worker outlived its host"
    lines = (tmp_path / "ledger" / "anthropic.jsonl").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines]
    assert [(e["event"], e.get("outcome")) for e in events] == [
        ("reserve", None),
        ("settle", "worker_lost"),
    ]
    assert Decimal(events[1]["booked"]) == 2 * Decimal(events[0]["bound"])
    summary = json.loads((tmp_path / "run" / "run-summary.json").read_text())
    assert summary["status"] == "aborted" and signum.name in summary["abort_reason"]


def test_no_anchor_outlives_a_host_killed_outright(tmp_path: Path) -> None:
    # SIGKILL leaves no teardown: the anchor must end once its host's pipe closes.
    host, ready = _start_host(tmp_path)
    try:
        _, anchor = _when_ready(host, ready)
        host.kill()
        host.wait(timeout=20)
    finally:
        if host.poll() is None:
            host.kill()
            host.wait()
    # No cleanup by raw pid: the test holds no handle to its host's anchor.
    assert _gone(anchor), f"the anchor (pid {anchor}) outlived its host"


def test_a_host_started_with_sighup_ignored_survives_sighup(tmp_path: Path) -> None:
    # As under nohup. Only the host is signalled; it must ignore SIGHUP, then end on SIGTERM.
    ignore = "import signal\nsignal.signal(signal.SIGHUP, signal.SIG_IGN)\n"
    host, ready = _start_host(tmp_path, prelude=ignore)
    try:
        worker, _ = _when_ready(host, ready)
        host.send_signal(signal.SIGHUP)
        with pytest.raises(subprocess.TimeoutExpired):  # 1 s for an effect that must not come
            host.wait(timeout=1)
        host.send_signal(signal.SIGTERM)
        host.wait(timeout=20)
    finally:
        if host.poll() is None:
            host.kill()
            host.wait()
    assert _gone(worker), "the worker outlived its host"
    summary = json.loads((tmp_path / "run" / "run-summary.json").read_text())
    assert summary["status"] == "aborted" and "SIGTERM" in summary["abort_reason"]


def _scripted_config(tmp_path: Path) -> AgenticConfig:
    return _config(
        tmp_path,
        models=("scripted-anthropic",),
        systems=("no-docs",),
        questions=("SYN-EX-001",),
        repeats=1,
    )


@pytest.fixture
def default_signal_handlers(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    getsignal = signal.getsignal
    before = {s: getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP)}
    monkeypatch.setattr(
        signal, "getsignal", lambda s: signal.SIG_DFL if s in before else getsignal(s)
    )
    yield
    assert {s: getsignal(s) for s in before} == before


def test_run_agentic_completes_off_the_main_thread(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], default_signal_handlers: None
) -> None:
    # No signal handler can be installed off the main thread: the run goes on without one,
    # and says so.
    with ThreadPoolExecutor(1) as pool:
        summary = pool.submit(run_agentic, _scripted_config(tmp_path)).result(timeout=60)
    assert summary["status"] == "completed" and summary["succeeded_cells"] == 1
    skipped = [line for line in capsys.readouterr().err.splitlines() if "handler" in line]
    assert len(skipped) == 1 and "SIGTERM, SIGHUP" in skipped[0] and "RuntimeError" in skipped[0]


def test_a_loop_without_signal_support_runs_without_handlers(
    capsys: pytest.CaptureFixture[str],
    default_signal_handlers: None,
) -> None:
    before = {signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGHUP)}
    removed: list[int] = []

    async def scenario() -> list[str]:
        loop = asyncio.get_running_loop()

        def unsupported(*args: Any) -> None:
            raise NotImplementedError

        loop.add_signal_handler = unsupported  # type: ignore[method-assign]
        loop.remove_signal_handler = removed.append  # type: ignore[method-assign,assignment]
        ran: list[str] = []

        async def run() -> None:
            ran.append("ran")

        await _until_signalled(run())
        return ran

    assert asyncio.run(scenario()) == ["ran"]
    assert removed == []  # nothing was installed, so nothing is removed
    assert {signum: signal.getsignal(signum) for signum in before} == before
    err = capsys.readouterr().err
    assert "SIGTERM, SIGHUP" in err and "NotImplementedError" in err


def test_an_ignored_sighup_is_left_out_of_the_skipped_handlers_line(
    capsys: pytest.CaptureFixture[str],
    default_signal_handlers: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    getsignal = signal.getsignal
    monkeypatch.setattr(
        signal, "getsignal", lambda s: signal.SIG_IGN if s == signal.SIGHUP else getsignal(s)
    )

    async def scenario() -> None:
        loop = asyncio.get_running_loop()

        def unsupported(*args: Any) -> None:
            raise NotImplementedError

        loop.add_signal_handler = unsupported  # type: ignore[method-assign]

        async def run() -> None:
            return None

        await _until_signalled(run())

    asyncio.run(scenario())
    line = next(line for line in capsys.readouterr().err.splitlines() if "handler" in line)
    assert "SIGTERM" in line and "SIGHUP" not in line


def test_run_agentic_restores_the_previous_signal_dispositions(tmp_path: Path) -> None:
    def previous(signum: int, frame: Any) -> None:
        raise AssertionError("no signal is sent in this test")

    saved = {signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGHUP)}
    try:
        signal.signal(signal.SIGTERM, previous)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        summary = run_agentic(_scripted_config(tmp_path))
        after = {signum: signal.getsignal(signum) for signum in saved}
    finally:
        for signum, handler in saved.items():
            signal.signal(signum, handler)
    assert summary["status"] == "completed"
    assert after == {signal.SIGTERM: previous, signal.SIGHUP: signal.SIG_IGN}


def test_a_rerun_that_never_ran_is_not_counted(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        models=("scripted-anthropic",),
        systems=("no-docs",),
        questions=("SYN-EX-001",),
        repeats=1,
    )
    summary = run_agentic(config, launcher=_LostAfterDiscard())
    assert summary["not_run"] == [
        {
            "cell": "no-docs--scripted-anthropic--r1",
            "corpus_id": "SYN-EX-001",
            "reason": "worker_lost",
            "attempt": None,
        }
    ]
    assert summary["families"]["scripted-anthropic"]["reruns"] == 0


def test_run_summary_wall_time_includes_failed_attempts_backoff_and_grant_waits(
    tmp_path: Path,
) -> None:
    # Two scripted families of provider anthropic, one no-docs job each, so their units contend
    # for the grant. The error step holds the grant for 1.0 s (owner amendment on #184), then
    # 0.3 s of backoff, then a 0.1 s answer.
    script = [
        {"error": {"kind": "rate_limited", "status": 429, "retry_after_s": 0.3}, "delay_s": 1.0},
        {"text": "ok", "delay_s": 0.1, "usage": {"input_tokens": 1000, "output_tokens": 100}},
    ]
    fixture = yaml.safe_load((FIX / "model-matrix.yml").read_text(encoding="utf-8"))
    twin = {**fixture["model_families"][0], "id": "scripted-anthropic-b"}
    matrix = _matrix_with(
        tmp_path, {"scripted-anthropic": script, "scripted-anthropic-b": script}, extra=[twin]
    )
    config = _config(
        tmp_path,
        matrix_path=matrix,
        models=("scripted-anthropic", "scripted-anthropic-b"),
        systems=("no-docs",),
        questions=("SYN-EX-001",),
        repeats=1,
    )
    summary = run_agentic(config)
    assert set(summary["families"]) == {"scripted-anthropic", "scripted-anthropic-b"}
    for fam in summary["families"].values():
        assert fam["failed_attempt_s"] >= 0.2 and fam["retry_wait_s"] >= 0.3
        assert fam["wall_s"] >= 0.6
        assert fam["first_started_at"] < fam["last_completed_at"]
    assert sum(f["grant_wait_s"] for f in summary["families"].values()) > 0


def test_run_summary_counts_input_bounds_exceeded_per_provider(tmp_path: Path) -> None:
    # 8,000 reported input tokens exceed the first call's input bound (about 1,000), while
    # the dollars stay under the bound, so there is no overrun stop.
    big_input = {"text": "ok", "usage": {"input_tokens": 8000, "output_tokens": 0}}
    matrix = _matrix_with(tmp_path, {"scripted-anthropic": [big_input]})
    config = _config(
        tmp_path,
        matrix_path=matrix,
        models=("scripted-anthropic",),
        systems=("no-docs",),
        questions=("SYN-EX-001",),
        repeats=1,
    )
    summary = run_agentic(config)
    assert summary["ledger"]["anthropic"]["input_bound_exceeded"] == 1
    assert summary["ledger"]["openai"]["input_bound_exceeded"] == 0
    assert summary["ledger"]["anthropic"]["stopped"] is None


def test_run_summary_counts_model_call_timeouts_per_family(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("benchmarks.agentic.run.HOSTED_CAPS", Caps(model_call_timeout_s=0.1))
    usage = {"input_tokens": 1000, "output_tokens": 100}
    script = [{"text": "slow", "delay_s": 0.5, "usage": usage}, {"text": "ok", "usage": usage}]
    matrix = _matrix_with(tmp_path, {"scripted-anthropic": script})
    config = _config(
        tmp_path,
        matrix_path=matrix,
        models=("scripted-anthropic",),
        systems=("no-docs",),
        questions=("SYN-EX-001",),
        repeats=1,
    )
    summary = run_agentic(config)
    assert summary["families"]["scripted-anthropic"]["timeouts"] == 1
    assert summary["succeeded_cells"] == 1


def test_latency_counts_successful_work_and_overhead_only(tmp_path: Path) -> None:
    script = [
        {"error": {"kind": "rate_limited", "status": 429, "retry_after_s": 0.3}, "delay_s": 0.2},
        {"text": "ok", "usage": {"input_tokens": 1000, "output_tokens": 100}},
    ]
    matrix = _matrix_with(tmp_path, {"scripted-anthropic": script})
    config = _config(
        tmp_path,
        matrix_path=matrix,
        models=("scripted-anthropic",),
        systems=("no-docs",),
        questions=("SYN-EX-001",),
        repeats=1,
    )
    run_agentic(config)
    cell = tmp_path / "run" / "latency" / "no-docs--scripted-anthropic--r1" / "SYN-EX-001.json"
    latency = json.loads(cell.read_text())
    timing = latency["timing"]
    assert latency["latency_ms"] == round(timing["used_ms"] + timing["overhead_ms"], 3)
    assert latency["latency_ms"] < timing["wall_ms"] - 400  # no failed attempt, no backoff
    assert set(timing) >= {"grant_wait_ms", "failed_attempt_ms", "retry_wait_ms"}


def test_continuation_answers_count_as_output_cap_stops(tmp_path: Path) -> None:
    # One scripted google family, no-docs, all 50 sample questions, repeats 2 = 100 answers.
    usage = {"prompt_token_count": 1000, "candidates_token_count": 100}
    script = [
        {"text": "partial", "stop_reason": "CONTINUATION", "prompt_contains": EX1, "usage": usage},
        {"text": "Scripted answer.", "usage": usage},
    ]
    matrix = _matrix_with(tmp_path, {"scripted-google": script})
    config = _config(
        tmp_path,
        matrix_path=matrix,
        models=("scripted-google",),
        systems=("no-docs",),
        questions=None,
        repeats=2,
    )
    summary = run_agentic(config)
    fam = summary["families"]["scripted-google"]
    assert (fam["output_cap_stops"], fam["answer_turns"]) == (2, 100)  # 2%


def test_late_max_tokens_answers_count_as_answer_turns_and_cap_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Codex's case: 98 normal answers plus 2 answers that arrive after the trajectory allowance
    # and stopped at the output cap. Both late ones fail the trajectory; both still count.
    monkeypatch.setattr("benchmarks.agentic.run.HOSTED_CAPS", Caps(trajectory_s=0.2))
    usage = {"prompt_token_count": 1000, "candidates_token_count": 100}
    script = [
        {
            "text": "cut",
            "stop_reason": "MAX_TOKENS",
            "delay_s": 0.4,
            "prompt_contains": EX1,
            "usage": usage,
        },
        {"text": "Scripted answer.", "usage": usage},
    ]
    matrix = _matrix_with(tmp_path, {"scripted-google": script})
    config = _config(
        tmp_path,
        matrix_path=matrix,
        models=("scripted-google",),
        systems=("no-docs",),
        questions=None,
        repeats=2,
    )
    summary = run_agentic(config)
    fam = summary["families"]["scripted-google"]
    assert summary["failed_cells"] == 2
    assert (fam["output_cap_stops"], fam["answer_turns"]) == (2, 100)


def test_a_key_shape_in_an_input_file_is_refused_before_any_snapshot(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.yml"
    key_shape = "sk-ant-api03-" + "A" * 90
    manifest.write_text(COMMITTED_MANIFEST.read_text() + f"\n# {key_shape}\n", encoding="utf-8")
    with pytest.raises(KeyLeakError) as caught:
        run_agentic(_config(tmp_path, manifest_path=manifest, dry_run=True))
    assert key_shape not in str(caught.value)
    assert not (tmp_path / "run" / "snapshots").exists()


def _ref_tools_manifest(tmp_path: Path, **fields: Any) -> Path:
    template = yaml.safe_load(TEMPLATE_MANIFEST.read_text(encoding="utf-8"))
    (ref,) = [c for c in template["competitors"] if c["id"] == "ref-tools"]
    ref |= {"adapter": "mcp-stdio", "command": ["{python}", str(FAKE_SERVER)], **fields}
    path = tmp_path / "manifest.yml"
    path.write_text(yaml.safe_dump({"competitors": [ref]}), encoding="utf-8")
    return path


def test_a_competitor_key_reaches_its_system_only_through_pass_env(tmp_path: Path) -> None:
    secret = "ref_synthetic_secret_123"
    literal = _ref_tools_manifest(tmp_path, env={"REF_API_KEY": secret})
    with pytest.raises(BenchmarkValidationError, match="REF_API_KEY") as caught:
        run_agentic(_config(tmp_path, manifest_path=literal, dry_run=True))
    assert secret not in str(caught.value)
    assert not (tmp_path / "run" / "snapshots").exists()
    passed = _ref_tools_manifest(tmp_path, env={}, pass_env=["REF_API_KEY"])
    summary = run_agentic(_config(tmp_path, manifest_path=passed, dry_run=True))
    assert summary["systems"] == ["ref-tools"]


@pytest.mark.parametrize(
    "fields",
    [
        {"env": {"REF_API_KEY": "ref_synthetic_secret_123"}},
        {"pass_env": ["ANTHROPIC_API_KEY"]},
    ],
)
def test_a_system_without_a_transport_is_screened_for_keys_too(
    tmp_path: Path, fields: dict[str, Any]
) -> None:
    manifest = tmp_path / "manifest.yml"
    no_docs = {"id": "no-docs", "adapter": "none", **fields}
    manifest.write_text(yaml.safe_dump({"competitors": [no_docs]}), encoding="utf-8")
    with pytest.raises(BenchmarkValidationError, match="_API_KEY") as caught:
        run_agentic(_config(tmp_path, manifest_path=manifest, dry_run=True))
    assert "ref_synthetic_secret_123" not in str(caught.value)
    assert not (tmp_path / "run" / "snapshots").exists()


def test_a_worker_error_costs_only_its_own_cells(tmp_path: Path) -> None:
    # Usage that cannot be bucketed (cached > input) raises BenchmarkValidationError in the
    # openai worker; the anthropic job still finishes and the summary lists the failure.
    bad = {
        "text": "x",
        "usage": {"input_tokens": 100, "input_tokens_details": {"cached_tokens": 200}},
    }
    matrix = _matrix_with(tmp_path, {"scripted-openai": [bad]})
    config = _config(
        tmp_path,
        matrix_path=matrix,
        models=("scripted-anthropic", "scripted-openai"),
        systems=("no-docs",),
        questions=("SYN-EX-001",),
        repeats=1,
    )
    summary = run_agentic(config)
    assert summary["status"] == "completed" and summary["succeeded_cells"] == 1
    assert summary["not_run"] == [
        {
            "cell": "no-docs--scripted-openai--r1",
            "corpus_id": "SYN-EX-001",
            "reason": "worker_error",
            "attempt": None,
        }
    ]
    (error,) = summary["worker_errors"]
    assert error["job_id"] == "scripted-openai--no-docs" and "negative" in error["reason"]
    assert Decimal(summary["ledger"]["openai"]["spent_after"]) > 0  # its unit, at 2x the bound


def test_an_aborted_run_still_writes_its_summary_and_ledger_events(tmp_path: Path) -> None:
    leak = {"text": "sk-ant-api03-" + "a" * 90, "usage": {"input_tokens": 1, "output_tokens": 1}}
    matrix = _matrix_with(tmp_path, {"scripted-anthropic": [leak]})
    config = _config(
        tmp_path,
        matrix_path=matrix,
        models=("scripted-anthropic",),
        systems=("no-docs",),
        questions=("SYN-EX-001",),
        repeats=1,
    )
    with pytest.raises(KeyLeakError):
        run_agentic(config)
    run = tmp_path / "run"
    summary = json.loads((run / "run-summary.json").read_text())
    assert summary["status"] == "aborted" and "KeyLeakError" in summary["abort_reason"]
    assert (run / "ledger-events.jsonl").read_text().count('"event": "reserve"') == 1


def test_a_run_that_stops_on_a_reopened_overrun_still_writes_its_outcome(tmp_path: Path) -> None:
    # An earlier run left an Anthropic $1 unit settled at $2 and an OpenAI $0.50 unit unsettled.
    earlier = Ledger.open(tmp_path / "ledger", run_id="earlier")
    units = {
        provider: earlier.reserve(
            provider,
            model_id="m",
            kind="model_call",
            bound=Decimal(bound),
            input_bound=1,
            output_bound=1,
        )
        for provider, bound in (("anthropic", "1"), ("openai", "0.50"))
    }
    earlier.settle(
        str(units["anthropic"]), outcome="ok", actual=Decimal("2"), buckets=Buckets(1, 0, 0, 1)
    )
    earlier.close()
    config = _config(
        tmp_path,
        models=("scripted-anthropic", "scripted-openai"),
        systems=("no-docs",),
        questions=("SYN-EX-001",),
        repeats=1,
    )
    with pytest.raises(BenchmarkValidationError, match="overrun"):
        run_agentic(config)
    run = tmp_path / "run"
    summary = json.loads((run / "run-summary.json").read_text())
    assert summary["status"] == "aborted" and "overrun" in summary["abort_reason"]
    events = (run / "ledger-events.jsonl").read_text().splitlines()
    (recovery,) = [json.loads(line) for line in events]
    assert (recovery["provider"], recovery["outcome"], recovery["booked"]) == (
        "openai",
        "unsettled_at_load",
        "1.00",
    )
    assert summary["ledger"]["openai"]["spent_after"] == "1.00"


def test_agentic_run_refuses_a_non_empty_out_dir(tmp_path: Path) -> None:
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "stale.json").write_text("{}")
    with pytest.raises(BenchmarkValidationError, match="not empty"):
        run_agentic(_config(tmp_path))


def test_agentic_run_refuses_an_unknown_family_or_system(tmp_path: Path) -> None:
    with pytest.raises(BenchmarkValidationError, match="no-such-model"):
        run_agentic(_config(tmp_path, models=("no-such-model",)))
    with pytest.raises(BenchmarkValidationError, match="context7"):
        run_agentic(_config(tmp_path, systems=("context7",)))


def test_agentic_run_refuses_a_family_whose_cap_excludes_reasoning(tmp_path: Path) -> None:
    fixture = yaml.safe_load((FIX / "model-matrix.yml").read_text(encoding="utf-8"))
    odd = {**fixture["model_families"][0], "id": "no-reasoning-in-cap"}
    odd["output_cap_includes_reasoning"] = False
    matrix = _matrix_with(tmp_path, {}, extra=[odd])
    with pytest.raises(BenchmarkValidationError, match="output_cap_includes_reasoning"):
        run_agentic(_config(tmp_path, matrix_path=matrix, models=("no-reasoning-in-cap",)))


def test_agentic_run_dry_run_writes_the_plan_without_a_ledger(tmp_path: Path) -> None:
    summary = run_agentic(_config(tmp_path, dry_run=True))
    run = tmp_path / "run"
    assert summary["dry_run"] is True and summary["planned_cells"] == 36
    planned = json.loads((run / "planned-cells.json").read_text())["cells"]
    assert len(planned) == 36
    assert {"cell", "competitor_id", "corpus_id", "repeat", "system_id"} <= set(planned[0])
    assert (run / "environment.json").is_file() and (run / "snapshots" / "corpus.yml").is_file()
    assert not (tmp_path / "ledger").exists()
    assert not (run / "transcripts").exists()


def test_cli_agentic_run_exits_2_with_a_provider_key_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "x-not-a-real-key")
    with pytest.raises(SystemExit) as caught:
        main(_cli_args(tmp_path))
    assert caught.value.code == 2


def test_cli_agentic_run_exits_3_on_a_key_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def leak(*args: Any, **kwargs: Any) -> Any:
        raise KeyLeakError("a key-shaped string was found in a test frame")

    monkeypatch.setattr("benchmarks.agentic.run.run_agentic", leak)
    with pytest.raises(SystemExit) as caught:
        main(_cli_args(tmp_path))
    assert caught.value.code == 3


def test_cli_agentic_run_dry_run_prints_the_plan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([*_cli_args(tmp_path), "--dry-run", "--repeats", "1", "--run-id", "cli"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["planned_cells"] == 18 and printed["run_id"] == "cli"


def test_runs_share_one_durable_ledger_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import platformdirs

    from benchmarks.agentic.run import default_ledger_dir

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "home" / "data"))
    expected = Path(platformdirs.user_data_dir("mcp-python-docs-benchmarks")) / "ledger"
    assert default_ledger_dir() == expected and str(tmp_path) in str(expected)
    spent = []
    for run in ("first", "second"):
        args = [
            "agentic-run",
            "--corpus",
            str(SAMPLE_CORPUS),
            "--out",
            str(tmp_path / run),
            "--matrix",
            str(FIX / "model-matrix.yml"),
            "--manifest",
            str(COMMITTED_MANIFEST),
            "--prices",
            str(FIX / "prices.yml"),
            "--models",
            "scripted-anthropic",
            "--systems",
            "no-docs",
            "--questions",
            "SYN-EX-001",
            "--repeats",
            "1",
        ]
        assert main(args) == 0
        captured = capsys.readouterr()
        ledger = json.loads(captured.out)["ledger"]["anthropic"]
        spent.append((Decimal(ledger["spent_before"]), Decimal(ledger["spent_after"])))
        assert "anthropic" in captured.err and "cap 180" in captured.err
    (first_before, first_after), (second_before, second_after) = spent
    assert first_before == 0 and second_before == first_after > 0
    assert (expected / "anthropic.jsonl").is_file()


def _cli_args(tmp_path: Path) -> list[str]:
    return [
        "agentic-run",
        "--corpus",
        str(SAMPLE_CORPUS),
        "--out",
        str(tmp_path / "run"),
        "--ledger-dir",
        str(tmp_path / "ledger"),
        "--matrix",
        str(FIX / "model-matrix.yml"),
        "--prices",
        str(FIX / "prices.yml"),
        "--questions",
        "SYN-EX-001,SYN-EX-002",
    ]


# --- the live gate inside run_agentic, and the agentic-run CLI (Task 13) -------------------------


def _container(tmp_path: Path, keys_file: Path | None = None) -> tuple[ContainerLauncher, Any]:
    from tests.benchmarks.test_agentic_sandbox import FakeDocker, Probes, _verified

    docker = FakeDocker()
    sandbox = Sandbox(
        SandboxConfig(
            image="sha256:" + "ab" * 32,
            run_id="r1",
            out_dir=tmp_path / "run",
            index_path=tmp_path / "index.db",
            keys_file=keys_file,
            repo_commit="c" * 40,
        ),
        docker=docker,
        verify=_verified,
    )
    probes = Probes()
    return ContainerLauncher(sandbox, probe=probes, control=probes.control), docker


def _live_config(tmp_path: Path, **overrides: Any) -> AgenticConfig:
    values: dict[str, Any] = {
        "matrix_path": COMMITTED_MATRIX,
        "prices_path": COMMITTED_PRICES,
        "models": ("claude-sonnet-5-5",),
        "systems": ("no-docs",),
        "questions": ("SYN-EX-001",),
        "repeats": 1,
    }
    return _config(tmp_path, **(values | overrides))


def test_the_gate_runs_before_any_key_is_read_or_container_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CI", "1")
    launcher, docker = _container(tmp_path, keys_file=tmp_path / "missing.env")
    with pytest.raises(BenchmarkValidationError, match="refused under CI"):
        run_agentic(_live_config(tmp_path), launcher=launcher, today=date(2026, 11, 4))
    assert docker.calls == []
    summary = json.loads((tmp_path / "run" / "run-summary.json").read_text())
    assert summary["status"] == "aborted" and "refused under CI" in summary["abort_reason"]


def test_a_declined_confirmation_starts_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CI", raising=False)
    launcher, docker = _container(tmp_path, keys_file=tmp_path / "missing.env")
    shown: list[str] = []

    def decline(text: str) -> bool:
        shown.append(text)
        return False

    with pytest.raises(BenchmarkValidationError, match="not confirmed"):
        run_agentic(
            _live_config(tmp_path), launcher=launcher, today=date(2026, 11, 4), confirm=decline
        )
    assert docker.calls == []
    assert len(shown) == 1 and shown[0].startswith("anthropic: 1 trajectories")
    summary = json.loads((tmp_path / "run" / "run-summary.json").read_text())
    assert summary["status"] == "aborted" and summary["live_confirmation"] == shown[0]


def test_the_full_run_gate_stops_a_run_before_the_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CI", raising=False)
    launcher, docker = _container(tmp_path)
    config = _live_config(tmp_path, models=("gpt-6.1-sol",), repeats=91)

    def never(text: str) -> bool:
        raise AssertionError("the gate refuses first")

    with pytest.raises(BenchmarkValidationError, match="--reference-run"):
        run_agentic(config, launcher=launcher, today=date(2026, 11, 4), confirm=never)
    assert docker.calls == []


def test_the_default_model_set_is_the_hosted_arm(tmp_path: Path) -> None:
    launcher, _ = _container(tmp_path)
    config = _live_config(tmp_path, models=None, dry_run=True)
    summary = run_agentic(config, launcher=launcher, today=date(2026, 11, 4))
    assert summary["models"] == ["claude-sonnet-5-5", "gpt-6.1-sol", "gemini-3.8-flash"]
    named = _live_config(tmp_path, models=("gemma4-12b",), dry_run=True, out_dir=tmp_path / "g")
    assert run_agentic(named, launcher=launcher, today=date(2026, 11, 4))["models"] == [
        "gemma4-12b"
    ]


def test_run_agentic_refuses_mixing_the_air_gapped_and_hosted_arms(tmp_path: Path) -> None:
    launcher, _ = _container(tmp_path)
    config = _live_config(tmp_path, models=("claude-sonnet-5-5", "gemma4-12b"), dry_run=True)
    with pytest.raises(BenchmarkValidationError, match="the air-gapped arm runs alone"):
        run_agentic(config, launcher=launcher, today=date(2026, 11, 4))


def _cli(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "agentic-run",
        "--corpus",
        str(SAMPLE_CORPUS),
        "--out",
        str(tmp_path / "run"),
        "--matrix",
        str(FIX / "model-matrix.yml"),
        "--prices",
        str(FIX / "prices.yml"),
        "--ledger-dir",
        str(tmp_path / "ledger"),
        "--models",
        "scripted-anthropic",
        "--questions",
        "SYN-EX-001",
        "--repeats",
        "1",
        *extra,
    ]


def test_cli_container_launcher_defaults_to_a_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import benchmarks.agentic.sandbox as sandbox_module

    def no_docker(env: Any) -> Any:
        async def run(argv: Any) -> Any:
            raise AssertionError(f"docker must not run in a dry run: {argv}")

        return run

    monkeypatch.setattr(sandbox_module, "docker_runner", no_docker)
    image = "sha256:" + "0" * 64
    assert main(_cli(tmp_path, "--launcher", "container", "--image", image)) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["dry_run"] is True and summary["planned_cells"] == 3
    assert not (tmp_path / "ledger").exists()


def test_cli_builds_the_production_sandbox_with_the_real_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Amendment 6: on the production path the sandbox keeps the real verify_image.
    import benchmarks.agentic.run as agentic_run
    from benchmarks.agentic.sandbox import verify_image

    seen: dict[str, Any] = {}

    def capture(config: AgenticConfig, **kwargs: Any) -> dict[str, Any]:
        seen.update(config=config, **kwargs)
        return {"dry_run": config.dry_run}

    monkeypatch.setattr(agentic_run, "run_agentic", capture)
    keys = tmp_path / "keys.env"
    image = "sha256:" + "1" * 64
    argv = ["--launcher", "container", "--image", image, "--keys-file", str(keys), "--live"]
    assert main(_cli(tmp_path, *argv, "--run-id", "run-42", "--index", str(tmp_path / "i.db"))) == 0
    launcher = seen["launcher"]
    assert isinstance(launcher, ContainerLauncher)
    assert launcher.sandbox.verify is verify_image
    sandbox_config = launcher.sandbox.config
    assert sandbox_config.run_id == seen["config"].run_id == "run-42"
    assert (sandbox_config.image, sandbox_config.keys_file) == (image, keys)
    assert sandbox_config.index_path == tmp_path / "i.db"
    assert sandbox_config.out_dir == tmp_path / "run"
    assert seen["config"].dry_run is False


@pytest.mark.parametrize(
    "extra",
    [
        ("--live",),  # the local launcher runs scripted models only
        ("--launcher", "container", "--image", "sha256:" + "0" * 64, "--live", "--dry-run"),
        ("--launcher", "container"),  # no --image
    ],
)
def test_cli_refuses_live_with_the_local_launcher(tmp_path: Path, extra: tuple[str, ...]) -> None:
    with pytest.raises(SystemExit) as caught:
        main(_cli(tmp_path, *extra))
    assert caught.value.code == 2
