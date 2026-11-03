"""The worker process: output protection, then one (model, system) job (ruling B2).

``protect_output`` runs before anything else. It keeps the original stdout as
the protocol channel and sends fds 1 and 2 through a redactor that replaces
the worker's own key, so ``print()``, ``sys.stderr``, ``os.write(2, ...)``,
tracebacks and the MCP servers' inherited stderr all pass it. Only
``ProtocolChannel.send`` writes the protocol fd, and it refuses a frame that
holds the own key.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import sys
import threading
from collections import deque
from contextlib import AsyncExitStack
from dataclasses import asdict
from importlib.metadata import version
from typing import Any

from benchmarks.agentic.bridge import Bridge, SystemSpec, open_bridge, system_spec_from_raw
from benchmarks.agentic.clients import PAID_KEY_ENVS, make_client
from benchmarks.agentic.ledger import Outcome
from benchmarks.agentic.loop import Caps, TrajectoryDiscarded, run_trajectory
from benchmarks.agentic.protocol import KeyLeakError, encode_frame, read_frame
from benchmarks.agentic.schemas import (
    schemas_sha256,
    to_anthropic,
    to_gemini,
    to_openai_responses,
)
from benchmarks.agentic.types import Buckets, ToolSpec
from benchmarks.model_matrix import model_family_from_raw
from benchmarks.runner import BenchmarkValidationError

MAX_RERUNS = 2
_REDACTED = b"[redacted]"
#: A trajectory that ended in one of these leaves the MCP server unusable: reopen it.
_REOPEN_AFTER = frozenset({"mcp_protocol_crash", "cap_tool_timeout"})


def _write_all(fd: int, data: bytes) -> None:
    while data:
        data = data[os.write(fd, data) :]


def _redact(read_fd: int, sink_fd: int, own_key: str | None) -> None:
    key = own_key.encode() if own_key else b""
    hold = max(len(key) - 1, 0)  # a key split across two reads is still caught
    pending = b""
    while chunk := os.read(read_fd, 65536):
        pending += chunk
        if key:
            pending = pending.replace(key, _REDACTED)
        out, pending = (pending[:-hold], pending[-hold:]) if hold else (pending, b"")
        try:
            _write_all(sink_fd, out)
        except OSError:
            pass  # the sink is gone; keep draining so writers never block
    try:
        _write_all(sink_fd, pending)
    except OSError:
        pass
    os.close(read_fd)


class ProtocolChannel:
    def __init__(
        self, protocol_fd: int, own_key: str | None, pipe_write_fd: int, thread: threading.Thread
    ) -> None:
        self._fd = protocol_fd
        self._own_key = own_key.encode() if own_key else None
        self._pipe_write_fd = pipe_write_fd
        self._thread = thread

    def send(self, message: dict[str, Any]) -> None:
        """Write one frame; refuse a frame that holds the own key."""
        frame = encode_frame(message)
        if self._own_key and self._own_key in frame:
            raise KeyLeakError("own key found in output")
        _write_all(self._fd, frame)

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


def protect_output(own_key: str | None) -> ProtocolChannel:
    # shortcut: fd redirection is POSIX only; the harness runs on macOS and Linux
    sys.stdout.flush()
    sys.stderr.flush()
    protocol_fd = os.dup(1)
    sink_fd = os.dup(2)
    read_fd, write_fd = os.pipe()
    os.dup2(write_fd, 1)
    os.dup2(write_fd, 2)
    thread = threading.Thread(target=_redact, args=(read_fd, sink_fd, own_key), daemon=True)
    thread.start()
    sys.stdout = io.TextIOWrapper(
        open(1, "wb", closefd=False),
        encoding="utf-8",
        errors="backslashreplace",
        line_buffering=True,
    )
    sys.stderr = io.TextIOWrapper(
        open(2, "wb", closefd=False),
        encoding="utf-8",
        errors="backslashreplace",
        line_buffering=True,
    )
    return ProtocolChannel(protocol_fd, own_key, write_fd, thread)


class _HostLedger:
    """The loop's LedgerPort over the protocol: each request waits for the host's reply."""

    def __init__(self, channel: ProtocolChannel, reader: asyncio.StreamReader) -> None:
        self._channel, self._reader = channel, reader

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
    if provider == "google":
        return to_gemini(tools)
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


def _system_info(system: SystemSpec, provider: str, bridge: Bridge | None) -> dict[str, Any]:
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
        "client_versions": {
            name: version(name) for name in ("anthropic", "openai", "google-genai", "mcp")
        },
    }


async def _open(stack: AsyncExitStack, system: SystemSpec) -> Bridge | None:
    if system.transport == "none":
        return None
    return await stack.enter_async_context(open_bridge(system))


async def _close(stack: AsyncExitStack) -> None:
    try:
        await stack.aclose()
    except Exception as exc:  # noqa: BLE001 - a dead server may fail its own shutdown
        print(f"closing the MCP bridge failed: {exc!r}", file=sys.stderr)


async def _run_job(channel: ProtocolChannel, reader: asyncio.StreamReader) -> int:
    job = await read_frame(reader)
    if job is None or job.get("type") != "job":
        raise BenchmarkValidationError("the host sent no job")
    family = model_family_from_raw(job["family"])
    system = system_spec_from_raw(job["system"])
    caps = Caps(**job["caps"])
    client = make_client(family, timeout_s=caps.model_call_timeout_s)
    host = _HostLedger(channel, reader)
    queue = deque((item, 1) for item in job["trajectories"])
    seen: set[str] = set()
    stack = AsyncExitStack()
    try:
        bridge = await _open(stack, system)
        channel.send(_system_info(system, family.provider, bridge))
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
                )
            except TrajectoryDiscarded as discarded:
                await host.request(
                    {
                        "type": "discarded",
                        "record": discarded.record,
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
                    channel.send({"type": "not_run", **ident, "reason": "retries_exhausted"})
                continue
            await host.request({"type": "trajectory", "record": record, **ident})
            if bridge is not None and (record["error"] or {}).get("category") in _REOPEN_AFTER:
                await _close(stack)
                stack = AsyncExitStack()
                bridge = await _open(stack, system)
    finally:
        await _close(stack)  # the MCP server exits and drops its copy of fd 2
    channel.send({"type": "done"})
    return 0


def _report(channel: ProtocolChannel, reason: str, code: int) -> int:
    try:
        channel.send({"type": "error", "reason": reason})
    except KeyLeakError:
        channel.send({"type": "error", "reason": "own key found in output"})
        return 3
    return code


async def run_worker(channel: ProtocolChannel, reader: asyncio.StreamReader) -> int:
    try:
        return await _run_job(channel, reader)
    except KeyLeakError:
        return _report(channel, "own key found in output", 3)
    except Exception as exc:  # noqa: BLE001 - every failure is reported to the host
        return _report(channel, f"{type(exc).__name__}: {exc}", 2)


async def _serve(channel: ProtocolChannel) -> int:
    reader = asyncio.StreamReader()
    await asyncio.get_running_loop().connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), open(0, "rb", buffering=0, closefd=False)
    )
    return await run_worker(channel, reader)


def main_worker() -> int:
    """``python -m benchmarks agentic-worker``: protect output first, then run the job."""
    # A worker holds at most one paid key (make_client refuses a second): that one is its own.
    own_key = next((os.environ[name] for name in PAID_KEY_ENVS if os.environ.get(name)), None)
    channel = protect_output(own_key)
    try:
        return asyncio.run(_serve(channel))
    finally:
        channel.close()
