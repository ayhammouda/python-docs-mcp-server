---
phase: 09-compare-versions
plan: 01
type: execute
wave: 0
depends_on: []
files_modified:
  - .planning/phases/09-compare-versions/09-01-data-shape-spike-SUMMARY.md
autonomous: true
requirements:
  - CMPR-01
must_haves:
  truths:
    - "A reproducible in-memory two-version SQLite fixture (built from the same `bootstrap_schema` + `doc_sets`/`documents`/`sections`/`symbols` insert pattern used by `tests/test_multi_version.py::multi_version_db`) is the source of all probe data — NOT the user's `index.db` cache, which is mutable and environment-dependent."
    - "The fixture seeds three known Sphinx-version-directive prose forms verbatim: `\"New in version 3.11.\"` for asyncio.TaskGroup in 3.11, `\"Changed in version 3.10:\"` for asyncio.run in 3.11, and `\"Deprecated since version 3.12.\"` for at least one symbol. This converts A1 / A2 from \"verify against live data\" into \"document the prose forms RESEARCH §Q3/Q4 already specifies\" — the spike's job is to lock the regex strings, not rediscover them."
    - "If the executor wants to additionally probe a live `index.db` (where one happens to exist), that is documented under `## Optional live-index cross-check` in the SUMMARY but is NEVER required for the spike to be considered complete."
    - "The regex patterns to extract `new_in` / `changed_in` / `deprecated_in` from `sections.content_text` are pinned to verified prose forms with explicit fallback decisions (return None) recorded for each extractor."
  artifacts:
    - path: ".planning/phases/09-compare-versions/09-01-data-shape-spike-SUMMARY.md"
      provides: "Locked regex pattern strings + fallback policy for CompareService extractors, sourced from a reproducible fixture, not user-cache state."
      contains: "## Locked regex patterns"
  key_links:
    - from: "09-01-data-shape-spike-SUMMARY.md"
      to: "services/compare.py (Plan 03)"
      via: "regex pattern strings recorded under '## Locked regex patterns'"
      pattern: "Locked regex patterns"
---

<objective>
Lock the regex patterns that `CompareService.compare` will use to extract `new_in` / `changed_in` / `deprecated_in` / `see_also` from section markdown, using a reproducible test fixture as the data source — NOT the user's mutable `index.db` cache.

Purpose: Plan 03 needs four concrete regex strings (or explicit `None`-fallback decisions) before its executor can write `services/compare.py`. The original spike design pointed at `get_index_path()` (user cache) for data — that is mutable, may be missing, and may force a slow rebuild. RESEARCH §Q3(a)/§Q4(a) already documents the exact post-markdownify prose forms; this spike re-verifies them against a reproducible fixture (mirroring `tests/test_multi_version.py::multi_version_db`) so the evidence file is bit-for-bit re-runnable in CI, on a fresh checkout, and offline.

Output: `09-01-data-shape-spike-SUMMARY.md` recording (a) the in-memory fixture used, (b) the post-extraction regex matches per probe, (c) the locked regex literals or fallback decisions, and (d) an optional `## Optional live-index cross-check` if the executor chose to additionally spot-check a real `index.db`.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@AGENTS.md
@.planning/ROADMAP.md
@.planning/phases/09-compare-versions/09-CONTEXT.md
@.planning/phases/09-compare-versions/09-RESEARCH.md
@tests/test_multi_version.py
@tests/conftest.py

<interfaces>
<!-- The fixture pattern to clone (in-memory, reproducible, no user-cache dependency). -->

From src/mcp_server_python_docs/storage/db.py:
```python
def get_readwrite_connection(db_path: Path) -> sqlite3.Connection: ...
def bootstrap_schema(conn: sqlite3.Connection) -> None: ...
```

Relevant schema (from src/mcp_server_python_docs/storage/schema.sql):
```sql
CREATE TABLE doc_sets (id, source, version, language, label, is_default, base_url, ...);
CREATE TABLE documents (id, doc_set_id, uri, slug, title, content_text, char_count, ...);
CREATE TABLE sections (id, document_id, uri, anchor, heading, level, ordinal, content_text, char_count, ...);
CREATE TABLE symbols (id, doc_set_id, qualified_name, normalized_name, module, symbol_type, document_id, section_id, uri, anchor, ...);
```

