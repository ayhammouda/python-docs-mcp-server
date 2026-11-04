"""Recognized worker credential failures survive real asynchronous teardown."""

import asyncio
import errno
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

import pytest

from benchmarks.__main__ import main
from benchmarks.adapters.guard import PROVIDER_API_KEY_ENV
from benchmarks.agentic import host
from benchmarks.agentic.cleanup import finish
from benchmarks.agentic.host import LocalLauncher, WorkerJob, run_jobs
from benchmarks.agentic.ledger import Ledger
from benchmarks.agentic.protocol import WORKER_LEAK_REASON, KeyLeakError
from benchmarks.agentic.sandbox import _rollback
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks.test_agentic_host import (
    ANSWER,
    REPO_ROOT,
    SYNTHETIC_KEY,
    D,
    Host,
    _job,
    _reserve_frame,
)
from tests.benchmarks.test_agentic_run import _cli_args


def _worker_code(*, leak: bool = True) -> str:
    # A separate isolated interpreter writes raw wire frames, bypassing output guards.
    return (
        "import json, os, sys\n"
        "def read():\n"
        "    n = int.from_bytes(sys.stdin.buffer.read(8), 'big')\n"
        "    return json.loads(sys.stdin.buffer.read(n))\n"
        "def send(value):\n"
        "    body = json.dumps(value).encode()\n"
        "    os.write(1, len(body).to_bytes(8, 'big') + body)\n"
        "assert read()['type'] == 'job'\n"
        f"send({_reserve_frame(1024)!r})\n"
        "assert read()['type'] == 'grant'\n"
        "os.write(2, b'clean worker stderr\\n')\n"
        + (f"send({{'type': 'error', 'reason': {WORKER_LEAK_REASON!r}}})\n" if leak else "")
        + "sys.exit(3)\n"
    )


def _storage_error(secret: str = "") -> OSError:
    error = OSError(errno.ENOSPC, "artifact volume full")
    if secret:
        error.__cause__ = RuntimeError("daemon response: " + secret)
        error.add_note("collection note: " + secret)
    return error


def _assert_safe_chain(error: BaseException, raw: BaseException) -> str:
    seen: set[int] = set()
    stack = [error]
    while stack:
        item = stack.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        assert item is not raw
        assert SYNTHETIC_KEY not in str(item)
        assert SYNTHETIC_KEY not in "\n".join(getattr(item, "__notes__", []))
        stack.extend(e for e in (item.__cause__, item.__context__) if e is not None)
        if isinstance(item, BaseExceptionGroup):
            stack.extend(item.exceptions)
    trace = "".join(traceback.format_exception(error))
    assert SYNTHETIC_KEY not in trace
    return trace


@pytest.mark.parametrize("secret", ["", SYNTHETIC_KEY], ids=["clean", "credential-chain"])
async def test_finish_keeps_original_leak_and_only_safe_cleanup_diagnostics(secret: str) -> None:
    original = KeyLeakError("recognized worker credential failure")
    failure = _storage_error(secret)
    prior = BenchmarkValidationError("earlier cleanup failed")
    original.__cause__ = prior

    async def fail() -> None:
        await asyncio.sleep(0)
        raise failure

    with pytest.raises(KeyLeakError) as caught:
        try:
            raise original
        finally:
            await finish(fail(), original)
    assert caught.value is original
    trace = _assert_safe_chain(caught.value, failure)
    assert "OSError" in trace and "artifact volume full" in trace
    assert "earlier cleanup failed" in trace and "cleanup" in trace
    if secret:
        assert "daemon response" in trace and "collection note" in trace
        assert "[redacted]" in trace


