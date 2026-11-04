"""Frame limits abort the harness before another dispatch or scored publication."""

import asyncio
import json
import sys
from contextlib import AsyncExitStack
from unittest.mock import AsyncMock, Mock

import pytest
from mcp.types import CallToolResult

from benchmarks.adapters.guard import PROVIDER_API_KEY_ENV
from benchmarks.agentic import worker
from benchmarks.agentic.bridge import Bridge
from benchmarks.agentic.cleanup import finish
from benchmarks.agentic.host import LocalLauncher, run_jobs
from benchmarks.agentic.mcp_stdio import ReplyGuard
from benchmarks.agentic.protocol import FrameTooLarge, KeyLeakError, encode_frame
from tests.benchmarks.test_agentic_host import (
    ANSWER,
    REPO_ROOT,
    Host,
    _fake_host_processes,
    _job,
    _reserve_frame,
)
from tests.benchmarks.test_agentic_loop import TOOL
from tests.benchmarks.test_agentic_mcp_boundary import _system


@pytest.fixture
def host(tmp_path, monkeypatch):
    for name in PROVIDER_API_KEY_ENV.values():
        monkeypatch.delenv(name, raising=False)
    value = Host(tmp_path)
    yield value
    value.ledger.close()


async def test_oversized_stdio_reply_aborts_real_worker_and_host(tmp_path, monkeypatch, host):
    calls, outcome = tmp_path / "calls", tmp_path / "outcome.json"
    program = tmp_path / "worker.py"
    program.write_text(f"""
import sys
sys.path.insert(0, {str(REPO_ROOT)!r})
import json
from pathlib import Path
from benchmarks.agentic import clients, mcp_stdio, worker
mcp_stdio.MAX_FRAME_BYTES = 512
original_call = clients.ScriptedClient.call
async def call(self, conv):
    with Path({str(calls)!r}).open('a') as file:
        file.write('provider\\n')
    return await original_call(self, conv)
clients.ScriptedClient.call = call
processes = []
create = mcp_stdio._create_platform_compatible_process
async def capture(**kwargs):
    process = await create(**kwargs)
    processes.append(process)
    return process
mcp_stdio._create_platform_compatible_process = capture
code = worker.main_worker()
Path({str(outcome)!r}).write_text(json.dumps({{
    'code': code, 'children': [p.returncode for p in processes]}}))
raise SystemExit(code)
""")
    job = _job(tmp_path, [TOOL, ANSWER, ANSWER], system=_system("oversize", "clean"))
    monkeypatch.setattr(
        LocalLauncher, "command", lambda self, job: ([sys.executable, "-I", str(program)], {})
    )
    stopped = AsyncMock()
    monkeypatch.setattr(LocalLauncher, "stop_worker", stopped)
    service = host.service(job.family)
    async with asyncio.timeout(10):
        with pytest.raises(FrameTooLarge, match="MCP stdio reply exceeds the frame limit"):
            await run_jobs(
                [job], launcher=LocalLauncher(), ledger=service, on_message=host.on_message,
                log_dir=tmp_path / "logs", cwd=REPO_ROOT,
            )
    assert calls.read_text().splitlines() == ["provider"]
    result = json.loads(outcome.read_text())
    assert result["code"] == 2
    assert len(result["children"]) == 1 and result["children"][0] is not None
    stopped.assert_awaited_once()
    assert [m["type"] for m in host.messages()] == ["system_info"]
    events = host.ledger.invocation_events()
    assert [e["event"] for e in events] == ["reserve", "settle"]
    assert events[1]["outcome"] == "ok"  # The known model usage was settled before MCP.
    assert await service.reserve(
        provider=job.family.provider, model_id=job.family.model_id, input_bound=1, output_bound=1
    ) is None
    assert b"synthetic server diagnostic retained" in b"".join(
        p.read_bytes() for p in (tmp_path / "logs").glob("*.log")
    )


