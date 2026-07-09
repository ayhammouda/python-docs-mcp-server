"""Competitor MCP adapter: DeepWiki (Cognition) -- issue #87.

Runs DeepWiki's hosted MCP server as a benchmark system under test: a
single ``ask_question`` call against the ``python/cpython`` Devin-generated
wiki. Unlike the other three competitors, DeepWiki's ``ask_question``
returns an **LLM-generated** answer (PLAN-87 section 1.3, live-probed
2026-07-09: ~13.5s, nondeterministic across identical calls) rather than
retrieved source content -- the manifest's ``answer_generation: "llm"``
metadata convention (see ``docs/benchmarks/competitor-manifest.template.yml``)
flags this so report generation never hides that an answer-synthesis system
is being scored inside a retrieval benchmark. ``read_wiki_contents`` (an
all-or-nothing ~1.18 MB blob) is not the documented flow and is not used.

Like ``benchmarks.adapters.python_docs_mcp_adapter.PythonDocsMcpAdapter``
(issue #86, the proven template this module mirrors), this adapter is
intentionally **not** a ``benchmarks.adapters.base.ProviderAdapter``
subclass.

Pin (PLAN-87 section 1.3): weakest of the four -- hosted endpoint
``https://mcp.deepwiki.com/mcp`` + access date + the live
``serverInfo.version`` recorded per run + the target wiki's last-indexed
date. No package/image/self-host exists. Third-party npm "deepwiki"
packages are NOT Cognition's server and must never be referenced here.

Terms (PLAN-87 section 1.3): no DeepWiki-specific ToS; the Cognition
Platform ToS/AUP contain no benchmark clause but a gray-zone competitive-use
note (DeepWiki's publisher is a competing docs tool). Maintainer decision:
include with disclosure (assumption A3).

Auth: none. Gated by
:func:`benchmarks.adapters.guard.require_live_competitor` (keyless
two-latch guard, PLAN-87 section 2.2), called as the first statement of
:meth:`DeepWikiAdapter.run`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

from benchmarks.adapters.guard import require_live_competitor
from benchmarks.runner import BenchmarkCellFailure

DEFAULT_ENDPOINT = "https://mcp.deepwiki.com/mcp"

#: The wiki target this adapter asks about. Any second target repo is
#: explicitly out of scope for issue #87 (PLAN-87 section 7).
DEFAULT_REPO_NAME = "python/cpython"

#: 60s, not the 30s default used by the other three competitors -- DeepWiki's
#: verified live round trip is ~13.5s and LLM-generation latency is more
#: variable than plain retrieval (PLAN-87 section 1.3).
DEFAULT_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True)
class ToolCallRecord:
    """One raw MCP tool call/response pair, recorded verbatim for audit."""

    tool: str
    arguments: dict[str, Any]
    result: dict[str, Any] | None
    is_error: bool


@dataclass(frozen=True)
class DeepWikiResult:
    """Structured output of one DeepWiki cell run."""

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


async def _run_deepwiki_flow(
    session: _CallToolSession,
    prompt: str,
    *,
    repo_name: str = DEFAULT_REPO_NAME,
) -> DeepWikiResult:
    """Run the single documented ``ask_question`` call for one prompt.

    Pure orchestration against a duck-typed ``session`` so it can be unit
    tested with a scripted double -- no real transport required. Raises
    ``BenchmarkCellFailure`` (category ``tool_failure``) when the tool call
    itself reports an error.
    """
    tool_calls: list[ToolCallRecord] = []

    args: dict[str, Any] = {"repoName": repo_name, "question": prompt}
    result = await session.call_tool("ask_question", args)
    payload = _tool_result_payload(result)
    tool_calls.append(
        ToolCallRecord(
            tool="ask_question", arguments=args, result=payload, is_error=bool(result.isError)
        )
    )
    if result.isError:
        raise BenchmarkCellFailure(
            "tool_failure", f"ask_question returned an error: {_first_text(result)}"
        )

    answer = _first_text(result)
    if answer is None:
        answer = str((payload or {}).get("answer", "") or (payload or {}).get("text", ""))
    return DeepWikiResult(answer=answer, tool_calls=tool_calls)


def _transport_factory(endpoint: str) -> Any:
    """Single owned seam for all transport construction.

    Imported lazily inside this function body so importing
    ``deepwiki_adapter`` never requires the ``mcp`` client submodules
    unless an actual run is attempted. Tests patch this one symbol to
    prove fail-closed guard behavior without any transport ever being
    constructed.
    """
    from mcp.client.streamable_http import streamable_http_client

    return streamable_http_client(endpoint)


class DeepWikiAdapter:
    """Adapter for DeepWiki's hosted MCP server: single ask_question call.

    ``run()`` is the synchronous entry point the benchmark runner's
    dispatch registry calls per cell (see
    ``benchmarks.runner._deepwiki_answer``).
    """

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        repo_name: str = DEFAULT_REPO_NAME,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._endpoint = endpoint if endpoint is not None else DEFAULT_ENDPOINT
        self._repo_name = repo_name
        self._timeout_seconds = timeout_seconds

    def run(self, prompt: str) -> DeepWikiResult:
        """Run one DeepWiki cell.

        ``require_live_competitor("deepwiki")`` is the FIRST statement
        here, before any transport construction (PLAN-87 section 2.2).

        Raises ``BenchmarkCellFailure`` with category ``tool_failure``
        (guard refusal or a tool-level error), ``timeout``, or
        ``mcp_protocol_crash`` -- never a bare exception.
        """
        require_live_competitor("deepwiki")
        try:
            return asyncio.run(asyncio.wait_for(self._run_async(prompt), self._timeout_seconds))
        except BenchmarkCellFailure:
            raise
        except TimeoutError as exc:
            raise BenchmarkCellFailure(
                "timeout",
                f"DeepWiki did not complete ask_question within {self._timeout_seconds}s",
            ) from exc
        except Exception as exc:  # noqa: BLE001 - failures are benchmark artifacts
            raise BenchmarkCellFailure("mcp_protocol_crash", str(exc)) from exc

    async def _run_async(self, prompt: str) -> DeepWikiResult:
        # Imported lazily so importing this module never requires the `mcp`
        # client submodules unless an actual run is attempted.
        from mcp import ClientSession

        async with _transport_factory(self._endpoint) as (read, write, _get_session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await _run_deepwiki_flow(session, prompt, repo_name=self._repo_name)
