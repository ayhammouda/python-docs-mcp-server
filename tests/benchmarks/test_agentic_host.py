"""Keyless host, redacted workers and length-prefixed frames (design §7.3; ruling B2, B3).

Workers are real subprocesses; models are scripted, so nothing reaches a network host.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import traceback
from collections.abc import Sequence
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path
from typing import Any, BinaryIO
from unittest.mock import AsyncMock, Mock

import pytest
import yaml

import benchmarks.agentic.host as agentic_host
import benchmarks.agentic.worker as worker
from benchmarks.adapters.guard import PAID_KEY_ENVS, PROVIDER_API_KEY_ENV
from benchmarks.agentic.bridge import system_spec_from_raw
from benchmarks.agentic.host import LocalLauncher, WorkerJob, refuse_host_keys, run_jobs
from benchmarks.agentic.ledger import Ledger, LedgerService, ModelPrice, Rates, Tier
from benchmarks.agentic.loop import HOSTED_CAPS, Caps
from benchmarks.agentic.protocol import (
    WORKER_LEAK_REASON,
    FrameTooLarge,
    KeyLeakError,
    assert_no_key_shaped,
    encode_frame,
    read_frame_fd,
    redact_key_shaped,
)
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
        ("bn" + "AIza" + "b" * 35, None),  # a base64 run containing a key-shaped suffix
        ("base64u0020" + "sk-proj-" + "c" * 40, None),
        ("\\" + "x" * 10 + "AIza" + "g" * 35, None),  # beyond the longest escape
        (r"\x20" + "AIza" + "g" * 36, None),  # the end boundary still applies
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


@pytest.mark.parametrize(
    "escape",
    [
        r"\b",
        r"\f",
        r"\n",
        r"\r",
        r"\t",
        r"\u000A",
        r"\\u0020",
        r"\x20",
        r"\0",
        r"\07",
        r"\377",
        r"\U00000020",
        r"\N{SPACE}",
        r"\a",
        r"\v",
        r"\e",
        r"\N",
        r"\_",
        r"\L",
        r"\P",
        r"\\\\x00",
        "\\" * 64 + "U00000020",
    ],
)
def test_structured_escape_is_a_key_boundary_in_arbitrary_text(escape: str) -> None:
    key = "sk-proj-" + "k" * 40
    prefix = "arbitrary text, no JSON required " + escape
    for value in (prefix + key, (prefix + key).encode()):
        with pytest.raises(KeyLeakError, match="openai"):
            assert_no_key_shaped(value, where="test output")
    expected = (prefix + "[redacted]").encode()
    assert redact_key_shaped((prefix + key).encode()) == (expected, "openai")


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
        "async def leaky(channel, reader, *args, **kwargs):\n"
        "    key = os.environ['ANTHROPIC_API_KEY']\n"
        "    print(key)\n"
        "    sys.stderr.write(key + '\\n')\n"
        "    sys.stderr.flush()\n"
        "    os.write(2, key.encode() + b'\\n')\n"
        "    return await real(channel, reader, *args, **kwargs)\n"
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
        "async def leaky(channel, reader, *args, **kwargs):\n"
        "    for name in ('ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'REF_API_KEY'):\n"
        "        print(os.environ[name])\n"
        "    return await real(channel, reader, *args, **kwargs)\n"
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


@pytest.fixture
async def worker_stream(monkeypatch: pytest.MonkeyPatch) -> asyncio.StreamReader:
    """Feed wire frames through supervision without creating or signalling any processes."""
    reader = asyncio.StreamReader()
    proc = Mock(stdout=reader, wait=AsyncMock())
    proc.stdin.drain = AsyncMock()
    monkeypatch.setattr(agentic_host, "_kill_group", Mock())
    monkeypatch.setattr(agentic_host, "_start_anchor", Mock())
    monkeypatch.setattr(agentic_host, "_reap", AsyncMock())
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    return reader


@pytest.mark.parametrize(
    "kind,fields",
    [
        ("discarded", {"corpus_id": "../other", "repeat": 1, "attempt": 1}),
        ("trajectory", {"corpus_id": "Q1", "repeat": 2, "attempt": 1}),
        ("trajectory", {"corpus_id": "Q1", "repeat": True, "attempt": 1}),
        ("not_run", {"corpus_id": "Q1", "repeat": 1.0}),
        ("trajectory", {"corpus_id": "Q1", "repeat": 1}),
        ("discarded", {"corpus_id": "Q1", "repeat": 1, "attempt": 0}),
        ("trajectory", {"corpus_id": "Q1", "repeat": 1, "attempt": 4}),
        ("discarded", {"corpus_id": "Q1", "repeat": 1, "attempt": True}),
        ("trajectory", {"corpus_id": "Q1", "repeat": 1, "attempt": 1.0}),
        ("not_run", {"corpus_id": "Q1", "repeat": 1, "attempt": None}),
    ],
)
async def test_unassigned_or_invalid_report_never_reaches_callback(
    host: Host,
    tmp_path: Path,
    worker_stream: asyncio.StreamReader,
    kind: str,
    fields: dict[str, Any],
) -> None:
    worker_stream.feed_data(encode_frame({"type": kind, "reason": "budget_stop", **fields}))
    worker_stream.feed_eof()
    with pytest.raises(BenchmarkValidationError):
        await host.run([_job(tmp_path, [], count=1)])
    assert host.received == []


@pytest.mark.parametrize("raw_only", [False, True])
async def test_host_refuses_an_escaped_key_in_a_worker_frame(
    host: Host, tmp_path: Path, worker_stream: asyncio.StreamReader, raw_only: bool
) -> None:
    key = "AIza" + "g" * 35
    if raw_only:  # The escaped A hides the key's end boundary in the decoded value.
        body = ('{"type":"done","extra":"' + key + r'\u0041"}').encode()
        worker_stream.feed_data(len(body).to_bytes(8, "big") + body)
    else:
        worker_stream.feed_data(encode_frame({"type": "done", "extra": [{"\n" + key: True}]}))
    worker_stream.feed_eof()
    with pytest.raises(KeyLeakError) as caught:
        await host.run([_job(tmp_path, [], count=1)])
    assert key not in str(caught.value)
    assert host.received == []


def test_worker_checks_decoded_fields_before_sending(monkeypatch: pytest.MonkeyPatch) -> None:
    write = Mock()
    monkeypatch.setattr(worker, "_write_all", write)
    key = "AIza" + "g" * 35
    channel = worker.ProtocolChannel(1, (), 2, Mock(), (Mock(), Mock()))
    with pytest.raises(KeyLeakError) as caught:
        channel.send({"answer": ["\n" + key]})
    assert key not in str(caught.value)
    write.assert_not_called()


async def test_assigned_reports_reach_callback(
    host: Host, tmp_path: Path, worker_stream: asyncio.StreamReader
) -> None:
    job = _job(tmp_path, [], count=3)
    frames = [
        {"type": "trajectory", "corpus_id": "Q1", "repeat": 1, "attempt": 1},
        {
            "type": "discarded",
            "corpus_id": "Q2",
            "repeat": 1,
            "attempt": 1,
            "reason": "budget_stop",
        },
        {"type": "not_run", "corpus_id": "Q3", "repeat": 1, "reason": "budget_stop"},
        {"type": "done"},
    ]
    for frame in frames:
        worker_stream.feed_data(encode_frame(frame))
    worker_stream.feed_eof()
    await host.run([job])
    assert host.messages() == frames


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


@pytest.mark.parametrize("indent", [None, 2], ids=["compact", "multiline"])
@pytest.mark.parametrize("bytes_repr", [False, True])
async def test_structured_worker_stderr_is_redacted_on_publication(
    host: Host, tmp_path: Path, indent: int | None, bytes_repr: bool
) -> None:
    key = "AIza" + "g" * 35
    detail = repr(b"\x00" + key.encode()) if bytes_repr else "failure\n" + key
    line = json.dumps({"detail": detail}, indent=indent) + "\n"
    code = (
        "import sys\nfrom benchmarks.agentic.worker import protect_output\n"
        "channel = protect_output()\n"
        f"sys.stderr.write({line!r})\nchannel.close()\n"
    )
    with pytest.raises(KeyLeakError, match="google") as caught:
        await host.run([_job(tmp_path, [], job_id="lost")], launcher=KeyedLauncher(code, ""))
    assert key not in str(caught.value)
    expected = line.replace(key, "[redacted]").encode()
    assert (tmp_path / "logs/lost.stderr.log").read_bytes() == expected


@pytest.mark.parametrize("read_only", [False, True])
async def test_raw_worker_stderr_is_private_before_publication(
    host: Host, tmp_path: Path, read_only: bool
) -> None:
    if read_only and os.geteuid() == 0:
        pytest.skip("the read-only directory fixture requires unprivileged permissions")
    log_dir = tmp_path / "logs"
    log = log_dir / "lost.stderr.log"
    links = tmp_path / "stderr-link-count"
    code = (
        "import os\nfrom pathlib import Path\n"
        f"os.write(2, {SYNTHETIC_KEY.encode()!r})\n"
        f"Path({str(links)!r}).write_text(str(os.fstat(2).st_nlink))\n"
        + _fake_worker(_reserve_frame(1024))
    )
    visible_at_stop = []

    class Launcher(KeyedLauncher):
        async def stop_worker(self, job: WorkerJob) -> None:
            visible_at_stop.extend(log_dir.iterdir())
            if read_only:
                log_dir.chmod(0o500)

    try:
        with pytest.raises(KeyLeakError, match="anthropic") as error:
            await host.run([_job(tmp_path, [ANSWER], job_id="lost")], launcher=Launcher(code, ""))
    finally:
        log_dir.chmod(0o700)
    assert host.ledger.spent("anthropic") == 2 * D("0.002024")
    assert SYNTHETIC_KEY not in "".join(traceback.format_exception(error.value))
    assert not visible_at_stop
    assert links.read_text() == "0"
    if read_only:
        assert isinstance(error.value.__cause__, PermissionError)
        assert not log.exists()
    else:
        assert log.read_text() == "[redacted]"


async def test_worker_log_publication_does_not_follow_path_collisions(
    host: Host, tmp_path: Path
) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log = log_dir / "lost.stderr.log"
    target = tmp_path / "other-artifact"
    target.write_bytes(b"keep me")
    log.symlink_to(target)
    legacy_stage = log_dir / (log.name + ".tmp")
    legacy_stage.symlink_to(target)
    code = f"import os\nos.write(2, {SYNTHETIC_KEY.encode()!r})"
    with pytest.raises(KeyLeakError):
        await host.run([_job(tmp_path, [], job_id="lost")], launcher=KeyedLauncher(code, ""))
    assert target.read_bytes() == b"keep me"
    assert legacy_stage.is_symlink()
    assert not log.is_symlink() and log.read_text() == "[redacted]"


async def test_worker_log_redaction_cannot_expose_an_adjacent_key(
    host: Host, tmp_path: Path
) -> None:
    # The Google key acquires a token boundary when the adjacent Anthropic key is removed.
    google = "AIza" + "g" * 35
    code = f"import os\nos.write(2, {(google + SYNTHETIC_KEY).encode()!r})"
    with pytest.raises(KeyLeakError):
        await host.run([_job(tmp_path, [], job_id="lost")], launcher=KeyedLauncher(code, ""))
    assert google not in (tmp_path / "logs" / "lost.stderr.log").read_text()


async def test_oversized_worker_log_is_rejected_before_reading(
    host: Host, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _job(tmp_path, [], job_id="lost")
    code = (
        f"import os\nos.ftruncate(2, {agentic_host._MAX_LOG_BYTES + 1})\n"
        + _fake_worker(_reserve_frame(1024))
    )
    pread = Mock(side_effect=AssertionError("oversized log read"))
    monkeypatch.setattr(os, "pread", pread)
    with pytest.raises(BenchmarkValidationError, match="scan limit") as error:
        await host.run([job], launcher=KeyedLauncher(code, ""))
    assert "lost.stderr.log" in "".join(traceback.format_exception(error.value))
    pread.assert_not_called()
    assert not list((tmp_path / "logs").iterdir())
    assert host.ledger.spent("anthropic") == 2 * D("0.002024")


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


def _worker_log(data: bytes) -> BinaryIO:
    capture = tempfile.TemporaryFile(buffering=0)
    capture.write(data)
    return capture


async def test_teardown_of_an_anchor_that_died_early(host: Host, tmp_path: Path) -> None:
    # Its group holds only a zombie, and macOS answers a group kill with EPERM.
    zombie = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    deadline = time.monotonic() + 10
    while _child_state(zombie.pid) != "zombie":  # exited, left unreaped
        assert time.monotonic() < deadline, "the throwaway child never exited"
        time.sleep(0.01)
    try:
        log = tmp_path / "exited.stderr.log"
        capture = _worker_log(b"worker: done\n")
        service = host.service(_job(tmp_path, [ANSWER]).family)
        worker, anchor = _Exited(zombie.pid), zombie  # the anchor died early
        pattern = await agentic_host._teardown(worker, anchor, log, service, None, capture)  # type: ignore[arg-type]
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
    capture = _worker_log(f"debug: {SYNTHETIC_KEY}\n".encode())
    async with asyncio.timeout(5):  # the bound under test is 0.1 s
        worker, anchor = _Unending(group.pid), group
        with pytest.raises(KeyLeakError, match="anthropic"):
            await agentic_host._teardown(worker, anchor, log, service, unit, capture)  # type: ignore[arg-type]
    assert group.wait(timeout=5) == -signal.SIGKILL
    assert log.read_text() == "debug: [redacted]\n"
    settles = [e for e in host.ledger.events("host-test") if e["event"] == "settle"]
    assert [(s["outcome"], D(s["booked"])) for s in settles] == [
        ("worker_lost", 2 * D("0.002024"))
    ]


@pytest.mark.parametrize("failure", ["stage", "write", "close", "replace"])
async def test_a_failed_log_publish_still_settles_and_leaves_no_key(
    host: Host, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    group = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )
    create_stage = tempfile.NamedTemporaryFile
    storage_error = OSError(28, "No space left on device")

    def fail(*args: Any) -> None:
        raise storage_error

    def full_disk(**kwargs: Any) -> Any:
        if failure == "stage":
            fail()
        stage = create_stage(**kwargs)
        write = stage.write
        close = stage.file.close

        def partial_write(data: bytes) -> None:
            assert SYNTHETIC_KEY.encode() not in data
            write(data[:10])
            fail()

        def delayed_close() -> None:
            if not stage.file.closed:
                close()
                fail()

        if failure == "write":
            stage.write = partial_write
        elif failure == "close":
            stage.file.close = delayed_close
        return stage

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", full_disk)
    if failure == "replace":
        monkeypatch.setattr(os, "replace", fail)
    service = host.service(_job(tmp_path, [ANSWER]).family)
    unit = await service.reserve(
        provider="anthropic", model_id="scripted-1", input_bound=1000, output_bound=1024
    )
    log = tmp_path / "full.stderr.log"
    capture = _worker_log(f"debug: {SYNTHETIC_KEY}\n".encode())
    # A writer that kept its descriptor through teardown.
    retained = os.fdopen(os.dup(capture.fileno()), "wb")
    try:
        with pytest.raises(KeyLeakError, match="anthropic") as error:
            worker, anchor = _Exited(group.pid), group
            await agentic_host._teardown(worker, anchor, log, service, unit, capture)  # type: ignore[arg-type]
        assert error.value.__cause__ is storage_error
        trace = "".join(traceback.format_exception(error.value))
        assert "No space left" in trace and SYNTHETIC_KEY not in trace
        retained.write(b"late: sk-ant-api03-" + b"z" * 90 + b"\n")
    finally:
        retained.close()
        group.wait(timeout=5)
    settles = [e for e in host.ledger.events("host-test") if e["event"] == "settle"]
    assert [(s["outcome"], D(s["booked"])) for s in settles] == [
        ("worker_lost", 2 * D("0.002024"))
    ]
    assert [path.name for path in tmp_path.glob("*.tmp")] == []
    assert not log.exists()


def test_cli_reports_leak_and_storage_failure_after_worker_reservation(
    host: Host, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from benchmarks.__main__ import main

    job = _job(tmp_path, [], job_id="lost")
    code = (
        f"import os\nos.write(2, {('private stderr: ' + SYNTHETIC_KEY).encode()!r})\n"
        + _fake_worker(_reserve_frame(1024))
    )

    def run(*args: Any, **kwargs: Any) -> None:
        asyncio.run(host.run([job], launcher=LostLauncher(code)))

    monkeypatch.setattr("benchmarks.agentic.run.run_agentic", run)
    monkeypatch.setattr(os, "replace", Mock(side_effect=PermissionError("artifact inaccessible")))
    with pytest.raises(SystemExit) as stopped:
        main(["agentic-run", "--corpus", "unused", "--out", str(tmp_path)])
    assert stopped.value.code == 3
    output = capsys.readouterr()
    assert not output.out and "anthropic" in output.err and "artifact inaccessible" in output.err
    assert SYNTHETIC_KEY not in output.err and "private stderr" not in output.err
    assert not list((tmp_path / "logs").iterdir())
    settles = [e for e in host.ledger.events("host-test") if e["event"] == "settle"]
    assert [(e["outcome"], D(e["booked"])) for e in settles] == [("worker_lost", 2 * D("0.002024"))]


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


def test_system_info_records_the_fetch_extraction_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch = system_spec_from_raw(
        {"id": "docs-fetch", "adapter": "mcp-stdio", "command": ["mcp-server-fetch"]}
    )
    monkeypatch.setattr(worker.shutil, "which", lambda name: None)
    assert worker._system_info(fetch, "anthropic", None)["fetch_extraction"] == "pure-python"
    monkeypatch.setattr(worker.shutil, "which", lambda name: "/usr/bin/node")
    assert worker._system_info(fetch, "anthropic", None)["fetch_extraction"] == "readability-js"
    assert worker._system_info(NO_DOCS, "anthropic", None)["fetch_extraction"] is None


# --- the launcher seam: prepare, key bootstrap, stop worker, close ------------------------------

SYNTHETIC_KEY = "sk-ant-api03-" + "a" * 90


class RecordingLauncher:
    """Wraps a launcher and logs every seam call to a shared list."""

    def __init__(
        self,
        inner: Any = None,
        *,
        events: list[Any] | None = None,
        bootstrap: dict[str, Any] | None = None,
        prepare_error: Exception | None = None,
        stop_error: Exception | None = None,
        problems: Sequence[str] = (),
    ) -> None:
        self.inner = inner or LocalLauncher()
        self.events = [] if events is None else events
        self.bootstrap, self.prepare_error, self.stop_error = bootstrap, prepare_error, stop_error
        self.problems = list(problems)

    def supports(self, family: ModelFamily) -> bool:
        return self.inner.supports(family)

    def command(self, job: WorkerJob) -> tuple[list[str], dict[str, str]]:
        self.events.append(("command", job.job_id))
        return self.inner.command(job)

    def bootstrap_frame(self, job: WorkerJob) -> dict[str, Any] | None:
        self.events.append(("bootstrap", job.job_id))
        return None if self.bootstrap is None else dict(self.bootstrap)

    async def prepare(self, jobs: Sequence[WorkerJob]) -> None:
        self.events.append(("prepare", [job.job_id for job in jobs]))
        if self.prepare_error is not None:
            raise self.prepare_error

    async def stop_worker(self, job: WorkerJob) -> None:
        self.events.append(f"stop_worker:{job.job_id}")
        if self.stop_error is not None:
            raise self.stop_error

    async def close(self) -> list[str]:
        self.events.append(("close",))
        return self.problems


def test_local_launcher_seam_defaults(tmp_path: Path) -> None:
    job = _job(tmp_path, [ANSWER])
    launcher = LocalLauncher()
    assert launcher.bootstrap_frame(job) is None

    async def seam() -> list[str]:
        await launcher.prepare([job])
        await launcher.stop_worker(job)
        return await launcher.close()

    assert asyncio.run(seam()) == []


async def test_run_jobs_prepares_every_job_before_any_worker_starts(
    host: Host, tmp_path: Path
) -> None:
    jobs = [_job(tmp_path, [ANSWER], count=1, job_id=job_id) for job_id in ("a", "b")]
    launcher = RecordingLauncher()
    await host.run(jobs, launcher=launcher)
    assert launcher.events[0] == ("prepare", ["a", "b"])
    assert {e for e in launcher.events if isinstance(e, tuple) and e[0] == "command"} == {
        ("command", "a"),
        ("command", "b"),
    }
    assert launcher.events[-1] == ("close",)


async def test_a_failed_prepare_starts_no_worker_and_still_closes(
    host: Host, tmp_path: Path
) -> None:
    launcher = RecordingLauncher(prepare_error=BenchmarkValidationError("probe failed"))
    with pytest.raises(BenchmarkValidationError, match="probe failed"):
        await host.run([_job(tmp_path, [ANSWER])], launcher=launcher)
    assert [e for e in launcher.events if e[0] in ("command", "bootstrap")] == []
    assert launcher.events[-1] == ("close",)


async def test_supervise_writes_the_bootstrap_before_the_job_frame(
    host: Host, tmp_path: Path
) -> None:
    code = (
        "import json, sys\n"
        "inp, out = sys.stdin.buffer, sys.stdout.buffer\n"
        "def read():\n"
        "    return json.loads(inp.read(int.from_bytes(inp.read(8), 'big')))\n"
        "types = [read()['type'], read()['type']]\n"
        "body = json.dumps({'type': 'seen', 'types': types}).encode()\n"
        "out.write(len(body).to_bytes(8, 'big') + body)\n"
        "out.flush()\n"
    )
    launcher = RecordingLauncher(
        LostLauncher(code), bootstrap={"type": "bootstrap", "env": {}}
    )
    await host.run([_job(tmp_path, [ANSWER], count=1, job_id="lost")], launcher=launcher)
    seen = [m for m in host.messages("lost") if m["type"] == "seen"]
    assert seen == [{"type": "seen", "types": ["bootstrap", "job"]}]


async def test_host_environ_is_unchanged_after_a_launch(host: Host, tmp_path: Path) -> None:
    # A real worker takes the key from its bootstrap frame; the host never holds it.
    before = dict(os.environ)
    bootstrap = {"type": "bootstrap", "env": {"ANTHROPIC_API_KEY": SYNTHETIC_KEY}}
    await host.run(
        [_job(tmp_path, [ANSWER], count=1)], launcher=RecordingLauncher(bootstrap=bootstrap)
    )
    assert dict(os.environ) == before
    refuse_host_keys(os.environ)
    assert [m["type"] for m in host.messages()] == ["system_info", "trajectory", "done"]
    for path in (tmp_path / "logs").iterdir():
        assert SYNTHETIC_KEY not in path.read_text()


def test_bootstrap_key_is_redacted_from_worker_output() -> None:
    code = (
        "import os, sys\n"
        "from benchmarks.agentic.protocol import read_frame_fd\n"
        "from benchmarks.agentic.worker import apply_bootstrap, protect_output\n"
        "apply_bootstrap(read_frame_fd(0))\n"
        "ch = protect_output()\n"
        "key = os.environ['ANTHROPIC_API_KEY']\n"
        "print(key)\n"
        "sys.stderr.write(key + '\\n')\n"
        "os.write(2, key.encode() + b'\\n')\n"
        "ch.send({'type': 'done'})\n"
        "ch.close()\n"
    )
    env = {k: v for k, v in os.environ.items() if k not in PROVIDER_API_KEY_ENV.values()}
    bootstrap = {"type": "bootstrap", "env": {"ANTHROPIC_API_KEY": SYNTHETIC_KEY}}
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        input=encode_frame(bootstrap),
        capture_output=True,
        timeout=60,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert SYNTHETIC_KEY.encode() not in proc.stdout + proc.stderr
    assert proc.stderr.count(b"[redacted]") == 3
    assert _frames(proc.stdout) == [{"type": "done"}]


class _Channel:
    """A stand-in ProtocolChannel that keeps every frame sent."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send(self, message: dict[str, Any]) -> None:
        self.sent.append(message)


