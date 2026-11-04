"""Credential checks on normal MCP stdio lines, before SDK parsing loses fields.

The SDK has no pre-parse observer on stdio_client. Keep its process ownership
and bounded shutdown helpers; only the pipe-to-SessionMessage boundary differs.
No SDK globals or caller-owned streams are patched.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from typing import TextIO

import anyio
from mcp import StdioServerParameters
from mcp.client._transport import TransportStreams
from mcp.client.stdio import (
    _create_platform_compatible_process,
    _get_executable_command,
    _parse_line,
    _stop_server_process,
    get_default_environment,
)
from mcp.shared.message import SessionMessage

from benchmarks.agentic.protocol import (
    MAX_FRAME_BYTES,
    FrameTooLarge,
    KeyLeakError,
    assert_no_key_shaped,
    check_json_text,
)


def checked_line(
    line: bytes, params: StdioServerParameters, check_content: Callable[[bytes], None] | None
) -> SessionMessage | Exception:
    """Scan bytes and decoded JSON before even the JSON-RPC envelope is converted."""
    assert_no_key_shaped(line, where="MCP stdio reply")
    if check_content is not None:
        check_content(line)
    text = line.decode(params.encoding, errors=params.encoding_error_handler)
    check_json_text(text, where="MCP stdio reply", check_content=check_content)
    return _parse_line(text)


class ReplyGuard:
    """Latch a receive-task failure before connection closure wakes a pending SDK call."""

    def __init__(self, check_content: Callable[[bytes], None] | None = None) -> None:
        self.check_content = check_content
        self.failure: KeyLeakError | FrameTooLarge | None = None

    def frame_too_large(self) -> None:
        # Use one fixed diagnostic without interpolating reply bytes. A known leak wins.
        if self.failure is None:
            self.failure = FrameTooLarge("MCP stdio reply exceeds the frame limit")
        self.raise_if_failed()

    def parse(self, line: bytes, params: StdioServerParameters) -> SessionMessage | Exception:
        self.raise_if_failed()
        try:
            return checked_line(line, params, self.check_content)
        except KeyLeakError as error:
            self.failure = error
            raise

    def raise_if_failed(self) -> None:
        failure = self.failure
        if isinstance(failure, (KeyLeakError, FrameTooLarge)):
            raise failure


@asynccontextmanager
async def checked_stdio_client(
    params: StdioServerParameters,
    *,
    errlog: TextIO,
    guard: ReplyGuard,
) -> AsyncIterator[TransportStreams]:
    """SDK-compatible streams, with SDK-owned process groups and bounded cleanup."""
    process = await _create_platform_compatible_process(
        command=await _get_executable_command(params.command),
        args=params.args,
        env=get_default_environment() | (params.env or {}),
        errlog=errlog,
        cwd=params.cwd,
    )
    incoming, read = anyio.create_memory_object_stream[SessionMessage | Exception](0)
    write, outgoing = anyio.create_memory_object_stream[SessionMessage](0)
    writer_done = anyio.Event()

    async def reader() -> None:
        assert process.stdout is not None
        buffer = bytearray()
        delivering = True
        # Drain while shutdown closes stdin/stops the owned process. A blocked
        # delivery is released by read.close(); subsequent lines are still scanned.
        with anyio.CancelScope(shield=True):
            with suppress(anyio.ClosedResourceError, anyio.BrokenResourceError, ConnectionError):
                async with incoming:
                    async for chunk in process.stdout:
                        for part in chunk.splitlines(keepends=True):
                            buffer.extend(part)
                            if len(buffer) > MAX_FRAME_BYTES:
                                guard.frame_too_large()
                            if not buffer.endswith(b"\n"):
                                continue
                            item = guard.parse(bytes(buffer), params)
                            buffer.clear()
                            if delivering:
                                try:
                                    await incoming.send(item)
                                except (anyio.ClosedResourceError, anyio.BrokenResourceError):
                                    delivering = False
            if buffer:
                guard.parse(bytes(buffer), params)

    async def writer() -> None:
        assert process.stdin is not None
        try:
            async with outgoing:
                async for item in outgoing:
                    data = item.message.model_dump_json(by_alias=True, exclude_unset=True) + "\n"
                    await process.stdin.send(
                        data.encode(params.encoding, errors=params.encoding_error_handler)
                    )
        except (anyio.ClosedResourceError, anyio.BrokenResourceError, OSError):
            await incoming.aclose()
        finally:
            writer_done.set()

    async with anyio.create_task_group() as group:
        group.start_soon(reader)
        group.start_soon(writer)
        try:
            yield read, write
        finally:
            with anyio.CancelScope(shield=True):
                read.close()
                write.close()
                with anyio.move_on_after(0.5):
                    await writer_done.wait()
                try:
                    await _stop_server_process(process)
                finally:
                    for stream in (read, write, incoming, outgoing):
                        await stream.aclose()
            group.cancel_scope.cancel()
