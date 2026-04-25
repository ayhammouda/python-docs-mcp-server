"""Curated retrieval regression coverage for search/get_docs behavior."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mcp_server_python_docs.app_context import AppContext
from mcp_server_python_docs.errors import VersionNotFoundError
from mcp_server_python_docs.server import create_server
from mcp_server_python_docs.services.content import ContentService
from mcp_server_python_docs.services.search import SearchService
from mcp_server_python_docs.services.version import VersionService
from mcp_server_python_docs.storage.db import bootstrap_schema, get_readwrite_connection

_CASES_PATH = Path(__file__).parent / "fixtures" / "retrieval_regression_cases.json"
_REGRESSION_CASES = json.loads(_CASES_PATH.read_text())


@pytest.fixture
def regression_db(tmp_path):
    """A small multi-version docs index for retrieval regression tests."""
    db_path = tmp_path / "retrieval-regression.db"
    conn = get_readwrite_connection(db_path)
    bootstrap_schema(conn)

    conn.execute(
        "INSERT INTO doc_sets (id, source, version, language, label, is_default, base_url) "
        "VALUES (1, 'python-docs', '3.12', 'en', 'Python 3.12', 0, "
        "'https://docs.python.org/3.12/')"
    )
    conn.execute(
        "INSERT INTO doc_sets (id, source, version, language, label, is_default, base_url) "
        "VALUES (2, 'python-docs', '3.13', 'en', 'Python 3.13', 1, "
        "'https://docs.python.org/3.13/')"
    )

    conn.execute(
        "INSERT INTO documents (id, doc_set_id, uri, slug, title, content_text, char_count) "
        "VALUES (1, 1, 'library/asyncio-task.html', 'library/asyncio-task.html', "
        "'asyncio Task', 'Python 3.12 asyncio task documentation.', 300)"
    )
    conn.execute(
        "INSERT INTO documents (id, doc_set_id, uri, slug, title, content_text, char_count) "
        "VALUES (2, 2, 'library/asyncio-task.html', 'library/asyncio-task.html', "
        "'asyncio Task', 'Python 3.13 asyncio task documentation.', 300)"
    )
    conn.execute(
        "INSERT INTO documents (id, doc_set_id, uri, slug, title, content_text, char_count) "
        "VALUES (3, 2, 'library/json.html', 'library/json.html', "
        "'json module', 'Python 3.13 json module documentation.', 240)"
    )

    asyncio_312 = (
        "Python 3.12 TaskGroup documentation for concurrent task management. "
        "Use TaskGroup to supervise multiple child tasks and await them together."
    )
    asyncio_313 = (
        "Python 3.13 TaskGroup documentation for concurrent task management. "
        "The section explains structured concurrency and highlights 3.13 behavior."
    )

    conn.execute(
        "INSERT INTO sections (id, document_id, uri, anchor, heading, level, ordinal, "
        "content_text, char_count) "
        "VALUES (1, 1, 'library/asyncio-task.html#asyncio.TaskGroup', "
        "'asyncio.TaskGroup', 'asyncio.TaskGroup', 2, 0, ?, ?)",
        (asyncio_312, len(asyncio_312)),
    )
    conn.execute(
        "INSERT INTO sections (id, document_id, uri, anchor, heading, level, ordinal, "
        "content_text, char_count) "
        "VALUES (2, 2, 'library/asyncio-task.html#asyncio.TaskGroup', "
        "'asyncio.TaskGroup', 'asyncio.TaskGroup', 2, 0, ?, ?)",
        (asyncio_313, len(asyncio_313)),
    )
    conn.execute(
        "INSERT INTO sections (id, document_id, uri, anchor, heading, level, ordinal, "
        "content_text, char_count) "
        "VALUES (3, 2, 'library/asyncio-task.html#introduction', "
        "'introduction', 'Introduction', 1, 1, "
        "'Introduction to asyncio tasks in Python 3.13.', 46)"
    )
    conn.execute(
        "INSERT INTO sections (id, document_id, uri, anchor, heading, level, ordinal, "
        "content_text, char_count) "
        "VALUES (4, 3, 'library/json.html#json-parsing', "
        "'json-parsing', 'JSON parsing', 2, 0, "
        "'Parse JSON strings with json.loads and inspect JSON objects safely.', 67)"
    )

    conn.execute(
        "INSERT INTO symbols (id, doc_set_id, qualified_name, normalized_name, module, "
        "symbol_type, uri, anchor) "
        "VALUES (1, 1, 'asyncio.TaskGroup', 'asyncio.taskgroup', 'asyncio', "
        "'class', 'library/asyncio-task.html#asyncio.TaskGroup', 'asyncio.TaskGroup')"
    )
    conn.execute(
        "INSERT INTO symbols (id, doc_set_id, qualified_name, normalized_name, module, "
        "symbol_type, uri, anchor) "
        "VALUES (2, 2, 'asyncio.TaskGroup', 'asyncio.taskgroup', 'asyncio', "
        "'class', 'library/asyncio-task.html#asyncio.TaskGroup', 'asyncio.TaskGroup')"
    )
    conn.execute(
        "INSERT INTO symbols (id, doc_set_id, qualified_name, normalized_name, module, "
        "symbol_type, uri, anchor) "
        "VALUES (3, 2, 'json.loads', 'json.loads', 'json', "
        "'function', 'library/json.html#json.loads', 'json.loads')"
    )

    conn.commit()
    conn.execute("INSERT INTO sections_fts(sections_fts) VALUES('rebuild')")
    conn.execute("INSERT INTO symbols_fts(symbols_fts) VALUES('rebuild')")
    conn.execute("INSERT INTO examples_fts(examples_fts) VALUES('rebuild')")
    conn.commit()

    yield conn
    conn.close()


def _make_app_context(db, detected_python_version: str | None) -> AppContext:
    """Build an AppContext for direct tool invocation tests."""
    return AppContext(
        db=db,
        index_path=Path("retrieval-regression.db"),
        search_service=SearchService(db, {}),
        content_service=ContentService(db),
        version_service=VersionService(db),
        detected_python_version=detected_python_version,
        detected_python_source="test fixture",
    )


def _make_ctx(app_context: AppContext):
    """Build a minimal FastMCP tool context shim."""
    return SimpleNamespace(
        request_context=SimpleNamespace(lifespan_context=app_context)
    )


def _assert_search_expectations(result, expect: dict) -> None:
    """Assert the expected shape of a search result."""
    if "hits" in expect:
        assert len(result.hits) == expect["hits"]
        return

    assert len(result.hits) >= expect["min_hits"]
    if "first_hit" in expect:
        first_hit = result.hits[0]
        for field, value in expect["first_hit"].items():
            assert getattr(first_hit, field) == value
    if "versions" in expect:
        returned_versions = {hit.version for hit in result.hits}
        assert set(expect["versions"]).issubset(returned_versions)


def _assert_docs_expectations(result, expect: dict) -> None:
    """Assert the expected shape of a get_docs result."""
    if "version" in expect:
        assert result.version == expect["version"]
    if "anchor" in expect:
        assert result.anchor == expect["anchor"]
    if "title" in expect:
        assert result.title == expect["title"]
    if "content_contains" in expect:
        assert expect["content_contains"] in result.content
    if "truncated" in expect:
        assert result.truncated is expect["truncated"]
    if "content_max_length" in expect:
        assert len(result.content) <= expect["content_max_length"]
    if expect.get("next_start_index") is True:
        assert result.next_start_index is not None


@pytest.mark.parametrize(
    "case",
    _REGRESSION_CASES,
    ids=[case["id"] for case in _REGRESSION_CASES],
)
def test_retrieval_regression_cases(case, regression_db):
    """Keep core retrieval and defaulting behavior stable over time."""
    search_service = SearchService(regression_db, {})
    content_service = ContentService(regression_db)

    if case["operation"] == "search":
        result = search_service.search(**case["input"])
        _assert_search_expectations(result, case["expect"])
        return

    if case["operation"] == "get_docs":
        if case.get("error") == "VersionNotFoundError":
            with pytest.raises(VersionNotFoundError):
                content_service.get_docs(**case["input"])
            return

        result = content_service.get_docs(**case["input"])
        _assert_docs_expectations(result, case["expect"])
        return

    if case["operation"] == "server_get_docs_defaulted":
        server = create_server()
        tool = server._tool_manager._tools["get_docs"]
        app_context = _make_app_context(
            regression_db,
            detected_python_version=case["detected_python_version"],
        )
        ctx = _make_ctx(app_context)
        result = tool.fn(ctx=ctx, **case["input"])
        _assert_docs_expectations(result, case["expect"])
        return

    pytest.fail(f"Unknown regression case operation: {case['operation']}")