async def test_worker_refuses_a_live_job_without_its_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import benchmarks.agentic.clients as clients

    def never(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("make_client must not be called")

    monkeypatch.setattr(clients, "make_client", never)
    family = {f.id: f for f in load_model_matrix(MODEL_MATRIX_PATH).model_families}[
        "claude-sonnet-5-5"
    ]
    job = {
        "type": "job",
        "job_id": "claude-sonnet-5-5--no-docs",
        "family": family.raw,
        "system": NO_DOCS.raw,
        "trajectories": [{"corpus_id": "Q1", "prompt": "q", "repeat": 1}],
        "caps": asdict(HOSTED_CAPS),
    }
    channel = _Channel()
    code = await worker.run_worker(channel, asyncio.StreamReader(), job)  # type: ignore[arg-type]
    assert code == 2
    assert [m["type"] for m in channel.sent] == ["error"]
    assert "bootstrap" in channel.sent[0]["reason"]


@pytest.mark.parametrize(
    "env",
    [
        {"HTTPS_PROXY": "http://elsewhere.invalid:3128"},
        {"ANTHROPIC_API_KEY": "not-a-real-key-1", "OPENAI_API_KEY": "not-a-real-key-2"},
        {"REF_API_KEY": "not-a-real-key-3"},
    ],
)
def test_a_bootstrap_with_a_foreign_or_second_variable_is_refused(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str]
) -> None:
    for name in env:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(BenchmarkValidationError) as caught:
        worker.apply_bootstrap({"type": "bootstrap", "env": env})
    assert all(name in str(caught.value) for name in env)
    assert "not-a-real-key" not in str(caught.value) and "elsewhere" not in str(caught.value)
    assert all(name not in os.environ for name in env)


