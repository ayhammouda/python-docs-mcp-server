"""Tests for the competitor MCP tool adapters (issue #87).

Covers, per ``PLAN-87-competitor-adapters.md`` section 4 (mirrored as a
comment on issue #87):

1. Flow tests per adapter against a scripted/fake MCP session -- no real
   transport, no subprocess, no network.
2. Failure-category mapping per adapter (``tool_failure`` / ``timeout`` /
   ``mcp_protocol_crash``).
3. Guard refusals fail closed: every disabled-env permutation refuses
   before any transport construction (each adapter's owned
   ``_transport_factory`` seam, plus ``asyncio.run`` and ``socket.socket``,
   are all patched to raise if reached).
4. ``require_live_competitor`` latch semantics in isolation.
5. Eligibility/exclusion: per-entry and manifest-level validation, the
   runner's manifest-load-time refusal (including the CLI exit-2 path),
   the ``exclusions:`` block's byte-for-byte snapshot survival, and the
   committed template manifest.
6. Registry integration: each of the four adapter ids dispatches to the
   right adapter class via ``benchmarks.runner``.

Zero network calls anywhere in this file, by construction: every test
either drives pure orchestration functions against a fake session, or
proves a transport-construction seam is never reached.
"""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml

import benchmarks.adapters.context7_adapter as context7_adapter
import benchmarks.adapters.deepwiki_adapter as deepwiki_adapter
import benchmarks.adapters.gitmcp_adapter as gitmcp_adapter
import benchmarks.adapters.ref_tools_adapter as ref_tools_adapter
from benchmarks.adapters.eligibility import (
    COMPETITOR_ADAPTER_IDS,
    validate_competitor_eligibility,
    validate_manifest_eligibility,
)
from benchmarks.adapters.guard import (
    LIVE_COMPETITORS_ENV,
    LIVE_PROVIDERS_ENABLED_ENV,
    PROVIDER_API_KEY_ENV,
    LiveProviderDisabledError,
    require_live_competitor,
)
from benchmarks.runner import (
    BenchmarkCellFailure,
    BenchmarkConfig,
    BenchmarkValidationError,
    run_benchmark,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE_MANIFEST_PATH = (
    _REPO_ROOT / "docs" / "benchmarks" / "competitor-manifest.template.yml"
)

#: Shared fixture URL for GitMCP/Ref.tools search-hit -> fetch/read flow
#: tests below -- kept short to fit within the project's line-length limit.
_PATHLIB_DOC_URL = "https://docs.python.org/3/library/pathlib.html"


# --- Shared fake MCP transport (no real session, no network) ---------------


@dataclass
class _FakeContentBlock:
    text: str


@dataclass
class _FakeCallToolResult:
    content: list[Any] = field(default_factory=list)
    structuredContent: dict[str, Any] | None = None
    isError: bool = False


class _ScriptedSession:
    """A minimal fake MCP session returning one scripted result per tool.

    No real transport, no subprocess, no network -- shared across all four
    competitor adapters' flow tests below since each adapter module's
    ``_CallToolSession``/``_CallToolResult`` are structural (``Protocol``)
    types, not concrete classes to subclass.
    """

    def __init__(self, responses: dict[str, _FakeCallToolResult]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> _FakeCallToolResult:
        self.calls.append((name, arguments or {}))
        return self._responses[name]


def _result(
    *,
    text: str | None = "ignored",
    structured: dict[str, Any] | None = None,
    is_error: bool = False,
) -> _FakeCallToolResult:
    content = [_FakeContentBlock(text=text)] if text is not None else []
    return _FakeCallToolResult(content=content, structuredContent=structured, isError=is_error)


def _patch_no_transport(monkeypatch: pytest.MonkeyPatch, module: Any) -> None:
    """Prove no transport/subprocess/socket is ever reached for a guard refusal.

    Patches the adapter module's owned ``_transport_factory`` seam (the one
    symbol all transport construction is routed through, per PLAN-87
    section 2.1/Codex round-1 finding 4), plus ``asyncio.run`` (so
    ``_run_async`` can never even begin), the global ``socket.socket``, and
    the global ``subprocess.Popen`` (defense-in-depth for Context7's stdio
    path, matching the fail-closed pattern established by
    ``test_python_docs_mcp_adapter.py::test_run_missing_index_check_never_opens_a_socket``
    and PLAN-87 section 4 item 3's "subprocess/stdio + socket.socket" list).
    """

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("network reached")

    monkeypatch.setattr(module, "_transport_factory", _boom)
    monkeypatch.setattr(module.asyncio, "run", _boom)
    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)


# =============================================================================
# 1 + 2. Flow tests and failure-category mapping, per adapter
# =============================================================================


# --- Context7: resolve-library-id -> query-docs -----------------------------


async def test_context7_flow_calls_resolve_then_query_and_records_payloads() -> None:
    session = _ScriptedSession(
        {
            "resolve-library-id": _result(
                structured={"results": [{"id": "/python/cpython", "title": "Python"}]}
            ),
            "query-docs": _result(text="TaskGroup docs content"),
        }
    )

    result = await context7_adapter._run_context7_flow(
        session, "asyncio.TaskGroup", library_name="Python"
    )

    assert result.answer == "TaskGroup docs content"
    assert [call.tool for call in result.tool_calls] == ["resolve-library-id", "query-docs"]
    assert session.calls[0] == (
        "resolve-library-id",
        {"libraryName": "Python", "query": "asyncio.TaskGroup"},
    )
    assert session.calls[1] == (
        "query-docs",
        {"libraryId": "/python/cpython", "query": "asyncio.TaskGroup"},
    )
    assert result.tool_calls[0].is_error is False


async def test_context7_flow_falls_back_to_text_regex_when_no_structured_candidates() -> None:
    session = _ScriptedSession(
        {
            "resolve-library-id": _result(text="Best match: /python/cpython (Python stdlib)"),
            "query-docs": _result(text="content"),
        }
    )

    await context7_adapter._run_context7_flow(session, "q")

    assert session.calls[1][1]["libraryId"] == "/python/cpython"


async def test_context7_flow_falls_back_to_structured_content_when_no_text_block() -> None:
    session = _ScriptedSession(
        {
            "resolve-library-id": _FakeCallToolResult(
                content=[], structuredContent={"results": [{"id": "/python/cpython"}]}
            ),
            "query-docs": _FakeCallToolResult(
                content=[], structuredContent={"content": "structured docs"}
            ),
        }
    )

    result = await context7_adapter._run_context7_flow(session, "q")

    assert result.answer == "structured docs"


async def test_context7_flow_returns_empty_answer_when_no_library_id_found() -> None:
    session = _ScriptedSession(
        {"resolve-library-id": _result(text="no matches found", structured={"results": []})}
    )

    result = await context7_adapter._run_context7_flow(session, "q")

    assert result.answer == ""
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].tool == "resolve-library-id"