@pytest.mark.parametrize("boundary", ["publish", "close", "problems"])
@pytest.mark.parametrize("secret", ["", SYNTHETIC_KEY], ids=["clean", "credential-chain"])
async def test_recognized_frame_survives_async_cleanup_and_settles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str, secret: str
) -> None:
    for name in PROVIDER_API_KEY_ENV.values():
        monkeypatch.delenv(name, raising=False)
    h = Host(tmp_path)
    job = _job(tmp_path, [ANSWER], job_id="recognized-leak")
    service = h.service(job.family)
    failure = _storage_error(secret)
    captures, processes, anchors, pending = [], [], [], []
    original_reserve = service.reserve
    original_spawn = asyncio.create_subprocess_exec
    original_anchor = host._start_anchor
    original_snapshot = host._scrubbed_snapshot
    observed: list[KeyLeakError] = []

    async def reserve(**kwargs: Any) -> str | None:
        unit = await original_reserve(**kwargs)
        if unit is not None:
            # Queued while the worker holds the only in-flight provider unit.
            pending.append(asyncio.create_task(original_reserve(**kwargs)))
        return unit

    async def spawn(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
        proc = await original_spawn(*args, **kwargs)
        processes.append(proc)
        return proc

    def anchor() -> Any:
        proc = original_anchor()
        anchors.append(proc)
        return proc

    def snapshot(log: Any) -> Any:
        captures.append(log)
        return original_snapshot(log)

    async def observe_finish(operation: Any, unwinding: BaseException | None = None) -> Any:
        if isinstance(unwinding, KeyLeakError):
            observed.append(unwinding)
        return await finish(operation, unwinding)

    class Launcher(LocalLauncher):
        stopped = closed = False

        def command(self, job: WorkerJob) -> tuple[list[str], dict[str, str]]:
            return [sys.executable, "-I", "-c", _worker_code()], {}

        async def stop_worker(self, job: WorkerJob) -> None:
            self.stopped = True
            assert pending
            assert not pending[0].done()  # settlement releases the provider lock later

        async def close(self) -> list[str]:
            await asyncio.sleep(0)  # exercise a real awaited close, after settlement
            self.closed = True
            assert await asyncio.wait_for(pending[0], 2) is None
            if boundary == "close":
                raise failure
            return ["outside network removal failed: " + secret] if boundary == "problems" else []

    def publish(data: bytes, path: Path) -> None:
        assert data == b"clean worker stderr\n"
        raise failure

    monkeypatch.setattr(service, "reserve", reserve)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(host, "_start_anchor", anchor)
    monkeypatch.setattr(host, "_scrubbed_snapshot", snapshot)
    monkeypatch.setattr(host, "finish", observe_finish)
    if boundary == "publish":
        monkeypatch.setattr(host, "_publish_worker_log", publish)
    launcher = Launcher()
    try:
        with pytest.raises(KeyLeakError) as caught:
            await run_jobs(
                [job], launcher=launcher, ledger=service, on_message=h.on_message,
                log_dir=tmp_path / "logs", cwd=REPO_ROOT,
            )
        assert caught.value is observed[0]
        trace = _assert_safe_chain(caught.value, failure)
        assert WORKER_LEAK_REASON in str(caught.value)
        assert ("outside network removal failed" if boundary == "problems"
                else "artifact volume full") in trace
        assert launcher.stopped and launcher.closed
        assert captures and all(log.closed for log in captures)
        assert processes and all(proc.returncode is not None for proc in processes)
        assert anchors and all(
            proc.returncode is not None and proc.stdin.closed for proc in anchors
        )
        events = h.ledger.invocation_events()
        assert [event["event"] for event in events] == ["reserve", "settle"]
        assert events[1]["outcome"] == "worker_lost"
        assert D(events[1]["booked"]) == 2 * D(events[0]["bound"])
        before = h.ledger.spent("anthropic")
        assert await original_reserve(
            provider="anthropic", model_id=job.family.model_id, input_bound=1000, output_bound=1024
        ) is None
        assert h.ledger.spent("anthropic") == before
        assert h.ledger.invocation_events() == events
        log_path = tmp_path / "logs" / f"{job.job_id}.stderr.log"
        assert not log_path.exists() if boundary == "publish" else (
            log_path.read_bytes() == b"clean worker stderr\n"
        )
    finally:
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        h.ledger.close()
    reopened = Ledger.open(tmp_path / "ledger", run_id="after-failure")
    try:
        assert reopened.spent("anthropic") == before
    finally:
        reopened.close()


async def test_rollback_keeps_recognized_leak_with_cleanup_diagnostics() -> None:
    original = KeyLeakError("setup credential failure")
    failure = _storage_error(SYNTHETIC_KEY)

    async def remove() -> None:
        await asyncio.sleep(0)
        raise failure

    with pytest.raises(KeyLeakError) as caught:
        await _rollback(remove(), original)
    assert caught.value is original
    assert "artifact volume full" in _assert_safe_chain(caught.value, failure)


async def test_unprintable_cleanup_does_not_replace_recognized_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = KeyLeakError("recognized worker credential failure")

    class BrokenError(Exception):
        def __str__(self) -> str:
            raise ValueError("formatting failed " + SYNTHETIC_KEY)

    async def fail() -> None:
        raise BrokenError()

    def broken_format(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("formatting failed " + SYNTHETIC_KEY)

    with monkeypatch.context() as formatting:
        formatting.setattr(traceback, "format_exception", broken_format)
        with pytest.raises(KeyLeakError) as caught:
            await finish(fail(), original)
    assert caught.value is original
    assert "could not safely report BrokenError" in str(original.__cause__)
    assert SYNTHETIC_KEY not in "".join(traceback.format_exception(original))


@pytest.mark.parametrize("pending_leak", [False, True])
async def test_finish_waits_through_repeated_cancellation_before_cleanup_failure(
    pending_leak: bool,
) -> None:
    stopping, release = asyncio.Event(), asyncio.Event()
    pending = (KeyLeakError("recognized credential failure") if pending_leak
               else asyncio.CancelledError("first signal"))
    failure = _storage_error()

    async def remove() -> None:
        stopping.set()
        await release.wait()
        raise failure

    task = asyncio.create_task(finish(remove(), pending))
    await stopping.wait()
    task.cancel("later signal")
    await asyncio.sleep(0)
    task.cancel("last signal")
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError) as caught:
        await task
    if pending_leak:
        assert str(caught.value) == "later signal"
    else:
        assert caught.value is pending
    assert caught.value.__cause__ is failure


def test_keyboard_interrupt_during_cleanup_keeps_its_precedence() -> None:
    # asyncio treats KeyboardInterrupt specially. Exercise it in an owned interpreter
    # so pytest's own event loop and signal handling remain outside the reproduction.
    code = (
        "import asyncio, sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "from benchmarks.agentic.cleanup import finish\n"
        "from benchmarks.agentic.protocol import KeyLeakError\n"
        "interrupt = KeyboardInterrupt('operator interrupt')\n"
        "async def remove():\n"
        "    await asyncio.sleep(0)\n"
        "    raise interrupt\n"
        "try:\n"
        "    asyncio.run(finish(remove(), KeyLeakError('recognized credential failure')))\n"
        "except KeyboardInterrupt as caught:\n"
        "    assert caught is interrupt\n"
        "else:\n"
        "    raise AssertionError('operator interrupt was replaced')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-I", "-c", code], env={}, capture_output=True, text=True, timeout=10
    )
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("leak", [False, True])
def test_cli_recognized_frame_and_clean_stderr_publication_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], leak: bool
) -> None:
    for name in PROVIDER_API_KEY_ENV.values():
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(LocalLauncher, "command", lambda self, job: (
        [sys.executable, "-I", "-c", _worker_code(leak=leak)], {}
    ))
    replace = os.replace

    def no_space(src: Any, dst: Any, **kwargs: Any) -> None:
        if str(dst).endswith(".stderr.log"):
            raise _storage_error()
        replace(src, dst, **kwargs)

    monkeypatch.setattr(os, "replace", no_space)
    args = [*_cli_args(tmp_path), "--models", "scripted-anthropic", "--systems", "no-docs"]
    if leak:
        with pytest.raises(SystemExit) as caught:
            main(args)
        assert caught.value.code == 3
    else:
        with pytest.raises(OSError, match="artifact volume full") as caught:
            main(args)
        assert caught.value.errno == errno.ENOSPC
    output = capsys.readouterr()
    assert "artifact volume full" in output.err
    assert SYNTHETIC_KEY not in output.err and output.out == ""
    summary = json.loads((tmp_path / "run" / "run-summary.json").read_text())
    assert summary["status"] == "aborted"
    assert summary["abort_reason"].startswith("KeyLeakError:" if leak else "OSError:")
    events = [json.loads(line) for line in (
        tmp_path / "ledger" / "anthropic.jsonl"
    ).read_text().splitlines()]
    reserve, settle = (event for event in events if event["event"] in ("reserve", "settle"))
    assert settle["outcome"] == "worker_lost"
    assert D(settle["booked"]) == 2 * D(reserve["bound"])
    assert not list((tmp_path / "run" / "logs").glob("*"))
    reopened = Ledger.open(tmp_path / "ledger", run_id="after-cli")
    try:
        assert reopened.spent("anthropic") == D(settle["booked"])
    finally:
        reopened.close()