def test_read_frame_fd_reads_one_frame_and_leaves_the_next(tmp_path: Path) -> None:
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, encode_frame({"type": "bootstrap"}) + encode_frame({"type": "job"}))
        os.close(write_fd)
        assert read_frame_fd(read_fd) == {"type": "bootstrap"}
        assert read_frame_fd(read_fd) == {"type": "job"}
        assert read_frame_fd(read_fd) is None
    finally:
        os.close(read_fd)
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, (10**9).to_bytes(8, "big"))
        os.close(write_fd)
        with pytest.raises(FrameTooLarge):
            read_frame_fd(read_fd, limit=1000)
    finally:
        os.close(read_fd)


def _settles_recorded(service: LedgerService, events: list[Any]) -> None:
    real = service.settle

    async def settle(unit_id: str, **kwargs: Any) -> None:
        events.append(f"settle:{kwargs['outcome']}")
        await real(unit_id, **kwargs)

    service.settle = settle  # type: ignore[method-assign]


async def _run_lost(
    host: Host, tmp_path: Path, launcher: RecordingLauncher, job: WorkerJob
) -> None:
    service = host.service(job.family)
    _settles_recorded(service, launcher.events)
    await run_jobs(
        [job],
        launcher=launcher,
        ledger=service,
        on_message=host.on_message,
        log_dir=tmp_path / "logs",
        cwd=REPO_ROOT,
    )


