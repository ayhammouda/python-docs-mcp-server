# Build and Publish Hygiene Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A failed `build-index` cleans up after itself, and builds that were
killed mid-way are swept a day later. While a new index is published,
`index.db` stays present on POSIX systems, and servers that open the new
index read-only no longer create `-wal` and `-shm` files.

**Architecture:** One PR on `fix/build-publish-hygiene` (roadmap PR 4). All
changes sit in `build_index` (`__main__.py`) and `ingestion/publish.py`.
- Two small helpers in `publish.py` do the cleanup: `delete_build` and
  `sweep_stale_builds`.
- `publish_index` deletes a build that fails its smoke tests, and switches a
  passing build to `journal_mode = DELETE` in its last read-write step.
- `atomic_swap` keeps `index.db` present throughout, by hard-linking it to
  `.previous` through a temporary name.

Task 1 first isolates the one test that ran `build-index` against the
developer's real cache folder, so no later gate run can sweep real files. No
forbidden territory is touched, but the PR still gets the `supervisor-review`
label (see Global Constraints).

**Tech Stack:**
- Python ≥ 3.12, stdlib `sqlite3` (SQLite 3.50.4 locally)
- `os.link`, `os.replace`, `shutil.copy2`
- Click 8.4 (`click.testing.CliRunner`)
- pytest (`monkeypatch`, `caplog`, `capsys`), Ruff, Pyright, uv

**Spec:**
- `docs/research/2026-10-30-audit-fix-best-practices.md`, section
  "Ingestion, storage and runtime": items 3 (finding 13 and row N5) and 4 (the
  `index.db` gap).
- `docs/plans/2026-10-30-audit-fixes-roadmap.md`: decision D8, PR map row 4,
  Global Constraints.

## Global Constraints

The roadmap's Global Constraints apply. For this PR:

- **Precondition:** `docs/plans/2026-10-30-*.md` and
  `docs/research/2026-10-30-audit-fix-best-practices.md` are on `main`; stop if
  not. Check: `git ls-tree --name-only main docs/plans/ docs/research/`.
  Expected: the list includes
  `docs/research/2026-10-30-audit-fix-best-practices.md`,
  `docs/plans/2026-10-30-audit-fixes-roadmap.md` and this file.
- **Base:** `main` after #162 merges; branch `fix/build-publish-hygiene`.
- **Merge order (recommended):** PR 4 first, because Task 1 stops the test
  suite from writing into the developer's real cache. Then PR 1, PR 3 and PR 5.
  The last of these to land refreshes the DESIGN.md `(line N)` references.
- **No forbidden territory:** no change to `schema.sql`, the tool surface,
  `pyproject.toml` or workflows. No existing test is deleted or weakened; one
  is changed (Task 1 adds cache isolation and one assertion to
  `test_build_index_bad_version_stderr_only`).
- **Supervisor review anyway:** the PR gets the `supervisor-review` label.
  Its "Why this approach" cites design choices that D8 and the research do not
  make (pipeline §7, last row): Rulings 1, 3 and 4. Its
  `## Why this triggered supervisor review` section cites: "The owner (Aymen
  Hammouda) asked on 2026-10-30 to implement all audit findings; decisions in
  `docs/plans/2026-10-30-audit-fixes-roadmap.md`."
- **Readers stay on `?mode=ro`.** Never add `immutable=1` (D8).
- **Keep the contracts:** `rollback()` is unchanged.
  `atomic_swap(new_db_path, target_path=None) -> Path | None` keeps its
  signature and still returns the `.previous` path, or `None` when there was
  no index.
- **No build lock.** The research defers it until concurrent builds are
  common.
- **Test isolation:** patch both cache bindings.
  - `mcp_server_python_docs.storage.db.get_cache_dir` is read by
    `get_index_path()`.
  - `mcp_server_python_docs.ingestion.publish.get_cache_dir` is read by
    `generate_build_path()`.
  - `test_symbol_only_mode_persisted_for_validation` patches only the first.
    A CLI build would then run in the real cache folder, and a "no leftover
    files in `tmp_path`" check would pass without testing anything.
- **Import `main` inside each test** (`from mcp_server_python_docs.__main__ import main`),
  as `tests/test_services.py:562` does, because importing it redirects fd 1.
- **TDD:** write each failing test first and watch it fail. One finding is one
  commit, with its test and its fix together.
- **Gate (`AGENTS.md`):** run fresh before the PR.
  - `uv run --locked ruff check src/ tests/ benchmarks/ ops/ .github/scripts/`
  - `uv run --locked pyright src/ benchmarks/`
  - `uv run --locked pytest --tb=short -q`
- **CHANGELOG:** add bullets under the existing `### Fixed` in
  `## [Unreleased]`.
- **Commits:** conventional prefix, and the message ends with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **DESIGN.md line references:** Tasks 1 and 5 add functions to `publish.py`,
  which shifts every Publish bullet's `(line N)` (`DESIGN.md:200-212`).
  Task 6 refreshes them once.
