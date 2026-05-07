"""SQLite-backed cache for completed get_docs results across MCP restarts."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import NamedTuple

from mcp_server_python_docs.models import GetDocsResult


class CacheStats(NamedTuple):
    hits: int = 0
    misses: int = 0
    writes: int = 0


class PersistentDocsCache:
    """Persist get_docs results by index fingerprint, version, and request identity."""

    def __init__(self, cache_path: Path, index_path: Path) -> None:
        self._cache_path = Path(cache_path)
        self._fingerprint = self._fingerprint_index(Path(index_path))
        self._hits = self._misses = self._writes = 0
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._cache_path), check_same_thread=False)
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS retrieved_docs_cache ("
            "index_fingerprint TEXT NOT NULL, version TEXT NOT NULL, slug TEXT NOT NULL, "
            "anchor TEXT NOT NULL, max_chars INTEGER NOT NULL, start_index INTEGER NOT NULL, "
            "result_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "PRIMARY KEY (index_fingerprint, version, slug, anchor, max_chars, start_index))"
        )
        self._conn.commit()

    @property
    def cache_path(self) -> Path:
        return self._cache_path

    @staticmethod
    def _fingerprint_index(index_path: Path) -> str:
        stat = index_path.stat()
        return f"{index_path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"

    def stats(self) -> CacheStats:
        return CacheStats(self._hits, self._misses, self._writes)

    def get(
        self, *, version: str, slug: str, anchor: str | None, max_chars: int, start_index: int
    ) -> GetDocsResult | None:
        row = self._conn.execute(
            "SELECT result_json FROM retrieved_docs_cache WHERE index_fingerprint = ? "
            "AND version = ? AND slug = ? AND anchor = ? AND max_chars = ? AND start_index = ?",
            (self._fingerprint, version, slug, anchor or "", max_chars, start_index),
        ).fetchone()
        if row is None:
            self._misses += 1
            return None
        self._hits += 1
        return GetDocsResult.model_validate_json(row[0])

    def put(self, *, result: GetDocsResult, max_chars: int, start_index: int) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO retrieved_docs_cache "
            "(index_fingerprint, version, slug, anchor, max_chars, start_index, result_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                self._fingerprint,
                result.version,
                result.slug,
                result.anchor or "",
                max_chars,
                start_index,
                result.model_dump_json(),
            ),
        )
        self._conn.commit()
        self._writes += 1

    def close(self) -> None:
        self._conn.close()