async def test_teardown_stops_the_worker_before_its_unit_is_settled(
    host: Host, tmp_path: Path
) -> None:
    launcher = RecordingLauncher(LostLauncher(_fake_worker(_reserve_frame(1024))))
    await _run_lost(host, tmp_path, launcher, _job(tmp_path, [ANSWER], job_id="lost"))
    order = [e for e in launcher.events if isinstance(e, str)]
    assert order == ["stop_worker:lost", "settle:worker_lost"]
    assert host.ledger.spent("anthropic") == 2 * D("0.002024")


async def test_a_failing_stop_worker_still_scrubs_the_log_and_settles_the_unit(
    host: Host, tmp_path: Path
) -> None:
    leaky = (
        f"import sys\nsys.stderr.write('debug: {SYNTHETIC_KEY}\\n')\nsys.stderr.flush()\n"
        + _fake_worker(_reserve_frame(1024))
    )
    launcher = RecordingLauncher(
        LostLauncher(leaky), stop_error=RuntimeError("docker rm -f failed")
    )
    with pytest.raises(RuntimeError, match="docker rm -f failed") as error:
        await _run_lost(host, tmp_path, launcher, _job(tmp_path, [ANSWER], job_id="lost"))
    assert isinstance(error.value.__cause__, KeyLeakError)
    assert SYNTHETIC_KEY not in str(error.value.__cause__)
    log = tmp_path / "logs" / "lost.stderr.log"
    assert not log.exists() or log.read_text() == "debug: [redacted]\n"
    assert host.ledger.spent("anthropic") == 2 * D("0.002024")
    assert [e for e in launcher.events if isinstance(e, str)] == [
        "stop_worker:lost",
        "settle:worker_lost",
    ]
    assert launcher.events[-1] == ("close",)


