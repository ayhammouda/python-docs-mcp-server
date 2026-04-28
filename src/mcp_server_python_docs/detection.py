"""Python version detection from the user's environment.

Probes multiple sources to determine which Python version the user
is working with, so the server can default to the right documentation.

Detection order:
1. .python-version file in cwd (pyenv, mise, rtx convention)
2. ``python3 --version`` in PATH (user's active interpreter)
3. ``sys.version_info`` (server's own runtime — last resort)
"""
from __future__ import annotations

import logging
import re
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_VERSION_RE = re.compile(r"(\d+\.\d+)")


def _parse_major_minor(raw: str) -> str | None:
    """Extract 'X.Y' from a version string like '3.13.2', 'Python 3.13.2', 'cpython-3.13'."""
    m = _VERSION_RE.search(raw)
    return m.group(1) if m else None


def detect_python_version() -> tuple[str, str]:
    """Detect the user's Python version.

    Returns:
        Tuple of (major_minor, source) where major_minor is like '3.13'
        and source describes how it was detected.
    """
    # 1. .python-version file (pyenv / mise / rtx)
    pv_file = Path.cwd() / ".python-version"
    if pv_file.is_file():
        try:
            first_line = pv_file.read_text().strip().splitlines()[0].strip()
            version = _parse_major_minor(first_line)
            if version:
                logger.info("Detected Python %s from .python-version", version)
                return version, ".python-version file"
        except Exception:
            pass

    # 2. python3 --version in PATH
    try:
        result = subprocess.run(
            ["python3", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            version = _parse_major_minor(result.stdout.strip())
            if version:
                logger.info("Detected Python %s from python3 in PATH", version)
                return version, "python3 in PATH"
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    # 3. Server's own runtime
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    logger.info("Using server runtime Python %s as fallback", version)
    return version, "server runtime"


def match_to_indexed(
    detected: str, indexed_versions: list[str]
) -> str | None:
    """Match a detected version to the closest indexed version.

    Returns the detected version if it's in the index, otherwise None.
    We don't guess -- if a detected version is not indexed, return None and
    let the normal default resolution handle it.
    """
    if detected in indexed_versions:
        return detected
    return None
