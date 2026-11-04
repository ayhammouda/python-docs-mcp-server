"""The host orchestrator: launchers, worker supervision and ledger arbitration (design §7.3).

The host never holds a provider key. It spawns one worker per (model, system),
answers its ledger requests through the one LedgerService, scans every frame
and every log for key-shaped strings, and books a lost worker's unit at twice
its bound.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass
from functools import partial
from pathlib import Path
from typing import Any, Protocol

from benchmarks.adapters.guard import PROVIDER_API_KEY_ENV
from benchmarks.agentic.bridge import SystemSpec
from benchmarks.agentic.ledger import LedgerService
from benchmarks.agentic.loop import Caps
from benchmarks.agentic.protocol import (
    MAX_FRAME_BYTES,
    FrameTooLarge,
    KeyLeakError,
    assert_no_key_shaped,
    encode_frame,
    read_frame,
    redact_key_shaped,
)
from benchmarks.agentic.types import Buckets
from benchmarks.agentic.worker import MAX_RERUNS
from benchmarks.model_matrix import ModelFamily
from benchmarks.runner import BenchmarkValidationError

#: What a local worker may see of the host's environment. Never a key, never the live flag.
_LOCAL_ENV = ("PATH", "HOME", "LANG", "TMPDIR", "XDG_CACHE_HOME", "VIRTUAL_ENV")
#: How long teardown waits for a killed worker to exit before it scrubs and settles anyway.
_EXIT_WAIT_S = 30.0


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


async def _teardown(
    proc: asyncio.subprocess.Process,
    anchor: subprocess.Popen[bytes],
    log_path: Path,
    ledger: LedgerService,
    in_flight: str | None,
    stop_worker: Callable[[], Awaitable[None]] | None = None,
) -> str | None:
    """Kill the worker's group, stop its worker, scrub its log and book its open unit.

    Returns the log's key pattern. A failed ``stop_worker`` is re-raised only after the log
    is scrubbed and the unit settled, so the run aborts with nothing left unbooked.
    """
    stop_error: BaseException | None = None
    try:
        # The whole group, even after the worker exited: no descendant outlives it to write.
        try:
            _kill_group(anchor)
            with suppress(TimeoutError):  # a worker that never exits cannot hang the run
                await asyncio.wait_for(proc.wait(), _EXIT_WAIT_S)
        finally:
            await _reap(anchor)
        if stop_worker is not None:
            try:
                await stop_worker()  # a container's removal ends everything in it
            except BaseException as exc:  # noqa: BLE001 - kept, then re-raised below
                stop_error = exc
        pattern = _publish_scrubbed(log_path)  # always, a failed stop_worker included
    finally:  # whatever happened to the log
        if in_flight is not None:  # may have been billed: booked at twice its bound
            await ledger.settle(in_flight, outcome="worker_lost", buckets=None)
    if stop_error is not None:
        raise stop_error
    return pattern


def _publish_scrubbed(log_path: Path) -> str | None:
    """Replace the log by its scrubbed snapshot; the key pattern it held, if any.

    On every exit, a raise or a cancel included, the leaked bytes never stay on disk. The copy
    replaces the log, so a writer still holding it writes into an unlinked file. If the copy
    cannot be published, the log is unlinked instead, for the same reason.
    """
    redacted, pattern = redact_key_shaped(log_path.read_bytes())
    scrubbed = log_path.with_name(log_path.name + ".tmp")
    try:
        scrubbed.write_bytes(redacted)
        os.replace(scrubbed, log_path)
    except OSError:  # a full or read-only disk, say
        log_path.unlink(missing_ok=True)
        scrubbed.unlink(missing_ok=True)
        raise
    return pattern


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
    anchor = _start_anchor()
    try:
        with log_path.open("wb") as log:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                env=env,
                cwd=cwd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=log,
                process_group=anchor.pid,  # the anchor's group, which teardown kills whole
            )
    except BaseException:  # the start error, never a kill error, reaches the summary
        _kill_group(anchor)
        await _reap(anchor)
        raise
    assert proc.stdin is not None and proc.stdout is not None
    in_flight: str | None = None
    reported: set[tuple[str, int]] = set()
    done = False
    lost_reason = "worker_lost"
    try:
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
            proc.stdin.write(encode_frame(job_frame))
            await proc.stdin.drain()
            # Between two frames a worker runs one model call or a trajectory's other tool
            # calls, plus a backoff of at most 120 s: silent for longer, it is lost.
            silence_s = job.caps.trajectory_s + job.caps.model_call_timeout_s
            while (
                message := await asyncio.wait_for(
                    read_frame(proc.stdout, limit=max_frame_bytes), silence_s
                )
            ) is not None:
                where = f"a frame from worker {job.job_id}"
                assert_no_key_shaped(json.dumps(message, ensure_ascii=False), where=where)
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
                    with suppress(TimeoutError):  # its drain writes the last lines to the log
                        await asyncio.wait_for(proc.wait(), 5)
                    if reason == "own key found in output":
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
                    proc.stdin.write(encode_frame(reply))
                    await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass  # the worker died or stalled; handled as a lost worker below
    finally:
        # Runs to its end even when a second abort cancels this task meanwhile; then re-raise.
        teardown = asyncio.ensure_future(
            _teardown(proc, anchor, log_path, ledger, in_flight, partial(launcher.stop_worker, job))
        )
        cancelled: asyncio.CancelledError | None = None
        while not teardown.done():
            try:
                await asyncio.shield(teardown)
            except asyncio.CancelledError as exc:
                cancelled = exc
        pattern = teardown.result()
        if cancelled is not None:
            raise cancelled
    if pattern:
        raise KeyLeakError(
            f"a key-shaped string ({pattern} pattern) was found in {log_path}; redacted there"
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
        except BaseException:
            for task in tasks:
                task.cancel()  # each supervisor kills its worker on the way out
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        failed = False
    finally:
        # Every worker is gone. Runs to its end even when a second abort cancels meanwhile.
        closing = asyncio.ensure_future(launcher.close())
        cancelled: asyncio.CancelledError | None = None
        while not closing.done():
            try:
                await asyncio.shield(closing)
            except asyncio.CancelledError as exc:
                cancelled = exc
        problems = closing.result()  # a KeyLeakError raises here
        if problems and failed:  # the error already propagating is the one to report
            print(f"launcher cleanup problems: {'; '.join(problems)}", file=sys.stderr)
        if cancelled is not None:
            raise cancelled
    if problems:
        raise BenchmarkValidationError(
            f"the run completed, but the launcher reported problems: {'; '.join(problems)}"
        )
