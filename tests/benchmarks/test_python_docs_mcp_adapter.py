"""Tests for the offline stdio python-docs-mcp-server adapter (issue #86).

All tests here drive the adapter against a fake/stubbed MCP transport (a
scripted session double) -- no real server subprocess is spawned. Two
exceptions: ``test_run_maps_a_failure_inside_a_real_stdio_session`` spawns
a silent child (``python -c "import sys; sys.stdin.read()"``) that only
holds the stdio pipe open, with no network and no server; and the optional
integration test at the bottom spawns the real server only when a local
index already exists at the default cache path, and is skipped otherwise
(per the issue's acceptance criteria).
"""

from __future__ import annotations

import asyncio
import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession

from benchmarks.adapters.python_docs_mcp_adapter import (
    PythonDocsMcpAdapter,
    PythonDocsMcpResult,
    _default_index_path,
    _run_retrieval_flow,
    _tool_result_payload,
)
from benchmarks.runner import BenchmarkCellFailure


@dataclass
class _FakeContentBlock:
    text: str


@dataclass
class _FakeCallToolResult:
    content: list[Any] = field(default_factory=list)
    structured_content: dict[str, Any] | None = None
    is_error: bool = False


class _ScriptedSession:
    """A minimal fake MCP session returning one scripted result per tool.

    No real transport, no subprocess, no network -- this is the
    "fake/stubbed MCP transport" the issue's acceptance criteria requires
    for unit tests of the search_docs -> get_docs flow.
    """

    def __init__(self, responses: dict[str, _FakeCallToolResult]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> _FakeCallToolResult:
        self.calls.append((name, arguments or {}))
        return self._responses[name]


def _search_result(hits: list[dict[str, Any]]) -> _FakeCallToolResult:
    return _FakeCallToolResult(
        content=[_FakeContentBlock(text="ignored")],
        structured_content={"hits": hits, "note": None},
        is_error=False,
    )


def _get_result(content: str, **extra: Any) -> _FakeCallToolResult:
    payload = {
        "content": content,
        "slug": "lib/m.html",
        "title": "t",
        "version": "3.13",
        **extra,
    }
    return _FakeCallToolResult(
        content=[_FakeContentBlock(text="ignored")],
        structured_content=payload,
        is_error=False,
    )


def _error_result(message: str) -> _FakeCallToolResult:
    return _FakeCallToolResult(
        content=[_FakeContentBlock(text=message)],
        structured_content=None,
        is_error=True,
    )


# --- _run_retrieval_flow: pure orchestration against a fake session --------


async def test_retrieval_flow_calls_search_then_get_docs_and_records_raw_payloads() -> None:
    hit = {"slug": "lib/asyncio-task.html", "anchor": "asyncio.TaskGroup", "version": "3.13"}
    session = _ScriptedSession(
        {
            "search_docs": _search_result([hit]),
            "get_docs": _get_result("TaskGroup docs content"),
        }
    )

    result = await _run_retrieval_flow(session, "asyncio.TaskGroup", max_results=3)

    assert result.answer == "TaskGroup docs content"
    assert [call.tool for call in result.tool_calls] == ["search_docs", "get_docs"]
    assert session.calls[0] == ("search_docs", {"query": "asyncio.TaskGroup", "max_results": 3})
    assert session.calls[1] == (
        "get_docs",
        {"slug": "lib/asyncio-task.html", "version": "3.13", "anchor": "asyncio.TaskGroup"},
    )
    assert result.tool_calls[0].result == {"hits": [hit], "note": None}
    assert result.tool_calls[0].is_error is False
    assert result.tool_calls[1].is_error is False


async def test_retrieval_flow_returns_empty_answer_when_search_has_no_hits() -> None:
    session = _ScriptedSession({"search_docs": _search_result([])})

    result = await _run_retrieval_flow(session, "no such symbol")

    assert result.answer == ""
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].tool == "search_docs"


async def test_retrieval_flow_omits_anchor_argument_when_hit_has_no_anchor() -> None:
    hit = {"slug": "lib/page.html", "anchor": None, "version": "3.13"}
    session = _ScriptedSession(
        {"search_docs": _search_result([hit]), "get_docs": _get_result("page content")}
    )

    await _run_retrieval_flow(session, "anything")

    assert session.calls[1] == ("get_docs", {"slug": "lib/page.html", "version": "3.13"})


