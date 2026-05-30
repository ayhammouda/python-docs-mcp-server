"""Persistent get_docs cache coverage."""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from pathlib import Path

from mcp_server_python_docs.models import GetDocsResult
from mcp_server_python_docs.services.content import ContentService
from mcp_server_python_docs.services.persistent_cache import _NO_ANCHOR_KEY, PersistentDocsCache


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


def test_current_default_codec_reads_identically_after_restart(tmp_path: Path):
    index_path, cache = _cache(tmp_path)
    expected = _result("compressed docs payload")
    cache.put(result=expected, max_chars=500, start_index=0)

    with sqlite3.connect(cache.cache_path) as conn:
        compression = conn.execute("SELECT compression FROM retrieved_docs_cache").fetchone()[0]
    assert compression == "zstd"

    restarted = PersistentDocsCache(tmp_path / "retrieved.sqlite3", index_path)
    assert (
        restarted.get(
            version="3.12",
            slug="library/json.html",
            anchor=None,
            max_chars=500,
            start_index=0,
        )
        == expected
    )
    assert restarted.stats().hits == 1


def test_legacy_uncompressed_cache_row_migrates_and_reads(tmp_path: Path):
    index_path = tmp_path / "index.db"
    index_path.write_bytes(b"index-1")
    fingerprint = PersistentDocsCache._fingerprint_index(index_path)
    cache_path = tmp_path / "retrieved.sqlite3"
    expected = _result("legacy docs payload")
    with sqlite3.connect(cache_path) as conn:
        conn.execute(
            "CREATE TABLE retrieved_docs_cache ("
            "index_fingerprint TEXT NOT NULL, version TEXT NOT NULL, slug TEXT NOT NULL, "
            "anchor TEXT NOT NULL, max_chars INTEGER NOT NULL, start_index INTEGER NOT NULL, "
            "result_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "PRIMARY KEY (index_fingerprint, version, slug, anchor, max_chars, start_index))"
        )
        conn.execute(
            "INSERT INTO retrieved_docs_cache "
            "(index_fingerprint, version, slug, anchor, max_chars, start_index, result_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                fingerprint,
                expected.version,
                expected.slug,
                _NO_ANCHOR_KEY,
                500,
                0,
                expected.model_dump_json(),
            ),
        )

    migrated = PersistentDocsCache(cache_path, index_path)
    assert (
        migrated.get(
            version="3.12",
            slug="library/json.html",
            anchor=None,
            max_chars=500,
            start_index=0,
        )
        == expected
    )
    with sqlite3.connect(cache_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(retrieved_docs_cache)")}
        compression = conn.execute("SELECT compression FROM retrieved_docs_cache").fetchone()[0]
    assert "compression" in columns
    assert compression == "none"


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


def test_cache_disables_gracefully_when_index_missing(tmp_path: Path, caplog):
    """Constructor must not raise when index.db is missing.

    Regression for CodeRabbit Major: ``_fingerprint_index()`` calls
    ``Path.stat()`` which raises ``FileNotFoundError``; without guarding,
    this turns an optional cache into a startup failure for the whole server.
    """
    cache_path = tmp_path / "cache.sqlite3"
    missing_index = tmp_path / "does-not-exist.db"
    assert not missing_index.exists()

    with caplog.at_level(logging.WARNING):
        cache = PersistentDocsCache(cache_path, missing_index)

    assert "Persistent docs cache disabled" in caplog.text
    # Cache should be disabled — get returns None, put is a no-op
    assert cache.get(
        version="3.13", slug="x", anchor=None, max_chars=100, start_index=0
    ) is None


def test_concurrent_puts_serialize_safely_without_lost_writes(tmp_path: Path):
    """Concurrent put() must not race on the shared connection or stats counter.

    Regression for CodeRabbit Major #2: ``check_same_thread=False`` alone does
    not make a sqlite connection safe for concurrent ``execute()``/``commit()``
    calls. Per the official Python sqlite3 docs, write operations must be
    serialized by the application. The unprotected ``self._writes += 1`` also
    races (read-modify-write across the GIL boundary), which is what this test
    exercises deterministically.
    """
    _, cache = _cache(tmp_path)
    n_threads = 20
    n_per_thread = 50
    expected = n_threads * n_per_thread
    errors: list[BaseException] = []
    err_lock = threading.Lock()

    def worker(tid: int) -> None:
        try:
            for i in range(n_per_thread):
                cache.put(
                    result=GetDocsResult(
                        content=f"c{tid}-{i}",
                        slug=f"slug-{tid}-{i}",
                        title="t",
                        version="3.12",
                        anchor=None,
                        char_count=4,
                    ),
                    max_chars=500,
                    start_index=0,
                )
        except Exception as e:
            with err_lock:
                errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Concurrent put() raised: {errors[:3]!r}"
    assert cache.stats().writes == expected, (
        f"writes counter raced: got {cache.stats().writes}, expected {expected}"
    )
    for tid in range(n_threads):
        for i in range(n_per_thread):
            got = cache.get(
                version="3.12",
                slug=f"slug-{tid}-{i}",
                anchor=None,
                max_chars=500,
                start_index=0,
            )
            assert got is not None, f"lost concurrent write for slug-{tid}-{i}"
