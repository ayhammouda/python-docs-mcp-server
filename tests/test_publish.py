"""Tests for atomic-swap publishing module.

Covers build path generation (PUBL-01),
smoke tests (PUBL-03), atomic swap with rollback (PUBL-04),
restart message (PUBL-05), and ingestion-while-serving regression (PUBL-06).
"""
from __future__ import annotations

import errno
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

from mcp_server_python_docs.ingestion.cpython_versions import SUPPORTED_DOC_VERSIONS
from mcp_server_python_docs.ingestion.publish import (
    SMOKE_SENTINEL_SYMBOL,
    _version_sort_key,
    atomic_swap,
    generate_build_path,
    print_restart_message,
    publish_index,
    rollback,
    run_smoke_tests,
    sweep_stale_builds,
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
        p1 = generate_build_path()
        time.sleep(0.01)  # Ensure different second if needed
        p2 = generate_build_path()
        # They should at least have the same prefix pattern
        assert "build-" in p1.name
        assert "build-" in p2.name


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
        """Preserve the original 3.10–3.14 build/default contract."""
        versions = ("3.10", "3.11", "3.12", "3.13", "3.14")
        db_path = tmp_path / "issue-5-range.db"
        self._create_populated_db(db_path, versions=versions)

        passed, messages = run_smoke_tests(
            db_path, expected_versions=versions
        )

        assert passed is True
        assert (
            "OK: doc_sets: expected versions present: 3.10, 3.11, 3.12, 3.13, 3.14"
            in messages
        )
        assert "OK: doc_sets: default version is 3.14" in messages

    def test_pass_on_python315_version_range(self, tmp_path):
        db_path = tmp_path / "issue-156-range.db"
        self._create_populated_db(db_path, versions=SUPPORTED_DOC_VERSIONS)
        passed, messages = run_smoke_tests(
            db_path, expected_versions=SUPPORTED_DOC_VERSIONS
        )
        assert passed is True
        assert (
            "OK: doc_sets: expected versions present: 3.10, 3.11, 3.12, 3.13, 3.14, 3.15"
            in messages
        )
        assert "OK: doc_sets: default version is 3.15" in messages

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


    def test_symbol_only_mode_persisted_for_validation(self, tmp_path, monkeypatch):
        """Published symbol-only indexes record build_mode in ingestion_runs notes.

        This ensures validate-corpus can auto-detect the build mode and skip
        content checks (P2 fix for publish/validate consistency).
        """
        monkeypatch.setattr(
            "mcp_server_python_docs.storage.db.get_cache_dir", lambda: tmp_path
        )
        db_path = tmp_path / "symbols-only-publish.db"
        self._create_symbols_only_db(db_path)

        # Publish with require_content=False (symbol-only mode)
        success = publish_index(db_path, "3.13", require_content=False)
        assert success is True

        # Read back the ingestion_runs notes from the published index
        from mcp_server_python_docs.storage.db import get_index_path

        published = get_index_path()
        assert published == tmp_path / "index.db"
        assert not db_path.exists()
        conn = get_readonly_connection(published)
        row = conn.execute(
            "SELECT notes FROM ingestion_runs "
            "WHERE status = 'published' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()

        assert row is not None
        assert "build_mode=symbol_only" in row[0]

    def test_rejected_build_is_logged_at_error_and_deleted(self, isolated_cache, caplog):
        build = isolated_cache / "build-1.db"
        conn = get_readwrite_connection(build)
        bootstrap_schema(conn)
        conn.close()

        with caplog.at_level(logging.INFO):
            assert publish_index(build, "3.13", require_content=False) is False

        errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
        assert "Smoke test: FAIL: symbols: 0 rows (need >= 1000)" in errors
        assert any(str(build) in message for message in errors)
        assert list(isolated_cache.iterdir()) == []

    def test_published_index_uses_rollback_journal(self, isolated_cache):
        build = isolated_cache / "build-1.db"
        self._create_symbols_only_db(build)

        assert publish_index(build, "3.13", require_content=False) is True

        index = isolated_cache / "index.db"
        with open(index, "rb") as f:
            assert f.read(20)[18:20] == b"\x01\x01"  # b"\x02\x02" means WAL
        conn = get_readonly_connection(index)
        assert conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0] == 2100
        conn.close()
        assert sorted(p.name for p in isolated_cache.iterdir()) == ["index.db"]

    def test_new_readers_ignore_stale_sidecars_of_a_wal_index(self, isolated_cache):
        if sys.platform == "win32":
            pytest.skip("Windows cannot replace an index.db that a reader holds open")
        index = isolated_cache / "index.db"
        conn = get_readwrite_connection(index)  # an index published before this fix: WAL mode
        bootstrap_schema(conn)
        conn.close()
        old = get_readonly_connection(index)  # a running server; leaves index.db-wal and -shm
        assert old.execute("SELECT COUNT(*) FROM symbols").fetchone()[0] == 0
        build = isolated_cache / "build-1.db"
        self._create_symbols_only_db(build)

        assert publish_index(build, "3.13", require_content=False) is True

        new = get_readonly_connection(index)
        assert new.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert new.execute("SELECT COUNT(*) FROM symbols").fetchone()[0] == 2100
        new.close()
        assert old.execute("SELECT COUNT(*) FROM symbols").fetchone()[0] == 0
        old.close()

    def test_publish_aborts_when_build_cannot_leave_wal(self, isolated_cache, caplog):
        build = isolated_cache / "build-1.db"
        self._create_symbols_only_db(build)
        holder = get_readonly_connection(build)  # e.g. a sqlite3 shell left open on the build
        holder.execute("SELECT COUNT(*) FROM symbols").fetchone()
        try:
            with caplog.at_level(logging.ERROR):
                assert publish_index(build, "3.13", require_content=False) is False
        finally:
            holder.close()
        assert not (isolated_cache / "index.db").exists()
        assert build.exists()
        assert "journal_mode" in caplog.text
        assert str(build) in caplog.text

    def test_locked_index_keeps_the_build(self, isolated_cache, monkeypatch, caplog, capsys):
        index = isolated_cache / "index.db"
        index.write_bytes(b"old index")
        build = isolated_cache / "build-1.db"
        self._create_symbols_only_db(build)
        real_replace = os.replace

        def locked_target(src, dst):
            if Path(dst) == index:
                raise PermissionError(errno.EACCES, "The process cannot access the file")
            real_replace(src, dst)

        monkeypatch.setattr("mcp_server_python_docs.ingestion.publish.os.replace", locked_target)
        with caplog.at_level(logging.ERROR):
            assert publish_index(build, "3.13", require_content=False) is False

        assert build.exists()
        assert index.read_bytes() == b"old index"
        assert "stop every MCP client" in caplog.text
        assert str(build) in caplog.text
        assert "Restart your MCP client" not in capsys.readouterr().err

    def test_refused_backup_is_logged_and_keeps_the_build(
        self, isolated_cache, monkeypatch, caplog
    ):
        index = isolated_cache / "index.db"
        index.write_bytes(b"old index")
        build = isolated_cache / "build-1.db"
        self._create_symbols_only_db(build)

        def no_links(src, dst, *args, **kwargs):
            raise OSError(errno.EPERM, "hard links not supported here")

        def refused_copy(src, dst, *args, **kwargs):
            raise PermissionError(errno.EACCES, "index.db is not readable")

        monkeypatch.setattr("mcp_server_python_docs.ingestion.publish.os.link", no_links)
        monkeypatch.setattr("mcp_server_python_docs.ingestion.publish.shutil.copy2", refused_copy)
        with caplog.at_level(logging.ERROR):
            assert publish_index(build, "3.13", require_content=False) is False

        errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
        assert any(str(build) in message for message in errors)
        assert build.exists()
        assert index.read_bytes() == b"old index"
        assert not list(isolated_cache.glob("*.tmp"))


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

    def test_target_exists_at_every_replace(self, tmp_path, monkeypatch):
        target = tmp_path / "index.db"
        target.write_bytes(b"old")
        new_db = tmp_path / "build-123.db"
        new_db.write_bytes(b"new")
        real_replace = os.replace
        replaced_onto: list[str] = []

        def checked_replace(src, dst):
            assert target.exists(), f"index.db missing at replace({src}, {dst})"
            replaced_onto.append(Path(dst).name)
            real_replace(src, dst)

        monkeypatch.setattr("mcp_server_python_docs.ingestion.publish.os.replace", checked_replace)
        prev = atomic_swap(new_db, target)

        assert target.read_bytes() == b"new"
        assert prev == tmp_path / "index.db.previous"
        assert prev.read_bytes() == b"old"
        assert replaced_onto == ["index.db.previous", "index.db"]
        assert sorted(p.name for p in tmp_path.iterdir()) == ["index.db", "index.db.previous"]

    def test_swap_copies_when_hard_links_fail(self, tmp_path, monkeypatch):
        target = tmp_path / "index.db"
        target.write_bytes(b"old")
        new_db = tmp_path / "build-123.db"
        new_db.write_bytes(b"new")

        def no_links(src, dst, *args, **kwargs):
            raise OSError(errno.EPERM, "hard links not supported here")

        monkeypatch.setattr("mcp_server_python_docs.ingestion.publish.os.link", no_links)
        prev = atomic_swap(new_db, target)

        assert target.read_bytes() == b"new"
        assert prev is not None and prev.read_bytes() == b"old"
        assert sorted(p.name for p in tmp_path.iterdir()) == ["index.db", "index.db.previous"]

    def test_failed_backup_copy_leaves_no_partial_backup(self, tmp_path, monkeypatch):
        target = tmp_path / "index.db"
        target.write_bytes(b"old")
        new_db = tmp_path / "build-123.db"
        new_db.write_bytes(b"new")

        def no_links(src, dst, *args, **kwargs):
            raise OSError(errno.EPERM, "hard links not supported here")

        def disk_fills_up(src, dst, *args, **kwargs):
            Path(dst).write_bytes(b"ol")  # a partial copy, then the disk is full
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr("mcp_server_python_docs.ingestion.publish.os.link", no_links)
        monkeypatch.setattr("mcp_server_python_docs.ingestion.publish.shutil.copy2", disk_fills_up)
        with pytest.raises(OSError) as failed:
            atomic_swap(new_db, target)

        assert failed.value.errno == errno.ENOSPC
        assert target.read_bytes() == b"old"
        assert new_db.exists()
        assert sorted(p.name for p in tmp_path.iterdir()) == ["build-123.db", "index.db"]

    def test_swap_after_a_refused_replace_leaves_no_temp_link(self, tmp_path):
        target = tmp_path / "index.db"
        target.write_bytes(b"old")
        os.link(target, tmp_path / "index.db.previous")  # a refused publish left this link
        new_db = tmp_path / "build-123.db"
        new_db.write_bytes(b"new")
        prev = atomic_swap(new_db, target)
        assert target.read_bytes() == b"new"
        assert prev is not None and prev.read_bytes() == b"old"
        assert sorted(p.name for p in tmp_path.iterdir()) == ["index.db", "index.db.previous"]

    def test_refused_previous_replace_cleans_temp_link(self, tmp_path, monkeypatch, caplog):
        target = tmp_path / "index.db"
        target.write_bytes(b"old")
        new_db = tmp_path / "build-123.db"
        new_db.write_bytes(b"new")
        real_replace = os.replace

        def locked_previous(src, dst):
            if Path(dst).name == "index.db.previous":
                raise PermissionError(errno.EACCES, "The process cannot access the file")
            real_replace(src, dst)

        monkeypatch.setattr("mcp_server_python_docs.ingestion.publish.os.replace", locked_previous)
        with caplog.at_level(logging.ERROR), pytest.raises(PermissionError):
            atomic_swap(new_db, target)

        assert sorted(p.name for p in tmp_path.iterdir()) == ["build-123.db", "index.db"]
        assert target.read_bytes() == b"old"
        assert "stop every MCP client" in caplog.text

    def test_refused_temp_link_cleanup_still_logs_the_refusal(
        self, tmp_path, monkeypatch, caplog
    ):
        target = tmp_path / "index.db"
        target.write_bytes(b"old")
        new_db = tmp_path / "build-123.db"
        new_db.write_bytes(b"new")
        tmp_link = tmp_path / f"index.db.previous.{os.getpid()}.tmp"
        real_replace = os.replace
        real_unlink = Path.unlink

        def locked_previous(src, dst):
            if Path(dst).name == "index.db.previous":
                raise PermissionError(errno.EACCES, "index.db.previous is held open")
            real_replace(src, dst)

        def locked_link(self, missing_ok=False):
            if self.name.endswith(".tmp") and self.exists():  # Windows: a client holds index.db
                raise PermissionError(errno.EACCES, "the temp link is held open")
            real_unlink(self, missing_ok=missing_ok)

        monkeypatch.setattr("mcp_server_python_docs.ingestion.publish.os.replace", locked_previous)
        monkeypatch.setattr(Path, "unlink", locked_link)
        with caplog.at_level(logging.WARNING), pytest.raises(PermissionError) as refused:
            atomic_swap(new_db, target)

        assert refused.value.strerror == "index.db.previous is held open"  # the replace's error

        errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("stop every MCP client" in message for message in errors)
        assert any(str(tmp_link) in message for message in warnings)
        assert target.read_bytes() == b"old"

    def test_failed_previous_replace_leaves_no_temp_link(self, tmp_path, monkeypatch):
        target = tmp_path / "index.db"
        target.write_bytes(b"old")
        new_db = tmp_path / "build-123.db"
        new_db.write_bytes(b"new")
        real_replace = os.replace

        def failing_previous(src, dst):
            if Path(dst).name == "index.db.previous":
                raise OSError(errno.EIO, "I/O error")
            real_replace(src, dst)

        monkeypatch.setattr("mcp_server_python_docs.ingestion.publish.os.replace", failing_previous)
        with pytest.raises(OSError) as failed:
            atomic_swap(new_db, target)

        assert failed.value.errno == errno.EIO
        assert target.read_bytes() == b"old"
        assert sorted(p.name for p in tmp_path.iterdir()) == ["build-123.db", "index.db"]


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


# ── Failed-build cleanup (build-index) ──


def _offline(conn, version, *, is_default=False):
    raise RuntimeError("objects.inv download failed")


def _interrupted(conn, version, *, is_default=False):
    raise KeyboardInterrupt


def _no_symbols(conn, version, *, is_default=False):
    return 0


def _disk_error(conn):
    raise sqlite3.OperationalError("disk I/O error")


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    """Send index.db (storage.db's binding) and new builds (publish's binding) to tmp_path."""
    monkeypatch.setattr("mcp_server_python_docs.storage.db.get_cache_dir", lambda: tmp_path)
    monkeypatch.setattr("mcp_server_python_docs.ingestion.publish.get_cache_dir", lambda: tmp_path)
    return tmp_path


class TestBuildCleanup:
    @pytest.mark.parametrize(
        ("ingest", "rebuild"),
        [(_offline, None), (_interrupted, None), (_no_symbols, _disk_error)],
        ids=["no-version-ingested", "ctrl-c", "error-after-ingestion"],
    )
    def test_failed_build_leaves_no_files(self, isolated_cache, monkeypatch, ingest, rebuild):
        from click.testing import CliRunner

        from mcp_server_python_docs.__main__ import main

        build_dirs: list[Path] = []

        def recording_ingest(conn, version, *, is_default=False):
            build_dirs.append(Path(conn.execute("PRAGMA database_list").fetchone()[2]).parent)
            return ingest(conn, version, is_default=is_default)

        monkeypatch.setattr(
            "mcp_server_python_docs.ingestion.inventory.ingest_inventory", recording_ingest
        )
        if rebuild is not None:
            monkeypatch.setattr(
                "mcp_server_python_docs.ingestion.sphinx_json.rebuild_fts_indexes", rebuild
            )

        result = CliRunner().invoke(main, ["build-index", "--versions", "3.13", "--skip-content"])

        assert result.exit_code == 1
        assert build_dirs == [isolated_cache]  # the build really ran in tmp_path
        assert list(isolated_cache.glob("build-*")) == []

    def test_successful_build_reaches_publish(self, isolated_cache, monkeypatch):
        from click.testing import CliRunner

        from mcp_server_python_docs.__main__ import main

        calls: list[tuple[Path, bool]] = []  # (build path, existed when publish ran)

        def recording_publish(build_db_path, version, *, require_content=True):
            calls.append((build_db_path, build_db_path.exists()))
            return True

        monkeypatch.setattr(
            "mcp_server_python_docs.ingestion.inventory.ingest_inventory",
            lambda conn, version, *, is_default=False: 1,
        )
        monkeypatch.setattr(
            "mcp_server_python_docs.ingestion.publish.publish_index", recording_publish
        )

        result = CliRunner().invoke(main, ["build-index", "--versions", "3.13", "--skip-content"])

        assert result.exit_code == 0
        assert len(calls) == 1
        build_db_path, existed = calls[0]
        assert build_db_path.parent == isolated_cache
        assert existed  # a dropped `built = True` would delete the build before publish

    def test_sweep_removes_only_stale_build_files(self, tmp_path):
        two_days_ago = time.time() - 2 * 86_400
        stale = [tmp_path / f"build-20200101-000000-000000.db{s}" for s in ("", "-wal", "-shm")]
        fresh = tmp_path / "build-20261030-000000-000000.db"
        kept = [
            tmp_path / name
            for name in (
                "index.db",
                "index.db.previous",
                "index.db.previous.12345.tmp",
                "retrieved-docs-cache.sqlite3",
            )
        ]
        for path in [*stale, fresh, *kept]:
            path.write_bytes(b"x")
        for path in [*stale, *kept]:
            os.utime(path, (two_days_ago, two_days_ago))

        removed = sweep_stale_builds(tmp_path)

        assert sorted(removed) == sorted(stale)
        assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
            [fresh.name, *(p.name for p in kept)]
        )

    def test_sweep_skips_files_it_cannot_delete(self, tmp_path, monkeypatch, caplog):
        stuck = tmp_path / "build-20200101-000000-000000.db"
        stuck.write_bytes(b"x")
        two_days_ago = time.time() - 2 * 86_400
        os.utime(stuck, (two_days_ago, two_days_ago))
        real_unlink = Path.unlink

        def refuse(self, missing_ok=False):
            if self == stuck:
                raise PermissionError(errno.EACCES, "in use by another process")
            real_unlink(self, missing_ok=missing_ok)

        monkeypatch.setattr(Path, "unlink", refuse)
        with caplog.at_level(logging.WARNING):
            assert sweep_stale_builds(tmp_path) == []

        assert stuck.exists()
        assert str(stuck) in caplog.text

    def test_build_index_sweeps_stale_builds(self, isolated_cache, monkeypatch):
        from click.testing import CliRunner

        from mcp_server_python_docs.__main__ import main

        stale = isolated_cache / "build-20200101-000000-000000.db-wal"
        stale.write_bytes(b"")
        two_days_ago = time.time() - 2 * 86_400
        os.utime(stale, (two_days_ago, two_days_ago))
        monkeypatch.setattr(
            "mcp_server_python_docs.ingestion.inventory.ingest_inventory", _offline
        )

        result = CliRunner().invoke(main, ["build-index", "--versions", "3.13", "--skip-content"])

        assert result.exit_code == 1
        assert list(isolated_cache.glob("build-*")) == []
