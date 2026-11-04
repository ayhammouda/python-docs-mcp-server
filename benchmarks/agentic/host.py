"""The host orchestrator: launchers, worker supervision and ledger arbitration (design §7.3).

The host never holds a provider key. It spawns one worker per (model, system),
answers its ledger requests through the one LedgerService, scans every frame
and every log for key-shaped strings, and books a lost worker's unit at twice
its bound.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import tempfile
import traceback
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass
from functools import partial
from pathlib import Path
from typing import Any, BinaryIO, Protocol

from benchmarks.adapters.guard import PROVIDER_API_KEY_ENV
from benchmarks.agentic.bridge import SystemSpec
from benchmarks.agentic.cleanup import add_cleanup_cause, finish
from benchmarks.agentic.ledger import LedgerService
from benchmarks.agentic.loop import Caps
from benchmarks.agentic.protocol import (
    KEY_SHAPED,
    MAX_FRAME_BYTES,
    WORKER_LEAK_REASON,
    FrameTooLarge,
    KeyLeakError,
    encode_frame,
    read_frame,
    redact_key_shaped,
    redact_key_shaped_text,
)
from benchmarks.agentic.types import Buckets
from benchmarks.agentic.worker import MAX_RERUNS
from benchmarks.model_matrix import ModelFamily
from benchmarks.runner import BenchmarkValidationError

#: What a local worker may see of the host's environment. Never a key, never the live flag.
_LOCAL_ENV = ("PATH", "HOME", "LANG", "TMPDIR", "XDG_CACHE_HOME", "VIRTUAL_ENV")
#: How long teardown waits for a killed worker to exit before it scrubs and settles anyway.
_EXIT_WAIT_S = 30.0
#: Scan a whole snapshot so a token cannot straddle scan boundaries. Fail closed above this size.
_MAX_LOG_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class WorkerJob:
    job_id: str  # f"{family.id}--{system.id}"
    family: ModelFamily  # family.raw["script"] is absolute for scripted families
    system: SystemSpec
    trajectories: tuple[dict[str, Any], ...]  # {"corpus_id", "prompt", "repeat"}, in run order
    caps: Caps

    def report_coordinates(self, message: dict[str, Any]) -> tuple[str, int]:
        """Validate a cell report and return its host-planned output coordinates."""
        if message["type"] != "not_run" or "attempt" in message:
            attempt = message.get("attempt")
            if type(attempt) is not int or not 1 <= attempt <= 1 + MAX_RERUNS:
                raise BenchmarkValidationError(f"worker {self.job_id} reported an invalid attempt")

        corpus_id, repeat = message.get("corpus_id"), message.get("repeat")
        # JSON booleans and floats can compare equal to an integer repeat.
        if isinstance(corpus_id, str) and type(repeat) is int:
            for item in self.trajectories:
                if (item["corpus_id"], item["repeat"]) == (corpus_id, repeat):
                    return item["corpus_id"], item["repeat"]
        raise BenchmarkValidationError(f"worker {self.job_id} reported an unassigned trajectory")


class Launcher(Protocol):
    def supports(self, family: ModelFamily) -> bool:
        """Whether this launcher can run the family's worker."""
        raise NotImplementedError

    def command(self, job: WorkerJob) -> tuple[list[str], dict[str, str]]:
        """The worker's argv and its whole environment."""
        raise NotImplementedError

    def bootstrap_frame(self, job: WorkerJob) -> dict[str, Any] | None:
        """The private first frame for the worker's stdin, or None. Called once, after prepare."""
        raise NotImplementedError

    async def prepare(self, jobs: Sequence[WorkerJob]) -> None:
        """Before any worker starts; raise to stop the run with nothing spent."""
        raise NotImplementedError

    async def stop_worker(self, job: WorkerJob) -> None:
        """After the group kill and before the job's in-flight unit is settled."""
        raise NotImplementedError

    async def close(self) -> list[str]:
        """After every worker is gone: release all; return problems, raise only KeyLeakError."""
        raise NotImplementedError


class LocalLauncher:
    """A local process with no provider key: scripted models only (PR 4 adds containers)."""

    def supports(self, family: ModelFamily) -> bool:
        return family.api == "scripted"

    def command(self, job: WorkerJob) -> tuple[list[str], dict[str, str]]:
        env = {name: os.environ[name] for name in _LOCAL_ENV if name in os.environ}
        return [sys.executable, "-m", "benchmarks", "agentic-worker"], env

    def bootstrap_frame(self, job: WorkerJob) -> dict[str, Any] | None:
        return None

    async def prepare(self, jobs: Sequence[WorkerJob]) -> None:
        return None

    async def stop_worker(self, job: WorkerJob) -> None:
        return None

    async def close(self) -> list[str]:
        return []


