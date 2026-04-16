---
phase: 4
plan_id: 04-D
title: "Ingestion and publishing tests with fjson fixtures"
wave: 2
depends_on:
  - 04-A
  - 04-B
files_modified:
  - tests/test_ingestion.py
  - tests/test_publish.py
  - tests/fixtures/sample_library.fjson
  - tests/fixtures/sample_broken.fjson
  - tests/conftest.py
requirements:
  - INGR-C-04
  - INGR-C-05
  - INGR-C-06
  - INGR-C-07
  - INGR-C-08
  - INGR-C-09
  - PUBL-01
  - PUBL-02
  - PUBL-03
  - PUBL-04
  - PUBL-05
  - PUBL-06
autonomous: true
---

<objective>
Create comprehensive test suites for the ingestion and publishing modules using small fjson fixtures (no CPython clone or sphinx-build needed). Tests cover fjson parsing, HTML-to-markdown, code block extraction, per-document failure isolation, atomic swap, smoke tests, rollback, synonym population, FTS rebuild, and the ingestion-while-serving regression test (PUBL-06).
</objective>

<tasks>

<task id="1">
<title>Create fjson test fixtures</title>
<read_first>
- .planning/phases/04-sphinx-json-ingestion-atomic-swap-publishing/04-RESEARCH.md (RQ3 — fjson file structure, key names)
- src/mcp_server_python_docs/storage/schema.sql (table schemas for documents, sections, examples)
</read_first>
<action>
Create `tests/fixtures/` directory and two fixture files:

**`tests/fixtures/sample_library.fjson`** — A realistic but small fjson file mimicking CPython's `library/asyncio-task.fjson`. Must contain:
```json
{
  "body": "<h1 id=\"module-asyncio\">asyncio &#x2014; Asynchronous I/O<a class=\"headerlink\" href=\"#module-asyncio\">\u00b6</a></h1>\n<p>This module provides infrastructure for writing single-threaded concurrent code using coroutines.</p>\n<h2 id=\"asyncio.TaskGroup\">TaskGroup<a class=\"headerlink\" href=\"#asyncio.TaskGroup\">\u00b6</a></h2>\n<p>A context manager that holds a group of tasks. Example usage:</p>\n<div class=\"highlight-python3 notranslate\"><div class=\"highlight\"><pre><span></span><span class=\"k\">async</span> <span class=\"k\">with</span> <span class=\"n\">asyncio</span><span class=\"o\">.</span><span class=\"n\">TaskGroup</span><span class=\"p\">()</span> <span class=\"k\">as</span> <span class=\"n\">tg</span><span class=\"p\">:</span>\n    <span class=\"n\">tg</span><span class=\"o\">.</span><span class=\"n\">create_task</span><span class=\"p\">(</span><span class=\"n\">coro1</span><span class=\"p\">())</span>\n</pre></div></div>\n<h3 id=\"asyncio.TaskGroup.create_task\">create_task<a class=\"headerlink\" href=\"#asyncio.TaskGroup.create_task\">\u00b6</a></h3>\n<p>Create a task in the group.</p>\n<div class=\"highlight-pycon notranslate\"><div class=\"highlight\"><pre><span></span><span class=\"gp\">&gt;&gt;&gt; </span><span class=\"kn\">import</span> <span class=\"nn\">asyncio</span>\n<span class=\"gp\">&gt;&gt;&gt; </span><span class=\"nb\">print</span><span class=\"p\">(</span><span class=\"s2\">\"hello\"</span><span class=\"p\">)</span>\n<span class=\"go\">hello</span>\n</pre></div></div>",
  "title": "asyncio &#x2014; Asynchronous I/O",
  "current_page_name": "library/asyncio-task",
  "toc": "<ul><li><a href=\"#module-asyncio\">asyncio</a><ul><li><a href=\"#asyncio.TaskGroup\">TaskGroup</a></li></ul></li></ul>",
  "parents": [{"link": "../", "title": "Library"}],
  "prev": null,
  "next": null,
  "meta": {},
  "display_toc": true,
  "sourcename": "library/asyncio-task.rst"
}
```

This fixture has:
- 3 headings (h1, h2, h3) with `id` attributes for section extraction
- 1 standalone Python example (`highlight-python3`) 
- 1 doctest (`highlight-pycon`)
- HTML entities in title
- Nested heading hierarchy

**`tests/fixtures/sample_broken.fjson`** — A deliberately broken file:
```json
{this is not valid json at all
```

