"""Schema tests for Phase 2 success criteria.

Tests FTS5 tokenizer regression (STOR-02), composite symbol uniqueness (STOR-03),
cross-version URI collision safety (STOR-04), idempotent bootstrap (STOR-09),
and platformdirs cache path audit (STOR-10).
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from mcp_server_python_docs.storage.db import bootstrap_schema


def _make_db() -> sqlite3.Connection:
    """Create an in-memory database with the full schema bootstrapped."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    bootstrap_schema(conn)
    return conn


def _insert_doc_set(
    conn: sqlite3.Connection,
    version: str = "3.13",
    is_default: int = 1,
) -> int:
    """Insert a doc_set and return its id."""
    conn.execute(
        "INSERT INTO doc_sets (source, version, language, label, is_default) "
        "VALUES ('python-docs', ?, 'en', ?, ?)",
        (version, f"Python {version}", is_default),
    )
    row = conn.execute(
        "SELECT id FROM doc_sets WHERE version = ?", (version,)
    ).fetchone()
    return row[0]


def _insert_document(
    conn: sqlite3.Connection,
    doc_set_id: int,
    slug: str,
    title: str = "Test Document",
) -> int:
    """Insert a document and return its id."""
    conn.execute(
        "INSERT INTO documents (doc_set_id, uri, slug, title, content_text, char_count) "
        "VALUES (?, ?, ?, ?, 'test content', 12)",
        (doc_set_id, slug, slug, title),
    )
    row = conn.execute(
        "SELECT id FROM documents WHERE slug = ? AND doc_set_id = ?",
        (slug, doc_set_id),
    ).fetchone()
    return row[0]


# ── Test 1: FTS5 tokenizer regression (Success Criterion 1) ──────────


def test_fts5_tokenizer_preserves_identifiers():
    """FTS5 indexes asyncio.TaskGroup, json.dumps, collections.OrderedDict
    as single tokens and retrieves each via exact-token search.

    Proves tokenize="unicode61 remove_diacritics 2 tokenchars '._'" is
    applied to sections_fts, symbols_fts, and examples_fts.
    No Porter stemming collapse (STOR-02).
    """
    conn = _make_db()
    ds_id = _insert_doc_set(conn)

    # Insert documents for FK references
    doc_asyncio = _insert_document(conn, ds_id, "library/asyncio-task.html", "asyncio")
    doc_json = _insert_document(conn, ds_id, "library/json.html", "json")
    doc_collections = _insert_document(
        conn, ds_id, "library/collections.html", "collections"
    )

    # Insert sections
    sections = [
        (doc_asyncio, "library/asyncio-task.html#asyncio.TaskGroup",
         "asyncio.TaskGroup", "asyncio.TaskGroup", 2, 1,
         "The TaskGroup class manages tasks", 31),
        (doc_json, "library/json.html#json.dumps",
         "json.dumps", "json.dumps", 2, 1,
         "Serialize obj to a JSON formatted str", 36),
        (doc_collections, "library/collections.html#collections.OrderedDict",
         "collections.OrderedDict", "collections.OrderedDict", 2, 1,
         "Dict subclass that remembers insertion order", 43),
    ]
    for doc_id, uri, anchor, heading, level, ordinal, content, char_count in sections:
        conn.execute(
            "INSERT INTO sections "
            "(document_id, uri, anchor, heading, level, ordinal, content_text, char_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (doc_id, uri, anchor, heading, level, ordinal, content, char_count),
        )

    # Insert symbols
    symbols = [
        (ds_id, "asyncio.TaskGroup", "asyncio.taskgroup", "asyncio", "class",
         "library/asyncio-task.html#asyncio.TaskGroup", "asyncio.TaskGroup"),
        (ds_id, "json.dumps", "json.dumps", "json", "function",
         "library/json.html#json.dumps", "json.dumps"),
        (ds_id, "collections.OrderedDict", "collections.ordereddict", "collections",
         "class", "library/collections.html#collections.OrderedDict",
         "collections.OrderedDict"),
    ]
    for ds, qn, nn, mod, st, uri, anchor in symbols:
        conn.execute(
            "INSERT INTO symbols "
            "(doc_set_id, qualified_name, normalized_name, module, symbol_type, uri, anchor) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ds, qn, nn, mod, st, uri, anchor),
        )

    # Insert an example
    conn.execute(
        "INSERT INTO examples (section_id, code, language, is_doctest, ordinal) "
        "VALUES (1, 'async with asyncio.TaskGroup() as tg:\n"
        "    tg.create_task(coro())', 'python', 0, 1)"
    )

    # Rebuild all FTS indexes
    conn.execute("INSERT INTO sections_fts(sections_fts) VALUES('rebuild')")
    conn.execute("INSERT INTO symbols_fts(symbols_fts) VALUES('rebuild')")
    conn.execute("INSERT INTO examples_fts(examples_fts) VALUES('rebuild')")

    # ── sections_fts: exact-token search on headings ──

    rows = conn.execute(
        "SELECT heading FROM sections_fts WHERE sections_fts MATCH ?",
        ('"asyncio.TaskGroup"',),
    ).fetchall()
    assert len(rows) >= 1, "asyncio.TaskGroup not found in sections_fts"
    assert any("asyncio.TaskGroup" in r[0] for r in rows)

    rows = conn.execute(
        "SELECT heading FROM sections_fts WHERE sections_fts MATCH ?",
        ('"json.dumps"',),
    ).fetchall()
    assert len(rows) >= 1, "json.dumps not found in sections_fts"

    rows = conn.execute(
        "SELECT heading FROM sections_fts WHERE sections_fts MATCH ?",
        ('"collections.OrderedDict"',),
    ).fetchall()
    assert len(rows) >= 1, "collections.OrderedDict not found in sections_fts"

    # ── symbols_fts: exact-token search on qualified_name ──

    rows = conn.execute(
        "SELECT qualified_name FROM symbols_fts WHERE symbols_fts MATCH ?",
        ('"asyncio.TaskGroup"',),
    ).fetchall()
    assert len(rows) >= 1, "asyncio.TaskGroup not found in symbols_fts"

    rows = conn.execute(
        "SELECT qualified_name FROM symbols_fts WHERE symbols_fts MATCH ?",
        ('"json.dumps"',),
    ).fetchall()
    assert len(rows) >= 1, "json.dumps not found in symbols_fts"

    # ── examples_fts: exact-token search on code ──

    rows = conn.execute(
        "SELECT code FROM examples_fts WHERE examples_fts MATCH ?",
        ('"asyncio.TaskGroup"',),
    ).fetchall()
    assert len(rows) >= 1, "asyncio.TaskGroup not found in examples_fts"

    # ── Porter stemming NOT active ──
    # If Porter stemming were active, "dump" would stem-match "dumps".
    # With unicode61 (no porter), searching the heading column for the
    # standalone token "dump" should not match "json.dumps" as a heading.
    rows = conn.execute(
        "SELECT heading FROM sections_fts WHERE heading MATCH ?",
        ('"dump"',),
    ).fetchall()
    assert len(rows) == 0, (
        "Porter stemming appears active -- 'dump' matched 'json.dumps' heading"
    )

    conn.close()


