"""Keyless host, redacted workers and length-prefixed frames (design §7.3; ruling B2, B3).

Workers are real subprocesses; models are scripted, so nothing reaches a network host.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

import benchmarks.agentic.host as agentic_host
from benchmarks.adapters.guard import PAID_KEY_ENVS, PROVIDER_API_KEY_ENV
from benchmarks.agentic.bridge import system_spec_from_raw
from benchmarks.agentic.host import LocalLauncher, WorkerJob, refuse_host_keys, run_jobs
from benchmarks.agentic.ledger import Ledger, LedgerService, ModelPrice, Rates, Tier
from benchmarks.agentic.loop import HOSTED_CAPS, Caps
from benchmarks.agentic.protocol import FrameTooLarge, KeyLeakError, assert_no_key_shaped
from benchmarks.model_matrix import ModelFamily, load_model_matrix, model_family_from_raw
from benchmarks.runner import BenchmarkValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_MATRIX_PATH = REPO_ROOT / "docs" / "benchmarks" / "model-matrix.yml"
FAKE_SERVER = Path(__file__).parent / "fixtures" / "agentic" / "fake_fetch_server.py"
KEY = "sk-ant-synthetic-" + "k" * 40
D = Decimal
USAGE = {"input_tokens": 1000, "output_tokens": 100}
ANSWER = {"text": "Scripted answer.", "usage": USAGE}
NO_DOCS = system_spec_from_raw({"id": "no-docs", "adapter": "none"})
FAKE_FETCH = system_spec_from_raw(
    {
        "id": "docs-fetch",
        "adapter": "mcp-stdio",
        "command": ["{python}", str(FAKE_SERVER), "--edge"],
        "env": {},
        "pass_env": [],
    }
)
FLAT = ModelPrice(
    "scripted-1", "anthropic", (Tier(None, Rates(D(1), D(1), D(1), D(1))),), "https://x.invalid"
)


def _frames(data: bytes) -> list[dict[str, Any]]:
    frames = []
    while data:
        length = int.from_bytes(data[:8], "big")
        frames.append(json.loads(data[8 : 8 + length]))
        data = data[8 + length :]
    return frames


def _family(script: Path) -> ModelFamily:
    return model_family_from_raw(
        {
            "id": "scripted-anthropic",
            "provider": "anthropic",
            "model_id": "scripted-1",
            "client": "scripted",
            "token_count_method": "claude_normalized_payload",
            "latency_scope": "request_dispatch_to_final_answer",
            "headline_eligible": False,
            "headline_ineligibility_reason": "test",
            "api": "scripted",
            "arm": "hosted",
            "max_input_tokens": 200_000,
            "max_output_tokens": 1024,
            "output_cap_includes_reasoning": True,
            "framing_allowance_tokens": 256,
            "script": str(script),
        }
    )


def _job(
    tmp_path: Path,
    steps: list[dict[str, Any]],
    *,
    system: Any = NO_DOCS,
    count: int = 2,
    job_id: str | None = None,
) -> WorkerJob:
    script = tmp_path / f"script-{job_id or 'main'}.yml"
    script.write_text(yaml.safe_dump({"steps": steps}), encoding="utf-8")
    family = _family(script)
    trajectories = tuple(
        {"corpus_id": f"Q{i}", "prompt": f"question {i}", "repeat": 1} for i in range(1, count + 1)
    )
    return WorkerJob(
        job_id or f"{family.id}--{system.id}", family, system, trajectories, HOSTED_CAPS
    )


class Host:
    """A ledger, a message collector and run_jobs with the repo root as the worker cwd."""

    def __init__(self, tmp_path: Path, caps: dict[str, Decimal] | None = None) -> None:
        self.tmp_path = tmp_path
        self.ledger = Ledger.open(
            tmp_path / "ledger", run_id="host-test", caps=caps or {"anthropic": D(180)}
        )
        self.received: list[tuple[str, dict[str, Any]]] = []

    def service(self, family: ModelFamily) -> LedgerService:
        return LedgerService(self.ledger, {family.model_id: FLAT}, {family.model_id: family})

    async def on_message(self, job: WorkerJob, message: dict[str, Any]) -> None:
        self.received.append((job.job_id, message))

    def messages(self, job_id: str | None = None) -> list[dict[str, Any]]:
        return [m for j, m in self.received if job_id is None or j == job_id]

    async def run(self, jobs: list[WorkerJob], launcher: Any = None, **kwargs: Any) -> None:
        await run_jobs(
            jobs,
            launcher=launcher or LocalLauncher(),
            ledger=self.service(jobs[0].family),
            on_message=self.on_message,
            log_dir=self.tmp_path / "logs",
            cwd=REPO_ROOT,
            **kwargs,
        )


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for name in PROVIDER_API_KEY_ENV.values():
        monkeypatch.delenv(name, raising=False)
    h = Host(tmp_path)
    yield h
    h.ledger.close()


class KeyedLauncher(LocalLauncher):
    """A test launcher that hands the worker a key and a wrapped entry point."""

    def __init__(self, code: str, key: str = KEY) -> None:
        self.code, self.key = code, key

    def supports(self, family: ModelFamily) -> bool:
        return True

    def command(self, job: WorkerJob) -> tuple[list[str], dict[str, str]]:
        _, env = super().command(job)
        return [sys.executable, "-c", self.code], {**env, "ANTHROPIC_API_KEY": self.key}


def test_host_refuses_to_start_with_a_provider_key_in_its_environment() -> None:
    with pytest.raises(BenchmarkValidationError, match="OPENAI_API_KEY") as caught:
        refuse_host_keys({"OPENAI_API_KEY": "x-not-a-key"})
    assert "x-not-a-key" not in str(caught.value)
    refuse_host_keys({"PATH": "/bin", "OPENAI_API_KEY": ""})  # an empty variable holds no key


@pytest.mark.parametrize("name", ["REF_API_KEY", "CONTEXT7_API_KEY", "GOOGLE_API_KEY"])
def test_host_refuses_every_provider_key_name(name: str) -> None:
    with pytest.raises(BenchmarkValidationError, match=name):
        refuse_host_keys({name: "x-not-a-key"})


def test_local_launcher_passes_no_provider_key_and_runs_only_scripted_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    monkeypatch.setenv("BENCHMARK_LIVE_PROVIDERS_ENABLED", "1")
    job = _job(tmp_path, [ANSWER])
    argv, env = LocalLauncher().command(job)
    assert argv == [sys.executable, "-m", "benchmarks", "agentic-worker"]
    assert not (set(PAID_KEY_ENVS) | {"BENCHMARK_LIVE_PROVIDERS_ENABLED"}) & set(env)
    assert set(env) <= {"PATH", "HOME", "LANG", "TMPDIR", "XDG_CACHE_HOME", "VIRTUAL_ENV"}
    families = load_model_matrix(MODEL_MATRIX_PATH).model_families
    (anthropic_live_family,) = [f for f in families if f.id == "claude-sonnet-5-5"]
    assert LocalLauncher().supports(anthropic_live_family) is False
    assert LocalLauncher().supports(job.family) is True


def test_local_launcher_passes_no_proxy_or_ca_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A SOCKS ALL_PROXY crashes the Anthropic client; a CA variable swaps the trust store.
    for name in ("HTTPS_PROXY", "http_proxy", "ALL_PROXY", "NO_PROXY", "SSL_CERT_FILE"):
        monkeypatch.setenv(name, "socks5://proxy.invalid:1080")
    monkeypatch.setenv("SSL_CERT_DIR", "/nonexistent")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/nonexistent/ca.pem")
    _, env = LocalLauncher().command(_job(tmp_path, [ANSWER]))
    assert not [
        name
        for name in env
        if name.upper().endswith("_PROXY")
        or name.startswith("SSL_CERT_")
        or name == "REQUESTS_CA_BUNDLE"
    ]


ANTHROPIC_SHAPE = "sk-ant-api03-" + "a" * 90


@pytest.mark.parametrize(
    "text,pattern",
    [
        (ANTHROPIC_SHAPE, "anthropic"),
        ("AIza" + "b" * 35, "google"),
        ("sk-proj-" + "c" * 40, "openai"),
        ("sk-svcacct-" + "c" * 40, "openai"),
        ("sk-" + "c" * 48, "openai"),
        ("q=%20" + ANTHROPIC_SHAPE, "anthropic"),  # URL-encoded: a digit just before the key
        ("7" + ANTHROPIC_SHAPE, "anthropic"),
        ("q=%2B" + "sk-proj-" + "c" * 40, "openai"),
        ("a disk-based cache", None),
        ("task-runner-" + "d" * 40, None),
        ("sk-ant-api03-" + "a" * 20, None),  # too short for any key shape
        ("AIza" + "b" * 36, None),  # a Google key is exactly 39 characters
    ],
)
def test_key_shaped_scan(text: str, pattern: str | None) -> None:
    if pattern:
        with pytest.raises(KeyLeakError, match=f"test frame.*{pattern}|{pattern}.*test frame"):
            assert_no_key_shaped(f"before {text} after", where="test frame")
        with pytest.raises(KeyLeakError) as caught:
            assert_no_key_shaped(text, where="test frame")
        assert text not in str(caught.value)
    else:
        assert_no_key_shaped(f"before {text} after", where="test frame")


def test_random_base64_is_not_key_shaped() -> None:
    import base64
    import random

    data = random.Random(20261107).randbytes(3 * 1024 * 1024)  # the old pattern fired here
    for text in (base64.b64encode(data), base64.urlsafe_b64encode(data)):
        assert_no_key_shaped(text.decode("ascii"), where="random base64")


def _run_python(code: str) -> subprocess.CompletedProcess[bytes]:
    """Run ``code`` as a worker would, holding KEY as its Anthropic key."""
    env = {**os.environ, "ANTHROPIC_API_KEY": KEY}
    return subprocess.run(
        [sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, timeout=60, env=env
    )


def test_protect_output_redacts_the_own_key_on_every_stream() -> None:
    code = (
        "import os, sys\n"
        "from benchmarks.agentic.worker import protect_output\n"
        f"KEY = {KEY!r}\n"
        "ch = protect_output()\n"
        "print(KEY)\n"
        "sys.stderr.write(KEY + '\\n')\n"
        "os.write(2, KEY[:10].encode())\n"
        "os.write(2, KEY[10:].encode() + b'\\n')\n"
        "ch.send({'type': 'done'})\n"
        "ch.close()\n"
    )
    proc = _run_python(code)
    assert proc.returncode == 0, proc.stderr
    assert KEY.encode() not in proc.stderr and proc.stderr.count(b"[redacted]") == 3
    assert _frames(proc.stdout) == [{"type": "done"}]  # the channel holds only the frame


def test_a_worker_loads_no_provider_sdk_before_protecting_its_output() -> None:
    # The modules a worker imports before main_worker calls protect_output.
    code = (
        "import sys\n"
        "import benchmarks.__main__, benchmarks.agentic.worker\n"
        "print(sorted(m for m in ('anthropic', 'openai', 'google.genai') if m in sys.modules))\n"
    )
    proc = _run_python(code)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.decode().strip() == "[]"


def test_protocol_channel_refuses_a_frame_holding_the_own_key() -> None:
    code = (
        "from benchmarks.agentic.protocol import KeyLeakError\n"
        "from benchmarks.agentic.worker import protect_output\n"
        f"KEY = {KEY!r}\n"
        "ch = protect_output()\n"
        "try:\n"
        "    ch.send({'type': 'trajectory', 'answer': 'x' + KEY})\n"
        "except KeyLeakError:\n"
        "    ch.send({'type': 'refused'})\n"
        "ch.close()\n"
    )
    proc = _run_python(code)
    assert proc.returncode == 0, proc.stderr
    assert _frames(proc.stdout) == [{"type": "refused"}] and KEY.encode() not in proc.stdout


async def test_host_logs_and_artifacts_never_hold_a_workers_key(host: Host, tmp_path: Path) -> None:
    code = (
        "import os, sys\n"
        "from benchmarks.agentic import worker\n"
        "real = worker.run_worker\n"
        "async def leaky(channel, reader):\n"
        "    key = os.environ['ANTHROPIC_API_KEY']\n"
        "    print(key)\n"
        "    sys.stderr.write(key + '\\n')\n"
        "    sys.stderr.flush()\n"
        "    os.write(2, key.encode() + b'\\n')\n"
        "    return await real(channel, reader)\n"
        "worker.run_worker = leaky\n"
        "sys.exit(worker.main_worker())\n"
    )
    await host.run([_job(tmp_path, [ANSWER])], launcher=KeyedLauncher(code))
    logs = "".join(p.read_text() for p in (tmp_path / "logs").iterdir())
    assert KEY not in logs and logs.count("[redacted]") == 3
    assert all(KEY not in json.dumps(m) for m in host.messages())
    assert [m["type"] for m in host.messages()][-1] == "done"


async def test_a_worker_redacts_every_provider_secret_it_holds(
    host: Host, tmp_path: Path
) -> None:
    # Two keys plus a secret that has no key shape (a competitor's server key).
    secrets = {
        "ANTHROPIC_API_KEY": KEY,
        "OPENAI_API_KEY": "openai-synthetic-secret-" + "o" * 30,
        "REF_API_KEY": "plain-server-secret-77",
    }

    class SecretsLauncher(KeyedLauncher):
        def command(self, job: WorkerJob) -> tuple[list[str], dict[str, str]]:
            argv, env = super().command(job)
            return argv, {**env, **secrets}

    code = (
        "import os, sys\n"
        "from benchmarks.agentic import worker\n"
        "real = worker.run_worker\n"
        "async def leaky(channel, reader):\n"
        "    for name in ('ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'REF_API_KEY'):\n"
        "        print(os.environ[name])\n"
        "    return await real(channel, reader)\n"
        "worker.run_worker = leaky\n"
        "sys.exit(worker.main_worker())\n"
    )
    await host.run([_job(tmp_path, [ANSWER])], launcher=SecretsLauncher(code))
    logs = "".join(p.read_text() for p in (tmp_path / "logs").iterdir())
    assert not [name for name, value in secrets.items() if value in logs]
    assert logs.count("[redacted]") == 3


async def test_worker_runs_its_job_over_the_channel_and_reports_done(
    host: Host, tmp_path: Path
) -> None:
    await host.run([_job(tmp_path, [ANSWER])])
    messages = host.messages()
    assert [m["type"] for m in messages] == ["system_info", "trajectory", "trajectory", "done"]
    info = messages[0]
    assert info["tools_raw"] == [] and info["index_sha256"] is None
    assert set(info["client_versions"]) == {"anthropic", "openai", "google-genai", "mcp"}
    assert [m["corpus_id"] for m in messages[1:3]] == ["Q1", "Q2"]
    assert [m["attempt"] for m in messages[1:3]] == [1, 1]  # a rerun counts by its attempt
    assert messages[1]["record"]["answer"] == "Scripted answer."
    assert host.ledger.spent("anthropic") == 2 * D("0.0011")  # 1000 in + 100 out at $1/1M
    assert (tmp_path / "logs" / "scripted-anthropic--no-docs.stderr.log").exists()


async def test_worker_exits_with_its_mcp_server_child_still_alive_at_shutdown(
    host: Host, tmp_path: Path
) -> None:
    pid_step = {"tools": [{"name": "pid", "arguments": {}}], "usage": USAGE}
    job = _job(tmp_path, [pid_step, ANSWER], system=FAKE_FETCH, count=1)
    started = time.monotonic()
    await host.run([job])
    assert time.monotonic() - started < 10
    messages = host.messages()
    assert [m["type"] for m in messages] == ["system_info", "trajectory", "done"]
    assert len(messages[0]["tools_raw"]) == 4 and messages[0]["server_info"]["name"]
    result = messages[1]["record"]["tool_calls"][0]["result"]
    server_pid = json.loads(result)["result"]
    with pytest.raises(ProcessLookupError):
        os.kill(server_pid, 0)


async def test_a_crashed_mcp_server_is_reopened_for_the_next_trajectory(
    host: Host, tmp_path: Path
) -> None:
    script = [
        {
            "tools": [{"name": "die", "arguments": {}}],
            "usage": USAGE,
            "prompt_contains": "question 1",
        },
        {
            "tools": [{"name": "pid", "arguments": {}}],
            "usage": USAGE,
            "prompt_contains": "question 2",
        },
        ANSWER,
    ]
    await host.run([_job(tmp_path, script, system=FAKE_FETCH, count=2)])
    messages = host.messages()
    assert [m["type"] for m in messages] == ["system_info", "trajectory", "trajectory", "done"]
    first, second = (m["record"] for m in messages[1:3])
    assert (messages[1]["corpus_id"], first["status"]) == ("Q1", "failed")
    assert first["error"]["category"] == "mcp_protocol_crash"
    assert (messages[2]["corpus_id"], second["status"]) == ("Q2", "succeeded")


async def test_a_trajectory_frame_over_100000_bytes_round_trips_through_a_worker(
    host: Host, tmp_path: Path
) -> None:
    await host.run([_job(tmp_path, [{"text": "x" * 150_000, "usage": USAGE}], count=1)])
    (received_trajectory,) = [m for m in host.messages() if m["type"] == "trajectory"]
    assert received_trajectory["record"]["answer"] == "x" * 150_000


async def test_a_frame_over_the_limit_is_a_harness_error(host: Host, tmp_path: Path) -> None:
    with pytest.raises(FrameTooLarge):
        await host.run(
            [_job(tmp_path, [{"text": "x" * 5_000, "usage": USAGE}])], max_frame_bytes=2_000
        )
    types = [m["type"] for m in host.messages()]
    assert types == ["system_info"]  # nothing for that trajectory reached on_message


def _fake_worker(*frames: dict[str, Any]) -> str:
    """A worker that reads its job, sends ``frames`` (reading each reply), then exits 1."""
    return (
        "import json, sys\n"
        "inp, out = sys.stdin.buffer, sys.stdout.buffer\n"
        "def read():\n"
        "    n = int.from_bytes(inp.read(8), 'big')\n"
        "    return json.loads(inp.read(n))\n"
        "def send(message):\n"
        "    body = json.dumps(message).encode()\n"
        "    out.write(len(body).to_bytes(8, 'big') + body)\n"
        "    out.flush()\n"
        "read()\n"
        f"for frame in {frames!r}:\n"
        "    send(frame)\n"
        "    read()\n"
        "sys.exit(1)\n"
    )


def _reserve_frame(output_bound: int) -> dict[str, Any]:
    return {
        "type": "reserve",
        "provider": "anthropic",
        "model_id": "scripted-1",
        "input_bound": 1000,
        "output_bound": output_bound,
        "kind": "model_call",
    }


class LostLauncher(LocalLauncher):
    """Runs ``code`` for the job named "lost" and a real worker for every other job."""

    def __init__(self, code: str) -> None:
        self.code = code

    def command(self, job: WorkerJob) -> tuple[list[str], dict[str, str]]:
        argv, env = super().command(job)
        return ([sys.executable, "-c", self.code] if job.job_id == "lost" else argv), env


async def test_host_settles_a_lost_workers_unit_at_twice_its_bound_and_frees_the_provider(
    host: Host, tmp_path: Path
) -> None:
    lost = _job(tmp_path, [ANSWER], job_id="lost")
    normal = _job(tmp_path, [ANSWER], count=1, job_id="normal")
    await host.run([lost, normal], launcher=LostLauncher(_fake_worker(_reserve_frame(1024))))
    first_bound = D("0.002024")  # (1000 + 1024) * $1 / 1M
    second_job_actual = D("0.0011")
    assert host.ledger.spent("anthropic") == 2 * first_bound + second_job_actual
    lost_job_messages = host.messages("lost")
    assert {(m["type"], m["reason"]) for m in lost_job_messages} == {("not_run", "worker_lost")}
    assert len(lost_job_messages) == 2
    assert [m["type"] for m in host.messages("normal")][-1] == "done"


@pytest.mark.parametrize(
    "reason,q1_lost",
    [
        ("retries_exhausted", True),  # its rerun is still owed
        ("budget_stop", False),  # final: never listed twice
    ],
)
async def test_only_a_final_discard_keeps_a_cell_off_the_worker_lost_list(
    host: Host, tmp_path: Path, reason: str, q1_lost: bool
) -> None:
    discarded = {
        "type": "discarded",
        "record": {"status": "discarded"},
        "corpus_id": "Q1",
        "repeat": 1,
        "reason": reason,
        "attempt": 1,
    }
    lost = _job(tmp_path, [ANSWER], job_id="lost")
    await host.run([lost], launcher=LostLauncher(_fake_worker(discarded)))
    assert [(m["type"], m["corpus_id"], m["reason"]) for m in host.messages()] == [
        ("discarded", "Q1", reason),
        *([("not_run", "Q1", "worker_lost")] if q1_lost else []),
        ("not_run", "Q2", "worker_lost"),
    ]


async def test_host_never_reserves_less_than_the_familys_output_cap(
    host: Host, tmp_path: Path
) -> None:
    lost = _job(tmp_path, [ANSWER], job_id="lost")
    await host.run([lost], launcher=LostLauncher(_fake_worker(_reserve_frame(0))))
    (reserve,) = [e for e in host.ledger.events("host-test") if e["event"] == "reserve"]
    assert reserve["output_bound"] == 1024  # the family's max_output_tokens, not the worker's 0
    assert host.ledger.spent("anthropic") == 2 * D("0.002024")


async def test_a_worker_that_stalls_mid_frame_is_a_lost_worker(
    host: Host, tmp_path: Path
) -> None:
    stalled = (
        "import sys, time\n"
        "sys.stdout.buffer.write((100).to_bytes(8, 'big'))\n"
        "sys.stdout.buffer.flush()\n"
        "time.sleep(30)\n"
    )
    job = replace(
        _job(tmp_path, [ANSWER]), caps=Caps(trajectory_s=0.2, model_call_timeout_s=0.3)
    )
    started = time.monotonic()
    await host.run([job], launcher=KeyedLauncher(stalled, key=""))
    assert time.monotonic() - started < 10  # the host's read deadline, not the 30 s sleep
    assert {(m["type"], m["reason"]) for m in host.messages()} == {("not_run", "worker_lost")}


async def test_a_key_shape_in_a_worker_log_is_redacted_before_the_run_stops(
    host: Host, tmp_path: Path
) -> None:
    leaked = "sk-ant-api03-" + "z" * 90
    noisy = f"import sys\nsys.stderr.write('debug: token={leaked}\\n')\n"
    with pytest.raises(KeyLeakError, match="anthropic"):
        await host.run([_job(tmp_path, [ANSWER])], launcher=KeyedLauncher(noisy, key=""))
    (log,) = (tmp_path / "logs").iterdir()
    assert log.read_text() == "debug: token=[redacted]\n"


async def test_a_line_logged_just_before_an_error_frame_reaches_the_log(
    host: Host, tmp_path: Path
) -> None:
    # Like the worker's redactor, this worker drains its log after the error frame.
    code = (
        "import json, os, sys, time\n"
        "sys.stdin.buffer.read(int.from_bytes(sys.stdin.buffer.read(8), 'big'))\n"
        "log = os.fdopen(2, 'wb', buffering=1 << 16)\n"
        "log.write(b'last line before the error\\n')\n"
        "body = json.dumps({'type': 'error', 'reason': 'RuntimeError: boom'}).encode()\n"
        "sys.stdout.buffer.write(len(body).to_bytes(8, 'big') + body)\n"
        "sys.stdout.buffer.flush()\n"
        "time.sleep(0.5)\n"
        "log.flush()\n"
    )
    await host.run([_job(tmp_path, [ANSWER])], launcher=KeyedLauncher(code, key=""))
    (log,) = (tmp_path / "logs").iterdir()
    assert log.read_text() == "last line before the error\n"
    assert host.messages()[0] == {"type": "worker_error", "reason": "RuntimeError: boom"}


async def test_every_worker_log_is_scrubbed_when_a_frame_leak_aborts_the_run(
    host: Host, tmp_path: Path
) -> None:
    # Both workers log the key. "leak" then sends it in a frame, which aborts the run;
    # "quiet" is cancelled with the rest. Neither log may keep the key.
    leaked = "sk-ant-api03-" + "z" * 90
    code = (
        "import json, sys, time\n"
        "job = json.loads(sys.stdin.buffer.read(int.from_bytes(sys.stdin.buffer.read(8), 'big')))\n"
        f"sys.stderr.write('debug: {leaked}\\n')\n"
        "sys.stderr.flush()\n"
        "if job['job_id'] == 'leak':\n"
        "    time.sleep(0.5)\n"
        f"    body = json.dumps({{'type': 'system_info', 'note': '{leaked}'}}).encode()\n"
        "    sys.stdout.buffer.write(len(body).to_bytes(8, 'big') + body)\n"
        "    sys.stdout.buffer.flush()\n"
        "time.sleep(30)\n"
    )
    jobs = [_job(tmp_path, [ANSWER], job_id=job_id) for job_id in ("leak", "quiet")]
    with pytest.raises(KeyLeakError):
        await host.run(jobs, launcher=KeyedLauncher(code, key=""))
    logs = sorted((tmp_path / "logs").iterdir())
    assert [log.name for log in logs] == ["leak.stderr.log", "quiet.stderr.log"]
    for log in logs:
        assert log.read_text() == "debug: [redacted]\n"


def _supervisor(job_id: str) -> asyncio.Task[Any] | None:
    """The running supervisor task of ``job_id``, found by its coroutine's frame."""
    for task in asyncio.all_tasks():
        frame = getattr(task.get_coro(), "cr_frame", None)
        if frame is not None and frame.f_code.co_name == "_supervise":
            if frame.f_locals["job"].job_id == job_id:
                return task
    return None


