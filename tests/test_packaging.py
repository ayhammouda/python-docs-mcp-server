"""Tests for packaging correctness.

Covers PKG-01 (entry-point), PKG-02 (deps), PKG-03 (installability),
PKG-04 (synonyms.yaml in wheel), and PKG-06 (--version flag).
"""
from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent


class TestWheelContent:
    """PKG-04: Built wheel contains synonyms.yaml."""

    @pytest.fixture(scope="class")
    def built_wheel(self, tmp_path_factory) -> Path:
        """Build the wheel using uv build and return its path."""
        dist_dir = tmp_path_factory.mktemp("dist")
        result = subprocess.run(
            ["uv", "build", "--wheel", "--out-dir", str(dist_dir)],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
        )
        assert result.returncode == 0, f"uv build failed: {result.stderr}"
        wheels = list(dist_dir.glob("*.whl"))
        assert len(wheels) == 1, f"Expected 1 wheel, found {len(wheels)}: {wheels}"
        return wheels[0]

    def test_synonyms_yaml_in_wheel(self, built_wheel):
        """PKG-04: Wheel must contain mcp_server_python_docs/data/synonyms.yaml."""
        with zipfile.ZipFile(built_wheel) as zf:
            names = zf.namelist()
        synonym_paths = [n for n in names if n.endswith("data/synonyms.yaml")]
        assert len(synonym_paths) == 1, (
            f"synonyms.yaml not found in wheel. Wheel contents:\n"
            + "\n".join(sorted(names))
        )
        assert "mcp_server_python_docs/data/synonyms.yaml" in synonym_paths[0]

    def test_schema_sql_in_wheel(self, built_wheel):
        """Wheel must contain storage/schema.sql."""
        with zipfile.ZipFile(built_wheel) as zf:
            names = zf.namelist()
        schema_paths = [n for n in names if n.endswith("storage/schema.sql")]
        assert len(schema_paths) >= 1, (
            f"schema.sql not found in wheel. Wheel contents:\n"
            + "\n".join(sorted(names))
        )

    def test_wheel_has_entry_point(self, built_wheel):
        """PKG-01: Wheel metadata declares the entry-point."""
        with zipfile.ZipFile(built_wheel) as zf:
            # Entry points are in the .dist-info/entry_points.txt
            entry_point_files = [
                n for n in zf.namelist() if n.endswith("entry_points.txt")
            ]
            assert len(entry_point_files) == 1
            content = zf.read(entry_point_files[0]).decode()
        assert "mcp-server-python-docs" in content
        assert "mcp_server_python_docs.__main__:main" in content


class TestPyprojectDeps:
    """PKG-02: Runtime deps are pinned correctly."""

    def test_required_deps_present(self):
        """All required runtime deps exist in pyproject.toml."""
        pyproject = (PROJECT_ROOT / "pyproject.toml").read_text()
        required_dep_names = [
            "mcp",
            "sphobjinv",
            "pydantic",
            "click",
            "platformdirs",
            "pyyaml",
            "markdownify",
        ]
        for dep_name in required_dep_names:
            assert dep_name in pyproject, f"Missing dependency: {dep_name}"


class TestVersionFlag:
    """PKG-06: --version flag prints version."""

    def test_version_flag_output(self):
        """--version prints 0.1.0."""
        result = subprocess.run(
            [sys.executable, "-m", "mcp_server_python_docs", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # Version output goes to stderr due to stdio hygiene
        combined = result.stdout + result.stderr
        assert "0.1.0" in combined, (
            f"Expected '0.1.0' in output.\n"
            f"stdout: {result.stdout!r}\n"
            f"stderr: {result.stderr!r}"
        )

    def test_version_flag_exits_zero(self):
        """--version exits with code 0."""
        result = subprocess.run(
            [sys.executable, "-m", "mcp_server_python_docs", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0


class TestInstallability:
    """PKG-03: Package is installable and entry-point works."""

    def test_module_runnable(self):
        """python -m mcp_server_python_docs --version succeeds."""
        result = subprocess.run(
            [sys.executable, "-m", "mcp_server_python_docs", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        combined = result.stdout + result.stderr
        assert "0.1.0" in combined

    def test_entry_point_module_exists(self):
        """The entry-point module is importable."""
        result = subprocess.run(
            [
                sys.executable, "-c",
                "from mcp_server_python_docs.__main__ import main; print('OK')",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # The import itself triggers stdio hygiene (os.dup2), so OK may go to stderr
        combined = result.stdout + result.stderr
        assert "OK" in combined
        assert result.returncode == 0
