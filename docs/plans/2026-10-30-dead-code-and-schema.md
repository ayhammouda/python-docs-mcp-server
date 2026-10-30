# Dead Code and Unused Schema Removal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the code and index schema that nothing reads at runtime, and
guard the tool surface with a snapshot of the `tools/list` the server really
serves.

**Architecture:** One PR on `refactor/remove-dead-code` (roadmap PR 3).
Task 1 replaces the model-based schema snapshots first, so every later removal
runs under a guard of the real wire surface. Tasks 2, 3 and 4 each drop one
part of `schema.sql` together with every reader and writer of that part, so no
commit can hit "no such column". Task 5 drops the test-only codec. Task 6
verifies and opens one supervisor-review PR. Old indexes keep the unused
tables and columns; the code only stops reading them.

**Tech Stack:**
- Python ≥ 3.12, stdlib `sqlite3` (SQLite 3.50.4 locally)
- `mcp` 2.3.0: `MCPServer.list_tools()`
- Pydantic ≥ 2.12, `zstandard`, Click 8.4 (`click.testing.CliRunner`)
- pytest, Ruff, Pyright, uv

**Spec:**
- `docs/research/2026-10-30-audit-fix-best-practices.md`, section
  "Ingestion, storage and runtime": item 6 (finding 17), the side note on
  `artifact_hash` in item 3, and rows N4 and N8 of "New issues found during
  the research".
- `docs/plans/2026-10-30-audit-fixes-roadmap.md`: decision D7, PR map row 3,
  Global Constraints.

## Global Constraints

The roadmap's Global Constraints apply. For this PR:

- **Precondition:** `docs/plans/2026-10-30-*.md` and
  `docs/research/2026-10-30-audit-fix-best-practices.md` are on `main`; stop if
  not. Check: `git ls-tree --name-only main docs/plans/ docs/research/`.
  Expected: the list includes
  `docs/research/2026-10-30-audit-fix-best-practices.md`,
  `docs/plans/2026-10-30-audit-fixes-roadmap.md` and this file.
- **Base:** `main` after #162 merges; branch `refactor/remove-dead-code`. If
  PR 1 (`fix/index-search-quality`) landed first, rebase over it. Both PRs
  edit `schema.sql`; PR 1 owns the tokenizer and `PRAGMA user_version`.
- **Merge order (recommended):** PR 4 first (it stops the test suite from
  writing into the real cache), then PR 1, this PR, and PR 5. The last of
  these to land refreshes the DESIGN.md `(line N)` references.
- **Release coupling:** ship together with PR 1 in one release. Task 3's
  column drop breaks symbol search in versions up to 0.4.0 that open a newer
  index, so users should rebuild once, with PR 1's rebuild.
- **Real cache:** until PR 4 lands, each pytest run leaves one orphan
  `build-….db-wal` / `build-….db-shm` pair in the real cache folder
  (`tests/test_stdio_hygiene.py:90-105` runs `build-index` there). Never
  delete anything in that folder by hand.
- **No version bump, no rebuild (D7).** Do not touch `PRAGMA user_version`.
  Existing indexes keep the unused tables and columns, which does no harm.
- **Tool surface:** names, parameters, descriptions, schemas and annotations
  stay the same. Task 1's `tests/fixtures/tools-list.json` is the proof:
  Tasks 2 to 6 must not change it.
- **TDD:** write each failing test first and watch it fail. One finding is one
  commit, with its test and its fix together.
- **Forbidden territory:** `schema.sql` (Tasks 2, 3, 4) and existing-test
  deletions or changes (Tasks 1 to 5). The PR gets the `supervisor-review`
  label and a `## Why this triggered supervisor review` section that cites:
  "The owner (Aymen Hammouda) asked on 2026-10-30 to implement all audit
  findings; decisions in `docs/plans/2026-10-30-audit-fixes-roadmap.md`."
  Task 6 lists every deleted or changed test for the PR body.
- **Hands off:** `AGENT-EXECUTION-PIPELINE.md`, which is governing policy. Its
  §4 examples go stale (lines 85-86 name `zstd-dict-v1`; line 87 expects a
  `yaml.safe_load` site in `ingestion/sphinx_json.py`), and the PR body flags
  them for Vision. Also leave the `CHANGELOG.md` history below
  `## [Unreleased]` alone (line 91 names `zstd-dict-v1`).
- **No new dependency** and no runtime network access.
- **Gate (`AGENTS.md`):** run fresh before the PR.
  - `uv run --locked ruff check src/ tests/ benchmarks/ ops/ .github/scripts/`
  - `uv run --locked pyright src/ benchmarks/`
  - `uv run --locked pytest --tb=short -q`
- **CHANGELOG:** add bullets under a `### Removed` heading inside
  `## [Unreleased]`, between `### Changed` and `### Fixed`. PR 5 may create
  the same heading; whichever lands second reuses it.
- **DESIGN.md line references:** in every bullet or sentence of
  `docs/architecture/DESIGN.md` you edit, refresh its `(line N)` numbers with
  `grep -n`. Leave other bullets alone; several are already stale (for
  example `populate_synonyms (line 583)` sits at line 608).
- **Commits:** conventional prefix, and the message ends with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Expected conflicts.** Whichever PR lands second keeps both changes.
  - PR 4 (`fix/build-publish-hygiene`) edits `__main__.py:394-408`,
    `publish_index` in `ingestion/publish.py`, the import block of
    `tests/test_publish.py` and DESIGN.md's Publish bullets (lines 200-212).
  - PR 1 (`fix/index-search-quality`, plan
    `docs/plans/2026-10-30-index-search-quality.md`) edits `schema.sql`
    (tokenizer lines, line 2, and a `PRAGMA user_version = 1;` at the end),
    appends tests to `tests/test_schema.py` and `tests/test_ingestion.py`, and
    edits DESIGN.md lines 166-168 (next to the `populate_synonyms` bullet this
    PR deletes) and 238-245 (the FTS paragraph whose line numbers this PR
    refreshes).

## Checked on 2026-10-30 (worktree at `c03e497`, Python 3.13.10 and 3.12.13)

- `anyio.run(create_server().list_tools)` works without the lifespan. It
  returns 7 tools in registration order, each dumped with exactly the keys
  `annotations`, `description`, `inputSchema`, `name`, `outputSchema`.
- Committed fixtures against the served schemas: the two input fixtures
  differ (`[False, False]`); the three output fixtures are equal
  (`[True, True, True]`). The input drift is the titles (`SearchDocsInput`
  versus `search_docsArguments`), the model docstring, and the `get_docs`
  `version` description (N4).
