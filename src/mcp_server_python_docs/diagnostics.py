"""Environment diagnostics shared by CLI health checks."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass

from mcp_server_python_docs.ingestion.sphinx_json import create_sphinx_venv


@dataclass(frozen=True)
class DiagnosticResult:
    """Result for a single environment diagnostic probe."""

    passed: bool
    detail: str


def _combined_output_excerpt(stdout: str, stderr: str, limit: int = 500) -> str:
    combined = "\n".join(part.strip() for part in (stderr, stdout) if part.strip())
    if len(combined) <= limit:
        return combined
    return combined[-limit:]


def _check_build_venv_runs(executable: str, timeout: float) -> DiagnosticResult:
    """Create a throwaway venv the way build-index does and run its python."""
    # No pip, to stay fast: a venv python that cannot start fails without it.
    with tempfile.TemporaryDirectory(prefix="pdmcp-venv-probe-") as venv_dir:
        venv_python = os.path.join(
            venv_dir, "Scripts" if sys.platform == "win32" else "bin", "python"
        )
        try:
            create_sphinx_venv(venv_dir, with_pip=False)
            result = subprocess.run(
                [venv_python, "-c", "import sys"],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            return DiagnosticResult(
                passed=False,
                detail=f"{executable} cannot create and run a build-index Sphinx venv: {e}",
            )

    if result.returncode == 0:
        return DiagnosticResult(
            passed=True,
            detail=f"{executable} can create and run build-index Sphinx venvs",
        )

    detail = (
        f"The python of a fresh build-index Sphinx venv made from {executable} "
        f"exited with code {result.returncode}, so build-index cannot run Sphinx."
    )
    output = _combined_output_excerpt(result.stdout, result.stderr)
    if output:
        detail = f"{detail} Output: {output}"
    return DiagnosticResult(passed=False, detail=detail)


def check_build_venv_support(timeout: float = 10.0) -> DiagnosticResult:
    """Check that build-index can create and run pip-enabled Sphinx environments.

    ``build-index`` creates a disposable Sphinx virtual environment with pip.
    Debian/Ubuntu hosts without the matching ``pythonX.Y-venv`` package can run
    the server but fail when that environment needs ``ensurepip``. A venv whose
    python cannot start fails too (a copied uv-managed CPython on macOS cannot
    find its libpython), so the probe then creates one the same way and runs it.
    """
    executable = sys.executable
    package_name = f"python{sys.version_info.major}.{sys.version_info.minor}-venv"
    command = [executable, "-c", "import ensurepip; import venv"]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return DiagnosticResult(
            passed=False,
            detail=f"{executable} not found; build-index cannot create Sphinx venvs",
        )
    except subprocess.TimeoutExpired:
        return DiagnosticResult(
            passed=False,
            detail=(
                f"{executable} timed out while checking venv/ensurepip support "
                "for build-index"
            ),
        )

    if result.returncode == 0:
        return _check_build_venv_runs(executable, timeout)

    detail = (
        f"{executable} cannot import venv/ensurepip; build-index needs them to "
        "create Sphinx venvs. On Debian/Ubuntu, install "
        f"{package_name} for this interpreter."
    )
    output = _combined_output_excerpt(result.stdout, result.stderr)
    if output:
        detail = f"{detail} Output: {output}"

    return DiagnosticResult(passed=False, detail=detail)
