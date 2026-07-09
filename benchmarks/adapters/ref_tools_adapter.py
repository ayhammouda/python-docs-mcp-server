"""Competitor MCP adapter: Ref.tools -- issue #87.

Runs Ref.tools' MCP server as a benchmark system under test and issues its
documented retrieval flow (``ref_search_documentation`` -> ``ref_read_url``)
for one corpus question. Ref.tools' own session behavior dedupes repeated
results and trims reads based on session history, so a fresh MCP session
per question is required for comparable results -- the benchmark runner
already instantiates adapters fresh per cell (PLAN-87 section 1.4), so this
is satisfied by construction; nothing here reuses a session across
``run()`` calls.

Like ``benchmarks.adapters.python_docs_mcp_adapter.PythonDocsMcpAdapter``
(issue #86, the proven template this module mirrors), this adapter is
intentionally **not** a ``benchmarks.adapters.base.ProviderAdapter``
subclass.

Pin (PLAN-87 section 1.4): npm client ``ref-tools-mcp@3.0.3`` (MIT) or
Docker ``mcp/ref-tools-mcp`` digest ``sha256:8b5fcfe3...``; the
``api.ref.tools`` backend is itself unpinnable, so the effective pin is
"client 3.0.3 + run date". This adapter defaults to the vendor-recommended
remote streamable-HTTP mode (``https://api.ref.tools/mcp`` with an
``x-ref-api-key`` header) -- no local Node/npx process required. A legacy
stdio mode (``npx ref-tools-mcp@3.0.3``, env ``REF_API_KEY``) exists but is
not the default here.

Terms (PLAN-87 section 1.4): no benchmark/evaluation clause, but sections 3
and 7 of Ref.tools' terms restrict automated/systematic access without
written permission; verdict recorded as
"unclear-permission-recommended". Maintainer decision: build now, live use
needs a maintainer key + credits, publication needs the permission decision
(assumption A4).

Auth: an API key is REQUIRED (no keyless mode exists for Ref.tools, unlike
GitMCP/DeepWiki/keyless-Context7). Gated by
:func:`benchmarks.adapters.guard.require_live_environment` (registered key
env: ``REF_API_KEY``, see
``benchmarks.adapters.guard.PROVIDER_API_KEY_ENV``), called as the first
statement of :meth:`RefToolsAdapter.run`.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from typing import Any, Protocol

from benchmarks.adapters.guard import require_live_environment
from benchmarks.runner import BenchmarkCellFailure

DEFAULT_ENDPOINT = "https://api.ref.tools/mcp"

#: Header the vendor-recommended remote streamable-HTTP mode requires
#: (PLAN-87 section 1.4).
API_KEY_HEADER = "x-ref-api-key"

#: Environment variable read for the required API key (also the guard's
#: registered key env for provider id "ref" -- see
#: ``benchmarks.adapters.guard.PROVIDER_API_KEY_ENV``).
API_KEY_ENV = "REF_API_KEY"

DEFAULT_TIMEOUT_SECONDS = 30.0

_URL_RE = re.compile(r"https?://[^\s\"']+")


@dataclass(frozen=True)
class ToolCallRecord:
    """One raw MCP tool call/response pair, recorded verbatim for audit."""

    tool: str
    arguments: dict[str, Any]
    result: dict[str, Any] | None
    is_error: bool


@dataclass(frozen=True)
class RefToolsResult:
    """Structured output of one ref_search_documentation -> ref_read_url cell run."""

    answer: str
    tool_calls: list[ToolCallRecord]


class _CallToolResult(Protocol):
    """Structural subset of ``mcp.types.CallToolResult`` this module needs."""

    content: list[Any]
    structuredContent: dict[str, Any] | None
    isError: bool


class _CallToolSession(Protocol):
    """Structural subset of ``mcp.ClientSession`` this module needs."""

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> _CallToolResult:
        """Invoke one MCP tool by name and return its raw call result."""
        raise NotImplementedError


def _first_text(result: _CallToolResult) -> str | None:
    for block in result.content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            return text
    return None


def _tool_result_payload(result: _CallToolResult) -> dict[str, Any] | None:
    if result.structuredContent is not None:
        return result.structuredContent
    text = _first_text(result)
    return {"text": text} if text is not None else None


def _extract_first_url(result: _CallToolResult) -> str | None:
    """Pick the top hit's URL out of a ``ref_search_documentation`` result.

    Prefers a structured hit list (``results``/``hits``) with a ``url``
    field, falling back to scanning the first text block for an
    ``http(s)://`` URL. Returns ``None`` (never raises) when neither
    yields a candidate.
    """
    payload = _tool_result_payload(result)
    if isinstance(payload, dict):
        candidates = payload.get("results") or payload.get("hits")
        if isinstance(candidates, list) and candidates:
            first = candidates[0]
            if isinstance(first, dict):
                url = first.get("url")
                if isinstance(url, str) and url:
                    return url
    text = _first_text(result)
    if text:
        match = _URL_RE.search(text)
        if match:
            return match.group(0)
    return None


async def _run_ref_tools_flow(session: _CallToolSession, prompt: str) -> RefToolsResult:
    """Run the documented ref_search_documentation -> ref_read_url flow for one prompt.

    Pure orchestration against a duck-typed ``session`` so it can be unit
    tested with a scripted double -- no real transport required. Raises
    ``BenchmarkCellFailure`` (category ``tool_failure``) when either tool
    call itself reports an error.
    """
    tool_calls: list[ToolCallRecord] = []

    search_args: dict[str, Any] = {"query": prompt}
    search_result = await session.call_tool("ref_search_documentation", search_args)
    search_payload = _tool_result_payload(search_result)
    tool_calls.append(
        ToolCallRecord(
            tool="ref_search_documentation",
            arguments=search_args,
            result=search_payload,
            is_error=bool(search_result.isError),
        )
    )
    if search_result.isError:
        raise BenchmarkCellFailure(
            "tool_failure",
            f"ref_search_documentation returned an error: {_first_text(search_result)}",
        )

    url = _extract_first_url(search_result)
    if url is None:
        return RefToolsResult(answer="", tool_calls=tool_calls)

    read_args: dict[str, Any] = {"url": url}
    read_result = await session.call_tool("ref_read_url", read_args)
    read_payload = _tool_result_payload(read_result)
    tool_calls.append(
        ToolCallRecord(
            tool="ref_read_url",
            arguments=read_args,
            result=read_payload,
            is_error=bool(read_result.isError),
        )
    )
    if read_result.isError:
        raise BenchmarkCellFailure(
            "tool_failure", f"ref_read_url returned an error: {_first_text(read_result)}"
        )

    answer = _first_text(read_result)
    if answer is None:
        payload = read_payload or {}
        answer = str(payload.get("content", "") or payload.get("text", ""))
    return RefToolsResult(answer=answer, tool_calls=tool_calls)


def _transport_factory(endpoint: str, headers: dict[str, str] | None) -> Any:
    """Single owned seam for all transport construction.

    Imported lazily inside this function body so importing
    ``ref_tools_adapter`` never requires the ``mcp`` client submodules
    unless an actual run is attempted. Tests patch this one symbol to
    prove fail-closed guard behavior without any transport ever being
    constructed.
    """
    import httpx
    from mcp.client.streamable_http import streamable_http_client

    # Built directly (rather than via the SDK's internal
    # ``create_mcp_http_client`` helper, which pyright flags as a private
    # re-export) with the same defaults that helper applies: redirects
    # followed, a 30s timeout unless the caller already sized one via
    # ``timeout_seconds`` on the adapter.
    http_client = (
        httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=30.0)
        if headers
        else None
    )
    return streamable_http_client(endpoint, http_client=http_client)


class RefToolsAdapter:
    """Adapter for Ref.tools' MCP server: ref_search_documentation -> ref_read_url.

    ``run()`` is the synchronous entry point the benchmark runner's
    dispatch registry calls per cell (see
    ``benchmarks.runner._ref_tools_answer``). The runner constructs a fresh
    instance per cell, which -- combined with this class never reusing a
    session across calls -- satisfies Ref.tools' fresh-session-per-question
    requirement (see module docstring) by construction.
    """

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._endpoint = endpoint if endpoint is not None else DEFAULT_ENDPOINT
        self._api_key = api_key if api_key is not None else os.environ.get(API_KEY_ENV)
        self._timeout_seconds = timeout_seconds

    def run(self, prompt: str) -> RefToolsResult:
        """Run one Ref.tools cell.

        ``require_live_environment("ref")`` is the FIRST statement here,
        before any transport construction -- Ref.tools has no keyless mode
        (PLAN-87 section 1.4), so it always goes through the keyed guard,
        unlike GitMCP/DeepWiki/keyless-Context7.

        Raises ``BenchmarkCellFailure`` with category ``tool_failure``
        (guard refusal or a tool-level error), ``timeout``, or
        ``mcp_protocol_crash`` -- never a bare exception.
        """
        require_live_environment("ref")
        try:
            return asyncio.run(asyncio.wait_for(self._run_async(prompt), self._timeout_seconds))
        except BenchmarkCellFailure:
            raise
        except TimeoutError as exc:
            raise BenchmarkCellFailure(
                "timeout",
                "Ref.tools did not complete ref_search_documentation -> ref_read_url within "
                f"{self._timeout_seconds}s",
            ) from exc
        except Exception as exc:  # noqa: BLE001 - failures are benchmark artifacts
            raise BenchmarkCellFailure("mcp_protocol_crash", str(exc)) from exc

    async def _run_async(self, prompt: str) -> RefToolsResult:
        # Imported lazily so importing this module never requires the `mcp`
        # client submodules unless an actual run is attempted.
        from mcp import ClientSession

        headers = {API_KEY_HEADER: self._api_key} if self._api_key else None
        async with _transport_factory(self._endpoint, headers) as (read, write, _get_session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await _run_ref_tools_flow(session, prompt)
