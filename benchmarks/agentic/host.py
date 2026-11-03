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
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
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
from benchmarks.model_matrix import ModelFamily
from benchmarks.runner import BenchmarkValidationError

#: What a local worker may see of the host's environment. Never a key, never the live flag.
_LOCAL_ENV = ("PATH", "HOME", "LANG", "TMPDIR", "XDG_CACHE_HOME", "VIRTUAL_ENV")


@dataclass(frozen=True)
class WorkerJob:
    job_id: str  # f"{family.id}--{system.id}"
    family: ModelFamily  # family.raw["script"] is absolute for scripted families
    system: SystemSpec
    trajectories: tuple[dict[str, Any], ...]  # {"corpus_id", "prompt", "repeat"}, in run order
    caps: Caps


class Launcher(Protocol):
    def supports(self, family: ModelFamily) -> bool:
        """Whether this launcher can run the family's worker."""
        raise NotImplementedError

    def command(self, job: WorkerJob) -> tuple[list[str], dict[str, str]]:
        """The worker's argv and its whole environment."""
        raise NotImplementedError


class LocalLauncher:
    """A local process with no provider key: scripted models only (PR 4 adds containers)."""

    def supports(self, family: ModelFamily) -> bool:
        return family.api == "scripted"

    def command(self, job: WorkerJob) -> tuple[list[str], dict[str, str]]:
        env = {name: os.environ[name] for name in _LOCAL_ENV if name in os.environ}
        return [sys.executable, "-m", "benchmarks", "agentic-worker"], env


def refuse_host_keys(environ: Mapping[str, str]) -> None:
    # Every key name, competitors included. Only agentic-run calls this: the fixed-flow
    # `run` command still counts tokens with ANTHROPIC_API_KEY on the host.
    held = sorted(name for name in set(PROVIDER_API_KEY_ENV.values()) if environ.get(name))
    if held:
        raise BenchmarkValidationError(
            f"the host must never hold a provider key, but {', '.join(held)} is set: "
            "keys go only to the worker of their own provider"
        )


async def _read(
    reader: asyncio.StreamReader, caps: Caps, max_frame_bytes: int
) -> dict[str, Any] | None:
    """The next frame, or TimeoutError once the worker has been silent too long.

    Between two frames a worker runs at most one model call (model_call_timeout_s) or the
    rest of a trajectory's tool calls (trajectory_s), plus a backoff of at most 120 s, which
    model_call_timeout_s (300 s or more) covers.
    """
    async with asyncio.timeout(caps.trajectory_s + caps.model_call_timeout_s):
        return await read_frame(reader, limit=max_frame_bytes)


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
    with log_path.open("wb") as log:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            env=env,
            cwd=cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=log,
        )
    assert proc.stdin is not None and proc.stdout is not None
    in_flight: str | None = None
    reported: set[tuple[str, int]] = set()
    done = False
    lost_reason = "worker_lost"
    try:
        try:
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
            while (message := await _read(proc.stdout, job.caps, max_frame_bytes)) is not None:
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
                    if reason == "own key found in output":
                        raise KeyLeakError(f"worker {job.job_id}: {reason}")
                    if reason.startswith("FrameTooLarge:"):
                        raise FrameTooLarge(f"worker {job.job_id}: {reason}")
                    # Any other failure costs only this job's cells, like a lost worker.
                    lost_reason = "worker_error"
                    await on_message(job, {"type": "worker_error", "reason": reason})
                    break
                else:
                    await on_message(job, message)
                    if kind in ("trajectory", "not_run"):
                        reported.add((message["corpus_id"], message["repeat"]))
                    done = done or kind == "done"
                    if kind in ("trajectory", "discarded"):
                        reply = {"type": "ack"}
                if reply is not None:
                    proc.stdin.write(encode_frame(reply))
                    await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass  # the worker died or stalled; handled as a lost worker below
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        if in_flight is not None:  # may have been billed: booked at twice its bound
            await ledger.settle(in_flight, outcome="worker_lost", buckets=None)
    redacted, pattern = redact_key_shaped(log_path.read_bytes())
    if pattern:
        log_path.write_bytes(redacted)  # the leaked bytes never stay on disk
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
            "the local launcher runs only scripted models (live runs need PR 4's containers)"
        )
    log_dir.mkdir(parents=True, exist_ok=True)
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
