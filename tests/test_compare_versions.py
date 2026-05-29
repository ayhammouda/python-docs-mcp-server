"""Behavioral tests for CompareService.compare (CMPR-01/02/03, Phase 09).

Covers the four diff branches (added / removed / changed / unchanged), the
version/symbol error paths, the see-also / deprecation / signature-delta
heuristics, the M2 PageNotFoundError -> changed+note fallback, and the L1
token-frugality smoke check.

Each test maps to a cross-AI review finding (H2, H3, H4, M1, M2, M3, L1) and/or
a CONTEXT.md success criterion. The exception types are imported from
``..errors`` (NOT from compare.py), satisfying H4 option (a).
"""
from __future__ import annotations

import json

import pytest

from mcp_server_python_docs.errors import (
    PageNotFoundError,
    SymbolNotFoundError,
    VersionNotFoundError,
)
from mcp_server_python_docs.services.compare import CompareService, _extract_see_also
from mcp_server_python_docs.services.content import ContentService
from mcp_server_python_docs.storage.db import bootstrap_schema, get_readwrite_connection

# Section text fixtures (verbatim post-markdownify prose forms per 09-01 spike).
_SECTIONS = {
    # (version, anchor) -> (slug, content_text)
    ("3.10", "asyncio.run"): (
        "library/asyncio-runner.html",
        "Execute the coroutine and return the result.\n\nMore prose.",
    ),
    ("3.11", "asyncio.run"): (
        "library/asyncio-runner.html",
        "def asyncio.run(coro, *, debug=None)\n\n"
        "Execute the coroutine and return the result.\n\n"
        "Changed in version 3.10: Improved behavior.",
    ),
    ("3.10", "json.dumps"): (
        "library/json.html",
        "Serialize obj to a JSON formatted str.",
    ),
    ("3.11", "json.dumps"): (
        "library/json.html",
        "Serialize obj to a JSON formatted str.",
    ),
    ("3.11", "asyncio.TaskGroup"): (
        "library/asyncio-task.html",
        "An asynchronous context manager holding a group of tasks.\n\n"
        "New in version 3.11.",
    ),
    ("3.10", "pathlib.Path"): (
        "library/pathlib.html",
        "Concrete path classes.\n\nMore prose.",
    ),
    ("3.11", "pathlib.Path"): (
        "library/pathlib.html",
        "Concrete path classes.\n\nSee also\n\n"
        "[os.path](library/os.path.html) — Operating system path manipulation.\n"
        "[fnmatch](library/fnmatch.html) — Pattern matching.",
    ),
    ("3.10", "functools.cache"): (
        "library/functools.html",
        "Simple cache.\n\nSee also\n\n"
        "[lru_cache](library/lru_cache.html) — LRU cache.",
    ),
    ("3.11", "functools.cache"): (
        "library/functools.html",
        "Simple cache.\n\nMore prose only, no see-also.",
    ),
    ("3.10", "some.old_func"): (
        "library/somemodule.html",
        "Old API.",
    ),
    ("3.11", "some.old_func"): (
        "library/somemodule.html",
        "Old API.\n\nDeprecated since version 3.11: use new_func() instead.",
    ),
    # Prose-only line-1 change variant for the M1 advisory-heuristic test.
    ("3.10", "prose.thing"): (
        "library/prose.html",
        "Concrete path classes.\n\nBody.",
    ),
    ("3.11", "prose.thing"): (
        "library/prose.html",
        "Concrete pathy classes.\n\nBody.",
    ),
}

# Symbols present per version. (version, qualified_name) -> (symbol_type, anchor)
_SYMBOLS = {
    ("3.10", "asyncio.run"): ("function", "asyncio.run"),
    ("3.11", "asyncio.run"): ("function", "asyncio.run"),
    ("3.10", "json.dumps"): ("function", "json.dumps"),
    ("3.11", "json.dumps"): ("function", "json.dumps"),
    ("3.11", "asyncio.TaskGroup"): ("class", "asyncio.TaskGroup"),
    ("3.10", "pathlib.Path"): ("class", "pathlib.Path"),
    ("3.11", "pathlib.Path"): ("class", "pathlib.Path"),
    ("3.10", "functools.cache"): ("function", "functools.cache"),
    ("3.11", "functools.cache"): ("function", "functools.cache"),
    ("3.10", "some.old_func"): ("function", "some.old_func"),
    ("3.11", "some.old_func"): ("function", "some.old_func"),
    ("3.10", "prose.thing"): ("class", "prose.thing"),
    ("3.11", "prose.thing"): ("class", "prose.thing"),
}


