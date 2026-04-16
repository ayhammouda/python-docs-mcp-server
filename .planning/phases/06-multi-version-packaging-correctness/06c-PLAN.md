---
phase: 6
plan: c
title: "Integration tests for multi-version and installability"
wave: 2
depends_on: [06a, 06b]
requirements: [PKG-03, MVER-01, MVER-05]
files_modified:
  - tests/test_multi_version.py
  - tests/test_packaging.py
autonomous: true
---

# Plan 06c: Integration tests for multi-version and installability

## Objective

Add integration-level tests that verify: (1) the `build-index` CLI with `--versions 3.12,3.13` argument parsing works correctly end-to-end, (2) cross-version URI collision safety at the schema level, (3) the full `--version` flag works as a subprocess invocation (PKG-03 installability proof).

## Tasks

### Task 1: Add build-index CLI argument parsing test

<read_first>
- src/mcp_server_python_docs/__main__.py
- tests/test_multi_version.py
</read_first>

<action>
Add integration tests to `tests/test_multi_version.py` that verify the CLI argument parsing for `--versions 3.12,3.13`:

```python
class TestBuildIndexCLIVersionParsing:
    """MVER-01: --versions flag parses comma-separated versions."""

    def test_version_parsing(self):
        """Verify version_list correctly splits comma-separated versions."""
        versions = "3.12,3.13"
        version_list = [v.strip() for v in versions.split(",") if v.strip()]
        assert version_list == ["3.12", "3.13"]

    def test_version_parsing_with_spaces(self):
        """Verify version_list handles spaces around commas."""
        versions = "3.12 , 3.13"
        version_list = [v.strip() for v in versions.split(",") if v.strip()]
        assert version_list == ["3.12", "3.13"]

    def test_default_version_selection(self):
        """MVER-02: Highest version is selected as default."""
        version_list = ["3.12", "3.13"]
        sorted_versions = sorted(
            version_list, key=lambda v: [int(x) for x in v.split(".")]
        )
        assert sorted_versions[-1] == "3.13"

    def test_default_version_selection_reversed(self):
        """Default version is 3.13 even if 3.13 is listed first."""
        version_list = ["3.13", "3.12"]
        sorted_versions = sorted(
            version_list, key=lambda v: [int(x) for x in v.split(".")]
        )
        assert sorted_versions[-1] == "3.13"
```
</action>

<acceptance_criteria>
- `python -m pytest tests/test_multi_version.py::TestBuildIndexCLIVersionParsing -v` passes all tests
- `grep -c "TestBuildIndexCLIVersionParsing" tests/test_multi_version.py` returns 1
</acceptance_criteria>

### Task 2: Add cross-version schema constraint stress tests

<read_first>
- tests/test_multi_version.py
- src/mcp_server_python_docs/storage/schema.sql
</read_first>

<action>
Add more thorough cross-version constraint tests to `tests/test_multi_version.py`:

```python
class TestCrossVersionSchemaConstraints:
    """MVER-05: Deep verification that cross-version data coexists safely."""

    def test_same_symbol_both_versions(self, multi_version_db):
        """Same qualified_name in two versions does not violate UNIQUE(doc_set_id, qualified_name, symbol_type)."""
        rows = multi_version_db.execute(
            "SELECT s.qualified_name, ds.version FROM symbols s "
            "JOIN doc_sets ds ON s.doc_set_id = ds.id "
            "WHERE s.qualified_name = 'asyncio.TaskGroup' "
            "ORDER BY ds.version"
        ).fetchall()
        assert len(rows) == 2
        assert rows[0]["version"] == "3.12"
        assert rows[1]["version"] == "3.13"

    def test_same_section_anchor_both_versions(self, multi_version_db):
        """Same anchor in two versions does not violate UNIQUE(document_id, anchor)
        because document_id differs across doc_sets."""
        rows = multi_version_db.execute(
            "SELECT sec.anchor, ds.version FROM sections sec "
            "JOIN documents doc ON sec.document_id = doc.id "
            "JOIN doc_sets ds ON doc.doc_set_id = ds.id "
            "WHERE sec.anchor = 'asyncio.TaskGroup' "
            "ORDER BY ds.version"
        ).fetchall()
        assert len(rows) == 2

    def test_fts_returns_results_for_both_versions(self, multi_version_db):
        """FTS5 indexes cover both versions."""
        rows = multi_version_db.execute(
            "SELECT qualified_name FROM symbols_fts WHERE symbols_fts MATCH 'asyncio'"
        ).fetchall()
        # Should have entries from both versions
        assert len(rows) >= 2

    def test_insert_third_version_no_conflict(self, multi_version_db):
        """Adding a third version (hypothetical 3.14) works without conflicts."""
        multi_version_db.execute(
            "INSERT INTO doc_sets (source, version, language, label, is_default, base_url) "
            "VALUES ('python-docs', '3.14', 'en', 'Python 3.14', 0, "
            "'https://docs.python.org/3.14/')"
        )
        ds_row = multi_version_db.execute(
            "SELECT id FROM doc_sets WHERE version = '3.14'"
        ).fetchone()
        ds_id = ds_row[0]

        # Same slug
        multi_version_db.execute(
            "INSERT INTO documents (doc_set_id, slug, title, char_count) "
            "VALUES (?, 'library/asyncio-task.html', 'asyncio.Task', 5000)",
            (ds_id,),
        )
        doc_row = multi_version_db.execute(
            "SELECT id FROM documents WHERE doc_set_id = ?", (ds_id,)
        ).fetchone()

        multi_version_db.execute(
            "INSERT INTO symbols (doc_set_id, qualified_name, normalized_name, "
            "module, symbol_type, uri, anchor) "
            "VALUES (?, 'asyncio.TaskGroup', 'asyncio.taskgroup', 'asyncio', 'class', "
            "'library/asyncio-task.html#asyncio.TaskGroup', 'asyncio.TaskGroup')",
            (ds_id,),
        )
        multi_version_db.commit()

        # Now 3 rows for this symbol
        rows = multi_version_db.execute(
            "SELECT COUNT(*) FROM symbols WHERE qualified_name = 'asyncio.TaskGroup'"
        ).fetchone()
        assert rows[0] == 3
```
</action>

<acceptance_criteria>
- `python -m pytest tests/test_multi_version.py::TestCrossVersionSchemaConstraints -v` passes all tests
- `grep -c "def test_" tests/test_multi_version.py` returns at least 14
</acceptance_criteria>

### Task 3: Add subprocess --version test to test_packaging.py

<read_first>
- tests/test_packaging.py
- src/mcp_server_python_docs/__main__.py
</read_first>

<action>
Verify that `tests/test_packaging.py` already contains the `TestVersionFlag` class from Plan 06b. If so, add an additional test to verify the exit code:

```python
class TestVersionFlag:
    """PKG-06: --version flag prints version."""

    def test_version_flag_output(self):
        """--version prints 0.1.0 to stderr."""
        result = subprocess.run(
            [sys.executable, "-m", "mcp_server_python_docs", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        combined = result.stdout + result.stderr
        assert "0.1.0" in combined

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
            [sys.executable, "-c", "from mcp_server_python_docs.__main__ import main; print('OK')"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert "OK" in result.stdout
        assert result.returncode == 0
```
</action>

<acceptance_criteria>
- `python -m pytest tests/test_packaging.py -v` passes all tests
- `grep -c "def test_" tests/test_packaging.py` returns at least 7
- `grep "PKG-03" tests/test_packaging.py` returns a match
</acceptance_criteria>

## Verification

```bash
# Run all Phase 6 tests
python -m pytest tests/test_multi_version.py tests/test_packaging.py -v

# Run existing tests to ensure no regressions
python -m pytest tests/ -v
```

## Must-Haves (goal-backward)

- [ ] Cross-version URI collision safety verified at schema level
- [ ] `--version` flag exits cleanly with version string
- [ ] Entry-point module is importable and runnable
- [ ] All Phase 6 tests pass alongside existing test suite
