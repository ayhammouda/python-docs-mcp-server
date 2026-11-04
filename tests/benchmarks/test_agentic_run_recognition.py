"""Run-wide recognition and terminal-status fallback, using owned, keyless workers."""

import asyncio
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from benchmarks.adapters.guard import PROVIDER_API_KEY_ENV
from benchmarks.agentic import host, worker
from benchmarks.agentic.host import LocalLauncher, WorkerJob, run_jobs
from benchmarks.agentic.loop import Caps
from benchmarks.agentic.protocol import (
    WORKER_LEAK_REASON,
    FrameTooLarge,
    KeyLeakError,
    encode_frame,
)
from tests.benchmarks.test_agentic_cleanup_failure import (
    _assert_safe_chain,
    _storage_error,
    _worker_code,
)
from tests.benchmarks.test_agentic_host import ANSWER, REPO_ROOT, SYNTHETIC_KEY, D, Host, _job


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for name in PROVIDER_API_KEY_ENV.values():
        monkeypatch.delenv(name, raising=False)
    h = Host(tmp_path)
    yield h
    h.ledger.close()


@pytest.mark.parametrize(
    "secret, interrupted, error_type, reason",
    [
        ("", False, KeyLeakError, WORKER_LEAK_REASON),
        (SYNTHETIC_KEY, False, KeyLeakError, WORKER_LEAK_REASON),
        ("", True, KeyLeakError, WORKER_LEAK_REASON),
        (SYNTHETIC_KEY, True, KeyLeakError, WORKER_LEAK_REASON),
        ("", False, FrameTooLarge, "FrameTooLarge: test limit"),
    ],
)
async def test_sibling_storage_failure_cannot_displace_recognition(
    harness: Host,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    secret: str,
    interrupted: bool,
    error_type: type[Exception],
    reason: str,
) -> None:
    jobs = [_job(tmp_path, [ANSWER], job_id=name) for name in ("recognized", "storage")]
    service = harness.service(jobs[0].family)
    recognized, draining, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    failure = _storage_error(secret)
    pending = []
    stopped = set()
    reserve, stop_grants = service.reserve, service.stop_grants

    async def reserve_and_queue(**kwargs: Any) -> str | None:
        unit = await reserve(**kwargs)
        if unit is not None:
            pending.append(asyncio.create_task(reserve(**kwargs)))
        return unit

    def recognize() -> None:
        stop_grants()
        recognized.set()

    async def on_message(job: WorkerJob, message: dict[str, Any]) -> None:
        if job.job_id == "storage":
            await recognized.wait()
        await harness.on_message(job, message)

    class Launcher(LocalLauncher):
        closed = False

        def command(self, job: WorkerJob) -> tuple[list[str], dict[str, str]]:
            if job.job_id == "recognized":
                # Keep its supervisor in proc.wait after recognition, until the sibling fails.
                code = _worker_code(reason=reason).replace(
                    f"sys.exit({3 if error_type is KeyLeakError else 2})",
                    "import time; time.sleep(30)",
                )
            else:
                code = (
                    "import os, json, sys\n"
                    "n = int.from_bytes(sys.stdin.buffer.read(8), 'big')\n"
                    "sys.stdin.buffer.read(n)\n"
                    "body = json.dumps({'type': 'ready'}).encode()\n"
                    "os.write(1, len(body).to_bytes(8, 'big') + body)\n"
                )
            return [sys.executable, "-I", "-c", code], {}

        async def stop_worker(self, job: WorkerJob) -> None:
            stopped.add(job.job_id)
            if job.job_id == "recognized":
                draining.set()
                await release.wait()

        async def close(self) -> list[str]:
            self.closed = True
            return []

    publish = host._publish_worker_log

    def publish_or_fail(data: bytes, path: Path) -> None:
        if path.name.startswith("storage."):
            raise failure
        publish(data, path)

    monkeypatch.setattr(service, "reserve", reserve_and_queue)
    monkeypatch.setattr(service, "stop_grants", recognize)
    monkeypatch.setattr(host, "_publish_worker_log", publish_or_fail)
    launcher = Launcher()
    task = asyncio.create_task(
        run_jobs(
            jobs,
            launcher=launcher,
            ledger=service,
            on_message=on_message,
            log_dir=tmp_path / "logs",
            cwd=REPO_ROOT,
        )
    )
    try:
        async with asyncio.timeout(10):
            await draining.wait()
            if interrupted:
                task.cancel("operator interrupt")
                await asyncio.sleep(0)
                task.cancel("later interrupt")
                await asyncio.sleep(0)
            release.set()
            with pytest.raises(asyncio.CancelledError if interrupted else error_type) as caught:
                await task
        trace = _assert_safe_chain(caught.value, failure)
        assert "artifact volume full" in trace
        if interrupted:
            assert str(caught.value) == "operator interrupt"
        else:
            assert reason in str(caught.value)
        assert stopped == {"recognized", "storage"} and launcher.closed
        events = harness.ledger.invocation_events()
        assert [event["event"] for event in events] == ["reserve", "settle"]
        assert events[1]["outcome"] == "worker_lost"
        assert D(events[1]["booked"]) == 2 * D(events[0]["bound"])
        assert len(pending) == 1 and await pending[0] is None
        assert (
            await reserve(
                provider="anthropic",
                model_id=jobs[0].family.model_id,
                input_bound=1000,
                output_bound=1024,
            )
            is None
        )
        assert harness.ledger.invocation_events() == events
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, *pending, return_exceptions=True)