@pytest.fixture
def compare_db(tmp_path):
    """Two-version fixture (3.10 not-default, 3.11 default) for compare tests.

    Local to this module (mirrors multi_version_db's inline placement). Seeds
    doc_sets, documents, sections, and symbols so CompareService.compare can be
    exercised end-to-end against real SQLite reads.
    """
    db_path = tmp_path / "compare.db"
    conn = get_readwrite_connection(db_path)
    bootstrap_schema(conn)

    for ver, is_default in (("3.10", 0), ("3.11", 1)):
        conn.execute(
            "INSERT INTO doc_sets (source, version, language, label, is_default, base_url) "
            "VALUES ('python-docs', ?, 'en', ?, ?, ?)",
            (ver, f"Python {ver}", is_default, f"https://docs.python.org/{ver}/"),
        )

    ds_ids = {
        row["version"]: row["id"]
        for row in conn.execute("SELECT id, version FROM doc_sets").fetchall()
    }

    # Insert documents + sections. One document per (version, slug); one section
    # per (version, anchor) keyed by the _SECTIONS table.
    doc_ids: dict[tuple[str, str], int] = {}
    for (ver, anchor), (slug, content) in _SECTIONS.items():
        ds_id = ds_ids[ver]
        key = (ver, slug)
        if key not in doc_ids:
            # Production-shaped slugs (CR-01 regression): real Sphinx ingestion
            # stores documents.uri WITH ".html" but documents.slug EXTENSIONLESS
            # (current_page_name); symbol/section URIs carry ".html". The old
            # fixture seeded both columns with the ".html" form, masking the slug
            # mismatch that broke get_docs on a real index.
            doc_slug = slug[:-5] if slug.endswith(".html") else slug
            conn.execute(
                "INSERT INTO documents (doc_set_id, uri, slug, title, content_text, char_count) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ds_id, slug, doc_slug, slug, "page", 4),
            )
            doc_ids[key] = conn.execute(
                "SELECT id FROM documents WHERE doc_set_id = ? AND slug = ?",
                (ds_id, doc_slug),
            ).fetchone()["id"]
        doc_id = doc_ids[key]
        uri = f"{slug}#{anchor}"
        conn.execute(
            "INSERT INTO sections "
            "(document_id, uri, anchor, heading, level, ordinal, content_text, char_count) "
            "VALUES (?, ?, ?, ?, 2, 0, ?, ?)",
            (doc_id, uri, anchor, anchor, content, len(content)),
        )

    # Insert symbols.
    for (ver, qname), (symbol_type, anchor) in _SYMBOLS.items():
        ds_id = ds_ids[ver]
        slug = _SECTIONS[(ver, anchor)][0]
        uri = f"{slug}#{anchor}"
        conn.execute(
            "INSERT INTO symbols "
            "(doc_set_id, qualified_name, normalized_name, module, symbol_type, uri, anchor) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ds_id, qname, qname.lower(), qname.split(".")[0], symbol_type, uri, anchor),
        )

    conn.commit()
    conn.execute("INSERT INTO symbols_fts(symbols_fts) VALUES('rebuild')")
    conn.execute("INSERT INTO sections_fts(sections_fts) VALUES('rebuild')")
    conn.commit()
    yield conn
    conn.close()


def _service(db) -> CompareService:
    """Build a CompareService wired to a ContentService over the same db."""
    return CompareService(db, ContentService(db))