- **The research's snapshot recipe fails on the Python 3.12 CI leg.** The SDK
  takes each tool's description from the raw `fn.__doc__`
  (`mcp/server/mcpserver/tools/base.py:85`). Python 3.12 keeps the docstring's
  8-space continuation indent in six of the seven descriptions; 3.13 strips it
  at compile time. CI runs 3.12, 3.13 and 3.14. After `inspect.cleandoc` on
  each tool's `description`, the 868-line fixture is byte-identical on 3.12.13
  and 3.13.10 (with pydantic 2.14.2 and 2.13.4). 3.14 was not probed.
- Nothing writes `symbols.document_id` or `symbols.section_id`:
  `ingestion/inventory.py:152-155` inserts 7 other columns, and `src/` has no
  `UPDATE symbols`. The only reader is `retrieval/ranker.py`. The only writer
  is the test at `tests/test_search_spare_slot.py:144-150`.
- That test only exercises the dead branch: with its foreign URI
  `library/alias.html#alias` and no ids, the alias resolves to
  `("library/alias.html", "alias")`. Rewritten to carry the section's own URI
  and anchor, it passes on today's code with its assertions unchanged.
- Removing `populate_synonyms` removes the only `yaml.safe_load` in
  `ingestion/sphinx_json.py`, so `test_yaml_loaded_only_via_safe_load` fails
  until its expected-site set drops that file.
- A cache row whose `compression` is `zstd-dict-v1` is already a miss today,
  with "Codec 'zstd-dict-v1' requires an explicit dictionary". A row naming
  `brotli` is a miss with "Unsupported cache codec: brotli".
- `build-index --versions 3.13 --skip-content` through `CliRunner`, with a fake
  inventory and both `get_cache_dir` bindings patched, exits 0 and publishes
  into `tmp_path`. The published index has `redirects` and `synonyms`.
- Versions up to 0.4.0 select `symbols.document_id` and `symbols.section_id`.
  On an index without them, `search_symbols` logs "FTS5 query failed" and
  returns nothing, and `lookup_symbols_exact` raises "no such column".
- platformdirs 4.11.0 honours a non-empty `XDG_CACHE_HOME` on macOS; an empty
  one falls back to `~/Library/Caches/mcp-python-docs`. Task 6's build steps
  patch both bindings instead, and its `doctor` step proves the path first.

## Rulings

Places where the roadmap or research was silent or did not match the code:

1. **The output-model snapshot pairs go too, beyond D7's wording** (D7 names
   only the input-model snapshots). The served snapshot pins the same bytes
   for those three schemas (Task 1 Step 6 shows them equal), plus the four
   other output schemas, every input schema, and the annotations. Two
   fixtures for one schema would double the regeneration work and could only
   disagree through a regeneration mistake. `models.py` keeps every result
   model, because `server.py` uses them as return types.
2. **Tool descriptions are compared after `inspect.cleandoc`** (see the 3.12
   finding above). The fixture then pins the description text, not its
   indentation.
3. **A missing fixture fails the test.** Today's guard writes the fixture and
   skips when the file is missing, so deleting it turns the guard off in CI.
4. **Three schema commits instead of one.** Each drop goes with its own readers
   and writers, as D7 requires, and a reviewer can reject one without the
   others.
5. **`test_inventory_alias_resolves_to_existing_section` is rewritten, not
   deleted.** Its alias row now carries the section's URI and anchor, and its
   assertions are unchanged, so it still pins alias dedup through the code
   path that real indexes use.
6. **`test_synonyms_populated` is replaced** by an end-to-end symbol-only build
   test, because `build-index --skip-content` is the server's auto-build path.
7. **Downgrades after Task 3.** Versions up to 0.4.0 cannot search symbols in
   an index built without the two columns, so the CHANGELOG says to rebuild
   after a downgrade, and this PR ships with PR 1. Option for Vision instead:
   keep the two nullable columns in `schema.sql` and drop only the readers.
   That keeps old versions working, at the cost of two dead columns.
8. **`AGENT-EXECUTION-PIPELINE.md:87` goes stale after Task 2**: its example
   still expects a `yaml.safe_load` call site in `ingestion/sphinx_json.py`.
   It is governing policy, so this PR flags it for Vision and does not edit
   it.

## Review Focus

1. **Python 3.12 CI leg.** Tool descriptions keep docstring indentation on
   3.12 and lose it on 3.13+. Expected: one committed fixture passes on 3.12,
   3.13 and 3.14. Pinned by Task 1 (`inspect.cleandoc` normalization; Step 5
   runs the test on 3.12 and 3.14).
2. **Snapshot fixture missing or deleted.** Expected: the test fails. It must
   not write a new fixture and skip, as the old guard did. Pinned by Task 1:
   `test_tools_list_snapshot` reads the fixture unconditionally, and Step 2
   shows the failure.
3. **Fresh install with no index.** The server auto-builds with
   `build-index --skip-content`. Expected: the build still publishes after
   `populate_synonyms` and the two tables are gone. Pinned by Task 2,
   `test_symbol_only_build_publishes_without_synonyms_table`.
4. **Index built before this PR, whose `symbols` rows carry stale
   `document_id`/`section_id` values.** Expected: lookups ignore them and
   resolve through URI and anchor. Pinned by Task 3,
   `test_symbol_lookup_ignores_legacy_location_columns`.
5. **Persistent cache written by an older server under `zstd-dict-v1`, or under
   any codec this build does not know.** Expected: a miss with a warning, and
   the next `put` rewrites the row with `zstd`, so the cache heals. Pinned by
   Task 5, `test_row_with_unknown_codec_is_a_miss_then_rewritten`.

---

### Task 1: Snapshot the served `tools/list` (N4)

Touches forbidden territory: it deletes the five `test_schema_snapshot`
cases and their fixtures.

**Files:**
- Modify (rewrite): `tests/test_schema_snapshot.py:1-53`
- Create: `tests/fixtures/tools-list.json` (generated by the test)
- Delete: `tests/fixtures/schema-search_docs-input.json`,
  `tests/fixtures/schema-get_docs-input.json`,
  `tests/fixtures/schema-search_docs-output.json`,
  `tests/fixtures/schema-get_docs-output.json`,
  `tests/fixtures/schema-list_versions-output.json`
- Modify: `src/mcp_server_python_docs/models.py:1-5` (docstring); delete
  `SearchDocsInput` (`:15-40`, with its two trailing blank lines) and
  `GetDocsInput` (`:73-100`)
- Modify: `.github/TEST-STRATEGY.md:39-40`

**Interfaces:**
- Consumes: `create_server() -> MCPServer` (`server.py:295`) and its async
  `list_tools() -> list[mcp.types.Tool]`.
- Produces: `tests/fixtures/tools-list.json`, a JSON list of the 7 served
  tools. Later tasks in this PR must leave it byte-for-byte unchanged. PR 2
  regenerates it when it edits the `get_docs` slug description.

- [ ] **Step 1: Replace `tests/test_schema_snapshot.py` with the served snapshot**