@pytest.mark.parametrize("phase", ["before", "during", "after"])
async def test_bridge_checks_frame_latch_at_each_call_boundary(phase):
    guard = ReplyGuard()
    failure = FrameTooLarge("synthetic frame limit")

    async def call(*args):
        guard.failure = failure
        if phase == "during":
            raise ConnectionError("connection closed")
        return CallToolResult(content=[])

    session = Mock(call_tool=AsyncMock(side_effect=call))
    bridge = Bridge(session, {}, [])
    bridge._reply_guard = guard
    if phase == "before":
        guard.failure = failure
    with pytest.raises(FrameTooLarge) as caught:
        await bridge.call("search_docs", {})
    assert caught.value is failure
    assert session.call_tool.await_count == (0 if phase == "before" else 1)


@pytest.mark.parametrize("cleanup", ["ordinary", "credential", "interrupt"])
async def test_frame_failure_cleanup_precedence(cleanup):
    pending = FrameTooLarge("recognized frame limit")
    error = {
        "ordinary": RuntimeError("cleanup failed"),
        "credential": KeyLeakError("synthetic credential failure"),
        "interrupt": asyncio.CancelledError("operator interrupt"),
    }[cleanup]

    async def fail():
        raise error

    expected = pending if cleanup == "ordinary" else error
    with pytest.raises(type(expected)) as caught:
        await finish(fail(), pending)
    assert caught.value is expected


async def test_worker_close_propagates_grouped_frame_limit():
    stack = AsyncExitStack()
    failure = FrameTooLarge("synthetic frame limit")

    async def close():
        raise ExceptionGroup("stdio teardown", [RuntimeError("clean"), failure])

    stack.push_async_callback(close)
    with pytest.raises(FrameTooLarge) as caught:
        await worker._close(stack)
    assert caught.value is failure


@pytest.mark.parametrize("source", ["error", "header"])
async def test_host_stops_grants_before_wait_or_teardown(host, tmp_path, monkeypatch, source):
    job = _job(tmp_path, [ANSWER])
    service = host.service(job.family)
    waiting, release = asyncio.Event(), asyncio.Event()
    stdout = asyncio.StreamReader()
    stdout.feed_data(
        encode_frame({"type": "error", "reason": "FrameTooLarge: synthetic frame limit"})
        if source == "error" else (513).to_bytes(8, "big")
    )

    async def wait():
        waiting.set()
        await release.wait()
        return 2

    proc = Mock(stdin=Mock(drain=AsyncMock()), stdout=stdout, wait=wait)
    _fake_host_processes(monkeypatch, AsyncMock(return_value=proc))
    task = asyncio.create_task(run_jobs(
        [job], launcher=LocalLauncher(), ledger=service, on_message=host.on_message,
        log_dir=tmp_path / "logs", cwd=REPO_ROOT, max_frame_bytes=512,
    ))
    try:
        await asyncio.wait_for(waiting.wait(), 2)
        unit = await service.reserve(
            provider=job.family.provider, model_id=job.family.model_id,
            input_bound=1, output_bound=1,
        )
        if unit is not None:  # Clean up the failing pre-fix assertion's own reservation.
            await service.settle(unit, outcome="worker_lost", buckets=None)
    finally:
        release.set()
        with pytest.raises(FrameTooLarge):
            await task
    assert unit is None
    assert host.ledger.invocation_events() == []


async def test_frame_recognized_while_waiting_for_grant_prevents_model_dispatch():
    guard = ReplyGuard()
    channel = Mock()
    channel.check_content = Mock()
    reader = asyncio.StreamReader()
    ledger = worker._HostLedger(channel, reader)
    ledger.check_failure = guard.raise_if_failed
    failure = FrameTooLarge("synthetic frame limit")

    async def request(message):
        guard.failure = failure
        return {"type": "grant", "unit_id": "already-granted"}

    ledger.request = request
    with pytest.raises(FrameTooLarge) as caught:
        await ledger.reserve(
            provider="anthropic", model_id="synthetic", input_bound=1, output_bound=1
        )
    assert caught.value is failure