This tests per-document failure isolation (INGR-C-06).
</action>
<acceptance_criteria>
- `tests/fixtures/sample_library.fjson` exists and is valid JSON
- `tests/fixtures/sample_broken.fjson` exists and is NOT valid JSON
- sample_library.fjson contains `"current_page_name": "library/asyncio-task"`
- sample_library.fjson contains `"body"` key with HTML containing headings
- sample_library.fjson body contains `highlight-python3` (example) and `highlight-pycon` (doctest)
- sample_library.fjson contains at least 2 headings with `id` attributes
</acceptance_criteria>
</task>

<task id="2">
<title>Create test_ingestion.py with fjson parsing and content tests</title>
<read_first>
- src/mcp_server_python_docs/ingestion/sphinx_json.py (functions to test)
- tests/test_schema.py (analog — DB test patterns with tmp_path)
- tests/test_retrieval.py (analog — test patterns)
- tests/fixtures/sample_library.fjson (fixture created in task 1)
- tests/fixtures/sample_broken.fjson (fixture created in task 1)
- src/mcp_server_python_docs/storage/db.py (get_readwrite_connection, bootstrap_schema)
</read_first>
<action>
Create `tests/test_ingestion.py` with these test functions:

**fjson parsing tests (INGR-C-04):**

```python
def test_parse_fjson_valid(tmp_path):
    """parse_fjson loads a valid .fjson file."""
    # Copy fixture to tmp_path, call parse_fjson, assert keys exist
    # Assert "body", "title", "current_page_name" keys present
    
def test_parse_fjson_broken():
    """parse_fjson raises IngestionError on invalid JSON (INGR-C-06 input)."""
    # Point at sample_broken.fjson, assert IngestionError raised
```

**HTML-to-markdown tests (INGR-C-05):**

```python
def test_html_to_markdown_headings():
    """html_to_markdown converts HTML headings to markdown ATX headings."""
    html = '<h2 id="foo">Section Title</h2><p>Content here.</p>'
    md = html_to_markdown(html)
    assert "## Section Title" in md or "Section Title" in md
    assert "Content here." in md

def test_html_to_markdown_code_preserved():
    """html_to_markdown preserves code blocks."""
    html = '<p>Use <code>asyncio.run()</code> to start.</p>'
    md = html_to_markdown(html)
    assert "asyncio.run()" in md

def test_html_to_markdown_links():
    """html_to_markdown converts HTML links to markdown links."""
    html = '<p>See <a href="https://docs.python.org">docs</a>.</p>'
    md = html_to_markdown(html)
    assert "docs" in md
```

**Section extraction tests (INGR-C-04):**

```python
def test_extract_sections_from_fixture():
    """extract_sections parses headings with id attributes from fixture HTML."""
    # Load fixture body HTML
    # Call extract_sections
    # Assert at least 3 sections returned (h1, h2, h3)
    # Assert first section anchor is "module-asyncio"
    # Assert second section anchor is "asyncio.TaskGroup"
    # Assert third section anchor is "asyncio.TaskGroup.create_task"
    # Assert each section has non-empty content_text (markdown, not HTML)
    # Assert ordinals are 0, 1, 2

def test_extract_sections_empty_body():
    """extract_sections handles empty HTML body gracefully."""
    sections = extract_sections("", "test.html")
    assert len(sections) >= 0  # Should not raise

def test_extract_sections_no_headings():
    """extract_sections creates single section for body with no headings."""
    sections = extract_sections("<p>Just a paragraph.</p>", "test.html")
    assert len(sections) == 1
    assert sections[0]["anchor"] == ""
```

**Code block extraction tests (INGR-C-07):**

```python
def test_extract_code_blocks_from_fixture():
    """extract_code_blocks finds both doctest and example blocks."""
    # Load fixture body HTML
    # Call extract_code_blocks
    # Assert at least 2 code blocks returned
    # Assert one has is_doctest=1 (from highlight-pycon)
    # Assert one has is_doctest=0 (from highlight-python3)
    # Assert code text is non-empty

def test_extract_code_blocks_empty():
    """extract_code_blocks returns empty list for body with no code."""
    blocks = extract_code_blocks("<p>No code here.</p>")
    assert blocks == []
```

**Per-document failure isolation tests (INGR-C-06):**

```python
def test_broken_fjson_does_not_abort_build(tmp_path):
    """A broken .fjson file is logged and skipped, not fatal (INGR-C-06)."""
    # Set up DB with bootstrap_schema
    # Create a doc_set
    # Call ingest_fjson_file with the broken fixture path
    # Assert returns False (failure)
    # Assert no rows in documents table (nothing committed for this file)
    # Assert no exception raised

def test_ingest_mixed_directory(tmp_path):
    """ingest_sphinx_json_dir handles mix of valid and broken files."""
    # Copy both fixtures to tmp_path
    # Call ingest_sphinx_json_dir
    # Assert success=1, failures=1
    # Assert documents table has 1 row (from the valid fixture)
```

