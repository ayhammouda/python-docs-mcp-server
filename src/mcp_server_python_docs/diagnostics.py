"""Environment diagnostics shared by CLI health checks."""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass


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


def check_build_venv_support(
    python_executable: str | None = None,
    timeout: float = 10.0,
) -> DiagnosticResult:
    """Check that build-index can create pip-enabled Sphinx environments.

    ``build-index`` creates a disposable Sphinx virtual environment with pip.
    Debian/Ubuntu hosts without the matching ``pythonX.Y-venv`` package can run
    the server but fail when that environment needs ``ensurepip``.
    """
    executable = python_executable or sys.executable
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
        return DiagnosticResult(
            passed=True,
            detail=f"{executable} has venv and ensurepip available for build-index",
        )

    detail = (
        f"{executable} cannot import venv/ensurepip; build-index needs them to "
        "create Sphinx venvs. On Debian/Ubuntu, install "
        f"{package_name} for this interpreter."
    )
    output = _combined_output_excerpt(result.stdout, result.stderr)
    if output:
        detail = f"{detail} Output: {output}"

    return DiagnosticResult(passed=False, detail=detail)