Fixture pattern from tests/test_multi_version.py lines 17-81:
```python
@pytest.fixture
def multi_version_db(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_readwrite_connection(db_path)
    bootstrap_schema(conn)
    # INSERT two doc_sets + documents + sections + symbols
    # INSERT INTO sections_fts(sections_fts) VALUES('rebuild')
    yield conn
    conn.close()
```
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Build the in-memory spike fixture with known Sphinx-directive prose</name>
  <read_first>
    - tests/test_multi_version.py lines 17-81 (the multi_version_db fixture pattern — already in context)
    - src/mcp_server_python_docs/storage/db.py (bootstrap_schema + get_readwrite_connection signatures)
    - src/mcp_server_python_docs/storage/schema.sql (full schema — doc_sets / documents / sections / symbols columns)
    - .planning/phases/09-compare-versions/09-RESEARCH.md Q3(a) lines 170-184 (canonical post-markdownify prose forms for `.. versionadded::` / `.. changed::` / `.. deprecated::`) and Q4(a) lines 197-210 (`.. seealso::` prose form)
  </read_first>
  <action>
    Write a standalone Python script `tests/test_compare_versions_spike.py` (or a notebook-style inline `uv run python -c "..."` if the executor prefers; the script form is preferred because it is rerunnable in CI). The script builds a temporary SQLite DB matching the multi_version_db shape, with two doc_sets (`3.10` not-default, `3.11` default) and four section rows carrying the EXACT post-markdownify prose forms that RESEARCH §Q3/§Q4 documents:

    (a) `(doc_set=3.11, slug='library/asyncio-task.html', anchor='asyncio.TaskGroup')` — `content_text = "An asynchronous context manager holding a group of tasks.\n\nNew in version 3.11."`
    (b) `(doc_set=3.11, slug='library/asyncio-runner.html', anchor='asyncio.run')` — `content_text = "Execute the coroutine and return the result.\n\nChanged in version 3.10: Added support for ..."`
    (c) `(doc_set=3.11, slug='library/somemodule.html', anchor='some.deprecated_func')` — `content_text = "Old API.\n\nDeprecated since version 3.12: use new_func() instead."`
    (d) `(doc_set=3.11, slug='library/pathlib.html', anchor='pathlib.Path')` — `content_text = "Concrete path classes.\n\nSee also\n\n[os.path](library/os.path.html) — Operating system path manipulation.\n[fnmatch](library/fnmatch.html) — Pattern matching."`

    These prose forms are NOT invented — they are the literal forms RESEARCH §Q3(a) lines 170-184 documents as surviving the markdownify call in `sphinx_json.py:247`. The spike's job is to lock the regex strings against this controlled fixture, not to rediscover what RESEARCH already proved.

    The script must build the fixture in-memory or under `tempfile.TemporaryDirectory()` — it must NOT touch `get_index_path()` and must NOT trigger a `build-index` rebuild. The script must be re-runnable on a fresh clone with `uv run python tests/test_compare_versions_spike.py` exiting 0.
  </action>
  <verify>
    <automated>uv run python -c "import tempfile, pathlib, sqlite3; from mcp_server_python_docs.storage.db import bootstrap_schema, get_readwrite_connection; td=tempfile.mkdtemp(); p=pathlib.Path(td)/'spike.db'; c=get_readwrite_connection(p); bootstrap_schema(c); c.execute(\"INSERT INTO doc_sets (source, version, language, label, is_default, base_url) VALUES ('python-docs', '3.11', 'en', 'Python 3.11', 1, 'x')\"); ds=c.execute('SELECT id FROM doc_sets').fetchone()[0]; c.execute(\"INSERT INTO documents (doc_set_id, uri, slug, title, content_text, char_count) VALUES (?, 'library/asyncio-task.html', 'library/asyncio-task.html', 't', '', 0)\", (ds,)); d=c.execute('SELECT id FROM documents').fetchone()[0]; c.execute(\"INSERT INTO sections (document_id, uri, anchor, heading, level, ordinal, content_text, char_count) VALUES (?, 'library/asyncio-task.html#asyncio.TaskGroup', 'asyncio.TaskGroup', 'TaskGroup', 2, 0, 'New in version 3.11.', 20)\", (d,)); c.commit(); print('spike fixture OK')"</automated>
  </verify>
  <acceptance_criteria>
    - Source: a standalone re-runnable Python entry point exists (either `tests/test_compare_versions_spike.py` or the SUMMARY embeds the script) that builds the fixture under `tempfile.TemporaryDirectory()` — no user-cache dependency.
    - Source: the script does NOT import `get_index_path`, does NOT call `build-index`, and does NOT touch `~/Library/Caches/mcp-python-docs/` or `~/.cache/mcp-python-docs/`. Verify by grep: the script does NOT contain the string `get_index_path` or `build-index` or `Caches/mcp-python-docs` or `.cache/mcp-python-docs`.
    - CLI: the verify command exits 0 and prints `spike fixture OK`, confirming `bootstrap_schema` + INSERTs work with the prose forms RESEARCH documents.
  </acceptance_criteria>
  <done>A reproducible, offline, no-side-effects fixture builder exists; running it produces the four seeded section rows.</done>
