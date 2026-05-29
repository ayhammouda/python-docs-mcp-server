#!/usr/bin/env python
"""Data-shape spike for Phase 09 compare_versions (Plan 09-01, CMPR-01).

This is a standalone, re-runnable evidence builder — NOT a pytest module.
Run it directly on a fresh checkout, offline, with no user-cache dependency:

    uv run python tests/test_compare_versions_spike.py

It builds a reproducible two-version SQLite fixture under a
``tempfile.TemporaryDirectory()`` (mirroring the
``tests/test_multi_version.py::multi_version_db`` pattern) and probes the
candidate regexes that ``services/compare.py`` (Plan 03) will use to extract
``new_in`` / ``changed_in`` / ``deprecated_in`` / ``see_also`` from
``sections.content_text``.

The seeded prose forms are the literal post-markdownify strings that
RESEARCH §Q3(a) (lines 170-184) and §Q4(a) (lines 197-210) document as
surviving the ``markdownify`` call in ``sphinx_json.py:247``. The spike's job
is to LOCK the regex literals against this controlled fixture — not to
rediscover what RESEARCH already proved.

It never touches the user cache: it does not resolve the default index path,
does not invoke the index-build CLI, and does not read or write the
platformdirs cache directory. All data lives in the temp fixture only.
Exits 0 and prints "spike fixture OK" plus per-probe results on success.
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

from mcp_server_python_docs.storage.db import (
    bootstrap_schema,
    get_readwrite_connection,
)

# ── Candidate regex literals (from RESEARCH §Q3(a)-(c) and §Q4(c)) ──────────
# These are the exact strings the spike locks for services/compare.py (Plan 03).
NEW_IN_RE = r"New in version\s+(\d+\.\d+)"
CHANGED_IN_RE = r"Changed in version\s+(\d+\.\d+)"
DEPRECATED_IN_RE = r"Deprecated since version\s+(\d+\.\d+)"
SEE_ALSO_LINK_RE = r"\[([^\]]+)\]\("

# ── Seeded prose forms (verbatim, RESEARCH-documented post-markdownify text) ─
# (slug, anchor, content_text)
_SECTIONS_3_11 = [
    (
        "library/asyncio-task.html",
        "asyncio.TaskGroup",
        "An asynchronous context manager holding a group of tasks.\n\n"
        "New in version 3.11.",
    ),
    (
        "library/asyncio-runner.html",
        "asyncio.run",
        "Execute the coroutine and return the result.\n\n"
        "Changed in version 3.10: Added support for ...",
    ),
    (
        "library/somemodule.html",
        "some.deprecated_func",
        "Old API.\n\nDeprecated since version 3.12: use new_func() instead.",
    ),
    (
        "library/pathlib.html",
        "pathlib.Path",
        "Concrete path classes.\n\nSee also\n\n"
        "[os.path](library/os.path.html) — Operating system path manipulation.\n"
        "[fnmatch](library/fnmatch.html) — Pattern matching.",
    ),
]


def build_fixture(db_path: Path):
    """Build the two-version SQLite fixture and return the open connection.

    Mirrors tests/test_multi_version.py::multi_version_db:
    - doc_set 3.10 (not default), doc_set 3.11 (default)
    - documents + sections carrying the verbatim RESEARCH prose forms
    - FTS rebuild for parity with the real ingestion path
    """
    conn = get_readwrite_connection(db_path)
    bootstrap_schema(conn)

    # 3.10 — not default (presence-delta baseline; carries no seeded directives)
    conn.execute(
        "INSERT INTO doc_sets (source, version, language, label, is_default, base_url) "
        "VALUES ('python-docs', '3.10', 'en', 'Python 3.10', 0, "
        "'https://docs.python.org/3.10/')"
    )
    # 3.11 — default; carries the four probed sections
    conn.execute(
        "INSERT INTO doc_sets (source, version, language, label, is_default, base_url) "
        "VALUES ('python-docs', '3.11', 'en', 'Python 3.11', 1, "
        "'https://docs.python.org/3.11/')"
    )

    ds_311 = conn.execute(
        "SELECT id FROM doc_sets WHERE version = '3.11'"
    ).fetchone()[0]

    for slug, anchor, content_text in _SECTIONS_3_11:
        conn.execute(
            "INSERT INTO documents (doc_set_id, uri, slug, title, content_text, char_count) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ds_311, slug, slug, anchor, content_text, len(content_text)),
        )
        doc_id = conn.execute(
            "SELECT id FROM documents WHERE doc_set_id = ? AND slug = ?",
            (ds_311, slug),
        ).fetchone()[0]
        uri = f"{slug}#{anchor}"
        conn.execute(
            "INSERT INTO sections "
            "(document_id, uri, anchor, heading, level, ordinal, content_text, char_count) "
            "VALUES (?, ?, ?, ?, 2, 0, ?, ?)",
            (doc_id, uri, anchor, anchor, content_text, len(content_text)),
        )

    conn.commit()
    conn.execute("INSERT INTO sections_fts(sections_fts) VALUES('rebuild')")
    conn.commit()
    return conn


def _fetch_section(conn, anchor: str) -> str:
    row = conn.execute(
        "SELECT content_text FROM sections WHERE anchor = ?", (anchor,)
    ).fetchone()
    assert row is not None, f"section not seeded: {anchor}"
    return row[0]


def run_probes(conn) -> list[tuple[str, str, object, bool]]:
    """Run each candidate regex against the seeded prose; return outcomes.

    Each tuple: (probe_label, regex_literal, captured_value, holds_bool).
    """
    results: list[tuple[str, str, object, bool]] = []

    # A1 — versionadded
    text = _fetch_section(conn, "asyncio.TaskGroup")
    m = re.search(NEW_IN_RE, text)
    cap = m.group(1) if m else None
    results.append(("A1 (versionadded)", NEW_IN_RE, cap, cap == "3.11"))

    # Sibling 1 — changed
    text = _fetch_section(conn, "asyncio.run")
    m = re.search(CHANGED_IN_RE, text)
    cap = m.group(1) if m else None
    results.append(("Sibling 1 (changed)", CHANGED_IN_RE, cap, cap == "3.10"))

    # Sibling 2 — deprecated
    text = _fetch_section(conn, "some.deprecated_func")
    m = re.search(DEPRECATED_IN_RE, text)
    cap = m.group(1) if m else None
    results.append(("Sibling 2 (deprecated)", DEPRECATED_IN_RE, cap, cap == "3.12"))

    # A2 — seealso (link labels within the "See also" window)
    text = _fetch_section(conn, "pathlib.Path")
    idx = text.lower().find("see also")
    window = text[idx:] if idx >= 0 else ""
    cap = re.findall(SEE_ALSO_LINK_RE, window)
    results.append(("A2 (seealso)", SEE_ALSO_LINK_RE, cap, cap == ["os.path", "fnmatch"]))

    return results


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "spike.db"
        conn = build_fixture(db_path)
        try:
            print("spike fixture OK")
            results = run_probes(conn)
            all_hold = True
            for label, regex, cap, holds in results:
                status = "HOLDS" if holds else "FALSIFIED"
                all_hold = all_hold and holds
                print(f"  [{status}] {label}: {regex!r} -> {cap!r}")
            if all_hold:
                print("all 4 probes HOLD")
                return 0
            print("FAILURE: one or more probes FALSIFIED", file=sys.stderr)
            return 1
        finally:
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