# ── Test 2: Composite symbol uniqueness (Success Criterion 2) ────────


def test_symbol_composite_uniqueness():
    """json.dumps can exist as both function and method in the same doc_set.

    UNIQUE(doc_set_id, qualified_name, symbol_type) allows this (STOR-03).
    A duplicate (same triple) must raise IntegrityError.
    """
    conn = _make_db()
    ds_id = _insert_doc_set(conn)

    # Insert json.dumps as function -- must succeed
    conn.execute(
        "INSERT INTO symbols "
        "(doc_set_id, qualified_name, normalized_name, module, symbol_type, uri, anchor) "
        "VALUES (?, 'json.dumps', 'json.dumps', 'json', 'function', "
        "'library/json.html#json.dumps', 'json.dumps')",
        (ds_id,),
    )

    # Insert json.dumps as method -- must also succeed
    conn.execute(
        "INSERT INTO symbols "
        "(doc_set_id, qualified_name, normalized_name, module, symbol_type, uri, anchor) "
        "VALUES (?, 'json.dumps', 'json.dumps', 'json', 'method', "
        "'library/json.html#json.dumps', 'json.dumps')",
        (ds_id,),
    )

    # Both rows exist
    count = conn.execute(
        "SELECT COUNT(*) FROM symbols WHERE qualified_name = 'json.dumps'"
    ).fetchone()[0]
    assert count == 2, f"Expected 2 rows for json.dumps, got {count}"

    # Duplicate triple must fail
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO symbols "
            "(doc_set_id, qualified_name, normalized_name, module, symbol_type, uri, anchor) "
            "VALUES (?, 'json.dumps', 'json.dumps', 'json', 'function', "
            "'library/json.html#json.dumps', 'json.dumps')",
            (ds_id,),
        )

    conn.close()


# ── Test 3: Cross-version URI collision (Success Criterion 3) ────────


