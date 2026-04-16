"""Tests for the FastMCP server shim (server.py).

Covers M-4 (_require_ctx guard against None context).
"""
from __future__ import annotations

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from mcp_server_python_docs.server import _require_ctx


class TestRequireCtx:
    """M-4: _require_ctx raises ToolError when ctx is None."""

    def test_none_ctx_raises_tool_error(self):
        with pytest.raises(ToolError) as excinfo:
            _require_ctx(None)
        assert "MCP context unavailable" in str(excinfo.value)

    def test_non_none_ctx_returned_unchanged(self):
        """A sentinel object is passed through; the guard is only about None."""

        class _Sentinel:
            pass

        sentinel = _Sentinel()
        # The type signature says Context, but the runtime check is `is None`.
        result = _require_ctx(sentinel)  # type: ignore[arg-type]
        assert result is sentinel


class TestToolShimsGuardAgainstNoneCtx:
    """M-4: each @mcp.tool shim calls _require_ctx(ctx) at the top."""

    def test_tools_reject_none_ctx(self):
        """Verify by reading the server module source that every tool calls
        _require_ctx. This is a structural check — it protects future
        refactors from silently dropping the guard from one shim."""
        import inspect

        from mcp_server_python_docs import server as server_module

        source = inspect.getsource(server_module.create_server)
        # Every @mcp.tool def must call _require_ctx(ctx) near the top of its body.
        tool_names = ["search_docs", "get_docs", "list_versions", "detect_python_version"]
        for name in tool_names:
            # Find the def and assert a _require_ctx call follows within ~10 lines.
            def_marker = f"def {name}("
            def_idx = source.index(def_marker)
            # Take a window from the def onward.
            window = source[def_idx:def_idx + 800]
            assert "_require_ctx(ctx)" in window, (
                f"tool shim {name!r} is missing _require_ctx(ctx) guard"
            )