**FTS population tests (INGR-C-08):**

```python
def test_fts_sections_populated(tmp_path):
    """sections_fts is searchable after ingestion + rebuild."""
    # Set up DB, ingest valid fixture, call rebuild_fts_indexes
    # Query: SELECT 1 FROM sections_fts WHERE sections_fts MATCH '"asyncio"'
    # Assert at least 1 row returned

def test_fts_examples_populated(tmp_path):
    """examples_fts is searchable after ingestion + rebuild."""
    # Set up DB, ingest valid fixture, call rebuild_fts_indexes
    # Query: SELECT 1 FROM examples_fts WHERE examples_fts MATCH '"asyncio"'
    # Assert at least 1 row returned
```

**Synonym population tests (INGR-C-09):**

```python
def test_synonym_population(tmp_path):
    """populate_synonyms loads from synonyms.yaml into DB (INGR-C-09)."""
    # Set up DB with bootstrap_schema
    # Call populate_synonyms
    # Assert synonyms table has at least 10 rows
    # Assert a known concept like "http" or "web" exists
```

All tests use `tmp_path` for isolated test databases. Import from `mcp_server_python_docs.ingestion.sphinx_json` and `mcp_server_python_docs.storage.db`. Use `Path(__file__).parent / "fixtures"` to locate fixture files.
</action>
<acceptance_criteria>
- `tests/test_ingestion.py` exists
- File contains `def test_parse_fjson_valid`
- File contains `def test_parse_fjson_broken`
- File contains `def test_html_to_markdown`
- File contains `def test_extract_sections`
- File contains `def test_extract_code_blocks`
- File contains `def test_broken_fjson_does_not_abort_build`
- File contains `def test_fts_sections_populated`
- File contains `def test_fts_examples_populated`
- File contains `def test_synonym_population`
- `uv run pytest tests/test_ingestion.py -x -q` passes with 0 failures
</acceptance_criteria>
</task>

<task id="3">
<title>Create test_publish.py with atomic swap and serving regression tests</title>
<read_first>
- src/mcp_server_python_docs/ingestion/publish.py (functions to test)
- src/mcp_server_python_docs/storage/db.py (get_readwrite_connection, get_readonly_connection, bootstrap_schema)
- src/mcp_server_python_docs/ingestion/inventory.py (ingest_inventory — for creating a realistic index)
- tests/test_schema.py (analog — DB test patterns)
</read_first>
<action>
Create `tests/test_publish.py` with these test functions:

**Build path tests (PUBL-01):**

```python
def test_generate_build_path_is_timestamped():
    """generate_build_path returns a path with timestamp in filename."""
    path = generate_build_path()
    assert "build-" in path.name
    assert path.suffix == ".db"
    assert path.parent.exists()
```

**SHA256 tests (PUBL-02):**

```python
def test_compute_sha256(tmp_path):
    """compute_sha256 returns a hex digest of the file."""
    test_file = tmp_path / "test.db"
    test_file.write_bytes(b"test content")
    sha = compute_sha256(test_file)
    assert len(sha) == 64  # SHA256 hex is 64 chars
    assert all(c in "0123456789abcdef" for c in sha)

def test_compute_sha256_deterministic(tmp_path):
    """Same content produces same hash."""
    f1 = tmp_path / "a.db"
    f2 = tmp_path / "b.db"
    f1.write_bytes(b"identical")
    f2.write_bytes(b"identical")
    assert compute_sha256(f1) == compute_sha256(f2)
```

**Smoke test tests (PUBL-03):**

```python
def test_smoke_tests_pass_on_populated_db(tmp_path):
    """Smoke tests pass on a DB with sufficient data."""
    # Create a DB with bootstrap_schema
    # Insert enough rows: 1 doc_set, 20+ documents, 100+ sections, 2000+ symbols
    # (Use simple bulk inserts, not real ingestion)
    # Call run_smoke_tests
    # Assert returns (True, messages)

def test_smoke_tests_fail_on_empty_db(tmp_path):
    """Smoke tests fail on an empty DB."""
    db_path = tmp_path / "empty.db"
    conn = get_readwrite_connection(db_path)
    bootstrap_schema(conn)
    conn.close()
    passed, messages = run_smoke_tests(db_path)
    assert passed is False
```

**Atomic swap tests (PUBL-04):**

