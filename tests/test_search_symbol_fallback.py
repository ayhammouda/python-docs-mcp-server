"""Empty-query fallback uses only one known prompt identifier in a requested version."""
from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from mcp_server_python_docs.retrieval.ranker import lookup_symbols_exact
from mcp_server_python_docs.services.search import SearchService


@pytest.mark.parametrize("prompt", [
    "zzmissing Explain `json.dumps`?",
    "zzmissing Explain (json.dumps(obj)).",
    "zzmissing json.dumps, json.dumps() and json.dumps!",
    "zzmissing json.dumps alongside unknown.missing",
    "zzmissing Explain [os.path.join()] for Python 3.13?",
])
@pytest.mark.parametrize("max_results", [1, 5])
def test_unique_known_identifier(stability_db, prompt, max_results):
    svc = SearchService(stability_db, {})
    name = "os.path.join" if "os.path.join" in prompt else "json.dumps"
    expected = lookup_symbols_exact(stability_db, name, "3.13", max_results)
    assert expected
    result = svc.search(prompt, version="3.13", max_results=max_results)
    assert result.hits == expected
    assert len(result.hits) <= max_results
    assert all(hit.version == "3.13" for hit in result.hits)
    assert result.note == f"No full-query results; showing symbol matches for {name!r}."
    assert svc._last_resolution == "exact"


@pytest.mark.parametrize("prompt", [
    "zzmissing json.dumps and json.loads",
    "zzmissing json.loads and json.dumps",
    "zzmissing unknown.missing",
    "zzmissing json.dump",  # A partial inventory name must not enable LIKE fallback.
    "zzmissing json.dumps.missing",
    "zzmissing Python 3.13 or 3.12",
    "3.13",
    "",
    "   ",
])
def test_ambiguous_unknown_numeric_or_empty_stays_empty(search_service, prompt):
    result = search_service.search(prompt, version="3.13")
    assert result.hits == []
    assert result.note is None
    assert search_service._last_resolution == "fts"


@pytest.mark.parametrize("kind", ["symbol", "section", "example", "page"])
def test_explicit_kinds_do_not_use_fallback(search_service, kind):
    result = search_service.search("zzmissing json.dumps", version="3.13", kind=kind)
    assert result.hits == []
    assert result.note is None


def test_no_version_does_not_use_fallback(search_service):
    result = search_service.search("zzmissing json.dumps")
    assert result.hits == []
    assert result.note is None
    # Existing cross-version direct lookup is still available.
    assert search_service.search("json.dumps").hits


def test_known_identifiers_are_counted_in_requested_version(stability_db):
    stability_db.execute(
        "INSERT INTO doc_sets (source, version, language, label, base_url) "
        "VALUES ('python-docs', '3.12', 'en', 'Python 3.12', 'https://docs.python.org/3.12/')"
    )
    stability_db.execute(
        "INSERT INTO symbols (doc_set_id, qualified_name, normalized_name, "
        "symbol_type, uri, anchor, module) "
        "SELECT d.id, s.qualified_name, s.normalized_name, s.symbol_type, "
        "s.uri, s.anchor, s.module FROM symbols s CROSS JOIN doc_sets d "
        "WHERE s.qualified_name = 'json.dumps' AND d.version = '3.12'"
    )
    stability_db.commit()
    svc = SearchService(stability_db, {})
    prompt = "zzmissing json.dumps and json.loads"
    assert svc.search(prompt, version="3.13").hits == []
    hits = svc.search(prompt, version="3.12").hits
    assert hits and all(hit.version == "3.12" for hit in hits)
    assert hits[0].title == "json.dumps"
    assert svc.search("zzmissing asyncio.TaskGroup", version="3.12").hits == []


def test_original_lookup_and_both_fts_paths_run_before_fallback(stability_db):
    from mcp_server_python_docs.services import search

    trace = Mock()
    with (
        patch.object(search, "lookup_symbols_exact", wraps=search.lookup_symbols_exact) as exact,
        patch.object(search, "search_sections", wraps=search.search_sections) as sections,
        patch.object(search, "search_symbols", wraps=search.search_symbols) as symbols,
    ):
        trace.attach_mock(exact, "exact")
        trace.attach_mock(sections, "sections")
        trace.attach_mock(symbols, "symbols")
        result = SearchService(stability_db, {}).search("zzmissing json.dumps", "3.13")
    assert result.hits
    assert [call[0] for call in trace.mock_calls] == ["exact", "sections", "symbols", "exact"]
    assert exact.call_args_list[0].args[1] == "zzmissing json.dumps"
    assert exact.call_args_list[1].args[1] == "json.dumps"


@pytest.mark.parametrize("query,kind", [
    ("json.dumps", "symbol"),
    ("json.dumps Serialize", "section"),
])
def test_nonempty_original_results_are_unchanged(search_service, query, kind):
    expected = search_service.search(query, version="3.13", kind=kind)
    assert expected.hits
    actual = search_service.search(query, version="3.13")
    assert actual == expected
    assert actual.note is None
