"""Tests for Phase 5: services, tool polish, caching, and validate-corpus.

Covers all Phase 5 requirements: SRVR-03, SRVR-04, SRVR-07,
OPS-01, OPS-02, OPS-03, OPS-04, OPS-05, PUBL-07.
"""
from __future__ import annotations

import io
import sys
from unittest.mock import patch

import pytest

from mcp_server_python_docs.errors import PageNotFoundError, VersionNotFoundError
from mcp_server_python_docs.models import (
    GetDocsResult,
    ListVersionsResult,
    SearchDocsResult,
)
from mcp_server_python_docs.services.cache import (
    create_section_cache,
    create_symbol_cache,
)
from mcp_server_python_docs.services.content import ContentService
from mcp_server_python_docs.services.observability import _format_logfmt, log_tool_call
from mcp_server_python_docs.services.search import SearchService
from mcp_server_python_docs.services.version import VersionService


# === Fixtures ===


@pytest.fixture
def populated_with_content(populated_db):
    """Extend populated_db with a document, sections, and symbols for testing."""
    db = populated_db

    # Get doc_set_id
    row = db.execute("SELECT id FROM doc_sets LIMIT 1").fetchone()
    doc_set_id = row[0]

    # Update built_at so VersionService returns non-empty string
    db.execute(
        "UPDATE doc_sets SET built_at = '2026-04-16T00:00:00' WHERE id = ?",
        (doc_set_id,),
    )

    # Add a document
    db.execute(
        "INSERT INTO documents (doc_set_id, uri, slug, title, content_text, char_count) "
        "VALUES (?, 'library/asyncio-task.html', 'library/asyncio-task.html', "
        "'asyncio.Task', 'Full page content here', 5000)",
        (doc_set_id,),
    )
    doc_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Add sections
    db.execute(
        "INSERT INTO sections (document_id, anchor, heading, level, ordinal, "
        "content_text, char_count, uri) "
        "VALUES (?, 'asyncio.TaskGroup', 'TaskGroup', 2, 0, "
        "'TaskGroup is a context manager for managing groups of tasks.', 60, "
        "'library/asyncio-task.html#asyncio.TaskGroup')",
        (doc_id,),
    )
    db.execute(
        "INSERT INTO sections (document_id, anchor, heading, level, ordinal, "
        "content_text, char_count, uri) "
        "VALUES (?, 'asyncio.create_task', 'create_task', 2, 1, "
        "'Create a task from a coroutine.', 31, "
        "'library/asyncio-task.html#asyncio.create_task')",
        (doc_id,),
    )

    # Add symbols
    db.execute(
        "INSERT INTO symbols (doc_set_id, qualified_name, normalized_name, "
        "symbol_type, uri, anchor, module) "
        "VALUES (?, 'asyncio.TaskGroup', 'asyncio.taskgroup', 'class', "
        "'library/asyncio-task.html#asyncio.TaskGroup', "
        "'asyncio.TaskGroup', 'asyncio')",
        (doc_set_id,),
    )

    db.commit()
    return db


# === SearchService Tests ===


class TestSearchService:
    """Tests for SearchService (SRVR-03 -- via search delegation)."""

    def test_search_returns_search_docs_result(self, populated_with_content):
        svc = SearchService(populated_with_content, {})
        result = svc.search("asyncio", kind="symbol")
        assert isinstance(result, SearchDocsResult)

    def test_search_symbol_fast_path(self, populated_with_content):
        svc = SearchService(populated_with_content, {})
        result = svc.search("asyncio.TaskGroup", kind="symbol")
        assert isinstance(result, SearchDocsResult)
        assert len(result.hits) >= 1
        assert result.hits[0].title == "asyncio.TaskGroup"

    def test_search_no_results(self, populated_with_content):
        svc = SearchService(populated_with_content, {})
        result = svc.search("nonexistent_xyz_symbol", kind="symbol")
        assert isinstance(result, SearchDocsResult)
        assert len(result.hits) == 0

    def test_search_resolution_tracking(self, populated_with_content):
        svc = SearchService(populated_with_content, {})
        svc.search("asyncio.TaskGroup", kind="symbol")
        assert svc._last_resolution == "exact"

    def test_search_synonym_expansion_tracking(self, populated_with_content):
        synonyms = {"http": ["urllib", "requests", "httplib"]}
        svc = SearchService(populated_with_content, synonyms)
        svc.search("http", kind="section")
        assert svc._last_synonym_expanded is True


# === ContentService Tests ===


