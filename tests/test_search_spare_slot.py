"""Additive canonical admission preserves semantic originals and byte budgets."""
from __future__ import annotations

import pytest

from mcp_server_python_docs.errors import VersionNotFoundError
from mcp_server_python_docs.models import SearchDocsResult, SymbolHit
from mcp_server_python_docs.retrieval.ranker import lookup_symbols_exact
from mcp_server_python_docs.services.search import SearchService


@pytest.fixture
def semantic_service(stability_db):
    doc = stability_db.execute(
        "SELECT id FROM documents WHERE slug = 'library/json.html'"
    ).fetchone()[0]
    for ordinal, anchor, text in [
        (3, "overview", "json.dumps settings overview for encoding"),
        (4, "release-notes", "Release changes to json.dumps settings compatibility"),
    ]:
        stability_db.execute(
            "INSERT INTO sections (document_id, uri, anchor, heading, level, ordinal, "
            "content_text, char_count) VALUES (?, ?, ?, ?, 2, ?, ?, ?)",
            (doc, f"library/json.html#{anchor}", anchor, anchor, ordinal, text, len(text)),
        )
    stability_db.execute("INSERT INTO sections_fts(sections_fts) VALUES('rebuild')")
    stability_db.commit()
    return SearchService(stability_db, {})


def test_real_search_retains_semantic_prefix_and_appends_once(semantic_service):
    svc = semantic_service
    query = "json.dumps settings"
    original = svc.search(query, "3.13", "section")
    assert len(original.hits) == 2
    assert {h.anchor for h in original.hits} == {"overview", "release-notes"}
    result = svc.search(query, "3.13")
    assert result.hits[:-1] == original.hits
    assert result.note == original.note
    assert result.hits[-1] == lookup_symbols_exact(svc._db, "json.dumps", "3.13", 1)[0]
    assert len(svc.search(query, "3.13", max_results=3).hits) == 3
    assert svc.search(query, "3.13", max_results=2) == original
    assert svc.search(query, "3.13", max_results=1).hits == original.hits[:1]
    assert svc._last_resolution == "fts"


@pytest.mark.parametrize("query", [
    "json.dump", "json.dumps.missing", "unknown.missing", "json.dumps json.loads",
    "éjson.dumps", "json.dumpsé", "", "   ",
])
def test_unknown_partial_ambiguous_unicode_identifiers_do_not_append(search_service, query):
    original = SearchDocsResult(hits=[SymbolHit(uri="other.html", title="Semantic",
                                              kind="section", version="3.13")])
    assert search_service._append_canonical(original, query, "3.13", "auto", 5) is original


@pytest.mark.parametrize("kind,version,limit", [
    ("symbol", "3.13", 5), ("page", "3.13", 5), ("section", "3.13", 5),
    ("example", "3.13", 5), ("auto", None, 5), ("auto", "3.13", 1),
])
def test_gates_preserve_original_object(search_service, kind, version, limit):
    original = SearchDocsResult(hits=[SymbolHit(uri="other.html", title="Semantic",
                                              kind="section", version="3.13")], note="Keep note")
    result = search_service._append_canonical(original, "json.dumps", version, kind, limit)
    assert result is original
    empty = SearchDocsResult(note="Empty")
    assert search_service._append_canonical(empty, "json.dumps", "3.13", "auto", 5) is empty


@pytest.mark.parametrize("suffix", ["", " alongside unknown.missing", " json.dumps()"])
def test_single_known_identifier_and_original_identity(search_service, suffix):
    original = SearchDocsResult(hits=[SymbolHit(uri="other.html", title="Semantic",
                                              kind="section", version="3.13", score=0.37)],
                                note='Keep "note" 😀')
    result = search_service._append_canonical(original, "Explain json.dumps" + suffix,
                                            "3.13", "auto", 5)
    assert len(result.hits) == 2
    assert result.hits[0] is original.hits[0]
    assert result.note == original.note
    assert original.hits[0].score == 0.37
    assert len(original.hits) == 1


def test_canonical_location_alias_and_extension_dedup(search_service):
    candidate = lookup_symbols_exact(search_service._db, "json.dumps", "3.13", 1)[0]
    # An alias/semantic title and extensionless get_docs slug share the same location.
    alias = candidate.model_copy(update={"title": "Alias", "slug": "library/json",
                                         "anchor": None})
    original = SearchDocsResult(hits=[alias])
    assert search_service._append_canonical(original, "json.dumps", "3.13", "auto", 5) is original
    assert search_service.search("json.dumps", "3.13").hits == [candidate]


