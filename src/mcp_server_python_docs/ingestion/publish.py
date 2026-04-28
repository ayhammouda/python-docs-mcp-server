"""Atomic-swap publishing with smoke tests and rollback (PUBL-01 through PUBL-06).

Handles the full publish pipeline: timestamped build artifacts, SHA256 hashing,
smoke test validation, atomic rename with .previous backup, and the restart
message to stderr.
"""
from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import sys
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Final

from mcp_server_python_docs.storage.db import (
    get_cache_dir,
    get_index_path,
    get_readonly_connection,
)

logger = logging.getLogger(__name__)

SMOKE_SENTINEL_SYMBOL: Final[str] = "asyncio.run"


def _version_sort_key(version: str) -> tuple[int, ...]:
    """Sort dotted Python versions numerically."""
    return tuple(int(part) for part in version.split("."))


def parse_expected_versions(version: str) -> list[str]:
    """Parse a comma-separated build version string."""
    return [v.strip() for v in version.split(",") if v.strip()]


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
        version: Version string (e.g., '3.13' or '3.10,3.11,3.12,3.13,3.14').
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
    expected_versions: Iterable[str] | None = None,
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
        expected_versions: Optional versions requested by the build command. When
            present, each version must have its own expected corpus rows and the
            highest requested version must be the default.

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
        expected_version_list = list(dict.fromkeys(expected_versions or []))

        # Check doc_sets
        doc_set_rows = conn.execute(
            "SELECT id, version, is_default FROM doc_sets"
        ).fetchall()
        count = len(doc_set_rows)
        present_versions = [row["version"] for row in doc_set_rows]
        if count >= 1:
            messages.append(f"OK: doc_sets: {count} rows")
        else:
            messages.append(f"FAIL: doc_sets: {count} rows (need >= 1)")
            passed = False

        if expected_version_list:
            missing_versions = [
                version
                for version in expected_version_list
                if version not in present_versions
            ]
            if missing_versions:
                messages.append(
                    "FAIL: doc_sets: missing expected versions: "
                    + ", ".join(missing_versions)
                )
                passed = False
            else:
                messages.append(
                    "OK: doc_sets: expected versions present: "
                    + ", ".join(expected_version_list)
                )

        versions_for_default_check = expected_version_list or present_versions
        if versions_for_default_check:
            expected_default = max(versions_for_default_check, key=_version_sort_key)
            default_versions = [
                row["version"] for row in doc_set_rows if row["is_default"]
            ]
            if not default_versions:
                messages.append(
                    f"FAIL: doc_sets: no default version (expected {expected_default})"
                )
                passed = False
            elif len(default_versions) > 1:
                messages.append(
                    "FAIL: doc_sets: multiple default versions: "
                    + ", ".join(default_versions)
                )
                passed = False
            elif default_versions[0] != expected_default:
                messages.append(
                    "FAIL: doc_sets: default version is "
                    f"{default_versions[0]} (expected {expected_default})"
                )
                passed = False
            else:
                messages.append(f"OK: doc_sets: default version is {expected_default}")

        # Check symbols
        count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        if count >= 1000:
            messages.append(f"OK: symbols: {count} rows")
        else:
            messages.append(f"FAIL: symbols: {count} rows (need >= 1000)")
            passed = False

        for version in expected_version_list:
            count = conn.execute(
                "SELECT COUNT(*) FROM symbols "
                "JOIN doc_sets ON doc_sets.id = symbols.doc_set_id "
                "WHERE doc_sets.version = ?",
                (version,),
            ).fetchone()[0]
            if count >= 1000:
                messages.append(f"OK: symbols: version {version} has {count} rows")
            else:
                messages.append(
                    f"FAIL: symbols: version {version} has {count} rows (need >= 1000)"
                )
                passed = False

            row = conn.execute(
                "SELECT 1 FROM symbols "
                "JOIN doc_sets ON doc_sets.id = symbols.doc_set_id "
                "WHERE doc_sets.version = ? "
                "AND symbols.qualified_name = ? LIMIT 1",
                (version, SMOKE_SENTINEL_SYMBOL),
            ).fetchone()
            if row:
                messages.append(
                    f"OK: sentinel: {SMOKE_SENTINEL_SYMBOL} symbol found "
                    f"for version {version}"
                )
            else:
                messages.append(
                    f"FAIL: sentinel: {SMOKE_SENTINEL_SYMBOL} symbol missing "
                    f"for version {version}"
                )
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

            for version in expected_version_list:
                count = conn.execute(
                    "SELECT COUNT(*) FROM documents "
                    "JOIN doc_sets ON doc_sets.id = documents.doc_set_id "
                    "WHERE doc_sets.version = ?",
                    (version,),
                ).fetchone()[0]
                if count >= 10:
                    messages.append(
                        f"OK: documents: version {version} has {count} rows"
                    )
                else:
                    messages.append(
                        f"FAIL: documents: version {version} has {count} rows (need >= 10)"
                    )
                    passed = False

                count = conn.execute(
                    "SELECT COUNT(*) FROM sections "
                    "JOIN documents ON documents.id = sections.document_id "
                    "JOIN doc_sets ON doc_sets.id = documents.doc_set_id "
                    "WHERE doc_sets.version = ?",
                    (version,),
                ).fetchone()[0]
                if count >= 50:
                    messages.append(
                        f"OK: sections: version {version} has {count} rows"
                    )
                else:
                    messages.append(
                        f"FAIL: sections: version {version} has {count} rows (need >= 50)"
                    )
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
                row = conn.execute(
                    'SELECT 1 FROM sections_fts WHERE sections_fts MATCH \'"asyncio"\' LIMIT 1'
                ).fetchone()
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
                row = conn.execute(
                    'SELECT 1 FROM symbols_fts WHERE symbols_fts MATCH \'"asyncio"\' LIMIT 1'
                ).fetchone()
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

    os.replace(new_db_path, target_path)
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
        os.replace(previous, target_path)
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
    4. If passed: atomic swap + restart message
    5. If failed: update run status, return False

    Args:
        build_db_path: Path to the build artifact database.
        version: Version string for the ingestion run record.
        require_content: Whether publish validation should require content tables.

    Returns:
        True if publishing succeeded, False if smoke tests failed.
    """
    # Compute SHA256 (PUBL-02)
    artifact_hash = compute_sha256(build_db_path)
    logger.info("Build artifact SHA256: %s", artifact_hash)

    # Record ingestion run
    from mcp_server_python_docs.storage.db import get_readwrite_connection

    build_notes = "build_mode=symbol_only" if not require_content else None
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
    finally:
        conn.close()

    # Run smoke tests (PUBL-03)
    passed, messages = run_smoke_tests(
        build_db_path,
        require_content=require_content,
        expected_versions=parse_expected_versions(version),
    )
    for msg in messages:
        logger.info("Smoke test: %s", msg)

    if not passed:
        # Update run status to failed
        conn = get_readwrite_connection(build_db_path)
        try:
            conn.execute(
                "UPDATE ingestion_runs SET status = ?, notes = ?, "
                "finished_at = CURRENT_TIMESTAMP WHERE id = ?",
                ("failed", "\n".join(messages), run_id),
            )
            conn.commit()
        finally:
            conn.close()
        logger.error("Smoke tests failed — not publishing")
        return False

    # Update run status to published (preserve build_mode note)
    conn = get_readwrite_connection(build_db_path)
    try:
        conn.execute(
            "UPDATE ingestion_runs SET status = ?, notes = ?, "
            "finished_at = CURRENT_TIMESTAMP WHERE id = ?",
            ("published", build_notes, run_id),
        )
        conn.commit()
    finally:
        conn.close()

    # Atomic swap (PUBL-04)
    atomic_swap(build_db_path)

    # Restart message (PUBL-05)
    print_restart_message()

    return True
