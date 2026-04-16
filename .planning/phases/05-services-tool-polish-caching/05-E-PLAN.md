---
phase: 5
plan_id: 05-E
title: "validate-corpus CLI implementation (PUBL-07)"
wave: 2
depends_on:
  - 05-B
files_modified:
  - src/mcp_server_python_docs/__main__.py
requirements:
  - PUBL-07
autonomous: true
---

<objective>
Implement the `validate-corpus` CLI subcommand that runs the same smoke-test suite Phase 4 uses at swap time against the currently-live `index.db`. Exits 0 on pass, non-zero on fail. Replaces the current stub in `__main__.py`.
</objective>

<tasks>

<task id="1">
<title>Implement validate-corpus CLI command</title>
<read_first>
- src/mcp_server_python_docs/__main__.py (lines 281-284 — current stub)
- src/mcp_server_python_docs/ingestion/publish.py (run_smoke_tests function — the smoke test suite to reuse)
- src/mcp_server_python_docs/storage/db.py (get_index_path — resolves the default index.db location)
</read_first>
<action>
Replace the `validate_corpus` stub in `src/mcp_server_python_docs/__main__.py` (lines 281-284) with a full implementation:

```python
@main.command("validate-corpus")
@click.option(
    "--db-path",
    default=None,
    type=click.Path(exists=True),
    help="Path to index database. Defaults to the standard cache location.",
)
def validate_corpus(db_path: str | None) -> None:
    """Validate the current index by running smoke tests.

    Runs the same smoke-test suite used during build-index publishing.
    Exits 0 if all checks pass, non-zero if any fail.
    """
    from pathlib import Path

    from mcp_server_python_docs.ingestion.publish import run_smoke_tests
    from mcp_server_python_docs.storage.db import get_index_path

    if db_path is not None:
        target = Path(db_path)
    else:
        target = get_index_path()

    if not target.exists():
        logger.error("Index not found at %s", target)
        logger.error("Run: mcp-server-python-docs build-index --versions 3.13")
        raise SystemExit(1)

    logger.info("Validating corpus at %s", target)

    passed, messages = run_smoke_tests(target)

    for msg in messages:
        if msg.startswith("OK:"):
            logger.info("  %s", msg)
        elif msg.startswith("WARN:"):
            logger.warning("  %s", msg)
        else:
            logger.error("  %s", msg)

    if passed:
        logger.info("Corpus validation PASSED")
        raise SystemExit(0)
    else:
        logger.error("Corpus validation FAILED")
        raise SystemExit(1)
```

Key design decisions:
- Reuses `run_smoke_tests()` from `ingestion/publish.py` directly (same tests as swap-time validation)
- Optional `--db-path` allows testing non-default locations (useful for CI and debugging)
- Default path resolved via `get_index_path()` (platformdirs-based)
- Exit codes: 0 = pass, 1 = fail (standard Unix convention)
- All output to stderr via `logger` (stdout reserved for MCP protocol per HYGN-01)

Remove the old stub import if necessary. The new implementation lazy-imports `run_smoke_tests` and `get_index_path` inside the function body (matching the pattern used by other CLI commands in __main__.py).
</action>
<acceptance_criteria>
- `validate-corpus` command no longer prints "not yet implemented"
- Running `mcp-server-python-docs validate-corpus` against a valid index exits with code 0
- Running against a missing index exits with code 1 and prints the build-index command
- The smoke tests run are identical to those in `publish.py:run_smoke_tests()`
- `--db-path` option allows specifying a custom database path
- All output goes to stderr (no stdout pollution)
- `python -c "from mcp_server_python_docs.__main__ import main"` succeeds (no import errors)
</acceptance_criteria>
</task>

</tasks>

<verification>
1. `uv run mcp-server-python-docs validate-corpus --help` shows the help text with `--db-path` option
2. `uv run mcp-server-python-docs validate-corpus --db-path /nonexistent.db` exits non-zero (graceful error)
3. If a test index exists: `uv run mcp-server-python-docs validate-corpus` exits 0
4. `uv run pytest tests/ -x -q` passes
</verification>

<must_haves>
- validate-corpus runs same smoke tests as publish pipeline (PUBL-07)
- Runs against currently-live index.db by default
- Exits 0 on pass, non-zero on fail
- All output to stderr
</must_haves>
