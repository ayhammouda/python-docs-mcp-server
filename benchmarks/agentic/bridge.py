"""Systems manifest, the generic MCP stdio bridge, canonical results and truncation.

Design §5 and §6: every MCP system goes through this one bridge, exposes its
full tool list unfiltered, and has each ``CallToolResult`` turned into one
canonical text string by the same rule.
"""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from mcp import ClientSession, MCPError, StdioServerParameters
from mcp.types import CONNECTION_CLOSED, CallToolResult, PaginatedRequestParams, TextContent

from benchmarks.adapters.guard import EGRESS_URL_ENV, PROVIDER_API_KEY_ENV
from benchmarks.agentic.mcp_stdio import ReplyGuard, checked_stdio_client
from benchmarks.agentic.protocol import (
    assert_no_key_shaped,
    find_frame_too_large,
    find_key_leak,
    scanned_json_bytes,
)
from benchmarks.agentic.types import ToolSpec
from benchmarks.runner import (
    _SAFE_ID,
    BenchmarkValidationError,
    _load_competitors,
    _load_yaml_mapping,
    _validate_manifest_eligibility,
    cell_failure_from,
)

TRUNCATION_MARKER = "\n[benchmark harness: tool result truncated at {limit} UTF-8 bytes]"
#: A manifest env value replaced by the run's egress proxy URL, or dropped without one.
EGRESS_PLACEHOLDER = "{egress}"

_TRANSPORTS: dict[str, Literal["stdio", "none"]] = {"mcp-stdio": "stdio", "none": "none"}
#: System id -> its own competitor key, the only provider key a system may receive.
_OWN_KEYS = {"ref-tools": PROVIDER_API_KEY_ENV["ref"], "context7": PROVIDER_API_KEY_ENV["context7"]}


@dataclass(frozen=True)
class SystemSpec:
    id: str
    transport: Literal["stdio", "none"]  # manifest `adapter`: "mcp-stdio" | "none"
    command: tuple[str, ...]  # "{python}" is replaced by sys.executable at spawn
    env: dict[str, str]
    pass_env: tuple[str, ...]
    raw: dict[str, Any]


@dataclass(frozen=True)
class ToolOutcome:
    text: str  # full canonical text, before truncation
    is_error: bool


class BridgeCrash(Exception):
    """The MCP transport or session failed; the server is gone."""


class ToolBridge(Protocol):
    tools: list[ToolSpec]

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        """Call one tool; a dead transport raises :class:`BridgeCrash`."""
        raise NotImplementedError


def _strings(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise BenchmarkValidationError(f"{label} must be a list of strings")
    return tuple(value)


def system_spec_from_raw(raw: Mapping[str, Any]) -> SystemSpec:
    """Validate one manifest entry; the worker rebuilds its system with this."""
    system_id = raw.get("id")
    if not isinstance(system_id, str) or not _SAFE_ID.fullmatch(system_id):
        raise BenchmarkValidationError(f"system id {system_id!r} must match {_SAFE_ID.pattern}")
    transport = _TRANSPORTS.get(str(raw.get("adapter")))
    if transport is None:
        raise BenchmarkValidationError(
            f"system {system_id!r} has adapter {raw.get('adapter')!r}; "
            f"expected one of {sorted(_TRANSPORTS)}"
        )
    # Keys first, whatever the transport: a manifest entry is snapshotted even if it spawns nothing.
    env = raw.get("env") or {}
    if not isinstance(env, Mapping) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in env.items()
    ):
        raise BenchmarkValidationError(f"system {system_id!r} 'env' must map strings to strings")
    pass_env = _strings(raw.get("pass_env") or [], f"system {system_id!r} 'pass_env'")
    # Every provider key, the paid ones always. A competitor's own key reaches its own system
    # only through pass_env, never as a literal in the manifest.
    names = set(PROVIDER_API_KEY_ENV.values())
    passed = (names - {_OWN_KEYS.get(system_id)}) & set(pass_env)
    keys = sorted((names & set(env)) | passed)
    if keys:
        raise BenchmarkValidationError(
            f"system {system_id!r} must not receive provider keys {keys} through env or pass_env"
        )
    for name, value in env.items():
        assert_no_key_shaped(value, where=f"system {system_id!r} env {name!r}")
    if transport == "none":
        return SystemSpec(system_id, transport, (), {}, (), dict(raw))
    command = _strings(raw.get("command"), f"system {system_id!r} 'command'")
    if not command:
        raise BenchmarkValidationError(f"system {system_id!r} 'command' must not be empty")
    return SystemSpec(system_id, transport, command, dict(env), pass_env, dict(raw))


def load_systems(manifest_path: Path) -> list[SystemSpec]:
    return systems_from_mapping(_load_yaml_mapping(manifest_path, "manifest"))


def systems_from_mapping(data: dict[str, Any]) -> list[SystemSpec]:
    _validate_manifest_eligibility(data)
    return [system_spec_from_raw(competitor.raw) for competitor in _load_competitors(data)]


