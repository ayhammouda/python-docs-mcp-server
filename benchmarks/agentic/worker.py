"""The worker process: output protection, then one (model, system) job (ruling B2).

``protect_output`` runs before anything else. It keeps the original stdout as
the protocol channel and sends fds 1 and 2 through a redactor that replaces
every provider secret the worker holds (each set ``PROVIDER_API_KEY_ENV``
value), so ``print()``, ``sys.stderr``, ``os.write(2, ...)``, tracebacks and
the MCP servers' inherited stderr all pass it. Only ``ProtocolChannel.send``
writes the protocol fd, and it refuses a frame that holds any of them.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import shutil
import sys
import threading
from collections import deque
from collections.abc import Callable, Mapping
from contextlib import AsyncExitStack, suppress
from dataclasses import asdict
from importlib.metadata import version
from typing import Any

from benchmarks.adapters.guard import PAID_KEY_ENVS, PROVIDER_API_KEY_ENV
from benchmarks.agentic.bridge import Bridge, SystemSpec, open_bridge, system_spec_from_raw
from benchmarks.agentic.cleanup import add_safe_cleanup_cause, settle
from benchmarks.agentic.ledger import Outcome
from benchmarks.agentic.protocol import (
    MAX_RERUNS as MAX_RERUNS,
)
from benchmarks.agentic.protocol import (
    WORKER_FRAME_LIMIT_EXIT,
    WORKER_LEAK_REASON,
    KeyLeakError,
    encode_frame,
    find_frame_too_large,
    find_key_leak,
    read_frame,
    read_frame_fd,
    scanned_json_bytes,
)
from benchmarks.agentic.types import Buckets, ToolSpec
from benchmarks.model_matrix import model_family_from_raw
from benchmarks.runner import BenchmarkValidationError

#: The type of the private first frame that carries a live worker's one key (ruling A1).
BOOTSTRAP = "bootstrap"
_REDACTED = b"[redacted]"
#: A trajectory that ended in one of these leaves the MCP server unusable: reopen it.
_REOPEN_AFTER = frozenset({"mcp_protocol_crash", "cap_tool_timeout"})


def apply_bootstrap(frame: Mapping[str, Any]) -> None:
    """Put the bootstrap frame's one paid key in this process's environment, in memory only.

    ``env`` holds at most one name, a paid key's. Anything else is refused, naming the
    variables and never a value.
    """
    env = frame.get("env")
    names = sorted(str(name) for name in env) if isinstance(env, Mapping) else []
    if (
        not isinstance(env, Mapping)
        or len(env) > 1
        or not set(names) <= set(PAID_KEY_ENVS)
        or not all(isinstance(value, str) and value for value in env.values())
    ):
        raise BenchmarkValidationError(
            f"a bootstrap frame may set at most one paid key, got {names}"
        )
    os.environ.update(env)


def _write_all(fd: int, data: bytes) -> None:
    while data:
        data = data[os.write(fd, data) :]


def _secrets() -> tuple[bytes, ...]:
    """Every provider secret set in this process's environment, longest first."""
    values = {os.environ.get(name, "") for name in PROVIDER_API_KEY_ENV.values()}
    return tuple(sorted((v.encode() for v in values if v), key=len, reverse=True))


def _redact(read_fd: int, sink_fd: int, secrets: tuple[bytes, ...]) -> None:
    hold = max((len(s) for s in secrets), default=1) - 1  # a split secret is still caught
    pending = b""
    while chunk := os.read(read_fd, 65536):
        pending += chunk
        for secret in secrets:
            pending = pending.replace(secret, _REDACTED)
        out, pending = (pending[:-hold], pending[-hold:]) if hold else (pending, b"")
        with suppress(OSError):  # the sink is gone; keep draining so no writer blocks
            _write_all(sink_fd, out)
    with suppress(OSError):
        _write_all(sink_fd, pending)
    os.close(read_fd)


