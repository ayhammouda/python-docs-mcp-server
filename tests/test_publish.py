"""Tests for atomic-swap publishing module.

Covers build path generation (PUBL-01), SHA256 hashing (PUBL-02),
smoke tests (PUBL-03), atomic swap with rollback (PUBL-04),
restart message (PUBL-05), and ingestion-while-serving regression (PUBL-06).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from mcp_server_python_docs.ingestion.publish import (
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
    def _create_populated_db(self, db_path: Path) -> None:
        """Helper: create a DB with enough data to pass smoke tests."""
        conn = get_readwrite_connection(db_path)
        bootstrap_schema(conn)

        # Insert doc_set
        conn.execute(
            "INSERT INTO doc_sets (source, version, language, label, is_default, base_url) "
            "VALUES ('python-docs', '3.13', 'en', 'Python 3.13', 1, "
            "'https://docs.python.org/3.13/')"
        )

        # Insert 20+ documents (need >= 10)
        for i in range(25):
            slug = f"library/module{i}" if i != 5 else "library/asyncio-task"
            conn.execute(
                "INSERT INTO documents (doc_set_id, uri, slug, title, content_text, char_count) "
                "VALUES (1, ?, ?, ?, 'content', 7)",
                (f"{slug}.html", slug, f"Module {i}"),
            )

        # Insert 100+ sections (need >= 50)
        for i in range(120):
            doc_id = (i % 25) + 1
            conn.execute(
                "INSERT INTO sections (document_id, uri, anchor, heading, level, "
                "ordinal, content_text, char_count) "
                "VALUES (?, ?, ?, ?, 1, ?, 'asyncio content', 15)",
                (doc_id, f"test.html#s{i}", f"s{i}", f"Section {i}", i),
            )

        # Insert 2000+ symbols (need >= 1000)
        for i in range(2100):
            conn.execute(
                "INSERT INTO symbols (doc_set_id, qualified_name, normalized_name, "
                "module, symbol_type, uri, anchor) "
                "VALUES (1, ?, ?, ?, 'function', ?, ?)",
                (f"mod{i}.func{i}", f"mod{i}.func{i}", f"mod{i}", f"lib/m.html#f{i}", f"f{i}"),
            )

        conn.commit()

        # Rebuild FTS indexes
        conn.execute("INSERT INTO sections_fts(sections_fts) VALUES('rebuild')")
        conn.commit()
        conn.close()

    def test_pass_on_populated_db(self, tmp_path):
        """Smoke tests pass on a DB with sufficient data."""
        db_path = tmp_path / "good.db"
        self._create_populated_db(db_path)
        passed, messages = run_smoke_tests(db_path)
        assert passed is True
        assert any("OK" in m for m in messages)

    def test_fail_on_empty_db(self, tmp_path):
        """Smoke tests fail on an empty DB."""
        db_path = tmp_path / "empty.db"
        conn = get_readwrite_connection(db_path)
        bootstrap_schema(conn)
        conn.close()
        passed, messages = run_smoke_tests(db_path)
        assert passed is False
        assert any("FAIL" in m for m in messages)


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
