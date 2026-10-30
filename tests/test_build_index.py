"""Tests for the build-index CLI subcommand."""
import logging
import sqlite3
import subprocess
import sys
import venv

import pytest
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


@pytest.mark.parametrize(
    ("platform", "symlinks"),
    [("darwin", True), ("linux", True), ("win32", False)],
)
def test_build_index_symlinks_the_sphinx_venv_python_on_posix(
    tmp_path, monkeypatch, platform, symlinks
):
    """A copied venv python cannot load uv-managed CPython's libpython on macOS (D1).

    Windows keeps venv's default (copies), because symlinks there need privileges.
    """
    from mcp_server_python_docs.__main__ import main

    created: list[dict] = []

    def record_venv(env_dir, **kwargs):
        created.append(kwargs)
        raise RuntimeError("stop after the venv step")

    def git_clone(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("mcp_server_python_docs.storage.db.get_cache_dir", lambda: tmp_path)
    monkeypatch.setattr("mcp_server_python_docs.ingestion.publish.get_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "mcp_server_python_docs.ingestion.inventory.ingest_inventory", lambda *a, **k: 0
    )
    monkeypatch.setattr(subprocess, "run", git_clone)
    monkeypatch.setattr(
        "mcp_server_python_docs.__main__._verify_cpython_source_sha", lambda *a, **k: None
    )
    monkeypatch.setattr(venv, "create", record_venv)
    monkeypatch.setattr(sys, "platform", platform)

    CliRunner().invoke(main, ["build-index", "--versions", "3.13"])

    assert len(created) == 1
    assert created[0]["with_pip"] is True
    assert created[0].get("symlinks", False) is symlinks