- **Expected conflicts with PR 3** (`refactor/remove-dead-code`):
  - it deletes the `populate_synonyms` lines inside the same `build_index`
    block (`__main__.py:401-404`);
  - it deletes `compute_sha256` and the `artifact_hash` argument in
    `publish_index`;
  - it deletes `TestSHA256` and an import in `tests/test_publish.py`;
  - it rewrites the same DESIGN.md Publish bullets (lines 200-212).

  Whichever PR lands second keeps both changes.

## Checked on 2026-10-30 (worktree at `c03e497`, Python 3.13.10, SQLite 3.50.4, macOS)

- **The test suite writes into the developer's real cache.**
  `tests/test_stdio_hygiene.py:90-105` (`test_build_index_bad_version_stderr_only`)
  runs `build-index --versions 99.99` with the real environment. Every pytest
  run therefore leaves one `build-….db-wal` / `build-….db-shm` pair in
  `~/Library/Caches/mcp-python-docs`, where 23 such pairs sat at 11:32 (20 an
  hour earlier). Once
  Task 5's sweep exists, the same subprocess would delete real `build-*.db*`
  files older than a day on every gate run. Run with
  `_isolated_cache_env(str(tmp_path))`, it leaves the pair under `tmp_path`
  instead and the real folder is untouched.
- **platformdirs 4.11.0 honours a non-empty `XDG_CACHE_HOME` on macOS**; an
  empty one falls back to `~/Library/Caches/mcp-python-docs`. Task 6's build
  steps patch both bindings instead, and its `doctor` step proves the path
  first.
- **A failed `build-index --versions 3.13 --skip-content`** run through
  `CliRunner` exits 1. With both cache bindings patched, and a recording
  `ingest_inventory` to confirm the build ran in `tmp_path`, it leaves:
  - no version ingested: `build-*.db-wal` and `build-*.db-shm` (the `.db` is
    unlinked before `close()`);
  - `KeyboardInterrupt` (Click turns it into "Aborted!" and exit 1):
    `build-*.db`;
  - `rebuild_fts_indexes` raising after the loop: `build-*.db`.
- **Today's `publish_index` publishes a WAL file.** The header bytes
  `[18:20]` are `b"\x02\x02"`. A `mode=ro` open then leaves `index.db-wal`
  (0 B) and `index.db-shm` (32 KiB). A real isolated
  `build-index --versions 3.13 --skip-content`, followed by
  `validate-corpus --db-path`, reproduces it. A stale
  `build-20200101-000000-000000.db` dated 2020 is left untouched.
- **The `mode=ro` smoke reader also leaves `-wal` and `-shm`** beside the build.
  A read-write connection that commits and then runs
  `PRAGMA journal_mode = DELETE` gets `'delete'`. After it closes, only the
  `.db` remains, the header is `b"\x01\x01"`, and later `mode=ro` opens
  create no files.
- **The pragma raises instead of returning `wal`** in two cases:
  - inside an open transaction: `cannot change out of wal mode from within a transaction`;
  - while another connection holds the file: `database is locked`, raised at
    once (0.00 s, no busy wait).
- **A `.previous` made by hard link does not disturb readers.** Take a WAL
  `index.db` whose reader left an empty `-wal` and a 32 KiB `-shm`. Link it to
  `index.db.previous.<pid>.tmp`, replace that onto `.previous`, then replace a
  DELETE-mode build onto `index.db`. A new reader sees the new rows with
  `journal_mode` `delete`, and the old reader keeps its old rows.
- **Renaming one hard link onto another link of the same file does nothing.**
  After a refused publish, `.previous` and `index.db` are links to one file.
  `os.replace(tmp, previous)` then returns without removing `tmp`, so
  `atomic_swap` must unlink `tmp` itself.
- **What stays as it is:** servers started before the first new publish keep
  their `index.db-wal` (0 B) and `index.db-shm` (32 KiB) beside the new index;
  new readers ignore them. The persistent-cache fingerprint (resolved path,
  size, `mtime_ns` of `index.db`) changes on every publish, as before.
  `.github/workflows/e2e.yml` (the installed `build-index` run) only runs on
  dispatch, call or schedule, never on a PR.
- **An empty build fails its smoke tests** with messages that include
  `FAIL: symbols: 0 rows (need >= 1000)`.

## Rulings

Places where the roadmap or research was silent or did not match the code:

1. **When the journal switch fails, the publish aborts and the build is kept.**
   A WAL build can never be published, so it is kept only for debugging. The
   ERROR line names it and says to close the program holding it and run
   `build-index` again; the sweep removes it after a day. Only a smoke-test
   failure deletes the build. Whatever blocks the switch is still holding the
   file, and Windows would refuse the delete.
2. **The abort covers the pragma raising as well as returning something other
   than `delete`**, because both real failure cases raise.
3. **A rejected build no longer gets a `failed` status row.** The row would be
   deleted with the file.