```python
"""Schema snapshot drift guard (SRVR-06).

Pins the served ``tools/list`` (names, descriptions, input and output schemas,
annotations) against ``tests/fixtures/tools-list.json``. The fixture pins
normalized description text: each passes through ``inspect.cleandoc``,
because Python 3.12 serves docstring indentation and 3.13+ serves a trailing
newline on lookup_package_docs and whatsnew_for_version. When the tool surface
changes on purpose, regenerate the fixture:
    UPDATE_SCHEMAS=1 uv run --locked pytest tests/test_schema_snapshot.py
"""
import inspect
import json
import os
from pathlib import Path

import anyio
import pytest

from mcp_server_python_docs.server import create_server

FIXTURE = Path(__file__).parent / "fixtures" / "tools-list.json"
UPDATE = os.environ.get("UPDATE_SCHEMAS", "") == "1"


def _served_tools() -> list[dict]:
    """Dump what tools/list sends; list_tools() skips the lifespan and its index."""
    tools = []
    for tool in anyio.run(create_server().list_tools):
        dumped = tool.model_dump(by_alias=True, mode="json", exclude_none=True)
        # Python 3.12 keeps docstring indentation that 3.13+ strips at compile time.
        dumped["description"] = inspect.cleandoc(dumped["description"])
        tools.append(dumped)
    return tools


def test_tools_list_snapshot() -> None:
    served = _served_tools()
    if UPDATE:
        FIXTURE.write_text(json.dumps(served, indent=2) + "\n")
        pytest.skip("tools-list.json regenerated")
    assert served == json.loads(FIXTURE.read_text()), (
        "tools/list drift; if intended, regenerate with "
        "UPDATE_SCHEMAS=1 uv run --locked pytest tests/test_schema_snapshot.py"
    )
```

- [ ] **Step 2: Run it before the fixture exists**

Run: `uv run --locked pytest tests/test_schema_snapshot.py -q`
Expected: 1 failed, with `FileNotFoundError` naming
`tests/fixtures/tools-list.json`. This is Review Focus 2.

- [ ] **Step 3: Generate the fixture**

Run: `UPDATE_SCHEMAS=1 uv run --locked pytest tests/test_schema_snapshot.py -q`
Expected: `1 skipped`. `wc -l tests/fixtures/tools-list.json` prints 868 (at
`c03e497`). The names, in order, are `search_docs`, `get_docs`,
`lookup_package_docs`, `list_versions`, `detect_python_version`,
`compare_versions`, `whatsnew_for_version`.

- [ ] **Step 4: Run it against the committed fixture**

Run: `uv run --locked pytest tests/test_schema_snapshot.py -q`
Expected: `1 passed`

- [ ] **Step 5: Run it on Python 3.12 and 3.14 (Review Focus 1; CI runs both)**

Run: `uv run --isolated --locked --python 3.12 pytest tests/test_schema_snapshot.py -q`
Expected: `1 passed`. If it fails on `description`, the normalization in
`_served_tools` is missing.

Run: `uv run --isolated --locked --python 3.14 pytest tests/test_schema_snapshot.py -q`
Expected: `1 passed`.

- [ ] **Step 6: Record what the old snapshots missed and what the new one covers**

Run: `uv run --locked python -c "import anyio,json; from mcp_server_python_docs.server import create_server; s={t.name:t.model_dump(by_alias=True,mode='json',exclude_none=True) for t in anyio.run(create_server().list_tools)}; f=lambda n: json.load(open('tests/fixtures/'+n)); print([f('schema-'+t+'-input.json')==s[t]['inputSchema'] for t in ('search_docs','get_docs')], [f('schema-'+t+'-output.json')==s[t]['outputSchema'] for t in ('search_docs','get_docs','list_versions')])"`
Expected: `[False, False] [True, True, True]`. Copy this line into the PR body.
It shows that the input fixtures guarded a schema clients never see (N4), and
that deleting the output fixtures loses nothing.

- [ ] **Step 7: Show that the new guard catches a description edit**

Change `"Start position for pagination"` at `server.py:275` to
`"Start offset"`. Run `uv run --locked pytest tests/test_schema_snapshot.py -q`.
Expected: 1 failed, with `AssertionError: tools/list drift`. Then run
`git checkout -- src/mcp_server_python_docs/server.py`. The old test never
failed on such an edit.

- [ ] **Step 8: Delete the unused input models and the five old fixtures**
  - Run `git rm tests/fixtures/schema-*.json`. Expected: five lines,
    `rm 'tests/fixtures/schema-get_docs-input.json'`,
    `rm 'tests/fixtures/schema-get_docs-output.json'`,
    `rm 'tests/fixtures/schema-list_versions-output.json'`,
    `rm 'tests/fixtures/schema-search_docs-input.json'` and
    `rm 'tests/fixtures/schema-search_docs-output.json'`.
  - In `models.py`, delete `SearchDocsInput` and `GetDocsInput`. Keep every
    other model, and keep `from typing import Literal`: `PackageKind`,
    `PackageDocsSource.trust_boundary`, `ChangeKind` and `WhatsNewKind` still
    use it.
  - Replace the module docstring (`models.py:1-5`) with:

```python
"""Pydantic result models for all MCP tools.

MCPServer builds each tool's inputSchema from the tool function's signature in
server.py, and its outputSchema from these BaseModel return types, which
provides structuredContent for free (SRVR-05).
"""
```

  - Replace the "Schema contract" bullet at `.github/TEST-STRATEGY.md:39-40`
    with:

```markdown
- **Schema contract**: `test_schema.py`, `test_schema_snapshot.py` — the served
  `tools/list` (every tool's description, input and output schema, and
  annotations) is frozen in `tests/fixtures/tools-list.json`; a wire-shape
  change fails CI.
```

- [ ] **Step 9: Verify nothing still names the deleted code**

Run: `git grep -n -E "SearchDocsInput|GetDocsInput|schema-(search_docs|get_docs|list_versions)" -- src tests benchmarks .github`
Expected: no output.

Run: `uv run --locked pytest tests/test_schema_snapshot.py tests/test_services.py tests/test_stdio_smoke.py -q`
Expected: all passed.

- [ ] **Step 10: Commit**