async def test_a_teardown_cancelled_by_a_second_abort_still_scrubs_and_settles(
    host: Host, tmp_path: Path
) -> None:
    # A holds a unit, leaves its process group (as a misbehaving worker may), then leaks a key.
    # The group kill then takes only A's sentinel, a child A left in the group, and A's
    # teardown waits for A itself to exit. A sees its sentinel die, so its teardown has begun,
    # and only then does B leak; B's abort cancels A mid-teardown. If A's teardown ended first,
    # A would never be cancelled and the bounded wait would fail. Every step waits for a
    # signal (a file, or a child's exit) within a bound: no step relies on timing, on how
    # asyncio's Process.wait() treats pipes, or on the platform's child watcher.
    leaked = "sk-ant-api03-" + "z" * 90
    entered, release = tmp_path / "a-in-teardown", tmp_path / "release"
    code = (
        "import json, os, subprocess, sys, time\n"
        "from pathlib import Path\n"
        "inp, out = sys.stdin.buffer, sys.stdout.buffer\n"
        "def read():\n"
        "    return json.loads(inp.read(int.from_bytes(inp.read(8), 'big')))\n"
        "def send(message):\n"
        "    body = json.dumps(message).encode()\n"
        "    out.write(len(body).to_bytes(8, 'big') + body)\n"
        "    out.flush()\n"
        "job = read()\n"
        f"sys.stderr.write('debug: {leaked}\\n')\n"
        "sys.stderr.flush()\n"
        f"leak = {{'type': 'system_info', 'note': '{leaked}'}}\n"
        "def until(done, failure):\n"
        "    deadline = time.monotonic() + 10\n"
        "    while not done():\n"
        "        if time.monotonic() > deadline:\n"
        "            sys.exit(failure)\n"
        "        time.sleep(0.01)\n"
        "if job['job_id'] == 'a':\n"
        f"    send({_reserve_frame(1024)!r})\n"
        "    read()\n"
        "    quiet = {'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL}\n"
        "    sentinel = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],"
        " **quiet)\n"
        "    os.setsid()\n"
        "    send(leak)\n"
        "    until(lambda: sentinel.poll() is not None, 'A: its group was never killed')\n"
        f"    Path({str(entered)!r}).touch()\n"
        f"    until(Path({str(release)!r}).exists, 'A: never released')\n"
        "else:\n"
        f"    until(Path({str(entered)!r}).exists, 'B: A never entered its teardown')\n"
        "    send(leak)\n"
        "    time.sleep(30)\n"
    )
    jobs = [_job(tmp_path, [ANSWER], job_id=job_id) for job_id in ("a", "b")]
    run = asyncio.create_task(host.run(jobs, launcher=KeyedLauncher(code, key="")))
    try:
        async with asyncio.timeout(10):  # bounded: a missed signal fails loudly
            while (a := _supervisor("a")) is None:
                await asyncio.sleep(0.01)
            while not a.cancelling():  # B's abort has reached A, mid-teardown
                await asyncio.sleep(0.01)
    finally:
        release.touch()
        await asyncio.wait([run], timeout=20)
    with pytest.raises(KeyLeakError):
        run.result()
    logs = sorted((tmp_path / "logs").iterdir())
    assert [log.read_text() for log in logs] == ["debug: [redacted]\n"] * 2
    settles = [e for e in host.ledger.events("host-test") if e["event"] == "settle"]
    assert [(s["outcome"], D(s["booked"])) for s in settles] == [
        ("worker_lost", 2 * D("0.002024"))
    ]