def test_cross_version_uri_no_collision():
    """Same URI string in sections for 3.12 and 3.13 inserts cleanly.

    sections.uri has no standalone UNIQUE constraint (STOR-04).
    Only UNIQUE(document_id, anchor) is enforced.
    """
    conn = _make_db()

    # Two doc_sets for different versions
    ds_12 = _insert_doc_set(conn, "3.12", is_default=0)
    ds_13 = _insert_doc_set(conn, "3.13", is_default=1)

    # Same slug in both versions
    doc_12 = _insert_document(conn, ds_12, "library/json.html", "json (3.12)")
    doc_13 = _insert_document(conn, ds_13, "library/json.html", "json (3.13)")

    # Insert sections with identical URI for both documents -- MUST succeed
    shared_uri = "library/json.html#json.dumps"
    conn.execute(
        "INSERT INTO sections "
        "(document_id, uri, anchor, heading, level, ordinal, content_text, char_count) "
        "VALUES (?, ?, 'json.dumps', 'json.dumps', 2, 1, 'Serialize obj', 13)",
        (doc_12, shared_uri),
    )
    conn.execute(
        "INSERT INTO sections "
        "(document_id, uri, anchor, heading, level, ordinal, content_text, char_count) "
        "VALUES (?, ?, 'json.dumps', 'json.dumps', 2, 1, 'Serialize obj', 13)",
        (doc_13, shared_uri),
    )

    # Both sections exist with the same URI
    count = conn.execute(
        "SELECT COUNT(*) FROM sections WHERE uri = ?", (shared_uri,)
    ).fetchone()[0]
    assert count == 2, f"Expected 2 sections with same URI, got {count}"

    # UNIQUE(document_id, anchor) still enforced -- duplicate within same doc fails
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO sections "
            "(document_id, uri, anchor, heading, level, ordinal, content_text, char_count) "
            "VALUES (?, ?, 'json.dumps', 'json.dumps dupe', 2, 2, 'Duplicate', 9)",
            (doc_12, "library/json.html#json.dumps-dupe"),
        )

    conn.close()


# ── Test 4: Idempotent bootstrap (Success Criterion 4) ───────────────


def test_bootstrap_idempotent():
    """Running bootstrap_schema() twice is a no-op. Data survives.

    doc_sets.language defaults to 'en' (STOR-05, STOR-09).
    """
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")

    # First bootstrap
    bootstrap_schema(conn)

    # Insert data
    conn.execute(
        "INSERT INTO doc_sets (source, version, language, label, is_default) "
        "VALUES ('python-docs', '3.13', 'en', 'Python 3.13', 1)"
    )

    # Second bootstrap -- must not error or lose data
    bootstrap_schema(conn)

    # Data survives
    row = conn.execute(
        "SELECT language FROM doc_sets WHERE version = '3.13'"
    ).fetchone()
    assert row is not None, "doc_set row lost after second bootstrap"
    assert row[0] == "en", f"Expected language='en', got '{row[0]}'"

    # language defaults to 'en' when omitted (STOR-05)
    conn.execute(
        "INSERT INTO doc_sets (source, version, label, is_default) "
        "VALUES ('python-docs', '3.12', 'Python 3.12', 0)"
    )
    row = conn.execute(
        "SELECT language FROM doc_sets WHERE version = '3.12'"
    ).fetchone()
    assert row[0] == "en", f"Expected default language='en', got '{row[0]}'"

    conn.close()


# ── Test 5: No hardcoded cache path (Success Criterion 5) ────────────


def test_no_hardcoded_cache_path():
    """No hardcoded ~/.cache/ paths in source tree (STOR-10).

    All cache directory paths must be resolved via platformdirs.user_cache_dir().
    References in comments and docstrings are allowed (documentation only).
    """
    src_dir = Path(__file__).parent.parent / "src"
    violations = []
    for root, _dirs, files in os.walk(src_dir):
        for fname in files:
            if not fname.endswith(".py"):
                continue
            fpath = Path(root) / fname
            content = fpath.read_text()
            in_docstring = False
            for lineno, line in enumerate(content.splitlines(), 1):
                stripped = line.strip()
                # Track triple-quote docstring boundaries
                triple_count = stripped.count('"""') + stripped.count("'''")
                if triple_count == 1:
                    in_docstring = not in_docstring
                elif triple_count >= 2:
                    pass  # single-line docstring, stays outside
                if in_docstring:
                    continue
                if "~/.cache" in line and not stripped.startswith("#"):
                    # Skip docstring lines (already filtered above)
                    # and single-line triple-quoted docstrings
                    if '"""' in stripped or "'''" in stripped:
                        continue
                    violations.append(f"{fpath}:{lineno}: {stripped}")
    if violations:
        pytest.fail(
            "Found hardcoded ~/.cache/ references in code:\n"
            + "\n".join(violations)
            + "\nAll cache paths must use platformdirs.user_cache_dir()"
        )
