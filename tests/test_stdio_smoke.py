"""Stdio smoke tests (TEST-05).

Spawns the MCP server as a real subprocess, sends JSON-RPC messages over
stdin/stdout, and verifies:
1. Zero stdout pollution (only valid JSON-RPC lines)
2. tools/list returns search_docs, get_docs, list_versions
3. Each tool round-trip returns a result without crashing
4. No nextCursor in tools/list (HYGN-06)

These tests create a minimal index.db in a temp dir so the server starts
successfully. The MCP stdio protocol uses newline-delimited JSON-RPC.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from mcp_server_python_docs.storage.db import bootstrap_schema, get_readwrite_connection


def _isolated_cache_env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    """Build subprocess env and matching platformdirs cache path."""
    overrides = {
        "HOME": str(tmp_path),
        "XDG_CACHE_HOME": str(tmp_path),
        "LOCALAPPDATA": str(tmp_path / "AppData" / "Local"),
        "APPDATA": str(tmp_path / "AppData" / "Roaming"),
        "USERPROFILE": str(tmp_path),
    }
    env = {**os.environ, **overrides}
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import platformdirs; print(platformdirs.user_cache_dir('mcp-python-docs'))",
        ],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return env, Path(result.stdout.strip())


def _create_test_index(cache_dir: Path) -> Path:
    """Create a minimal index.db for the server to start with."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    db_path = cache_dir / "index.db"
    conn = get_readwrite_connection(db_path)
    bootstrap_schema(conn)

    # Insert minimal data so the server has something to query
    conn.execute(
        "INSERT INTO doc_sets (source, version, language, label, is_default, base_url) "
        "VALUES ('python-docs', '3.13', 'en', 'Python 3.13', 1, "
        "'https://docs.python.org/3.13/')"
    )
    doc_set_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # A single symbol for search_docs to find
    conn.execute(
        "INSERT INTO symbols (doc_set_id, qualified_name, normalized_name, "
        "module, symbol_type, uri, anchor) "
        "VALUES (?, 'asyncio.TaskGroup', 'asyncio_taskgroup', 'asyncio', 'class', "
        "'library/asyncio-task.html#asyncio.TaskGroup', 'asyncio.TaskGroup')",
        (doc_set_id,),
    )

    # A document + section for get_docs to find
    conn.execute(
        "INSERT INTO documents (doc_set_id, uri, slug, title, content_text, char_count) "
        "VALUES (?, 'library/asyncio-task.html', 'library/asyncio-task.html', "
        "'asyncio Tasks', 'Task group documentation.', 28)",
        (doc_set_id,),
    )
    doc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO sections (document_id, uri, anchor, heading, level, ordinal, "
        "content_text, char_count) "
        "VALUES (?, 'library/asyncio-task.html#asyncio.TaskGroup', 'asyncio.TaskGroup', "
        "'asyncio.TaskGroup', 2, 1, 'A task group manages concurrent tasks.', 38)",
        (doc_id,),
    )

    conn.commit()

    # Rebuild FTS indexes
    conn.execute("INSERT INTO symbols_fts(symbols_fts) VALUES('rebuild')")
    conn.execute("INSERT INTO sections_fts(sections_fts) VALUES('rebuild')")
    conn.execute("INSERT INTO examples_fts(examples_fts) VALUES('rebuild')")
    conn.commit()
    conn.close()

    return db_path


def _make_request(method: str, params: dict[str, object] | None = None, req_id: int = 1) -> bytes:
    """Build a JSON-RPC 2.0 request as newline-terminated bytes."""
    msg: dict[str, object] = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg).encode() + b"\n"


def _make_notification(method: str, params: dict[str, object] | None = None) -> bytes:
    """Build a JSON-RPC 2.0 notification (no id) as newline-terminated bytes."""
    msg: dict[str, object] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg).encode() + b"\n"


def _read_responses(stdout_data: bytes) -> list[dict]:
    """Parse all JSON-RPC responses from raw stdout bytes."""
    responses = []
    for line in stdout_data.split(b"\n"):
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
            responses.append(parsed)
        except json.JSONDecodeError:
            # Non-JSON line on stdout = pollution
            responses.append({"_raw": line.decode("utf-8", errors="replace")})
    return responses


def _jsonrpc_frames(stream_data: bytes) -> list[dict]:
    """Return JSON-RPC objects parsed from a stream, ignoring log lines."""
    frames: list[dict] = []
    for line in stream_data.split(b"\n"):
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and parsed.get("jsonrpc") == "2.0":
            frames.append(parsed)
    return frames


def _assert_protocol_on_stdout_only(result: subprocess.CompletedProcess) -> list[dict]:
    """Assert JSON-RPC protocol frames are emitted only on stdout."""
    responses = _read_responses(result.stdout)
    assert responses, f"No JSON-RPC responses on stdout; stderr was: {result.stderr!r}"

    for resp in responses:
        assert "_raw" not in resp, f"Non-JSON stdout pollution: {resp.get('_raw')}"
        assert resp.get("jsonrpc") == "2.0", f"Missing JSON-RPC frame on stdout: {resp}"

    stderr_frames = _jsonrpc_frames(result.stderr)
    assert stderr_frames == [], f"JSON-RPC frames leaked to stderr: {stderr_frames}"
    return responses


def _find_response(responses: list[dict], req_id: int) -> dict | None:
    """Find a JSON-RPC response matching the given request id."""
    for resp in responses:
        if resp.get("id") == req_id:
            return resp
    return None


