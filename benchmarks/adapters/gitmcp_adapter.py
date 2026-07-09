"""Competitor MCP adapter: GitMCP -- issue #87.

Runs GitMCP's hosted MCP server as a benchmark system under test. GitMCP's
own doc-search tool (``search_cpython_documentation``) is verified (PLAN-87
section 1.2, live-probed 2026-07-09) to return no results for stdlib
queries -- cpython has no ``llms.txt`` and the tool falls back to indexing
only the repo README -- so the adapter tries doc-search first (the honest,
advertised path; the empty result is a legitimate benchmark finding, not an
exclusion) and falls back to ``search_cpython_code`` ->
``fetch_generic_url_content`` on the top hit's URL when doc-search comes up
empty.

Like ``benchmarks.adapters.python_docs_mcp_adapter.PythonDocsMcpAdapter``
(issue #86, the proven template this module mirrors), this adapter is
intentionally **not** a ``benchmarks.adapters.base.ProviderAdapter``
subclass.

Pin (PLAN-87 section 1.2): weak -- hosted endpoint
``https://gitmcp.io/python/cpython`` + access date + the live
``serverInfo.version`` recorded per run, plus code reference
``idosal/git-mcp@main`` SHA ``c487a29``. No npm package, tag, release, or
Docker image exists to pin against; the npm package named ``git-mcp`` is an
UNRELATED project and must never be referenced here. Self-hosting needs a
Cloudflare account and is not clean-clone reproducible, so it is not used.

Terms (PLAN-87 section 1.2): Apache-2.0 code, no hosted-service ToS
document, README explicitly invites programmatic agent access --
benchmarking is permitted (assumption A2). Keep concurrency low: the
GitHub API quota behind GitMCP is shared.

Auth: none. Gated by
:func:`benchmarks.adapters.guard.require_live_competitor` (keyless
two-latch guard, PLAN-87 section 2.2), called as the first statement of
:meth:`GitMcpAdapter.run`.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Protocol

from benchmarks.adapters.guard import require_live_competitor
from benchmarks.runner import BenchmarkCellFailure

#: Hosted endpoint pin (PLAN-87 section 1.2). Repository target is fixed to
#: python/cpython -- any second target repo is explicitly out of scope for
#: issue #87 (PLAN-87 section 7).
DEFAULT_ENDPOINT = "https://gitmcp.io/python/cpython"

#: Code reference recorded alongside the endpoint pin (not fetched at
#: runtime -- purely descriptive metadata for the manifest/template).
CODE_REFERENCE = "idosal/git-mcp@main#c487a29"

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
class GitMcpResult:
    """Structured output of one GitMCP cell run."""

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
    """Pick the top hit's URL out of a search result.

    Prefers a structured hit list (``hits``/``results``) with a ``url``
    field, falling back to scanning the first text block for an
    ``http(s)://`` URL. Returns ``None`` (never raises) when neither yields
    a candidate.
    """
    payload = _tool_result_payload(result)
    if isinstance(payload, dict):
        candidates = payload.get("hits") or payload.get("results")
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


async def _run_gitmcp_flow(session: _CallToolSession, prompt: str) -> GitMcpResult:
    """Run the doc-search -> (code-search -> fetch) fallback flow for one prompt.

    Pure orchestration against a duck-typed ``session`` so it can be unit
    tested with a scripted double -- no real transport required. Raises
    ``BenchmarkCellFailure`` (category ``tool_failure``) when any tool call
    itself reports an error.
    """
    tool_calls: list[ToolCallRecord] = []

    doc_args: dict[str, Any] = {"query": prompt}
    doc_result = await session.call_tool("search_cpython_documentation", doc_args)
    doc_payload = _tool_result_payload(doc_result)
    tool_calls.append(
        ToolCallRecord(
            tool="search_cpython_documentation",
            arguments=doc_args,
            result=doc_payload,
            is_error=bool(doc_result.isError),
        )
    )
    if doc_result.isError:
        raise BenchmarkCellFailure(
            "tool_failure",
            f"search_cpython_documentation returned an error: {_first_text(doc_result)}",
        )

    url = _extract_first_url(doc_result)
    if url is None:
        code_args: dict[str, Any] = {"query": prompt}
        code_result = await session.call_tool("search_cpython_code", code_args)
        code_payload = _tool_result_payload(code_result)
        tool_calls.append(
            ToolCallRecord(
                tool="search_cpython_code",
                arguments=code_args,
                result=code_payload,
                is_error=bool(code_result.isError),
            )
        )
        if code_result.isError:
            raise BenchmarkCellFailure(
                "tool_failure",
                f"search_cpython_code returned an error: {_first_text(code_result)}",
            )
        url = _extract_first_url(code_result)

    if url is None:
        return GitMcpResult(answer="", tool_calls=tool_calls)

    fetch_args: dict[str, Any] = {"url": url}
    fetch_result = await session.call_tool("fetch_generic_url_content", fetch_args)
    fetch_payload = _tool_result_payload(fetch_result)
    tool_calls.append(
        ToolCallRecord(
            tool="fetch_generic_url_content",
            arguments=fetch_args,
            result=fetch_payload,
            is_error=bool(fetch_result.isError),
        )
    )
    if fetch_result.isError:
        raise BenchmarkCellFailure(
            "tool_failure",
            f"fetch_generic_url_content returned an error: {_first_text(fetch_result)}",
        )

    answer = _first_text(fetch_result)
    if answer is None:
        payload = fetch_payload or {}
        answer = str(payload.get("content", "") or payload.get("text", ""))
    return GitMcpResult(answer=answer, tool_calls=tool_calls)


def _transport_factory(endpoint: str) -> Any:
    """Single owned seam for all transport construction.

    Imported lazily inside this function body so importing
    ``gitmcp_adapter`` never requires the ``mcp`` client submodules unless
    an actual run is attempted. Tests patch this one symbol to prove
    fail-closed guard behavior without any transport ever being
    constructed.
    """
    from mcp.client.streamable_http import streamable_http_client

    return streamable_http_client(endpoint)


class GitMcpAdapter:
    """Adapter for GitMCP's hosted MCP server: doc-search -> code-search -> fetch.

    ``run()`` is the synchronous entry point the benchmark runner's
    dispatch registry calls per cell (see
    ``benchmarks.runner._gitmcp_answer``).
    """

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._endpoint = endpoint if endpoint is not None else DEFAULT_ENDPOINT
        self._timeout_seconds = timeout_seconds

    def run(self, prompt: str) -> GitMcpResult:
        """Run one GitMCP cell.

        ``require_live_competitor("gitmcp")`` is the FIRST statement here,
        before any transport construction (PLAN-87 section 2.2).

        Raises ``BenchmarkCellFailure`` with category ``tool_failure``
        (guard refusal or a tool-level error), ``timeout``, or
        ``mcp_protocol_crash`` -- never a bare exception.
        """
        require_live_competitor("gitmcp")
        try:
            return asyncio.run(asyncio.wait_for(self._run_async(prompt), self._timeout_seconds))
        except BenchmarkCellFailure:
            raise
        except TimeoutError as exc:
            raise BenchmarkCellFailure(
                "timeout",
                f"GitMCP did not complete its retrieval flow within {self._timeout_seconds}s",
            ) from exc
        except Exception as exc:  # noqa: BLE001 - failures are benchmark artifacts
            raise BenchmarkCellFailure("mcp_protocol_crash", str(exc)) from exc

    async def _run_async(self, prompt: str) -> GitMcpResult:
        # Imported lazily so importing this module never requires the `mcp`
        # client submodules unless an actual run is attempted.
        from mcp import ClientSession

        async with _transport_factory(self._endpoint) as (read, write, _get_session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await _run_gitmcp_flow(session, prompt)