def test_requested_version_inventory_and_location(search_service):
    db = search_service._db
    db.execute("INSERT INTO doc_sets (source, version, language, label, base_url) "
               "VALUES ('python-docs', '3.12', 'en', 'Python 3.12', 'https://docs.python.org/3.12/')")
    db.execute("INSERT INTO symbols (doc_set_id, qualified_name, normalized_name, symbol_type, "
               "uri, anchor, module) SELECT d.id, s.qualified_name, s.normalized_name, "
               "s.symbol_type, s.uri, s.anchor, s.module FROM symbols s CROSS JOIN doc_sets d "
               "WHERE s.qualified_name = 'json.dumps' AND d.version = '3.12'")
    db.commit()
    original = SearchDocsResult(hits=[SymbolHit(uri="other.html", title="Semantic",
                                              kind="section", version="3.12")])
    result = search_service._append_canonical(original, "json.dumps json.loads", "3.12", "auto", 5)
    assert len(result.hits) == 2 and all(h.version == "3.12" for h in result.hits)
    assert search_service._append_canonical(original, "json.loads", "3.12", "auto", 5) is original
    with pytest.raises(VersionNotFoundError):
        search_service.search("json.dumps settings", "9.99")


@pytest.mark.parametrize("extra_bytes,admitted", [(0, True), (1, False), (9000, False)])
def test_full_compact_utf8_budget_exact_fit_one_byte_short_and_oversized(
    search_service, extra_bytes, admitted,
):
    original = SearchDocsResult(hits=[SymbolHit(
        uri="other.html", title='Semantic "quoted"', kind="section", version="3.13",
        snippet='😀 café 漢字 "quote" \\escape\n\t', score=0.41,
    )], note='Preserve "note" \\ 😀')
    candidate = lookup_symbols_exact(search_service._db, "json.dumps", "3.13", 1)[0]
    proposed = original.model_copy(update={"hits": [*original.hits, candidate]})
    padding = 8000 - len(proposed.model_dump_json().encode("utf-8")) + extra_bytes
    original.hits[0].snippet += "x" * padding
    proposed = original.model_copy(update={"hits": [*original.hits, candidate]})
    assert len(proposed.model_dump_json().encode("utf-8")) == 8000 + extra_bytes
    assert len(proposed.model_dump_json()) < len(proposed.model_dump_json().encode("utf-8"))
    before = original.model_dump_json()
    result = search_service._append_canonical(original, "json.dumps", "3.13", "auto", 5)
    assert original.model_dump_json() == before
    if admitted:
        assert result.model_dump_json() == proposed.model_dump_json()
        assert result.hits[0] is original.hits[0]
    else:
        assert result is original
    if extra_bytes == 9000:
        assert len(original.model_dump_json().encode("utf-8")) > 8000


def test_inventory_alias_resolves_to_existing_section(search_service):
    db = search_service._db
    section = db.execute(
        "SELECT id, document_id FROM sections WHERE anchor = 'json.dumps'",
    ).fetchone()
    db.execute(
        "INSERT INTO symbols (doc_set_id, qualified_name, normalized_name, symbol_type, "
        "uri, anchor, module, document_id, section_id) "
        "VALUES (1, 'json.encode_alias', 'json_encode_alias', 'function', "
        "'library/alias.html#alias', 'alias', 'json', ?, ?)",
        (section['document_id'], section['id']),
    )
    db.commit()
    existing = lookup_symbols_exact(db, "json.dumps", "3.13", 1)[0]
    original = SearchDocsResult(hits=[existing])
    assert search_service._append_canonical(
        original, "json.encode_alias", "3.13", "auto", 5,
    ) is original
    alias = lookup_symbols_exact(db, "json.encode_alias", "3.13", 1)[0]
    assert (alias.slug, alias.anchor) == (existing.slug, existing.anchor)


def test_oversized_candidate_does_not_replace_original(search_service):
    search_service._db.execute(
        "UPDATE symbols SET uri = ? WHERE qualified_name = 'json.dumps'", ("x" * 9000,),
    )
    original = SearchDocsResult(hits=[SymbolHit(uri="other.html", title="Semantic",
                                              kind="section", version="3.13")])
    assert search_service._append_canonical(original, "json.dumps", "3.13", "auto", 5) is original