@pytest.mark.parametrize("phase", ["spawn", "kill", "reap", "stop", "chained_stop"])
async def test_cleanup_scrubs_and_accounts_even_with_a_broken_stderr(
    host: Host, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    granted, started = asyncio.Event(), asyncio.Event()
    stopped = AsyncMock()

    async def spawn(*args: Any, stderr: Any, **kwargs: Any) -> Mock:
        os.write(stderr.fileno(), f"debug: {SYNTHETIC_KEY}\n".encode())
        started.set()
        if phase == "spawn":
            await asyncio.Event().wait()
        stdout = asyncio.StreamReader()
        stdout.feed_data(encode_frame(_reserve_frame(1024)))

        def write(frame: bytes) -> None:
            if _frames(frame)[0]["type"] == "grant":
                granted.set()

        return Mock(stdin=Mock(write=write, drain=AsyncMock()), stdout=stdout, wait=AsyncMock())

    class Launcher(LocalLauncher):
        async def stop_worker(self, job: WorkerJob) -> None:
            await stopped(job)
            if phase == "chained_stop":
                try:
                    raise ValueError(SYNTHETIC_KEY)
                except ValueError as cause:
                    raise RuntimeError("removal failed") from cause
            if phase == "stop":
                raise RuntimeError(f"removal failed: {SYNTHETIC_KEY}")

    _fake_host_processes(monkeypatch, spawn)
    if phase == "kill":
        monkeypatch.setattr(agentic_host, "_kill_group", Mock(side_effect=OSError("kill failed")))
    if phase == "reap":
        monkeypatch.setattr(agentic_host, "_reap", AsyncMock(side_effect=OSError("reap failed")))
    job = _job(tmp_path, [ANSWER], job_id="interrupted")
    task = asyncio.create_task(host.run([job], launcher=Launcher()))
    await asyncio.wait_for((started if phase == "spawn" else granted).wait(), 2)
    broken = Mock(write=Mock(side_effect=BrokenPipeError("reader closed")))
    with monkeypatch.context() as stream:
        stream.setattr(sys, "stderr", broken)
        task.cancel("signal SIGTERM")
        with pytest.raises(asyncio.CancelledError, match="signal SIGTERM") as error:
            await task
    log = tmp_path / "logs" / "interrupted.stderr.log"
    assert log.read_text() == "debug: [redacted]\n"
    stopped.assert_awaited_once_with(job)
    trace = "".join(traceback.format_exception(error.value))
    assert SYNTHETIC_KEY not in trace
    assert log.name in trace and "KeyLeakError" in trace
    if phase in ("stop", "chained_stop"):
        assert "removal failed" in trace
    assert host.ledger.spent("anthropic") == (0 if phase == "spawn" else 2 * D("0.002024"))


@pytest.mark.parametrize("cancel_spawn", [False, True])
async def test_parent_log_close_failure_still_cleans_the_spawned_worker(
    host: Host, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel_spawn: bool
) -> None:
    opened = asyncio.Event()
    log = tmp_path / "logs" / "interrupted.stderr.log"
    create_capture = tempfile.TemporaryFile

    class FailingClose(io.FileIO):
        def close(self) -> None:
            super().close()
            raise OSError("log close failed")

    def open_file(**kwargs: Any) -> FailingClose:
        with create_capture(**kwargs) as capture:
            return FailingClose(os.dup(capture.fileno()), "rb+")

    async def spawn(*args: Any, stderr: Any, **kwargs: Any) -> Mock:
        os.write(stderr.fileno(), SYNTHETIC_KEY.encode())
        opened.set()
        if cancel_spawn:
            await asyncio.Event().wait()
        stdout = asyncio.StreamReader()
        stdout.feed_eof()
        return Mock(stdin=Mock(drain=AsyncMock()), stdout=stdout, wait=AsyncMock())

    _fake_host_processes(monkeypatch, spawn)
    kill = Mock()
    reap = AsyncMock()
    monkeypatch.setattr(agentic_host, "_kill_group", kill)
    monkeypatch.setattr(agentic_host, "_reap", reap)
    monkeypatch.setattr(tempfile, "TemporaryFile", open_file)
    launcher = RecordingLauncher()
    job = _job(tmp_path, [ANSWER], job_id="interrupted")
    task = asyncio.create_task(host.run([job], launcher=launcher))
    await asyncio.wait_for(opened.wait(), 2)
    if cancel_spawn:
        task.cancel("signal SIGTERM")
    with pytest.raises((asyncio.CancelledError, OSError, KeyLeakError)) as error:
        await task
    kill.assert_called_once()
    reap.assert_awaited_once()
    assert "stop_worker:interrupted" in launcher.events
    assert log.read_text() == "[redacted]"
    trace = "".join(traceback.format_exception(error.value))
    assert "log close failed" in trace and SYNTHETIC_KEY not in trace
    if cancel_spawn:
        assert isinstance(error.value, asyncio.CancelledError)
        assert str(error.value) == "signal SIGTERM"


async def test_unreadable_worker_log_is_not_published(
    host: Host, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _job(tmp_path, [], job_id="lost")
    code = f"import os\nos.write(2, {SYNTHETIC_KEY.encode()!r})"
    monkeypatch.setattr(os, "pread", Mock(side_effect=OSError("read failed")))
    with pytest.raises(OSError, match="read failed"):
        await host.run([job], launcher=KeyedLauncher(code, ""))
    assert not list((tmp_path / "logs").iterdir())


async def test_scrub_allocation_failure_leaves_no_log_and_settles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "worker.stderr.log"
    capture = _worker_log(SYNTHETIC_KEY.encode())
    failure = MemoryError("redaction allocation failed")
    monkeypatch.setattr(agentic_host, "redact_key_shaped", Mock(side_effect=failure))
    monkeypatch.setattr(agentic_host, "_kill_group", Mock())
    monkeypatch.setattr(agentic_host, "_reap", AsyncMock())
    ledger = Mock(settle=AsyncMock())
    with pytest.raises(MemoryError, match="redaction allocation failed") as error:
        await agentic_host._teardown(None, Mock(), log, ledger, "reserved", capture)
    assert error.value is failure
    assert not log.exists() and not log.with_suffix(".log.tmp").exists()
    ledger.settle.assert_awaited_once_with("reserved", outcome="worker_lost", buckets=None)


async def test_unformattable_removal_error_cannot_skip_scrub_or_settlement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "worker.stderr.log"
    capture = _worker_log(SYNTHETIC_KEY.encode())
    monkeypatch.setattr(agentic_host, "_kill_group", Mock())
    monkeypatch.setattr(agentic_host, "_reap", AsyncMock())
    ledger = Mock(settle=AsyncMock())
    stop = AsyncMock(side_effect=RuntimeError(f"removal failed: {SYNTHETIC_KEY}"))
    with monkeypatch.context() as formatting, pytest.raises(KeyLeakError) as error:
        formatting.setattr(traceback, "format_exception", Mock(side_effect=MemoryError()))
        await agentic_host._teardown(None, Mock(), log, ledger, "reserved", capture, stop)
    assert log.read_text() == "[redacted]"
    ledger.settle.assert_awaited_once_with("reserved", outcome="worker_lost", buckets=None)
    trace = "".join(traceback.format_exception(error.value))
    assert log.name in trace and SYNTHETIC_KEY not in trace


async def test_close_problems_fail_a_clean_run(host: Host, tmp_path: Path) -> None:
    launcher = RecordingLauncher(problems=["proxy pdmcp-x-proxy: exit 137"])
    with pytest.raises(BenchmarkValidationError, match="exit 137"):
        await host.run([_job(tmp_path, [ANSWER], count=1)], launcher=launcher)
    assert [m["type"] for m in host.messages()][-1] == "done"  # the run itself completed


@pytest.mark.parametrize("boundary", ["spawn", "spawn_kill", "worker", "launcher"])
async def test_host_cleanup_preserves_signal_and_waits_for_failure(
    host: Host, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    running, removing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    failure = RuntimeError(f"{boundary} cleanup failed")
    interrupted: list[asyncio.CancelledError] = []

    async def until_cancelled(*args: Any, **kwargs: Any) -> None:
        running.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as exc:
            interrupted.append(exc)
            raise

    async def fail_cleanup(*args: Any) -> None:
        removing.set()
        await release.wait()
        raise failure

    launcher = RecordingLauncher()
    if boundary == "launcher":
        monkeypatch.setattr(launcher, "prepare", until_cancelled)
        monkeypatch.setattr(launcher, "close", fail_cleanup)
    else:
        monkeypatch.setattr(agentic_host, "_start_anchor", Mock(return_value=Mock(pid=object())))

        async def reap(*args: Any) -> None:
            if boundary == "spawn":
                await fail_cleanup()
            if boundary == "spawn_kill":
                removing.set()
                await release.wait()

        def kill(*args: Any) -> None:
            if boundary == "spawn_kill":
                removing.set()
                raise failure

        monkeypatch.setattr(agentic_host, "_kill_group", kill)
        monkeypatch.setattr(agentic_host, "_reap", reap)
        proc = Mock(stdin=Mock(drain=until_cancelled), wait=AsyncMock(return_value=0))
        monkeypatch.setattr(
            asyncio,
            "create_subprocess_exec",
            until_cancelled if boundary.startswith("spawn") else AsyncMock(return_value=proc),
        )
        monkeypatch.setattr(launcher, "stop_worker", fail_cleanup)

    task = asyncio.create_task(host.run([_job(tmp_path, [ANSWER])], launcher=launcher))
    await asyncio.wait_for(running.wait(), 2)
    task.cancel("signal SIGTERM")
    await asyncio.wait_for(removing.wait(), 2)
    task.cancel("signal SIGHUP")
    await asyncio.sleep(0)
    still_cleaning = not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError, match="signal SIGTERM") as error:
        await task
    assert still_cleaning
    assert error.value is interrupted[0]
    assert error.value.__cause__ is failure


def _fake_host_processes(monkeypatch: pytest.MonkeyPatch, spawn: Any) -> None:
    monkeypatch.setattr(agentic_host, "_start_anchor", Mock(return_value=Mock(pid=object())))
    monkeypatch.setattr(agentic_host, "_kill_group", Mock())
    monkeypatch.setattr(agentic_host, "_reap", AsyncMock())
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)


@pytest.mark.parametrize("during_teardown", [False, True])
async def test_cancelled_worker_reports_scrubbed_log_and_books_open_unit(
    host: Host, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, during_teardown: bool
) -> None:
    granted, stopping, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def spawn(*args: Any, stderr: Any, **kwargs: Any) -> Mock:
        stderr.write(f"local debug: {SYNTHETIC_KEY}\n".encode())
        stdout = asyncio.StreamReader()
        stdout.feed_data(encode_frame(_reserve_frame(1024)))
        if during_teardown:
            stdout.feed_eof()

        def write(frame: bytes) -> None:
            if _frames(frame)[0]["type"] == "grant":
                granted.set()

        return Mock(stdin=Mock(write=write, drain=AsyncMock()), stdout=stdout, wait=AsyncMock())

    class Launcher(LocalLauncher):
        async def stop_worker(self, job: WorkerJob) -> None:
            stopping.set()
            await release.wait()

    _fake_host_processes(monkeypatch, spawn)
    job = _job(tmp_path, [ANSWER], job_id="interrupted")
    task = asyncio.create_task(host.run([job], launcher=Launcher()))
    await asyncio.wait_for((stopping if during_teardown else granted).wait(), 2)
    task.cancel("signal SIGTERM")
    await asyncio.wait_for(stopping.wait(), 2)
    release.set()
    with pytest.raises(asyncio.CancelledError, match="signal SIGTERM") as error:
        await task
    log = tmp_path / "logs" / "interrupted.stderr.log"
    assert log.read_text() == "local debug: [redacted]\n"
    assert host.ledger.spent("anthropic") == 2 * D("0.002024")
    leak = error.value.__cause__
    assert isinstance(leak, KeyLeakError)
    assert str(log) in str(leak) and "anthropic" in str(leak)
    assert SYNTHETIC_KEY not in str(leak) and "local debug" not in str(leak)


@pytest.mark.parametrize("problem", ["stop", "leak"])
async def test_later_worker_reports_cleanup_after_clean_sibling_cancellation(
    host: Host,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    problem: str,
) -> None:
    ready = {name: asyncio.Event() for name in ("first", "later")}
    first_done, release = asyncio.Event(), asyncio.Event()

    async def spawn(*args: Any, stderr: Any, **kwargs: Any) -> Mock:
        name = args[-1]
        if name == "first":
            supervisor = asyncio.current_task()
            assert supervisor is not None
            supervisor.add_done_callback(lambda _: first_done.set())
        elif problem == "leak":
            stderr.write(f"local debug: {SYNTHETIC_KEY}\n".encode())
        ready[name].set()
        return Mock(stdin=Mock(drain=AsyncMock()), stdout=asyncio.StreamReader(), wait=AsyncMock())

    class Launcher(LocalLauncher):
        def command(self, job: WorkerJob) -> tuple[list[str], dict[str, str]]:
            return ["unused", job.job_id], {}

        async def stop_worker(self, job: WorkerJob) -> None:
            if job.job_id == "later":
                await release.wait()
                if problem == "stop":
                    raise RuntimeError("worker removal failed")

    _fake_host_processes(monkeypatch, spawn)
    jobs = [_job(tmp_path, [ANSWER], job_id=name) for name in ready]
    task = asyncio.create_task(host.run(jobs, launcher=Launcher()))
    for event in ready.values():
        await asyncio.wait_for(event.wait(), 2)
    task.cancel("signal SIGTERM")
    await asyncio.wait_for(first_done.wait(), 2)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError, match="signal SIGTERM"):
        await task
    stderr = capsys.readouterr().err
    assert "later" in stderr
    if problem == "leak":
        log = tmp_path / "logs" / "later.stderr.log"
        assert log.read_text() == "local debug: [redacted]\n"
        assert "KeyLeakError" in stderr and "anthropic" in stderr and str(log) in stderr
        assert SYNTHETIC_KEY not in stderr and "local debug" not in stderr
    else:
        assert "worker removal failed" in stderr