async def test_retrieval_flow_raises_tool_failure_when_search_docs_errors() -> None:
    session = _ScriptedSession({"search_docs": _error_result("boom")})

    with pytest.raises(BenchmarkCellFailure) as exc_info:
        await _run_retrieval_flow(session, "anything")

    assert exc_info.value.category == "tool_failure"
    assert "boom" in str(exc_info.value)


async def test_retrieval_flow_raises_tool_failure_when_get_docs_errors() -> None:
    hit = {"slug": "lib/missing.html", "anchor": None, "version": "3.13"}
    session = _ScriptedSession(
        {
            "search_docs": _search_result([hit]),
            "get_docs": _error_result("Page not found"),
        }
    )

    with pytest.raises(BenchmarkCellFailure) as exc_info:
        await _run_retrieval_flow(session, "anything")

    assert exc_info.value.category == "tool_failure"
    assert "Page not found" in str(exc_info.value)


# --- _tool_result_payload ---------------------------------------------------


def test_tool_result_payload_prefers_structured_content() -> None:
    result = _get_result("x")

    assert _tool_result_payload(result) == result.structured_content


def test_tool_result_payload_falls_back_to_first_text_block_on_error() -> None:
    result = _error_result("Error executing tool get_docs: not found")

    assert _tool_result_payload(result) == {"text": "Error executing tool get_docs: not found"}


# --- PythonDocsMcpAdapter.run(): missing index -> tool_failure, no spawn ---


