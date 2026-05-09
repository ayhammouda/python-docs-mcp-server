"""SQLite-backed cache for completed get_docs results across MCP restarts."""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path
from typing import NamedTuple

from pydantic import ValidationError

from mcp_server_python_docs.models import GetDocsResult

logger = logging.getLogger(__name__)
_NO_ANCHOR_KEY = "\x00mcp-python-docs:no-anchor\x00"


class CacheStats(NamedTuple):
    hits: int = 0
    misses: int = 0
    writes: int = 0


class PersistentDocsCache:
    """Persist get_docs results by index fingerprint, version, and request identity."""

    def __init__(self, cache_path: Path, index_path: Path) -> None:
        self._cache_path = Path(cache_path)
        # Set after fingerprint stat succeeds; stays "" if init fails so the
        # cache disables cleanly without leaking partial state.
        self._fingerprint = ""
        self._hits = self._misses = self._writes = 0
        # ``check_same_thread=False`` lets multiple threads share the connection,
        # but per the Python sqlite3 docs writes must still be serialized by the
        # application — this lock guards every execute()/commit() and the stats
        # counters they update.
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        try:
            self._fingerprint = self._fingerprint_index(Path(index_path))
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._cache_path), check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = NORMAL")
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS retrieved_docs_cache ("
                "index_fingerprint TEXT NOT NULL, version TEXT NOT NULL, slug TEXT NOT NULL, "
                "anchor TEXT NOT NULL, max_chars INTEGER NOT NULL, start_index INTEGER NOT NULL, "
                "result_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                "PRIMARY KEY (index_fingerprint, version, slug, anchor, max_chars, start_index))"
            )
            self._conn.execute(
                "DELETE FROM retrieved_docs_cache WHERE index_fingerprint != ?",
                (self._fingerprint,),
            )
            self._conn.commit()
        except (OSError, sqlite3.Error) as e:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
            logger.warning("Persistent docs cache disabled: %s", e)

    @property
    def cache_path(self) -> Path:
        return self._cache_path

    @staticmethod
    def _fingerprint_index(index_path: Path) -> str:
        stat = index_path.stat()
        return f"{index_path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"

    @staticmethod
    def _anchor_key(anchor: str | None) -> str:
        return _NO_ANCHOR_KEY if anchor is None else anchor

    def stats(self) -> CacheStats:
        return CacheStats(self._hits, self._misses, self._writes)

    def get(
        self, *, version: str, slug: str, anchor: str | None, max_chars: int, start_index: int
    ) -> GetDocsResult | None:
        if self._conn is None:
            with self._lock:
                self._misses += 1
            return None
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT result_json FROM retrieved_docs_cache WHERE index_fingerprint = ? "
                    "AND version = ? AND slug = ? AND anchor = ? AND max_chars = ? "
                    "AND start_index = ?",
                    (
                        self._fingerprint,
                        version,
                        slug,
                        self._anchor_key(anchor),
                        max_chars,
                        start_index,
                    ),
                ).fetchone()
            except sqlite3.Error as e:
                self._misses += 1
                logger.warning("Persistent docs cache read skipped: %s", e)
                return None
            if row is None:
                self._misses += 1
                return None
            try:
                result = GetDocsResult.model_validate_json(row[0])
            except (ValidationError, ValueError) as e:
                self._misses += 1
                logger.warning("Persistent docs cache entry ignored: %s", e)
                return None
            self._hits += 1
            return result

    def put(self, *, result: GetDocsResult, max_chars: int, start_index: int) -> None:
        if self._conn is None:
            return
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT OR REPLACE INTO retrieved_docs_cache "
                    "(index_fingerprint, version, slug, anchor, max_chars, start_index, "
                    "result_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        self._fingerprint,
                        result.version,
                        result.slug,
                        self._anchor_key(result.anchor),
                        max_chars,
                        start_index,
                        result.model_dump_json(),
                    ),
                )
                self._conn.commit()
            except (sqlite3.Error, ValueError) as e:
                logger.warning("Persistent docs cache write skipped: %s", e)
                return
            self._writes += 1

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
