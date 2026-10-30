# Drop the pysqlite3 Extra Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the `pysqlite3` extra, which never enabled FTS5. Give one
FTS5 hint everywhere, and make `doctor` run the same FTS5 check as the server.

**Architecture:** One PR on `chore/drop-pysqlite3-extra` (roadmap PR 5).
Task 1 makes `doctor` call `assert_fts5_available`, so one function owns the
FTS5 check and its message. Task 2 then drops the extra, its lock entries and
the Linux-only hint together, so no commit tells users to install an extra
that does not exist. Task 3 verifies and opens one supervisor-review PR.

**Tech Stack:**
- Python ≥ 3.12, stdlib `sqlite3` (SQLite 3.50.4 locally)
- uv 0.13 (`uv lock`, `uv pip`, `uv export`)
- Click 8.4 (`click.testing.CliRunner`)
- pytest, Ruff, Pyright

**Spec:**
- `docs/research/2026-10-30-audit-fix-best-practices.md`, section
  "Ingestion, storage and runtime", item 2 (finding 12).
- `docs/plans/2026-10-30-audit-fixes-roadmap.md`: decision D9, PR map row 5,
  Global Constraints.

## Global Constraints

The roadmap's Global Constraints apply. For this PR:

- **Precondition:** `docs/plans/2026-10-30-*.md` and
  `docs/research/2026-10-30-audit-fix-best-practices.md` are on `main`; stop if
  not. Check: `git ls-tree --name-only main docs/plans/ docs/research/`.
  Expected: the list includes
  `docs/research/2026-10-30-audit-fix-best-practices.md`,
  `docs/plans/2026-10-30-audit-fixes-roadmap.md` and this file.
- **Base:** `main` after #162 merges; branch `chore/drop-pysqlite3-extra`.
- **Merge order (recommended):** PR 4 first (it stops the test suite from
  writing into the real cache), then PR 1, PR 3 and this PR. The last of these
  to land refreshes the DESIGN.md `(line N)` references (Task 3, Step 2).
- **Real cache:** until PR 4 lands, each pytest run leaves one orphan
  `build-….db-wal` / `build-….db-shm` pair in the real cache folder
  (`tests/test_stdio_hygiene.py:90-105` runs `build-index` there). Never
  delete anything in that folder by hand.
- **Delete the extra; do not wire `pysqlite3` in (D9).**
- **Forbidden territory:** `pyproject.toml` `[project]` (pipeline §2). Any
  `pyproject.toml` change is also a §7 trigger. The PR gets the
  `supervisor-review` label and a `## Why this triggered supervisor review`
  section that cites: "The owner (Aymen Hammouda) asked on 2026-10-30 to
  implement all audit findings; decisions in
  `docs/plans/2026-10-30-audit-fixes-roadmap.md`."
- **Lock file:** only `uv lock` writes `uv.lock`; never hand-edit it. Run
  `uv lock --check` afterwards. PRs #159 and #160 also change `uv.lock`;
  #159 changes `pyproject.toml` and #160 changes `README.md`. Whichever of
  these PRs lands later re-locks.
- **Must not break:**
  - the read-only `PRAGMA compile_options` path in `assert_fts5_available`;
  - the `FTS5UnavailableError` type;
  - `doctor`'s probe name `SQLite FTS5`, and its success detail
    `SQLite <version>`.
- **Expected conflicts:** PR 1 (plan
  `docs/plans/2026-10-30-index-search-quality.md`) also edits the `doctor`
  import line (`__main__.py:503`), adds an `Index format` probe, adds a
  constant near `storage/db.py:14`, and appends imports, helpers and tests to
  `tests/test_doctor.py`. Keep both sides on rebase.
- **TDD:** write each failing test first and watch it fail. One finding is one
  commit, with its test and its fix together.
- **Gate (`AGENTS.md`):** run fresh before the PR.
  - `uv run --locked ruff check src/ tests/ benchmarks/ ops/ .github/scripts/`
  - `uv run --locked pyright src/ benchmarks/`
  - `uv run --locked pytest --tb=short -q`

  Pipeline §5 adds `uv lock --check` and `uv pip compile` for dependency
  changes.
- **CHANGELOG:** add bullets under `## [Unreleased]`: one under the existing
  `### Changed`, one under `### Removed`. PR 3 may create that heading;
  whichever lands second reuses it.
