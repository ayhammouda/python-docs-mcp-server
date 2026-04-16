---
phase: 2
plan_id: 02-C
title: "Schema tests — FTS5 tokenizer regression, composite uniqueness, cross-version URI, idempotent bootstrap, platformdirs audit"
wave: 3
depends_on:
  - 02-A
  - 02-B
files_modified:
  - tests/test_schema.py
requirements:
  - STOR-01
  - STOR-02
  - STOR-03
  - STOR-04
  - STOR-05
  - STOR-09
autonomous: true
---

<objective>
Create `tests/test_schema.py` with all 5 test fixtures from the Phase 2 success criteria: FTS5 tokenizer regression (no Porter stemming, correct tokenchars), composite symbol uniqueness, cross-version URI collision safety, idempotent bootstrap, and a platformdirs grep audit.
</objective>

<tasks>

<task id="1">
<title>Create FTS5 tokenizer regression test</title>
<read_first>
- src/mcp_server_python_docs/storage/schema.sql (the schema under test)
- src/mcp_server_python_docs/storage/db.py (bootstrap_schema function)
- .planning/ROADMAP.md (Phase 2 success criterion 1)
</read_first>
<action>
Create `tests/test_schema.py` with a test class or function group for FTS5 tokenizer regression.

The test `test_fts5_tokenizer_preserves_identifiers` must:

1. Create an in-memory SQLite connection: `conn = sqlite3.connect(":memory:")`
2. Set pragmas: `conn.execute("PRAGMA foreign_keys = ON")`
3. Call `bootstrap_schema(conn)` to create all tables
4. Insert a doc_set row:
```python
conn.execute(
    "INSERT INTO doc_sets (source, version, language, label, is_default) "
    "VALUES ('python-docs', '3.13', 'en', 'Python 3.13', 1)"
)
```
5. Insert a document row (needed as FK for sections):
```python
conn.execute(
    "INSERT INTO documents (doc_set_id, uri, slug, title, content_text, char_count) "
    "VALUES (1, 'library/asyncio-task.html', 'library/asyncio-task.html', "
    "'asyncio.Task', 'Task content', 12)"
)
```
6. Insert section rows for FTS testing:
```python
# Insert sections for each test identifier
test_sections = [
    ("library/asyncio-task.html#asyncio.TaskGroup", "asyncio.TaskGroup",
     "asyncio.TaskGroup", 2, 1, "The TaskGroup class manages tasks", 31),
    ("library/json.html#json.dumps", "json.dumps",
     "json.dumps", 2, 1, "Serialize obj to a JSON formatted str", 36),
    ("library/collections.html#collections.OrderedDict", "collections.OrderedDict",
     "collections.OrderedDict", 2, 1, "Dict subclass that remembers insertion order", 43),
]
```
   (Note: a second document row is needed for json.html and collections.html — insert them too)

7. Insert symbol rows for `symbols_fts` testing:
```python
test_symbols = [
    (1, "asyncio.TaskGroup", "asyncio.taskgroup", "asyncio", "class",
     "library/asyncio-task.html#asyncio.TaskGroup", "asyncio.TaskGroup"),
    (1, "json.dumps", "json.dumps", "json", "function",
     "library/json.html#json.dumps", "json.dumps"),
    (1, "collections.OrderedDict", "collections.ordereddict", "collections", "class",
     "library/collections.html#collections.OrderedDict", "collections.OrderedDict"),
]
```

8. Insert example rows for `examples_fts` testing:
```python
conn.execute(
    "INSERT INTO examples (section_id, code, language, is_doctest, ordinal) "
    "VALUES (1, 'async with asyncio.TaskGroup() as tg:\\n    tg.create_task(coro())', "
    "'python', 0, 1)"
)
```

9. Rebuild all FTS indexes:
```python
conn.execute("INSERT INTO sections_fts(sections_fts) VALUES('rebuild')")
conn.execute("INSERT INTO symbols_fts(symbols_fts) VALUES('rebuild')")
conn.execute("INSERT INTO examples_fts(examples_fts) VALUES('rebuild')")
```

