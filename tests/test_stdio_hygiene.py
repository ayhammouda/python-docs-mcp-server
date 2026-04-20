"""Stdio hygiene tests (HYGN-04).

Spawns the MCP server as a subprocess and asserts zero non-MCP bytes
appear on stdout. This catches:
- Stray print() calls
- Library warnings to stdout
- C extension writes to fd 1
- atexit handlers printing to stdout
"""
import os
import subprocess
import sys
from pathlib import Path


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


class TestStdioHygiene:
    """Verify stdout contains only MCP protocol messages."""

    def test_import_produces_no_stdout(self):
        """Importing the package should produce zero stdout output."""
        result = subprocess.run(
            [sys.executable, "-c", "import mcp_server_python_docs"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.stdout == "", (
            f"Import produced stdout: {result.stdout!r}"
        )

    def test_cli_help_is_clean(self):
        """--help output should be clean and contain expected commands.

        Note: Because our fd redirect sends stdout to stderr, Click's help
        output ends up on stderr. This is intentional -- it proves the
        redirect is working. We verify the content is correct regardless
        of which stream it arrives on.
        """
        result = subprocess.run(
            [sys.executable, "-m", "mcp_server_python_docs", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        # Help text ends up on stderr due to our fd redirect
        combined = result.stdout + result.stderr
        assert "Usage:" in combined or "usage:" in combined.lower()
        assert "serve" in combined
        assert "build-index" in combined

    def test_server_startup_no_index_stderr_only(self):
        """Starting server without index.db should produce only stderr output."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                [sys.executable, "-m", "mcp_server_python_docs", "serve"],
                capture_output=True,
                text=True,
                timeout=10,
                env=_isolated_cache_env(tmpdir),
            )
            # Server should exit with error (missing index)
            assert result.returncode != 0
            # stdout should have NO output (fd 1 redirected to stderr)
            assert result.stdout == "", (
                f"Server startup produced stdout: {result.stdout!r}"
            )
            # stderr should have the missing-index message
            assert "build-index" in result.stderr

    def test_build_index_bad_version_stderr_only(self):
        """build-index with unreachable version should log to stderr, not stdout."""
        result = subprocess.run(
            [
                sys.executable, "-m", "mcp_server_python_docs",
                "build-index", "--versions", "99.99",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        # This will fail (invalid version), but any output should be on stderr
        # stdout should be empty because of fd redirection
        assert result.stdout == "", (
            f"build-index produced stdout: {result.stdout!r}"
        )
