"""Shared version resolution logic for all services.

Centralizes version validation and default resolution to avoid
duplication across SearchService and ContentService (CR-01, WR-01).
"""
from __future__ import annotations

import sqlite3

from mcp_server_python_docs.errors import VersionNotFoundError


def validate_version(db: sqlite3.Connection, version: str) -> str:
    """Validate that a version exists in doc_sets. Returns the version string.

    Raises VersionNotFoundError with available versions list if not found (MVER-03).
    """
    row = db.execute(
        "SELECT version FROM doc_sets WHERE version = ?",
        (version,),
    ).fetchone()
    if row is None:
        available = [
            r[0]
            for r in db.execute(
                "SELECT version FROM doc_sets ORDER BY version"
            ).fetchall()
        ]
        raise VersionNotFoundError(
            f"version {version!r} not found; available: {available}"
        )
    return version


def resolve_default_version(db: sqlite3.Connection) -> str:
    """Resolve the default version from doc_sets.

    Prefers is_default=1 row, falls back to highest version.
    Raises VersionNotFoundError if no versions exist.
    """
    row = db.execute(
        "SELECT version FROM doc_sets WHERE is_default = 1 LIMIT 1"
    ).fetchone()
    if row is None:
        row = db.execute(
            "SELECT version FROM doc_sets ORDER BY version DESC LIMIT 1"
        ).fetchone()
    if row is None:
        raise VersionNotFoundError("No versions available in index")
    return row[0]


def resolve_version_strict(db: sqlite3.Connection, version: str | None) -> str:
    """Resolve version to a concrete version string. Never returns None.

    Used by ContentService where a specific version is always needed
    (e.g., to look up a document by slug + version).

    - If version is provided, validates it exists.
    - If version is None, resolves to default version.
    """
    if version is not None:
        return validate_version(db, version)
    return resolve_default_version(db)


def resolve_version_permissive(db: sqlite3.Connection, version: str | None) -> str | None:
    """Resolve version, allowing None to mean 'all versions'.

    Used by SearchService where None means cross-version search
    (intentional design -- LLMs benefit from seeing results across
    all versions so they can compare and pick the right one).

    - If version is provided, validates it exists.
    - If version is None, returns None (search all versions).
    """
    if version is not None:
        return validate_version(db, version)
    return None