- **Commits:** conventional prefix, and the message ends with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Checked on 2026-10-30 (worktree at `c03e497`, Python 3.13.10, uv 0.13.6, macOS)

- **Today's Linux hint names the extra.** With `platform.system` patched to
  `Linux` and `platform.machine` to `x86_64`, a connection whose `CREATE`
  raises `no such module: fts5` gets:
  `SQLite FTS5 extension is not available in this Python build.\nInstall the pysqlite3-binary fallback:\n  pip install 'python-docs-mcp-server[pysqlite3]'`.
- **`doctor` ignores `assert_fts5_available`.** Run through `CliRunner` with
  that function patched to raise, it still prints
  `+ PASS: SQLite FTS5 -- SQLite 3.50.4`, because it runs its own probe
  (`__main__.py:516-537`).
- **A `mode=ro` connection can still create `temp._fts5_check`.** So the
  `compile_options` branch runs only when even the temp schema is not
  writable. No test covers that branch today.
- **Removing the extra and re-locking works offline** (in a scratch copy).
  `uv lock` prints `Removed pysqlite3-binary v0.5.4.post2` and resolves 50
  packages, and `uv lock --check` passes. The lock diff is 17 deletions:
  the `pysqlite3-binary` package, the project's
  `[package.optional-dependencies]`, its `requires-dist` entry and
  `provides-extras`.
- **Old install commands still work.** After the removal,
  `uv pip install --dry-run '<project>[pysqlite3]'` warns
  `does not have an extra named pysqlite3` and exits 0. pip 26.2.1, given a
  wheel built without the extra, prints
  `WARNING: python-docs-mcp-server 0.4.0 does not provide the extra 'pysqlite3'`
  on a dry run and exits 0.
- **The CI audit export still works.** The `security.yml` command
  (`uv export --locked --format requirements-txt --all-groups --all-extras --no-emit-project --no-hashes`)
  exits 0 with no `pysqlite3` line.
- **Every other mention, outside `uv.lock` and the research and plan files:**
  `pyproject.toml:37-38`, `storage/db.py:83,102-103`, `__main__.py:531`,
  `README.md:347` and `docs/architecture/DESIGN.md:265`.

## Rulings

Places where the roadmap or research was silent or did not match the code:

1. **Two commits, `doctor` first.** After Task 1, `doctor` and the server share
   one message, which still names the extra that still exists. Task 2 removes
   the extra and the mention together.
2. **The hint text is pinned below.** The research fixed its content, not its
   words. Its first line is unchanged.
3. **`doctor` prints `str(e)` as-is.** The message has several lines, so its
   lines after the first print below the `FAIL:` line.
4. **The FTS5 tests go in `tests/test_doctor.py`.** No test file covers
   `storage/db.py`, and `TestBuildVenvSupportProbe` already tests a function
   that `doctor` calls. This also avoids PR 3's additions to
   `tests/test_schema.py`.

## Review Focus

1. **Server start on a Python without FTS5.** The read-only connection's
   `CREATE` fails as read-only, and `compile_options` lacks `ENABLE_FTS5`.
   Expected: `FTS5UnavailableError` with the one hint, with no `pysqlite3`, on
   every platform. Pinned by Task 2,
   `test_readonly_probe_without_fts5_gets_the_one_hint`.
2. **Server start on a read-only connection whose SQLite has FTS5 compiled
   in.** Expected: the check passes. Pinned by Task 2,
   `test_readonly_probe_with_fts5_compiled_in_passes`.
3. **Install scripts that still ask for `python-docs-mcp-server[pysqlite3]`**,
   with uv or with pip (the README told users to `pip install` it). Expected:
   the install proceeds with a warning, not an error. Pinned by Task 2,
   Step 7 (`uv pip install --dry-run` and `pip install --dry-run`).
4. **`doctor` on a healthy Python.** Expected: the probe is still named
   `SQLite FTS5` and still reports the SQLite version. Pinned by Task 1,
   `test_doctor_fts5_pass_shows_sqlite_version`.
5. **The CI dependency audit after the extra is gone.** The `security.yml`
   step `uv export --all-extras` must still succeed. Pinned by Task 2,
   Step 8.

---

### Task 1: `doctor` reuses `assert_fts5_available`

**Files:**
- Modify: `src/mcp_server_python_docs/__main__.py:503` (import), `:516-537`
  (the FTS5 probe)