4. **The `PermissionError` message is the same on every platform.** It names
   Windows as the usual cause, so it needs no `sys.platform` branch and can be
   tested on any OS. It covers both replaces in the swap:
   `Could not replace %s (%s). On Windows a running MCP client holds it open: stop every MCP client, then move %s onto %s, or run build-index again. build-index deletes builds older than one day.`
   - `atomic_swap` logs it, removes its temporary link, and re-raises, which
     keeps its return value.
   - `publish_index` turns it into `False`: no restart message, no traceback,
     and the build is kept.
   - `build_index`'s message "Publishing failed — smoke tests did not pass" is
     no longer always true, so it becomes "Publishing failed; see the errors
     above".
5. **`build_index` cleans up only while it holds the connection.**
   `publish_index` handles its own failures. A Ctrl-C during publishing leaves
   the build for the sweep.
6. **The sweep runs on the build's own folder, right after
   `generate_build_path()`.** That is after argument validation, so a typo in
   `--versions` touches no files. A file it cannot delete is skipped with a
   WARNING.
7. **Temporary links.** `atomic_swap` always unlinks
   `index.db.previous.<pid>.tmp` after the replace, because the replace is a
   no-op when both names already point at one file (after a refused publish).
   Known limit, not fixed: a crash between the link and the replace can still
   leave one. The sweep does not take `index.db.previous.*.tmp` (the optional
   part of the review): a hard link, and a `copy2` copy, carry the old index's
   mtime, so a one-day mtime rule could delete the live temporary link of a
   concurrent publish. Build files keep the mtime rule.
8. **The real-cache test is isolated before anything else lands.** Task 1, the
   first commit, gives `test_build_index_bad_version_stderr_only` an isolated
   cache. Without that, every gate run after Task 5 would sweep the
   developer's real cache folder.

## Review Focus

1. **Existing users' `index.db` is in WAL mode, with `-wal` and `-shm` left by
   running servers (N5).** Expected: after the first new publish, new readers
   open the new file in `delete` mode with the new rows, and a running server
   keeps its old snapshot. Pinned by Task 3,
   `test_new_readers_ignore_stale_sidecars_of_a_wal_index`.
2. **Another process holds the build open at publish time.** Expected: the
   journal switch fails, the publish aborts, `index.db` is untouched, and the
   build is kept and named at ERROR. Pinned by Task 3,
   `test_publish_aborts_when_build_cannot_leave_wal`.
3. **The cache folder is on a filesystem without hard links.** Expected:
   `.previous` is made by copy, and no temporary file is left. Pinned by
   Task 4, `test_swap_copies_when_hard_links_fail`.
4. **On Windows a running MCP client holds `index.db` open.** Expected: the
   final replace fails, ERROR says to stop every MCP client and names the kept
   build, `publish_index` returns `False`, and no restart message is printed.
   Pinned by Task 4, `test_locked_index_keeps_the_build`.
5. **A stale build file cannot be deleted** (still open on Windows, or no
   permission). Expected: a WARNING names it, and `build-index` carries on.
   Pinned by Task 5, `test_sweep_skips_files_it_cannot_delete`.

---

### Task 1: Close, then delete, a failed build

This is PR 4's first commit. It also isolates the one existing test that ran
`build-index` against the developer's real cache folder.

**Files:**
- Modify: `src/mcp_server_python_docs/ingestion/publish.py` (add `delete_build`
  after `generate_build_path`, which ends at line 52)
- Modify: `src/mcp_server_python_docs/__main__.py:169-174` (import),
  `:215-408` (the build block), `:396-398` (the early unlink)
- Modify (existing test): `tests/test_stdio_hygiene.py:90-105`
  (`test_build_index_bad_version_stderr_only`)
- Modify: `CHANGELOG.md`
- Test: `tests/test_publish.py` (new helpers, fixture and class at the end of
  the file)

**Interfaces:**
- Produces:
  - `delete_build(build_db_path: Path) -> None` in `ingestion/publish.py`. It
    unlinks the `.db` and its `-wal` and `-shm` sidecars, each with
    `missing_ok=True`, and logs one INFO line naming the build. Task 2 reuses
    it.
  - The `isolated_cache` fixture in `tests/test_publish.py`, which Tasks 2 to
    5 reuse.

- [ ] **Step 1: Write the failing test.** Append to `tests/test_publish.py`. It
  needs no new imports. Add each later import only in the task that first uses
  it, or Ruff flags it as unused.

```python
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
```

  Then isolate the existing subprocess test in `tests/test_stdio_hygiene.py`.
  Give `test_build_index_bad_version_stderr_only` a `tmp_path` parameter, pass
  the module's own `_isolated_cache_env` to `subprocess.run`, and add one
  assertion after the existing one. Nothing is removed:

```python
            timeout=30,
            env=_isolated_cache_env(str(tmp_path)),  # never the developer's real cache
        )
        # This will fail (invalid version), but any output should be on stderr
        # stdout should be empty because of fd redirection
        assert result.stdout == "", (
            f"build-index produced stdout: {result.stdout!r}"
        )
        assert sorted(p.name for p in tmp_path.rglob("build-*")) == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --locked pytest tests/test_publish.py tests/test_stdio_hygiene.py -q -k "failed_build_leaves_no_files or bad_version"`
Expected: 4 failed.
- `no-version-ingested` leaves `build-….db-wal` and `build-….db-shm`;
  `ctrl-c` and `error-after-ingestion` leave `build-….db`.
- `test_build_index_bad_version_stderr_only` lists `build-….db-shm` and
  `build-….db-wal`, now under `tmp_path` instead of the real cache.

- [ ] **Step 3: Add `delete_build` and clean up on every failure path**
  - Add `delete_build(build_db_path: Path) -> None` to `publish.py`, as given in
    Interfaces.
  - Import it in `build_index` (`__main__.py:169-174`).
  - In `build_index`, close the connection first, then call
    `delete_build(build_db_path)` on every exit from the `try` block
    (`__main__.py:216-408`) that does not reach its end. That covers
    `SystemExit` (no version ingested), any exception, and
    `KeyboardInterrupt`. One way: set `built = False` before the `try`, set
    `built = True` after `rebuild_fts_indexes(conn)`, and in `finally` run
    `conn.close()` and then, if not `built`, `delete_build(build_db_path)`.
  - Delete the early `build_db_path.unlink()` at lines 396-398, which ran
    before the close.

- [ ] **Step 4: Run the tests**

Run: `uv run --locked pytest tests/test_publish.py tests/test_stdio_hygiene.py -q`
Expected: all passed.

- [ ] **Step 5: Add the changelog entry under `### Fixed`**

```markdown
- A failed `build-index` run (no version ingested, an error, or Ctrl-C) no
  longer leaves `build-*.db`, `-wal` or `-shm` files in the cache folder. The
  test suite no longer runs `build-index` against the real cache folder.
```

- [ ] **Step 6: Commit**

```bash
git add src/mcp_server_python_docs/ingestion/publish.py src/mcp_server_python_docs/__main__.py tests/test_publish.py tests/test_stdio_hygiene.py CHANGELOG.md
git commit -m "fix: close and delete a failed index build on every exit path" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 2: Delete a build that fails its smoke tests

**Files:**
- Modify: `src/mcp_server_python_docs/ingestion/publish.py:462-484` (smoke
  logging and the failure branch), `:90` (`record_ingestion_run` status
  values), `:425-431` (`publish_index` docstring steps)
- Modify: `docs/architecture/DESIGN.md:200-202`, `:208-212`, `CHANGELOG.md`
- Test: `tests/test_publish.py` (`TestSmokeTests`, after
  `test_symbol_only_mode_persisted_for_validation`, which ends at line 412)

**Interfaces:**
- Consumes: `delete_build(build_db_path: Path) -> None` (Task 1).
- Produces: `publish_index(build_db_path: Path, version: str, *, require_content: bool = True) -> bool`,
  with the same signature. When the smoke tests fail it returns `False` with
  the build deleted.

- [ ] **Step 1: Write the failing test.** Add `import logging`, and add
  `publish_index` to the `ingestion.publish` import block at the top of
  `tests/test_publish.py`. Then add to `TestSmokeTests`:

```python
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --locked pytest tests/test_publish.py -q -k rejected_build`
Expected: FAIL. Today the smoke messages are logged at INFO, so the first
assertion fails.

- [ ] **Step 3: Log at ERROR, then delete.** In `publish_index`:
  - When `run_smoke_tests` fails, log every message as
    `Smoke test: <message>` at ERROR. Then log
    `Smoke tests failed; deleting build <build_db_path>` at ERROR, call
    `delete_build(build_db_path)`, and return `False`.
  - Delete the read-write block that recorded status `failed` (lines
    472-482).
  - When the tests pass, keep logging the messages at INFO.
  - Fix the text this makes stale: the `status` line in
    `record_ingestion_run`'s docstring (line 90) lists only
    `'smoke_testing'` and `'published'`, and `publish_index`'s docstring
    step 5 becomes "If failed: log the smoke messages at ERROR, delete the
    build, return False".

- [ ] **Step 4: Run the tests**

Run: `uv run --locked pytest tests/test_publish.py -q`
Expected: all passed.

- [ ] **Step 5: Update the docs**
  - In the `record_ingestion_run` bullet (`DESIGN.md:200-202`), replace "used
    to audit each build attempt regardless of outcome" with "used to record
    each build that reaches publishing; a rejected build is deleted with its
    row".
  - In the `publish_index` bullet (`DESIGN.md:208-212`), replace "on failure
    it marks the run failed and returns `False` without swapping anything into
    place" with "on failure it logs every smoke message and the build path at
    ERROR, deletes the build and returns `False`".
  - Add under `### Fixed` in `CHANGELOG.md`:

```markdown
- When smoke tests reject a new index, `build-index` logs every smoke message
  and the build path at ERROR and deletes the build.
```

- [ ] **Step 6: Commit**

```bash
git add src/mcp_server_python_docs/ingestion/publish.py tests/test_publish.py docs/architecture/DESIGN.md CHANGELOG.md
git commit -m "fix: log a rejected index build at ERROR and delete it" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 3: Publish `index.db` in rollback-journal mode (N5)

**Files:**
- Modify: `src/mcp_server_python_docs/ingestion/publish.py:486-496` (the
  `published` block), `:438-439` (the `Returns:` docstring line)
- Modify: `src/mcp_server_python_docs/__main__.py:414`
- Modify: `docs/architecture/DESIGN.md:208-212`, `CHANGELOG.md`
- Test: `tests/test_publish.py` (`TestSmokeTests`)

**Interfaces:**
- Produces: `publish_index` returns `False`, and keeps the build, when the
  build cannot leave WAL mode. A published `index.db` always has header bytes
  `[18:20] == b"\x01\x01"`.

- [ ] **Step 1: Write the failing tests** in `TestSmokeTests`. The second and
  third are Review Focus 1 and 2.

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --locked pytest tests/test_publish.py -q -k "rollback_journal or stale_sidecars or cannot_leave_wal"`
Expected: 3 failed:
- the header is `b"\x02\x02"`;
- `journal_mode` is `'wal'`;
- `publish_index` returns `True`.

- [ ] **Step 3: Switch the journal mode in the last read-write step**
  - In the `published` block (`publish.py:486-496`), after `conn.commit()`,
    run `PRAGMA journal_mode = DELETE` on the same connection and read
    `fetchone()[0]`. It must run there: every later read-write open turns WAL
    back on (`db.py:42-45`), and the pragma cannot run inside a transaction.
  - Treat any result other than `"delete"`, and any `sqlite3.OperationalError`
    from the pragma, as an abort. After `conn.close()`, log at ERROR
    `Could not switch build %s to journal_mode=DELETE (%s); not publishing it. Close the program holding it and run build-index again.`
    with the build path and the result or error, and return `False`. Keep the
    build for debugging only (Ruling 1).
  - Change the message at `__main__.py:414` to
    `Publishing failed; see the errors above`.
  - In `publish_index`'s docstring, the `Returns:` line becomes "True if
    publishing succeeded; False if the smoke tests failed (the build is
    deleted) or the build could not leave WAL mode (the build is kept)".

- [ ] **Step 4: Run the tests**

Run: `uv run --locked pytest tests/test_publish.py tests/test_services.py -q`
Expected: all passed.

- [ ] **Step 5: Update the docs**
  - In the `publish_index` bullet (`DESIGN.md:208-212`), before the
    `atomic_swap` call, add: "its last read-write step switches the build to
    `journal_mode = DELETE`, so read-only servers never create `-wal` or
    `-shm` files; if the switch fails it keeps the build, logs its path and
    returns `False`".
  - Add under `### Fixed` in `CHANGELOG.md`:

```markdown
- The published `index.db` now uses SQLite's rollback journal
  (`journal_mode=DELETE`), so servers that open it read-only no longer create
  `index.db-wal` and `index.db-shm` files beside it.
```

- [ ] **Step 6: Commit**

```bash
git add src/mcp_server_python_docs/ingestion/publish.py src/mcp_server_python_docs/__main__.py tests/test_publish.py docs/architecture/DESIGN.md CHANGELOG.md
git commit -m "fix: publish index.db in rollback-journal mode" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 4: Keep `index.db` present during the swap

**Files:**
- Modify: `src/mcp_server_python_docs/ingestion/publish.py:1-6` (module
  docstring), `:344-380` (`atomic_swap`), `:438-439` (the `Returns:` line),
  `:498-499` (the swap call in `publish_index`)
- Modify: `docs/architecture/DESIGN.md:203-207`, `CHANGELOG.md`
- Test: `tests/test_publish.py` (`TestAtomicSwap`, after line 482; and
  `TestSmokeTests`)

**Interfaces:**
- Produces: `atomic_swap(new_db_path: Path, target_path: Path | None = None) -> Path | None`,
  with the same signature and return value. When either replace is refused
  with `PermissionError`, it removes its temporary link, logs the message from
  Ruling 4 at ERROR, and re-raises. `publish_index` returns `False` in that
  case and keeps the build.

- [ ] **Step 1: Write the failing tests.** Add `import errno` and `import os` to
  the imports. The second test is Review Focus 3, and the third and fourth
  pin the temporary link (Ruling 7 and the refused `.previous` replace). Add
  to `TestAtomicSwap`:

```python
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
```

Add to `TestSmokeTests` (Review Focus 4):

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --locked pytest tests/test_publish.py -q -k "every_replace or hard_links_fail or refused or locked_index"`
Expected: 3 failed, 2 passed.
- `test_target_exists_at_every_replace` fails with
  `AssertionError: index.db missing at replace(...)`.
