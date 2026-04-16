"""Structural stability tests (TEST-01).

Assert structural properties of search results — not exact content.
These tests survive CPython doc revisions because they check:
- len(hits) >= 1 (symbol exists)
- "asyncio" in hit.uri (correct module)
- hit.kind in ("class", "function", ...) (correct type)

Never: assert hit.title == "some exact string"

All tests exercise the full SearchService stack (classifier -> synonym
expansion -> FTS5 or symbol fast-path -> ranker) via the search_service
fixture backed by the stability_db fixture in conftest.py.
"""
from __future__ import annotations

from mcp_server_python_docs.services.search import SearchService


class TestSymbolResolution:
    """Symbol fast-path resolution for dotted Python identifiers."""

    def test_resolve_asyncio_taskgroup(self, search_service: SearchService):
        """asyncio.TaskGroup resolves to a symbol hit in the asyncio module."""
        result = search_service.search("asyncio.TaskGroup", kind="symbol")
        assert len(result.hits) >= 1
        hit = result.hits[0]
        assert hit.kind == "class"
        assert "asyncio" in hit.uri
        assert "TaskGroup" in hit.title

    def test_resolve_json_dumps(self, search_service: SearchService):
        """json.dumps resolves to a hit in the json module."""
        result = search_service.search("json.dumps")
        assert len(result.hits) >= 1
        hit = result.hits[0]
        assert "json" in hit.uri
        assert "dumps" in hit.title

    def test_resolve_dotted_symbol(self, search_service: SearchService):
        """os.path.join resolves despite being a three-part dotted name."""
        result = search_service.search("os.path.join")
        assert len(result.hits) >= 1
        assert any("os.path" in h.uri for h in result.hits)

    def test_resolve_class_symbol(self, search_service: SearchService):
        """pathlib.Path resolves as a class-type symbol."""
        result = search_service.search("pathlib.Path", kind="symbol")
        assert len(result.hits) >= 1
        assert result.hits[0].kind in ("class", "symbol")

    def test_resolve_collections_ordereddict(self, search_service: SearchService):
        """collections.OrderedDict resolves in the collections module."""
        result = search_service.search("collections.OrderedDict")
        assert len(result.hits) >= 1
        assert any("collections" in h.uri for h in result.hits)

    def test_resolve_typing_optional(self, search_service: SearchService):
        """typing.Optional resolves in the typing module."""
        result = search_service.search("typing.Optional")
        assert len(result.hits) >= 1
        assert any("typing" in h.uri for h in result.hits)


class TestModuleLevelSearch:
    """Broad module-name queries return at least one hit."""

    def test_search_asyncio_module(self, search_service: SearchService):
        """Searching 'asyncio' returns hits (module or symbols)."""
        result = search_service.search("asyncio")
        assert len(result.hits) >= 1

    def test_search_json_module(self, search_service: SearchService):
        """Searching 'json' returns hits."""
        result = search_service.search("json")
        assert len(result.hits) >= 1

    def test_search_subprocess_module(self, search_service: SearchService):
        """Searching 'subprocess' returns hits."""
        result = search_service.search("subprocess")
        assert len(result.hits) >= 1

    def test_search_logging_module(self, search_service: SearchService):
        """Searching 'logging' returns hits."""
        result = search_service.search("logging")
        assert len(result.hits) >= 1


class TestResultShape:
    """Every hit has the required fields with valid types."""

    def test_hit_has_required_fields(self, search_service: SearchService):
        """Every hit has non-None uri, title, kind, and version."""
        result = search_service.search("asyncio.TaskGroup")
        assert len(result.hits) >= 1
        for hit in result.hits:
            assert hit.uri is not None
            assert hit.title is not None
            assert hit.kind is not None
            assert hit.version is not None

    def test_hit_score_is_numeric(self, search_service: SearchService):
        """Every hit has a non-negative numeric score."""
        result = search_service.search("asyncio.TaskGroup")
        assert len(result.hits) >= 1
        for hit in result.hits:
            assert isinstance(hit.score, (int, float))
            assert hit.score >= 0

    def test_max_results_respected(self, search_service: SearchService):
        """max_results caps the number of returned hits."""
        result = search_service.search("asyncio", max_results=2)
        assert len(result.hits) <= 2


class TestStdlibBreadth:
    """Spot-check that various stdlib domains return hits."""

    def test_search_stdlib_breadth_io(self, search_service: SearchService):
        """io.StringIO resolves."""
        result = search_service.search("io.StringIO")
        assert len(result.hits) >= 1

    def test_search_stdlib_breadth_csv(self, search_service: SearchService):
        """csv.reader resolves."""
        result = search_service.search("csv.reader")
        assert len(result.hits) >= 1

    def test_search_stdlib_breadth_threading(self, search_service: SearchService):
        """threading.Thread resolves."""
        result = search_service.search("threading.Thread")
        assert len(result.hits) >= 1


class TestEdgeCases:
    """Negative and adversarial inputs do not crash the search stack."""

    def test_nonexistent_symbol_returns_empty_or_few(self, search_service: SearchService):
        """A fabricated symbol returns zero hits or only low-relevance ones."""
        result = search_service.search("nonexistent.FakeSymbol12345")
        # Either no hits or all hits are low relevance (score < 0.5)
        assert len(result.hits) == 0 or all(h.score < 0.5 for h in result.hits)

    def test_empty_query_does_not_crash(self, search_service: SearchService):
        """An empty query returns without raising."""
        result = search_service.search("")
        assert isinstance(result.hits, list)

    def test_special_chars_do_not_crash(self, search_service: SearchService):
        """Adversarial special characters do not crash FTS5."""
        for query in ["c++", "*", "(", '"unbalanced', "AND OR NOT NEAR"]:
            result = search_service.search(query)
            assert isinstance(result.hits, list)

    def test_version_filter(self, search_service: SearchService):
        """When version is specified, all returned hits match that version."""
        result = search_service.search("asyncio.TaskGroup", version="3.13")
        for hit in result.hits:
            assert hit.version == "3.13"