```bash
git add tests/test_schema_snapshot.py tests/fixtures/tools-list.json src/mcp_server_python_docs/models.py .github/TEST-STRATEGY.md
git commit -m "refactor: snapshot the served tools/list and drop unused input models" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 2: Drop the `synonyms` and `redirects` tables

Touches forbidden territory: `schema.sql`, the deletion of
`test_synonyms_populated`, and a changed assertion set in
`test_yaml_loaded_only_via_safe_load`.

**Files:**
- Modify: `src/mcp_server_python_docs/storage/schema.sql:80-94` (delete both
  `CREATE TABLE` statements and their trailing blank lines)
- Modify: `src/mcp_server_python_docs/ingestion/sphinx_json.py`: docstring
  `:5-6`, `import importlib.resources` (`:10`), `import yaml` (`:20`), delete
  `populate_synonyms` (`:608-648`)
- Modify: `src/mcp_server_python_docs/__main__.py:180` (import) and `:401-404`
  (call and log)
- Modify: `tests/test_ingestion.py:3-6` (docstring), `:37` (import), `:511-525`
  (replace `TestSynonymPopulation`)
- Modify: `tests/test_synonyms.py:91-94`
- Modify: `docs/architecture/YAML-TRUST-BOUNDARY.md:7-18`,
  `docs/architecture/DESIGN.md:169-171` and `:226-240`, `CHANGELOG.md`
- Test: `tests/test_schema.py`, `tests/test_ingestion.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `ingestion/sphinx_json.py` no longer defines `populate_synonyms`.
  Synonym expansion still reads `data/synonyms.yaml` through
  `server._load_synonyms()`, which is unchanged.

- [ ] **Step 1: Write the failing schema test.** Append to `tests/test_schema.py`:

```python
def test_unused_tables_are_not_created():
    """Nothing read synonyms or redirects; new indexes no longer create them."""
    conn = _make_db()
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE '%fts%'"
        )
    }
    conn.close()
    assert tables == {
        "doc_sets", "documents", "sections", "symbols", "examples", "ingestion_runs",
    }
```

- [ ] **Step 2: Replace `TestSynonymPopulation` (`tests/test_ingestion.py:511-525`)
  with the end-to-end auto-build test (Review Focus 3).** Delete the
  `populate_synonyms` import at line 37. Add
  `from mcp_server_python_docs.ingestion.publish import SMOKE_SENTINEL_SYMBOL`
  and `from mcp_server_python_docs.storage.db import get_readonly_connection`
  to the imports. In the module docstring (lines 5-6), replace "and synonym
  population\n(INGR-C-09)" with "and the symbol-only build path".

```python
# ── Symbol-only build (the server's auto-build path) ──


def _fake_inventory(conn, version, *, is_default=False):
    """Stand in for objects.inv: one doc set with the smoke-test minimum of symbols."""
    conn.execute(
        "INSERT INTO doc_sets (source, version, language, label, is_default) "
        "VALUES ('python-docs', ?, 'en', ?, 1)",
        (version, f"Python {version}"),
    )
    names = [SMOKE_SENTINEL_SYMBOL, *(f"mod{i}.func{i}" for i in range(1, 1000))]
    conn.executemany(
        "INSERT INTO symbols (doc_set_id, qualified_name, normalized_name, module, "
        "symbol_type, uri, anchor) VALUES (1, ?, ?, ?, 'function', 'library/m.html#x', 'x')",
        [(n, n.lower(), n.rsplit(".", 1)[0]) for n in names],
    )
    conn.execute("INSERT INTO symbols_fts(symbols_fts) VALUES('rebuild')")
    conn.commit()
    return len(names)


class TestSymbolOnlyBuild:
    def test_symbol_only_build_publishes_without_synonyms_table(self, tmp_path, monkeypatch):
        from click.testing import CliRunner

        from mcp_server_python_docs.__main__ import main

        # get_index_path() reads storage.db's binding; generate_build_path() reads publish's.
        monkeypatch.setattr("mcp_server_python_docs.storage.db.get_cache_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "mcp_server_python_docs.ingestion.publish.get_cache_dir", lambda: tmp_path
        )
        monkeypatch.setattr(
            "mcp_server_python_docs.ingestion.inventory.ingest_inventory", _fake_inventory
        )

        result = CliRunner().invoke(main, ["build-index", "--versions", "3.13", "--skip-content"])

        assert result.exit_code == 0, result.output
        conn = get_readonly_connection(tmp_path / "index.db")
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE '%fts%'"
            )
        }
        conn.close()
        assert tables == {
            "doc_sets", "documents", "sections", "symbols", "examples", "ingestion_runs",
        }
```

- [ ] **Step 3: Run both to verify they fail**

Run: `uv run --locked pytest tests/test_schema.py tests/test_ingestion.py -q -k "unused_tables or symbol_only_build"`
Expected: 2 failed. Each extra set holds `'redirects'` and `'synonyms'`.

- [ ] **Step 4: Drop the tables and their only writer**
  - Delete the `synonyms` and `redirects` `CREATE TABLE` statements from
    `schema.sql`, so `ingestion_runs` follows `examples` after one blank line.
  - Delete `populate_synonyms` from `sphinx_json.py`, and the
    `import importlib.resources` and `import yaml` lines it alone used. In the
    module docstring (lines 5-6), replace "(INGR-C-07), populates\nFTS5 indexes
    (INGR-C-08), and seeds the synonyms table (INGR-C-09)." with
    "(INGR-C-07) and\npopulates FTS5 indexes (INGR-C-08)."
  - In `build_index`, delete the `populate_synonyms` import (`__main__.py:180`)
    and lines 401-404 (comment, call, log, blank line).

- [ ] **Step 5: Shrink the YAML trust-boundary expectation.** The packaged YAML now
  has one parse site. In `tests/test_synonyms.py:91-94`, set
  `expected_safe_load_sites = {"src/mcp_server_python_docs/server.py"}`. Keep the
  `<=` subset check and the unsafe-loader scan as they are.

- [ ] **Step 6: Run the affected tests**

Run: `uv run --locked pytest tests/test_schema.py tests/test_ingestion.py tests/test_synonyms.py tests/test_services.py tests/test_schema_snapshot.py -q`
Expected: all passed.

- [ ] **Step 7: Update the docs**
  - `YAML-TRUST-BOUNDARY.md:7-18`: say the file is parsed with
    `yaml.safe_load` in one call site, `server.py` at server start. Delete the
    `sphinx_json.py` bullet. Change "confirms both expected source
    `safe_load` call sites" to "confirms the expected source `safe_load` call
    site".
  - `DESIGN.md:169-171`: delete the `populate_synonyms` bullet.
  - `DESIGN.md:226-229`: delete `` `synonyms` (line 80), `redirects` (line 87), ``
    and refresh every `(line N)` in the two table paragraphs (`:226-240`) from
    `grep -n "CREATE" src/mcp_server_python_docs/storage/schema.sql`.
  - `CHANGELOG.md`, under a new `### Removed` heading in `## [Unreleased]`:

```markdown
- New indexes no longer contain the unused `synonyms` and `redirects` tables.
  Search still expands synonyms from the packaged `synonyms.yaml`. Existing
  indexes keep working; no rebuild is needed.
```

- [ ] **Step 8: Verify no reader or writer is left**

Run: `git grep -n -E "populate_synonyms|INTO synonyms|TABLE IF NOT EXISTS (synonyms|redirects)|^import yaml|^import importlib" -- src/mcp_server_python_docs/ingestion src/mcp_server_python_docs/__main__.py src/mcp_server_python_docs/storage/schema.sql tests/test_ingestion.py`
Expected: no output. Before this task the same command listed 11 lines.