async def test_context7_flow_raises_tool_failure_when_resolve_errors() -> None:
    session = _ScriptedSession({"resolve-library-id": _result(text="boom", is_error=True)})

    with pytest.raises(BenchmarkCellFailure) as exc_info:
        await context7_adapter._run_context7_flow(session, "q")

    assert exc_info.value.category == "tool_failure"
    assert "boom" in str(exc_info.value)


async def test_context7_flow_raises_tool_failure_when_query_docs_errors() -> None:
    session = _ScriptedSession(
        {
            "resolve-library-id": _result(structured={"results": [{"id": "/python/cpython"}]}),
            "query-docs": _result(text="boom", is_error=True),
        }
    )

    with pytest.raises(BenchmarkCellFailure) as exc_info:
        await context7_adapter._run_context7_flow(session, "q")

    assert exc_info.value.category == "tool_failure"


# --- GitMCP: doc-search -> (code-search -> fetch) fallback ------------------


async def test_gitmcp_flow_uses_doc_search_hit_when_available() -> None:
    session = _ScriptedSession(
        {
            "search_cpython_documentation": _result(
                structured={"hits": [{"url": _PATHLIB_DOC_URL}]}
            ),
            "fetch_generic_url_content": _result(text="pathlib docs"),
        }
    )

    result = await gitmcp_adapter._run_gitmcp_flow(session, "pathlib.Path.read_text")

    assert result.answer == "pathlib docs"
    assert [call.tool for call in result.tool_calls] == [
        "search_cpython_documentation",
        "fetch_generic_url_content",
    ]


async def test_gitmcp_flow_falls_back_to_code_search_when_doc_search_has_no_hits() -> None:
    session = _ScriptedSession(
        {
            "search_cpython_documentation": _result(structured={"hits": []}, text=""),
            "search_cpython_code": _result(
                structured={
                    "results": [
                        {
                            "url": (
                                "https://github.com/python/cpython/blob/main/"
                                "Doc/library/pathlib.rst"
                            )
                        }
                    ]
                }
            ),
            "fetch_generic_url_content": _result(text="pathlib rst content"),
        }
    )

    result = await gitmcp_adapter._run_gitmcp_flow(session, "pathlib.Path.read_text")

    assert [call.tool for call in result.tool_calls] == [
        "search_cpython_documentation",
        "search_cpython_code",
        "fetch_generic_url_content",
    ]
    assert result.answer == "pathlib rst content"