def test_compare_added_in_v2(compare_db):
    """CMPR-01 + success criterion #1: symbol new in v2 -> change='added'."""
    result = _service(compare_db).compare("asyncio.TaskGroup", "3.10", "3.11")
    assert result.change == "added"
    assert result.symbol == "asyncio.TaskGroup"
    assert result.v1 == "3.10"
    assert result.v2 == "3.11"
    assert result.new_in == "3.11"


def test_compare_identical_versions(compare_db):
    """Success criterion #2: identical versions (symbol exists) -> 'unchanged'."""
    result = _service(compare_db).compare("json.dumps", "3.11", "3.11")
    assert result.change == "unchanged"
    assert result.new_in is None
    assert result.section_diff is None
    assert result.see_also_added == []
    assert result.note is None


def test_compare_changed_signature(compare_db):
    """CMPR-01 changed branch + M1: line-1 signature reflected in signature_delta."""
    result = _service(compare_db).compare("asyncio.run", "3.10", "3.11")
    assert result.change == "changed"
    assert result.changed_in == "3.10"
    assert result.signature_delta is not None
    # v2 line 1 is the function signature, so the heuristic includes it.
    assert "def asyncio.run" in result.signature_delta


def test_compare_signature_delta_documents_prose_change(compare_db):
    """M1: heuristic is advisory — same line-1 -> None; differing prose -> non-None.

    pathlib.Path's line 1 is identical across versions, so signature_delta is
    None. prose.thing's line 1 differs in PROSE ONLY, yet the heuristic still
    flags it: this is expected advisory behavior per M1 — production callers
    should NOT trust signature_delta as a definitive signature-change indicator.
    """
    same = _service(compare_db).compare("pathlib.Path", "3.10", "3.11")
    assert same.signature_delta is None

    prose = _service(compare_db).compare("prose.thing", "3.10", "3.11")
    assert prose.signature_delta is not None


def test_compare_see_also_added(compare_db):
    """CMPR-01 see-also + H3(a): gained references -> see_also_added populated."""
    result = _service(compare_db).compare("pathlib.Path", "3.10", "3.11")
    assert result.change == "changed"
    assert "os.path" in result.see_also_added
    assert "fnmatch" in result.see_also_added
    assert result.see_also_removed == []


def test_compare_see_also_removed(compare_db):
    """CMPR-01 see-also + H3(b): lost references -> see_also_removed populated."""
    result = _service(compare_db).compare("functools.cache", "3.10", "3.11")
    assert result.change == "changed"
    assert "lru_cache" in result.see_also_removed
    assert result.see_also_added == []


def test_compare_deprecated_in_v2(compare_db):
    """CMPR-01 deprecation + H3(c): v2 deprecation marker -> deprecated_in set."""
    result = _service(compare_db).compare("some.old_func", "3.10", "3.11")
    assert result.change == "changed"
    assert result.deprecated_in == "3.11"


def test_compare_unknown_version_raises_with_indexed_list(compare_db):
    """CMPR-02 + success criterion #3 + M3: error names the missing AND indexed versions."""
    with pytest.raises(VersionNotFoundError) as exc_info:
        _service(compare_db).compare("asyncio.run", "3.99", "3.11")
    message = str(exc_info.value)
    assert "3.99" in message  # the missing version is named
    assert "3.10" in message  # at least one indexed version is named
    assert "3.11" in message  # the other indexed version is also named


def test_compare_identical_versions_missing_symbol_raises(compare_db):
    """H2: identical versions must NOT short-circuit past symbol-existence check."""
    with pytest.raises(SymbolNotFoundError, match="does.not.exist"):
        _service(compare_db).compare("does.not.exist", "3.11", "3.11")


def test_compare_neither_version_has_symbol(compare_db):
    """RESEARCH §Q2(c) step 7: symbol absent in both versions -> SymbolNotFoundError."""
    with pytest.raises(SymbolNotFoundError, match="does.not.exist"):
        _service(compare_db).compare("does.not.exist", "3.10", "3.11")


