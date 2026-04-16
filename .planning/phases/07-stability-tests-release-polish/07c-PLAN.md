---
phase: 7
plan: c
title: "Doctor CLI Subcommand"
wave: 1
depends_on: []
files_modified:
  - src/mcp_server_python_docs/__main__.py
  - tests/test_doctor.py
requirements:
  - CLI-02
autonomous: true
---

# Plan 07c: Doctor CLI Subcommand

<objective>
Add a `doctor` subcommand to the CLI that inspects the environment (Python version, SQLite FTS5, cache directory, index.db presence, free disk space) and prints a PASS/FAIL report for each probe. This enables first-run diagnostics when users encounter issues.
</objective>

## Tasks

<task id="1">
<title>Implement the doctor subcommand</title>

<read_first>
- src/mcp_server_python_docs/__main__.py
- src/mcp_server_python_docs/storage/db.py
- src/mcp_server_python_docs/errors.py
</read_first>

<action>
Add a `doctor` subcommand to the Click group in `src/mcp_server_python_docs/__main__.py`.

The command implementation:

```python
@main.command()
def doctor() -> None:
    """Check environment health and report issues."""
    import shutil
    import sqlite3

    from mcp_server_python_docs.storage.db import get_cache_dir, get_index_path

    results: list[tuple[str, bool, str]] = []  # (probe_name, passed, detail)

    # 1. Python version check
    py_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    py_ok = sys.version_info >= (3, 12)
    results.append((
        "Python version",
        py_ok,
        f"{py_version}" + ("" if py_ok else " (requires >= 3.12)"),
    ))

    # 2. SQLite FTS5 availability
    fts5_ok = False
    fts5_detail = ""
    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE _fts5_check USING fts5(x)")
        conn.execute("DROP TABLE _fts5_check")
        conn.close()
        fts5_ok = True
        fts5_detail = f"SQLite {sqlite3.sqlite_version}"
    except sqlite3.OperationalError:
        import platform as plat
        if plat.system() == "Linux" and plat.machine() == "x86_64":
            fts5_detail = "FTS5 unavailable — pip install 'mcp-server-python-docs[pysqlite3]'"
        else:
            fts5_detail = "FTS5 unavailable — install Python from python.org or uv python install"
    results.append(("SQLite FTS5", fts5_ok, fts5_detail))

    # 3. Cache directory
    cache_dir = get_cache_dir()
    cache_exists = cache_dir.exists()
    cache_writable = False
    if cache_exists:
        try:
            test_file = cache_dir / ".doctor-write-test"
            test_file.touch()
            test_file.unlink()
            cache_writable = True
        except OSError:
            pass
    cache_ok = cache_exists and cache_writable
    cache_detail = str(cache_dir)
    if not cache_exists:
        cache_detail += " (does not exist — will be created on first build-index)"
        cache_ok = True  # not existing yet is OK
    elif not cache_writable:
        cache_detail += " (not writable)"
    results.append(("Cache directory", cache_ok, cache_detail))

    # 4. Index database presence
    index_path = get_index_path()
    index_exists = index_path.exists()
    index_detail = str(index_path)
    if not index_exists:
        index_detail += " (not found — run: mcp-server-python-docs build-index --versions 3.13)"
    else:
        # Report file size
        size_mb = index_path.stat().st_size / (1024 * 1024)
        index_detail += f" ({size_mb:.1f} MB)"
    results.append(("Index database", index_exists, index_detail))

    # 5. Free disk space
    disk_usage = shutil.disk_usage(cache_dir if cache_exists else cache_dir.parent)
    free_gb = disk_usage.free / (1024 ** 3)
    disk_ok = free_gb >= 1.0  # at least 1 GB free
    disk_detail = f"{free_gb:.1f} GB free"
    if not disk_ok:
        disk_detail += " (recommend >= 1 GB for index builds)"
    results.append(("Disk space", disk_ok, disk_detail))

    # Print report
    all_passed = True
    click.echo("\nmcp-server-python-docs doctor\n", err=True)
    for probe_name, passed, detail in results:
        status = "PASS" if passed else "FAIL"
        marker = "✓" if passed else "✗"
        click.echo(f"  {marker} {status}: {probe_name} — {detail}", err=True)
        if not passed:
            all_passed = False

    click.echo("", err=True)
    if all_passed:
        click.echo("All checks passed.", err=True)
    else:
        click.echo("Some checks failed. See details above.", err=True)
        raise SystemExit(1)
```

