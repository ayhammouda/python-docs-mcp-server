"""Unit tests for environment Python-version detection (detection.py).

Closes the long-standing gap: ``detect_python_version`` backs 1 of the 6
public MCP tools but had no dedicated coverage. See ``.github/TEST-STRATEGY.md``
section 5/6.

Detection is a *fallback chain*:
    1. ``.python-version`` file in cwd
    2. ``python3 --version`` on PATH
    3. ``sys.version_info`` (server runtime)

To test any branch in isolation we must neutralize the branches *above* it:
escape the dev machine's real ``.python-version`` with ``monkeypatch.chdir``,
and control the ``python3`` probe by patching ``subprocess.run``.
"""
from __future__ import annotations

import subprocess

import pytest

from mcp_server_python_docs import detection
from mcp_server_python_docs.detection import (
    _parse_major_minor,
    detect_python_version,
    match_to_indexed,
)

# ── _parse_major_minor: pure regex extraction ──────────────────────

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("3.13.2", "3.13"),
        ("Python 3.13.2", "3.13"),
        ("cpython-3.13", "3.13"),
        ("3.9", "3.9"),
        ("no digits here", None),
        ("", None),
    ],
)
def test_parse_major_minor(raw: str, expected: str | None) -> None:
    assert _parse_major_minor(raw) == expected


# ── match_to_indexed: only return exact, indexed matches ───────────

def test_match_to_indexed_returns_exact_match() -> None:
    assert match_to_indexed("3.13", ["3.12", "3.13"]) == "3.13"


def test_match_to_indexed_returns_none_when_absent() -> None:
    assert match_to_indexed("3.9", ["3.12", "3.13"]) is None


# ── detect_python_version: the fallback chain ──────────────────────

def test_detects_from_python_version_file(tmp_path, monkeypatch) -> None:
    """Branch 1: a .python-version file in cwd wins over everything else."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".python-version").write_text("3.11.4\n")

    version, source = detect_python_version()

    assert version == "3.11"
    assert source == ".python-version file"


def test_malformed_version_file_falls_through(tmp_path, monkeypatch) -> None:
    """Branch 1 with no parseable version must NOT crash — it falls through.

    We stub the PATH probe so the assertion is deterministic regardless of
    what ``python3`` the host actually has.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".python-version").write_text("not-a-version\n")

    def fake_run(*args, **_kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="Python 3.12.1\n", stderr="")

    monkeypatch.setattr(detection.subprocess, "run", fake_run)

    version, source = detect_python_version()

    assert version == "3.12"
    assert source == "python3 in PATH"


# TODO(you): implement the remaining two branches of the fallback chain.
#
# These are the cases where the test *design* matters most — you have to
# neutralize the branches above the one under test. Both start from an empty
# cwd so no real .python-version interferes:
#
#     monkeypatch.chdir(tmp_path)   # escape any real .python-version
#
# 1) test_detects_from_path_probe:
#       - Patch detection.subprocess.run to return a CompletedProcess with
#         returncode=0 and stdout="Python 3.10.9\n".
#       - Assert (version, source) == ("3.10", "python3 in PATH").
#
# 2) test_falls_back_to_runtime_when_no_python3:
#       - Make the PATH probe fail: patch detection.subprocess.run to raise
#         FileNotFoundError (python3 absent). Decide what the function should
#         return — it falls back to the server's own interpreter. Assert the
#         source is "server runtime" and the version matches
#         f"{sys.version_info.major}.{sys.version_info.minor}".
#
# Why this is the meaningful part: detection is order-dependent, so a test that
# forgets to chdir() or forgets to stub subprocess will pass on your machine
# and fail in CI (or vice-versa). The isolation is the assertion.