async def test_run_jobs_preserves_cancellation_while_draining_other_workers(
    host: Host, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    draining, release = asyncio.Event(), asyncio.Event()
    original = asyncio.CancelledError("signal SIGTERM")

    async def supervise(job: WorkerJob, **kwargs: Any) -> None:
        if job.job_id == "first":
            raise original
        try:
            await asyncio.Event().wait()
        finally:
            draining.set()
            await release.wait()

    monkeypatch.setattr(agentic_host, "_supervise", supervise)
    jobs = [_job(tmp_path, [ANSWER], job_id=name) for name in ("first", "second")]
    task = asyncio.create_task(host.run(jobs))
    await asyncio.wait_for(draining.wait(), 2)
    task.cancel("signal SIGHUP")
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError, match="signal SIGTERM") as error:
        await task
    assert error.value is original


@pytest.mark.parametrize("during_prepare", [True, False])
async def test_launcher_close_problems_are_the_cause_of_cancellation(
    host: Host, tmp_path: Path, during_prepare: bool
) -> None:
    started, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Launcher(LocalLauncher):
        async def prepare(self, jobs: Sequence[WorkerJob]) -> None:
            if during_prepare:
                started.set()
                await asyncio.Event().wait()

        async def close(self) -> list[str]:
            closing.set()
            await release.wait()
            return ["outside network removal failed"]

    task = asyncio.create_task(
        run_jobs(
            [],
            launcher=Launcher(),
            ledger=host.service(_job(tmp_path, [ANSWER]).family),
            on_message=host.on_message,
            log_dir=tmp_path,
            cwd=tmp_path,
        )
    )
    await asyncio.wait_for((started if during_prepare else closing).wait(), 2)
    task.cancel("signal SIGTERM")
    await asyncio.wait_for(closing.wait(), 2)
    release.set()
    with pytest.raises(asyncio.CancelledError, match="signal SIGTERM") as error:
        await task
    assert isinstance(error.value.__cause__, BenchmarkValidationError)
    assert "outside network removal failed" in str(error.value.__cause__)


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
@pytest.mark.parametrize("failure", [WORKER_LEAK_REASON, "own key found in output", "decoded"])
async def test_credential_failure_denies_sibling_grants_during_teardown(
    host: Host, tmp_path: Path, provider: str, failure: str
) -> None:
    """Real protocol workers reproduce the R15 sibling grant during removal."""
    boundary, response = tmp_path / "removing", tmp_path / "reply.json"
    if failure == "decoded":
        body = json.dumps({"type": "system_info", "note": SYNTHETIC_KEY}).replace(
            "sk-ant", r"\u0073k-ant"
        ).encode()
        assert SYNTHETIC_KEY.encode() not in body
        assert_no_key_shaped(body, where="encoded synthetic frame")
        leak_frame = len(body).to_bytes(8, "big") + body
    else:
        leak_frame = encode_frame({"type": "error", "reason": failure})
    code = (
        "import json, os, sys, time\n"
        "from pathlib import Path\n"
        "from benchmarks.agentic.protocol import encode_frame, read_frame_fd\n"
        "job = read_frame_fd(0)\n"
        "if job['job_id'] == 'leak':\n"
        f"    os.write(1, {leak_frame!r})\n"
        "else:\n"
        "    deadline = time.monotonic() + 5\n"
        f"    while not Path({str(boundary)!r}).exists():\n"
        "        assert time.monotonic() < deadline, 'teardown never started'\n"
        "        time.sleep(0.01)\n"
        f"    os.write(1, {encode_frame(_reserve_frame(1024))!r})\n"
        "    reply = read_frame_fd(0)\n"
        f"    Path({str(response)!r}).write_text(json.dumps(reply))\n"
    )

    class Launcher(KeyedLauncher):
        closed = False

        async def stop_worker(self, job: WorkerJob) -> None:
            if job.job_id == "leak":
                boundary.touch()
                async with asyncio.timeout(5):
                    while not response.exists():
                        await asyncio.sleep(0.01)

        async def close(self) -> list[str]:
            self.closed = True
            return []

    jobs = [_job(tmp_path, [ANSWER], job_id=name) for name in ("leak", "sibling")]
    jobs[1] = replace(jobs[1], family=replace(jobs[1].family, provider=provider))
    host.ledger.close()
    host.ledger = Ledger.open(
        tmp_path / "ledger", run_id="host-test", caps={"anthropic": D(180), "openai": D(65)}
    )
    service = host.service(jobs[0].family)
    launcher = Launcher(code, key="")
    with pytest.raises(KeyLeakError) as error:
        await run_jobs(
            jobs, launcher=launcher, ledger=service, on_message=host.on_message,
            log_dir=tmp_path / "logs", cwd=REPO_ROOT,
        )
    assert launcher.closed
    assert len(list((tmp_path / "logs").glob("*.stderr.log"))) == 2
    assert SYNTHETIC_KEY not in "".join(traceback.format_exception(error.value))
    assert json.loads(response.read_text()) == {"type": "deny"}
    assert host.ledger.invocation_events() == []