async def test_gitmcp_flow_returns_empty_answer_when_no_url_found_anywhere() -> None:
    session = _ScriptedSession(
        {
            "search_cpython_documentation": _result(structured={"hits": []}, text=""),
            "search_cpython_code": _result(structured={"results": []}, text=""),
        }
    )

    result = await gitmcp_adapter._run_gitmcp_flow(session, "q")

    assert result.answer == ""
    assert [call.tool for call in result.tool_calls] == [
        "search_cpython_documentation",
        "search_cpython_code",
    ]


async def test_gitmcp_flow_falls_back_to_structured_content_when_no_text_block() -> None:
    session = _ScriptedSession(
        {
            "search_cpython_documentation": _FakeCallToolResult(
                content=[],
                structuredContent={"hits": [{"url": _PATHLIB_DOC_URL}]},
            ),
            "fetch_generic_url_content": _FakeCallToolResult(
                content=[], structuredContent={"text": "structured fetch text"}
            ),
        }
    )

    result = await gitmcp_adapter._run_gitmcp_flow(session, "q")

    assert result.answer == "structured fetch text"


async def test_gitmcp_flow_raises_tool_failure_when_doc_search_errors() -> None:
    session = _ScriptedSession(
        {"search_cpython_documentation": _result(text="boom", is_error=True)}
    )

    with pytest.raises(BenchmarkCellFailure) as exc_info:
        await gitmcp_adapter._run_gitmcp_flow(session, "q")

    assert exc_info.value.category == "tool_failure"


async def test_gitmcp_flow_raises_tool_failure_when_code_search_errors() -> None:
    session = _ScriptedSession(
        {
            "search_cpython_documentation": _result(structured={"hits": []}, text=""),
            "search_cpython_code": _result(text="boom", is_error=True),
        }
    )

    with pytest.raises(BenchmarkCellFailure) as exc_info:
        await gitmcp_adapter._run_gitmcp_flow(session, "q")

    assert exc_info.value.category == "tool_failure"


async def test_gitmcp_flow_raises_tool_failure_when_fetch_errors() -> None:
    session = _ScriptedSession(
        {
            "search_cpython_documentation": _result(
                structured={"hits": [{"url": _PATHLIB_DOC_URL}]}
            ),
            "fetch_generic_url_content": _result(text="boom", is_error=True),
        }
    )

    with pytest.raises(BenchmarkCellFailure) as exc_info:
        await gitmcp_adapter._run_gitmcp_flow(session, "q")

    assert exc_info.value.category == "tool_failure"


# --- DeepWiki: single ask_question call --------------------------------------


async def test_deepwiki_flow_calls_ask_question_and_records_payload() -> None:
    session = _ScriptedSession({"ask_question": _result(text="TaskGroup answer")})

    result = await deepwiki_adapter._run_deepwiki_flow(
        session, "asyncio.TaskGroup", repo_name="python/cpython"
    )

    assert result.answer == "TaskGroup answer"
    assert session.calls[0] == (
        "ask_question",
        {"repoName": "python/cpython", "question": "asyncio.TaskGroup"},
    )
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].tool == "ask_question"


async def test_deepwiki_flow_falls_back_to_structured_answer_when_no_text_block() -> None:
    session = _ScriptedSession(
        {
            "ask_question": _FakeCallToolResult(
                content=[], structuredContent={"answer": "structured answer"}
            )
        }
    )

    result = await deepwiki_adapter._run_deepwiki_flow(session, "q")

    assert result.answer == "structured answer"


async def test_deepwiki_flow_raises_tool_failure_on_error() -> None:
    session = _ScriptedSession({"ask_question": _result(text="boom", is_error=True)})

    with pytest.raises(BenchmarkCellFailure) as exc_info:
        await deepwiki_adapter._run_deepwiki_flow(session, "q")

    assert exc_info.value.category == "tool_failure"
    assert "boom" in str(exc_info.value)


# --- Ref.tools: ref_search_documentation -> ref_read_url ---------------------


async def test_ref_tools_flow_calls_search_then_read_and_records_payloads() -> None:
    session = _ScriptedSession(
        {
            "ref_search_documentation": _result(
                structured={"results": [{"url": _PATHLIB_DOC_URL}]}
            ),
            "ref_read_url": _result(text="pathlib docs"),
        }
    )

    result = await ref_tools_adapter._run_ref_tools_flow(session, "pathlib.Path.read_text")

    assert result.answer == "pathlib docs"
    assert session.calls[0] == ("ref_search_documentation", {"query": "pathlib.Path.read_text"})
    assert session.calls[1] == ("ref_read_url", {"url": _PATHLIB_DOC_URL})


async def test_ref_tools_flow_falls_back_to_structured_content_when_no_text_block() -> None:
    session = _ScriptedSession(
        {
            "ref_search_documentation": _FakeCallToolResult(
                content=[],
                structuredContent={"results": [{"url": _PATHLIB_DOC_URL}]},
            ),
            "ref_read_url": _FakeCallToolResult(
                content=[], structuredContent={"content": "structured content"}
            ),
        }
    )

    result = await ref_tools_adapter._run_ref_tools_flow(session, "q")

    assert result.answer == "structured content"