def test_run_fails_with_tool_failure_when_index_is_missing_and_never_spawns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail_if_called(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("asyncio.run must not be called when the index is missing")

    monkeypatch.setattr("benchmarks.adapters.python_docs_mcp_adapter.asyncio.run", _fail_if_called)
    adapter = PythonDocsMcpAdapter(index_path=tmp_path / "does-not-exist.db")

    with pytest.raises(BenchmarkCellFailure) as exc_info:
        adapter.run("anything")

    assert exc_info.value.category == "tool_failure"
    assert "no local index found" in str(exc_info.value)


def test_run_missing_index_check_never_opens_a_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail_if_called(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("no socket should be opened when the index is missing")

    monkeypatch.setattr(socket, "socket", _fail_if_called)
    adapter = PythonDocsMcpAdapter(index_path=tmp_path / "does-not-exist.db")

    with pytest.raises(BenchmarkCellFailure):
        adapter.run("anything")


def test_run_uses_default_index_path_when_none_is_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "benchmarks.adapters.python_docs_mcp_adapter._default_index_path",
        lambda: tmp_path / "default-missing.db",
    )
    adapter = PythonDocsMcpAdapter()

    with pytest.raises(BenchmarkCellFailure) as exc_info:
        adapter.run("anything")

    assert "default-missing.db" in str(exc_info.value)


# --- PythonDocsMcpAdapter.run(): failure-category mapping ------------------


def test_run_maps_a_slow_call_to_a_timeout_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index_path = tmp_path / "index.db"
    index_path.write_text("existence is all `run()` checks before spawning")

    async def _slow_run_async(self: PythonDocsMcpAdapter, prompt: str) -> PythonDocsMcpResult:
        await asyncio.sleep(1)
        return PythonDocsMcpResult(answer="too late", tool_calls=[])

    monkeypatch.setattr(PythonDocsMcpAdapter, "_run_async", _slow_run_async)
    adapter = PythonDocsMcpAdapter(index_path=index_path, timeout_seconds=0.01)

    with pytest.raises(BenchmarkCellFailure) as exc_info:
        adapter.run("anything")

    assert exc_info.value.category == "timeout"


def test_run_maps_an_unexpected_transport_error_to_mcp_protocol_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index_path = tmp_path / "index.db"
    index_path.write_text("existence is all `run()` checks before spawning")

    async def _broken_run_async(self: PythonDocsMcpAdapter, prompt: str) -> PythonDocsMcpResult:
        raise RuntimeError("stdio pipe closed unexpectedly")

    monkeypatch.setattr(PythonDocsMcpAdapter, "_run_async", _broken_run_async)
    adapter = PythonDocsMcpAdapter(index_path=index_path)

    with pytest.raises(BenchmarkCellFailure) as exc_info:
        adapter.run("anything")

    assert exc_info.value.category == "mcp_protocol_crash"
    assert "stdio pipe closed unexpectedly" in str(exc_info.value)


def test_run_propagates_benchmark_cell_failure_from_run_async_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index_path = tmp_path / "index.db"
    index_path.write_text("existence is all `run()` checks before spawning")

    async def _tool_level_failure(self: PythonDocsMcpAdapter, prompt: str) -> PythonDocsMcpResult:
        raise BenchmarkCellFailure("tool_failure", "search_docs returned an error: boom")

    monkeypatch.setattr(PythonDocsMcpAdapter, "_run_async", _tool_level_failure)
    adapter = PythonDocsMcpAdapter(index_path=index_path)

    with pytest.raises(BenchmarkCellFailure) as exc_info:
        adapter.run("anything")

    assert exc_info.value.category == "tool_failure"
    assert "boom" in str(exc_info.value)


def test_run_maps_a_failure_inside_a_real_stdio_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index_path = tmp_path / "index.db"
    index_path.write_text("existence is all `run()` checks before spawning")

    async def _failing_initialize(self: ClientSession) -> Any:
        raise BenchmarkCellFailure("tool_failure", "boom inside the session")

    monkeypatch.setattr(ClientSession, "initialize", _failing_initialize)
    adapter = PythonDocsMcpAdapter(
        command=[sys.executable, "-c", "import sys; sys.stdin.read()"],
        index_path=index_path,
        timeout_seconds=20,
    )

    with pytest.raises(BenchmarkCellFailure) as exc_info:
        adapter.run("anything")

    assert exc_info.value.category == "tool_failure"
    assert str(exc_info.value) == "boom inside the session"
    assert isinstance(exc_info.value.__cause__, ExceptionGroup)


# --- The spawned server gets only the environment it needs


class _TransportReached(Exception):
    """Raised by the patched stdio client once it has seen its parameters."""


@pytest.mark.parametrize(
    "xdg_cache_home,expected",
    [
        pytest.param(
            "/tmp/bench-cache",
            {"PYTHON_DOCS_MCP_DISABLE_AUTO_INDEX": "1", "XDG_CACHE_HOME": "/tmp/bench-cache"},
            id="cache-location-set",
        ),
        pytest.param(None, {"PYTHON_DOCS_MCP_DISABLE_AUTO_INDEX": "1"}, id="cache-location-unset"),
    ],
)
def test_server_env_holds_only_the_auto_index_switch_and_cache_location(
    xdg_cache_home: str | None, expected: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNRELATED_SECRET", "s")
    if xdg_cache_home is None:
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    else:
        monkeypatch.setenv("XDG_CACHE_HOME", xdg_cache_home)
    recorded: dict[str, Any] = {}

    def _record(params: Any) -> Any:
        recorded["env"] = params.env
        raise _TransportReached

    monkeypatch.setattr("mcp.client.stdio.stdio_client", _record)

    with pytest.raises(_TransportReached):
        asyncio.run(PythonDocsMcpAdapter()._run_async("q"))

    assert recorded["env"] == expected


# --- Optional integration test: real server spawn, skipped without an index


@pytest.mark.skipif(
    not _default_index_path().exists(),
    reason="requires a locally built python-docs-mcp-server index",
)
def test_real_server_round_trip_when_a_local_index_exists() -> None:
    adapter = PythonDocsMcpAdapter(timeout_seconds=60)

    try:
        result = adapter.run("os.path.join")
    except BenchmarkCellFailure as exc:
        # A locally built but symbol-only index (no content ingestion) is a
        # legitimate dev state; get_docs then fails, and that failure must
        # surface as a recorded tool_failure, not as an mcp_protocol_crash.
        # A timeout is the only other acceptable outcome for this
        # environment-specific smoke test.
        assert exc.category in {"tool_failure", "timeout"}, str(exc)
        return

    assert isinstance(result.answer, str)
    assert result.tool_calls
    assert result.tool_calls[0].tool == "search_docs"