- [ ] **Step 9: Commit**

```bash
git add src/mcp_server_python_docs/storage/schema.sql src/mcp_server_python_docs/ingestion/sphinx_json.py src/mcp_server_python_docs/__main__.py tests/test_schema.py tests/test_ingestion.py tests/test_synonyms.py docs/architecture/YAML-TRUST-BOUNDARY.md docs/architecture/DESIGN.md CHANGELOG.md
git commit -m "refactor: drop the unused synonyms and redirects tables" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 3: Drop `symbols.document_id` and `symbols.section_id`

Touches forbidden territory: `schema.sql`, and a rewritten existing test
(`test_inventory_alias_resolves_to_existing_section`, assertions unchanged).

**Files:**
- Modify: `src/mcp_server_python_docs/storage/schema.sql:64-65`
- Modify: `src/mcp_server_python_docs/retrieval/ranker.py:52-75` (both id
  branches and the `if doc_row is None:` guard around the candidate loop),
  `:269` and `:392` (SELECT lists)
- Modify: `src/mcp_server_python_docs/services/compare.py:134-135` (docstring)
- Modify: `tests/test_search_spare_slot.py:139-151`
- Modify: `docs/architecture/DESIGN.md:226-240` (line numbers only),
  `CHANGELOG.md`
- Test: `tests/test_schema.py`, `tests/test_search_spare_slot.py`

**Interfaces:**
- Produces: `_resolve_symbol_location(conn: sqlite3.Connection, row: sqlite3.Row) -> tuple[str, str | None]`,
  with the same signature. It now reads only `uri`, `anchor` and `doc_set_id`
  from `row`. `search_symbols` and `lookup_symbols_exact` no longer select the
  two columns, so they work on old indexes (which have them) and new ones
  (which do not).

- [ ] **Step 1: Write the failing schema test.** Append to `tests/test_schema.py`:

```python
def test_symbols_has_no_location_id_columns():
    """Nothing wrote symbols.document_id or section_id; hits resolve by URI and anchor."""
    conn = _make_db()
    columns = [row[1] for row in conn.execute("PRAGMA table_info(symbols)")]
    conn.close()
    assert columns == [
        "id", "doc_set_id", "qualified_name", "normalized_name",
        "module", "symbol_type", "uri", "anchor",
    ]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --locked pytest tests/test_schema.py -q -k location_id_columns`
Expected: FAIL. The list holds `'document_id', 'section_id'` between
`'symbol_type'` and `'uri'`.

- [ ] **Step 3: Rewrite the alias row in
  `test_inventory_alias_resolves_to_existing_section`.** Replace lines 141-150
  with the block below. Lines 151-158, including both assertions, stay as they
  are.

```python
    section = db.execute(
        "SELECT uri, anchor FROM sections WHERE anchor = 'json.dumps'",
    ).fetchone()
    db.execute(
        "INSERT INTO symbols (doc_set_id, qualified_name, normalized_name, symbol_type, "
        "uri, anchor, module) "
        "VALUES (1, 'json.encode_alias', 'json_encode_alias', 'function', ?, ?, 'json')",
        (section['uri'], section['anchor']),
    )
```

- [ ] **Step 4: Run it on today's code**

Run: `uv run --locked pytest tests/test_search_spare_slot.py -q -k inventory_alias`
Expected: `1 passed`. The alias now dedups through the URI path, which is the
only path real indexes take.

- [ ] **Step 5: Add the legacy-index test (Review Focus 4)** to
  `tests/test_search_spare_slot.py`:

```python
def test_symbol_lookup_ignores_legacy_location_columns(search_service):
    """Indexes built before this change still carry symbols.document_id/section_id."""
    db = search_service._db
    columns = {row[1] for row in db.execute("PRAGMA table_info(symbols)")}
    for column in ("document_id", "section_id"):
        if column not in columns:  # indexes built after this change lack them
            db.execute(f"ALTER TABLE symbols ADD COLUMN {column} INTEGER")
    other = db.execute(
        "SELECT id, document_id FROM sections WHERE anchor != 'json.dumps' LIMIT 1"
    ).fetchone()
    db.execute(
        "UPDATE symbols SET document_id = ?, section_id = ? WHERE qualified_name = 'json.dumps'",
        (other["document_id"], other["id"]),
    )
    db.commit()
    hit = lookup_symbols_exact(db, "json.dumps", "3.13", 1)[0]
    assert (hit.slug, hit.anchor) == ("library/json.html", "json.dumps")
```

Run: `uv run --locked pytest tests/test_search_spare_slot.py -q -k legacy_location`
Expected: FAIL on behaviour. Today's ranker follows the stale `section_id`,
so the hit is `('library/asyncio-task.html', 'asyncio.TaskGroup')` instead of
`('library/json.html', 'json.dumps')`.

- [ ] **Step 6: Drop the columns and every reader**
  - Delete `schema.sql:64-65` (the two column lines).
  - In `_resolve_symbol_location`, delete lines 52-66 (the `section_id`
    branch) and 68-75 (the `document_id` lookup and the `if doc_row is None:`
    guard). Keep `doc_row = None` (line 67), so Pyright still sees `doc_row`
    bound, and dedent the candidate loop (lines 76-87) one level. Lines 89-105
    stay.
  - Remove `sym.document_id, sym.section_id` from the SELECT at line 269, and
    `s.document_id, s.section_id` from the SELECT at line 392.
  - In `compare.py:134-135`, end the sentence at "and uses the symbol anchor."
    It no longer mentions `symbols.section_id`.

- [ ] **Step 7: Run the affected tests**

Run: `uv run --locked pytest tests/test_schema.py tests/test_search_spare_slot.py tests/test_retrieval.py tests/test_retrieval_regression.py tests/test_services.py tests/test_compare_versions.py tests/test_stability.py tests/test_schema_snapshot.py -q`
Expected: all passed.

No retrieval regression run is needed: no real index ever filled these
columns, so the deleted branches never ran outside tests.

- [ ] **Step 8: Verify no reader is left**

Run: `git grep -n -E "sym\.document_id|s\.document_id, s\.section_id|row\[.(document_id|section_id).\]|symbols\.section_id" -- src/mcp_server_python_docs/retrieval src/mcp_server_python_docs/services/compare.py`
Expected: no output. Before this task it listed `ranker.py:52`, `:68`, `:269`,
`:392` and `compare.py:135`.

- [ ] **Step 9: Update the docs**
  - Refresh the `(line N)` numbers in `DESIGN.md:226-240`, as in Task 2.
  - Add under `### Removed` in `CHANGELOG.md`:

```markdown
- New indexes no longer contain `symbols.document_id` and `symbols.section_id`.
  Nothing wrote them; symbol hits still resolve through their URI and anchor.
  Versions up to 0.4.0 still read these columns, so after a downgrade, rebuild
  the index.
```