- `test_refused_previous_replace_cleans_temp_link` fails with
  `DID NOT RAISE`, because today's swap renames instead of replacing onto
  `.previous`.
- `test_locked_index_keeps_the_build` fails: the `PermissionError` escapes
  `publish_index`, and `index.db` has already been renamed away. That is the
  gap this task closes.
- `test_swap_copies_when_hard_links_fail` and
  `test_swap_after_a_refused_replace_leaves_no_temp_link` pass today, because
  nothing makes a link yet. They pin the new fallback and the extra unlink.

- [ ] **Step 3: Swap without a gap.** In `atomic_swap`, when `target_path` exists:
  - Set `previous = target_path.parent / (target_path.name + ".previous")` and
    `tmp = previous.with_name(f"{previous.name}.{os.getpid()}.tmp")`.
  - Run `tmp.unlink(missing_ok=True)`, then `os.link(target_path, tmp)`. On
    `OSError`, fall back to `shutil.copy2(target_path, tmp)`.
  - Run `os.replace(tmp, previous)`, then
    `tmp.unlink(missing_ok=True)  # rename is a no-op when tmp and .previous already name the same file`,
    then log it as today.

  Then run `os.replace(new_db_path, target_path)` in every case. Wrap both
  replaces: on `PermissionError`, run `tmp.unlink(missing_ok=True)` when a
  temporary link was made, log the Ruling 4 message at ERROR with the path
  that could not be replaced, the error, `new_db_path` and `target_path`,
  then re-raise.

  Then finish the task:
  - Update the docstring to describe the link, the copy fallback and the
    error.
  - In `publish_index`, wrap the `atomic_swap(build_db_path)` call in
    `except PermissionError: return False`. Its `Returns:` line gains "or the
    swap was refused (the build is kept)".
  - In the module docstring, change "atomic rename with .previous backup" to
    "atomic replace with a hard-linked .previous backup".

- [ ] **Step 4: Run the tests**

Run: `uv run --locked pytest tests/test_publish.py -q`
Expected: all passed, including the existing `TestAtomicSwap`, `TestRollback`
and `TestIngestionWhileServing` tests, unchanged.

- [ ] **Step 5: Update the docs**
  - Replace the `atomic_swap` bullet (`DESIGN.md:203-207`) with: "`atomic_swap`
    (line N) keeps `index.db` present throughout. It hard-links an existing
    `index.db` to `index.db.previous.<pid>.tmp` (copying it where the
    filesystem has no hard links), `os.replace()`s that onto
    `index.db.previous` and removes the temporary name, then `os.replace()`s
    the build onto `index.db`. On Windows a running MCP client holds
    `index.db` open, so a replace fails; it then logs that clients must be
    stopped, names the kept build, and re-raises. `rollback()` restores
    `index.db.previous`."
  - Add under `### Fixed` in `CHANGELOG.md`:

```markdown
- `index.db` no longer disappears for a moment while a new index is published,
  so a server that starts at that moment no longer starts an auto-build. When
  a running MCP client blocks the publish on Windows, `build-index` now says to
  stop the clients, keeps the new build and names it, so it can be moved into
  place by hand.
```

- [ ] **Step 6: Commit**

