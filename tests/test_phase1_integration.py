"""Phase 1 integration tests (permanent regression guard).

Stability-test style: asserts structural properties that survive CPython doc revisions.
Uses a real objects.inv download for Python 3.13 to populate a temp SQLite index,
then queries via the search_docs path.

These tests require internet access for the initial objects.inv download.
"""
import tempfile
from pathlib import Path

import pytest

from mcp_server_python_docs.ingestion.inventory import ingest_inventory
from mcp_server_python_docs.storage.db import (
    assert_fts5_available,
    get_readwrite_connection,
)


@pytest.fixture(scope="module")
def populated_db():
    """Create a temp DB populated with Python 3.13 symbols.

    Module-scoped to avoid re-downloading objects.inv for every test.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test-index.db"
        conn = get_readwrite_connection(db_path)
        assert_fts5_available(conn)
        count = ingest_inventory(conn, "3.13")
        yield conn, count
        conn.close()


class TestSymbolIngestion:
    """Verify objects.inv ingestion produces expected data."""

    def test_symbol_count_minimum(self, populated_db):
        """objects.inv for 3.13 should produce 9K+ deduplicated Python symbols.

        The raw objects.inv has ~16K entries across all domains. After filtering
        to py domain only and deduplicating by qualified_name (keeping highest
        priority role), we get ~9-10K unique symbols.
        """
        _conn, count = populated_db
        assert count >= 9000, f"Expected 9K+ symbols, got {count}"

    def test_asyncio_taskgroup_exists(self, populated_db):
        """asyncio.TaskGroup must be in the symbols table."""
        conn, _ = populated_db
        row = conn.execute(
            "SELECT qualified_name, uri, symbol_type FROM symbols "
            "WHERE qualified_name = 'asyncio.TaskGroup'",
        ).fetchone()
        assert row is not None, "asyncio.TaskGroup not found in symbols"

    def test_asyncio_taskgroup_uri_contains_asyncio_task(self, populated_db):
        """asyncio.TaskGroup URI must contain 'asyncio-task.html'."""
        conn, _ = populated_db
        row = conn.execute(
            "SELECT uri FROM symbols WHERE qualified_name = 'asyncio.TaskGroup'",
        ).fetchone()
        assert row is not None
        assert "asyncio-task.html" in row["uri"], f"URI was: {row['uri']}"

    def test_asyncio_taskgroup_is_class(self, populated_db):
        """asyncio.TaskGroup should be typed as 'class'."""
        conn, _ = populated_db
        row = conn.execute(
            "SELECT symbol_type FROM symbols "
            "WHERE qualified_name = 'asyncio.TaskGroup'",
        ).fetchone()
        assert row is not None
        assert row["symbol_type"] == "class"

    def test_uri_expansion_no_dollar_signs(self, populated_db):
        """No URIs should contain unexpanded $ shorthand (INGR-I-03)."""
        conn, _ = populated_db
        rows = conn.execute(
            "SELECT COUNT(*) as cnt FROM symbols WHERE uri LIKE '%$%'",
        ).fetchone()
        assert rows["cnt"] == 0, "Found unexpanded $ in symbol URIs"

    def test_symbols_fts_populated(self, populated_db):
        """symbols_fts should have the same row count as symbols (INGR-I-06)."""
        conn, _ = populated_db
        sym_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        fts_count = conn.execute("SELECT COUNT(*) FROM symbols_fts").fetchone()[0]
        assert fts_count == sym_count, (
            f"FTS has {fts_count} rows vs {sym_count} symbols"
        )

    def test_doc_set_created(self, populated_db):
        """A doc_set for version 3.13 should exist."""
        conn, _ = populated_db
        row = conn.execute(
            "SELECT version, source FROM doc_sets WHERE version = '3.13'",
        ).fetchone()
        assert row is not None
        assert row["source"] == "python-docs"

    def test_json_module_symbols(self, populated_db):
        """json module symbols should be present (basic sanity)."""
        conn, _ = populated_db
        row = conn.execute(
            "SELECT qualified_name FROM symbols "
            "WHERE qualified_name = 'json.dumps'",
        ).fetchone()
        assert row is not None

    def test_module_extraction(self, populated_db):
        """Module should be extracted from qualified name."""
        conn, _ = populated_db
        row = conn.execute(
            "SELECT module FROM symbols "
            "WHERE qualified_name = 'asyncio.TaskGroup'",
        ).fetchone()
        assert row is not None
        assert row["module"] == "asyncio"