# A worker's descendant that holds its log (fd 2) and writes a key once the log is published.
# "stay" remains in the worker's process group; "escape" leaves it with setsid. Bounded waits.
_LATE_WRITER = (
    "import os, sys, time\n"
    "from pathlib import Path\n"
    "mode, signals = sys.argv[1], Path(sys.argv[2])\n"
    "if mode == 'escape':\n"
    "    os.setsid()\n"
    "    (signals / 'escaped').touch()\n"
    "deadline = time.monotonic() + 10\n"
    "while not (signals / 'published').exists():\n"
    "    if time.monotonic() > deadline:\n"
    "        sys.exit(mode + ': the log was never published')\n"
    "    time.sleep(0.01)\n"
    "os.write(2, b'late: sk-ant-api03-' + b'z' * 90 + b'\\n')\n"
    "(signals / (mode + '-wrote')).touch()\n"
)


def _alive(pid: int) -> bool:
    """Whether ``pid`` is a live process; a zombie is dead, whoever has yet to reap it.

    ps on Linux and macOS alike: it shows a zombie as such, and sends no signal at all.
    """
    state = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
    ).stdout.strip()
    return bool(state) and not state.startswith("Z")


@pytest.mark.skipif(not hasattr(os, "waitid"), reason="waitid: Linux; macOS from Python 3.13")
def test_alive_counts_an_unreaped_zombie_as_exited() -> None:
    child = subprocess.Popen([sys.executable, "-c", "pass"])  # this test's own child
    try:
        os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT)  # exited, held unreaped
        assert not _alive(child.pid)
    finally:
        child.wait()  # reaped here, by its parent