def canonical_text(
    result: CallToolResult, *, check_content: Callable[[bytes], None] | None = None
) -> str:
    """Compact JSON of ``structured_content`` when present, else the joined text blocks."""
    # Check the decoded MCP envelope before canonicalization drops text, metadata or fields.
    scanned_json_bytes(
        result.model_dump(mode="json", by_alias=True),
        where="MCP tool result",
        check_content=check_content,
    )
    if result.structured_content is not None:
        return scanned_json_bytes(
            result.structured_content,
            where="structured tool result",
            check_content=check_content,
            ensure_ascii=False,
            separators=(",", ":"),
        ).decode()
    return "\n".join(
        block.text if isinstance(block, TextContent) else f"[{block.type} content omitted]"
        for block in result.content
    )


def truncate_utf8(text: str, limit: int) -> tuple[str, bool]:
    """Cut to the longest prefix whose UTF-8 fits ``limit``, plus the marker naming the cap."""
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text, False
    # The prefix is valid UTF-8 except for a character the cut split; "ignore" drops it.
    prefix = data[:limit].decode("utf-8", errors="ignore")
    return prefix + TRUNCATION_MARKER.format(limit=limit), True


class Bridge:
    """One live MCP session; implements :class:`ToolBridge`."""

    check_content: Callable[[bytes], None] | None = None
    _reply_guard: ReplyGuard | None = None

    def __init__(
        self,
        session: ClientSession,
        server_info: dict[str, Any],
        tools_raw: list[dict[str, Any]],
    ) -> None:
        self._session = session
        self.server_info = server_info
        self.tools_raw = tools_raw
        self.tools = [
            ToolSpec(t["name"], t.get("description") or "", t["inputSchema"]) for t in tools_raw
        ]

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.raise_if_failed()
        try:
            result = await self._session.call_tool(name, arguments)
        except BaseException as exc:
            if leak := find_key_leak(exc):
                raise leak
            if not isinstance(exc, Exception):
                raise
            self.raise_if_failed()
            if frame := find_frame_too_large(exc):
                raise frame
            if isinstance(exc, MCPError):
                scanned_json_bytes(
                    exc.error.model_dump(mode="json"),
                    where="MCP tool error",
                    check_content=self.check_content,
                )
                if exc.code == CONNECTION_CLOSED:
                    raise BridgeCrash(f"MCP connection closed during {name}: {exc}") from exc
                return ToolOutcome(text=str(exc.message), is_error=True)
            scanned_json_bytes(
                str(exc), where="MCP tool exception", check_content=self.check_content
            )
            failure = cell_failure_from(exc, timeout_seconds=0.0, label=f"MCP tool {name}")
            raise BridgeCrash(str(failure)) from exc
        self.raise_if_failed()
        return ToolOutcome(
            text=canonical_text(result, check_content=self.check_content),
            is_error=bool(result.is_error),
        )

    def raise_if_failed(self) -> None:
        """Check the receive task before dispatch, reservation or publication."""
        if self._reply_guard is not None:
            self._reply_guard.raise_if_failed()


def server_env(system: SystemSpec, environ: Mapping[str, str]) -> dict[str, str]:
    """The MCP server's environment: its manifest ``env``, then its ``pass_env`` names.

    An ``env`` value of ``{egress}`` becomes the worker's ``BENCHMARK_EGRESS_URL``, and the
    variable is dropped when that is unset: no other proxy ever reaches a server.
    """
    env: dict[str, str] = {}
    for name, value in system.env.items():
        if value != EGRESS_PLACEHOLDER:
            env[name] = value
        elif egress := environ.get(EGRESS_URL_ENV):
            env[name] = egress
    env.update({name: environ[name] for name in system.pass_env if name in environ})
    return env


@asynccontextmanager
async def open_bridge(
    system: SystemSpec, *, check_content: Callable[[bytes], None] | None = None
) -> AsyncIterator[Bridge]:
    if system.transport != "stdio":
        raise BenchmarkValidationError(f"system {system.id!r} has no MCP server to open")
    command = [sys.executable if part == "{python}" else part for part in system.command]
    params = StdioServerParameters(
        command=command[0], args=command[1:], env=server_env(system, os.environ)
    )
    # errlog read now, not at import: the worker points sys.stderr at its redactor.
    guard = ReplyGuard(check_content)
    async with checked_stdio_client(params, errlog=sys.stderr, guard=guard) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            tools_raw: list[dict[str, Any]] = []
            cursor: str | None = None
            while True:
                page = await session.list_tools(
                    params=None if cursor is None else PaginatedRequestParams(cursor=cursor)
                )
                tools_raw += [
                    t.model_dump(mode="json", by_alias=True, exclude_unset=True) for t in page.tools
                ]
                cursor = page.next_cursor
                if not cursor:
                    break
            server_info = initialized.server_info.model_dump(
                mode="json", by_alias=True, exclude_unset=True
            )
            bridge = Bridge(session, server_info, tools_raw)
            bridge._reply_guard = guard
            bridge.check_content = check_content
            yield bridge
