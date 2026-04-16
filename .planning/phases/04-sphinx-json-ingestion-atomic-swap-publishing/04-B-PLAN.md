---
phase: 4
plan_id: 04-B
title: "Atomic-swap publishing with smoke tests and rollback"
wave: 1
depends_on: []
files_modified:
  - src/mcp_server_python_docs/ingestion/publish.py
requirements:
  - PUBL-01
  - PUBL-02
  - PUBL-03
  - PUBL-04
  - PUBL-05
autonomous: true
---

<objective>
Create `ingestion/publish.py` — the module that handles the atomic-swap publishing protocol: write to a temp build artifact, compute SHA256, run smoke tests, atomically rename to `index.db`, keep `.previous` for rollback, and print the restart message to stderr.
</objective>

<tasks>

<task id="1">
<title>Create ingestion/publish.py with atomic swap protocol</title>
<read_first>
- src/mcp_server_python_docs/storage/db.py (get_cache_dir, get_index_path, get_readonly_connection, get_readwrite_connection)
- src/mcp_server_python_docs/storage/schema.sql (ingestion_runs table schema — artifact_hash column)
- python-docs-mcp-server-build-guide.md §8 (atomic index publishing protocol, lines 330-339)
- src/mcp_server_python_docs/errors.py (IngestionError)
</read_first>
<action>
Create `src/mcp_server_python_docs/ingestion/publish.py` with these functions:

**1. `generate_build_path() -> Path`**
- Uses `get_cache_dir()` from `storage.db`
- Returns `cache_dir / f"build-{timestamp}.db"` where timestamp is `datetime.now().strftime("%Y%m%d-%H%M%S")`
- Creates cache dir if not exists: `cache_dir.mkdir(parents=True, exist_ok=True)`
- PUBL-01

**2. `compute_sha256(db_path: Path) -> str`**
- Read the file in 8KB chunks
- Compute SHA256 hex digest
- Return the hex string
- PUBL-02

**3. `record_ingestion_run(conn: sqlite3.Connection, source: str, version: str, status: str, artifact_hash: str | None, notes: str | None = None) -> int`**
- INSERT into `ingestion_runs` table: `(source, version, status, started_at=CURRENT_TIMESTAMP, finished_at=CURRENT_TIMESTAMP, artifact_hash, notes)`
- Return the new row id
- PUBL-02

**4. `run_smoke_tests(db_path: Path) -> tuple[bool, list[str]]`**
- Open a read-only connection to `db_path`
- Run these checks:
  - `doc_sets` table has at least 1 row → `"doc_sets: {count} rows"`
  - `documents` table has at least 10 rows → `"documents: {count} rows"`
  - `sections` table has at least 50 rows → `"sections: {count} rows"`
  - `symbols` table has at least 1000 rows → `"symbols: {count} rows"`
  - Spot-check: query for a known section like `SELECT 1 FROM documents WHERE slug LIKE '%asyncio%' LIMIT 1` returns a row → `"spot-check: asyncio document found"`
  - FTS5 check: `SELECT 1 FROM sections_fts WHERE sections_fts MATCH '"asyncio"' LIMIT 1` returns a row → `"fts5: sections_fts searchable"`
- Each check produces a log message
- If ALL pass: return `(True, messages)`
- If ANY fail: return `(False, messages)` with failure details
- Close the connection after checks
- PUBL-03

**5. `atomic_swap(new_db_path: Path, target_path: Path | None = None) -> Path | None`**
- `target_path` defaults to `get_index_path()` if None
- If `target_path` exists:
  - `previous_path = target_path.with_suffix(".db.previous")`
  - If `previous_path` exists, remove it: `previous_path.unlink()`
  - Rename current to previous: `os.rename(target_path, previous_path)` 
  - Log: `logger.info(f"Previous index backed up to {previous_path}")`
- Rename new to target: `os.rename(new_db_path, target_path)`
- Log: `logger.info(f"New index published at {target_path}")`
- Return `previous_path` if backup was created, else `None`
- PUBL-04
- Note: Both renames must be on the same filesystem (same cache dir) for POSIX atomicity

**6. `rollback(target_path: Path | None = None) -> bool`**
- `target_path` defaults to `get_index_path()` if None
- `previous_path = target_path.with_suffix(".db.previous")`
- If `previous_path` exists:
  - `os.rename(previous_path, target_path)`
  - Log: `logger.info("Rolled back to previous index")`
  - Return `True`
- Else: log warning, return `False`

**7. `print_restart_message() -> None`**
- Print to stderr: `"Index rebuilt. Restart your MCP client to pick up the new index."`
- Uses `print(..., file=sys.stderr)` — NOT stdout (stdio hygiene)
- PUBL-05

**8. `publish_index(build_db_path: Path, version: str) -> bool`**
- Orchestrates the full publish pipeline:
  1. Compute SHA256 of build_db_path → `artifact_hash`
  2. Open RW connection to build_db_path
  3. Record ingestion run with `status="smoke_testing"`, `artifact_hash`
  4. Run smoke tests against build_db_path
  5. If smoke tests fail:
     - Update ingestion run to `status="failed"`, notes with failure details
     - Log error, return `False`
  6. If smoke tests pass:
     - Update ingestion run to `status="published"`
     - Close connection
     - Call `atomic_swap(build_db_path)`
     - Call `print_restart_message()`
     - Return `True`

Module-level: `logger = logging.getLogger(__name__)`, `import os, sys, hashlib, logging` plus `from datetime import datetime` and `from pathlib import Path`.
Import `get_cache_dir, get_index_path` from `storage.db`.
</action>
<acceptance_criteria>
- `src/mcp_server_python_docs/ingestion/publish.py` exists
- File contains `def generate_build_path(`
- File contains `def compute_sha256(`
- File contains `def record_ingestion_run(`
- File contains `def run_smoke_tests(`
- File contains `def atomic_swap(`
- File contains `def rollback(`
- File contains `def print_restart_message(`
- File contains `def publish_index(`
- File contains `file=sys.stderr` in print_restart_message (stdout hygiene)
- File contains `"Index rebuilt. Restart your MCP client to pick up the new index."`
- File contains `os.rename` (atomic rename)
- File contains `hashlib.sha256` (SHA256 computation)
- File contains `build-` in generate_build_path (timestamp naming)
- File does NOT contain `print(` without `file=sys.stderr` (stdout hygiene)
- File does NOT contain `import mcp` or `from mcp` (ingestion never imports MCP)
- `python -c "from mcp_server_python_docs.ingestion.publish import generate_build_path, compute_sha256, run_smoke_tests, atomic_swap, rollback, publish_index, print_restart_message"` succeeds
</acceptance_criteria>
</task>

</tasks>

<verification>
- [ ] publish.py exists with all 8 functions
- [ ] Build artifact uses timestamped filename (PUBL-01)
- [ ] SHA256 computed and recorded in ingestion_runs (PUBL-02)
- [ ] Smoke tests validate row counts and spot-check (PUBL-03)
- [ ] Atomic swap via os.rename with .previous backup (PUBL-04)
- [ ] Restart message printed to stderr (PUBL-05)
- [ ] Rollback function restores .previous
- [ ] No stdout writes (stdio hygiene)
</verification>

<must_haves>
- Timestamped build artifact path (PUBL-01) — never write directly to index.db
- SHA256 hash in ingestion_runs.artifact_hash (PUBL-02)
- Smoke tests before swap (PUBL-03) — row counts + spot check
- Atomic rename with .previous backup (PUBL-04)
- Restart message to stderr (PUBL-05)
</must_haves>