async def test_no_descendant_can_write_a_key_into_the_published_log(
    host: Host, tmp_path: Path
) -> None:
    # The worker skips protect_output, starts both late writers, then exits: a lost worker.
    signals = tmp_path / "signals"
    signals.mkdir()
    code = (
        "import subprocess, sys, time\n"
        "from pathlib import Path\n"
        "sys.stdin.buffer.read(int.from_bytes(sys.stdin.buffer.read(8), 'big'))\n"
        "sys.stderr.write('worker: started\\n')\n"
        "sys.stderr.flush()\n"
        f"signals = Path({str(signals)!r})\n"
        "quiet = {'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL}  # fd 2 only\n"
        f"argv = [sys.executable, '-c', {_LATE_WRITER!r}]\n"
        "stay = subprocess.Popen([*argv, 'stay', str(signals)], **quiet)\n"
        "(signals / 'stay.pid').write_text(str(stay.pid))\n"
        "subprocess.Popen([*argv, 'escape', str(signals)], **quiet)\n"
        "deadline = time.monotonic() + 10\n"
        "while not (signals / 'escaped').exists():\n"
        "    if time.monotonic() > deadline:\n"
        "        sys.exit('worker: the escape never left the group')\n"
        "    time.sleep(0.01)\n"
        "sys.exit(1)\n"
    )
    await host.run([_job(tmp_path, [ANSWER])], launcher=KeyedLauncher(code, key=""))
    stay = int((signals / "stay.pid").read_text())
    async with asyncio.timeout(5):  # (a) the group kill took the descendant in the group
        while _alive(stay):
            await asyncio.sleep(0.01)
    (signals / "published").touch()
    async with asyncio.timeout(10):  # (b) the escaped one writes into the unlinked old log
        while not (signals / "escape-wrote").exists():
            await asyncio.sleep(0.01)
    (log,) = (tmp_path / "logs").iterdir()
    assert log.read_text() == "worker: started\n"


