"""MCP subprocess smoke tests for get_docs and persistent cache behavior."""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

from mcp_server_python_docs.services.persistent_cache import _NO_ANCHOR_KEY
from mcp_server_python_docs.storage.db import bootstrap_schema, get_readwrite_connection
from tests.test_stdio_smoke import (
    _assert_protocol_on_stdout_only,
    _find_response,
    _isolated_cache_env,
    _make_notification,
    _make_request,
)


def _create_contentful_json_index(cache_dir: Path) -> Path:
    """Create a deterministic contentful docs index for subprocess smoke tests."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    db_path = cache_dir / "index.db"
    conn = get_readwrite_connection(db_path)
    bootstrap_schema(conn)
    conn.execute(
        "INSERT INTO doc_sets (source, version, language, label, is_default, base_url) "
        "VALUES ('python-docs', '3.13', 'en', 'Python 3.13', 1, "
        "'https://docs.python.org/3.13/')"
    )
    doc_set_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO symbols (doc_set_id, qualified_name, normalized_name, "
        "module, symbol_type, uri, anchor) "
        "VALUES (?, 'json.dumps', 'json_dumps', 'json', 'function', "
        "'library/json.html#json.dumps', 'json.dumps')",
        (doc_set_id,),
    )
    conn.execute(
        "INSERT INTO documents (doc_set_id, uri, slug, title, content_text, char_count) "
        "VALUES (?, 'library/json.html', 'library/json.html', "
        "'json — JSON encoder and decoder', "
        "'The json module exposes APIs for encoding and decoding JSON data.', 64)",
        (doc_set_id,),
    )
    doc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.executemany(
        "INSERT INTO sections (document_id, uri, anchor, heading, level, ordinal, "
        "content_text, char_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                doc_id,
                "library/json.html#top",
                "top",
                "json — JSON encoder and decoder",
                1,
                1,
                "The json module exposes APIs for encoding and decoding JSON data.",
                64,
            ),
            (
                doc_id,
                "library/json.html#json.dumps",
                "json.dumps",
                "json.dumps",
                2,
                2,
                "Serialize obj to a JSON formatted str using a conversion table.",
                62,
            ),
        ],
    )
    conn.commit()
    conn.execute("INSERT INTO symbols_fts(symbols_fts) VALUES('rebuild')")
    conn.execute("INSERT INTO sections_fts(sections_fts) VALUES('rebuild')")
    conn.execute("INSERT INTO examples_fts(examples_fts) VALUES('rebuild')")
    conn.commit()
    conn.close()
    return db_path


def _run_server(stdin_data: bytes, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "mcp_server_python_docs", "serve"],
        input=stdin_data,
        capture_output=True,
        timeout=15,
        env=env,
    )


def _initialized_tool_call(name: str, arguments: dict, req_id: int = 2) -> bytes:
    return (
        _make_request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.1"},
            },
            req_id=1,
        )
        + _make_notification("notifications/initialized")
        + _make_request("tools/call", {"name": name, "arguments": arguments}, req_id=req_id)
    )


def _tool_structured_content(result: subprocess.CompletedProcess, req_id: int = 2) -> dict:
    responses = _assert_protocol_on_stdout_only(result)
    response = _find_response(responses, req_id)
    assert response is not None, f"Missing tools/call response: {responses}"
    assert "result" in response, response
    assert response["result"].get("isError") is not True, response
    return response["result"]["structuredContent"]


def _tool_error_text(result: subprocess.CompletedProcess, req_id: int = 2) -> str:
    responses = _assert_protocol_on_stdout_only(result)
    response = _find_response(responses, req_id)
    assert response is not None, f"Missing tools/call response: {responses}"
    assert response["result"].get("isError") is True, response
    return "\n".join(item.get("text", "") for item in response["result"].get("content", []))


def test_get_docs_cache_restart_and_corrupt_cache_fallback(tmp_path: Path):
    """Exercise get_docs through real MCP stdio with isolated contentful cache."""
    env, cache_dir = _isolated_cache_env(tmp_path)
    _create_contentful_json_index(cache_dir)
    cache_path = cache_dir / "retrieved-docs-cache.sqlite3"

    full_page = _tool_structured_content(
        _run_server(
            _initialized_tool_call(
                "get_docs",
                {"slug": "library/json.html", "version": "3.13"},
            ),
            env,
        )
    )
    assert full_page["slug"] == "library/json.html"
    assert full_page["anchor"] is None
    assert "json module" in full_page["content"]

    with sqlite3.connect(cache_path) as conn:
        rows = conn.execute(
            "SELECT version, slug, anchor, max_chars, start_index, length(result_json) "
            "FROM retrieved_docs_cache"
        ).fetchall()
    assert len(rows) == 1
    version, slug, anchor, max_chars, start_index, result_json_length = rows[0]
    assert (version, slug, anchor, max_chars, start_index) == (
        "3.13",
        "library/json.html",
        _NO_ANCHOR_KEY,
        8000,
        0,
    )
    assert result_json_length > 0

    restarted_page = _tool_structured_content(
        _run_server(
            _initialized_tool_call(
                "get_docs",
                {"slug": "library/json.html", "version": "3.13"},
            ),
            env,
        )
    )
    assert restarted_page == full_page

    section = _tool_structured_content(
        _run_server(
            _initialized_tool_call(
                "get_docs",
                {
                    "slug": "library/json.html",
                    "version": "3.13",
                    "anchor": "json.dumps",
                },
            ),
            env,
        )
    )
    assert section["anchor"] == "json.dumps"
    assert "Serialize obj" in section["content"]

    empty_anchor_error = _tool_error_text(
        _run_server(
            _initialized_tool_call(
                "get_docs",
                {"slug": "library/json.html", "version": "3.13", "anchor": ""},
            ),
            env,
        )
    )
    assert "Section '' not found" in empty_anchor_error

    cache_path.write_bytes(b"not a sqlite database")
    after_corrupt_cache = _tool_structured_content(
        _run_server(
            _initialized_tool_call(
                "get_docs",
                {"slug": "library/json.html", "version": "3.13"},
            ),
            env,
        )
    )
    assert after_corrupt_cache == full_page
