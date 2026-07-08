"""Offline stdio adapter for python-docs-mcp-server itself (issue #86).

Runs the product's own MCP server as a benchmark system under test: spawns
it as a local subprocess over stdio, issues the documented retrieval flow
(``search_docs`` -> ``get_docs``) for one corpus question, and returns the
retrieved content plus the raw tool call/response payloads for transcript
recording.

This adapter is intentionally **not** a
``benchmarks.adapters.base.ProviderAdapter`` subclass: that contract models
an LLM provider call (prompt in, generated answer + token usage out). This
adapter models a documentation *retrieval* tool call instead -- there is no
LLM in this offline work package. Pairing the retrieved context with an LLM
adapter (``benchmarks.adapters.openai_adapter`` / ``google_adapter``, issue
#73) is reserved for a maintainer-run live phase; see the confirmed
composition decision on issue #86 (also PLAN.md Amendment 2026-07-08).

Offline guarantee (roadmap principle 2.2, preserved at runtime):

- This module never performs network I/O itself.
- The spawned server process always runs with
  ``PYTHON_DOCS_MCP_DISABLE_AUTO_INDEX=1`` so a missing index can never
  trigger the server's own auto-index-build fallback
  (``mcp_server_python_docs.server._auto_build_symbol_index``), which does
  fetch network resources.
- A missing index is instead detected *before* the subprocess is even
  spawned (see :meth:`PythonDocsMcpAdapter.run`) and reported as a
  ``tool_failure``, never a crash.
- ``search_docs`` and ``get_docs`` themselves only ever read the local
  SQLite index; neither performs network I/O (see
  ``mcp_server_python_docs.services.search`` / ``services.content``).
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from benchmarks.runner import BenchmarkCellFailure

#: Environment variable the server checks to skip its own auto-index-build
#: fallback (see ``mcp_server_python_docs.server._auto_build_symbol_index``).
#: Always set for benchmark-spawned servers so a missing index can never
#: trigger a network-touching auto-build.
DISABLE_AUTO_INDEX_ENV = "PYTHON_DOCS_MCP_DISABLE_AUTO_INDEX"

#: Default per-cell budget for the full search_docs -> get_docs round trip.
DEFAULT_TIMEOUT_SECONDS = 30.0

#: Default number of search_docs hits requested; only the top hit is
#: followed up with get_docs (see module docstring "documented retrieval
#: flow").
DEFAULT_MAX_RESULTS = 3


@dataclass(frozen=True)
class ToolCallRecord:
    """One raw MCP tool call/response pair, recorded verbatim for audit."""

    tool: str
    arguments: dict[str, Any]
    result: dict[str, Any] | None
    is_error: bool


@dataclass(frozen=True)
class PythonDocsMcpResult:
    """Structured output of one search_docs -> get_docs cell run."""

    answer: str
    tool_calls: list[ToolCallRecord]


class _CallToolResult(Protocol):
    """Structural subset of ``mcp.types.CallToolResult`` this module needs.

    Naming this as a ``Protocol`` (rather than importing the concrete SDK
    type at module scope for the type hint) lets unit tests hand in a
    lightweight stand-in without constructing a full MCP session.
    """

    content: list[Any]
    structuredContent: dict[str, Any] | None
    isError: bool


class _CallToolSession(Protocol):
    """Structural subset of ``mcp.ClientSession`` this module needs."""

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> _CallToolResult: ...


def _default_command() -> list[str]:
    return [sys.executable, "-m", "mcp_server_python_docs", "serve"]


def _default_index_path() -> Path:
    from mcp_server_python_docs.storage.db import get_index_path

    return get_index_path()


def _first_text(result: _CallToolResult) -> str | None:
    for block in result.content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            return text
    return None


def _tool_result_payload(result: _CallToolResult) -> dict[str, Any] | None:
    """Extract a JSON-safe raw payload from a tool call result for the transcript.

    Prefers ``structuredContent`` (FastMCP auto-generates this from the
    tool's Pydantic return model). Falls back to the first text content
    block (e.g. on an error result, where ``structuredContent`` is
    ``None``) so the transcript still records something useful for audit.
    """
    if result.structuredContent is not None:
        return result.structuredContent
    text = _first_text(result)
    return {"text": text} if text is not None else None


async def _run_retrieval_flow(
    session: _CallToolSession,
    prompt: str,
    *,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> PythonDocsMcpResult:
    """Run the documented search_docs -> get_docs flow for one prompt.

    Pure orchestration logic against a duck-typed ``session`` (only
    ``call_tool`` is used) so it can be unit tested with a fake/stubbed
    transport -- no real server spawn required. Raises
    ``BenchmarkCellFailure`` (category ``tool_failure``) when either tool
    call itself reports an error.
    """
    tool_calls: list[ToolCallRecord] = []

    search_args: dict[str, Any] = {"query": prompt, "max_results": max_results}
    search_result = await session.call_tool("search_docs", search_args)
    search_payload = _tool_result_payload(search_result)
    tool_calls.append(
        ToolCallRecord(
            tool="search_docs",
            arguments=search_args,
            result=search_payload,
            is_error=bool(search_result.isError),
        )
    )
    if search_result.isError:
        raise BenchmarkCellFailure(
            "tool_failure",
            f"search_docs returned an error: {_first_text(search_result)}",
        )

    hits = (search_payload or {}).get("hits") or []
    if not hits:
        return PythonDocsMcpResult(answer="", tool_calls=tool_calls)

    top_hit = hits[0]
    get_args: dict[str, Any] = {"slug": top_hit.get("slug"), "version": top_hit.get("version")}
    if top_hit.get("anchor"):
        get_args["anchor"] = top_hit["anchor"]
    get_result = await session.call_tool("get_docs", get_args)
    get_payload = _tool_result_payload(get_result)
    tool_calls.append(
        ToolCallRecord(
            tool="get_docs",
            arguments=get_args,
            result=get_payload,
            is_error=bool(get_result.isError),
        )
    )
    if get_result.isError:
        raise BenchmarkCellFailure(
            "tool_failure",
            f"get_docs returned an error: {_first_text(get_result)}",
        )

    answer = str((get_payload or {}).get("content", ""))
    return PythonDocsMcpResult(answer=answer, tool_calls=tool_calls)


class PythonDocsMcpAdapter:
    """Offline stdio adapter: python-docs-mcp-server as a retrieval tool.

    ``run()`` is the synchronous entry point the benchmark runner's
    dispatch registry calls per cell (see
    ``benchmarks.runner._python_docs_mcp_stdio_answer``).
    """

    def __init__(
        self,
        *,
        command: list[str] | None = None,
        index_path: Path | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_results: int = DEFAULT_MAX_RESULTS,
    ) -> None:
        self._command = command if command is not None else _default_command()
        self._index_path = index_path
        self._timeout_seconds = timeout_seconds
        self._max_results = max_results

    def run(self, prompt: str) -> PythonDocsMcpResult:
        """Run one search_docs -> get_docs cell.

        Raises ``BenchmarkCellFailure`` with category ``tool_failure``
        (missing index or a tool-level error), ``timeout`` (the round trip
        exceeded ``timeout_seconds``), or ``mcp_protocol_crash`` (the stdio
        transport/session itself failed) -- never a bare exception, so the
        runner's existing except clause for adapter dispatch (see
        ``benchmarks.runner._execute_cell``) can catch this uniformly.
        """
        index_path = self._index_path if self._index_path is not None else _default_index_path()
        if not index_path.exists():
            raise BenchmarkCellFailure(
                "tool_failure",
                f"no local index found at {index_path}; run 'python-docs-mcp-server "
                "build-index --versions <versions>' before running this benchmark adapter "
                "(dry-run validates without needing one)",
            )
        try:
            return asyncio.run(asyncio.wait_for(self._run_async(prompt), self._timeout_seconds))
        except BenchmarkCellFailure:
            raise
        except TimeoutError as exc:
            raise BenchmarkCellFailure(
                "timeout",
                f"python-docs-mcp-server did not complete search_docs -> get_docs within "
                f"{self._timeout_seconds}s",
            ) from exc
        except Exception as exc:  # noqa: BLE001 - failures are benchmark artifacts
            raise BenchmarkCellFailure("mcp_protocol_crash", str(exc)) from exc

    async def _run_async(self, prompt: str) -> PythonDocsMcpResult:
        # Imported lazily so importing this module never requires the `mcp`
        # client submodules unless an actual run is attempted.
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        env = {**os.environ, DISABLE_AUTO_INDEX_ENV: "1"}
        params = StdioServerParameters(
            command=self._command[0], args=list(self._command[1:]), env=env
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await _run_retrieval_flow(session, prompt, max_results=self._max_results)