@pytest.fixture
def popens(monkeypatch: pytest.MonkeyPatch) -> list[subprocess.Popen[bytes]]:
    """Every subprocess.Popen started during the test, in order, asyncio's own included."""
    created: list[subprocess.Popen[bytes]] = []

    class Recorded(subprocess.Popen):  # type: ignore[type-arg]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr(subprocess, "Popen", Recorded)
    return created


def _child_state(pid: int) -> str:
    """'running', 'zombie' or 'reaped' (or not this process's child); nothing is reaped here.

    ps, not os.waitid(WNOWAIT): macOS has no waitid before Python 3.13. A pid whose parent
    is not this process counts as reaped, so a reused pid is never taken for the child.
    """
    shown = subprocess.run(
        ["ps", "-o", "ppid=,stat=", "-p", str(pid)], capture_output=True, text=True
    ).stdout.split()
    if len(shown) < 2 or int(shown[0]) != os.getpid():
        return "reaped"
    return "zombie" if shown[1].startswith("Z") else "running"


def _holding_worker(signals: Path, *, exit_after: str | None = None) -> str:
    """A worker that reports its pid and group, starts a descendant that leaves the group but
    keeps the host's pipe (fd 1), waits for ``exit_after`` if given, then exits."""
    holder = (
        "import os, sys, time\n"
        "from pathlib import Path\n"
        "signals = Path(sys.argv[1])\n"
        "os.setsid()\n"
        "(signals / 'escaped').touch()\n"
        "deadline = time.monotonic() + 10\n"
        "while not (signals / 'done').exists() and time.monotonic() < deadline:\n"
        "    time.sleep(0.01)\n"
    )
    wait_for = [] if exit_after is None else ["escaped", exit_after]
    return (
        "import os, subprocess, sys, time\n"
        "from pathlib import Path\n"
        "sys.stdin.buffer.read(int.from_bytes(sys.stdin.buffer.read(8), 'big'))\n"
        f"signals = Path({str(signals)!r})\n"
        "quiet = {'stdin': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}  # keeps fd 1\n"
        f"subprocess.Popen([sys.executable, '-c', {holder!r}, str(signals)], **quiet)\n"
        "(signals / 'ids.tmp').write_text(f'{os.getpid()} {os.getpgid(0)}')\n"
        "os.replace(signals / 'ids.tmp', signals / 'ids')\n"
        f"for name in {wait_for or ['escaped']!r}:\n"
        "    deadline = time.monotonic() + 10\n"
        "    while not (signals / name).exists():\n"
        "        if time.monotonic() > deadline:\n"
        "            sys.exit('worker: no ' + name)\n"
        "        time.sleep(0.01)\n"
        "sys.exit(1)\n"
    )