@pytest.mark.parametrize("cleanup_error", [False, True])
async def test_frame_failure_denies_sibling_grants_and_publications_during_teardown(
    host, tmp_path, cleanup_error
):
    boundary, response = tmp_path / "stopping", tmp_path / "reply.json"
    record = {
        "type": "trajectory", "corpus_id": "Q1", "repeat": 1, "attempt": 1, "record": {}
    }
    code = f"""
import os, sys, time, json
sys.path.insert(0, {str(REPO_ROOT)!r})
from pathlib import Path
from benchmarks.agentic.protocol import encode_frame, read_frame_fd
job = read_frame_fd(0)
if job['job_id'] == 'oversize':
    os.write(1, {encode_frame(_reserve_frame(1024))!r})
    assert read_frame_fd(0)['type'] == 'grant'
    os.write(1, {encode_frame({'type': 'error', 'reason': 'FrameTooLarge: synthetic limit'})!r})
else:
    deadline = time.monotonic() + 5
    while not Path({str(boundary)!r}).exists():
        assert time.monotonic() < deadline
        time.sleep(0.01)
    os.write(1, {encode_frame(record)!r})
    assert read_frame_fd(0)['type'] == 'ack'
    os.write(1, {encode_frame(_reserve_frame(1024))!r})
    reply = read_frame_fd(0)
    stage = Path({str(response.with_suffix('.tmp'))!r})
    stage.write_text(json.dumps(reply))
    stage.replace({str(response)!r})
"""

    class Launcher(LocalLauncher):
        def __init__(self):
            self.stopped = []
            self.closed = False

        def command(self, job):
            return [sys.executable, "-I", "-c", code], {}

        async def stop_worker(self, job):
            self.stopped.append(job.job_id)
            if job.job_id == "oversize":
                boundary.touch()
                async with asyncio.timeout(5):
                    while not response.exists():
                        await asyncio.sleep(0.01)
                if cleanup_error:
                    raise RuntimeError("worker removal diagnostic")

        async def close(self):
            self.closed = True
            if cleanup_error:
                raise RuntimeError("launcher cleanup diagnostic")
            return []

    jobs = [_job(tmp_path, [ANSWER], job_id=name) for name in ("oversize", "sibling")]
    service = host.service(jobs[0].family)
    launcher = Launcher()
    with pytest.raises(FrameTooLarge, match="synthetic limit"):
        async with asyncio.timeout(10):
            await run_jobs(
                jobs, launcher=launcher, ledger=service, on_message=host.on_message,
                log_dir=tmp_path / "logs", cwd=REPO_ROOT,
            )
    assert sorted(launcher.stopped) == ["oversize", "sibling"] and launcher.closed
    assert json.loads(response.read_text()) == {"type": "deny"}
    assert host.messages() == []
    events = host.ledger.invocation_events()
    assert [e["event"] for e in events] == ["reserve", "settle"]
    assert events[1]["outcome"] == "worker_lost"
    assert len(list((tmp_path / "logs").glob("*.log"))) == 2


@pytest.mark.parametrize("credential", [False, True])
async def test_frame_latch_never_masks_credential_or_interrupt(credential):
    guard = ReplyGuard()
    guard.failure = FrameTooLarge("synthetic limit")
    failure = (
        KeyLeakError("synthetic credential failure") if credential else asyncio.CancelledError()
    )
    bridge = Bridge(Mock(call_tool=AsyncMock(side_effect=failure)), {}, [])

    # The latch arrives during the SDK call, after the pre-dispatch check.
    async def call(*args):
        bridge._reply_guard = guard
        raise failure

    bridge._session.call_tool.side_effect = call
    with pytest.raises(type(failure)) as caught:
        await bridge.call("search_docs", {})
    assert caught.value is failure