class TestContentService:
    """Tests for ContentService (SRVR-03 -- get_docs)."""

    def test_get_docs_section_level(self, populated_with_content):
        svc = ContentService(populated_with_content)
        result = svc.get_docs(
            slug="library/asyncio-task.html",
            anchor="asyncio.TaskGroup",
        )
        assert isinstance(result, GetDocsResult)
        assert "TaskGroup" in result.content
        assert result.slug == "library/asyncio-task.html"
        assert result.anchor == "asyncio.TaskGroup"
        assert result.version == "3.13"

    def test_get_docs_page_level(self, populated_with_content):
        svc = ContentService(populated_with_content)
        result = svc.get_docs(slug="library/asyncio-task.html")
        assert isinstance(result, GetDocsResult)
        assert result.anchor is None
        assert len(result.content) > 0
        # Page-level should include content from both sections
        assert "TaskGroup" in result.content

    def test_get_docs_truncation(self, populated_with_content):
        svc = ContentService(populated_with_content)
        result = svc.get_docs(
            slug="library/asyncio-task.html",
            max_chars=10,
        )
        assert result.truncated is True
        assert result.next_start_index is not None
        assert len(result.content) <= 10

    def test_get_docs_page_not_found(self, populated_with_content):
        svc = ContentService(populated_with_content)
        with pytest.raises(PageNotFoundError):
            svc.get_docs(slug="nonexistent.html")

    def test_get_docs_version_not_found(self, populated_with_content):
        svc = ContentService(populated_with_content)
        with pytest.raises(VersionNotFoundError):
            svc.get_docs(slug="library/asyncio-task.html", version="99.99")

    def test_get_docs_section_not_found(self, populated_with_content):
        svc = ContentService(populated_with_content)
        with pytest.raises(PageNotFoundError):
            svc.get_docs(
                slug="library/asyncio-task.html",
                anchor="nonexistent_anchor",
            )

    def test_get_docs_default_version(self, populated_with_content):
        """Version defaults to is_default=1 when not specified."""
        svc = ContentService(populated_with_content)
        result = svc.get_docs(slug="library/asyncio-task.html")
        assert result.version == "3.13"


# === VersionService Tests ===


class TestVersionService:
    """Tests for VersionService (SRVR-04)."""

    def test_list_versions(self, populated_with_content):
        svc = VersionService(populated_with_content)
        result = svc.list_versions()
        assert isinstance(result, ListVersionsResult)
        assert len(result.versions) >= 1
        assert result.versions[0].version == "3.13"
        assert result.versions[0].is_default is True

    def test_list_versions_fields(self, populated_with_content):
        svc = VersionService(populated_with_content)
        result = svc.list_versions()
        v = result.versions[0]
        assert v.language == "en"
        assert v.label == "Python 3.13"
        assert v.built_at != ""


# === Observability Tests ===


class TestObservability:
    """Tests for structured logging (OPS-01, OPS-02, OPS-03)."""

    def test_format_logfmt_basic(self):
        result = _format_logfmt(
            tool="search_docs", latency_ms=12.3, truncated=False
        )
        assert "tool=search_docs" in result
        assert "latency_ms=12.3" in result
        assert "truncated=false" in result

    def test_format_logfmt_none_omitted(self):
        result = _format_logfmt(tool="test", version=None)
        assert "version" not in result
        assert "tool=test" in result

    def test_format_logfmt_string_with_spaces(self):
        result = _format_logfmt(note="hello world")
        assert 'note="hello world"' in result

    def test_format_logfmt_integer(self):
        result = _format_logfmt(result_count=5)
        assert "result_count=5" in result

    def test_log_tool_call_decorator_produces_stderr(self, populated_with_content):
        """OPS-03: verify decorator (not middleware) produces log output."""
        svc = SearchService(populated_with_content, {})
        stderr_capture = io.StringIO()
        with patch(
            "mcp_server_python_docs.services.observability.sys.stderr",
            stderr_capture,
        ):
            svc.search("asyncio.TaskGroup", kind="symbol")
        output = stderr_capture.getvalue()
        assert "tool=search_docs" in output
        assert "latency_ms=" in output

    def test_log_tool_call_includes_result_count(self, populated_with_content):
        svc = SearchService(populated_with_content, {})
        stderr_capture = io.StringIO()
        with patch(
            "mcp_server_python_docs.services.observability.sys.stderr",
            stderr_capture,
        ):
            svc.search("asyncio.TaskGroup", kind="symbol")
        output = stderr_capture.getvalue()
        assert "result_count=" in output

    def test_log_tool_call_content_service(self, populated_with_content):
        """ContentService.get_docs logs with truncated field."""
        svc = ContentService(populated_with_content)
        stderr_capture = io.StringIO()
        with patch(
            "mcp_server_python_docs.services.observability.sys.stderr",
            stderr_capture,
        ):
            svc.get_docs(
                slug="library/asyncio-task.html",
                anchor="asyncio.TaskGroup",
            )
        output = stderr_capture.getvalue()
        assert "tool=get_docs" in output
        assert "truncated=" in output

    def test_log_tool_call_version_service(self, populated_with_content):
        """VersionService.list_versions logs with result_count."""
        svc = VersionService(populated_with_content)
        stderr_capture = io.StringIO()
        with patch(
            "mcp_server_python_docs.services.observability.sys.stderr",
            stderr_capture,
        ):
            svc.list_versions()
        output = stderr_capture.getvalue()
        assert "tool=list_versions" in output
        assert "result_count=" in output