10. Assert exact-token search works for each identifier:
```python
# sections_fts: search headings
rows = conn.execute(
    "SELECT heading FROM sections_fts WHERE sections_fts MATCH ?",
    ('"asyncio.TaskGroup"',)
).fetchall()
assert len(rows) >= 1, "asyncio.TaskGroup not found in sections_fts"
assert any("asyncio.TaskGroup" in r[0] for r in rows)

rows = conn.execute(
    "SELECT heading FROM sections_fts WHERE sections_fts MATCH ?",
    ('"json.dumps"',)
).fetchall()
assert len(rows) >= 1, "json.dumps not found in sections_fts"

rows = conn.execute(
    "SELECT heading FROM sections_fts WHERE sections_fts MATCH ?",
    ('"collections.OrderedDict"',)
).fetchall()
assert len(rows) >= 1, "collections.OrderedDict not found in sections_fts"

# symbols_fts: search qualified_name
rows = conn.execute(
    "SELECT qualified_name FROM symbols_fts WHERE symbols_fts MATCH ?",
    ('"asyncio.TaskGroup"',)
).fetchall()
assert len(rows) >= 1, "asyncio.TaskGroup not found in symbols_fts"

# examples_fts: search code
rows = conn.execute(
    "SELECT code FROM examples_fts WHERE examples_fts MATCH ?",
    ('"asyncio.TaskGroup"',)
).fetchall()
assert len(rows) >= 1, "asyncio.TaskGroup not found in examples_fts"
```

11. Assert Porter stemming is NOT applied — `"dump"` should NOT match `"json.dumps"` as a standalone token (Porter would stem `dumps` -> `dump`):
```python
# If Porter stemming were active, searching for "dump" would match "dumps"
# With unicode61 (no porter), "dump" should NOT match "json.dumps" as a token
# (it might match as a substring in content_text, but not as an FTS token match
# for the heading column which contains "json.dumps" as a single token)
rows = conn.execute(
    "SELECT heading FROM sections_fts WHERE heading MATCH ?",
    ('"dump"',)
).fetchall()
assert len(rows) == 0, "Porter stemming appears active — 'dump' matched 'json.dumps'"
```
</action>
<acceptance_criteria>
- `tests/test_schema.py` contains a test function that indexes `asyncio.TaskGroup`, `json.dumps`, and `collections.OrderedDict`
- Test queries `sections_fts`, `symbols_fts`, and `examples_fts` via `MATCH` for each identifier
- Each query returns at least 1 result (exact-token search works)
- Test asserts Porter stemming is NOT active (searching `"dump"` does not match `json.dumps` heading)
- Test uses in-memory SQLite database (`:memory:`)
- Test calls `bootstrap_schema(conn)` to create tables
- `pytest tests/test_schema.py::test_fts5_tokenizer_preserves_identifiers` exits 0
</acceptance_criteria>
</task>

<task id="2">
<title>Create composite symbol uniqueness test</title>
<read_first>
- src/mcp_server_python_docs/storage/schema.sql (symbols table UNIQUE constraint)
- .planning/ROADMAP.md (Phase 2 success criterion 2)
</read_first>
<action>
Add test `test_symbol_composite_uniqueness` to `tests/test_schema.py`:

1. Create in-memory DB, call `bootstrap_schema(conn)`.
2. Insert a doc_set row.
3. Insert `json.dumps` as `function`:
```python
conn.execute(
    "INSERT INTO symbols (doc_set_id, qualified_name, normalized_name, module, "
    "symbol_type, uri, anchor) VALUES (1, 'json.dumps', 'json.dumps', 'json', "
    "'function', 'library/json.html#json.dumps', 'json.dumps')"
)
```
4. Insert `json.dumps` as `method` — MUST succeed:
```python
conn.execute(
    "INSERT INTO symbols (doc_set_id, qualified_name, normalized_name, module, "
    "symbol_type, uri, anchor) VALUES (1, 'json.dumps', 'json.dumps', 'json', "
    "'method', 'library/json.html#json.dumps', 'json.dumps')"
)
```
5. Assert both rows exist:
```python
count = conn.execute(
    "SELECT COUNT(*) FROM symbols WHERE qualified_name = 'json.dumps'"
).fetchone()[0]
assert count == 2, f"Expected 2 rows for json.dumps, got {count}"
```
6. Assert duplicate `(doc_set_id, qualified_name, symbol_type)` triple raises `IntegrityError`:
```python
import sqlite3
with pytest.raises(sqlite3.IntegrityError):
    conn.execute(
        "INSERT INTO symbols (doc_set_id, qualified_name, normalized_name, module, "
        "symbol_type, uri, anchor) VALUES (1, 'json.dumps', 'json.dumps', 'json', "
        "'function', 'library/json.html#json.dumps', 'json.dumps')"
    )
```
</action>
<acceptance_criteria>
- Test inserts `json.dumps` as both `function` and `method` into `symbols` without error
- Test asserts 2 rows exist for `json.dumps` after both inserts
- Test asserts `IntegrityError` on a third insert with the same `(doc_set_id, qualified_name, symbol_type)` triple
- `pytest tests/test_schema.py::test_symbol_composite_uniqueness` exits 0
</acceptance_criteria>
</task>