def test_compare_page_not_available_returns_changed_with_note(compare_db, monkeypatch):
    """M2: PageNotFoundError in the both-present branch -> changed + note (not unchanged)."""

    def _raise(*args, **kwargs):
        raise PageNotFoundError("simulated page not found")

    svc = _service(compare_db)
    monkeypatch.setattr(svc._content, "get_docs", _raise)
    # json.dumps exists in both 3.10 and 3.11, so we reach the both-present branch.
    result = svc.compare("json.dumps", "3.10", "3.11")
    assert result.change == "changed"
    assert result.section_diff is None
    assert result.note == "docs page not available for one or both versions"


def test_compare_diff_is_token_frugal(compare_db):
    """CMPR-03 + success criterion #4 + L1: byte-count regression smoke check.

    This is a REGRESSION SMOKE CHECK, not a literal token guarantee. Production
    tokenization may differ on unicode-heavy content; the assertion catches
    'result accidentally got 3x bigger' regressions (per cross-AI review L1).
    """
    result = _service(compare_db).compare("asyncio.TaskGroup", "3.10", "3.11")
    serialized = json.dumps(result.model_dump())
    approx_tokens = len(serialized) // 4
    assert approx_tokens < 300, (
        f"diff serialized to {len(serialized)} bytes "
        f"(~{approx_tokens} tokens), expected under 300"
    )
    assert len(serialized) < 1200


# --- Code-review regression tests (CR-01, WR-01, WR-02) ---


def test_section_text_resolves_extensionless_slug_cr01(compare_db):
    """CR-01: symbol URIs carry '.html' but documents.slug is extensionless.

    _section_text must resolve the '.html' symbol URI against the extensionless
    documents.slug that real Sphinx ingestion stores. Before the fix this raised
    PageNotFoundError on the production-shaped fixture, forcing every both-present
    comparison into the M2 'page unavailable' fallback. Guards against regression
    of the slug-derivation mismatch directly at the helper level.
    """
    svc = _service(compare_db)
    text = svc._section_text("library/json.html#json.dumps", "json.dumps", "3.11")
    assert "Serialize obj to a JSON formatted str." in text

    # And end-to-end: the both-present 'changed' branch computes real metadata
    # instead of returning the page-unavailable note.
    result = svc.compare("some.old_func", "3.10", "3.11")
    assert result.change == "changed"
    assert result.deprecated_in == "3.11"
    assert result.note is None


def test_extract_see_also_excludes_unrelated_body_links_wr01():
    """WR-01: the see-also window is one contiguous block.

    A 'See also' admonition followed by a blank line and then unrelated body
    prose (with its own links) must NOT capture those later links. markdownify
    rarely emits an ATX heading after the admonition, so the blank-line boundary
    is what bounds the window.
    """
    text = (
        "Some intro paragraph.\n\n"
        "See also\n"
        "[json.tool](library/json.tool.html) — CLI helper.\n\n"
        "This unrelated paragraph links to "
        "[totally.unrelated](library/unrelated.html) and more.\n"
    )
    labels = _extract_see_also(text)
    assert labels == ["json.tool"]
    assert "totally.unrelated" not in labels


def test_section_diff_truncates_on_line_boundary_wr02(compare_db, monkeypatch):
    """WR-02: oversized section_diff truncates on a line boundary + marker.

    The output must remain a parseable unified diff — no mid-line slice. Every
    emitted line is a valid unified-diff line (' ', '+', '-', '@') or the
    explicit truncation marker.
    """
    svc = _service(compare_db)

    def _long_section(uri, anchor, version):
        # Two large, fully-divergent bodies so the unified diff exceeds the cap.
        marker = "alpha" if version == "3.10" else "omega"
        return "\n".join(f"{marker} line {i} with content" for i in range(200))

    monkeypatch.setattr(svc, "_section_text", _long_section)
    result = svc.compare("json.dumps", "3.10", "3.11")

    assert result.change == "changed"
    assert result.section_diff is not None
    assert len(result.section_diff) <= 600 + len("\n... (diff truncated)")
    assert result.section_diff.endswith("... (diff truncated)")
    for line in result.section_diff.splitlines():
        if line == "... (diff truncated)":
            continue
        assert line[:1] in {" ", "+", "-", "@"}, f"corrupt diff line: {line!r}"