def _guarded_killpg(
    monkeypatch: pytest.MonkeyPatch, signals: Path
) -> list[tuple[int, str, str]]:
    """Record each group kill as (pgid, its leader's state, the worker's state). It is passed
    on only for an unreaped child of this process, whose pid cannot have been reused."""
    real_killpg, calls = os.killpg, []

    def killpg(pgid: int, sig: int) -> None:
        worker = int((signals / "ids").read_text().split()[0])
        state = _child_state(pgid)
        calls.append((pgid, state, _child_state(worker)))
        if pgid > 0 and state != "reaped":
            real_killpg(pgid, sig)

    monkeypatch.setattr(os, "killpg", killpg)
    monkeypatch.setattr(agentic_host, "_EXIT_WAIT_S", 0.5)  # the held pipe delays wait()
    return calls


async def test_teardown_kills_the_anchors_live_group_after_the_worker_is_reaped(
    host: Host,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    popens: list[subprocess.Popen[bytes]],
) -> None:
    # Codex's case: an escaped descendant holds the host's pipe, so the host reads on after
    # the worker exited and was reaped. The group it then kills must still be the anchor's.
    signals = tmp_path / "signals"
    signals.mkdir()
    calls = _guarded_killpg(monkeypatch, signals)
    job = replace(_job(tmp_path, [ANSWER]), caps=Caps(trajectory_s=0.2, model_call_timeout_s=0.3))
    try:
        await host.run([job], launcher=KeyedLauncher(_holding_worker(signals), key=""))
    finally:
        (signals / "done").touch()
    worker, group = map(int, (signals / "ids").read_text().split())
    assert calls == [(group, "running", "reaped")], "the anchor's live group; the worker reaped"
    (anchor,) = [p for p in popens if p.pid == group]
    assert anchor.returncode == -signal.SIGKILL and worker != group