async def test_ref_tools_flow_returns_empty_answer_when_no_hits() -> None:
    session = _ScriptedSession({"ref_search_documentation": _result(structured={"results": []})})

    result = await ref_tools_adapter._run_ref_tools_flow(session, "q")

    assert result.answer == ""
    assert len(result.tool_calls) == 1


async def test_ref_tools_flow_raises_tool_failure_when_search_errors() -> None:
    session = _ScriptedSession({"ref_search_documentation": _result(text="boom", is_error=True)})

    with pytest.raises(BenchmarkCellFailure) as exc_info:
        await ref_tools_adapter._run_ref_tools_flow(session, "q")

    assert exc_info.value.category == "tool_failure"


async def test_ref_tools_flow_raises_tool_failure_when_read_errors() -> None:
    session = _ScriptedSession(
        {
            "ref_search_documentation": _result(
                structured={"results": [{"url": _PATHLIB_DOC_URL}]}
            ),
            "ref_read_url": _result(text="boom", is_error=True),
        }
    )

    with pytest.raises(BenchmarkCellFailure) as exc_info:
        await ref_tools_adapter._run_ref_tools_flow(session, "q")

    assert exc_info.value.category == "tool_failure"


# --- run()-level failure-category mapping: timeout / mcp_protocol_crash -----
#
# Table-driven across all four adapters: each entry pairs the adapter class
# with its Result dataclass and an env-setup function that satisfies that
# adapter's own guard (so the mapping logic inside the try/except in run()
# -- which runs only after the guard passes -- is what's actually exercised).