def refuse_host_keys(environ: Mapping[str, str]) -> None:
    # Every key name, competitors included. Only agentic-run calls this: the fixed-flow
    # `run` command still counts tokens with ANTHROPIC_API_KEY on the host.
    held = sorted(name for name in set(PROVIDER_API_KEY_ENV.values()) if environ.get(name))
    if held:
        raise BenchmarkValidationError(
            f"the host must never hold a provider key, but {', '.join(held)} is set: "
            "keys go only to the worker of their own provider"
        )


def _start_anchor() -> subprocess.Popen[bytes]:
    """The leader of a worker's process group, in a group of its own in this session.

    Plain Popen, not asyncio: no child watcher ever reaps it, and nothing here waits on it
    before the final group kill. So even an anchor killed from outside stays a zombie, and
    its pid, which is the group id, cannot be reused while the host may still signal it. It
    reads its stdin, a pipe only the host holds, so it also ends with a host killed outright.
    """
    # The host's `cat`; run.sh checks for it, and the worker image has it too
    # (benchmarks/sandbox/Dockerfile).
    return subprocess.Popen(
        ["cat"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        process_group=0,  # a worker cannot join a group of another session
    )


def _kill_group(anchor: subprocess.Popen[bytes]) -> None:
    """Kill the anchor's whole group: the worker and every descendant still in it.

    The anchor is not reaped before this, so the group id is still this job's.
    """
    # Reaches only this host's process tree. A container launcher's `stop_worker` removes its
    # own container, which ends everything in it, before the unit is settled.
    # ESRCH, or EPERM (macOS, when the group holds only zombies, the anchor included).
    with suppress(ProcessLookupError, PermissionError):
        os.killpg(anchor.pid, signal.SIGKILL)


async def _reap(anchor: subprocess.Popen[bytes]) -> None:
    """After the group kill only: close the anchor's stdin and reap it, within the bound."""
    if anchor.stdin is not None:
        anchor.stdin.close()
    with suppress(subprocess.TimeoutExpired):  # an anchor that never exits cannot hang the run
        await asyncio.to_thread(anchor.wait, _EXIT_WAIT_S)


def _diagnostic(message: str) -> None:
    # During terminal hangup even stderr can be closed.
    if sys.stderr is not None:
        with suppress(OSError, ValueError):
            print(redact_key_shaped_text(message), file=sys.stderr, flush=True)


async def _teardown(
    proc: asyncio.subprocess.Process | None,
    anchor: subprocess.Popen[bytes],
    log_path: Path,
    ledger: LedgerService,
    in_flight: str | None,
    worker_log: BinaryIO,
    stop_worker: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Kill the worker, scrub its log and book any open unit before teardown returns."""

    errors: list[BaseException] = []
    location = f"worker teardown ({log_path.name})"

    @contextmanager
    def attempt() -> Iterator[None]:
        # Report each job's failures before gather drains it, and attempt every cleanup step.
        try:
            yield
        except BaseException as exc:  # noqa: BLE001 - propagate after all cleanup steps
            if isinstance(exc, KeyLeakError):
                ledger.stop_grants()
            errors.append(exc)

    with attempt():
        _kill_group(anchor)
        if proc is not None:
            with suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), _EXIT_WAIT_S)
    with attempt():
        await _reap(anchor)
    if stop_worker is not None:
        with attempt():
            await stop_worker()
    with attempt():
        redacted, pattern = _scrubbed_snapshot(worker_log)
        if pattern:
            ledger.stop_grants()
        detected = f"a key-shaped string ({pattern} pattern) was found in {log_path}"
        try:
            _publish_worker_log(redacted, log_path)
        except Exception:
            if pattern:
                # Keep the leak primary and report the storage error after settlement.
                errors.append(KeyLeakError(f"{detected}; scrubbed log publication failed"))
            raise
        if pattern:
            raise KeyLeakError(f"{detected}; redacted there")
    with attempt():
        worker_log.close()
    if in_flight is not None:
        with attempt():
            await ledger.settle(in_flight, outcome="worker_lost", buckets=None)

    # Formatting failures must never interrupt mandatory scrubbing or accounting.
    for i, exc in enumerate(errors):
        try:
            detail = f"{location}: {type(exc).__name__}: {redact_key_shaped_text(str(exc))}"
            if KEY_SHAPED.search("".join(traceback.format_exception(exc, limit=0))):
                # An outer exception may be clean while its notes or chained daemon error leak.
                errors[i] = KeyLeakError(detail + " (key shape redacted)")
                ledger.stop_grants()
            else:
                exc.add_note(location)
            _diagnostic(detail)
        except Exception:  # noqa: BLE001 - an unscannable failure must not enter the traceback
            errors[i] = KeyLeakError(f"{location}: could not safely report {type(exc).__name__}")
            ledger.stop_grants()
    if errors:
        for primary, secondary in zip(errors, errors[1:]):
            add_cleanup_cause(primary, secondary)
        raise errors[0]


def _scrubbed_snapshot(worker_log: BinaryIO) -> tuple[bytes, str | None]:
    """Late writers keep their anonymous inode and offset."""
    fd = worker_log.fileno()
    size = os.fstat(fd).st_size
    if size > _MAX_LOG_BYTES:
        raise BenchmarkValidationError(f"worker log exceeds the {_MAX_LOG_BYTES}-byte scan limit")
    data = os.pread(fd, size, 0)
    if len(data) != size:
        raise OSError("incomplete worker log read")
    return redact_key_shaped(data)


def _publish_worker_log(redacted: bytes, log_path: Path) -> None:
    stage = tempfile.NamedTemporaryFile(dir=log_path.parent, suffix=".tmp", delete=False)
    try:
        with stage:
            if stage.write(redacted) != len(redacted):
                raise OSError("incomplete scrubbed log write")
        os.replace(stage.name, log_path)
    except BaseException:
        os.unlink(stage.name)
        raise


async def _supervise(
    job: WorkerJob,
    *,
    launcher: Launcher,
    ledger: LedgerService,
    on_message: Callable[[WorkerJob, dict[str, Any]], Awaitable[None]],
    log_dir: Path,
    cwd: Path,
    max_frame_bytes: int,
) -> None:
    argv, env = launcher.command(job)
    log_path = log_dir / f"{job.job_id}.stderr.log"
    # On POSIX the TemporaryFile fallback unlinks before returning the descriptor.
    with tempfile.TemporaryDirectory() as private_dir:
        log = tempfile.TemporaryFile(dir=private_dir, buffering=0)
    try:
        anchor = _start_anchor()
    except BaseException:
        log.close()
        raise
    proc = None
    in_flight: str | None = None
    reported: set[tuple[str, int]] = set()
    done = False
    lost_reason = "worker_lost"
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            env=env,
            cwd=cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=log,
            process_group=anchor.pid,  # the anchor's group, which teardown kills whole
        )
        assert proc.stdin is not None and proc.stdout is not None
        try:
            # The private key frame first, then the job. The frame is never logged or kept.
            if (bootstrap := launcher.bootstrap_frame(job)) is not None:
                proc.stdin.write(encode_frame(bootstrap))
                del bootstrap
                await proc.stdin.drain()
            job_frame = {
                "type": "job",
                "job_id": job.job_id,
                "family": job.family.raw,
                "system": job.system.raw,
                "trajectories": list(job.trajectories),
                "caps": asdict(job.caps),
            }
            proc.stdin.write(encode_frame(job_frame, where="job frame"))
            await proc.stdin.drain()
            # Between two frames a worker runs one model call or a trajectory's other tool
            # calls, plus a backoff of at most 120 s: silent for longer, it is lost.
            silence_s = job.caps.trajectory_s + job.caps.model_call_timeout_s
            while True:
                # Decode in this task so a credential failure latches before another task runs.
                async with asyncio.timeout(silence_s):
                    message = await read_frame(
                        proc.stdout, limit=max_frame_bytes, where="worker frame"
                    )
                if message is None:
                    break
                kind = message.get("type")
                reply: dict[str, Any] | None = None
                if kind == "reserve":
                    if in_flight is not None:
                        raise BenchmarkValidationError(f"worker {job.job_id} reserved twice")
                    # The job's own provider and model, never the worker's claim.
                    in_flight = await ledger.reserve(
                        provider=job.family.provider,
                        model_id=job.family.model_id,
                        input_bound=int(message["input_bound"]),
                        # Never below the cap the family sends: a worker cannot shrink its bound.
                        output_bound=max(
                            int(message["output_bound"]), job.family.max_output_tokens or 0
                        ),
                        kind=str(message.get("kind", "model_call")),
                        job_id=job.job_id,
                    )
                    reply = (
                        {"type": "grant", "unit_id": in_flight} if in_flight else {"type": "deny"}
                    )
                elif kind == "settle":
                    if in_flight is None or message.get("unit_id") != in_flight:
                        raise BenchmarkValidationError(
                            f"worker {job.job_id} settled a unit it does not hold"
                        )
                    buckets = message.get("buckets")
                    await ledger.settle(
                        in_flight,
                        outcome=message["outcome"],
                        buckets=None if buckets is None else Buckets(**buckets),
                    )
                    in_flight = None
                    reply = {"type": "ack"}
                elif kind == "error":
                    reason = str(message.get("reason"))
                    if reason in (WORKER_LEAK_REASON, "own key found in output"):
                        ledger.stop_grants()
                    with suppress(TimeoutError):  # its drain writes the last lines to the log
                        await asyncio.wait_for(proc.wait(), 5)
                    if reason in (WORKER_LEAK_REASON, "own key found in output"):
                        raise KeyLeakError(f"worker {job.job_id}: {reason}")
                    if reason.startswith("FrameTooLarge:"):
                        raise FrameTooLarge(f"worker {job.job_id}: {reason}")
                    # Any other failure costs only this job's cells, like a lost worker.
                    lost_reason = "worker_error"
                    await on_message(job, {"type": "worker_error", "reason": reason})
                    break
                else:
                    if kind in ("trajectory", "discarded", "not_run"):
                        coordinates = job.report_coordinates(message)
                        # A retries_exhausted discard still owes its rerun or a final not_run.
                        if kind != "discarded" or message["reason"] == "budget_stop":
                            reported.add(coordinates)
                    await on_message(job, message)
                    done = done or kind == "done"
                    if kind in ("trajectory", "discarded"):
                        reply = {"type": "ack"}
                if reply is not None:
                    proc.stdin.write(encode_frame(reply, where="host reply"))
                    await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass  # the worker died or stalled; handled as a lost worker below
    except KeyLeakError:
        ledger.stop_grants()
        raise
    finally:
        await finish(
            _teardown(
                proc, anchor, log_path, ledger, in_flight, log, partial(launcher.stop_worker, job)
            ),
            sys.exception(),
        )
    if not done:
        for item in job.trajectories:
            if (item["corpus_id"], item["repeat"]) not in reported:
                await on_message(
                    job,
                    {
                        "type": "not_run",
                        "corpus_id": item["corpus_id"],
                        "repeat": item["repeat"],
                        "reason": lost_reason,
                    },
                )


async def run_jobs(
    jobs: Sequence[WorkerJob],
    *,
    launcher: Launcher,
    ledger: LedgerService,
    on_message: Callable[[WorkerJob, dict[str, Any]], Awaitable[None]],
    log_dir: Path,
    cwd: Path,
    max_frame_bytes: int = MAX_FRAME_BYTES,
) -> None:
    """Run every job in its own worker, in parallel; a harness error kills them all."""
    refuse_host_keys(os.environ)
    unsupported = sorted({job.family.id for job in jobs if not launcher.supports(job.family)})
    if unsupported:
        raise BenchmarkValidationError(
            f"the {type(launcher).__name__} cannot run model families {unsupported}: "
            "the local launcher runs only scripted models; use --launcher container"
        )
    log_dir.mkdir(parents=True, exist_ok=True)
    failed = True
    try:
        await launcher.prepare(jobs)  # raises before any worker starts
        tasks = [
            asyncio.create_task(
                _supervise(
                    job,
                    launcher=launcher,
                    ledger=ledger,
                    on_message=on_message,
                    log_dir=log_dir,
                    cwd=cwd,
                    max_frame_bytes=max_frame_bytes,
                )
            )
            for job in jobs
        ]
        try:
            await asyncio.gather(*tasks)
        except BaseException as exc:
            for task in tasks:
                task.cancel()  # each supervisor kills its worker on the way out
            await finish(asyncio.gather(*tasks, return_exceptions=True), exc)
            raise
        failed = False
    finally:
        reported: BenchmarkValidationError | None = None

        async def close() -> None:
            nonlocal reported
            if problems := await launcher.close():
                detail = redact_key_shaped_text("; ".join(problems))
                if failed:
                    _diagnostic(f"launcher cleanup problems: {detail}")
                reason = (
                    "launcher cleanup problems"
                    if failed
                    else "the run completed, but the launcher reported problems"
                )
                reported = BenchmarkValidationError(f"{reason}: {detail}")
                raise reported

        pending = sys.exception()
        try:
            await finish(close(), pending)
        except KeyLeakError as leak:
            if isinstance(pending, Exception):
                # The CLI prints only the final message. Keep earlier resource IDs visible.
                earlier = redact_key_shaped_text(str(pending))
                raise KeyLeakError(
                    f"{redact_key_shaped_text(str(leak))}; earlier failure: {earlier}"
                ) from None
            raise
        except BenchmarkValidationError as exc:
            if not failed or exc is not reported:
                raise