async def test_an_anchor_killed_early_keeps_its_group_id_until_teardown(
    host: Host,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    popens: list[subprocess.Popen[bytes]],
) -> None:
    # Codex's case: the anchor dies early (the test kills the anchor its host started, through
    # its handle), the worker exits and is reaped, and an escaped descendant holds the pipe.
    # Unreaped, the anchor still reserves its pid and group id when teardown kills the group.
    signals = tmp_path / "signals"
    signals.mkdir()
    calls = _guarded_killpg(monkeypatch, signals)
    code = _holding_worker(signals, exit_after="anchor-killed")
    job = replace(_job(tmp_path, [ANSWER]), caps=Caps(trajectory_s=1.0, model_call_timeout_s=0.5))
    run = asyncio.create_task(host.run([job], launcher=KeyedLauncher(code, key="")))
    try:
        async with asyncio.timeout(10):  # bounded: a missed signal fails loudly
            while not (signals / "ids").exists():
                await asyncio.sleep(0.01)
            _, group = map(int, (signals / "ids").read_text().split())
            (anchor,) = [p for p in popens if p.pid == group]
            anchor.send_signal(signal.SIGKILL)
            while _child_state(group) == "running":
                await asyncio.sleep(0.01)
        (signals / "anchor-killed").touch()
        await asyncio.wait_for(run, 20)
    finally:
        (signals / "done").touch()
        if not run.done():
            run.cancel()
            await asyncio.gather(run, return_exceptions=True)
    assert calls == [(group, "zombie", "reaped")], "an unreaped anchor; the worker reaped"
    assert anchor.returncode == -signal.SIGKILL  # reaped by the teardown, after its kill


async def test_no_anchor_outlives_its_job(
    host: Host, tmp_path: Path, popens: list[subprocess.Popen[bytes]]
) -> None:
    await host.run([_job(tmp_path, [ANSWER])])
    assert len(popens) == 2 and all(p.returncode is not None for p in popens)


class _Missing(LocalLauncher):
    def command(self, job: WorkerJob) -> tuple[list[str], dict[str, str]]:
        return [str(Path(job.family.raw["script"]).parent / "missing-worker")], {}


async def test_no_anchor_outlives_a_worker_that_never_started(
    host: Host, tmp_path: Path, popens: list[subprocess.Popen[bytes]]
) -> None:
    with pytest.raises(FileNotFoundError):
        await host.run([_job(tmp_path, [ANSWER])], launcher=_Missing())
    assert len(popens) == 1 and popens[0].returncode is not None


class _Exited:
    """A worker or anchor that has exited: wait() returns at once.

    Its pid is always a throwaway process the test created, never 0 (os.killpg(0) is the
    caller's own group), so nothing else can be signalled through it, even by a bug.
    """

    def __init__(self, pid: int) -> None:
        self.pid = pid

    async def wait(self) -> int:
        return 0