```bash
git add src/mcp_server_python_docs/ingestion/publish.py tests/test_publish.py docs/architecture/DESIGN.md CHANGELOG.md
git commit -m "fix: keep index.db present while a new index is published" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 5: Sweep stale builds at the start of `build-index`

**Files:**
- Modify: `src/mcp_server_python_docs/ingestion/publish.py` (add
  `sweep_stale_builds` after `delete_build`)
- Modify: `src/mcp_server_python_docs/__main__.py:169-174` (import),
  `:212-213` (call)
- Modify: `CHANGELOG.md`
- Test: `tests/test_publish.py` (`TestBuildCleanup`)

**Interfaces:**
- Produces: `sweep_stale_builds(cache_dir: Path, max_age_seconds: float = 86_400.0) -> list[Path]`
  in `ingestion/publish.py`.
  - It deletes every `cache_dir.glob("build-*.db*")` file whose `st_mtime` is
    older than `max_age_seconds`, using `path.unlink(missing_ok=True)`.
  - It logs each deletion at INFO and returns the deleted paths.
  - An `OSError` from `stat` or `unlink` logs
    `Could not delete stale build file %s: %s` at WARNING and skips that file.

- [ ] **Step 1: Write the failing tests.** Add `import time` and add
  `sweep_stale_builds` to the `ingestion.publish` import block, then add to
  `TestBuildCleanup`. The second test is Review Focus 5.

```python
    def test_sweep_removes_only_stale_build_files(self, tmp_path):
        two_days_ago = time.time() - 2 * 86_400
        stale = [tmp_path / f"build-20200101-000000-000000.db{s}" for s in ("", "-wal", "-shm")]
        fresh = tmp_path / "build-20261030-000000-000000.db"
        kept = [
            tmp_path / name
            for name in ("index.db", "index.db.previous", "retrieved-docs-cache.sqlite3")
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --locked pytest tests/test_publish.py -q -k "sweep"`
Expected: the module fails to collect, with
`ImportError: cannot import name 'sweep_stale_builds'`.

- [ ] **Step 3: Implement the sweep**
  - Add `sweep_stale_builds` to `publish.py`, as given in Interfaces, with
    `import time`.
  - In `build_index`, call `sweep_stale_builds(build_db_path.parent)` right
    after `build_db_path = generate_build_path()` (`__main__.py:212`). That is
    after argument validation, so a bad `--versions` value touches no files.

- [ ] **Step 4: Run the tests**

Run: `uv run --locked pytest tests/test_publish.py -q`
Expected: all passed.

- [ ] **Step 5: Add the changelog entry under `### Fixed`**

```markdown
- `build-index` now deletes `build-*.db*` files older than one day. Killed
  auto-builds used to leave them in the cache folder for good.
```

- [ ] **Step 6: Commit**

```bash
git add src/mcp_server_python_docs/ingestion/publish.py src/mcp_server_python_docs/__main__.py tests/test_publish.py CHANGELOG.md
git commit -m "fix: sweep stale build files at the start of build-index" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 6: Verify, open the supervisor-review PR

- [ ] **Step 1: Rebase onto the current `main`.** Run `git fetch origin main`,
  then `git rebase origin/main`.
  Expected: `Successfully rebased and updated refs/heads/fix/build-publish-hygiene.`,
  or `Current branch fix/build-publish-hygiene is up to date.` If git stops on
  a conflict, keep both sides (Global Constraints), `git add` the files, run
  `git rebase --continue`, and expect the same success line.

- [ ] **Step 2: Refresh the Publish line references.** Set each `(line N)` in
  `DESIGN.md:200-212` (the bullets for `record_ingestion_run`, `atomic_swap`
  and `publish_index`) from
  `grep -n "^def " src/mcp_server_python_docs/ingestion/publish.py`. Commit:

```bash
git add docs/architecture/DESIGN.md
git commit -m "docs: refresh publish.py line references in DESIGN.md" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 3: Run the gate in a fresh shell**

| Run | Expected |
|---|---|
| `uv run --locked ruff check src/ tests/ benchmarks/ ops/ .github/scripts/` | `All checks passed!` |
| `uv run --locked pyright src/ benchmarks/` | `0 errors` |
| `uv run --locked pytest --tb=short -q` | all passed |

Steps 4 to 10 use one isolated folder. Each command sets `ISO` itself, because
shell variables do not carry over between commands. The index goes in
`mcp-python-docs` below `ISO`: that is the folder platformdirs derives from
`XDG_CACHE_HOME="${ISO}"`, so the build, `validate-corpus` and `doctor` all
see one index. The build one-liner patches both `get_cache_dir` bindings and
never consults platformdirs. Never delete anything under
`~/Library/Caches/mcp-python-docs` or `~/.cache/mcp-python-docs` by hand.

- [ ] **Step 4: Prepare the isolated folder with a stale build**

Run: `ISO=/tmp/pd-pr4-index; mkdir "${ISO:?}" "${ISO:?}/mcp-python-docs" && touch -t 202001010000 "${ISO:?}/mcp-python-docs/build-20200101-000000-000000.db"`
Expected: no output. If `mkdir` says a folder exists, choose a new name for
`ISO` in this and every later step, and never delete the old folder by hand.

- [ ] **Step 5: Build a real symbol-only index there** (it downloads
  `objects.inv` for 3.13)

Run: `ISO=/tmp/pd-pr4-index; uv run --locked python -c "import sys, pathlib, mcp_server_python_docs.storage.db as d, mcp_server_python_docs.ingestion.publish as p; d.get_cache_dir = p.get_cache_dir = lambda: pathlib.Path(sys.argv[1]); from mcp_server_python_docs.__main__ import main; main(['build-index', '--versions', '3.13', '--skip-content'])" "${ISO:?}/mcp-python-docs"`
Expected: exit 0. Stderr has `Building index at /tmp/pd-pr4-index/mcp-python-docs/build-`
and ends with `Index rebuilt. Restart your MCP client to pick up the new index.`

- [ ] **Step 6: Validate it read-only, then list the folder**

Run: `ISO=/tmp/pd-pr4-index; uv run --locked python-docs-mcp-server validate-corpus --db-path "${ISO:?}/mcp-python-docs/index.db"`,
then `ISO=/tmp/pd-pr4-index; ls "${ISO:?}/mcp-python-docs"`
Expected: `Corpus validation PASSED`. `ls` shows only `index.db`: the stale
build is gone, and no `-wal` or `-shm` appeared. On `c03e497` it showed the
2020 build, `index.db-shm` and `index.db-wal`.

- [ ] **Step 7: Check the header**

Run: `ISO=/tmp/pd-pr4-index; uv run --locked python -c "import sys; print(open(sys.argv[1], 'rb').read(20)[18:20])" "${ISO:?}/mcp-python-docs/index.db"`
Expected: `b'\x01\x01'`. It was `b'\x02\x02'` on `c03e497`.

- [ ] **Step 8: Publish over the existing index.** Rerun Step 5's command, then
  `ISO=/tmp/pd-pr4-index; ls "${ISO:?}/mcp-python-docs"`.
  Expected: exit 0, and `ls` shows exactly `index.db` and `index.db.previous`.

- [ ] **Step 9: Prove where `doctor` will look**

Run: `ISO=/tmp/pd-pr4-index; UV_CACHE_DIR="$(uv cache dir)" XDG_CACHE_HOME="${ISO:?}" uv run --locked python -c "import pathlib, sys; from mcp_server_python_docs.storage.db import get_index_path; p = get_index_path(); print(p); home = pathlib.Path.home(); sys.exit(p.is_relative_to(home / 'Library' / 'Caches') or p.is_relative_to(home / '.cache'))"`
Expected: it prints `/tmp/pd-pr4-index/mcp-python-docs/index.db` and exits 0.
If it prints a path under `~/Library/Caches` or `~/.cache` (exit 1), stop:
the next step would read the real cache. `UV_CACHE_DIR` keeps uv's own cache
in place, because uv honours `XDG_CACHE_HOME` too.

- [ ] **Step 10: Run `doctor` against the isolated index**

Run: `ISO=/tmp/pd-pr4-index; UV_CACHE_DIR="$(uv cache dir)" XDG_CACHE_HOME="${ISO:?}" uv run --locked python-docs-mcp-server doctor`,
then `ISO=/tmp/pd-pr4-index; ls "${ISO:?}/mcp-python-docs"`
Expected: `All checks passed.`, and `ls` still shows exactly `index.db` and
`index.db.previous`.

- [ ] **Step 11: Push the branch**

Run: `git push -u origin fix/build-publish-hygiene`
Expected: `branch 'fix/build-publish-hygiene' set up to track 'origin/fix/build-publish-hygiene'.`

- [ ] **Step 12: Run the slow E2E workflow, which never runs on a PR.** Run
  `gh workflow run e2e.yml --ref fix/build-publish-hygiene`.
  Expected: `Created workflow_dispatch event for e2e.yml at fix/build-publish-hygiene`.
  Record its result from `gh run list --workflow e2e.yml --branch fix/build-publish-hygiene --limit 1`
  when it finishes. If you cannot dispatch it, say so in the PR body.

- [ ] **Step 13: Open the PR** against `main`, titled
  `fix: clean up failed builds and publish index.db without a gap`, with the
  `supervisor-review` label. Expected: `gh pr create` prints the PR URL. The
  body follows `.github/PULL_REQUEST_TEMPLATE/agent.md`:
  - In place of `Closes #`: "Roadmap PR 4,
    `docs/plans/2026-10-30-audit-fixes-roadmap.md` (finding 13, N5, the
    `index.db` gap)".
  - Acceptance criteria, one line of evidence each, from Steps 6 to 10 and
    the E2E run.
  - The gate output.
  - CodeRabbit: `Pending.`
  - Why this approach:
    - `journal_mode=DELETE`, not `immutable=1`, because a `cp` over
      `index.db` must not give silent wrong results;
    - a hard link through a per-process temp name, not a lock, because servers
      check `exists()` without taking any lock;
    - Rulings 1, 3 and 4.
  - `## Why this triggered supervisor review`:
    - Why this approach cites design choices that D8 and the research do not
      make (pipeline §7, last row): Rulings 1, 3 and 4.
    - Cite the owner approval sentence from Global Constraints.
    - One existing test changed:
      `tests/test_stdio_hygiene.py::TestStdioHygiene::test_build_index_bad_version_stderr_only`
      now runs with an isolated cache and gains one assertion (no `build-*`
      file left). Nothing was removed.
  - Product decision and independent evidence:
    - Kind: bugfix. No schema, tool-surface or rebuild change.
    - Exact head/base SHA: the output of `git rev-parse HEAD origin/main`.
    - Independent verifier check: pending Vision.
    - Unresolved limitations: on Windows, MCP clients must still be stopped
      before a publish, but the message now says so and names the kept build.
      Servers started before the first new publish keep their `index.db-wal`
      (0 B) and `index.db-shm` (32 KiB) beside the new index; new readers
      ignore them. A crash between the link and the replace can leave
      `index.db.previous.<pid>.tmp` (Ruling 7). The persistent-cache
      fingerprint is unaffected. Whether the E2E run passed, or why it did not
      run.
  - The closing line
    `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

  Do not merge it yourself.
