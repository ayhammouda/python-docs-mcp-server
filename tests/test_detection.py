"""Tests for Python version detection (detection.py).

Covers M-2 (anchored version regex) and M-3 (bounded .python-version read).
"""
from __future__ import annotations

import pytest

from mcp_server_python_docs.detection import (
    _parse_major_minor,
    detect_python_version,
    match_to_indexed,
)


class TestParseMajorMinor:
    """M-2: _VERSION_RE uses non-digit lookaround on both sides."""

    def test_plain_version(self):
        assert _parse_major_minor("3.13") == "3.13"

    def test_embedded_in_python_prefix(self):
        assert _parse_major_minor("Python 3.13.2") == "3.13"

    def test_cpython_dash_prefix(self):
        assert _parse_major_minor("cpython-3.13") == "3.13"

    def test_version_with_trailing_newline(self):
        assert _parse_major_minor("3.13\n") == "3.13"

    def test_no_over_match_inside_longer_digit_runs(self):
        """M-2: '3.133' must NOT be parsed as '3.13' (trailing-digit boundary).

        Without the (?!\\d) lookahead the regex would return '3.13' inside
        '3.133'. With the boundary the match fails entirely because the
        greedy \\d+ absorbs all trailing digits and the resulting '3.133'
        still has no digit after it (EOS) -- so this specific string DOES
        match as '3.133'. The real regression this test locks down is that
        '3.13' is NOT extracted from '3.133other' when there is no
        separator:
        """
        # '3.13' embedded between non-digit boundaries is OK
        assert _parse_major_minor("v3.13-rc1") == "3.13"
        # '3.13' should not be extracted from '3.1337' (no boundary)
        # — the greedy match takes '3.1337' which is still a \d+\.\d+ match.
        # That's expected regex behavior; the M-2 fix only prevents
        # (?<!\d) and (?!\d) substring theft across surrounding digits.
        assert _parse_major_minor("3.1337") == "3.1337"

    def test_no_left_boundary_over_match(self):
        """M-2: left boundary rejects '...N.M' being parsed starting mid-digit.

        E.g. in '13.2', the regex must match '13.2', not '3.2' starting at
        position 1. Without (?<!\\d) the engine could yield '3.2'.
        """
        # The whole '13.2' is the only valid match; regex must not grab '3.2'.
        assert _parse_major_minor("13.2") == "13.2"

    def test_multi_digit_major_still_parses(self):
        """Major versions can be multi-digit; reject only trailing-digit substring matches."""
        assert _parse_major_minor("11.2") == "11.2"

    def test_three_part_extracts_first_two(self):
        """M-2: '11.2.3' extracts '11.2' (first major.minor, '.3' is the boundary)."""
        assert _parse_major_minor("11.2.3") == "11.2"

    def test_no_version_in_string(self):
        assert _parse_major_minor("nope") is None

    def test_empty_string(self):
        assert _parse_major_minor("") is None


class TestMatchToIndexed:
    def test_detected_version_in_index(self):
        assert match_to_indexed("3.13", ["3.12", "3.13"]) == "3.13"

    def test_detected_version_not_in_index(self):
        assert match_to_indexed("3.11", ["3.12", "3.13"]) is None


class TestDetectPythonVersionPythonVersionFile:
    """M-3: .python-version file is read with a bounded byte limit."""

    def test_plain_python_version_file(self, tmp_path, monkeypatch):
        """A .python-version file with '3.13\\n' resolves to 3.13."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".python-version").write_text("3.13\n")
        version, source = detect_python_version()
        assert version == "3.13"
        assert source == ".python-version file"

    def test_huge_python_version_file_is_bounded(self, tmp_path, monkeypatch):
        """M-3: a 2MB .python-version does NOT hang, does NOT raise, falls through.

        If the first 1024 bytes contain a parseable version, the bounded read
        may still produce a valid version. We construct a hostile garbage file
        with no parseable version in the leading 1024 bytes to force fallthrough.
        """
        monkeypatch.chdir(tmp_path)
        # 2MB of garbage with no parseable version pattern in the first 1024 bytes.
        garbage = b"A" * (2 * 1024 * 1024)
        (tmp_path / ".python-version").write_bytes(garbage)
        # Must not hang, must not raise.
        version, source = detect_python_version()
        # Source must NOT be the .python-version file path (it fell through).
        assert source != ".python-version file"
        # Any non-empty version string is fine (the fallback chain ran).
        assert version

    def test_empty_python_version_file_falls_through(self, tmp_path, monkeypatch):
        """M-3: an empty .python-version file falls through to the next detection step."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".python-version").write_text("")
        version, source = detect_python_version()
        assert source != ".python-version file"
        assert version

    def test_whitespace_only_python_version_file_falls_through(self, tmp_path, monkeypatch):
        """M-3: whitespace-only .python-version falls through."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".python-version").write_text("   \n\n")
        version, source = detect_python_version()
        assert source != ".python-version file"
        assert version


class TestDetectPythonVersionFallbackOrder:
    """End-to-end fallback: no .python-version -> python3 in PATH or server runtime."""

    def test_no_python_version_file(self, tmp_path, monkeypatch):
        """Without a .python-version file, detection uses python3 in PATH or runtime."""
        monkeypatch.chdir(tmp_path)
        # Ensure no .python-version exists
        pv = tmp_path / ".python-version"
        if pv.exists():
            pv.unlink()
        version, source = detect_python_version()
        # Source must be one of the other two paths (we can't control which).
        assert source in ("python3 in PATH", "server runtime")
        assert version


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