</task>

<task type="auto">
  <name>Task 2: Probe each Sphinx-directive prose form with its candidate regex</name>
  <read_first>
    - .planning/phases/09-compare-versions/09-RESEARCH.md Q3(a)-(c) (regex candidates: `_NEW_IN_RE`, `_CHANGED_IN_RE`, `_DEPRECATED_IN_RE`)
    - .planning/phases/09-compare-versions/09-RESEARCH.md Q4(c) (`_SEE_ALSO_LINK_RE`)
    - The fixture from Task 1 (already in context)
  </read_first>
  <action>
    Against the fixture from Task 1, run each candidate regex from RESEARCH and record the match:

    | Probe | content_text excerpt | Candidate regex | Expected capture |
    |-------|----------------------|-----------------|------------------|
    | A1 (versionadded) | `"New in version 3.11."` | `r"New in version\s+(\d+\.\d+)"` | `"3.11"` |
    | Sibling 1 (changed) | `"Changed in version 3.10: ..."` | `r"Changed in version\s+(\d+\.\d+)"` | `"3.10"` |
    | Sibling 2 (deprecated) | `"Deprecated since version 3.12: ..."` | `r"Deprecated since version\s+(\d+\.\d+)"` | `"3.12"` |
    | A2 (seealso) | `"See also\n\n[os.path](...)\n[fnmatch](...)"` | `r"\[([^\]]+)\]\("` (anchored to "See also" window) | `["os.path", "fnmatch"]` |

    For each row: if the candidate regex captures the expected value → record `HOLDS` and lock the literal. If the candidate fails → record `FALSIFIED` and lock the fallback (`None` for scalar extractors, `[]` for `_extract_see_also`). Record the outcome per probe in the SUMMARY's `## A1 result` / `## A2 result` / `## Sibling directive results` sections.

    Note: against this controlled fixture, all four are expected to HOLD because the fixture seeds the exact prose form RESEARCH documents. The probe is therefore a tautological verification — its real value is producing a self-contained, re-runnable evidence file. The executor MUST still run the probes (do not just copy the expected outcomes) so that the SUMMARY contains real `re.search` outputs.
  </action>
  <verify>
    <automated>uv run python -c "import re; assert re.search(r'New in version\s+(\d+\.\d+)', 'New in version 3.11.').group(1) == '3.11'; assert re.search(r'Changed in version\s+(\d+\.\d+)', 'Changed in version 3.10: x').group(1) == '3.10'; assert re.search(r'Deprecated since version\s+(\d+\.\d+)', 'Deprecated since version 3.12: y').group(1) == '3.12'; assert re.findall(r'\[([^\]]+)\]\(', 'See also\n\n[os.path](x)\n[fnmatch](y)') == ['os.path', 'fnmatch']; print('all 4 probes HOLD')"</automated>
  </verify>
  <acceptance_criteria>
    - CLI: the verify command exits 0 and prints `all 4 probes HOLD`.
    - Source: SUMMARY records each probe's outcome with the actual captured value (or `None`/`[]` if FALSIFIED).
    - Source: the regex strings recorded in SUMMARY match the literals tested by the verify command — no drift between probe and lock.
  </acceptance_criteria>
  <done>All four regex extractors have a HOLDS/FALSIFIED outcome backed by a real `re.search` call against the fixture's seeded prose.</done>
</task>