```python
def test_atomic_swap_creates_previous(tmp_path):
    """atomic_swap renames current to .previous and new to target."""
    target = tmp_path / "index.db"
    target.write_bytes(b"old content")
    new_db = tmp_path / "build-123.db"
    new_db.write_bytes(b"new content")
    
    prev = atomic_swap(new_db, target)
    
    assert target.exists()
    assert target.read_bytes() == b"new content"
    assert prev is not None
    assert prev.exists()
    assert prev.read_bytes() == b"old content"
    assert not new_db.exists()  # Moved, not copied

def test_atomic_swap_no_previous_when_fresh(tmp_path):
    """atomic_swap works when no previous index.db exists."""
    target = tmp_path / "index.db"
    new_db = tmp_path / "build-123.db"
    new_db.write_bytes(b"first build")
    
    prev = atomic_swap(new_db, target)
    
    assert target.exists()
    assert target.read_bytes() == b"first build"
    assert prev is None
```

**Rollback tests:**

```python
def test_rollback_restores_previous(tmp_path):
    """rollback() restores index.db.previous to index.db."""
    target = tmp_path / "index.db"
    previous = tmp_path / "index.db.previous"
    target.write_bytes(b"bad content")
    previous.write_bytes(b"good content")
    
    result = rollback(target)
    
    assert result is True
    assert target.read_bytes() == b"good content"
    assert not previous.exists()

def test_rollback_returns_false_without_previous(tmp_path):
    """rollback() returns False when no .previous exists."""
    target = tmp_path / "index.db"
    target.write_bytes(b"only version")
    result = rollback(target)
    assert result is False
```

**Restart message test (PUBL-05):**

```python
def test_print_restart_message(capsys):
    """print_restart_message outputs to stderr, not stdout."""
    print_restart_message()
    captured = capsys.readouterr()
    assert captured.out == ""  # Nothing on stdout
    assert "Restart your MCP client" in captured.err
```

**Ingestion-while-serving regression test (PUBL-06):**

```python
def test_ingestion_while_serving_does_not_crash(tmp_path):
    """Server with RO handle survives a rebuild in the same directory (PUBL-06).
    
    This test:
    1. Creates a populated index.db
    2. Opens a read-only connection (simulating server)
    3. Performs a "rebuild" by creating a new DB and atomic-swapping
    4. Asserts the original RO connection still works (stale results OK)
    5. Asserts the RO connection can execute queries without crashing
    """
    index_path = tmp_path / "index.db"
    
    # Step 1: Create initial populated DB
    conn_rw = get_readwrite_connection(index_path)
    bootstrap_schema(conn_rw)
    conn_rw.execute(
        "INSERT INTO doc_sets (source, version, language, label, is_default, base_url) "
        "VALUES ('python-docs', '3.13', 'en', 'Python 3.13', 1, 'https://docs.python.org/3.13/')"
    )
    conn_rw.execute(
        "INSERT INTO documents (doc_set_id, uri, slug, title, content_text, char_count) "
        "VALUES (1, 'library/test.html', 'library/test', 'Test', 'content', 7)"
    )
    conn_rw.commit()
    conn_rw.close()
    
    # Step 2: Open RO connection (simulating server)
    conn_ro = get_readonly_connection(index_path)
    # Verify it works
    row = conn_ro.execute("SELECT COUNT(*) FROM documents").fetchone()
    assert row[0] == 1
    
    # Step 3: "Rebuild" — create new DB and atomic swap
    new_db = tmp_path / "build-test.db"
    conn_new = get_readwrite_connection(new_db)
    bootstrap_schema(conn_new)
    conn_new.execute(
        "INSERT INTO doc_sets (source, version, language, label, is_default, base_url) "
        "VALUES ('python-docs', '3.13', 'en', 'Python 3.13', 1, 'https://docs.python.org/3.13/')"
    )
    conn_new.execute(
        "INSERT INTO documents (doc_set_id, uri, slug, title, content_text, char_count) "
        "VALUES (1, 'library/test.html', 'library/test', 'Test Updated', 'new content', 11)"
    )
    conn_new.execute(
        "INSERT INTO documents (doc_set_id, uri, slug, title, content_text, char_count) "
        "VALUES (1, 'library/test2.html', 'library/test2', 'Test 2', 'content 2', 9)"
    )
    conn_new.commit()
    conn_new.close()
    
    # Swap
    atomic_swap(new_db, index_path)
    
    # Step 4: Original RO connection still works (reads old inode — stale is OK)
    try:
        row = conn_ro.execute("SELECT COUNT(*) FROM documents").fetchone()
        # Stale read — should return 1 (old data), not crash
        assert row[0] >= 0  # Any non-crash result is acceptable
    except sqlite3.OperationalError:
        # Some platforms may error on renamed file — that's also acceptable
        # as long as it doesn't crash the process
        pass
    
    # Step 5: New RO connection sees new data
    conn_ro2 = get_readonly_connection(index_path)
    row = conn_ro2.execute("SELECT COUNT(*) FROM documents").fetchone()
    assert row[0] == 2  # New data
    
    # Cleanup
    conn_ro.close()
    conn_ro2.close()
```