def _request_ids(stdin_data: bytes) -> list[int]:
    """Return request ids present in newline-delimited JSON-RPC input."""
    request_ids: list[int] = []
    for line in stdin_data.splitlines():
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "id" in parsed:
            request_ids.append(parsed["id"])
    return request_ids


class TestStdioSmoke:
    """Spawn the MCP server as a subprocess and verify protocol compliance."""

    @pytest.fixture(autouse=True)
    def _setup_test_env(self, tmp_path):
        """Create a temp dir with a minimal index.db."""
        self.tmp_dir = tmp_path
        self.env, self.cache_dir = _isolated_cache_env(self.tmp_dir)
        _create_test_index(self.cache_dir)

    def _run_server_with_input(
        self, stdin_data: bytes, timeout: int = 15,
    ) -> subprocess.CompletedProcess:
        """Run the server subprocess with line-paced stdin and return the result."""
        proc = subprocess.Popen(
            [sys.executable, "-m", "mcp_server_python_docs", "serve"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.env,
        )
        assert proc.stdin is not None
        assert proc.stdout is not None
        assert proc.stderr is not None

        stdout_lines: list[bytes] = []
        stderr_lines: list[bytes] = []
        output_lock = threading.Lock()

        def read_stream(stream, sink: list[bytes]) -> None:
            for line in iter(stream.readline, b""):
                with output_lock:
                    sink.append(line)

        stdout_thread = threading.Thread(
            target=read_stream,
            args=(proc.stdout, stdout_lines),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=read_stream,
            args=(proc.stderr, stderr_lines),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        expected_ids = _request_ids(stdin_data)
        deadline = time.monotonic() + timeout
        try:
            for line in stdin_data.splitlines(keepends=True):
                proc.stdin.write(line)
                proc.stdin.flush()

            while time.monotonic() < deadline:
                with output_lock:
                    responses = _read_responses(b"".join(stdout_lines))
                if all(_find_response(responses, req_id) is not None for req_id in expected_ids):
                    break
                if proc.poll() is not None:
                    break
                time.sleep(0.02)
        finally:
            try:
                proc.stdin.close()
            except BrokenPipeError:
                # The subprocess may have already closed stdin after handling the requests.
                pass
            proc.stdin = None

        remaining = max(0.1, deadline - time.monotonic())
        try:
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

        stdout_thread.join(timeout=1)
        stderr_thread.join(timeout=1)
        stdout = b"".join(stdout_lines)
        stderr = b"".join(stderr_lines)
        return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)

    def test_server_lists_tools_no_stdout_pollution(self):
        """Server returns tool list and stdout has no non-JSON-RPC bytes."""
        stdin_data = (
            _make_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.1"},
            }, req_id=1)
            + _make_notification("notifications/initialized")
            + _make_request("tools/list", {}, req_id=2)
        )

        result = self._run_server_with_input(stdin_data)

        responses = _assert_protocol_on_stdout_only(result)

        # Find the tools/list response
        tools_resp = _find_response(responses, 2)
        assert tools_resp is not None, f"Server exited before tools/list response: {responses}"
        assert "result" in tools_resp, f"tools/list error: {tools_resp}"
        tool_names = [t["name"] for t in tools_resp["result"].get("tools", [])]
        assert "search_docs" in tool_names
        assert "get_docs" in tool_names
        assert "list_versions" in tool_names
        # HYGN-06: no nextCursor
        assert "nextCursor" not in tools_resp["result"]

    def test_search_docs_round_trip(self):
        """search_docs tool call returns a result without crashing."""
        stdin_data = (
            _make_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.1"},
            }, req_id=1)
            + _make_notification("notifications/initialized")
            + _make_request("tools/call", {
                "name": "search_docs",
                "arguments": {"query": "asyncio.TaskGroup"},
            }, req_id=2)
        )

        result = self._run_server_with_input(stdin_data)
        responses = _assert_protocol_on_stdout_only(result)

        # Find the tools/call response
        call_resp = _find_response(responses, 2)
        assert call_resp is not None, f"Server exited before search_docs response: {responses}"
        assert "result" in call_resp, f"tools/call error: {call_resp}"
        content = call_resp["result"].get("content", [])
        assert len(content) >= 1, "search_docs returned no content"

    def test_list_versions_round_trip(self):
        """list_versions tool call returns version data."""
        stdin_data = (
            _make_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.1"},
            }, req_id=1)
            + _make_notification("notifications/initialized")
            + _make_request("tools/call", {
                "name": "list_versions",
                "arguments": {},
            }, req_id=2)
        )

        result = self._run_server_with_input(stdin_data)
        responses = _assert_protocol_on_stdout_only(result)

        call_resp = _find_response(responses, 2)
        assert call_resp is not None, f"Server exited before list_versions response: {responses}"
        assert "result" in call_resp, f"tools/call error: {call_resp}"

    def test_all_stdout_is_valid_jsonrpc(self):
        """Every byte on stdout is part of a valid JSON-RPC message."""
        stdin_data = (
            _make_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.1"},
            }, req_id=1)
            + _make_notification("notifications/initialized")
            + _make_request("tools/list", {}, req_id=2)
            + _make_request("tools/call", {
                "name": "search_docs",
                "arguments": {"query": "json.dumps"},
            }, req_id=3)
        )

        result = self._run_server_with_input(stdin_data)

        # Parse every non-empty line as JSON
        for line in result.stdout.split(b"\n"):
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                pytest.fail(f"Non-JSON stdout line: {line!r}")
            assert "jsonrpc" in parsed, f"Missing jsonrpc field: {parsed}"
            assert parsed["jsonrpc"] == "2.0", f"Wrong jsonrpc version: {parsed}"
