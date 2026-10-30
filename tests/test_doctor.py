"""Tests for the doctor CLI subcommand (CLI-02).

`TestDoctor` spawns a real subprocess, matching test_stdio_hygiene.py. The
probe classes call the checks in-process.
"""
import os
import platform
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from mcp_server_python_docs.errors import FTS5UnavailableError
from mcp_server_python_docs.storage.db import assert_fts5_available


def _isolated_cache_env(tmpdir: str) -> dict[str, str]:
    """Build subprocess env that forces platformdirs into a temp cache root."""
    tmp_path = Path(tmpdir)
    overrides = {
        "HOME": str(tmp_path),
        "XDG_CACHE_HOME": str(tmp_path),
        "LOCALAPPDATA": str(tmp_path / "AppData" / "Local"),
        "APPDATA": str(tmp_path / "AppData" / "Roaming"),
        "USERPROFILE": str(tmp_path),
    }
    return {**os.environ, **overrides}


class TestDoctor:
    """Verify doctor subcommand behavior."""

    def test_doctor_runs_without_error(self):
        """doctor command runs and produces output on stderr."""
        result = subprocess.run(
            [sys.executable, "-m", "mcp_server_python_docs", "doctor"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        # May exit 0 or 1 depending on index presence, but should not crash
        assert result.returncode in (0, 1)
        assert "Python version" in result.stderr
        assert "SQLite FTS5" in result.stderr
        assert "Cache directory" in result.stderr

    def test_doctor_no_stdout(self):
        """All doctor output goes to stderr, nothing to stdout."""
        result = subprocess.run(
            [sys.executable, "-m", "mcp_server_python_docs", "doctor"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.stdout == "", f"doctor produced stdout: {result.stdout!r}"

    def test_doctor_checks_python_version(self):
        """Doctor reports PASS for Python version (we run on >= 3.12)."""
        result = subprocess.run(
            [sys.executable, "-m", "mcp_server_python_docs", "doctor"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert "PASS: Python version" in result.stderr

    def test_doctor_checks_fts5(self):
        """Doctor reports PASS for FTS5 (test environment has FTS5)."""
        result = subprocess.run(
            [sys.executable, "-m", "mcp_server_python_docs", "doctor"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert "PASS: SQLite FTS5" in result.stderr

    def test_doctor_checks_disk_space(self):
        """Doctor reports PASS for disk space (test systems have > 1 GB)."""
        result = subprocess.run(
            [sys.executable, "-m", "mcp_server_python_docs", "doctor"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert "PASS: Disk space" in result.stderr

    def test_doctor_checks_build_venv_support(self):
        """Doctor reports whether build-index can create Sphinx venvs."""
        result = subprocess.run(
            [sys.executable, "-m", "mcp_server_python_docs", "doctor"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert "Build venv support" in result.stderr

    def test_doctor_reports_missing_index(self):
        """Doctor reports FAIL for Index database when pointed at empty dir."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                [sys.executable, "-m", "mcp_server_python_docs", "doctor"],
                capture_output=True,
                text=True,
                timeout=15,
                env=_isolated_cache_env(tmpdir),
            )
            assert "FAIL: Index database" in result.stderr
            assert "build-index" in result.stderr

    def test_doctor_exit_code_on_failure(self):
        """Doctor exits with code 1 when any probe fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                [sys.executable, "-m", "mcp_server_python_docs", "doctor"],
                capture_output=True,
                text=True,
                timeout=15,
                env=_isolated_cache_env(tmpdir),
            )
            assert result.returncode == 1

    def test_doctor_in_help(self):
        """doctor appears as a subcommand in --help output."""
        result = subprocess.run(
            [sys.executable, "-m", "mcp_server_python_docs", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        combined = result.stdout + result.stderr
        assert "doctor" in combined


class TestBuildVenvSupportProbe:
    """Verify the build-index venv prerequisite probe."""

    def test_probe_reports_missing_ensurepip_with_platform_package_hint(self, monkeypatch):
        """Missing ensurepip points users to the versioned Debian/Ubuntu venv package."""

        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(
                args=args[0],
                returncode=1,
                stdout="",
                stderr="ModuleNotFoundError: No module named 'ensurepip'",
            )

        monkeypatch.setattr(subprocess, "run", fake_run)

        from mcp_server_python_docs.diagnostics import check_build_venv_support

        result = check_build_venv_support()

        assert result.passed is False
        assert "ensurepip" in result.detail
        assert (
            f"python{sys.version_info.major}.{sys.version_info.minor}-venv"
            in result.detail
        )

    def test_probe_passes_when_venv_and_ensurepip_are_importable(self, monkeypatch):
        """Available venv and ensurepip support passes the build prerequisite probe."""

        def fake_run(*args, **kwargs):
            command = args[0]
            assert "ensurepip" in command[-1]
            assert "venv" in command[-1]
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout="",
                stderr="",
            )

        monkeypatch.setattr(subprocess, "run", fake_run)

        from mcp_server_python_docs.diagnostics import check_build_venv_support

        result = check_build_venv_support()

        assert result.passed is True
        assert "build-index" in result.detail


class _StubConnection:
    """A sqlite3 connection stand-in whose CREATE VIRTUAL TABLE fails."""

    def __init__(self, create_error: str, compile_options: tuple[str, ...] = ()):
        self.create_error = create_error
        self.compile_options = compile_options

    def execute(self, sql: str, *params):
        if sql.startswith("CREATE VIRTUAL TABLE"):
            raise sqlite3.OperationalError(self.create_error)
        return [(option,) for option in self.compile_options]  # PRAGMA compile_options


class TestFts5Probe:
    """One FTS5 check, storage.db.assert_fts5_available, serves the server and doctor."""

    def test_doctor_reports_the_fts5_check_error(self, tmp_path, monkeypatch):
        from click.testing import CliRunner

        from mcp_server_python_docs.__main__ import main

        def no_fts5(conn):
            raise FTS5UnavailableError(
                "SQLite FTS5 extension is not available in this Python build.\nprobe hint"
            )

        monkeypatch.setattr("mcp_server_python_docs.storage.db.get_cache_dir", lambda: tmp_path)
        monkeypatch.setattr("mcp_server_python_docs.storage.db.assert_fts5_available", no_fts5)
        result = CliRunner().invoke(main, ["doctor"])

        assert result.exit_code == 1
        assert (
            "x FAIL: SQLite FTS5 -- SQLite FTS5 extension is not available in this Python build."
            in result.output
        )
        assert "probe hint" in result.output

    def test_doctor_fts5_pass_shows_sqlite_version(self, tmp_path, monkeypatch):
        from click.testing import CliRunner

        from mcp_server_python_docs.__main__ import main

        monkeypatch.setattr("mcp_server_python_docs.storage.db.get_cache_dir", lambda: tmp_path)
        result = CliRunner().invoke(main, ["doctor"])

        assert f"+ PASS: SQLite FTS5 -- SQLite {sqlite3.sqlite_version}" in result.output

    def test_fts5_hint_never_suggests_pysqlite3(self, monkeypatch):
        # The old pysqlite3 hint was Linux x86-64 only; keep testing there.
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        monkeypatch.setattr(platform, "machine", lambda: "x86_64")
        with pytest.raises(FTS5UnavailableError) as excinfo:
            assert_fts5_available(_StubConnection("no such module: fts5"))
        message = str(excinfo.value)
        assert "pysqlite3" not in message
        assert "uv python install" in message
        assert "python.org" in message

    def test_readonly_probe_without_fts5_gets_the_one_hint(self, monkeypatch):
        # The old pysqlite3 hint was Linux x86-64 only; keep testing there.
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        monkeypatch.setattr(platform, "machine", lambda: "x86_64")
        conn = _StubConnection("attempt to write a readonly database", ("THREADSAFE=1",))
        with pytest.raises(FTS5UnavailableError) as excinfo:
            assert_fts5_available(conn)
        assert "pysqlite3" not in str(excinfo.value)
        assert "uv python install" in str(excinfo.value)

    def test_readonly_probe_with_fts5_compiled_in_passes(self):
        conn = _StubConnection(
            "attempt to write a readonly database", ("ENABLE_FTS5", "THREADSAFE=1")
        )
        assert assert_fts5_available(conn) is None