<task type="auto">
  <name>Task 3: Write 09-01 SUMMARY with locked extractor decisions</name>
  <read_first>
    - $HOME/.claude/get-shit-done/templates/summary.md
    - The probe outputs collected in Tasks 1-2 (already in context)
  </read_first>
  <action>
    Create `.planning/phases/09-compare-versions/09-01-data-shape-spike-SUMMARY.md`. Sections required, in order:

    `## Spike data source` — Document that the spike used an in-memory fixture (NOT the user cache). Include the path-pattern (`tempfile.mkdtemp()` or `tests/test_compare_versions_spike.py`) and a one-line invocation command that reproduces the spike on a fresh checkout.

    `## Seeded prose forms` — Per-row table mapping `(version, slug, anchor)` to the verbatim `content_text` used for each probe, cross-referenced to RESEARCH §Q3/§Q4.

    `## A1 result` (HOLDS / FALSIFIED + captured value)
    `## A2 result` (HOLDS / FALSIFIED + captured link labels)
    `## Sibling directive results` (Changed-in / Deprecated-since — HOLDS / FALSIFIED + captured values)

    `## Locked regex patterns` (exact Python raw-string regex literals, ready to paste into `services/compare.py`):
      - `_NEW_IN_RE = r"New in version\s+(\d+\.\d+)"` (or `None` if A1 falsified)
      - `_CHANGED_IN_RE = r"Changed in version\s+(\d+\.\d+)"`
      - `_DEPRECATED_IN_RE = r"Deprecated since version\s+(\d+\.\d+)"`
      - `_SEE_ALSO_LINK_RE = r"\[([^\]]+)\]\("`

    `## Fallback policy` — Per extractor, what to return when no match: `None` for scalar extractors (`_extract_new_in`, `_extract_changed_in`, `_extract_deprecated_in`); `[]` for `_extract_see_also`.

    `## Optional live-index cross-check` — OPTIONAL section. If the executor additionally spot-checked a real `index.db` and the prose forms there match, record it here as supplementary evidence. If the executor skipped this (which is fine), record `Not performed — fixture is the authoritative source per the revised spike scope.`

    `## Implications for Plan 03` — 1-3 sentences directing Plan 03's executor: "All four extractors HOLD against the seeded fixture — implement as documented in RESEARCH §Q3/Q4. If live `index.db` shows different prose forms during Plan 03 implementation, raise a finding rather than mutating the regex unilaterally."
  </action>
  <verify>
    <automated>test -f .planning/phases/09-compare-versions/09-01-data-shape-spike-SUMMARY.md && grep -q "## Spike data source" .planning/phases/09-compare-versions/09-01-data-shape-spike-SUMMARY.md && grep -q "## Locked regex patterns" .planning/phases/09-compare-versions/09-01-data-shape-spike-SUMMARY.md && grep -q "## A1 result" .planning/phases/09-compare-versions/09-01-data-shape-spike-SUMMARY.md && grep -q "## A2 result" .planning/phases/09-compare-versions/09-01-data-shape-spike-SUMMARY.md && grep -q "## Fallback policy" .planning/phases/09-compare-versions/09-01-data-shape-spike-SUMMARY.md && grep -q "## Implications for Plan 03" .planning/phases/09-compare-versions/09-01-data-shape-spike-SUMMARY.md && ! grep -q "get_index_path" .planning/phases/09-compare-versions/09-01-data-shape-spike-SUMMARY.md</automated>
  </verify>
  <acceptance_criteria>
    - Source: file `.planning/phases/09-compare-versions/09-01-data-shape-spike-SUMMARY.md` exists with the seven required section headings exactly.
    - Source: file does NOT contain `get_index_path` (verified by the negative grep above) — confirming the spike did not depend on the user cache.
    - Behavior: Plan 03's executor can copy the regex literals from `## Locked regex patterns` verbatim into `services/compare.py` without re-probing anything.
  </acceptance_criteria>
  <done>The SUMMARY is checked into the worktree (uncommitted is fine — Plan 03 picks it up immediately).</done>
</task>

</tasks>

<verification>
- The spike is reproducible on a fresh clone, offline, with no `index.db` dependency.
- All four extractor decisions (`new_in`, `changed_in`, `deprecated_in`, `see_also`) are recorded as locked-regex-or-fallback in the SUMMARY.
- The SUMMARY explicitly documents that the user cache was NOT used as the data source.
- No source code under `src/` is modified by this plan (it is a pure spike).
</verification>

<success_criteria>
- `09-01-data-shape-spike-SUMMARY.md` exists at the path declared in `files_modified`.
- The seven required section headings are present (verified by the Task 3 grep gate).
- The Implications-for-Plan-03 line tells the next executor exactly what to lock in.
- The spike artifact is bit-reproducible offline — running the spike script on a clean checkout produces identical probe outputs.
</success_criteria>

<output>
Create `.planning/phases/09-compare-versions/09-01-data-shape-spike-SUMMARY.md` when done.
</output>
