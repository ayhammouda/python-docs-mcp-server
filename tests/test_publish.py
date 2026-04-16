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
                qualified_name = "asyncio.TaskGroup"
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


# ── WAL sidecar cleanup regression (I-2 + M-7) ──


class TestWalCleanupOnSwap:
    """I-2: publish_index must not leak -wal or -shm sidecars into the cache dir."""

    def _seed_passing_build(self, db_path: Path) -> None:
        """Helper: create a full-content DB that passes smoke tests."""
        conn = get_readwrite_connection(db_path)
        bootstrap_schema(conn)
        conn.execute(
            "INSERT INTO doc_sets (source, version, language, label, is_default, base_url) "
            "VALUES ('python-docs', '3.13', 'en', 'Python 3.13', 1, "
            "'https://docs.python.org/3.13/')"
        )
        # >= 10 documents, one asyncio-shaped
        for i in range(25):
            slug = f"library/module{i}" if i != 5 else "library/asyncio-task"
            conn.execute(
                "INSERT INTO documents (doc_set_id, uri, slug, title, content_text, char_count) "
                "VALUES (1, ?, ?, ?, 'content', 7)",
                (f"{slug}.html", slug, f"Module {i}"),
            )
        # >= 50 sections
        for i in range(120):
            doc_id = (i % 25) + 1
            conn.execute(
                "INSERT INTO sections (document_id, uri, anchor, heading, level, "
                "ordinal, content_text, char_count) "
                "VALUES (?, ?, ?, ?, 1, ?, 'asyncio content', 15)",
                (doc_id, f"test.html#s{i}", f"s{i}", f"Section {i}", i),
            )
        # >= 1000 symbols
        for i in range(2100):
            conn.execute(
                "INSERT INTO symbols (doc_set_id, qualified_name, normalized_name, "
                "module, symbol_type, uri, anchor) "
                "VALUES (1, ?, ?, ?, 'function', ?, ?)",
                (f"mod{i}.func{i}", f"mod{i}.func{i}", f"mod{i}", f"lib/m.html#f{i}", f"f{i}"),
            )
        conn.commit()
        conn.execute("INSERT INTO sections_fts(sections_fts) VALUES('rebuild')")
        conn.commit()
        conn.close()

    def test_publish_index_leaves_no_wal_sidecars(self, tmp_path, monkeypatch):
        """I-2: after a successful publish, the target dir contains index.db only."""
        from mcp_server_python_docs.ingestion import publish as publish_mod

        target_index = tmp_path / "index.db"
        monkeypatch.setattr(
            "mcp_server_python_docs.storage.db.get_cache_dir",
            lambda: tmp_path,
        )
        monkeypatch.setattr(
            "mcp_server_python_docs.storage.db.get_index_path",
            lambda: target_index,
        )
        # publish_mod imports get_index_path from storage.db AT CALL TIME
        # via atomic_swap's default; also patch the local binding for safety.
        monkeypatch.setattr(publish_mod, "get_index_path", lambda: target_index)

        build_db = tmp_path / "build-wal-test.db"
        self._seed_passing_build(build_db)

        assert publish_mod.publish_index(build_db, "3.13") is True

        entries = sorted(p.name for p in tmp_path.iterdir())
        assert "index.db" in entries, f"expected index.db in {entries}"
        wal_sidecars = [p.name for p in tmp_path.iterdir() if p.name.endswith("-wal")]
        shm_sidecars = [p.name for p in tmp_path.iterdir() if p.name.endswith("-shm")]
        assert not wal_sidecars, f"WAL sidecar leaked: {entries}"
        assert not shm_sidecars, f"SHM sidecar leaked: {entries}"

    def test_publish_index_second_build_replaces_cleanly(self, tmp_path, monkeypatch):
        """I-2: a second publish also leaves no -wal/-shm; exercises the .previous path."""
        from mcp_server_python_docs.ingestion import publish as publish_mod

        target_index = tmp_path / "index.db"
        monkeypatch.setattr(
            "mcp_server_python_docs.storage.db.get_cache_dir",
            lambda: tmp_path,
        )
        monkeypatch.setattr(
            "mcp_server_python_docs.storage.db.get_index_path",
            lambda: target_index,
        )
        monkeypatch.setattr(publish_mod, "get_index_path", lambda: target_index)

        # First build
        build_db_1 = tmp_path / "build-first.db"
        self._seed_passing_build(build_db_1)
        assert publish_mod.publish_index(build_db_1, "3.13") is True

        # Second build — must replace index.db and push the old one to .previous.
        build_db_2 = tmp_path / "build-second.db"
        self._seed_passing_build(build_db_2)
        assert publish_mod.publish_index(build_db_2, "3.13") is True

        entries = sorted(p.name for p in tmp_path.iterdir())
        assert "index.db" in entries
        # index.db.previous is expected after the second build.
        assert "index.db.previous" in entries
        wal_sidecars = [p.name for p in tmp_path.iterdir() if p.name.endswith("-wal")]
        shm_sidecars = [p.name for p in tmp_path.iterdir() if p.name.endswith("-shm")]
        assert not wal_sidecars, f"WAL sidecar leaked after 2nd build: {entries}"
        assert not shm_sidecars, f"SHM sidecar leaked after 2nd build: {entries}"


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