async def test_teardown_of_an_anchor_that_died_early(host: Host, tmp_path: Path) -> None:
    # Its group holds only a zombie, and macOS answers a group kill with EPERM.
    zombie = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    deadline = time.monotonic() + 10
    while _child_state(zombie.pid) != "zombie":  # exited, left unreaped
        assert time.monotonic() < deadline, "the throwaway child never exited"
        time.sleep(0.01)
    try:
        log = tmp_path / "exited.stderr.log"
        log.write_text("worker: done\n", encoding="utf-8")
        service = host.service(_job(tmp_path, [ANSWER]).family)
        worker, anchor = _Exited(zombie.pid), zombie  # the anchor died early
        pattern = await agentic_host._teardown(worker, anchor, log, service, None)  # type: ignore[arg-type]
        assert pattern is None and log.read_text() == "worker: done\n"
    finally:
        zombie.wait()


class _Unending(_Exited):
    """A worker whose wait() never returns, as if it survived its kill."""

    async def wait(self) -> int:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


async def test_a_worker_that_never_exits_cannot_hang_its_teardown(
    host: Host, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agentic_host, "_EXIT_WAIT_S", 0.1)
    group = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )
    family = _job(tmp_path, [ANSWER]).family
    service = host.service(family)
    unit = await service.reserve(
        provider="anthropic", model_id="scripted-1", input_bound=1000, output_bound=1024
    )
    log = tmp_path / "never-exits.stderr.log"
    log.write_text("debug: sk-ant-api03-" + "z" * 90 + "\n", encoding="utf-8")
    async with asyncio.timeout(5):  # the bound under test is 0.1 s
        worker, anchor = _Unending(group.pid), group
        pattern = await agentic_host._teardown(worker, anchor, log, service, unit)  # type: ignore[arg-type]
    assert group.wait(timeout=5) == -signal.SIGKILL
    assert pattern is not None and log.read_text() == "debug: [redacted]\n"
    settles = [e for e in host.ledger.events("host-test") if e["event"] == "settle"]
    assert [(s["outcome"], D(s["booked"])) for s in settles] == [
        ("worker_lost", 2 * D("0.002024"))
    ]


async def test_a_failed_log_publish_still_settles_and_leaves_no_key(
    host: Host, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    group = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )
    real_write = Path.write_bytes

    def full_disk(self: Path, data: bytes) -> int:
        if self.name.endswith(".tmp"):  # a partial copy, then the disk is full
            real_write(self, data[:10])
            raise OSError(28, "No space left on device")
        return real_write(self, data)

    monkeypatch.setattr(Path, "write_bytes", full_disk)
    service = host.service(_job(tmp_path, [ANSWER]).family)
    unit = await service.reserve(
        provider="anthropic", model_id="scripted-1", input_bound=1000, output_bound=1024
    )
    log = tmp_path / "full.stderr.log"
    log.write_text("debug: sk-ant-api03-" + "z" * 90 + "\n", encoding="utf-8")
    retained = log.open("ab")  # a writer that kept its descriptor through the teardown
    try:
        with pytest.raises(OSError, match="No space left"):
            worker, anchor = _Exited(group.pid), group
            await agentic_host._teardown(worker, anchor, log, service, unit)  # type: ignore[arg-type]
        retained.write(b"late: sk-ant-api03-" + b"z" * 90 + b"\n")
    finally:
        retained.close()
        group.wait(timeout=5)
    settles = [e for e in host.ledger.events("host-test") if e["event"] == "settle"]
    assert [(s["outcome"], D(s["booked"])) for s in settles] == [
        ("worker_lost", 2 * D("0.002024"))
    ]
    assert [path.name for path in tmp_path.glob("*.tmp")] == []
    assert not log.exists() or "sk-ant-api03-" not in log.read_text()


async def test_host_aborts_the_run_when_a_worker_frame_holds_a_key_shaped_string(
    host: Host, tmp_path: Path
) -> None:
    leaky_worker = (
        "import json, sys\n"
        "body = json.dumps({'type': 'system_info', 'note': 'sk-ant-api03-' + 'a' * 90}).encode()\n"
        "sys.stdout.buffer.write(len(body).to_bytes(8, 'big') + body)\n"
        "sys.stdout.buffer.flush()\n"
        "import time; time.sleep(30)\n"
    )
    started = time.monotonic()
    with pytest.raises(KeyLeakError):
        await host.run([_job(tmp_path, [ANSWER])], launcher=KeyedLauncher(leaky_worker, key=""))
    assert time.monotonic() - started < 10  # the sleeping worker was killed
    assert host.messages() == []


async def test_budget_stop_marks_the_rest_of_the_job_not_run(tmp_path: Path) -> None:
    host = Host(tmp_path, caps={"anthropic": D("0")})
    try:
        await host.run([_job(tmp_path, [ANSWER], count=3)])
    finally:
        host.ledger.close()
    messages = host.messages()
    assert [(m["type"], m.get("reason")) for m in messages] == [
        ("system_info", None),
        ("discarded", "budget_stop"),
        ("not_run", "budget_stop"),
        ("not_run", "budget_stop"),
        ("done", None),
    ]
    assert [m["corpus_id"] for m in messages[1:4]] == ["Q1", "Q2", "Q3"]


async def test_retries_exhausted_trajectory_is_rerun_at_most_twice(
    host: Host, tmp_path: Path
) -> None:
    limited = {"error": {"kind": "rate_limited", "status": 429, "retry_after_s": 0}}
    await host.run([_job(tmp_path, [limited] * 5, count=1)])
    messages = host.messages()
    discarded = [m for m in messages if m["type"] == "discarded"]
    assert [d["attempt"] for d in discarded] == [1, 2, 3]
    assert {d["reason"] for d in discarded} == {"retries_exhausted"}
    assert [(m["type"], m.get("reason")) for m in messages[-2:]] == [
        ("not_run", "retries_exhausted"),
        ("done", None),
    ]
    assert host.ledger.spent("anthropic") == 0  # every 429 books zero
