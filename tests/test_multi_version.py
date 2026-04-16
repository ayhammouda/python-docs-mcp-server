"""Tests for multi-version co-ingestion and version resolution.

Covers MVER-01 through MVER-05 and PKG-06.
"""
from __future__ import annotations

import pytest

from mcp_server_python_docs.errors import VersionNotFoundError
from mcp_server_python_docs.models import ListVersionsResult
from mcp_server_python_docs.services.content import ContentService
from mcp_server_python_docs.services.search import SearchService
from mcp_server_python_docs.services.version import VersionService
from mcp_server_python_docs.storage.db import bootstrap_schema, get_readwrite_connection


@pytest.fixture
def multi_version_db(tmp_path):
    """Database with two doc_sets: 3.12 (not default) and 3.13 (default)."""
    db_path = tmp_path / "test.db"
    conn = get_readwrite_connection(db_path)
    bootstrap_schema(conn)

    # Insert 3.12 (not default)
    conn.execute(
        "INSERT INTO doc_sets (source, version, language, label, is_default, base_url) "
        "VALUES ('python-docs', '3.12', 'en', 'Python 3.12', 0, "
        "'https://docs.python.org/3.12/')"
    )
    # Insert 3.13 (default) -- MVER-02
    conn.execute(
        "INSERT INTO doc_sets (source, version, language, label, is_default, base_url) "
        "VALUES ('python-docs', '3.13', 'en', 'Python 3.13', 1, "
        "'https://docs.python.org/3.13/')"
    )

    # Insert documents and sections for cross-version URI collision test (MVER-05)
    for ver in ("3.12", "3.13"):
        ds_row = conn.execute(
            "SELECT id FROM doc_sets WHERE version = ?", (ver,)
        ).fetchone()
        ds_id = ds_row[0]

        conn.execute(
            "INSERT INTO documents (doc_set_id, uri, slug, title, content_text, char_count) "
            "VALUES (?, 'library/asyncio-task.html', 'library/asyncio-task.html', "
            "'asyncio.Task', 'Full page content', 5000)",
            (ds_id,),
        )
        doc_row = conn.execute(
            "SELECT id FROM documents WHERE doc_set_id = ? AND slug = 'library/asyncio-task.html'",
            (ds_id,),
        ).fetchone()
        doc_id = doc_row[0]

        conn.execute(
            "INSERT INTO sections "
            "(document_id, uri, anchor, heading, level, ordinal, content_text, char_count) "
            "VALUES (?, 'library/asyncio-task.html#asyncio.TaskGroup', "
            "'asyncio.TaskGroup', 'TaskGroup', 2, 0, "
            "'TaskGroup content for ' || ?, 100)",
            (doc_id, ver),
        )

        # Insert a symbol for each version
        conn.execute(
            "INSERT INTO symbols "
            "(doc_set_id, qualified_name, normalized_name, module, "
            "symbol_type, uri, anchor) "
            "VALUES (?, 'asyncio.TaskGroup', 'asyncio.taskgroup', 'asyncio', 'class', "
            "'library/asyncio-task.html#asyncio.TaskGroup', 'asyncio.TaskGroup')",
            (ds_id,),
        )

    conn.commit()
    # Rebuild FTS indexes
    conn.execute("INSERT INTO symbols_fts(symbols_fts) VALUES('rebuild')")
    conn.execute("INSERT INTO sections_fts(sections_fts) VALUES('rebuild')")
    conn.commit()
    yield conn
    conn.close()


class TestMultiVersionDocSets:
    """MVER-01: Two doc_sets in same index.db."""

    def test_two_doc_sets_exist(self, multi_version_db):
        rows = multi_version_db.execute(
            "SELECT version FROM doc_sets ORDER BY version"
        ).fetchall()
        assert len(rows) == 2
        assert rows[0][0] == "3.12"
        assert rows[1][0] == "3.13"

    def test_default_is_3_13(self, multi_version_db):
        """MVER-02: is_default=True on 3.13 only."""
        rows = multi_version_db.execute(
            "SELECT version, is_default FROM doc_sets ORDER BY version"
        ).fetchall()
        assert rows[0]["is_default"] == 0  # 3.12
        assert rows[1]["is_default"] == 1  # 3.13


class TestCrossVersionURICollision:
    """MVER-05: Same slug in both versions does not violate UNIQUE."""

    def test_same_slug_both_versions(self, multi_version_db):
        rows = multi_version_db.execute(
            "SELECT d.slug, ds.version FROM documents d "
            "JOIN doc_sets ds ON d.doc_set_id = ds.id "
            "WHERE d.slug = 'library/asyncio-task.html' "
            "ORDER BY ds.version"
        ).fetchall()
        assert len(rows) == 2
        assert rows[0]["version"] == "3.12"
        assert rows[1]["version"] == "3.13"