def _closed_report_worker(*, reserve: bool, failure_type: str = "KeyLeakError") -> str:
    # Exercise main_worker, protect_output, run_worker and ProtocolChannel.send. Only
    # this child's own protocol fd and a fresh pipe are closed. No credentials exist.
    return (
        "import asyncio, os, sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "from benchmarks.agentic import worker\n"
        f"from benchmarks.agentic.protocol import {failure_type}\n"
        "async def job(channel, reader, *args, **kwargs):\n"
        + (
            "    await worker._HostLedger(channel, reader).reserve(\n"
            "        provider='anthropic', model_id='scripted-1',\n"
            "        input_bound=1000, output_bound=1024)\n"
            if reserve
            else ""
        )
        + "    read_fd, write_fd = os.pipe()\n"
        "    os.close(read_fd)\n"
        "    os.close(channel._fd)\n"
        "    channel._fd = write_fd\n"
        "    await asyncio.sleep(0.05)\n"  # EOF precedes terminal status becoming available.
        f"    raise {failure_type}('synthetic content refusal')\n"
        "worker._run_job = job\n"
        "sys.exit(worker.main_worker())\n"
    )


@pytest.mark.parametrize("failure_type, status", [("KeyLeakError", 3), ("FrameTooLarge", 4)])
def test_genuine_worker_retains_harness_failure_when_error_pipe_is_closed(failure_type, status):
    proc = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            _closed_report_worker(reserve=False, failure_type=failure_type),
        ],
        env={},
        input=encode_frame({"type": "job"}),
        capture_output=True,
        timeout=10,
    )
    assert proc.returncode == status, proc.stderr
    assert proc.stdout == proc.stderr == b""