Key implementation details:
- All output goes to stderr (via `err=True`) to maintain stdio hygiene
- Each probe is a `(name, passed, detail)` tuple
- Exit code is 0 on all-pass, 1 on any failure
- Cache dir not existing is a soft pass (it gets created on first build-index)
- Index not existing is a FAIL (user needs to build it)
- Disk space threshold is 1 GB (index builds clone CPython source)
- FTS5 check uses an in-memory database (no file creation needed)
- Platform-aware FTS5 fix suggestion matches the existing `assert_fts5_available` pattern
</action>

<acceptance_criteria>
- `src/mcp_server_python_docs/__main__.py` contains a `doctor` Click command
- Running `uv run mcp-server-python-docs doctor` produces output to stderr
- Output contains `PASS` or `FAIL` for each of: Python version, SQLite FTS5, Cache directory, Index database, Disk space
- Exit code is 0 when all probes pass, 1 when any fails
- The `--help` output includes `doctor` as a subcommand
- No output goes to stdout (stdio hygiene preserved)
</acceptance_criteria>
</task>

<task id="2">
<title>Create doctor subcommand tests</title>

<read_first>
- src/mcp_server_python_docs/__main__.py (after task 1)
- tests/test_stdio_hygiene.py
</read_first>

<action>
Create `tests/test_doctor.py` with tests for the doctor subcommand:

1. `test_doctor_runs_without_error` -- Run `sys.executable -m mcp_server_python_docs doctor` as a subprocess. Assert it exits (with 0 or 1, depending on whether an index exists). Assert stderr contains "Python version" and "SQLite FTS5" and "Cache directory".

2. `test_doctor_no_stdout` -- Run doctor as subprocess. Assert `result.stdout == ""` (all output goes to stderr).

3. `test_doctor_checks_python_version` -- Run doctor, parse stderr for "Python version". Assert it says "PASS" (since we're running on Python >= 3.12).

4. `test_doctor_checks_fts5` -- Run doctor, parse stderr for "SQLite FTS5". Assert it says "PASS" (since test environments have FTS5).

5. `test_doctor_checks_disk_space` -- Run doctor, parse stderr for "Disk space". Assert it says "PASS" (test systems have > 1 GB free).

6. `test_doctor_reports_missing_index` -- Run doctor with `HOME` and `XDG_CACHE_HOME` pointing to an empty temp dir. Assert stderr contains "FAIL" for "Index database" and contains "build-index".

7. `test_doctor_exit_code_on_failure` -- Run doctor with `HOME` pointing to empty temp dir. Assert exit code is 1 (because index is missing).

8. `test_doctor_in_help` -- Run `--help` and verify `doctor` appears in the subcommand list.

All tests use `subprocess.run` to spawn a real process, matching the pattern from `test_stdio_hygiene.py`.
</action>

<acceptance_criteria>
- `tests/test_doctor.py` exists with at least 6 test functions
- Every test spawns a subprocess (no mocking of Click internals)
- `test_doctor_no_stdout` verifies `result.stdout == ""`
- `test_doctor_reports_missing_index` verifies FAIL + build-index suggestion
- All doctor tests pass: `pytest tests/test_doctor.py -v`
</acceptance_criteria>
</task>

## Verification

```bash
# Run doctor command
uv run mcp-server-python-docs doctor 2>&1

# Verify help includes doctor
uv run mcp-server-python-docs --help 2>&1 | grep doctor

# Run doctor tests
uv run pytest tests/test_doctor.py -v 2>&1

# Verify no stdout output
uv run mcp-server-python-docs doctor 2>/dev/null | wc -c  # should be 0
```

<must_haves>
- `doctor` subcommand exists and runs 5 probes
- All output goes to stderr, nothing to stdout
- Missing index triggers FAIL with actionable "build-index" suggestion
- FTS5 failure gives platform-aware fix guidance
- Exit code 0 on all-pass, 1 on any failure
</must_haves>