- [ ] **Step 10: Commit**

```bash
git add src/mcp_server_python_docs/storage/schema.sql src/mcp_server_python_docs/retrieval/ranker.py src/mcp_server_python_docs/services/compare.py tests/test_schema.py tests/test_search_spare_slot.py docs/architecture/DESIGN.md CHANGELOG.md
git commit -m "refactor: drop unused symbol location columns and their ranker branches" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 4: Drop `ingestion_runs.artifact_hash` and `compute_sha256` (N8)

Touches forbidden territory: `schema.sql`, and the deletion of `TestSHA256`
(3 tests).

**Files:**
- Modify: `src/mcp_server_python_docs/storage/schema.sql:102`
- Modify: `src/mcp_server_python_docs/ingestion/publish.py`: docstring `:3`,
  `import hashlib` (`:9`), delete `compute_sha256` (`:55-75`),
  `record_ingestion_run` (`:76-104`), `publish_index` docstring (`:427-431`)
  and body (`:441-444`, `:456`)
- Modify: `tests/test_publish.py:3` (docstring), `:20` (import), delete
  `:55-83` (`TestSHA256` and its section comment)
- Modify: `docs/architecture/DESIGN.md:200-202`, `:208-212`, `:226-240`;
  `CHANGELOG.md`
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces: `record_ingestion_run(conn: sqlite3.Connection, source: str, version: str, status: str, notes: str | None = None) -> int`.
  The only caller is `publish_index`. `compute_sha256` no longer exists.

- [ ] **Step 1: Write the failing schema test.** Append to `tests/test_schema.py`:

```python
def test_ingestion_runs_has_no_artifact_hash():
    """The hash was taken before the last writes, so it never matched the published file."""
    conn = _make_db()
    columns = [row[1] for row in conn.execute("PRAGMA table_info(ingestion_runs)")]
    conn.close()
    assert columns == [
        "id", "source", "version", "status", "started_at", "finished_at", "notes",
    ]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --locked pytest tests/test_schema.py -q -k artifact_hash`
Expected: FAIL. The list holds `'artifact_hash'` before `'notes'`.

- [ ] **Step 3: Drop the column, its writer and the misleading hash**
  - Delete `schema.sql:102` (`artifact_hash TEXT,`).
  - Give `record_ingestion_run` the signature in Interfaces: drop the
    parameter, its docstring line, and the column and placeholder from the
    INSERT.
  - In `publish_index`, delete lines 441-444 (the hash, the
    "Build artifact SHA256" log and the blank line after them) and the
    `artifact_hash=` argument at line 456. Delete docstring step 1 (line 427)
    and renumber steps 2-5 as 1-4.
  - Delete `compute_sha256` and `import hashlib`. In the module docstring,
    drop "SHA256 hashing,".

- [ ] **Step 4: Delete the tests of the deleted function.** Remove `TestSHA256`
  with its `# ── SHA256 tests (PUBL-02) ──` comment, and remove the
  `compute_sha256` import. In the module docstring, drop
  "SHA256 hashing (PUBL-02),".

- [ ] **Step 5: Run the affected tests**

Run: `uv run --locked pytest tests/test_schema.py tests/test_publish.py tests/test_ingestion.py tests/test_services.py -q`
Expected: all passed.

- [ ] **Step 6: Update the docs**
  - `DESIGN.md:200-202`: the column list becomes
    (`source`, `version`, `status`, `notes`).
  - `DESIGN.md:208-209`: delete "compute the build artifact's SHA256,".
  - Refresh the `(line N)` numbers in all three Publish bullets
    (`DESIGN.md:200-212`) from `grep -n "^def " src/mcp_server_python_docs/ingestion/publish.py`,
    because deleting `compute_sha256` shifts all three.
  - Refresh the numbers in `DESIGN.md:226-240` as in Task 2. Without PR 1 the
    table lines are then 21, 33, 44, 57, 69 and 78, and the FTS lines are 102,
    108 and 114.
  - Add under `### Removed` in `CHANGELOG.md`:

```markdown
- Removed `ingestion_runs.artifact_hash` and the "Build artifact SHA256" log
  line. The hash was taken before the last writes to the build, so it never
  matched the published `index.db`.
```

- [ ] **Step 7: Verify nothing still computes, stores or describes the hash**

Run: `git grep -n -E "compute_sha256|artifact_hash|Build artifact SHA256|^import hashlib" -- src tests/test_publish.py docs/architecture`
Expected: no output. Before this task it listed 16 lines in `publish.py`,
`schema.sql`, `tests/test_publish.py` and `DESIGN.md`.

- [ ] **Step 8: Commit**

```bash
git add src/mcp_server_python_docs/storage/schema.sql src/mcp_server_python_docs/ingestion/publish.py tests/test_schema.py tests/test_publish.py docs/architecture/DESIGN.md CHANGELOG.md
git commit -m "refactor: drop the misleading ingestion_runs.artifact_hash" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 5: Delete the test-only `zstd-dict-v1` codec

Touches forbidden territory: it deletes
`test_zstd_dict_v1_round_trips_with_explicit_dictionary` and changes the
expected list in `test_list_supported_is_stable`.

**Files:**
- Modify: `src/mcp_server_python_docs/cache/codec.py:10-112`
- Modify: `tests/cache/test_codec.py:5` (import), `:10-19` (`_test_dictionary`),
  `:23`, `:40-44`
- Modify: `docs/architecture/ADR-007-cache.md:100-106`, `:130-133`, `:149-151`;
  `docs/architecture/DESIGN.md:461`; `CHANGELOG.md`
- Test: `tests/cache/test_codec.py`, `tests/test_persistent_docs_cache.py`

**Interfaces:**
- Produces:
  - `list_supported() -> list[str]`, returning `["none", "zstd"]`
  - `encode(text: str, codec: str) -> bytes`
  - `decode(blob: bytes, codec: str) -> str`
  - The error texts stay `Unsupported cache codec: {codec}`,
    `zstd encode failed: {e}` and `zstd decode failed: {e}`.
  - The `dictionary` keyword is gone. The only caller,
    `services/persistent_cache.py:136,161`, never passed it.
  - The `compression` column and `PersistentDocsCache` are unchanged.

- [ ] **Step 1: Write the failing codec test.** In `tests/cache/test_codec.py`,
  add `import pytest`, then add:

```python
def test_dictionary_codec_is_no_longer_supported() -> None:
    with pytest.raises(ValueError, match="Unsupported cache codec: zstd-dict-v1"):
        encode("x", "zstd-dict-v1")
    with pytest.raises(ValueError, match="Unsupported cache codec: zstd-dict-v1"):
        decode(b"x", "zstd-dict-v1")