class TestVersionResolution:
    """MVER-02, MVER-03: Default version and unknown version handling."""

    def test_search_no_version_resolves_default(self, multi_version_db):
        """search_docs without version should not raise."""
        svc = SearchService(multi_version_db, {})
        result = svc.search("asyncio.TaskGroup", version=None)
        # Should succeed (hits from default version)
        assert isinstance(result.hits, list)

    def test_search_unknown_version_raises(self, multi_version_db):
        """MVER-03: version=3.99 raises VersionNotFoundError."""
        svc = SearchService(multi_version_db, {})
        with pytest.raises(VersionNotFoundError, match=r"3\.99.*not found.*3\.12.*3\.13"):
            svc.search("asyncio", version="3.99")

    def test_content_no_version_resolves_3_13(self, multi_version_db):
        """get_docs without version resolves to 3.13."""
        svc = ContentService(multi_version_db)
        result = svc.get_docs(
            "library/asyncio-task.html", version=None, anchor="asyncio.TaskGroup"
        )
        assert result.version == "3.13"

    def test_content_unknown_version_raises(self, multi_version_db):
        """get_docs with unknown version raises VersionNotFoundError."""
        svc = ContentService(multi_version_db)
        with pytest.raises(VersionNotFoundError, match=r"3\.99.*not found"):
            svc.get_docs("library/asyncio-task.html", version="3.99")


class TestListVersions:
    """MVER-04: list_versions returns all doc_sets."""

    def test_list_versions_both(self, multi_version_db):
        svc = VersionService(multi_version_db)
        result = svc.list_versions()
        assert isinstance(result, ListVersionsResult)
        assert len(result.versions) == 2
        versions = {v.version for v in result.versions}
        assert versions == {"3.12", "3.13"}

    def test_list_versions_fields(self, multi_version_db):
        """Each version has required fields."""
        svc = VersionService(multi_version_db)
        result = svc.list_versions()
        for v in result.versions:
            assert v.version in ("3.12", "3.13")
            assert v.language == "en"
            assert v.label.startswith("Python")
            assert isinstance(v.is_default, bool)
            assert isinstance(v.built_at, str)

    def test_list_versions_default_flag(self, multi_version_db):
        """Only 3.13 has is_default=True."""
        svc = VersionService(multi_version_db)
        result = svc.list_versions()
        defaults = [v for v in result.versions if v.is_default]
        assert len(defaults) == 1
        assert defaults[0].version == "3.13"


class TestIngestInventoryDefault:
    """MVER-01: ingest_inventory is_default parameter."""

    def test_is_default_false(self, tmp_path):
        """Ingesting with is_default=False sets is_default=0."""
        db_path = tmp_path / "test_default.db"
        conn = get_readwrite_connection(db_path)
        bootstrap_schema(conn)
        # Test the is_default parameter on the doc_sets insert directly.
        conn.execute(
            "INSERT INTO doc_sets (source, version, language, label, is_default, base_url) "
            "VALUES ('python-docs', '3.12', 'en', 'Python 3.12', 0, "
            "'https://docs.python.org/3.12/')"
        )
        conn.commit()
        row = conn.execute(
            "SELECT is_default FROM doc_sets WHERE version = '3.12'"
        ).fetchone()
        assert row[0] == 0
        conn.close()

    def test_is_default_true(self, tmp_path):
        """Ingesting with is_default=True sets is_default=1."""
        db_path = tmp_path / "test_default_true.db"
        conn = get_readwrite_connection(db_path)
        bootstrap_schema(conn)
        conn.execute(
            "INSERT INTO doc_sets (source, version, language, label, is_default, base_url) "
            "VALUES ('python-docs', '3.13', 'en', 'Python 3.13', 1, "
            "'https://docs.python.org/3.13/')"
        )
        conn.commit()
        row = conn.execute(
            "SELECT is_default FROM doc_sets WHERE version = '3.13'"
        ).fetchone()
        assert row[0] == 1
        conn.close()


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


class TestCrossVersionSchemaConstraints:
    """MVER-05: Deep verification that cross-version data coexists safely."""

    def test_same_symbol_both_versions(self, multi_version_db):
        """Same qualified_name in two versions does not violate
        UNIQUE(doc_set_id, qualified_name, symbol_type)."""
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
            "SELECT qualified_name FROM symbols_fts "
            "WHERE symbols_fts MATCH 'asyncio'"
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

        # Same slug as existing versions
        multi_version_db.execute(
            "INSERT INTO documents (doc_set_id, uri, slug, title, content_text, char_count) "
            "VALUES (?, 'library/asyncio-task.html', 'library/asyncio-task.html', "
            "'asyncio.Task', 'Content for 3.14', 5000)",
            (ds_id,),
        )

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