- Modify: `CHANGELOG.md`
- Test: `tests/test_doctor.py` (new class `TestFts5Probe`)

**Interfaces:**
- Consumes: `assert_fts5_available(conn: sqlite3.Connection) -> None` from
  `mcp_server_python_docs.storage.db`, and `FTS5UnavailableError` from
  `mcp_server_python_docs.errors`.
- Produces: the `doctor` probe `("SQLite FTS5", passed, detail)`. The detail
  is `f"SQLite {sqlite3.sqlite_version}"` on success, and
  `str(FTS5UnavailableError)` on failure.

- [ ] **Step 1: Write the failing tests.** Add `import sqlite3` and
  `from mcp_server_python_docs.errors import FTS5UnavailableError` to
  `tests/test_doctor.py`, then append:

```python
class TestFts5Probe:
    """One FTS5 check, storage.db.assert_fts5_available, serves the server and doctor."""

    def test_doctor_reports_the_fts5_check_error(self, tmp_path, monkeypatch):
        from click.testing import CliRunner

        from mcp_server_python_docs.__main__ import main

        def no_fts5(conn):
            raise FTS5UnavailableError(
                "SQLite FTS5 extension is not available in this Python build.\nprobe hint"
            )

        monkeypatch.setattr("mcp_server_python_docs.storage.db.get_cache_dir", lambda: tmp_path)
        monkeypatch.setattr("mcp_server_python_docs.storage.db.assert_fts5_available", no_fts5)
        result = CliRunner().invoke(main, ["doctor"])

        assert result.exit_code == 1
        assert (
            "x FAIL: SQLite FTS5 -- SQLite FTS5 extension is not available in this Python build."
            in result.output
        )
        assert "probe hint" in result.output

    def test_doctor_fts5_pass_shows_sqlite_version(self, tmp_path, monkeypatch):
        from click.testing import CliRunner

        from mcp_server_python_docs.__main__ import main

        monkeypatch.setattr("mcp_server_python_docs.storage.db.get_cache_dir", lambda: tmp_path)
        result = CliRunner().invoke(main, ["doctor"])

        assert f"+ PASS: SQLite FTS5 -- SQLite {sqlite3.sqlite_version}" in result.output
```

The `get_cache_dir` patch keeps `doctor`'s write probe out of the real cache
folder. `result.output` holds `doctor`'s `err=True` lines, because Click 8.2+
mixes stderr into it. `exit_code == 1` already holds today, because the
isolated cache has no index; the `FAIL: SQLite FTS5` line is what fails.

- [ ] **Step 2: Run them to verify the first fails**

Run: `uv run --locked pytest tests/test_doctor.py -q -k TestFts5Probe`
Expected: 1 failed, 1 passed. The output of
`test_doctor_reports_the_fts5_check_error` shows
`+ PASS: SQLite FTS5 -- SQLite 3.50.4`. The second test passes today and
pins Review Focus 4.

- [ ] **Step 3: Replace the probe.** In `doctor`, replace lines 516-537 with
  one call to `assert_fts5_available` on a connection opened with
  `contextlib.closing(sqlite3.connect(":memory:"))`, so it is closed
  afterwards.
  - On success, the probe passes with detail
    `f"SQLite {sqlite3.sqlite_version}"`.
  - On `FTS5UnavailableError`, it fails with detail `str(e)`.
  - The probe name stays `"SQLite FTS5"`.
  - Import `assert_fts5_available` next to `get_cache_dir` and
    `get_index_path` (line 503), and import `contextlib` and
    `FTS5UnavailableError` inside `doctor`, like its other imports. The
    `import platform as plat` branch goes away.

- [ ] **Step 4: Run the doctor tests**

Run: `uv run --locked pytest tests/test_doctor.py -q`
Expected: all passed, including the subprocess test `test_doctor_checks_fts5`.

- [ ] **Step 5: Add the changelog entry under `### Changed`**

```markdown
- `doctor` now runs the same FTS5 check as the server and, when FTS5 is
  missing, prints that check's message.
```

- [ ] **Step 6: Commit**

