"""SQLite connection management with RO/RW split, WAL mode, and FTS5 check."""
from __future__ import annotations

import logging
import platform
import sqlite3
from pathlib import Path

import platformdirs

from mcp_server_python_docs.errors import FTS5UnavailableError

logger = logging.getLogger(__name__)


def get_cache_dir() -> Path:
    """Resolve cache directory via platformdirs (STOR-10).

    Returns ~/.cache/mcp-python-docs/ on Linux,
    ~/Library/Caches/mcp-python-docs/ on macOS, etc.
    """
    return Path(platformdirs.user_cache_dir("mcp-python-docs"))


def get_index_path() -> Path:
    """Return the default index.db path."""
    return get_cache_dir() / "index.db"


def _set_pragmas(conn: sqlite3.Connection) -> None:
    """Set required PRAGMAs on a connection (STOR-07)."""
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")


def get_readonly_connection(path: str | Path) -> sqlite3.Connection:
    """Open a read-only connection for serving (STOR-06).

    Uses SQLite URI mode with ?mode=ro to prevent accidental writes.
    """
    path = Path(path)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    _set_pragmas(conn)
    conn.row_factory = sqlite3.Row
    return conn


def get_readwrite_connection(path: str | Path) -> sqlite3.Connection:
    """Open a read-write connection for ingestion (STOR-06).

    Creates parent directories if needed. Sets WAL mode for
    concurrent reads during ingestion.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    _set_pragmas(conn)
    conn.row_factory = sqlite3.Row
    return conn


def assert_fts5_available(conn: sqlite3.Connection) -> None:
    """Check FTS5 availability with platform-aware error message (STOR-08).

    Raises FTS5UnavailableError with actionable guidance:
    - Linux x86-64: suggests pysqlite3-binary
    - macOS/Windows/ARM: suggests uv python install or python.org
    """
    try:
        conn.execute("CREATE VIRTUAL TABLE _fts5_check USING fts5(x)")
        conn.execute("DROP TABLE _fts5_check")
    except sqlite3.OperationalError as e:
        system = platform.system()
        machine = platform.machine()
        if system == "Linux" and machine == "x86_64":
            hint = (
                "Install the pysqlite3-binary fallback:\n"
                "  pip install 'mcp-server-python-docs[pysqlite3]'"
            )
        else:
            hint = (
                "Install a Python build with FTS5 support:\n"
                "  uv python install\n"
                "Or install Python from python.org (includes FTS5)."
            )
        raise FTS5UnavailableError(
            f"SQLite FTS5 extension is not available in this Python build.\n{hint}"
        ) from e


def bootstrap_schema(conn: sqlite3.Connection) -> None:
    """Create minimal schema for Phase 1 (symbols + doc_sets only).

    Full schema with sections, documents, examples, etc. lands in Phase 2.
    This creates just enough for symbol ingestion and search.
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS doc_sets (
            id          INTEGER PRIMARY KEY,
            source      TEXT NOT NULL DEFAULT 'python-docs',
            version     TEXT NOT NULL,
            language    TEXT NOT NULL DEFAULT 'en',
            label       TEXT NOT NULL,
            is_default  INTEGER NOT NULL DEFAULT 0,
            base_url    TEXT,
            built_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source, version, language)
        );

        CREATE TABLE IF NOT EXISTS symbols (
            id               INTEGER PRIMARY KEY,
            doc_set_id       INTEGER NOT NULL REFERENCES doc_sets(id) ON DELETE CASCADE,
            qualified_name   TEXT NOT NULL,
            normalized_name  TEXT NOT NULL,
            module           TEXT,
            symbol_type      TEXT,
            uri              TEXT NOT NULL,
            anchor           TEXT,
            UNIQUE(doc_set_id, qualified_name)
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS symbols_fts USING fts5(
            qualified_name, module,
            content='symbols', content_rowid='id',
            tokenize='unicode61'
        );
    """)
