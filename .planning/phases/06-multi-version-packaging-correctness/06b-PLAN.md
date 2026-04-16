---
phase: 6
plan: b
title: "Pyproject.toml verification and wheel content test"
wave: 1
depends_on: []
requirements: [PKG-01, PKG-02, PKG-04]
files_modified:
  - pyproject.toml
  - tests/test_packaging.py
autonomous: true
---

# Plan 06b: Pyproject.toml verification and wheel content test

## Objective

Verify that `pyproject.toml` has the correct entry-point (`PKG-01`), all required runtime deps are pinned (`PKG-02`), and create a wheel content test that asserts `synonyms.yaml` is present in the built wheel (`PKG-04`).

## Tasks

### Task 1: Verify pyproject.toml entry-point and deps

<read_first>
- pyproject.toml
- .planning/REQUIREMENTS.md (PKG-01, PKG-02 sections)
</read_first>

<action>
Verify the following are already present in `pyproject.toml`:

1. **PKG-01 entry-point** (already present):
```toml
[project.scripts]
mcp-server-python-docs = "mcp_server_python_docs.__main__:main"
```

2. **PKG-02 runtime deps** — verify all required pins are present:
   - `mcp>=1.27.0,<2.0.0` (present)
   - `sphobjinv>=2.4,<3.0` (present)
   - `pydantic>=2.0.0,<3.0` (present)
   - `click>=8.1.7,<9.0` (present)
   - `platformdirs>=4.0` (present, need to verify it says `>=4`)
   - `pyyaml>=6.0,<7.0` (present)
   - `markdownify>=0.14,<2.0` (present — the markdown converter)
   - `beautifulsoup4>=4.12,<5.0` (present — required by markdownify)

All dependencies are already correctly pinned. No changes needed to pyproject.toml unless any pin is missing.

If `platformdirs>=4.0` is present but should be `platformdirs>=4`, that is fine — `>=4.0` and `>=4` are equivalent in PEP 440.
</action>

<acceptance_criteria>
- `grep 'mcp-server-python-docs = "mcp_server_python_docs.__main__:main"' pyproject.toml` returns a match
- `grep 'mcp>=1.27.0,<2.0.0' pyproject.toml` returns a match
- `grep 'sphobjinv>=2.4,<3.0' pyproject.toml` returns a match
- `grep 'pydantic>=2' pyproject.toml` returns a match
- `grep 'click>=8' pyproject.toml` returns a match
- `grep 'platformdirs>=4' pyproject.toml` returns a match
- `grep 'pyyaml>=6' pyproject.toml` returns a match
- `grep 'markdownify' pyproject.toml` returns a match
</acceptance_criteria>

### Task 2: Create wheel content test for synonyms.yaml

<read_first>
- pyproject.toml
- src/mcp_server_python_docs/data/synonyms.yaml
- tests/conftest.py
</read_first>

<action>
Create `tests/test_packaging.py` with the following tests:

```python
"""Tests for packaging correctness.

Covers PKG-01 (entry-point), PKG-02 (deps), PKG-04 (synonyms.yaml in wheel),
and PKG-06 (--version flag).
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
        """Wheel must contain mcp_server_python_docs/data/synonyms.yaml."""
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
        required_deps = [
            "mcp>=1.27.0",
            "sphobjinv>=2.4",
            "pydantic>=2",
            "click>=8",
            "platformdirs>=4",
            "pyyaml>=6",
        ]
        for dep in required_deps:
            # Check that the dep name and lower bound are present
            dep_name = dep.split(">=")[0]
            assert dep_name in pyproject, f"Missing dependency: {dep_name}"


class TestVersionFlag:
    """PKG-06: --version flag prints version."""

    def test_version_flag(self):
        """mcp-server-python-docs --version prints 0.1.0."""
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
```

The key test is `test_synonyms_yaml_in_wheel` which builds the wheel using `uv build` and asserts the file is present via `zipfile`. This is the CI wheel-content check from PKG-04.
</action>

<acceptance_criteria>
- `test -f tests/test_packaging.py` exits 0
- `python -m pytest tests/test_packaging.py::TestWheelContent::test_synonyms_yaml_in_wheel -v` passes
- `python -m pytest tests/test_packaging.py::TestVersionFlag -v` passes (after Plan 06a Task 3)
- `grep -c "def test_" tests/test_packaging.py` returns at least 5
- `grep "PKG-04" tests/test_packaging.py` returns a match
</acceptance_criteria>

## Verification

```bash
# Build the wheel and verify synonyms.yaml is in it
uv build --wheel
unzip -l dist/*.whl | grep synonyms.yaml

# Run packaging tests
python -m pytest tests/test_packaging.py -v
```

## Must-Haves (goal-backward)

- [ ] `uv build` produces a wheel with `mcp_server_python_docs/data/synonyms.yaml` inside
- [ ] Wheel content test fails the build if synonyms.yaml is missing
- [ ] Entry-point is `mcp_server_python_docs.__main__:main`
- [ ] All required runtime deps are declared in pyproject.toml