class ProtocolChannel:
    def __init__(
        self,
        protocol_fd: int,
        secrets: tuple[bytes, ...],
        pipe_write_fd: int,
        thread: threading.Thread,
        streams: tuple[io.TextIOWrapper, io.TextIOWrapper],
    ) -> None:
        self._fd = protocol_fd
        self._secrets = secrets
        self._pipe_write_fd = pipe_write_fd
        self._thread = thread
        self._streams = streams

    def send(self, message: dict[str, Any]) -> None:
        """Write one frame; refuse a frame that holds any of the worker's secrets."""
        frame = encode_frame(message, where="worker frame")
        self.check_content(frame)
        _write_all(self._fd, frame)

    def check_content(self, content: bytes) -> None:
        """Worker-local literal check; callers supply content, never SDK auth or argv."""
        if any(secret in content for secret in self._secrets):
            raise KeyLeakError("own key found in output")

    def close(self) -> None:
        """Drain the redactor: every byte written so far reaches the sink. Restores nothing."""
        sys.stdout.flush()
        sys.stderr.flush()
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
        os.close(devnull)
        os.close(self._pipe_write_fd)
        # A grandchild that still holds the pipe cannot keep the worker alive: the thread
        # is a daemon, and its bytes are dropped, never written unredacted.
        self._thread.join(5)
        for stream in self._streams:  # fds 1 and 2 stay open (closefd=False)
            stream.close()


def _text_stream(fd: int) -> io.TextIOWrapper:
    return io.TextIOWrapper(
        open(fd, "wb", closefd=False),  # closed by ProtocolChannel.close()
        encoding="utf-8",
        errors="backslashreplace",
        line_buffering=True,
    )


def protect_output() -> ProtocolChannel:
    # shortcut: fd redirection is POSIX only; the harness runs on macOS and Linux
    sys.stdout.flush()
    sys.stderr.flush()
    protocol_fd = os.dup(1)
    sink_fd = os.dup(2)
    secrets = _secrets()
    read_fd, write_fd = os.pipe()
    os.dup2(write_fd, 1)
    os.dup2(write_fd, 2)
    thread = threading.Thread(target=_redact, args=(read_fd, sink_fd, secrets), daemon=True)
    thread.start()
    sys.stdout, sys.stderr = streams = (_text_stream(1), _text_stream(2))
    return ProtocolChannel(protocol_fd, secrets, write_fd, thread, streams)


class _HostLedger:
    """The loop's LedgerPort over the protocol: each request waits for the host's reply."""

    def __init__(self, channel: ProtocolChannel, reader: asyncio.StreamReader) -> None:
        self._channel, self._reader = channel, reader
        self.check_failure: Callable[[], None] | None = None

    async def request(self, message: dict[str, Any]) -> dict[str, Any]:
        self._channel.send(message)
        reply = await read_frame(self._reader)
        if reply is None:
            raise ConnectionError("the host closed the job channel")
        return reply

    async def reserve(
        self,
        *,
        provider: str,
        model_id: str,
        input_bound: int,
        output_bound: int,
        kind: str = "model_call",
    ) -> str | None:
        if self.check_failure is not None:
            self.check_failure()
        reply = await self.request(
            {
                "type": "reserve",
                "provider": provider,
                "model_id": model_id,
                "input_bound": input_bound,
                "output_bound": output_bound,
                "kind": kind,
            }
        )
        if self.check_failure is not None:
            self.check_failure()
        return reply["unit_id"] if reply.get("type") == "grant" else None

    async def settle(self, unit_id: str, *, outcome: Outcome, buckets: Buckets | None) -> None:
        await self.request(
            {
                "type": "settle",
                "unit_id": unit_id,
                "outcome": outcome,
                "buckets": None if buckets is None else asdict(buckets),
            }
        )


def _converted(provider: str, tools: list[ToolSpec]) -> tuple[list[dict[str, Any]], list[str]]:
    from benchmarks.agentic.schemas import (
        to_anthropic,
        to_gemini,
        to_openai_chat,
        to_openai_responses,
    )

    if provider == "google":
        return to_gemini(tools)
    if provider == "ollama":
        return to_openai_chat(tools), []
    if provider == "openai":
        return to_openai_responses(tools), []
    return to_anthropic(tools), []


