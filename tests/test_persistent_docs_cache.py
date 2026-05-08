"""Persistent get_docs cache coverage."""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

from mcp_server_python_docs.models import GetDocsResult
from mcp_server_python_docs.services.content import ContentService
from mcp_server_python_docs.services.persistent_cache import PersistentDocsCache


def _doc(db, version: str, content: str, default: int = 0) -> None:
    db.execute("DELETE FROM doc_sets WHERE source='python-docs' AND version=?", (version,))
    db.execute(
        "INSERT INTO doc_sets (source, version, language, label, is_default) "
        "VALUES ('python-docs', ?, 'en', ?, ?)",
        (version, f"Python {version}", default),
    )
    ds = db.execute("SELECT id FROM doc_sets WHERE version=?", (version,)).fetchone()[0]
    db.execute(
        "INSERT INTO documents (doc_set_id, uri, slug, title, content_text, char_count) "
        "VALUES (?, 'library/json.html', 'library/json.html', ?, ?, ?)",
        (ds, f"json {version}", content, len(content)),
    )
    doc_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute(
        "INSERT INTO sections (document_id, uri, anchor, heading, level, ordinal, "
        "content_text, char_count) VALUES (?, 'library/json.html#top', 'top', "
        "'Top', 1, 0, ?, ?)",
        (doc_id, content, len(content)),
    )
    db.commit()


def _cache(tmp_path: Path, marker: bytes = b"index-1") -> tuple[Path, PersistentDocsCache]:
    index_path = tmp_path / "index.db"
    index_path.write_bytes(marker)
    return index_path, PersistentDocsCache(tmp_path / "retrieved.sqlite3", index_path)


def _result(content: str, *, anchor: str | None = None) -> GetDocsResult:
    return GetDocsResult(
        content=content,
        slug="library/json.html",
        title="json",
        version="3.12",
        anchor=anchor,
        char_count=len(content),
    )


def test_cache_survives_restart_and_miss_falls_back(populated_db, tmp_path: Path):
    _doc(populated_db, "3.12", "persisted docs", 1)
    index_path, cache = _cache(tmp_path)
    first = ContentService(populated_db, cache).get_docs("library/json.html", "3.12", max_chars=500)
    assert "persisted docs" in first.content
    assert cache.stats().misses == cache.stats().writes == 1

    restarted = PersistentDocsCache(tmp_path / "retrieved.sqlite3", index_path)
    second = ContentService(populated_db, restarted).get_docs(
        "library/json.html", "3.12", max_chars=500
    )
    assert second == first
    assert restarted.stats().hits == 1


def test_cache_key_includes_python_version(populated_db, tmp_path: Path):
    _doc(populated_db, "3.12", "docs for 3.12")
    _doc(populated_db, "3.13", "docs for 3.13", 1)
    _, cache = _cache(tmp_path)
    svc = ContentService(populated_db, cache)
    assert "3.12" in svc.get_docs("library/json.html", "3.12", max_chars=500).content
    assert "3.13" in svc.get_docs("library/json.html", "3.13", max_chars=500).content
    assert cache.stats().misses == 2


def test_cache_ignores_stale_entries_after_index_replacement(populated_db, tmp_path: Path):
    _doc(populated_db, "3.12", "old generation", 1)
    index_path, cache = _cache(tmp_path)
    ContentService(populated_db, cache).get_docs("library/json.html", "3.12", max_chars=500)
    index_path.write_bytes(b"index-generation-2-with-different-size")
    os.utime(index_path, None)

    stale = PersistentDocsCache(tmp_path / "retrieved.sqlite3", index_path)
    ContentService(populated_db, stale).get_docs("library/json.html", "3.12", max_chars=500)
    assert stale.stats().hits == 0
    assert stale.stats().misses == 1


def test_cache_key_distinguishes_no_anchor_from_empty_anchor(tmp_path: Path):
    _, cache = _cache(tmp_path)
    full_page = _result("full page", anchor=None)
    empty_anchor = _result("empty anchor", anchor="")

    cache.put(result=full_page, max_chars=500, start_index=0)
    assert (
        cache.get(version="3.12", slug="library/json.html", anchor="", max_chars=500, start_index=0)
        is None
    )

    cache.put(result=empty_anchor, max_chars=500, start_index=0)
    assert (
        cache.get(
            version="3.12", slug="library/json.html", anchor=None, max_chars=500, start_index=0
        )
        == full_page
    )
    assert (
        cache.get(version="3.12", slug="library/json.html", anchor="", max_chars=500, start_index=0)
        == empty_anchor
    )


def test_cache_key_includes_budget_and_start_index(tmp_path: Path):
    _, cache = _cache(tmp_path)
    page_100 = _result("chars-100")
    page_200 = _result("chars-200")
    page_start_10 = _result("start-10")

    cache.put(result=page_100, max_chars=100, start_index=0)
    cache.put(result=page_200, max_chars=200, start_index=0)
    cache.put(result=page_start_10, max_chars=100, start_index=10)

    assert (
        cache.get(
            version="3.12", slug="library/json.html", anchor=None, max_chars=100, start_index=0
        )
        == page_100
    )
    assert (
        cache.get(
            version="3.12", slug="library/json.html", anchor=None, max_chars=200, start_index=0
        )
        == page_200
    )
    assert (
        cache.get(
            version="3.12", slug="library/json.html", anchor=None, max_chars=100, start_index=10
        )
        == page_start_10
    )


def test_corrupt_cache_db_is_best_effort_miss(tmp_path: Path, caplog):
    index_path = tmp_path / "index.db"
    index_path.write_bytes(b"index")
    cache_path = tmp_path / "retrieved.sqlite3"
    cache_path.write_bytes(b"not sqlite")

    with caplog.at_level(logging.WARNING):
        cache = PersistentDocsCache(cache_path, index_path)
        cache.put(result=_result("ignored"), max_chars=100, start_index=0)
        assert (
            cache.get(
                version="3.12", slug="library/json.html", anchor=None, max_chars=100, start_index=0
            )
            is None
        )

    assert "Persistent docs cache disabled" in caplog.text
    assert cache.stats().misses == 1
    assert cache.stats().writes == 0


def test_invalid_cached_json_is_best_effort_miss(tmp_path: Path, caplog):
    _, cache = _cache(tmp_path)
    cache.put(result=_result("valid"), max_chars=100, start_index=0)
    with sqlite3.connect(cache.cache_path) as conn:
        conn.execute("UPDATE retrieved_docs_cache SET result_json = 'not json'")

    with caplog.at_level(logging.WARNING):
        assert (
            cache.get(
                version="3.12", slug="library/json.html", anchor=None, max_chars=100, start_index=0
            )
            is None
        )

    assert "Persistent docs cache entry ignored" in caplog.text
    assert cache.stats().misses == 1