@pytest.mark.parametrize(
    "error, reason, status",
    [
        (KeyLeakError, WORKER_LEAK_REASON, 3),
        (FrameTooLarge, "frame limit", 4),
    ],
)
async def test_eof_recovers_genuine_worker_recognition_and_settles_once(
    harness: Host,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error,
    reason,
    status,
) -> None:
    job = _job(tmp_path, [ANSWER])
    processes = []
    spawn = asyncio.create_subprocess_exec

    async def record_process(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
        proc = await spawn(*args, **kwargs)
        processes.append(proc)
        return proc

    class Launcher(LocalLauncher):
        def command(self, job: WorkerJob) -> tuple[list[str], dict[str, str]]:
            return [
                sys.executable,
                "-I",
                "-c",
                _closed_report_worker(reserve=True, failure_type=error.__name__),
            ], {}

    monkeypatch.setattr(asyncio, "create_subprocess_exec", record_process)
    service = harness.service(job.family)
    with pytest.raises(error, match=reason):
        await run_jobs(
            [job],
            launcher=Launcher(),
            ledger=service,
            on_message=harness.on_message,
            log_dir=tmp_path / "logs",
            cwd=REPO_ROOT,
        )
    assert [proc.returncode for proc in processes] == [status]
    events = harness.ledger.invocation_events()
    assert [event["event"] for event in events] == ["reserve", "settle"]
    assert events[1]["outcome"] == "worker_lost"
    assert D(events[1]["booked"]) == 2 * D(events[0]["bound"])
    assert harness.messages() == []
    assert (
        await service.reserve(
            provider="anthropic",
            model_id=job.family.model_id,
            input_bound=1000,
            output_bound=1024,
        )
        is None
    )
    assert harness.ledger.invocation_events() == events
    # Both the retained error and the stopped ledger arbiter belong to this invocation.
    fresh = _job(tmp_path, [ANSWER], count=1, job_id="next-invocation")
    await harness.run([fresh])
    assert harness.messages(fresh.job_id)[-1] == {"type": "done"}


@pytest.mark.parametrize("ending", ["death", "timeout", "closed-stdout"])
async def test_unrelated_worker_loss_allows_a_healthy_sibling(
    harness: Host,
    tmp_path: Path,
    ending: str,
) -> None:
    lost = replace(
        _job(tmp_path, [ANSWER], job_id="lost"),
        caps=Caps(trajectory_s=0.1, model_call_timeout_s=0.1),
    )
    healthy = _job(tmp_path, [ANSWER], count=1, job_id="healthy")

    class Launcher(LocalLauncher):
        def command(self, job: WorkerJob) -> tuple[list[str], dict[str, str]]:
            if job.job_id != "lost":
                return super().command(job)
            end = {
                "death": "sys.exit(1)",
                "timeout": "time.sleep(30)",
                "closed-stdout": "os.close(1); time.sleep(30)",
            }[ending]
            code = _worker_code(leak=False).replace("sys.exit(2)", end)
            return [sys.executable, "-I", "-c", "import time\n" + code], {}

    async with asyncio.timeout(5):
        await harness.run([lost, healthy], launcher=Launcher())
    assert {m["reason"] for m in harness.messages("lost")} == {"worker_lost"}
    assert harness.messages("healthy")[-1] == {"type": "done"}
    events = harness.ledger.invocation_events()
    assert [event["event"] for event in events] == ["reserve", "settle", "reserve", "settle"]
    assert [event["outcome"] for event in events if event["event"] == "settle"] == [
        "worker_lost",
        "ok",
    ]


@pytest.mark.parametrize("report_leak", [False, True])
async def test_reporting_preserves_recognition_when_fallback_pipe_breaks(
    monkeypatch: pytest.MonkeyPatch,
    report_leak: bool,
) -> None:
    class Channel:
        def send(self, message: dict[str, Any]) -> None:
            if report_leak and message["reason"] != WORKER_LEAK_REASON:
                raise KeyLeakError("synthetic diagnostic refusal")
            raise BrokenPipeError("closed protocol pipe")

    async def fail(*args: Any, **kwargs: Any) -> int:
        if report_leak:
            raise ValueError("synthetic ordinary error")
        raise KeyLeakError("synthetic content refusal")

    monkeypatch.setattr(worker, "_run_job", fail)
    assert await worker.run_worker(Channel(), asyncio.StreamReader()) == 3  # type: ignore[arg-type]