```bash
git add src/mcp_server_python_docs/__main__.py tests/test_doctor.py CHANGELOG.md
git commit -m "fix: make doctor run the server's FTS5 check" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 2: Drop the `pysqlite3` extra and its hint

Touches forbidden territory: `pyproject.toml` `[project]`.

**Files:**
- Modify: `pyproject.toml:37-39` (delete `[project.optional-dependencies]` and
  the blank line after it)
- Modify: `uv.lock` (regenerated by `uv lock` only)
- Modify: `src/mcp_server_python_docs/storage/db.py:6` (`import platform`),
  `:75-85` (docstring), `:97-113` (hint and raise)
- Modify: `src/mcp_server_python_docs/server.py:66` (the `_assert_fts5`
  docstring says "platform-aware error")
- Modify: `README.md:340-356` (body of "FTS5 unavailable")
- Modify: `docs/architecture/DESIGN.md:250-275` (the four `storage/db.py`
  bullets), `CHANGELOG.md`
- Test: `tests/test_doctor.py` (`TestFts5Probe`)

**Interfaces:**
- Produces: `assert_fts5_available(conn: sqlite3.Connection) -> None`, with the
  same signature. When FTS5 is missing, it raises `FTS5UnavailableError` with
  exactly this message on every platform:

```text
SQLite FTS5 extension is not available in this Python build.
Install a Python whose SQLite includes FTS5:
  uv python install
or use the installer from python.org. Pythons from Linux distributions, Homebrew and conda include FTS5 too.
```

- [ ] **Step 1: Write the failing tests.** Add `import platform`,
  `import pytest` and
  `from mcp_server_python_docs.storage.db import assert_fts5_available` to
  `tests/test_doctor.py`. Add this module-level stub, then the three tests to
  `TestFts5Probe`. The last two are Review Focus 1 and 2.

```python
class _StubConnection:
    """A sqlite3 connection stand-in whose CREATE VIRTUAL TABLE fails."""

    def __init__(self, create_error: str, compile_options: tuple[str, ...] = ()):
        self.create_error = create_error
        self.compile_options = compile_options

    def execute(self, sql: str, *params):
        if sql.startswith("CREATE VIRTUAL TABLE"):
            raise sqlite3.OperationalError(self.create_error)
        return [(option,) for option in self.compile_options]  # PRAGMA compile_options