<task id="3">
<title>Create cross-version URI collision test</title>
<read_first>
- src/mcp_server_python_docs/storage/schema.sql (sections table constraints)
- .planning/ROADMAP.md (Phase 2 success criterion 3)
</read_first>
<action>
Add test `test_cross_version_uri_no_collision` to `tests/test_schema.py`:

1. Create in-memory DB, call `bootstrap_schema(conn)`.
2. Insert two doc_sets — one for 3.12, one for 3.13:
```python
conn.execute(
    "INSERT INTO doc_sets (source, version, language, label, is_default) "
    "VALUES ('python-docs', '3.12', 'en', 'Python 3.12', 0)"
)
conn.execute(
    "INSERT INTO doc_sets (source, version, language, label, is_default) "
    "VALUES ('python-docs', '3.13', 'en', 'Python 3.13', 1)"
)
```
3. Insert documents with the same slug for both versions:
```python
conn.execute(
    "INSERT INTO documents (doc_set_id, uri, slug, title, content_text, char_count) "
    "VALUES (1, 'library/json.html', 'library/json.html', 'json (3.12)', 'content', 7)"
)
conn.execute(
    "INSERT INTO documents (doc_set_id, uri, slug, title, content_text, char_count) "
    "VALUES (2, 'library/json.html', 'library/json.html', 'json (3.13)', 'content', 7)"
)
```
4. Insert sections with the SAME URI for both documents — this MUST succeed:
```python
conn.execute(
    "INSERT INTO sections (document_id, uri, anchor, heading, level, ordinal, "
    "content_text, char_count) VALUES (1, 'library/json.html#json.dumps', "
    "'json.dumps', 'json.dumps', 2, 1, 'Serialize obj', 13)"
)
conn.execute(
    "INSERT INTO sections (document_id, uri, anchor, heading, level, ordinal, "
    "content_text, char_count) VALUES (2, 'library/json.html#json.dumps', "
    "'json.dumps', 'json.dumps', 2, 1, 'Serialize obj', 13)"
)
```
5. Assert both sections exist:
```python
count = conn.execute(
    "SELECT COUNT(*) FROM sections WHERE uri = 'library/json.html#json.dumps'"
).fetchone()[0]
assert count == 2, f"Expected 2 sections with same URI, got {count}"
```
6. Assert `UNIQUE(document_id, anchor)` still enforced — same document + same anchor fails:
```python
with pytest.raises(sqlite3.IntegrityError):
    conn.execute(
        "INSERT INTO sections (document_id, uri, anchor, heading, level, ordinal, "
        "content_text, char_count) VALUES (1, 'library/json.html#json.dumps-dupe', "
        "'json.dumps', 'json.dumps duplicate', 2, 2, 'Duplicate', 9)"
    )
```
</action>
<acceptance_criteria>
- Test inserts sections with identical URI strings for different doc versions without error
- Test asserts 2 sections with the same URI exist
- Test asserts `IntegrityError` when same `(document_id, anchor)` pair is inserted twice
- `pytest tests/test_schema.py::test_cross_version_uri_no_collision` exits 0
</acceptance_criteria>
</task>

<task id="4">
<title>Create idempotent bootstrap test</title>
<read_first>
- src/mcp_server_python_docs/storage/db.py (bootstrap_schema function)
- .planning/ROADMAP.md (Phase 2 success criterion 4)
</read_first>
<action>
Add test `test_bootstrap_idempotent` to `tests/test_schema.py`:

1. Create in-memory DB.
2. Call `bootstrap_schema(conn)` first time — no error.
3. Insert a doc_set row to prove the schema works:
```python
conn.execute(
    "INSERT INTO doc_sets (source, version, language, label, is_default) "
    "VALUES ('python-docs', '3.13', 'en', 'Python 3.13', 1)"
)
```
4. Call `bootstrap_schema(conn)` SECOND time — no error (this is the idempotency test).
5. Assert the doc_set row still exists after second bootstrap:
```python
row = conn.execute("SELECT language FROM doc_sets WHERE version = '3.13'").fetchone()
assert row is not None, "doc_set row lost after second bootstrap"
assert row[0] == "en", f"Expected language='en', got '{row[0]}'"
```
6. Assert `doc_sets.language` defaults to `'en'` by inserting a row without specifying language:
```python
conn.execute(
    "INSERT INTO doc_sets (source, version, label, is_default) "
    "VALUES ('python-docs', '3.12', 'Python 3.12', 0)"
)
row = conn.execute("SELECT language FROM doc_sets WHERE version = '3.12'").fetchone()
assert row[0] == "en", f"Expected default language='en', got '{row[0]}'"
```
</action>
<acceptance_criteria>
- Test calls `bootstrap_schema(conn)` twice on the same connection without error
- Test inserts data between the two bootstrap calls and asserts data survives
- Test verifies `doc_sets.language` defaults to `'en'`
- `pytest tests/test_schema.py::test_bootstrap_idempotent` exits 0
</acceptance_criteria>
</task>

<task id="5">
<title>Create platformdirs grep audit test</title>
<read_first>
- src/mcp_server_python_docs/storage/db.py (get_cache_dir uses platformdirs)
- src/mcp_server_python_docs/server.py (uses platformdirs)
- src/mcp_server_python_docs/__main__.py (uses platformdirs)
</read_first>
<action>
Add test `test_no_hardcoded_cache_path` to `tests/test_schema.py`:

This is a grep audit test that asserts no source file contains hardcoded `~/.cache/` paths. All cache path resolution must go through `platformdirs.user_cache_dir()`.

```python
import subprocess

def test_no_hardcoded_cache_path():
    """Assert no hardcoded ~/.cache/ paths in source tree (STOR-10 / success criterion 5).

    All cache directory paths must be resolved via platformdirs.user_cache_dir().
    """
    result = subprocess.run(
        ["rg", "--count", r"~/.cache", "src/"],
        capture_output=True, text=True, cwd=Path(__file__).parent.parent
    )
    # rg returns exit code 1 when no matches found (good), 0 when matches found (bad)
    if result.returncode == 0:
        pytest.fail(
            f"Found hardcoded ~/.cache/ references in source tree:\n{result.stdout}\n"
            "All cache paths must use platformdirs.user_cache_dir()"
        )
```

If `rg` is not available, fall back to a Python-based file scan:
```python
import os

def test_no_hardcoded_cache_path():
    """Assert no hardcoded ~/.cache/ paths in source tree (STOR-10)."""
    src_dir = Path(__file__).parent.parent / "src"
    violations = []
    for root, _dirs, files in os.walk(src_dir):
        for fname in files:
            if not fname.endswith(".py"):
                continue
            fpath = Path(root) / fname
            content = fpath.read_text()
            for lineno, line in enumerate(content.splitlines(), 1):
                if "~/.cache" in line and not line.strip().startswith("#"):
                    violations.append(f"{fpath}:{lineno}: {line.strip()}")
    if violations:
        pytest.fail(
            "Found hardcoded ~/.cache/ references:\n"
            + "\n".join(violations)
            + "\nAll cache paths must use platformdirs.user_cache_dir()"
        )
```

Use the Python-based approach (no `rg` dependency in test suite).
</action>
<acceptance_criteria>
- Test scans all `.py` files under `src/` for `~/.cache` strings
- Test fails if any non-comment line contains `~/.cache`
- Test passes on the current codebase (no hardcoded cache paths exist)
- `pytest tests/test_schema.py::test_no_hardcoded_cache_path` exits 0
</acceptance_criteria>
</task>

</tasks>

<verification>
- [ ] `pytest tests/test_schema.py` runs all 5 tests and exits 0
- [ ] FTS5 tokenizer test covers sections_fts, symbols_fts, and examples_fts
- [ ] FTS5 test proves Porter stemming is NOT active
- [ ] Composite uniqueness test allows json.dumps as both function and method
- [ ] Cross-version URI test allows same URI string across versions
- [ ] Idempotent bootstrap test calls bootstrap_schema twice without error
- [ ] platformdirs test confirms no hardcoded ~/.cache/ in source
</verification>

<must_haves>
- All 5 success criteria from ROADMAP Phase 2 are covered by tests
- Tests use in-memory SQLite (no temp files needed)
- Tests exercise bootstrap_schema() from storage/db.py
- Tests are deterministic and fast
</must_haves>
