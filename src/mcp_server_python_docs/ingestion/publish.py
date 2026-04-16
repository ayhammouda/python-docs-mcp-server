"""Atomic-swap publishing with smoke tests and rollback (PUBL-01 through PUBL-06).

Handles the full publish pipeline: timestamped build artifacts, SHA256 hashing,
smoke test validation, atomic rename with .previous backup, and the restart
message to stderr.
"""
from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from mcp_server_python_docs.storage.db import (
    get_cache_dir,
    get_index_path,
    get_readonly_connection,
)

logger = logging.getLogger(__name__)


def generate_build_path() -> Path:
    """Generate a timestamped build artifact path (PUBL-01).

    Returns a path like ``~/.cache/mcp-python-docs/build-20260416-143022-123456.db``.
    Creates the cache directory if it does not exist.

    Returns:
        Path to the new build artifact.
    """
    cache_dir = get_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return cache_dir / f"build-{timestamp}.db"


def compute_sha256(db_path: Path) -> str:
    """Compute the SHA256 hex digest of a file (PUBL-02).

    Reads the file in 8KB chunks for memory efficiency.

    Args:
        db_path: Path to the database file.

    Returns:
        SHA256 hex digest string (64 characters).
    """
    h = hashlib.sha256()
    with open(db_path, "rb") as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def record_ingestion_run(
    conn: sqlite3.Connection,
    source: str,
    version: str,
    status: str,
    artifact_hash: str | None,
    notes: str | None = None,
) -> int:
    """Record an ingestion run in the ingestion_runs table (PUBL-02).

    Args:
        conn: Read-write SQLite connection.
        source: Source identifier (e.g., 'python-docs').
        version: Version string (e.g., '3.13' or '3.12,3.13').
        status: Run status ('building', 'smoke_testing', 'published', 'failed').
        artifact_hash: SHA256 hash of the build artifact.
        notes: Optional notes about the run.

    Returns:
        Row ID of the inserted record.
    """
    cursor = conn.execute(
        "INSERT INTO ingestion_runs "
        "(source, version, status, started_at, finished_at, artifact_hash, notes) "
        "VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?, ?)",
        (source, version, status, artifact_hash, notes),
    )
    conn.commit()
    return cursor.lastrowid  # type: ignore[return-value]


def run_smoke_tests(
    db_path: Path,
    *,
    require_content: bool = True,
) -> tuple[bool, list[str]]:
    """Run smoke tests against a newly built database (PUBL-03).

    Validates that the index has sufficient data to be useful:
    - doc_sets table has at least 1 row
    - symbols table has at least 1000 rows
    - For content builds: documents/sections are populated and sections_fts is searchable
    - For symbol-only builds: symbols_fts is searchable

    Args:
        db_path: Path to the database to test.
        require_content: When True, enforce document/section checks suitable for
            full-content builds. When False, validate a symbol-only build.

    Returns:
        Tuple of (passed, messages). ``passed`` is True only if ALL
        checks succeed.
    """
    messages: list[str] = []
    passed = True

    try:
        conn = get_readonly_connection(db_path)
    except Exception as e:
        return False, [f"FAIL: Cannot open database: {e}"]

    try:
        # Check doc_sets
        count = conn.execute("SELECT COUNT(*) FROM doc_sets").fetchone()[0]
        if count >= 1:
            messages.append(f"OK: doc_sets: {count} rows")
        else:
            messages.append(f"FAIL: doc_sets: {count} rows (need >= 1)")
            passed = False

        # Check symbols
        count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        if count >= 1000:
            messages.append(f"OK: symbols: {count} rows")
        else:
            messages.append(f"FAIL: symbols: {count} rows (need >= 1000)")
            passed = False

        if require_content:
            # Check documents
            count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            if count >= 10:
                messages.append(f"OK: documents: {count} rows")
            else:
                messages.append(f"FAIL: documents: {count} rows (need >= 10)")
                passed = False

            # Check sections
            count = conn.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
            if count >= 50:
                messages.append(f"OK: sections: {count} rows")
            else:
                messages.append(f"FAIL: sections: {count} rows (need >= 50)")
                passed = False

            # Spot-check: asyncio document exists
            row = conn.execute(
                "SELECT 1 FROM documents WHERE slug LIKE '%asyncio%' LIMIT 1"
            ).fetchone()
            if row:
                messages.append("OK: spot-check: asyncio document found")
            else:
                messages.append("FAIL: spot-check: no asyncio document found")
                passed = False

            # FTS5 check: sections_fts is searchable
            try:
                with contextlib.closing(
                    conn.execute(
                        'SELECT 1 FROM sections_fts WHERE sections_fts MATCH \'"asyncio"\' LIMIT 1'
                    )
                ) as cursor:
                    row = cursor.fetchone()
                if row:
                    messages.append("OK: fts5: sections_fts searchable")
                else:
                    messages.append(
                        "WARN: fts5: sections_fts has no asyncio matches"
                        " (may be OK for partial builds)"
                    )
            except sqlite3.OperationalError as e:
                messages.append(f"FAIL: fts5: sections_fts query failed: {e}")
                passed = False
        else:
            messages.append("OK: content checks skipped for symbol-only build")
            try:
                with contextlib.closing(
                    conn.execute(
                        'SELECT 1 FROM symbols_fts WHERE symbols_fts MATCH \'"asyncio"\' LIMIT 1'
                    )
                ) as cursor:
                    row = cursor.fetchone()
                if row:
                    messages.append("OK: fts5: symbols_fts searchable")
                else:
                    messages.append(
                        "WARN: fts5: symbols_fts has no asyncio matches"
                        " (unexpected for stdlib builds)"
                    )
            except sqlite3.OperationalError as e:
                messages.append(f"FAIL: fts5: symbols_fts query failed: {e}")
                passed = False

    except Exception as e:
        messages.append(f"FAIL: Unexpected error during smoke tests: {e}")
        passed = False
    finally:
        conn.close()

    return passed, messages