```

```python
    def test_fts5_hint_never_suggests_pysqlite3(self, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        monkeypatch.setattr(platform, "machine", lambda: "x86_64")
        with pytest.raises(FTS5UnavailableError) as excinfo:
            assert_fts5_available(_StubConnection("no such module: fts5"))
        message = str(excinfo.value)
        assert "pysqlite3" not in message
        assert "uv python install" in message
        assert "python.org" in message

    def test_readonly_probe_without_fts5_gets_the_one_hint(self, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        monkeypatch.setattr(platform, "machine", lambda: "x86_64")
        conn = _StubConnection("attempt to write a readonly database", ("THREADSAFE=1",))
        with pytest.raises(FTS5UnavailableError) as excinfo:
            assert_fts5_available(conn)
        assert "pysqlite3" not in str(excinfo.value)
        assert "uv python install" in str(excinfo.value)

    def test_readonly_probe_with_fts5_compiled_in_passes(self):
        conn = _StubConnection(
            "attempt to write a readonly database", ("ENABLE_FTS5", "THREADSAFE=1")
        )
        assert assert_fts5_available(conn) is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --locked pytest tests/test_doctor.py -q -k "pysqlite3 or readonly_probe"`
Expected: 2 failed, 1 passed. Both failures show
`Install the pysqlite3-binary fallback`. The compiled-in case passes today; it
pins the read-only path this PR must not break.

- [ ] **Step 3: Use the one hint.** In `assert_fts5_available`:
  - Replace the platform branch (`db.py:97-110`) with the single message in
    Interfaces.
  - Delete `import platform`.
  - In the docstring, change "platform-aware error message" to "one
    actionable error message", and replace the two hint bullets
    (lines 82-84) with "Raises FTS5UnavailableError telling the user to
    install a Python whose SQLite includes FTS5 (uv python install or
    python.org)."
  - In `server.py:66`, the `_assert_fts5` docstring becomes
    "Check FTS5 availability; the error says how to get a Python with FTS5 (STOR-08)."

- [ ] **Step 4: Run the tests**

Run: `uv run --locked pytest tests/test_doctor.py tests/test_services.py tests/test_stdio_smoke.py -q`
Expected: all passed. The stdio smoke tests start a real server, whose
lifespan runs `assert_fts5_available` on its read-only connection.

- [ ] **Step 5: Delete the extra and re-lock.** Delete `pyproject.toml:37-39`.
  Run `uv lock`, then `uv lock --check`.
  Expected:
  - `uv lock` prints `Removed pysqlite3-binary v0.5.4.post2`.
  - `uv lock --check` exits 0.
  - `git diff --stat uv.lock` shows 17 deletions and no insertions.

- [ ] **Step 6: Update the docs**
  - Replace `README.md:340-356` (keep the `### FTS5 unavailable` heading at
    line 338) with:

````markdown
The index needs SQLite's FTS5 extension. The python.org installers,
`uv python install`, and the Pythons from Linux distributions, Homebrew and
conda all include it. If `doctor` reports `FAIL: SQLite FTS5`, your Python was
built against a SQLite without FTS5. Install one that has it:

```bash
uv python install
```

Or install Python from [python.org](https://www.python.org/).
````

  - In the `assert_fts5_available` bullet (`DESIGN.md:258-266`):
    - change "with a platform-aware error message" to "with one actionable
      error message";
    - replace "with a platform-specific remediation hint (the
      `pysqlite3-binary` extra on Linux x86-64; a `uv python install` or
      python.org build otherwise)" with "telling the user to install a Python
      whose SQLite includes FTS5 (`uv python install` or python.org).
      `doctor` runs the same check on an in-memory connection";
    - refresh the `(line N)` of all four `storage/db.py` bullets
      (`:250-275`) from
      `grep -n "^def " src/mcp_server_python_docs/storage/db.py`.
  - Add under `### Removed` in `## [Unreleased]` of `CHANGELOG.md`:

```markdown
- Removed the `pysqlite3` optional extra. Nothing imported `pysqlite3`, so
  installing the extra never enabled FTS5. When FTS5 is missing, the server and
  `doctor` now give one hint: install a Python whose SQLite includes FTS5
  (`uv python install` or python.org).
```

- [ ] **Step 7: Check that old install commands still work (Review Focus 3)**

With uv. Run: `uv pip install --dry-run --offline '.[pysqlite3]'`
Expected: exit 0, and a line ending in
``does not have an extra named `pysqlite3` ``. It is a dry run, so nothing is
installed.

With pip, which the README used to name. First build a wheel:
`ISO=/tmp/pd-pr5-dist; mkdir "${ISO:?}" && uv build --wheel --out-dir "${ISO:?}"`.
Expected: `Successfully built /tmp/pd-pr5-dist/python_docs_mcp_server-<version>-py3-none-any.whl`.
If `mkdir` says the folder exists, choose a new name. Then run
`ISO=/tmp/pd-pr5-dist; uvx pip install --dry-run --ignore-installed "$(ls "${ISO:?}"/*.whl)[pysqlite3]"`.
Expected: exit 0, and
`WARNING: python-docs-mcp-server <version> does not provide the extra 'pysqlite3'`.
pip 26.2.1 printed exactly this on 2026-10-30 against a wheel without the
extra.

- [ ] **Step 8: Check the CI audit export (Review Focus 5)**

Run: `uv export --locked --format requirements-txt --all-groups --all-extras --no-emit-project --no-hashes --output-file /tmp/requirements-audit.txt`,
then `grep -c pysqlite /tmp/requirements-audit.txt`
Expected: the export exits 0, and `grep` prints `0`.

- [ ] **Step 9: Verify no mention is left**

Run: `git grep -n -i pysqlite -- . ':!docs/research' ':!docs/plans' ':!CHANGELOG.md'`
Expected: no output.

Run: `git grep -n -i -E "platform-aware|platform-specific" -- src docs/architecture`
Expected: no output. Before this task it listed `server.py:66`, `db.py:75`,
`db.py:97` and `DESIGN.md:259,264`.

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml uv.lock src/mcp_server_python_docs/storage/db.py src/mcp_server_python_docs/server.py tests/test_doctor.py README.md docs/architecture/DESIGN.md CHANGELOG.md
git commit -m "chore: drop the pysqlite3 extra and its FTS5 hint" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 3: Verify, open the supervisor-review PR

- [ ] **Step 1: Rebase onto the current `main`, re-locking if #159 or #160
  landed.** Run `git fetch origin main`, then `git rebase origin/main`.
  Expected: `Successfully rebased and updated refs/heads/chore/drop-pysqlite3-extra.`,
  or `Current branch chore/drop-pysqlite3-extra is up to date.`
  On a `uv.lock` conflict:
  - Run `git checkout origin/main -- uv.lock`, then `uv lock`.
    Expected: `Removed pysqlite3-binary v0.5.4.post2`.
  - Run `uv lock --check`. Expected: exit 0.
  - Run `git add uv.lock`, then `git rebase --continue`. Expected: the same
    success line as above.

  Keep #160's `README.md` changes, which sit outside the FTS5 section. If
  PR 1 landed, keep both `doctor` hunks.

- [ ] **Step 2: Run the gate in a fresh shell**

| Run | Expected |
|---|---|
| `uv run --locked ruff check src/ tests/ benchmarks/ ops/ .github/scripts/` | `All checks passed!` |
| `uv run --locked pyright src/ benchmarks/` | `0 errors` |
| `uv run --locked pytest --tb=short -q` | all passed |
| `uv lock --check` | exit 0 |
| `uv pip compile --quiet pyproject.toml -o /tmp/requirements-check.txt` | exit 0 |
| `uv run --locked python-docs-mcp-server doctor` | `+ PASS: SQLite FTS5 -- SQLite <version>` |

Pipeline §5's `validate-corpus` gate does not apply. The only storage change
is the text of an error raised before any index is read, and no index content
changes. `doctor` here reads the real environment, as users run it; it only
writes and removes its own `.doctor-write-test` file.

- [ ] **Step 3: Refresh DESIGN.md line references if this PR lands last.** In
  the recommended order (PR 4, PR 1, PR 3, this PR) it does. Refresh every
  `(line N)` in the DESIGN.md bullets for `schema.sql` (§3 tables, from
  `grep -n "CREATE" src/mcp_server_python_docs/storage/schema.sql`),
  `publish.py` (Publish bullets, from
  `grep -n "^def " src/mcp_server_python_docs/ingestion/publish.py`) and
  `storage/db.py` (Connection management, from
  `grep -n "^def " src/mcp_server_python_docs/storage/db.py`). Commit it as
  `docs: refresh DESIGN.md line references`, with the trailer from Global
  Constraints. Expected: `git diff --stat HEAD~1` shows only
  `docs/architecture/DESIGN.md`.

- [ ] **Step 4: Push the branch**

Run: `git push -u origin chore/drop-pysqlite3-extra`
Expected: `branch 'chore/drop-pysqlite3-extra' set up to track 'origin/chore/drop-pysqlite3-extra'.`

- [ ] **Step 5: Open the PR** against `main`, titled
  `chore: drop the pysqlite3 extra`, with the `supervisor-review` label.
  Expected: `gh pr create` prints the PR URL. The body follows
  `.github/PULL_REQUEST_TEMPLATE/agent.md`:
  - In place of `Closes #`: "Roadmap PR 5,
    `docs/plans/2026-10-30-audit-fixes-roadmap.md` (finding 12)".
  - Acceptance criteria, one line of evidence each: Task 2 Steps 5, 7, 8
    and 9, and the `doctor` line.
  - The gate output.
  - CodeRabbit: `Pending.`
  - Why this approach:
    - The extra was never imported.
    - Every mainstream Python build ships FTS5: python.org, `uv python
      install`, Debian, Ubuntu, Alpine, Homebrew and conda (research Ingestion
      2).
    - A real swap to `pysqlite3` would have to alias the `sqlite3` module
      before 16 modules import it.
  - `## Why this triggered supervisor review`: `pyproject.toml` `[project]`
    changed (pipeline §2 and §7) and `uv.lock` was regenerated. Cite the owner
    approval sentence from Global Constraints. Note that installers now warn
    about the unknown extra instead of failing.
  - Product decision and independent evidence:
    - Kind: maintenance. No runtime dependency or tool-surface change, and no
      rebuild.
    - Exact head/base SHA: the output of `git rev-parse HEAD origin/main`.
    - Independent verifier check: pending Vision.
    - Unresolved limitations: no Python without FTS5 was available to see the
      new hint end to end; the stub-connection tests stand in for it. Scripts
      that install `[pysqlite3]` keep working but print a warning.
  - The closing line
    `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

  Do not merge it yourself.
