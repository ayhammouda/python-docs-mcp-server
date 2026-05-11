"""Tests for packaging correctness.

Covers PKG-01 (entry-point), PKG-02 (deps), PKG-03 (installability),
PKG-04 (synonyms.yaml in wheel), and PKG-06 (--version flag).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from importlib.metadata import version
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
DIST_NAME = "python-docs-mcp-server"
LEGACY_CLI_NAME = "mcp-server-python-docs"


def _uv_command() -> list[str]:
    """Return a runnable uv command on platforms where Scripts may not be on PATH."""
    uv_executable = shutil.which("uv")
    if uv_executable is not None:
        return [uv_executable]
    base_executable = getattr(sys, "_base_executable", sys.executable)
    return [base_executable, "-m", "uv"]


class TestWheelContent:
    """PKG-04: Built wheel contains synonyms.yaml."""

    @pytest.fixture(scope="class")
    def built_wheel(self, tmp_path_factory) -> Path:
        """Build the wheel using uv build and return its path."""
        dist_dir = tmp_path_factory.mktemp("dist")
        result = subprocess.run(
            _uv_command() + ["build", "--wheel", "--out-dir", str(dist_dir)],
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
            "synonyms.yaml not found in wheel. Wheel contents:\n"
            + "\n".join(sorted(names))
        )
        assert "mcp_server_python_docs/data/synonyms.yaml" in synonym_paths[0]

    def test_schema_sql_in_wheel(self, built_wheel):
        """Wheel must contain storage/schema.sql."""
        with zipfile.ZipFile(built_wheel) as zf:
            names = zf.namelist()
        schema_paths = [n for n in names if n.endswith("storage/schema.sql")]
        assert len(schema_paths) >= 1, (
            "schema.sql not found in wheel. Wheel contents:\n"
            + "\n".join(sorted(names))
        )

    def test_wheel_metadata_uses_public_distribution_name(self, built_wheel):
        """Wheel metadata must publish under the repo-aligned distribution name."""
        with zipfile.ZipFile(built_wheel) as zf:
            metadata_files = [n for n in zf.namelist() if n.endswith("METADATA")]
            assert len(metadata_files) == 1
            content = zf.read(metadata_files[0]).decode()
        assert f"Name: {DIST_NAME}" in content

    def test_wheel_has_entry_point(self, built_wheel):
        """PKG-01: Wheel metadata declares both console scripts, structurally."""
        import configparser
        import io

        with zipfile.ZipFile(built_wheel) as zf:
            entry_point_files = [
                n for n in zf.namelist() if n.endswith("entry_points.txt")
            ]
            assert len(entry_point_files) == 1
            content = zf.read(entry_point_files[0]).decode()

        parser = configparser.ConfigParser()
        parser.read_file(io.StringIO(content))
        assert parser.has_section("console_scripts"), (
            f"entry_points.txt missing [console_scripts]:\n{content}"
        )
        scripts = dict(parser.items("console_scripts"))
        target = "mcp_server_python_docs.__main__:main"
        assert scripts.get(DIST_NAME) == target, (
            f"Expected {DIST_NAME} -> {target}, got {scripts!r}"
        )
        assert scripts.get(LEGACY_CLI_NAME) == target, (
            f"Expected {LEGACY_CLI_NAME} -> {target}, got {scripts!r}"
        )


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
            "beautifulsoup4",
        ]
        for dep_name in required_dep_names:
            assert dep_name in pyproject, f"Missing dependency: {dep_name}"

    def test_classifiers_advertise_supported_python_versions(self):
        """Classifiers must list every Python runtime the project supports."""
        pyproject = (PROJECT_ROOT / "pyproject.toml").read_text()
        for minor in (12, 13, 14):
            classifier = f"Programming Language :: Python :: 3.{minor}"
            assert classifier in pyproject, f"Missing classifier: {classifier}"


class TestVersionFlag:
    """PKG-06: --version flag prints version."""

    def test_module_version_matches_package_metadata(self):
        """__version__ stays in sync with installed package metadata."""
        import mcp_server_python_docs

        assert mcp_server_python_docs.__version__ == version(DIST_NAME)

    def test_source_tree_import_without_installed_metadata(self, tmp_path: Path):
        """Source-tree import falls back to pyproject.toml when metadata is absent.

        Forces the fallback path by monkey-patching importlib.metadata.version
        to raise PackageNotFoundError, since `-S` alone does not reliably
        suppress editable-install dist-info discovery.
        """
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
        prelude = (
            "import importlib.metadata as m\n"
            "def _raise(_):\n"
            "    raise m.PackageNotFoundError\n"
            "m.version = _raise\n"
        )
        result = subprocess.run(
            [
                sys.executable,
                "-S",
                "-c",
                prelude + "import mcp_server_python_docs; print(mcp_server_python_docs.__version__)",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=str(tmp_path),
        )
        assert result.returncode == 0, (
            f"Source-tree import failed.\n"
            f"stdout: {result.stdout!r}\n"
            f"stderr: {result.stderr!r}"
        )
        # The fallback reads pyproject.toml directly; assert it equals
        # whatever pyproject currently declares (decoupled from installed metadata).
        import tomllib
        pyproject_version = tomllib.loads(
            (PROJECT_ROOT / "pyproject.toml").read_text()
        )["project"]["version"]
        assert result.stdout.strip() == pyproject_version

    def test_version_flag_output(self):
        """--version prints the installed package metadata version."""
        result = subprocess.run(
            [sys.executable, "-m", "mcp_server_python_docs", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # Version output goes to stderr due to stdio hygiene
        combined = result.stdout + result.stderr
        expected = version(DIST_NAME)
        assert expected in combined, (
            f"Expected {expected!r} in output.\n"
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
        assert version(DIST_NAME) in combined

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