```

- [ ] **Step 2: Write the failing cache-row test (Review Focus 5).** Add
  `import pytest` to `tests/test_persistent_docs_cache.py`, then:

```python
@pytest.mark.parametrize("codec", ["zstd-dict-v1", "brotli"])
def test_row_with_unknown_codec_is_a_miss_then_rewritten(tmp_path: Path, caplog, codec):
    _, cache = _cache(tmp_path)
    cache.put(result=_result("payload"), max_chars=100, start_index=0)
    with sqlite3.connect(cache.cache_path) as conn:
        conn.execute("UPDATE retrieved_docs_cache SET compression = ?", (codec,))
    key = dict(version="3.12", slug="library/json.html", anchor=None, max_chars=100, start_index=0)

    with caplog.at_level(logging.WARNING):
        assert cache.get(**key) is None
    assert f"Unsupported cache codec: {codec}" in caplog.text
    assert cache.stats().misses == 1

    cache.put(result=_result("payload"), max_chars=100, start_index=0)
    with sqlite3.connect(cache.cache_path) as conn:
        assert conn.execute("SELECT compression FROM retrieved_docs_cache").fetchone()[0] == "zstd"
    assert cache.get(**key) == _result("payload")
```

- [ ] **Step 3: Run both to verify they fail**

Run: `uv run --locked pytest tests/cache/test_codec.py tests/test_persistent_docs_cache.py -q -k "no_longer_supported or unknown_codec"`
Expected: 2 failed, 1 passed. Both failures report
`Codec 'zstd-dict-v1' requires an explicit dictionary` instead of the
expected text. The `brotli` case passes today; it pins the general rule.

- [ ] **Step 4: Delete the codec and the dictionary plumbing it alone needs**
  - `_SUPPORTED_CODECS = ["none", "zstd"]`
  - `_Codec` holds `encode: Callable[[str], bytes]` and
    `decode: Callable[[bytes], str]`.
  - `encode` and `decode` take the signatures in Interfaces.
  - `_encode_none`, `_decode_none`, `_encode_zstd` and `_decode_zstd` take
    one argument each.
  - Delete `_encode_zstd_dict`, `_decode_zstd_dict`, `_reject_dictionary`,
    `_coerce_dictionary` and the `"zstd-dict-v1"` registry entry.

- [ ] **Step 5: Update the existing codec tests**
  - `test_list_supported_is_stable` asserts
    `list_supported() == ["none", "zstd"]`.
  - Delete `test_zstd_dict_v1_round_trips_with_explicit_dictionary`, the
    `_test_dictionary` helper it alone used, and `import zstandard as zstd`.

- [ ] **Step 6: Run the cache tests**

Run: `uv run --locked pytest tests/cache tests/test_persistent_docs_cache.py tests/test_mcp_get_docs_cache_smoke.py -q`
Expected: all passed.

- [ ] **Step 7: Update the docs**
  - `ADR-007-cache.md:100-106`: replace from "The codec registry" to the end
    of the paragraph with: "The codec registry (`cache/codec.py`) also defines
    `none`, a plain passthrough used by rows written before compression
    existed. A row whose `compression` value names a codec this build does not
    know is read as a cache miss, and the next `get_docs` call for that key
    rewrites it with the current codec. Per decision 5.16, this codec detail
    is an implementation footnote of the cache layer, not the reason the layer
    exists."
  - `ADR-007-cache.md:131-132`: delete "(or a dictionary-based one, once a
    dictionary ships)".
  - `ADR-007-cache.md:149-151`: delete the `zstd-dict-v1` sentence.
  - `DESIGN.md:461`: change `` (`none`, `zstd`, `zstd-dict-v1`) `` to
    `` (`none`, `zstd`) ``.
  - Add under `### Removed` in `CHANGELOG.md`:

```markdown
- Removed the test-only `zstd-dict-v1` cache codec. A cached `get_docs` result
  stored under a codec this version does not know is treated as a miss and
  recomputed.
```

- [ ] **Step 8: Verify the codec is gone outside tests and history**

Run: `git grep -n -i -E "zstd-dict-v1|dictionary" -- src/mcp_server_python_docs/cache docs/architecture`
Expected: no output. `AGENT-EXECUTION-PIPELINE.md:85-86` and
`CHANGELOG.md:91` still name the codec; leave them.

- [ ] **Step 9: Commit**