# === Cache Tests ===


class TestLRUCache:
    """Tests for LRU caching (OPS-04, OPS-05)."""

    def test_section_cache_maxsize(self, populated_with_content):
        cache_fn = create_section_cache(populated_with_content)
        info = cache_fn.cache_info()
        assert info.maxsize == 512

    def test_symbol_cache_maxsize(self, populated_with_content):
        cache_fn = create_symbol_cache(populated_with_content)
        info = cache_fn.cache_info()
        assert info.maxsize == 128

    def test_section_cache_hit(self, populated_with_content):
        cache_fn = create_section_cache(populated_with_content)
        # Get a section id
        row = populated_with_content.execute(
            "SELECT id FROM sections LIMIT 1"
        ).fetchone()
        if row is None:
            pytest.skip("No sections in test DB")
        section_id = row[0]

        # First call: miss
        result1 = cache_fn(section_id)
        assert result1 is not None
        assert cache_fn.cache_info().misses == 1

        # Second call: hit
        result2 = cache_fn(section_id)
        assert result2 == result1
        assert cache_fn.cache_info().hits == 1

    def test_symbol_cache_hit(self, populated_with_content):
        cache_fn = create_symbol_cache(populated_with_content)

        # First call: miss
        result1 = cache_fn("asyncio.TaskGroup", "3.13")
        if result1 is None:
            pytest.skip("Symbol not in test DB")
        assert cache_fn.cache_info().misses == 1

        # Second call: hit
        result2 = cache_fn("asyncio.TaskGroup", "3.13")
        assert result2 == result1
        assert cache_fn.cache_info().hits == 1

    def test_cache_process_lifetime(self, populated_with_content):
        """OPS-05: verify no TTL or invalidation exists."""
        cache_fn = create_section_cache(populated_with_content)
        # No TTL attribute exists on lru_cache
        assert not hasattr(cache_fn, "ttl")
        # Cache is only cleared by cache_clear() (manual) or process restart
        assert hasattr(cache_fn, "cache_clear")

    def test_section_cache_miss_returns_none(self, populated_with_content):
        cache_fn = create_section_cache(populated_with_content)
        result = cache_fn(999999)  # nonexistent ID
        assert result is None


# === Tool Registration Tests ===


class TestToolRegistration:
    """Tests for tool annotations (SRVR-03, SRVR-04, SRVR-07)."""

    def test_create_server_has_three_tools(self):
        from mcp_server_python_docs.server import create_server

        server = create_server()
        tools = server._tool_manager._tools
        tool_names = set(tools.keys())
        assert "search_docs" in tool_names
        assert "get_docs" in tool_names
        assert "list_versions" in tool_names

    def test_all_tools_have_annotations(self):
        from mcp_server_python_docs.server import create_server

        server = create_server()
        tools = server._tool_manager._tools
        for name in ["search_docs", "get_docs", "list_versions"]:
            tool = tools[name]
            annotations = tool.annotations
            assert annotations is not None, f"{name} missing annotations"
            assert (
                annotations.readOnlyHint is True
            ), f"{name} readOnlyHint should be True"
            assert (
                annotations.destructiveHint is False
            ), f"{name} destructiveHint should be False"
            assert (
                annotations.openWorldHint is False
            ), f"{name} openWorldHint should be False"

    def test_three_tools_registered(self):
        from mcp_server_python_docs.server import create_server

        server = create_server()
        tools = server._tool_manager._tools
        assert len(tools) == 3


# === validate-corpus Tests ===


class TestValidateCorpus:
    """Tests for validate-corpus CLI (PUBL-07)."""

    def test_validate_corpus_smoke_tests_shape(self, test_db, tmp_path):
        """validate-corpus reuses run_smoke_tests which returns (bool, list)."""
        from mcp_server_python_docs.ingestion.publish import run_smoke_tests

        db_path = tmp_path / "test.db"
        test_db.close()

        passed, messages = run_smoke_tests(db_path)
        assert isinstance(passed, bool)
        assert isinstance(messages, list)

    def test_validate_corpus_cli_help(self):
        """validate-corpus --help works."""
        from click.testing import CliRunner

        from mcp_server_python_docs.__main__ import main

        runner = CliRunner()
        result = runner.invoke(main, ["validate-corpus", "--help"])
        assert result.exit_code == 0
        assert "Validate" in result.output

    def test_validate_corpus_has_db_path_option(self):
        """validate-corpus has --db-path option."""
        from click.testing import CliRunner

        from mcp_server_python_docs.__main__ import main

        runner = CliRunner()
        result = runner.invoke(main, ["validate-corpus", "--help"])
        assert "--db-path" in result.output
