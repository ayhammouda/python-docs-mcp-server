"""Competitor MCP adapter: Context7 (Upstash) -- issue #87.

Runs Context7's own MCP server as a benchmark system under test and issues
its documented retrieval flow (``resolve-library-id`` -> ``query-docs``) for
one corpus question.

Like ``benchmarks.adapters.python_docs_mcp_adapter.PythonDocsMcpAdapter``
(issue #86, the proven template this module mirrors), this adapter is
intentionally **not** a ``benchmarks.adapters.base.ProviderAdapter``
subclass: that contract models an LLM provider call, not a documentation
retrieval tool call.

Pin (PLAN-87-competitor-adapters.md section 1.1, live-probed 2026-07-09):
npm ``@upstash/context7-mcp@3.2.3``, run as a local stdio subprocess via
``npx -y @upstash/context7-mcp@3.2.3`` -- the default and strongest-pinned
mode. A remote streamable-HTTP fallback (``https://mcp.context7.com/mcp``)
also exists and is selectable via ``mode="http"``; the hosted doc index
behind either mode is itself unpinnable, so the effective pin is always
"client 3.2.3 + run date" regardless of mode.

Terms (PLAN-87 section 1.1): Upstash ToS section C.8 forbids benchmarking
without express permission; the maintainer confirmed (assumption A1)
building this adapter now, with live inclusion/publication deferred to a
separate permission decision. Nothing in this module or its tests performs
a live call -- see :func:`benchmarks.adapters.guard.require_live_environment`
/ :func:`benchmarks.adapters.guard.require_live_competitor`, called as the
first statement of :meth:`Context7Adapter.run`.

Auth (optional): ``CONTEXT7_API_KEY`` raises Context7's free-tier rate
limit; keyless use is real but more rate-limited. A manifest entry that
declares key mode (``key_mode=True``) is gated by
``require_live_environment("context7")`` (same shape as every keyed
provider); a keyless entry is gated by
``require_live_competitor("context7")`` (PLAN-87 section 2.2).
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from typing import Any, Protocol

from benchmarks.adapters.guard import require_live_competitor, require_live_environment
from benchmarks.runner import BenchmarkCellFailure

#: Pinned npm package + version (PLAN-87 section 1.1). Never reference the
#: unrelated/stale ``mcp/context7`` Docker image -- 13 months stale per the
#: dossier, explicitly rejected.
PINNED_PACKAGE = "@upstash/context7-mcp@3.2.3"

#: Remote streamable-HTTP fallback endpoint.
DEFAULT_ENDPOINT = "https://mcp.context7.com/mcp"

#: Environment variable read for the optional API key (also the guard's
#: registered key env for provider id "context7" -- see
#: ``benchmarks.adapters.guard.PROVIDER_API_KEY_ENV``).
API_KEY_ENV = "CONTEXT7_API_KEY"

DEFAULT_TIMEOUT_SECONDS = 30.0

#: Library-id-shaped token (e.g. "/python/cpython") used as a fallback when
#: a scripted/live response has no structured candidate list -- see
#: :func:`_extract_library_id`.
_LIBRARY_ID_RE = re.compile(r"/[\w.\-]+/[\w.\-]+")


@dataclass(frozen=True)
class ToolCallRecord:
    """One raw MCP tool call/response pair, recorded verbatim for audit."""

    tool: str
    arguments: dict[str, Any]
    result: dict[str, Any] | None
    is_error: bool


@dataclass(frozen=True)
class Context7Result:
    """Structured output of one resolve-library-id -> query-docs cell run."""

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


def _default_command() -> list[str]:
    return ["npx", "-y", PINNED_PACKAGE]


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


def _extract_library_id(result: _CallToolResult) -> str | None:
    """Pick the top library id out of a ``resolve-library-id`` result.

    Context7's exact structured-response shape is not publicly pinned (the
    dossier verifies the tool *flow*, not a byte-exact schema), so this
    prefers a structured candidate list under any of a few plausible keys
    and falls back to scanning the first text block for a
    ``/org/project``-shaped token -- the documented Context7 library id
    format. Returns ``None`` (never raises) when neither yields a
    candidate, matching the "no hits" honest-empty-answer behavior of
    ``benchmarks.adapters.python_docs_mcp_adapter``.
    """
    payload = _tool_result_payload(result)
    if isinstance(payload, dict):
        candidates = payload.get("results") or payload.get("libraries") or payload.get("hits")
        if isinstance(candidates, list) and candidates:
            first = candidates[0]
            if isinstance(first, dict):
                library_id = (
                    first.get("id") or first.get("libraryId") or first.get("library_id")
                )
                if isinstance(library_id, str) and library_id:
                    return library_id
    text = _first_text(result)
    if text:
        match = _LIBRARY_ID_RE.search(text)
        if match:
            return match.group(0)
    return None


async def _run_context7_flow(
    session: _CallToolSession,
    prompt: str,
    *,
    library_name: str = "Python",
) -> Context7Result:
    """Run the documented resolve-library-id -> query-docs flow for one prompt.

    Pure orchestration against a duck-typed ``session`` so it can be unit
    tested with a scripted double -- no real transport required. Raises
    ``BenchmarkCellFailure`` (category ``tool_failure``) when either tool
    call itself reports an error.
    """
    tool_calls: list[ToolCallRecord] = []

    resolve_args: dict[str, Any] = {"libraryName": library_name, "query": prompt}
    resolve_result = await session.call_tool("resolve-library-id", resolve_args)
    resolve_payload = _tool_result_payload(resolve_result)
    tool_calls.append(
        ToolCallRecord(
            tool="resolve-library-id",
            arguments=resolve_args,
            result=resolve_payload,
            is_error=bool(resolve_result.isError),
        )
    )
    if resolve_result.isError:
        raise BenchmarkCellFailure(
            "tool_failure",
            f"resolve-library-id returned an error: {_first_text(resolve_result)}",
        )

    library_id = _extract_library_id(resolve_result)
    if not library_id:
        return Context7Result(answer="", tool_calls=tool_calls)

    query_args: dict[str, Any] = {"libraryId": library_id, "query": prompt}
    query_result = await session.call_tool("query-docs", query_args)
    query_payload = _tool_result_payload(query_result)
    tool_calls.append(
        ToolCallRecord(
            tool="query-docs",
            arguments=query_args,
            result=query_payload,
            is_error=bool(query_result.isError),
        )
    )
    if query_result.isError:
        raise BenchmarkCellFailure(
            "tool_failure", f"query-docs returned an error: {_first_text(query_result)}"
        )

    answer = _first_text(query_result)
    if answer is None:
        payload = query_payload or {}
        answer = str(payload.get("text", "") or payload.get("content", ""))
    return Context7Result(answer=answer, tool_calls=tool_calls)


def _transport_factory(
    *,
    mode: str,
    command: list[str] | None = None,
    endpoint: str | None = None,
    headers: dict[str, str] | None = None,
    env: dict[str, str] | None = None,
) -> Any:
    """Single owned seam for all transport construction (stdio or HTTP).

    Imported lazily inside this function body (not at module scope) so
    importing ``context7_adapter`` never requires the ``mcp`` client
    submodules unless an actual run is attempted -- same guarantee as
    ``python_docs_mcp_adapter``. Tests patch this one symbol (plus
    ``subprocess``/``socket.socket`` as defense-in-depth for the stdio
    path) to prove fail-closed guard behavior without any transport ever
    being constructed.
    """
    if mode == "stdio":
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client

        assert command  # narrows for type-checkers; ctor always sets a default
        params = StdioServerParameters(command=command[0], args=list(command[1:]), env=env)
        return stdio_client(params)

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
    assert endpoint
    return streamable_http_client(endpoint, http_client=http_client)


class Context7Adapter:
    """Adapter for Context7's MCP server: resolve-library-id -> query-docs.

    ``run()`` is the synchronous entry point the benchmark runner's
    dispatch registry calls per cell (see
    ``benchmarks.runner._context7_answer``).
    """

    def __init__(
        self,
        *,
        mode: str = "stdio",
        command: list[str] | None = None,
        endpoint: str | None = None,
        key_mode: bool = False,
        api_key: str | None = None,
        library_name: str = "Python",
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if mode not in {"stdio", "http"}:
            raise ValueError(f"Context7Adapter mode must be 'stdio' or 'http', got {mode!r}")
        self._mode = mode
        self._command = command if command is not None else _default_command()
        self._endpoint = endpoint if endpoint is not None else DEFAULT_ENDPOINT
        self._key_mode = key_mode
        self._api_key = api_key if api_key is not None else os.environ.get(API_KEY_ENV)
        self._library_name = library_name
        self._timeout_seconds = timeout_seconds

    def run(self, prompt: str) -> Context7Result:
        """Run one resolve-library-id -> query-docs cell.

        The applicable live-competitor guard is the FIRST statement here,
        before any subprocess spawn or transport construction: key mode
        goes through ``require_live_environment("context7")`` (same shape
        as every keyed provider); keyless mode goes through
        ``require_live_competitor("context7")`` (PLAN-87 section 2.2).

        Raises ``BenchmarkCellFailure`` with category ``tool_failure``
        (guard refusal or a tool-level error), ``timeout``, or
        ``mcp_protocol_crash`` -- never a bare exception.
        """
        if self._key_mode:
            require_live_environment("context7")
        else:
            require_live_competitor("context7")
        try:
            return asyncio.run(asyncio.wait_for(self._run_async(prompt), self._timeout_seconds))
        except BenchmarkCellFailure:
            raise
        except TimeoutError as exc:
            raise BenchmarkCellFailure(
                "timeout",
                "Context7 did not complete resolve-library-id -> query-docs within "
                f"{self._timeout_seconds}s",
            ) from exc
        except Exception as exc:  # noqa: BLE001 - failures are benchmark artifacts
            raise BenchmarkCellFailure("mcp_protocol_crash", str(exc)) from exc

    async def _run_async(self, prompt: str) -> Context7Result:
        # Imported lazily so importing this module never requires the `mcp`
        # client submodules unless an actual run is attempted.
        from mcp import ClientSession

        if self._mode == "stdio":
            # The pinned npm client also accepts CONTEXT7_API_KEY via
            # environment (in addition to the remote HTTP mode's header
            # below), so the key -- when present -- is passed through the
            # subprocess environment rather than dropped for this mode.
            env = {**os.environ, API_KEY_ENV: self._api_key} if self._api_key else None
            transport = _transport_factory(mode="stdio", command=self._command, env=env)
        else:
            headers = {API_KEY_ENV: self._api_key} if self._api_key else None
            transport = _transport_factory(mode="http", endpoint=self._endpoint, headers=headers)

        async with transport as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await _run_context7_flow(session, prompt, library_name=self._library_name)
