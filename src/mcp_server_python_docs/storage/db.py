"""SQLite connection management with RO/RW split, WAL mode, and FTS5 check."""
from __future__ import annotations

import importlib.resources
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

    Works on both read-only and read-write connections:
    - RW: attempts CREATE/DROP of a temp FTS5 table (definitive check)
    - RO: falls back to PRAGMA compile_options when CREATE fails due to
      readonly mode (not a missing-FTS5 error)

    Raises FTS5UnavailableError with actionable guidance:
    - Linux x86-64: suggests pysqlite3-binary
    - macOS/Windows/ARM: suggests uv python install or python.org
    """
    try:
        conn.execute("CREATE VIRTUAL TABLE _fts5_check USING fts5(x)")
        conn.execute("DROP TABLE _fts5_check")
        return  # FTS5 confirmed via CREATE
    except sqlite3.OperationalError as e:
        error_msg = str(e).lower()
        # If the error is about readonly (not missing FTS5), check compile_options
        if "readonly" in error_msg or "read-only" in error_msg:
            opts = [row[0] for row in conn.execute("PRAGMA compile_options")]
            if "ENABLE_FTS5" in opts:
                return  # FTS5 is compiled in
        # FTS5 genuinely unavailable -- build platform-specific hint
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
    """Create all tables and FTS5 indexes from schema.sql (STOR-01, STOR-09).

    Loads the complete DDL from storage/schema.sql via importlib.resources.
    All CREATE statements use IF NOT EXISTS for idempotency -- running this
    twice on the same database is a no-op.

    FTS5 virtual tables are dropped and recreated on every bootstrap to ensure
    the tokenizer configuration matches the current schema.sql. This is safe
    because FTS5 external-content tables are derived data that can be rebuilt
    from canonical tables via the 'rebuild' command.

    Warning:
        This function uses ``executescript()``, which issues an implicit
        ``COMMIT`` before executing the DDL. Do not call ``bootstrap_schema()``
        while a transaction with uncommitted writes is in progress -- those
        writes will be silently committed.
    """
    # Drop FTS5 virtual tables first so they can be recreated with the
    # correct tokenizer. IF NOT EXISTS would skip recreation if the table
    # exists with a different tokenizer -- there is no ALTER for FTS5.
    for fts_table in ("sections_fts", "symbols_fts", "examples_fts"):
        conn.execute(f"DROP TABLE IF EXISTS {fts_table}")

    # Load and execute the full schema DDL
    ref = importlib.resources.files("mcp_server_python_docs.storage") / "schema.sql"
    with importlib.resources.as_file(ref) as schema_path:
        schema_sql = schema_path.read_text()
    conn.executescript(schema_sql)
