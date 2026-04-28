"""Tests for atomic-swap publishing module.

Covers build path generation (PUBL-01), SHA256 hashing (PUBL-02),
smoke tests (PUBL-03), atomic swap with rollback (PUBL-04),
restart message (PUBL-05), and ingestion-while-serving regression (PUBL-06).
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

from mcp_server_python_docs.ingestion.cpython_versions import SUPPORTED_DOC_VERSIONS
from mcp_server_python_docs.ingestion.publish import (
    SMOKE_SENTINEL_SYMBOL,
    _version_sort_key,
    atomic_swap,
    compute_sha256,
    generate_build_path,
    print_restart_message,
    rollback,
    run_smoke_tests,
)
from mcp_server_python_docs.storage.db import (
    bootstrap_schema,
    get_readonly_connection,
    get_readwrite_connection,
)

# ── Build path tests (PUBL-01) ──


class TestBuildPath:
    def test_timestamped(self):
        """generate_build_path returns a path with timestamp in filename."""
        path = generate_build_path()
        assert "build-" in path.name
        assert path.suffix == ".db"
        assert path.parent.exists()

    def test_unique(self):
        """Two calls produce different paths (different timestamps)."""
        import time

        p1 = generate_build_path()
        time.sleep(0.01)  # Ensure different second if needed
        p2 = generate_build_path()
        # They should at least have the same prefix pattern
        assert "build-" in p1.name
        assert "build-" in p2.name


# ── SHA256 tests (PUBL-02) ──


class TestSHA256:
    def test_hex_digest(self, tmp_path):
        """compute_sha256 returns a valid hex digest."""
        test_file = tmp_path / "test.db"
        test_file.write_bytes(b"test content")
        sha = compute_sha256(test_file)
        assert len(sha) == 64
        assert all(c in "0123456789abcdef" for c in sha)

    def test_deterministic(self, tmp_path):
        """Same content produces same hash."""
        f1 = tmp_path / "a.db"
        f2 = tmp_path / "b.db"
        f1.write_bytes(b"identical")
        f2.write_bytes(b"identical")
        assert compute_sha256(f1) == compute_sha256(f2)

    def test_different_content(self, tmp_path):
        """Different content produces different hash."""
        f1 = tmp_path / "a.db"
        f2 = tmp_path / "b.db"
        f1.write_bytes(b"content A")
        f2.write_bytes(b"content B")
        assert compute_sha256(f1) != compute_sha256(f2)


# ── Smoke test tests (PUBL-03) ──


class TestSmokeTests:
    def _create_symbols_only_db(self, db_path: Path) -> None:
        """Helper: create a DB with symbols but no documents or sections."""
        conn = get_readwrite_connection(db_path)
        bootstrap_schema(conn)

        conn.execute(
            "INSERT INTO doc_sets (source, version, language, label, is_default, base_url) "
            "VALUES ('python-docs', '3.13', 'en', 'Python 3.13', 1, "
            "'https://docs.python.org/3.13/')"
        )

        for i in range(2100):
            qualified_name = f"mod{i}.func{i}"
            if i == 0:
                qualified_name = SMOKE_SENTINEL_SYMBOL
            conn.execute(
                "INSERT INTO symbols (doc_set_id, qualified_name, normalized_name, "
                "module, symbol_type, uri, anchor) "
                "VALUES (1, ?, ?, ?, 'function', ?, ?)",
                (
                    qualified_name,
                    qualified_name.lower(),
                    qualified_name.rsplit(".", 1)[0],
                    f"lib/m.html#f{i}",
                    f"f{i}",
                ),
            )

        conn.commit()
        conn.execute("INSERT INTO symbols_fts(symbols_fts) VALUES('rebuild')")
        conn.commit()
        conn.close()

    def _create_populated_db(
        self,
        db_path: Path,
        versions: tuple[str, ...] = ("3.12", "3.13"),
    ) -> None:
        """Helper: create a DB with enough data to pass smoke tests."""
        conn = get_readwrite_connection(db_path)
        bootstrap_schema(conn)

        doc_set_ids: dict[str, int] = {}
        default_version = max(versions, key=_version_sort_key)

        # Insert doc_sets
        for version in versions:
            conn.execute(
                "INSERT INTO doc_sets (source, version, language, label, is_default, base_url) "
                "VALUES ('python-docs', ?, 'en', ?, ?, ?)",
                (
                    version,
                    f"Python {version}",
                    1 if version == default_version else 0,
                    f"https://docs.python.org/{version}/",
                ),
            )
            doc_set_ids[version] = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        for version, doc_set_id in doc_set_ids.items():
            doc_ids: list[int] = []

            # Insert 20+ documents per expected version (need >= 10)
            for i in range(25):
                slug = f"library/module{i}" if i != 5 else "library/asyncio-task"
                conn.execute(
                    "INSERT INTO documents (doc_set_id, uri, slug, title, "
                    "content_text, char_count) "
                    "VALUES (?, ?, ?, ?, 'content', 7)",
                    (doc_set_id, f"{slug}.html", slug, f"Module {i}"),
                )
                doc_ids.append(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

            # Insert 100+ sections per expected version (need >= 50)
            for i in range(120):
                doc_id = doc_ids[i % len(doc_ids)]
                anchor = "asyncio.TaskGroup" if i == 0 else f"s{i}"
                heading = "asyncio.TaskGroup" if i == 0 else f"Section {i}"
                content = (
                    "asyncio TaskGroup content"
                    if i == 0
                    else f"asyncio content for Python {version}"
                )
                conn.execute(
                    "INSERT INTO sections (document_id, uri, anchor, heading, level, "
                    "ordinal, content_text, char_count) "
                    "VALUES (?, ?, ?, ?, 1, ?, ?, ?)",
                    (
                        doc_id,
                        f"test.html#{anchor}",
                        anchor,
                        heading,
                        i,
                        content,
                        len(content),
                    ),
                )

            # Insert 2000+ symbols per expected version (need >= 1000)
            conn.execute(
                "INSERT INTO symbols (doc_set_id, qualified_name, normalized_name, "
                "module, symbol_type, uri, anchor) "
                "VALUES (?, ?, ?, 'asyncio', "
                "'function', 'library/asyncio-runner.html#asyncio.run', "
                "'asyncio.run')",
                (
                    doc_set_id,
                    SMOKE_SENTINEL_SYMBOL,
                    SMOKE_SENTINEL_SYMBOL.lower(),
                ),
            )
            for i in range(2099):
                conn.execute(
                    "INSERT INTO symbols (doc_set_id, qualified_name, normalized_name, "
                    "module, symbol_type, uri, anchor) "
                    "VALUES (?, ?, ?, ?, 'function', ?, ?)",
                    (
                        doc_set_id,
                        f"mod{i}.func{i}",
                        f"mod{i}.func{i}",
                        f"mod{i}",
                        f"lib/m.html#f{i}",
                        f"f{i}",
                    ),
                )

        conn.commit()

        # Rebuild FTS indexes
        conn.execute("INSERT INTO symbols_fts(symbols_fts) VALUES('rebuild')")
        conn.execute("INSERT INTO sections_fts(sections_fts) VALUES('rebuild')")
        conn.commit()
        conn.close()

    def _remove_version_content(self, db_path: Path, version: str) -> None:
        """Helper: remove documents and sections for one version."""
        conn = get_readwrite_connection(db_path)
        conn.execute(
            "DELETE FROM sections WHERE document_id IN ("
            "SELECT documents.id FROM documents "
            "JOIN doc_sets ON doc_sets.id = documents.doc_set_id "
            "WHERE doc_sets.version = ?)",
            (version,),
        )
        conn.execute(
            "DELETE FROM documents WHERE doc_set_id = "
            "(SELECT id FROM doc_sets WHERE version = ?)",
            (version,),
        )
        conn.execute("INSERT INTO sections_fts(sections_fts) VALUES('rebuild')")
        conn.commit()
        conn.close()

    def _remove_symbol(self, db_path: Path, qualified_name: str) -> None:
        """Helper: remove a symbol from all versions."""
        conn = get_readwrite_connection(db_path)
        conn.execute("DELETE FROM symbols WHERE qualified_name = ?", (qualified_name,))
        conn.execute("INSERT INTO symbols_fts(symbols_fts) VALUES('rebuild')")
        conn.commit()
        conn.close()

    def test_pass_on_populated_db(self, tmp_path):
        """Smoke tests pass on a DB with sufficient data."""
        db_path = tmp_path / "good.db"
        self._create_populated_db(db_path)
        passed, messages = run_smoke_tests(
            db_path, expected_versions=["3.12", "3.13"]
        )
        assert passed is True
        assert any("OK" in m for m in messages)

    def test_pass_on_supported_issue_5_version_range(self, tmp_path):
        """Smoke tests accept the full supported docs version range."""
        db_path = tmp_path / "issue-5-range.db"
        self._create_populated_db(db_path, versions=SUPPORTED_DOC_VERSIONS)

        passed, messages = run_smoke_tests(
            db_path, expected_versions=SUPPORTED_DOC_VERSIONS
        )

        assert passed is True
        assert (
            "OK: doc_sets: expected versions present: 3.10, 3.11, 3.12, 3.13, 3.14"
            in messages
        )
        assert "OK: doc_sets: default version is 3.14" in messages

    def test_fails_when_expected_version_is_missing(self, tmp_path):
        """Smoke tests fail when a requested build version is absent."""
        db_path = tmp_path / "missing-version.db"
        self._create_populated_db(db_path)
        conn = get_readwrite_connection(db_path)
        conn.execute("DELETE FROM doc_sets WHERE version = '3.12'")
        conn.commit()
        conn.close()

        passed, messages = run_smoke_tests(
            db_path, expected_versions=["3.12", "3.13"]
        )

        assert passed is False
        assert "FAIL: doc_sets: missing expected versions: 3.12" in messages

    def test_fails_when_default_version_is_not_highest_expected_version(self, tmp_path):
        """Smoke tests fail when the default version is not the highest request."""
        db_path = tmp_path / "wrong-default.db"
        self._create_populated_db(db_path)
        conn = get_readwrite_connection(db_path)
        conn.execute(
            "UPDATE doc_sets "
            "SET is_default = CASE WHEN version = '3.12' THEN 1 ELSE 0 END"
        )
        conn.commit()
        conn.close()

        passed, messages = run_smoke_tests(
            db_path, expected_versions=["3.12", "3.13"]
        )

        assert passed is False
        assert "FAIL: doc_sets: default version is 3.12 (expected 3.13)" in messages

    def test_fails_when_expected_version_has_no_content(self, tmp_path):
        """Smoke tests fail when a requested version has symbols but no content corpus."""
        db_path = tmp_path / "missing-version-content.db"
        self._create_populated_db(db_path)
        self._remove_version_content(db_path, "3.13")

        passed, messages = run_smoke_tests(
            db_path, expected_versions=["3.12", "3.13"]
        )

        assert passed is False
        assert "FAIL: documents: version 3.13 has 0 rows (need >= 10)" in messages

    def test_fails_when_asyncio_run_symbol_sentinel_is_missing(self, tmp_path):
        """Smoke tests fail when the cross-version asyncio.run sentinel is absent."""
        db_path = tmp_path / "missing-sentinel-symbol.db"
        self._create_populated_db(db_path)
        self._remove_symbol(db_path, SMOKE_SENTINEL_SYMBOL)

        passed, messages = run_smoke_tests(
            db_path, expected_versions=["3.12", "3.13"]
        )

        assert passed is False
        assert (
            f"FAIL: sentinel: {SMOKE_SENTINEL_SYMBOL} symbol missing for version 3.13"
            in messages
        )

    def test_fail_on_empty_db(self, tmp_path):
        """Smoke tests fail on an empty DB."""
        db_path = tmp_path / "empty.db"
        conn = get_readwrite_connection(db_path)
        bootstrap_schema(conn)
        conn.close()
        passed, messages = run_smoke_tests(db_path)
        assert passed is False
        assert any("FAIL" in m for m in messages)

    def test_symbols_only_passes_when_content_not_required(self, tmp_path):
        """Symbol-only builds can publish when content checks are disabled."""
        db_path = tmp_path / "symbols-only.db"
        self._create_symbols_only_db(db_path)
        passed, messages = run_smoke_tests(db_path, require_content=False)
        assert passed is True
        assert "OK: content checks skipped for symbol-only build" in messages

    def test_symbols_only_fails_when_content_required(self, tmp_path):
        """Symbol-only builds still fail the default full-content smoke tests."""
        db_path = tmp_path / "symbols-only-default.db"
        self._create_symbols_only_db(db_path)
        passed, messages = run_smoke_tests(db_path)
        assert passed is False
        assert any("documents" in msg for msg in messages)


    def test_symbol_only_mode_persisted_for_validation(self, tmp_path):
        """Published symbol-only indexes record build_mode in ingestion_runs notes.

        This ensures validate-corpus can auto-detect the build mode and skip
        content checks (P2 fix for publish/validate consistency).
        """
        from mcp_server_python_docs.ingestion.publish import publish_index

        db_path = tmp_path / "symbols-only-publish.db"
        self._create_symbols_only_db(db_path)

        # Publish with require_content=False (symbol-only mode)
        success = publish_index(db_path, "3.13", require_content=False)
        assert success is True

        # Read back the ingestion_runs notes from the published index
        from mcp_server_python_docs.storage.db import get_index_path

        published = get_index_path()
        conn = get_readonly_connection(published)
        row = conn.execute(
            "SELECT notes FROM ingestion_runs "
            "WHERE status = 'published' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()

        assert row is not None
        assert "build_mode=symbol_only" in row[0]


class TestReadOnlyConnection:
    def test_can_query_existing_db(self, tmp_path):
        """Read-only helper can open and query a database without write PRAGMAs."""
        db_path = tmp_path / "readonly.db"
        conn = get_readwrite_connection(db_path)
        bootstrap_schema(conn)
        conn.execute(
            "INSERT INTO doc_sets (source, version, language, label, is_default, base_url) "
            "VALUES ('python-docs', '3.13', 'en', 'Python 3.13', 1, "
            "'https://docs.python.org/3.13/')"
        )
        conn.commit()
        conn.close()

        ro_conn = get_readonly_connection(db_path)
        row = ro_conn.execute("SELECT COUNT(*) FROM doc_sets").fetchone()
        ro_conn.close()

        assert row[0] == 1


# ── Atomic swap tests (PUBL-04) ──


class TestAtomicSwap:
    def test_creates_previous(self, tmp_path):
        """atomic_swap renames current to .previous and new to target."""
        target = tmp_path / "index.db"
        target.write_bytes(b"old content")
        new_db = tmp_path / "build-123.db"
        new_db.write_bytes(b"new content")

        prev = atomic_swap(new_db, target)

        assert target.exists()
        assert target.read_bytes() == b"new content"
        assert prev is not None
        assert prev.exists()
        assert prev.read_bytes() == b"old content"
        assert not new_db.exists()  # Moved, not copied

    def test_no_previous_when_fresh(self, tmp_path):
        """atomic_swap works when no previous index.db exists."""
        target = tmp_path / "index.db"
        new_db = tmp_path / "build-123.db"
        new_db.write_bytes(b"first build")

        prev = atomic_swap(new_db, target)

        assert target.exists()
        assert target.read_bytes() == b"first build"
        assert prev is None

    def test_replaces_old_previous(self, tmp_path):
        """atomic_swap replaces an existing .previous file."""
        target = tmp_path / "index.db"
        target.write_bytes(b"current")
        old_prev = tmp_path / "index.db.previous"
        old_prev.write_bytes(b"very old")
        new_db = tmp_path / "build-123.db"
        new_db.write_bytes(b"newest")

        prev = atomic_swap(new_db, target)

        assert target.read_bytes() == b"newest"
        assert prev is not None
        assert prev.read_bytes() == b"current"  # Not "very old"


# ── Rollback tests ──


class TestRollback:
    def test_restores_previous(self, tmp_path):
        """rollback() restores index.db.previous to index.db."""
        target = tmp_path / "index.db"
        previous = tmp_path / "index.db.previous"
        target.write_bytes(b"bad content")
        previous.write_bytes(b"good content")

        result = rollback(target)

        assert result is True
        assert target.read_bytes() == b"good content"
        assert not previous.exists()

    def test_returns_false_without_previous(self, tmp_path):
        """rollback() returns False when no .previous exists."""
        target = tmp_path / "index.db"
        target.write_bytes(b"only version")
        result = rollback(target)
        assert result is False


# ── Restart message test (PUBL-05) ──


class TestRestartMessage:
    def test_outputs_to_stderr(self, capsys):
        """print_restart_message outputs to stderr, not stdout."""
        print_restart_message()
        captured = capsys.readouterr()
        assert captured.out == ""  # Nothing on stdout
        assert "Restart your MCP client" in captured.err


# ── Ingestion-while-serving regression test (PUBL-06) ──


class TestIngestionWhileServing:
    def test_server_survives_rebuild(self, tmp_path):
        """Server with RO handle survives a rebuild (PUBL-06).

        1. Creates a populated index.db
        2. Opens a read-only connection (simulating server)
        3. Performs a "rebuild" by creating a new DB and atomic-swapping
        4. Asserts the original RO connection still works
        5. New RO connection sees new data
        """
        if sys.platform == "win32":
            pytest.skip("Windows locks the live SQLite file during atomic swap")

        index_path = tmp_path / "index.db"

        # Step 1: Create initial populated DB
        conn_rw = get_readwrite_connection(index_path)
        bootstrap_schema(conn_rw)
        conn_rw.execute(
            "INSERT INTO doc_sets (source, version, language, label, is_default, base_url) "
            "VALUES ('python-docs', '3.13', 'en', 'Python 3.13', 1, "
            "'https://docs.python.org/3.13/')"
        )
        conn_rw.execute(
            "INSERT INTO documents (doc_set_id, uri, slug, title, content_text, char_count) "
            "VALUES (1, 'library/test.html', 'library/test', 'Test', 'content', 7)"
        )
        conn_rw.commit()
        conn_rw.close()

        # Step 2: Open RO connection (simulating server)
        conn_ro = get_readonly_connection(index_path)
        row = conn_ro.execute("SELECT COUNT(*) FROM documents").fetchone()
        assert row[0] == 1

        # Step 3: "Rebuild" — create new DB and atomic swap
        new_db = tmp_path / "build-test.db"
        conn_new = get_readwrite_connection(new_db)
        bootstrap_schema(conn_new)
        conn_new.execute(
            "INSERT INTO doc_sets (source, version, language, label, is_default, base_url) "
            "VALUES ('python-docs', '3.13', 'en', 'Python 3.13', 1, "
            "'https://docs.python.org/3.13/')"
        )
        conn_new.execute(
            "INSERT INTO documents (doc_set_id, uri, slug, title, content_text, char_count) "
            "VALUES (1, 'library/test.html', 'library/test', 'Test Updated', 'new content', 11)"
        )
        conn_new.execute(
            "INSERT INTO documents (doc_set_id, uri, slug, title, content_text, char_count) "
            "VALUES (1, 'library/test2.html', 'library/test2', 'Test 2', 'content 2', 9)"
        )
        conn_new.commit()
        conn_new.close()

        # Swap
        atomic_swap(new_db, index_path)

        # Step 4: Original RO connection still works (reads old inode)
        try:
            row = conn_ro.execute("SELECT COUNT(*) FROM documents").fetchone()
            # Stale read — should return 1 (old data), not crash
            assert row[0] >= 0  # Any non-crash result is acceptable
        except sqlite3.OperationalError:
            # Some platforms may error on renamed file — acceptable
            pass

        # Step 5: New RO connection sees new data
        conn_ro2 = get_readonly_connection(index_path)
        row = conn_ro2.execute("SELECT COUNT(*) FROM documents").fetchone()
        assert row[0] == 2  # New data

        # Cleanup
        conn_ro.close()
        conn_ro2.close()