def atomic_swap(
    new_db_path: Path,
    target_path: Path | None = None,
) -> Path | None:
    """Atomically swap a new database into place (PUBL-04).

    If a previous ``index.db`` exists, it is renamed to ``index.db.previous``
    for rollback. The new database is then renamed to ``index.db``.

    Both renames must be on the same filesystem for POSIX atomicity.

    Args:
        new_db_path: Path to the new build artifact.
        target_path: Target path for index.db. Defaults to the standard
            cache location.

    Returns:
        Path to the .previous backup if one was created, or None.
    """
    if target_path is None:
        target_path = get_index_path()

    previous_path: Path | None = None

    if target_path.exists():
        previous = target_path.parent / (target_path.name + ".previous")
        # Remove old .previous if it exists
        if previous.exists():
            previous.unlink()
        os.rename(target_path, previous)
        logger.info("Previous index backed up to %s", previous)
        previous_path = previous

    os.rename(new_db_path, target_path)
    logger.info("New index published at %s", target_path)

    return previous_path


def rollback(target_path: Path | None = None) -> bool:
    """Restore the previous index from .previous backup.

    Args:
        target_path: Path to index.db. Defaults to the standard
            cache location.

    Returns:
        True if rollback succeeded, False if no .previous exists.
    """
    if target_path is None:
        target_path = get_index_path()

    previous = target_path.parent / (target_path.name + ".previous")

    if previous.exists():
        os.rename(previous, target_path)
        logger.info("Rolled back to previous index")
        return True

    logger.warning("No .previous backup found at %s", previous)
    return False


def print_restart_message() -> None:
    """Print the restart message to stderr (PUBL-05).

    Stdout is reserved for MCP protocol messages — this message
    must go to stderr only.
    """
    print(
        "Index rebuilt. Restart your MCP client to pick up the new index.",
        file=sys.stderr,
    )


def publish_index(
    build_db_path: Path,
    version: str,
    *,
    require_content: bool = True,
) -> bool:
    """Orchestrate the full publish pipeline.

    1. Compute SHA256 of the build artifact
    2. Record ingestion run
    3. Run smoke tests
    4. If passed: finalize WAL, atomic swap, restart message
    5. If failed: update run status, return False

    M-7: a single read-write connection spans all three ingestion_runs
    updates so we don't repeatedly tear down and rebuild the WAL
    superstructure.

    I-2: before atomic_swap, call finalize_for_swap() to checkpoint the WAL
    back into the main DB file and switch journal_mode off — this prevents
    -wal / -shm sidecars from being renamed alongside the main file and
    leaking into the cache dir.

    Args:
        build_db_path: Path to the build artifact database.
        version: Version string for the ingestion run record.
        require_content: Whether publish validation should require content tables.

    Returns:
        True if publishing succeeded, False if smoke tests failed.
    """
    from mcp_server_python_docs.storage.db import (
        finalize_for_swap,
        get_readwrite_connection,
    )

    # Compute SHA256 (PUBL-02)
    artifact_hash = compute_sha256(build_db_path)
    logger.info("Build artifact SHA256: %s", artifact_hash)

    build_notes = "build_mode=symbol_only" if not require_content else None

    # === M-7: single RW connection for all three ingestion_runs updates ===
    conn = get_readwrite_connection(build_db_path)
    try:
        run_id = record_ingestion_run(
            conn,
            source="python-docs",
            version=version,
            status="smoke_testing",
            artifact_hash=artifact_hash,
            notes=build_notes,
        )

        # Smoke tests open their own RO connection — that's fine; they're read-only.
        passed, messages = run_smoke_tests(build_db_path, require_content=require_content)
        for msg in messages:
            logger.info("Smoke test: %s", msg)

        if not passed:
            conn.execute(
                "UPDATE ingestion_runs SET status = ?, notes = ?, "
                "finished_at = CURRENT_TIMESTAMP WHERE id = ?",
                ("failed", "\n".join(messages), run_id),
            )
            conn.commit()
            logger.error("Smoke tests failed — not publishing")
            return False

        conn.execute(
            "UPDATE ingestion_runs SET status = ?, notes = ?, "
            "finished_at = CURRENT_TIMESTAMP WHERE id = ?",
            ("published", build_notes, run_id),
        )
        conn.commit()

        # === I-2: finalize WAL so atomic_swap moves only the main DB file ===
        finalize_for_swap(conn)
    finally:
        conn.close()

    # Atomic swap (PUBL-04)
    atomic_swap(build_db_path)

    # Restart message (PUBL-05)
    print_restart_message()

    return True