def _index_sha256() -> str | None:
    from mcp_server_python_docs.storage.db import get_index_path

    path = get_index_path()
    if not path.exists():
        return None
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def _system_info(
    system: SystemSpec,
    provider: str,
    bridge: Bridge | None,
    model_limits: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from benchmarks.agentic.schemas import schemas_sha256

    tools_raw = bridge.tools_raw if bridge else []
    converted, dropped = _converted(provider, bridge.tools if bridge else [])
    return {
        "type": "system_info",
        "server_info": bridge.server_info if bridge else None,
        "tools_raw": tools_raw,
        "tools_raw_sha256": schemas_sha256(tools_raw),
        "tools_converted": converted,
        "tools_converted_sha256": schemas_sha256(converted),
        "gemini_dropped": dropped,
        "index_sha256": _index_sha256() if system.id == "python-docs" else None,
        "client_versions": _client_versions(),
        "fetch_extraction": _fetch_extraction() if system.id == "docs-fetch" else None,
        "model_limits": model_limits,
    }


def _fetch_extraction() -> str:
    """readabilipy uses Readability.js only when node is on PATH (design §5.1)."""
    return "readability-js" if shutil.which("node") else "pure-python"


def _client_versions() -> dict[str, str]:
    """Installed versions, read from metadata: no SDK is imported."""
    return {name: version(name) for name in ("anthropic", "openai", "google-genai", "mcp")}


async def _open(
    stack: AsyncExitStack,
    system: SystemSpec,
    *,
    check_content: Callable[[bytes], None] | None = None,
) -> Bridge | None:
    if system.transport == "none":
        return None
    return await stack.enter_async_context(open_bridge(system, check_content=check_content))


async def _close(stack: AsyncExitStack, pending: BaseException | None = None) -> None:
    try:
        await stack.aclose()
    except BaseException as exc:
        if leak := find_key_leak(exc):
            raise leak
        if not isinstance(exc, Exception):
            raise
        if pending is not None:
            if leak := find_key_leak(pending):
                raise leak
            if not isinstance(pending, Exception):
                raise pending from exc
            if frame := find_frame_too_large(pending):
                raise frame
        if frame := find_frame_too_large(exc):
            raise frame
        print(f"closing the MCP bridge failed: {exc!r}", file=sys.stderr)


async def _run_job(
    channel: ProtocolChannel,
    reader: asyncio.StreamReader,
    first: dict[str, Any] | None = None,
    *,
    bootstrapped: bool = False,
) -> int:
    # Imported here, after protect_output: the provider SDKs load behind the redactor.
    from benchmarks.agentic.clients import fetch_model_limits, make_client
    from benchmarks.agentic.loop import Caps, TrajectoryDiscarded, run_trajectory

    job = first if first is not None else await read_frame(reader)
    if job is None or job.get("type") != "job":
        raise BenchmarkValidationError("the host sent no job")
    scanned_json_bytes(job, where="worker job", check_content=channel.check_content)
    family = model_family_from_raw(job["family"])
    system = system_spec_from_raw(job["system"])
    caps = Caps(**job["caps"])
    if family.api != "scripted" and not bootstrapped:
        raise BenchmarkValidationError("a live job needs its key bootstrap frame first")
    host = _HostLedger(channel, reader)
    task = job.get("task", "trajectories")
    if task == "items":
        from benchmarks.agentic.count import run_count_task

        await run_count_task(job, host, channel)
        return 0
    if task == "batches":
        from benchmarks.grading.worker_task import run_batch_task

        await run_batch_task(job, host, channel)
        return 0
    if task != "trajectories":
        raise BenchmarkValidationError(f"unknown worker task {task!r}")
    client = make_client(family, timeout_s=caps.model_call_timeout_s)
    # Model-limit discovery is also an outgoing provider request, before the loop.
    scanned_json_bytes(
        client.request_params(),
        where="outgoing provider settings",
        check_content=channel.check_content,
    )
    execution = {"provider": client.provider, "model_id": client.model_id, "api": client.api}
    # One free models GET per live worker, outside the ledger (round-2 ruling D10).
    limits = None if family.api == "scripted" else await fetch_model_limits(client)
    queue = deque((item, 1) for item in job["trajectories"])
    seen: set[str] = set()
    stack = AsyncExitStack()
    try:
        bridge = await _open(stack, system, check_content=channel.check_content)
        if bridge is not None:
            bridge.check_content = channel.check_content
            host.check_failure = bridge.raise_if_failed
        channel.send(_system_info(system, family.provider, bridge, limits))
        while queue:
            item, attempt = queue.popleft()
            ident = {"corpus_id": item["corpus_id"], "repeat": item["repeat"]}
            try:
                record = await run_trajectory(
                    client=client,
                    bridge=bridge,
                    family=family,
                    prompt=item["prompt"],
                    ledger=host,
                    caps=caps,
                    seen_tool_args=seen,
                    check_content=channel.check_content,
                )
            except TrajectoryDiscarded as discarded:
                if bridge is not None:
                    bridge.raise_if_failed()
                await host.request(
                    {
                        "type": "discarded",
                        "record": {**discarded.record, "execution": execution},
                        **ident,
                        "reason": discarded.reason,
                        "attempt": attempt,
                    }
                )
                if discarded.reason == "budget_stop":
                    for rest, _ in queue:
                        channel.send(
                            {
                                "type": "not_run",
                                "corpus_id": rest["corpus_id"],
                                "repeat": rest["repeat"],
                                "reason": "budget_stop",
                            }
                        )
                    queue.clear()
                elif attempt <= MAX_RERUNS:
                    queue.append((item, attempt + 1))
                else:
                    channel.send(
                        {
                            "type": "not_run",
                            **ident,
                            "reason": "retries_exhausted",
                            "attempt": attempt,
                        }
                    )
                continue
            if bridge is not None:
                bridge.raise_if_failed()
            record["execution"] = execution
            trajectory = {"type": "trajectory", "record": record, **ident, "attempt": attempt}
            await host.request(trajectory)
            if bridge is not None and (record["error"] or {}).get("category") in _REOPEN_AFTER:
                await _close(stack)
                stack = AsyncExitStack()
                bridge = await _open(stack, system, check_content=channel.check_content)
                if bridge is not None:
                    bridge.check_content = channel.check_content
                host.check_failure = bridge.raise_if_failed if bridge is not None else None
    finally:
        await _close(stack, sys.exception())  # the MCP server exits and drops its copy of fd 2
    channel.send({"type": "done"})
    return 0


def _report(channel: ProtocolChannel, reason: str, code: int) -> int:
    with suppress(OSError):
        try:
            channel.send({"type": "error", "reason": reason})
        except KeyLeakError:
            code = 3
            with suppress(KeyLeakError):
                channel.send({"type": "error", "reason": WORKER_LEAK_REASON})
    return code


async def run_worker(
    channel: ProtocolChannel,
    reader: asyncio.StreamReader,
    first: dict[str, Any] | None = None,
    *,
    bootstrapped: bool = False,
) -> int:
    # SDK task groups cancel the task that opened them when their reader fails.
    # Keep that cancellation inside the one job, distinct from our caller's
    # cancellation. wait() observes completion without propagating child cancellation.
    job = asyncio.create_task(_run_job(channel, reader, first, bootstrapped=bootstrapped))
    try:
        await asyncio.wait((job,))
    except asyncio.CancelledError as interrupt:
        job.cancel()
        await settle(job)  # drain the owned SDK tasks/process even on repeated interrupts
        try:
            job.result()
        except BaseException as failure:
            add_safe_cleanup_cause(interrupt, failure)
        raise
    try:
        return job.result()
    except BaseException as exc:  # task groups may combine a leak with cancellation
        if find_key_leak(exc) is not None:
            return _report(channel, WORKER_LEAK_REASON, 3)
        if frame := find_frame_too_large(exc):
            return _report(channel, f"FrameTooLarge: {frame}", WORKER_FRAME_LIMIT_EXIT)
        if not isinstance(exc, Exception):
            raise
        return _report(channel, f"{type(exc).__name__}: {exc}", 2)


async def _serve(
    channel: ProtocolChannel, first: dict[str, Any] | None = None, *, bootstrapped: bool = False
) -> int:
    reader = asyncio.StreamReader()
    await asyncio.get_running_loop().connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), sys.stdin
    )
    return await run_worker(channel, reader, first, bootstrapped=bootstrapped)


def main_worker() -> int:
    """``python -m benchmarks agentic-worker``: the key bootstrap, output protection, the job.

    The first frame is read before anything else, so a bootstrap key is in the environment
    when protect_output collects the secrets it redacts, and before any SDK is imported.
    """
    first = read_frame_fd(0)
    bootstrapped = False
    if first is not None and first.get("type") == BOOTSTRAP:
        apply_bootstrap(first)
        first, bootstrapped = None, True
    channel = protect_output()
    try:
        return asyncio.run(_serve(channel, first, bootstrapped=bootstrapped))
    finally:
        channel.close()