```bash
git add src/mcp_server_python_docs/cache/codec.py tests/cache/test_codec.py tests/test_persistent_docs_cache.py docs/architecture/ADR-007-cache.md docs/architecture/DESIGN.md CHANGELOG.md
git commit -m "refactor: delete the test-only zstd-dict-v1 cache codec" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 6: Verify, open the supervisor-review PR

- [ ] **Step 1: Rebase onto the current `main`.** Run `git fetch origin main`,
  then `git rebase origin/main`.
  Expected: `Successfully rebased and updated refs/heads/refactor/remove-dead-code.`,
  or `Current branch refactor/remove-dead-code is up to date.` On a conflict,
  keep both sides (Global Constraints). In `schema.sql`, keep PR 1's tokenizer
  and `PRAGMA user_version` lines together with this PR's three drops, then
  `git add` the files and run `git rebase --continue`, and expect the same
  success line. Refresh the `DESIGN.md:226-240` line numbers again if PR 1
  landed, and commit that as `docs: refresh schema.sql line references in DESIGN.md`.

- [ ] **Step 2: Run the gate in a fresh shell**

| Run | Expected |
|---|---|
| `uv run --locked ruff check src/ tests/ benchmarks/ ops/ .github/scripts/` | `All checks passed!` |
| `uv run --locked pyright src/ benchmarks/` | `0 errors` |
| `uv run --locked pytest --tb=short -q` | all passed |
| `uv run --locked pytest tests/test_stdio_smoke.py -q` | all passed |
| `git log --oneline origin/main.. -- tests/fixtures/tools-list.json` | exactly one commit, Task 1's |

Steps 3 to 7 use one isolated folder. Each command sets `ISO` itself, because
shell variables do not carry over between commands. The index goes in
`mcp-python-docs` below `ISO`: that is the folder platformdirs derives from
`XDG_CACHE_HOME="${ISO}"`, so the build, `validate-corpus` and `doctor` all
see one index. The build one-liner patches both `get_cache_dir` bindings and
never consults platformdirs. Never delete anything under
`~/Library/Caches/mcp-python-docs` or `~/.cache/mcp-python-docs` by hand.

- [ ] **Step 3: Build a real symbol-only index in the isolated folder** (it
  downloads `objects.inv` for 3.13)

Run: `ISO=/tmp/pd-pr3-index; mkdir "${ISO:?}" "${ISO:?}/mcp-python-docs" && uv run --locked python -c "import sys, pathlib, mcp_server_python_docs.storage.db as d, mcp_server_python_docs.ingestion.publish as p; d.get_cache_dir = p.get_cache_dir = lambda: pathlib.Path(sys.argv[1]); from mcp_server_python_docs.__main__ import main; main(['build-index', '--versions', '3.13', '--skip-content'])" "${ISO:?}/mcp-python-docs"`
Expected: exit 0. Stderr has `Building index at /tmp/pd-pr3-index/mcp-python-docs/build-`
and ends with `Index rebuilt. Restart your MCP client to pick up the new index.`
If `mkdir` says a folder exists, choose a new name for `ISO` in this and every
later step, and never delete the old folder by hand.

- [ ] **Step 4: Validate it (pipeline §5, storage change)**

Run: `ISO=/tmp/pd-pr3-index; uv run --locked python-docs-mcp-server validate-corpus --db-path "${ISO:?}/mcp-python-docs/index.db"`
Expected: `Corpus validation PASSED`

- [ ] **Step 5: Check the published schema**

Run: `ISO=/tmp/pd-pr3-index; uv run --locked python -c "import sqlite3, sys; c = sqlite3.connect(sys.argv[1]); cols = lambda t: [r[1] for r in c.execute(f'PRAGMA table_info({t})')]; print(sorted(r[0] for r in c.execute('SELECT name FROM sqlite_master WHERE type = ? AND name NOT LIKE ?', ('table', '%fts%'))), cols('symbols'), cols('ingestion_runs'))" "${ISO:?}/mcp-python-docs/index.db"`
Expected: `['doc_sets', 'documents', 'examples', 'ingestion_runs', 'sections', 'symbols'] ['id', 'doc_set_id', 'qualified_name', 'normalized_name', 'module', 'symbol_type', 'uri', 'anchor'] ['id', 'source', 'version', 'status', 'started_at', 'finished_at', 'notes']`

On `c03e497` the same command printed `redirects` and `synonyms` in the first
list, `document_id` and `section_id` in the second, and `artifact_hash` in
the third.

- [ ] **Step 6: Prove where `doctor` will look**

Run: `ISO=/tmp/pd-pr3-index; UV_CACHE_DIR="$(uv cache dir)" XDG_CACHE_HOME="${ISO:?}" uv run --locked python -c "import pathlib, sys; from mcp_server_python_docs.storage.db import get_index_path; p = get_index_path(); print(p); home = pathlib.Path.home(); sys.exit(p.is_relative_to(home / 'Library' / 'Caches') or p.is_relative_to(home / '.cache'))"`
Expected: it prints `/tmp/pd-pr3-index/mcp-python-docs/index.db` and exits 0.
If it prints a path under `~/Library/Caches` or `~/.cache` (exit 1), stop:
the next step would read the real cache. `UV_CACHE_DIR` keeps uv's own cache
in place, because uv honours `XDG_CACHE_HOME` too.

- [ ] **Step 7: Run `doctor` against the isolated index**

Run: `ISO=/tmp/pd-pr3-index; UV_CACHE_DIR="$(uv cache dir)" XDG_CACHE_HOME="${ISO:?}" uv run --locked python-docs-mcp-server doctor`
Expected: `All checks passed.`

- [ ] **Step 8: Push the branch**

Run: `git push -u origin refactor/remove-dead-code`
Expected: `branch 'refactor/remove-dead-code' set up to track 'origin/refactor/remove-dead-code'.`

- [ ] **Step 9: Open the PR** against `main`. Use the title
  `refactor: remove dead code and unused index schema`, and add the
  `supervisor-review` label. Expected: `gh pr create` prints the PR URL. The
  body follows `.github/PULL_REQUEST_TEMPLATE/agent.md`:
  - In place of `Closes #`: "Roadmap PR 3,
    `docs/plans/2026-10-30-audit-fixes-roadmap.md` (finding 17, N4, N8)".
  - Acceptance criteria, one line of evidence each: the served snapshot passes
    on 3.12, 3.13 and 3.14 (Task 1 Steps 4-5), Task 1 Step 6's output line,
    the Task 6 Step 5 output, `validate-corpus` PASSED, and the `doctor`
    output.
  - The gate output.
  - CodeRabbit: `Pending.`
  - Why this approach: the snapshot is taken from `list_tools()`, which skips
    the lifespan, with descriptions normalized for 3.12. Each schema drop
    lands with its readers in the same commit. No version bump: old indexes
    keep the unused parts harmlessly.
    - Deleting the three output-model snapshot pairs goes beyond D7's wording,
      which names only the input-model snapshots. Task 1 Step 6 shows the
      output fixtures equal the served output schemas, which the new snapshot
      pins (Ruling 1).
  - Compatibility: indexes built by this version break symbol search in
    versions up to 0.4.0, which still select `symbols.document_id` and
    `symbols.section_id`; after a downgrade, rebuild the index. Ship with
    PR 1. Option for Vision: keep the two nullable columns and drop only the
    readers (Ruling 7).
  - For Vision, not edited here: `AGENT-EXECUTION-PIPELINE.md:87` still
    expects a `yaml.safe_load` call site in `ingestion/sphinx_json.py`, which
    Task 2 removes (Ruling 8).
  - `## Why this triggered supervisor review`: `schema.sql` changed
    (pipeline §2), and existing tests were deleted or changed (§7). Cite the
    owner approval sentence from Global Constraints, then list each test
    change:
    1. `tests/test_schema_snapshot.py`: the five `test_schema_snapshot`
       cases are replaced by `test_tools_list_snapshot`. Their five
       `tests/fixtures/schema-*.json` fixtures are deleted.
    2. `tests/test_ingestion.py::TestSynonymPopulation::test_synonyms_populated`
       is deleted with `populate_synonyms`. It is replaced by
       `TestSymbolOnlyBuild::test_symbol_only_build_publishes_without_synonyms_table`.
    3. `tests/test_synonyms.py::test_yaml_loaded_only_via_safe_load`: the
       expected `safe_load` sites drop `ingestion/sphinx_json.py`, which no
       longer parses YAML.
    4. `tests/test_search_spare_slot.py::test_inventory_alias_resolves_to_existing_section`:
       the alias row carries the section's URI and anchor instead of
       `document_id`/`section_id`. Its assertions are unchanged.
    5. `tests/test_publish.py::TestSHA256` (3 tests) is deleted with
       `compute_sha256`.
    6. `tests/cache/test_codec.py`:
       `test_zstd_dict_v1_round_trips_with_explicit_dictionary` is deleted, and
       `test_list_supported_is_stable` now expects `["none", "zstd"]`.
  - Product decision and independent evidence:
    - Kind: maintenance. No tool-surface change; the snapshot proves it. No
      rebuild for users who stay on this version.
    - Exact head/base SHA: the output of `git rev-parse HEAD origin/main`.
    - Independent verifier check: pending Vision.
    - Unresolved limitations: a downgrade to 0.4.0 or older needs a rebuild
      (Ruling 7); `AGENT-EXECUTION-PIPELINE.md:85-87` names removed code; old
      indexes keep the unused tables and columns until they are rebuilt.
  - The closing line
    `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

  Do not merge it yourself. Vision merges after independent verification.
