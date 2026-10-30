"""Tests for the build-index CLI subcommand."""
import logging
import sqlite3

from click.testing import CliRunner

from mcp_server_python_docs.errors import FTS5UnavailableError

FTS5_HINT = "SQLite FTS5 extension is not available in this Python build.\nprobe hint"


def test_build_index_without_fts5_reports_the_fts5_hint(tmp_path, monkeypatch, caplog):
    """A Python without FTS5 gets the FTS5 check's hint, not schema.sql's raw error (STOR-08)."""
    from mcp_server_python_docs.__main__ import main

    def schema_without_fts5(conn):
        raise sqlite3.OperationalError("no such module: fts5")

    def no_fts5(conn):
        raise FTS5UnavailableError(FTS5_HINT)

    monkeypatch.setattr("mcp_server_python_docs.storage.db.get_cache_dir", lambda: tmp_path)
    monkeypatch.setattr("mcp_server_python_docs.ingestion.publish.get_cache_dir", lambda: tmp_path)
    monkeypatch.setattr("mcp_server_python_docs.storage.db.bootstrap_schema", schema_without_fts5)
    monkeypatch.setattr("mcp_server_python_docs.storage.db.assert_fts5_available", no_fts5)
    with caplog.at_level(logging.ERROR):
        result = CliRunner().invoke(main, ["build-index", "--versions", "3.13", "--skip-content"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)  # a clean exit, not a traceback
    assert FTS5_HINT in caplog.text
