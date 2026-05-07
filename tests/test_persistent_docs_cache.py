"""Persistent get_docs cache coverage."""
from __future__ import annotations

import os
from pathlib import Path

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