def _enable_context7_keyless(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(LIVE_PROVIDERS_ENABLED_ENV, "1")
    monkeypatch.setenv(LIVE_COMPETITORS_ENV, "context7")


def _enable_gitmcp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(LIVE_PROVIDERS_ENABLED_ENV, "1")
    monkeypatch.setenv(LIVE_COMPETITORS_ENV, "gitmcp")


def _enable_deepwiki(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(LIVE_PROVIDERS_ENABLED_ENV, "1")
    monkeypatch.setenv(LIVE_COMPETITORS_ENV, "deepwiki")


def _enable_ref_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(LIVE_PROVIDERS_ENABLED_ENV, "1")
    monkeypatch.setenv(PROVIDER_API_KEY_ENV["ref"], "fake-not-a-real-key")


_ADAPTER_MAPPING_CASES = [
    pytest.param(
        context7_adapter.Context7Adapter,
        context7_adapter.Context7Result,
        _enable_context7_keyless,
        id="context7",
    ),
    pytest.param(
        gitmcp_adapter.GitMcpAdapter, gitmcp_adapter.GitMcpResult, _enable_gitmcp, id="gitmcp"
    ),
    pytest.param(
        deepwiki_adapter.DeepWikiAdapter,
        deepwiki_adapter.DeepWikiResult,
        _enable_deepwiki,
        id="deepwiki",
    ),
    pytest.param(
        ref_tools_adapter.RefToolsAdapter,
        ref_tools_adapter.RefToolsResult,
        _enable_ref_tools,
        id="ref-tools",
    ),
]


@pytest.mark.parametrize("adapter_cls,result_cls,enable_guard", _ADAPTER_MAPPING_CASES)
def test_adapter_maps_a_slow_call_to_a_timeout_failure(
    adapter_cls: type, result_cls: type, enable_guard: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    enable_guard(monkeypatch)

    async def _slow_run_async(self: Any, prompt: str) -> Any:
        await asyncio.sleep(1)
        return result_cls(answer="too late", tool_calls=[])

    monkeypatch.setattr(adapter_cls, "_run_async", _slow_run_async)
    adapter = adapter_cls(timeout_seconds=0.01)

    with pytest.raises(BenchmarkCellFailure) as exc_info:
        adapter.run("anything")

    assert exc_info.value.category == "timeout"


@pytest.mark.parametrize("adapter_cls,result_cls,enable_guard", _ADAPTER_MAPPING_CASES)
def test_adapter_maps_an_unexpected_transport_error_to_mcp_protocol_crash(
    adapter_cls: type, result_cls: type, enable_guard: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    enable_guard(monkeypatch)

    async def _broken_run_async(self: Any, prompt: str) -> Any:
        raise RuntimeError("transport closed unexpectedly")

    monkeypatch.setattr(adapter_cls, "_run_async", _broken_run_async)
    adapter = adapter_cls()

    with pytest.raises(BenchmarkCellFailure) as exc_info:
        adapter.run("anything")

    assert exc_info.value.category == "mcp_protocol_crash"
    assert "transport closed unexpectedly" in str(exc_info.value)


@pytest.mark.parametrize("adapter_cls,result_cls,enable_guard", _ADAPTER_MAPPING_CASES)
def test_adapter_propagates_benchmark_cell_failure_from_run_async_unchanged(
    adapter_cls: type, result_cls: type, enable_guard: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    enable_guard(monkeypatch)

    async def _tool_level_failure(self: Any, prompt: str) -> Any:
        raise BenchmarkCellFailure("tool_failure", "boom")

    monkeypatch.setattr(adapter_cls, "_run_async", _tool_level_failure)
    adapter = adapter_cls()

    with pytest.raises(BenchmarkCellFailure) as exc_info:
        adapter.run("anything")

    assert exc_info.value.category == "tool_failure"
    assert "boom" in str(exc_info.value)


# =============================================================================
# 3. Guard refusals fail closed -- every disabled-env permutation, per adapter
# =============================================================================


def _no_env(mp: pytest.MonkeyPatch, competitor: str) -> None:
    pass


def _flag_only(mp: pytest.MonkeyPatch, competitor: str) -> None:
    mp.setenv(LIVE_PROVIDERS_ENABLED_ENV, "1")


def _flag_plus_wrong_allowlist_entry(mp: pytest.MonkeyPatch, competitor: str) -> None:
    mp.setenv(LIVE_PROVIDERS_ENABLED_ENV, "1")
    mp.setenv(LIVE_COMPETITORS_ENV, f"not-{competitor}")


def _allowlist_only_no_flag(mp: pytest.MonkeyPatch, competitor: str) -> None:
    mp.setenv(LIVE_COMPETITORS_ENV, competitor)


_KEYLESS_REFUSAL_CASES = [
    pytest.param(_no_env, id="no-flag-no-allowlist"),
    pytest.param(_flag_only, id="flag-only-no-allowlist"),
    pytest.param(_flag_plus_wrong_allowlist_entry, id="flag-plus-wrong-allowlist-entry"),
    pytest.param(_allowlist_only_no_flag, id="allowlist-only-no-flag"),
]

_KEYLESS_ADAPTERS = [
    pytest.param(
        context7_adapter,
        context7_adapter.Context7Adapter,
        "context7",
        id="context7-keyless",
    ),
    pytest.param(gitmcp_adapter, gitmcp_adapter.GitMcpAdapter, "gitmcp", id="gitmcp"),
    pytest.param(
        deepwiki_adapter, deepwiki_adapter.DeepWikiAdapter, "deepwiki", id="deepwiki"
    ),
]


@pytest.mark.parametrize("module,make_adapter,competitor", _KEYLESS_ADAPTERS)
@pytest.mark.parametrize("env_setup", _KEYLESS_REFUSAL_CASES)
def test_keyless_competitor_adapters_refuse_and_never_build_transport(
    module: Any,
    make_adapter: Any,
    competitor: str,
    env_setup: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(LIVE_PROVIDERS_ENABLED_ENV, raising=False)
    monkeypatch.delenv(LIVE_COMPETITORS_ENV, raising=False)
    env_setup(monkeypatch, competitor)
    _patch_no_transport(monkeypatch, module)

    with pytest.raises(LiveProviderDisabledError):
        make_adapter().run("anything")


def _no_env_keyed(mp: pytest.MonkeyPatch, key_env: str) -> None:
    pass


def _flag_no_key(mp: pytest.MonkeyPatch, key_env: str) -> None:
    mp.setenv(LIVE_PROVIDERS_ENABLED_ENV, "1")


def _key_no_flag(mp: pytest.MonkeyPatch, key_env: str) -> None:
    mp.setenv(key_env, "fake-not-a-real-key")


_KEYED_REFUSAL_CASES = [
    pytest.param(_no_env_keyed, id="no-flag-no-key"),
    pytest.param(_flag_no_key, id="flag-no-key"),
    pytest.param(_key_no_flag, id="key-no-flag"),
]

_KEYED_ADAPTERS = [
    pytest.param(
        context7_adapter,
        lambda: context7_adapter.Context7Adapter(key_mode=True),
        PROVIDER_API_KEY_ENV["context7"],
        id="context7-keyed",
    ),
    pytest.param(
        ref_tools_adapter,
        ref_tools_adapter.RefToolsAdapter,
        PROVIDER_API_KEY_ENV["ref"],
        id="ref-tools",
    ),
]


@pytest.mark.parametrize("module,make_adapter,key_env", _KEYED_ADAPTERS)
@pytest.mark.parametrize("env_setup", _KEYED_REFUSAL_CASES)
def test_keyed_competitor_adapters_refuse_and_never_build_transport(
    module: Any, make_adapter: Any, key_env: str, env_setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(LIVE_PROVIDERS_ENABLED_ENV, raising=False)
    monkeypatch.delenv(key_env, raising=False)
    env_setup(monkeypatch, key_env)
    _patch_no_transport(monkeypatch, module)

    with pytest.raises(LiveProviderDisabledError):
        make_adapter().run("anything")


# =============================================================================
# 4. require_live_competitor latch semantics in isolation
# =============================================================================


def test_require_live_competitor_refuses_without_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(LIVE_PROVIDERS_ENABLED_ENV, raising=False)
    monkeypatch.delenv(LIVE_COMPETITORS_ENV, raising=False)

    with pytest.raises(LiveProviderDisabledError, match=LIVE_PROVIDERS_ENABLED_ENV):
        require_live_competitor("gitmcp")


def test_require_live_competitor_refuses_with_flag_but_name_absent_from_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(LIVE_PROVIDERS_ENABLED_ENV, "1")
    monkeypatch.setenv(LIVE_COMPETITORS_ENV, "deepwiki")

    with pytest.raises(LiveProviderDisabledError, match=LIVE_COMPETITORS_ENV):
        require_live_competitor("gitmcp")


def test_require_live_competitor_refuses_with_allowlist_but_no_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(LIVE_PROVIDERS_ENABLED_ENV, raising=False)
    monkeypatch.setenv(LIVE_COMPETITORS_ENV, "gitmcp")

    with pytest.raises(LiveProviderDisabledError, match=LIVE_PROVIDERS_ENABLED_ENV):
        require_live_competitor("gitmcp")


def test_require_live_competitor_passes_only_with_both_latches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(LIVE_PROVIDERS_ENABLED_ENV, "1")
    monkeypatch.setenv(LIVE_COMPETITORS_ENV, "gitmcp")

    require_live_competitor("gitmcp")  # must not raise


def test_require_live_competitor_is_key_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    # No PROVIDER_API_KEY_ENV entry is consulted for a keyless competitor --
    # the allowlist is the second latch, not a key.
    monkeypatch.setenv(LIVE_PROVIDERS_ENABLED_ENV, "1")
    monkeypatch.setenv(LIVE_COMPETITORS_ENV, "gitmcp")
    monkeypatch.delenv(PROVIDER_API_KEY_ENV["ref"], raising=False)
    monkeypatch.delenv(PROVIDER_API_KEY_ENV["context7"], raising=False)

    require_live_competitor("gitmcp")  # must not raise despite no key env set anywhere


def test_require_live_competitor_allowlist_parsing_tolerant_of_spaces_and_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(LIVE_PROVIDERS_ENABLED_ENV, "1")
    monkeypatch.setenv(LIVE_COMPETITORS_ENV, " GitMCP , DeepWiki ")

    require_live_competitor("gitmcp")  # must not raise
    require_live_competitor("deepwiki")  # must not raise
    require_live_competitor("GITMCP")  # must not raise -- case-insensitive on both sides


# =============================================================================
# 5. Eligibility / exclusion screening
# =============================================================================


def _valid_competitor_entry(adapter: str = "gitmcp", **overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": adapter,
        "adapter": adapter,
        "pin": {
            "kind": "endpoint-date",
            "value": "https://example.test",
            "access_date": "2026-07-09",
        },
        "terms_check": {
            "verdict": "permitted",
            "checked_on": "2026-07-09",
            "source_url": "https://example.test/terms",
        },
        "eligibility": {"status": "eligible"},
    }
    entry.update(overrides)
    return entry


def test_validate_competitor_eligibility_accepts_a_well_formed_entry() -> None:
    validate_competitor_eligibility(_valid_competitor_entry())  # must not raise


def test_validate_competitor_eligibility_accepts_conditional_status_with_conditions() -> None:
    validate_competitor_eligibility(
        _valid_competitor_entry(
            eligibility={"status": "conditional", "conditions": ["needs permission"]}
        )
    )  # must not raise


def test_validate_competitor_eligibility_rejects_missing_pin() -> None:
    entry = _valid_competitor_entry()
    del entry["pin"]

    with pytest.raises(BenchmarkValidationError, match="pin"):
        validate_competitor_eligibility(entry)


def test_validate_competitor_eligibility_rejects_unrecognized_pin_kind() -> None:
    with pytest.raises(BenchmarkValidationError, match=r"pin\.kind"):
        validate_competitor_eligibility(
            _valid_competitor_entry(
                pin={"kind": "made-up", "value": "x", "access_date": "2026-07-09"}
            )
        )


def test_validate_competitor_eligibility_rejects_missing_terms_check() -> None:
    entry = _valid_competitor_entry()
    del entry["terms_check"]

    with pytest.raises(BenchmarkValidationError, match="terms_check"):
        validate_competitor_eligibility(entry)


def test_validate_competitor_eligibility_rejects_unrecognized_terms_verdict() -> None:
    with pytest.raises(BenchmarkValidationError, match=r"terms_check\.verdict"):
        validate_competitor_eligibility(
            _valid_competitor_entry(
                terms_check={
                    "verdict": "maybe",
                    "checked_on": "2026-07-09",
                    "source_url": "https://example.test",
                }
            )
        )


def test_validate_competitor_eligibility_rejects_missing_eligibility() -> None:
    entry = _valid_competitor_entry()
    del entry["eligibility"]

    with pytest.raises(BenchmarkValidationError, match=r"eligibility\.status"):
        validate_competitor_eligibility(entry)


def test_validate_competitor_eligibility_rejects_excluded_status_points_to_exclusions() -> None:
    with pytest.raises(BenchmarkValidationError, match="exclusions"):
        validate_competitor_eligibility(
            _valid_competitor_entry(eligibility={"status": "excluded"})
        )


def test_validate_manifest_eligibility_skips_non_competitor_adapter_entries() -> None:
    manifest = {"competitors": [{"id": "no-mcp", "adapter": "no-mcp-baseline"}]}

    validate_manifest_eligibility(manifest)  # must not raise despite no pin/terms metadata


def test_validate_manifest_eligibility_validates_only_competitor_adapter_entries() -> None:
    manifest = {
        "competitors": [
            {"id": "no-mcp", "adapter": "no-mcp-baseline"},
            _valid_competitor_entry("gitmcp"),
        ]
    }

    validate_manifest_eligibility(manifest)  # must not raise


def test_validate_manifest_eligibility_raises_for_excluded_competitor_entry() -> None:
    manifest = {
        "competitors": [_valid_competitor_entry("gitmcp", eligibility={"status": "excluded"})]
    }

    with pytest.raises(BenchmarkValidationError, match="gitmcp"):
        validate_manifest_eligibility(manifest)


def test_competitor_adapter_ids_are_a_subset_of_the_runner_dispatch_registry() -> None:
    from benchmarks.runner import _ADAPTER_DISPATCH

    assert COMPETITOR_ADAPTER_IDS <= set(_ADAPTER_DISPATCH)


# --- Integration: runner refuses at manifest-load time, before any cell ----


def _write_yaml(path: Path, data: dict[str, Any]) -> Path:
    path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
    return path


def _corpus(tmp_path: Path) -> Path:
    return _write_yaml(
        tmp_path / "corpus.yml",
        {
            "questions": [
                {
                    "id": "q001",
                    "prompt": "What does pathlib.Path.read_text return?",
                }
            ]
        },
    )


def test_run_benchmark_refuses_manifest_with_excluded_competitor_entry_and_writes_nothing(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "results" / "excluded"
    manifest_path = _write_yaml(
        tmp_path / "competitors.yml",
        {"competitors": [_valid_competitor_entry("gitmcp", eligibility={"status": "excluded"})]},
    )

    with pytest.raises(BenchmarkValidationError, match="gitmcp"):
        run_benchmark(
            BenchmarkConfig(
                corpus_path=_corpus(tmp_path), manifest_path=manifest_path, out_dir=out_dir
            )
        )

    assert not out_dir.exists()


def test_run_benchmark_refuses_manifest_with_incomplete_pin_metadata(tmp_path: Path) -> None:
    out_dir = tmp_path / "results" / "missing-pin"
    entry = _valid_competitor_entry("ref-tools")
    del entry["pin"]
    manifest_path = _write_yaml(tmp_path / "competitors.yml", {"competitors": [entry]})

    with pytest.raises(BenchmarkValidationError, match="ref-tools"):
        run_benchmark(
            BenchmarkConfig(
                corpus_path=_corpus(tmp_path), manifest_path=manifest_path, out_dir=out_dir
            )
        )

    assert not out_dir.exists()


def test_cli_exits_2_for_manifest_with_excluded_competitor_entry(tmp_path: Path) -> None:
    out_dir = tmp_path / "results" / "excluded-cli"
    manifest_path = _write_yaml(
        tmp_path / "competitors.yml",
        {"competitors": [_valid_competitor_entry("gitmcp", eligibility={"status": "excluded"})]},
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks",
            "run",
            "--corpus",
            str(_corpus(tmp_path)),
            "--manifest",
            str(manifest_path),
            "--out",
            str(out_dir),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        cwd=_REPO_ROOT,
    )

    assert result.returncode == 2
    assert "gitmcp" in result.stderr
    assert not out_dir.exists()


def test_exclusions_block_passes_validation_and_survives_snapshot_byte_for_byte(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "results" / "exclusions-block"
    manifest_bytes = (
        b"competitors:\n"
        b"  - id: no-mcp\n"
        b"    adapter: no-mcp-baseline\n"
        b"exclusions:\n"
        b"  - id: some-excluded-competitor\n"
        b'    reason: "vendor denied benchmarking permission"\n'
        b"    terms_check:\n"
        b"      verdict: forbidden-without-permission\n"
        b'      checked_on: "2026-07-09"\n'
        b'      source_url: "https://example.test/terms"\n'
        b'    decided_on: "2026-07-09"\n'
    )
    manifest_path = tmp_path / "competitors.yml"
    manifest_path.write_bytes(manifest_bytes)

    summary = run_benchmark(
        BenchmarkConfig(
            corpus_path=_corpus(tmp_path),
            manifest_path=manifest_path,
            out_dir=out_dir,
            run_id="exclusions-block",
        )
    )

    assert summary["succeeded_cells"] == 1
    assert (out_dir / "snapshots" / "competitor-manifest.yml").read_bytes() == manifest_bytes


# --- The committed template manifest ----------------------------------------


def test_template_manifest_parses_and_validates() -> None:
    data = yaml.safe_load(_TEMPLATE_MANIFEST_PATH.read_text(encoding="utf-8"))

    validate_manifest_eligibility(data)  # must not raise

    competitors = data["competitors"]
    assert {competitor["adapter"] for competitor in competitors} == COMPETITOR_ADAPTER_IDS
    assert data["exclusions"] == []


def test_template_manifest_dry_run_plans_all_four_competitor_cells(tmp_path: Path) -> None:
    out_dir = tmp_path / "results" / "template-dry-run"

    summary = run_benchmark(
        BenchmarkConfig(
            corpus_path=_corpus(tmp_path),
            manifest_path=_TEMPLATE_MANIFEST_PATH,
            out_dir=out_dir,
            run_id="template-dry-run",
            dry_run=True,
        )
    )

    assert summary["dry_run"] is True
    assert summary["planned_cells"] == 4
    assert not (out_dir / "transcripts").exists()


# =============================================================================
# 6. Registry integration: manifest adapter id -> correct adapter class
# =============================================================================


@pytest.mark.parametrize(
    "adapter_id,module,adapter_cls,result_cls,tool_name",
    [
        pytest.param(
            "context7",
            context7_adapter,
            context7_adapter.Context7Adapter,
            context7_adapter.Context7Result,
            "query-docs",
            id="context7",
        ),
        pytest.param(
            "gitmcp",
            gitmcp_adapter,
            gitmcp_adapter.GitMcpAdapter,
            gitmcp_adapter.GitMcpResult,
            "fetch_generic_url_content",
            id="gitmcp",
        ),
        pytest.param(
            "deepwiki",
            deepwiki_adapter,
            deepwiki_adapter.DeepWikiAdapter,
            deepwiki_adapter.DeepWikiResult,
            "ask_question",
            id="deepwiki",
        ),
        pytest.param(
            "ref-tools",
            ref_tools_adapter,
            ref_tools_adapter.RefToolsAdapter,
            ref_tools_adapter.RefToolsResult,
            "ref_read_url",
            id="ref-tools",
        ),
    ],
)
def test_competitor_adapter_is_dispatchable_via_manifest_adapter_id(
    adapter_id: str,
    module: Any,
    adapter_cls: type,
    result_cls: type,
    tool_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # This test is dispatch-wiring-only (registry -> correct adapter class,
    # tool_calls conversion into the transcript shape): it monkeypatches the
    # adapter class's own `run` method, so it never touches the guard or any
    # transport. Guard behavior is covered in section 3/4 above; flow
    # behavior against a scripted session is covered in section 1 above.
    def _fake_run(self: Any, prompt: str) -> Any:
        return result_cls(
            answer=f"fake-answer for {prompt}",
            tool_calls=[
                module.ToolCallRecord(
                    tool=tool_name, arguments={"q": prompt}, result={"ok": True}, is_error=False
                )
            ],
        )

    monkeypatch.setattr(adapter_cls, "run", _fake_run)
    out_dir = tmp_path / "results" / f"dispatch-{adapter_id}"

    summary = run_benchmark(
        BenchmarkConfig(
            corpus_path=_corpus(tmp_path),
            manifest_path=_write_yaml(
                tmp_path / "competitors.yml", {"competitors": [_valid_competitor_entry(adapter_id)]}
            ),
            out_dir=out_dir,
            run_id=f"dispatch-{adapter_id}",
        )
    )

    assert summary["succeeded_cells"] == 1
    transcript = json.loads(
        (out_dir / "transcripts" / adapter_id / "q001.json").read_text(encoding="utf-8")
    )
    assert transcript["status"] == "succeeded"
    assert transcript["answer"].startswith("fake-answer")
    assert transcript["adapter"] == adapter_id
    assert transcript["tool_calls"][0]["tool"] == tool_name
    assert transcript["tool_calls"][0]["is_error"] is False
    assert transcript["external_provider_calls"] is False