async def test_credential_failure_stops_grants_before_waiting_for_worker_exit(
    host: Host, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    waiting, release = asyncio.Event(), asyncio.Event()
    job = _job(tmp_path, [ANSWER], job_id="leak")
    service = host.service(job.family)
    stdout = asyncio.StreamReader()
    stdout.feed_data(encode_frame({"type": "error", "reason": WORKER_LEAK_REASON}))

    async def wait() -> int:
        waiting.set()
        await release.wait()
        return 3

    proc = Mock(stdin=Mock(drain=AsyncMock()), stdout=stdout, wait=wait)
    _fake_host_processes(monkeypatch, AsyncMock(return_value=proc))
    task = asyncio.create_task(run_jobs(
        [job], launcher=LocalLauncher(), ledger=service, on_message=host.on_message,
        log_dir=tmp_path / "logs", cwd=REPO_ROOT,
    ))
    try:
        await asyncio.wait_for(waiting.wait(), 2)
        assert not task.done()
        unit = await service.reserve(
            provider="anthropic", model_id=job.family.model_id, input_bound=1000, output_bound=1024
        )
    finally:
        release.set()
        with pytest.raises(KeyLeakError, match=WORKER_LEAK_REASON):
            await task
    assert unit is None
    assert host.ledger.invocation_events() == []


@pytest.mark.parametrize("decoded", [False, True])
async def test_protocol_failure_stops_grants_at_recognition_without_a_scheduling_gap(
    host: Host, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, decoded: bool
) -> None:
    job = _job(tmp_path, [ANSWER], job_id="leak")
    service = host.service(job.family)
    body = json.dumps({"type": "system_info", "note": SYNTHETIC_KEY})
    if decoded:
        body = body.replace("sk-ant", r"\u0073k-ant")
        assert_no_key_shaped(body, where="encoded synthetic frame")
    stdout = asyncio.StreamReader()
    stdout.feed_data(len(body.encode()).to_bytes(8, "big") + body.encode())
    proc = Mock(stdin=Mock(drain=AsyncMock()), stdout=stdout, wait=AsyncMock())
    _fake_host_processes(monkeypatch, AsyncMock(return_value=proc))
    real_read = agentic_host.read_frame
    competing: list[asyncio.Task[str | None]] = []

    async def read(*args: Any, **kwargs: Any) -> Any:
        try:
            return await real_read(*args, **kwargs)
        except KeyLeakError:
            # A sibling ready at recognition must not outrun the supervisor's stop.
            competing.append(asyncio.create_task(service.reserve(
                provider="anthropic", model_id=job.family.model_id,
                input_bound=1000, output_bound=1024,
            )))
            raise

    monkeypatch.setattr(agentic_host, "read_frame", read)
    with pytest.raises(KeyLeakError, match="worker frame"):
        await run_jobs(
            [job], launcher=LocalLauncher(), ledger=service, on_message=host.on_message,
            log_dir=tmp_path / "logs", cwd=REPO_ROOT,
        )
    assert len(competing) == 1
    assert await competing[0] is None
    assert host.ledger.invocation_events() == []


@pytest.mark.parametrize("failure", ["log", "stop_worker", "diagnostic"])
async def test_cleanup_credential_failure_stops_queued_grants_and_settles_open_unit(
    host: Host, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    service = host.service(_job(tmp_path, [ANSWER]).family)
    unit = await service.reserve(
        provider="anthropic", model_id="scripted-1", input_bound=1000, output_bound=1024
    )
    assert unit is not None
    pending = asyncio.create_task(service.reserve(
        provider="anthropic", model_id="scripted-1", input_bound=1000, output_bound=1024
    ))
    await asyncio.sleep(0)
    assert not pending.done()
    monkeypatch.setattr(agentic_host, "_kill_group", Mock())
    monkeypatch.setattr(agentic_host, "_reap", AsyncMock())
    capture = _worker_log(SYNTHETIC_KEY.encode() if failure == "log" else b"clean")
    log = tmp_path / "worker.stderr.log"
    stop = AsyncMock()
    if failure == "stop_worker":
        stop.side_effect = KeyLeakError("synthetic credential failure in cleanup")
    elif failure == "diagnostic":
        stop.side_effect = RuntimeError(SYNTHETIC_KEY)
    try:
        with pytest.raises(KeyLeakError) as error:
            await agentic_host._teardown(None, Mock(), log, service, unit, capture, stop)
        assert await asyncio.wait_for(pending, 1) is None
        assert host.ledger.spent("anthropic") == 2 * D("0.002024")
        assert [e["event"] for e in host.ledger.invocation_events()] == ["reserve", "settle"]
        assert log.read_text() == ("[redacted]" if failure == "log" else "clean")
        assert SYNTHETIC_KEY not in "".join(traceback.format_exception(error.value))
        assert capture.closed
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