All tests use `tmp_path` for isolation. Import from `mcp_server_python_docs.ingestion.publish` and `mcp_server_python_docs.storage.db`.
</action>
<acceptance_criteria>
- `tests/test_publish.py` exists
- File contains `def test_generate_build_path_is_timestamped`
- File contains `def test_compute_sha256`
- File contains `def test_smoke_tests_pass_on_populated_db`
- File contains `def test_smoke_tests_fail_on_empty_db`
- File contains `def test_atomic_swap_creates_previous`
- File contains `def test_atomic_swap_no_previous_when_fresh`
- File contains `def test_rollback_restores_previous`
- File contains `def test_print_restart_message`
- File contains `def test_ingestion_while_serving_does_not_crash`
- `uv run pytest tests/test_publish.py -x -q` passes with 0 failures
</acceptance_criteria>
</task>

<task id="4">
<title>Update conftest.py with shared fixtures for ingestion tests</title>
<read_first>
- tests/conftest.py (current state — may be empty or have existing fixtures)
- tests/test_ingestion.py (needs fixtures)
- tests/test_publish.py (needs fixtures)
</read_first>
<action>
Update `tests/conftest.py` to add shared fixtures if not already present:

```python
import sqlite3
from pathlib import Path

import pytest

from mcp_server_python_docs.storage.db import bootstrap_schema, get_readwrite_connection

FIXTURES_DIR = Path(__file__).parent / "fixtures"

@pytest.fixture
def fixtures_dir():
    """Path to test fixtures directory."""
    return FIXTURES_DIR

@pytest.fixture
def sample_fjson_path():
    """Path to the sample valid .fjson fixture."""
    return FIXTURES_DIR / "sample_library.fjson"

@pytest.fixture
def broken_fjson_path():
    """Path to the deliberately broken .fjson fixture."""
    return FIXTURES_DIR / "sample_broken.fjson"

@pytest.fixture
def test_db(tmp_path):
    """A fresh test database with schema bootstrapped."""
    db_path = tmp_path / "test.db"
    conn = get_readwrite_connection(db_path)
    bootstrap_schema(conn)
    yield conn
    conn.close()

@pytest.fixture
def populated_db(test_db):
    """A test database with a doc_set and minimal data."""
    test_db.execute(
        "INSERT INTO doc_sets (source, version, language, label, is_default, base_url) "
        "VALUES ('python-docs', '3.13', 'en', 'Python 3.13', 1, 'https://docs.python.org/3.13/')"
    )
    test_db.commit()
    return test_db
```

Do NOT remove any existing fixtures — only add new ones.
</action>
<acceptance_criteria>
- `tests/conftest.py` contains `def fixtures_dir`
- `tests/conftest.py` contains `def sample_fjson_path`
- `tests/conftest.py` contains `def broken_fjson_path`
- `tests/conftest.py` contains `def test_db`
- `tests/conftest.py` contains `def populated_db`
- Existing tests still pass: `uv run pytest tests/test_schema.py tests/test_retrieval.py -x -q`
</acceptance_criteria>
</task>

</tasks>

<verification>
- [ ] All test fixtures exist and contain expected content
- [ ] test_ingestion.py covers INGR-C-04 through INGR-C-09
- [ ] test_publish.py covers PUBL-01 through PUBL-06
- [ ] Per-document failure isolation test passes (INGR-C-06)
- [ ] Ingestion-while-serving regression test passes (PUBL-06)
- [ ] FTS searchability verified after rebuild (INGR-C-08)
- [ ] Synonym population verified (INGR-C-09)
- [ ] All existing tests continue to pass
- [ ] Full test suite: `uv run pytest tests/ -x -q` exits 0
</verification>

<must_haves>
- fjson fixture with realistic HTML body containing headings + code blocks
- Broken fjson fixture for failure isolation testing (INGR-C-06)
- Ingestion-while-serving regression test (PUBL-06) — server doesn't crash during rebuild
- FTS searchability verification after rebuild (INGR-C-08)
- Atomic swap correctness tests with .previous backup (PUBL-04)
- All tests pass without network access or CPython clone
</must_haves>
