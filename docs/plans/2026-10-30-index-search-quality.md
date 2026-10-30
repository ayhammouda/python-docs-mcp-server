# Index Search Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make search find words that end a sentence, file each code example
under its own section or API entry, and stamp the index format so `doctor`
tells users when to rebuild.

**Architecture:** One PR on branch `fix/index-search-quality`, cut from `main`
after #162 merges (roadmap Wave 1). Three fixes, one commit each, each with its
test:
- Task 1 changes the `sections_fts` tokenizer (finding 1, D1).
- Task 2 changes how `extract_code_blocks` finds a block's anchor (finding 2).
- Task 3 stamps `PRAGMA user_version = 1` and adds a `doctor` probe (D2).

Task 4 rebuilds one full 3.11–3.13 index in an isolated cache and runs the
retrieval regression gate once for both findings (D3). Task 5 runs the gate and
opens the PR. Users get the fixes only after they rebuild. An old index keeps
working, with the old behaviour.

**Tech Stack:**
- SQLite FTS5 `unicode61` tokenizer (SQLite 3.50.4 locally)
- BeautifulSoup (`bs4`) over the Sphinx HTML body of each `.fjson` file
- click (`doctor`), platformdirs 4.11.0 (locked)
- uv, pytest (`asyncio_mode = "auto"`), Ruff, Pyright (standard mode)

**Spec:**
- `docs/plans/2026-10-30-audit-fixes-roadmap.md`: D1, D2, D3, PR map row 1,
  Global Constraints.
- `docs/research/2026-10-30-audit-fix-best-practices.md`: "Search and
  retrieval" item 1; "Ingestion, storage and runtime" item 1, and item 6 for
  the `PRAGMA user_version` part only. Where the research refines the audit,
  the research wins.

## Global Constraints

- **Precondition:** `docs/plans/2026-10-30-*.md` and `docs/research/2026-10-30-audit-fix-best-practices.md` are on `main` (roadmap Wave 0.5); stop if not.
- **Base:** `main` after #162 merges. Never merge `feat/mcp-sdk-v2` into this
  branch.
- **Line numbers** are at `c03e497`. PR 3 and PR 5 edit some of the same
  files, so if a cited line has moved, find the named symbol.
- **TDD:** follow superpowers:test-driven-development. Write each failing test
  and watch it fail before the fix. One finding is one commit, with its test
  and its fix.
- **D1:** only `sections_fts` changes, to
  `unicode61 remove_diacritics 2 tokenchars '_'`. `symbols_fts` and
  `examples_fts` keep `tokenchars '._'`. Do not touch `fts5_escape` or
  `build_match_expression`.
- **D2:** `schema.sql` stamps `PRAGMA user_version = 1;`. `doctor` reports FAIL
  with a `build-index` command when an index has another version. No startup
  refusal: the server keeps serving old indexes.
- Do not edit `tests/fixtures/sample_library.fjson`.
- **Existing tests:** never delete one or weaken an assertion. Correcting a
  docstring or comment is allowed; the PR body lists each one as "assertions
  unchanged".
- **Forbidden territory:** `src/mcp_server_python_docs/storage/schema.sql`
  (`AGENT-EXECUTION-PIPELINE.md` §2). The PR gets the `supervisor-review`
  label and a `## Why this triggered supervisor review` section that cites:
  "The owner (Aymen Hammouda) asked on 2026-10-30 to implement all audit
  findings; decisions in `docs/plans/2026-10-30-audit-fixes-roadmap.md`."
- No new runtime dependency and no new runtime network access. No tool name,
  parameter, description or result shape changes.
- **Retrieval check:** run
  `uv run --locked python -m benchmarks.regression --output <file>` on a full
  `build-index --versions 3.11,3.12,3.13` index. Record changed results with
  `--record`. The baseline change needs independent review, so this PR never
  edits `docs/benchmarks/regression-baseline.json`.
- **CHANGELOG:** entries go under `## [Unreleased]`, grouped as Changed and
  Fixed. One entry says a rebuild is required and that `doctor` prints the
  exact `build-index` command, for the versions each index holds.
- **Gate (`AGENTS.md`),** run fresh before the PR:
  - `uv run --locked ruff check src/ tests/ benchmarks/ ops/ .github/scripts/`
  - `uv run --locked pyright src/ benchmarks/`
  - `uv run --locked pytest --tb=short -q`
- **Commits and PR:** conventional prefixes; every commit ends with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. The PR body
  follows `.github/PULL_REQUEST_TEMPLATE/agent.md` and ends with
  `🤖 Generated with [Claude Code](https://claude.com/claude-code)`. Never
  self-merge.

## Checked on 2026-10-30 against `c03e497` (Python 3.13.10, SQLite 3.50.4, platformdirs 4.11.0)

- **Whole plan on a scratch copy.** With this plan's code changes and its
  exact tests, the suite passed: 666 passed (650 existing, 16 new). Ruff (gate
  scope) and Pyright (`src/ benchmarks/`) were clean. Every new test failed on
  the unchanged code, with the message given in its task. The two guards,
  `test_search_still_matches_dotted_name` and
  `test_bare_module_name_still_resolves_to_the_module`, pass before and after.
- **Ranking shift, measured.** On the real 3.13 `library/json` page (36
  sections, fetched 2026-10-30), a bare `json` query to `search_sections`
  puts `module-json` 2nd before the change and 4th after it, behind
  `json.JSONDecodeError` and `json.dumps`. The cause: headings such as
  `json. dumps ( obj )` index `json.` today and `json` after the change. On a
  small synthetic seed with six such headings, the module section falls to
  7th. So "the module section stays first" does not hold even today, and
  cannot be pinned. The default `kind="auto"` path answers a bare module name
  through the symbol table, which this change does not touch.
- **The version stamp applies.** `bootstrap_schema` drops the three FTS tables
  first and then runs `schema.sql` through `executescript()`. A
  `PRAGMA user_version = 1;` at the end of the file is applied, and the
  drop-and-recreate order is unchanged. `ingest_inventory` calls
  `bootstrap_schema` once per version; that only re-stamps 1.
- **Real markup.** The 3.13 `library/asyncio-task` page (fetched 2026-10-30)
  puts ids on `<section>` elements and none on headings:
  `<section id="task-groups"><span id="taskgroups"></span><h2>`. Its examples
  sit after an entry's `dl`, in nested sections, and inside a `dd`
  (`asyncio.timeout`). Today `extract_code_blocks` gives 32 of the page's 32
  blocks an empty anchor. With Task 2, all 32 map to anchors that
  `extract_sections` produces.
- **Cache isolation.** platformdirs 4.11.0 honours `XDG_CACHE_HOME` on macOS
  as well as Linux (`XDGMixin`), so `XDG_CACHE_HOME=<dir>` puts the index at
  `<dir>/mcp-python-docs/index.db` on both. It treats an empty value as unset
  (`platformdirs/_xdg.py:54`), so a lost variable silently points at the live
  index. uv also follows `XDG_CACHE_HOME`, so pin `UV_CACHE_DIR` as well.
- **Rebuild versions.** `build-index` makes the highest requested version the
  default (`__main__.py:209`), and `SUPPORTED_DOC_VERSIONS_CSV` includes the
  opt-in 3.15. So `doctor`'s rebuild command lists the versions the index
  already holds.
- **CI runs the same gate.** `.github/workflows/product-quality.yml` ("Product
  regression") rebuilds the index when `ingestion/`, `storage/` or
  `__main__.py` change. It runs the gate on the PR, fails on a lost hit, and
  uploads `regression-results.json` as the `product-regression-results`
  artifact.

## Review Focus

1. **Words with a leading dot or a trailing ellipsis** (`.pyc`,
   `module...`). Expected: a bare-word query finds them. Pinned by Task 1,
   `test_search_finds_leading_dot_and_ellipsis_words`.
2. **An API entry with several signatures, where only the first `dt` has an
   id.** CPython renders `range(stop)` / `range(start, stop, step=1)` this way.
   Expected: the entry's example is filed under `range`. Pinned by Task 2,
   `test_code_block_in_multi_signature_entry_uses_first_id`.
3. **An id-less nested `dl` (a field list) inside an entry's `dd`.** Expected:
   the example is filed under the enclosing entry, not the section. Pinned by
   Task 2, `test_code_block_in_field_list_attaches_to_enclosing_entry`.
4. **A bare module-name query** (`json`). API headings such as
   `json. dumps ( obj )` now match it in the weight-10 heading column. With
   `kind="section"` they can rank above the module section; see "Ranking
   shift, measured" above.
   Expected:
   - the default `kind="auto"` call still returns the module first, through
     the symbol table;
   - the `kind="section"` shift is measured on the full index (Task 4 Step 5)
     and reported in the PR body, not hidden.

   Pinned by Task 1, `test_bare_module_name_still_resolves_to_the_module`, a
   guard that passes before and after.
5. **An `index.db` that is not a SQLite file** (a truncated copy or a partial
   download). Expected: `FAIL: Index format` with the rebuild command, exit
   code 1, no traceback. Pinned by Task 3, `test_doctor_reports_unreadable_index`.

---

### Task 1: Index sentence-final words in `sections_fts`

**Files:**
- Modify: `src/mcp_server_python_docs/storage/schema.sql:5-8,112-124`
  (forbidden territory)
- Modify: `docs/architecture/DESIGN.md:238-245`, `CHANGELOG.md`
  (`## [Unreleased]`, `### Fixed`)
- Modify (docstrings and comments only): `tests/test_schema.py:66-70,133`,
  `tests/test_retrieval.py:399`
- Test: `tests/test_retrieval.py` (new section after `test_normalize_scores`,
  before `# Budget tests (RETR-08)` at line 528)

**Interfaces:**
- Produces: `sections_fts` uses
  `tokenize="unicode61 remove_diacritics 2 tokenchars '_'"`. A dot separates
  tokens there, so a quoted dotted name matches as a phrase.

- [ ] **Step 1: Write the failing tests.** `sqlite3`, `search_sections`,
  `build_match_expression` and `fts5_escape` are already imported. `test_db`
  comes from `tests/conftest.py`.

```python
# ---------------------------------------------------------------------------
# sections_fts tokenizer: sentence punctuation (audit finding 1)
# ---------------------------------------------------------------------------


def _add_section(
    conn: sqlite3.Connection,
    content: str,
    *,
    anchor: str = "intro",
    heading: str = "Introduction",
) -> None:
    """Index one library/json section in a bootstrapped database."""
    conn.execute(
        "INSERT OR IGNORE INTO doc_sets (id, source, version, language, label, is_default) "
        "VALUES (1, 'python-docs', '3.13', 'en', 'Python 3.13', 1)"
    )
    conn.execute(
        "INSERT OR IGNORE INTO documents (id, doc_set_id, uri, slug, title, content_text, "
        "char_count) VALUES (1, 1, 'library/json.html', 'library/json', 'json', '', 0)"
    )
    conn.execute(
        "INSERT INTO sections (document_id, uri, anchor, heading, level, ordinal, "
        "content_text, char_count) VALUES (1, ?, ?, ?, 2, 0, ?, ?)",
        (f"library/json.html#{anchor}", anchor, heading, content, len(content)),
    )
    conn.execute("INSERT INTO sections_fts(sections_fts) VALUES('rebuild')")


def test_search_finds_word_that_ends_a_sentence(test_db):
    _add_section(test_db, "Encode objects with the json module.")
    hits = search_sections(test_db, build_match_expression("json module", {}), None, 5)
    assert [hit.slug for hit in hits] == ["library/json"]


def test_search_still_matches_dotted_name(test_db):
    _add_section(test_db, "Run tasks with asyncio.TaskGroup today.")
    assert len(search_sections(test_db, fts5_escape("asyncio.TaskGroup"), None, 5)) == 1


def test_search_finds_bare_name_inside_dotted_name(test_db):
    _add_section(test_db, "Run tasks with asyncio.TaskGroup today.")
    assert len(search_sections(test_db, fts5_escape("TaskGroup"), None, 5)) == 1


def test_search_finds_leading_dot_and_ellipsis_words(test_db):
    _add_section(test_db, "Byte code lives in .pyc files; see the json module...")
    assert len(search_sections(test_db, fts5_escape("pyc"), None, 5)) == 1
    assert len(search_sections(test_db, build_match_expression("json module", {}), None, 5)) == 1


def test_bare_module_name_still_resolves_to_the_module(test_db):
    """Guard: API headings such as 'json. dumps ( obj )' now match a bare 'json' in
    sections_fts, but a bare module name takes the symbol fast path, which this
    change does not touch."""
    from mcp_server_python_docs.services.search import SearchService

    _add_section(
        test_db,
        "The json module exposes an API familiar to users of marshal and pickle.",
        anchor="module-json",
        heading="json — JSON encoder and decoder",
    )
    for name in ("dump", "dumps", "load", "loads", "JSONEncoder", "JSONDecoder"):
        heading = f"json. {name} ( obj )"
        _add_section(test_db, f"{name} works on obj.", anchor=f"json.{name}", heading=heading)
    test_db.execute(
        "INSERT INTO symbols (doc_set_id, qualified_name, normalized_name, module, "
        "symbol_type, uri, anchor) VALUES (1, 'json', 'json', 'json', 'module', "
        "'library/json.html#module-json', 'module-json')"
    )
    hits = SearchService(test_db, {}).search("json", version="3.13").hits
    assert hits[0].anchor == "module-json"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --locked pytest tests/test_retrieval.py -q -k "search_finds or still_matches_dotted or bare_module"`

Expected: 3 failed, 2 passed.
- `test_search_finds_word_that_ends_a_sentence`: `assert [] == ['library/json']`.
- `test_search_finds_bare_name_inside_dotted_name` and
  `test_search_finds_leading_dot_and_ellipsis_words`: `assert 0 == 1`.
- The two guards pass:
  - `test_search_still_matches_dotted_name` keeps dotted names working;
  - `test_bare_module_name_still_resolves_to_the_module` keeps the default
    `kind="auto"` answer for a bare module name, once the API headings match
    too.

- [ ] **Step 3: Change the `sections_fts` tokenizer (forbidden territory:
  `schema.sql`).**
  - In the `sections_fts` definition (line 123), set
    `tokenize="unicode61 remove_diacritics 2 tokenchars '_'"`.
  - Leave `symbols_fts` (line 129) and `examples_fts` (line 135) unchanged.
  - Rewrite the header comment (lines 5-8) and the FTS comment block (lines
    112-117). They must say three things:
    - `sections_fts` holds prose and uses `tokenchars '_'`. A dot splits
      tokens there, so a sentence-final word matches and a quoted dotted name
      matches as a phrase.
    - `symbols_fts` and `examples_fts` keep `tokenchars '._'`, so a dotted
      name is one token there.
    - Porter stemming stays off (STOR-02).

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `uv run --locked pytest tests/test_retrieval.py -q -k "search_finds or still_matches_dotted or bare_module"`

Expected: 5 passed.

- [ ] **Step 5: Run the whole suite**

Run: `uv run --locked pytest --tb=short -q`

Expected: all tests pass, including STOR-02
`test_fts5_tokenizer_preserves_identifiers`, `test_fts5_escape_fuzz` and
`tests/test_retrieval_regression.py`.

- [ ] **Step 6: Correct the prose that describes the old tokenizer.**
  - In `docs/architecture/DESIGN.md:238-245`, say that `sections_fts` uses
    `tokenchars '_'` (dotted names match as phrases), while `symbols_fts` and
    `examples_fts` keep `tokenchars '._'`. Refresh the paragraph's three
    `schema.sql` line numbers if your header edit moved them. PR 3 edits the
    same line numbers; if it landed first, keep its numbers.
  - Correct these to the same fact, with assertions unchanged:
    - the `test_fts5_tokenizer_preserves_identifiers` docstring in
      `tests/test_schema.py`, lines 66-67 ("as single tokens") and 69-70
      (`tokenchars '._'` on all three tables);
    - the comment at `tests/test_schema.py:133` ("exact-token search on
      headings");
    - the docstring at `tests/test_retrieval.py:399`, where "asyncio.TaskGroup"
      is now two tokens in `sections_fts`.
  - Add this under `## [Unreleased]` → `### Fixed` in `CHANGELOG.md`:

```markdown
- `search_docs` now finds a word that ends a sentence (`json module` matches
  "the json module.") and a bare name inside a dotted one (`TaskGroup` in
  `asyncio.TaskGroup`). Rebuild the index to get this fix (see Changed).
```

- [ ] **Step 7: Commit**

```bash
git add src/mcp_server_python_docs/storage/schema.sql tests/test_retrieval.py tests/test_schema.py docs/architecture/DESIGN.md CHANGELOG.md
git commit -m "fix: index sentence-final words in sections_fts" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Expected: `git status --short` prints nothing, and `git log -1 --format=%s`
prints `fix: index sentence-final words in sections_fts`.

### Task 2: File code examples under their own section or API entry

**Files:**
- Modify: `src/mcp_server_python_docs/ingestion/sphinx_json.py:377-438`
  (`extract_code_blocks`; the anchor loop is at 419-426)
- Create: `tests/fixtures/sample_modern_sections.fjson`
- Modify: `docs/architecture/DESIGN.md:166-168`, `CHANGELOG.md`
  (`## [Unreleased]`, `### Fixed`)
- Test: `tests/test_ingestion.py` (`TestExtractCodeBlocks` at line 416,
  `TestDocumentContent` at line 531)

**Interfaces:**
- Produces: `_code_block_anchor(div: Tag) -> str`, a private function in
  `sphinx_json.py`.
- `extract_code_blocks(body_html: str) -> list[dict]` keeps its signature.
  Only its `section_anchor` values change.

- [ ] **Step 1: Create the fixture.** Write
  `tests/fixtures/sample_modern_sections.fjson` with
  `json.dumps(data, indent=2) + "\n"`, where `data` has these keys:
  - `"title": "Coroutines and tasks"`
  - `"current_page_name": "library/asyncio-task"`
  - `"sourcename": "library/asyncio-task.rst"`
  - `"body"`: exactly the HTML below. It is trimmed from the 3.13
    `library/asyncio-task` page and keeps its real shape: ids on `<section>`
    and `<dt>`, none on headings, and an extra `<span id>`.

```html
<section id="coroutines-and-tasks">
<h1>Coroutines and tasks<a class="headerlink" href="#coroutines-and-tasks" title="Link to this heading">¶</a></h1>
<p>This section outlines high-level asyncio APIs to work with coroutines and Tasks.</p>
<section id="task-groups">
<span id="taskgroups"></span><h2>Task groups<a class="headerlink" href="#task-groups" title="Link to this heading">¶</a></h2>
<p>Task groups combine a task creation API with a convenient
and reliable way to wait for all tasks in the group to finish.</p>
<dl class="py class">
<dt class="sig sig-object py" id="asyncio.TaskGroup">
<em class="property"><span class="k"><span class="pre">class</span></span><span class="w"> </span></em><span class="sig-prename descclassname"><span class="pre">asyncio.</span></span><span class="sig-name descname"><span class="pre">TaskGroup</span></span><a class="headerlink" href="#asyncio.TaskGroup" title="Link to this definition">¶</a></dt>
<dd><p>An asynchronous context manager holding a group of tasks.</p>
<div class="versionadded">
<p><span class="versionmodified added">Added in version 3.11.</span></p>
</div>
</dd></dl>

<p>Example:</p>
<div class="highlight-python3 notranslate"><div class="highlight"><pre><span></span>async def main():
    async with asyncio.TaskGroup() as tg:
        task1 = tg.create_task(some_coro(...))
</pre></div>
</div>
<section id="terminating-a-task-group">
<h3>Terminating a task group<a class="headerlink" href="#terminating-a-task-group" title="Link to this heading">¶</a></h3>
<p>Termination can be achieved by adding an exception-raising task to the task group.</p>
<div class="highlight-python notranslate"><div class="highlight"><pre><span></span>class TerminateTaskGroup(Exception):
    """Exception raised to terminate a task group."""
</pre></div>
</div>
</section>
</section>
<section id="timeouts">
<h2>Timeouts<a class="headerlink" href="#timeouts" title="Link to this heading">¶</a></h2>
<dl class="py function">
<dt class="sig sig-object py" id="asyncio.timeout">
<span class="sig-prename descclassname"><span class="pre">asyncio.</span></span><span class="sig-name descname"><span class="pre">timeout</span></span><span class="sig-paren">(</span><em class="sig-param"><span class="n"><span class="pre">delay</span></span></em><span class="sig-paren">)</span><a class="headerlink" href="#asyncio.timeout" title="Link to this definition">¶</a></dt>
<dd><p>Return an asynchronous context manager that can be used to limit the amount
of time spent waiting on something.</p>
<p>Example:</p>
<div class="highlight-python3 notranslate"><div class="highlight"><pre><span></span>async def main():
    async with asyncio.timeout(10):
        await long_running_task()
</pre></div>
</div>
</dd></dl>

</section>
</section>
```

- [ ] **Step 2: Write the failing tests.** Add the first four to
  `TestExtractCodeBlocks`, and the last one to `TestDocumentContent`.

```python
    def test_anchor_comes_from_enclosing_section_or_api_entry(self):
        body = (
            '<section id="task-groups"><h2>Task Groups</h2>'
            '<div class="highlight-python3"><pre>a = 1</pre></div>'
            '<dl class="py class"><dt id="asyncio.TaskGroup">class TaskGroup</dt>'
            '<dd><div class="highlight-python3"><pre>b = 2</pre></div></dd></dl></section>'
        )
        anchors = [block["section_anchor"] for block in extract_code_blocks(body)]
        assert anchors == ["task-groups", "asyncio.TaskGroup"]

    def test_code_block_in_multi_signature_entry_uses_first_id(self):
        body = (
            '<section id="ranges"><h2>Ranges</h2><dl class="py class">'
            '<dt id="range">range(stop)</dt><dt>range(start, stop, step=1)</dt>'
            '<dd><div class="highlight-python3"><pre>r = range(3)</pre></div></dd>'
            "</dl></section>"
        )
        assert [block["section_anchor"] for block in extract_code_blocks(body)] == ["range"]

    def test_code_block_in_field_list_attaches_to_enclosing_entry(self):
        body = (
            '<section id="json"><h1>json</h1><dl class="py function">'
            '<dt id="json.dumps">dumps(obj)</dt><dd><dl class="field-list">'
            '<dt>Parameters</dt><dd><div class="highlight-python3"><pre>dumps(1)</pre>'
            "</div></dd></dl></dd></dl></section>"
        )
        assert [block["section_anchor"] for block in extract_code_blocks(body)] == ["json.dumps"]

    def test_anchor_comes_from_legacy_div_section(self):
        body = (
            '<div class="section" id="json"><h1>json</h1>'
            '<div class="highlight-python3"><pre>x = 1</pre></div></div>'
        )
        assert [block["section_anchor"] for block in extract_code_blocks(body)] == ["json"]
```

```python
    def test_examples_join_their_own_section(self, populated_db, fixtures_dir):
        """Real Sphinx markup: ids on <section> and <dt>, none on headings."""
        fixture = fixtures_dir / "sample_modern_sections.fjson"
        assert ingest_fjson_file(populated_db, fixture, doc_set_id=1)
        rows = populated_db.execute(
            "SELECT s.anchor FROM examples e JOIN sections s ON s.id = e.section_id "
            "ORDER BY e.ordinal"
        ).fetchall()
        assert [row[0] for row in rows] == [
            "task-groups",
            "terminating-a-task-group",
            "asyncio.timeout",
        ]
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run --locked pytest tests/test_ingestion.py -q -k "anchor_comes_from or multi_signature or field_list or join_their_own"`

Expected: 5 failed.
- `assert ['', ''] == ['task-groups', 'asyncio.TaskGroup']`
- `assert [''] == ['range']`
- `assert [''] == ['json.dumps']`
- `assert [''] == ['json']`
- The fixture test: every example falls back to the page's first section,
  `['coroutines-and-tasks', 'coroutines-and-tasks', 'coroutines-and-tasks']`.

- [ ] **Step 4: Add `_code_block_anchor(div: Tag) -> str` above
  `extract_code_blocks`, and call it in place of the loop at lines 419-426.**
  It walks `div.parents` and returns the first match:
  - a `dd` whose `find_previous_sibling("dt", id=True)` exists gives that
    `dt`'s id;
  - otherwise a `section`, or a `div` with class `section`, that has an id
    gives that id;
  - otherwise it falls back to today's `find_previous(h1-h6, id=True)`
    lookup, else `""`.

  The tests above fix this order. Leave `extract_sections` and the
  first-section fallback in `ingest_fjson_file` as they are.

- [ ] **Step 5: Run the ingestion tests**

Run: `uv run --locked pytest tests/test_ingestion.py -q`

Expected: all passed. This includes the old-fixture test
`test_section_anchor_association`, which now goes through the heading fallback.

- [ ] **Step 6: Update the docs.**
  - In `docs/architecture/DESIGN.md:166-168`, say how `section_anchor` is
    chosen. In order: the id of the `dt` that heads the enclosing `dd`, then
    the id of the enclosing `<section>`, then the previous heading with an id.
  - Add this under `## [Unreleased]` → `### Fixed` in `CHANGELOG.md`:

```markdown
- Code examples are now filed under the section or API entry that contains
  them, not under the page's first section, so `kind="example"` hits point at
  the right anchor. Rebuild the index to get this fix (see Changed).
```

- [ ] **Step 7: Commit**

```bash
git add src/mcp_server_python_docs/ingestion/sphinx_json.py tests/fixtures/sample_modern_sections.fjson tests/test_ingestion.py docs/architecture/DESIGN.md CHANGELOG.md
git commit -m "fix: file code examples under their own section or API entry" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Expected: `git status --short` prints nothing, and `git log -1 --format=%s`
prints `fix: file code examples under their own section or API entry`.

### Task 3: Stamp the index format and check it in `doctor`

**Files:**
- Modify: `src/mcp_server_python_docs/storage/schema.sql:2` and the end of the
  file (forbidden territory)
- Modify: `src/mcp_server_python_docs/storage/db.py:14` (new constant below
  `logger`)
- Modify: `src/mcp_server_python_docs/__main__.py:503` (import) and `:581` (new
  probe after "Index database")
- Modify: `README.md:327-328`, `CHANGELOG.md` (`## [Unreleased]`,
  `### Changed`)
- Test: `tests/test_schema.py` (after `test_bootstrap_idempotent`, which ends at
  line 332), `tests/test_doctor.py` (new class `TestIndexFormatProbe`)

**Interfaces:**
- Produces:
  - `INDEX_FORMAT_VERSION = 1` in `mcp_server_python_docs.storage.db`;
  - a `doctor` probe named `Index format`;
  - every index built from now on has `PRAGMA user_version` 1.

- [ ] **Step 1: Write the failing tests.** In `tests/test_schema.py`:

```python
def test_bootstrap_schema_stamps_index_format_version():
    """D2: every new index records the format this release reads."""
    from mcp_server_python_docs.storage.db import INDEX_FORMAT_VERSION

    conn = _make_db()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == INDEX_FORMAT_VERSION == 1
```

  In `tests/test_doctor.py`, add these module imports and helpers, then the
  class:

```python
from mcp_server_python_docs.ingestion.cpython_versions import SUPPORTED_DOC_VERSIONS_CSV
from mcp_server_python_docs.storage.db import (
    bootstrap_schema,
    get_index_path,
    get_readwrite_connection,
)
```

```python
REBUILD = f"python-docs-mcp-server build-index --versions {SUPPORTED_DOC_VERSIONS_CSV}"


def _doctor_with_index(tmp_path, monkeypatch, *, user_version=None, raw=None, versions=()):
    """Run doctor against an isolated cache whose index.db this test controls."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    index_path = get_index_path()  # the path the doctor subprocess will compute
    if raw is not None:
        index_path.parent.mkdir(parents=True)
        index_path.write_bytes(raw)
    else:
        conn = get_readwrite_connection(index_path)
        bootstrap_schema(conn)
        for version in versions:
            conn.execute(
                "INSERT INTO doc_sets (version, label) VALUES (?, ?)",
                (version, f"Python {version}"),
            )
        conn.commit()
        conn.execute(f"PRAGMA user_version = {user_version}")
        conn.close()
    return subprocess.run(
        [sys.executable, "-m", "mcp_server_python_docs", "doctor"],
        capture_output=True,
        text=True,
        timeout=15,
        env=_isolated_cache_env(str(tmp_path)),
    )


class TestIndexFormatProbe:
    """D2: doctor tells users when their index needs a rebuild."""

    def test_doctor_fails_on_unversioned_index(self, tmp_path, monkeypatch):
        result = _doctor_with_index(tmp_path, monkeypatch, user_version=0)
        assert "FAIL: Index format -- version 0, expected 1" in result.stderr
        assert REBUILD in result.stderr
        assert result.returncode == 1

    def test_doctor_passes_current_index_format(self, tmp_path, monkeypatch):
        result = _doctor_with_index(tmp_path, monkeypatch, user_version=1)
        assert "PASS: Index format -- version 1" in result.stderr

    def test_doctor_fails_on_newer_index_format(self, tmp_path, monkeypatch):
        result = _doctor_with_index(tmp_path, monkeypatch, user_version=2)
        assert "FAIL: Index format -- version 2, expected 1" in result.stderr
        assert REBUILD in result.stderr

    def test_doctor_rebuild_command_keeps_indexed_versions(self, tmp_path, monkeypatch):
        result = _doctor_with_index(
            tmp_path, monkeypatch, user_version=0, versions=("3.12", "3.13")
        )
        assert "-- run: python-docs-mcp-server build-index --versions 3.12,3.13\n" in result.stderr

    def test_doctor_reports_unreadable_index(self, tmp_path, monkeypatch):
        result = _doctor_with_index(tmp_path, monkeypatch, raw=b"not a database" * 100)
        assert "FAIL: Index format -- unreadable" in result.stderr
        assert REBUILD in result.stderr
        assert "Traceback" not in result.stderr
        assert result.returncode == 1
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --locked pytest tests/test_schema.py tests/test_doctor.py -q -k "TestIndexFormatProbe or stamps_index_format"`

Expected: 6 failed.
- The schema test fails with
  `ImportError: cannot import name 'INDEX_FORMAT_VERSION' from 'mcp_server_python_docs.storage.db'`.
- Each doctor test fails on its `Index format` assertion. Today's stderr has
  no such line and ends with `All checks passed.`

- [ ] **Step 3: Stamp the format in `schema.sql` (forbidden territory).**
  - Append a short comment block and the stamp as the last statement:
    `PRAGMA user_version = 1;`. The comment says the value must equal
    `storage.db.INDEX_FORMAT_VERSION`, and that both are bumped when a change
    needs every user to rebuild. The stamp goes last so that it lands only
    after every `CREATE` has succeeded.
  - Replace the header's `-- Version: Phase 2 (2026-04-16)` (line 2) with
    `-- Index format: 1 (PRAGMA user_version, end of file)`.

- [ ] **Step 4: Add the constant.** In `storage/db.py`, directly below
  `logger`, add `INDEX_FORMAT_VERSION = 1` with a one-line comment: it must
  equal `PRAGMA user_version` in `schema.sql`.

- [ ] **Step 5: Add the `Index format` probe to `doctor`.**
  - **Import.** Import `INDEX_FORMAT_VERSION` and `get_readonly_connection`
    next to `get_cache_dir` and `get_index_path` (line 503). PR 5 changes
    `doctor`'s FTS5 probe and may edit this same import line. If it landed
    first, expect a conflict here and keep both sets of names.
  - **Placement.** Right after the `("Index database", ...)` result (line
    581), add the probe, only when `index_exists`. A missing index stays the
    job of the existing probe.
  - **Reading.** The probe opens the index with
    `get_readonly_connection(index_path)`. On that one connection it reads
    `PRAGMA user_version` and the indexed versions, then closes it in a
    `finally`. It catches `sqlite3.Error`; `sqlite3` is already imported in
    `doctor`.
  - **Result.** PASS only when the value equals `INDEX_FORMAT_VERSION`. Any
    other value, older or newer, is a FAIL, because a rebuild fixes both.
  - **Versions.** `<versions>` is
    `",".join(SELECT version FROM doc_sets ORDER BY version)`. Fall back to
    `SUPPORTED_DOC_VERSIONS_CSV` when the index is unreadable or has no rows.
    A rebuild then keeps the user's versions and their default.
  - **Details.** `<run>` is
    `run: python-docs-mcp-server build-index --versions <versions>`. The
    details are exact:
    - PASS: `version {found}`
    - FAIL: `version {found}, expected {INDEX_FORMAT_VERSION} -- <run>`
    - Unreadable: `unreadable ({error}) -- <run>`

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run --locked pytest tests/test_schema.py tests/test_doctor.py -q`

Expected: all passed. An index with no `doc_sets` rows now prints the
fallback list:
`x FAIL: Index format -- version 0, expected 1 -- run: python-docs-mcp-server build-index --versions 3.10,3.11,3.12,3.13,3.14,3.15`.

- [ ] **Step 7: Update the user-facing docs.**
  - In `README.md:327-328`, add "the index format" to the list of things
    `doctor` checks. Then add: "If it reports `FAIL: Index format`, run the
    `build-index` command it prints; it lists the versions your index already
    holds."
  - Add this under `## [Unreleased]` → `### Changed` in `CHANGELOG.md`:

```markdown
- **Rebuild the index after upgrading.** New indexes record format version 1
  (`PRAGMA user_version`). `doctor` reports `FAIL: Index format` for an index
  built by another release and prints the `build-index` command for the
  versions that index holds. Run it, then restart your MCP client. An index you
  do not rebuild keeps working, with the old search behaviour.
```

- [ ] **Step 8: Commit**

```bash
git add src/mcp_server_python_docs/storage/schema.sql src/mcp_server_python_docs/storage/db.py src/mcp_server_python_docs/__main__.py tests/test_schema.py tests/test_doctor.py README.md CHANGELOG.md
git commit -m "feat: stamp the index format and check it in doctor" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Expected: `git status --short` prints nothing, and `git log -1 --format=%s`
prints `feat: stamp the index format and check it in doctor`.

### Task 4: Rebuild a full index and run the retrieval regression

No code changes. This task produces evidence for the PR body.

**Interfaces:**
- Consumes: Tasks 1-3, committed.
- Produces: `/tmp/pd-pr1-index/mcp-python-docs/index.db`,
  `/tmp/pd-pr1-index/regression.json` and, if anything changed,
  `/tmp/pd-pr1-index/regression-recorded.json`.

- [ ] **Step 1: Pin the isolated cache and prove it.** Agent shells do not keep exported variables, so every command in Tasks 4–5 starts with `ISO=/tmp/pd-pr1-index;` and passes `UV_CACHE_DIR="$(uv cache dir)" XDG_CACHE_HOME="${ISO:?}"` as a prefix. `${ISO:?}` aborts on an empty value.

Run: `ISO=/tmp/pd-pr1-index; mkdir -p "$ISO"; UV_CACHE_DIR="$(uv cache dir)" XDG_CACHE_HOME="${ISO:?}" uv run --locked python -c "from mcp_server_python_docs.storage.db import get_index_path; print(get_index_path())"`

Expected: `/tmp/pd-pr1-index/mcp-python-docs/index.db`. Stop if the path is
under `~/Library/Caches` or `~/.cache`: that is the live index.

- [ ] **Step 2: Build the full index.** This needs network and takes 10-30
  minutes. Run it in the background, or with a timeout of at least 45 minutes,
  and wait for it to exit. Do not interrupt it.

Run: `ISO=/tmp/pd-pr1-index; UV_CACHE_DIR="$(uv cache dir)" XDG_CACHE_HOME="${ISO:?}" uv run --locked python-docs-mcp-server build-index --versions 3.11,3.12,3.13`

Expected:
- Exit code 0, and stderr ends with
  `Index rebuilt. Restart your MCP client to pick up the new index.`
- `/tmp/pd-pr1-index/mcp-python-docs/index.db` exists.

- [ ] **Step 3: Check `doctor` on the new index**

Run: `ISO=/tmp/pd-pr1-index; UV_CACHE_DIR="$(uv cache dir)" XDG_CACHE_HOME="${ISO:?}" uv run --locked python-docs-mcp-server doctor`

Expected: `+ PASS: Index format -- version 1` and `All checks passed.`

- [ ] **Step 4: Validate the corpus.** Pipeline §5 requires this gate for
  ingestion and storage changes.

Run: `ISO=/tmp/pd-pr1-index; UV_CACHE_DIR="$(uv cache dir)" XDG_CACHE_HOME="${ISO:?}" uv run --locked python-docs-mcp-server validate-corpus`

Expected: `Corpus validation PASSED` and exit code 0.

- [ ] **Step 5: Check finding 2 and measure the ranking shift on real data**

Run: `ISO=/tmp/pd-pr1-index; UV_CACHE_DIR="$(uv cache dir)" XDG_CACHE_HOME="${ISO:?}" uv run --locked python -c "from mcp_server_python_docs.storage.db import get_index_path, get_readonly_connection as ro; from mcp_server_python_docs.services.search import SearchService; hits = SearchService(ro(get_index_path()), {}).search('long_running_task', version='3.13', kind='example').hits; print(sorted({h.anchor for h in hits}))"`

Expected: the set contains `asyncio.timeout` and does not contain
`coroutines-and-tasks`. On the old code, every asyncio-task example fell back
to `coroutines-and-tasks`.

  Then measure the ranking shift from Review Focus 4. This is a measurement,
  not a gate.

Run: `ISO=/tmp/pd-pr1-index; UV_CACHE_DIR="$(uv cache dir)" XDG_CACHE_HOME="${ISO:?}" uv run --locked python -c "from mcp_server_python_docs.storage.db import get_index_path, get_readonly_connection as ro; from mcp_server_python_docs.services.search import SearchService; print([h.anchor for h in SearchService(ro(get_index_path()), {}).search('json', version='3.13', kind='section').hits])"`

Expected: five anchors. Write down where `module-json` falls, or "not in the
top 5", for the PR body's ranking-shift note.

- [ ] **Step 6: Run the regression gate**

Run: `ISO=/tmp/pd-pr1-index; UV_CACHE_DIR="$(uv cache dir)" XDG_CACHE_HOME="${ISO:?}" uv run --locked python -m benchmarks.regression --output "${ISO:?}/regression.json"`

Expected: stdout `{"cases": 65, "regressions": []}` and exit code 0.
- If `regressions` lists anything, still do Steps 7 and 8.
- The PR then stays a draft until the independent baseline review decides. CI
  "Product regression" fails the same way.

- [ ] **Step 7: List every case that changed, gains included**

Run: `ISO=/tmp/pd-pr1-index; UV_CACHE_DIR="$(uv cache dir)" XDG_CACHE_HOME="${ISO:?}" uv run --locked python -c "import json,sys; a,b=(json.load(open(p))['cases'] for p in sys.argv[1:]); print(sorted(k for k in b if (a[k]['retrieval_hit_at_5'], set(a[k]['resolved_citations'])) != (b[k]['retrieval_hit_at_5'], set(b[k]['resolved_citations']))))" "${ISO:?}/regression.json" docs/benchmarks/regression-baseline.json`

Expected: a list of case keys, possibly `[]`.

  Optionally, compare with `main`'s own latest CI run as well. That separates
  this PR's effect from drift between `main` and the committed baseline.
  1. Run: `gh run list --workflow product-quality.yml --branch main --status success --limit 1 --json databaseId --jq '.[0].databaseId'`
  2. Run: `ISO=/tmp/pd-pr1-index; gh run download <that id> --name product-regression-results --dir "${ISO:?}/ci-main"`
  3. Rerun this step's command with `"${ISO:?}/ci-main/regression-results.json"`
     in place of `docs/benchmarks/regression-baseline.json`.

- [ ] **Step 8: Record the changes if the list is not empty**

Run: `ISO=/tmp/pd-pr1-index; UV_CACHE_DIR="$(uv cache dir)" XDG_CACHE_HOME="${ISO:?}" uv run --locked python -m benchmarks.regression --record --output "${ISO:?}/regression-recorded.json"`

Expected: `Recorded evaluation; baseline changes require independent review.`
- Keep the file for the reviewer.
- For each changed case, write down the key, the hit before and after, and the
  citations added or removed. Task 5 puts this in the PR body.
- Do not edit `docs/benchmarks/regression-baseline.json`.

### Task 5: Run the gate and open the PR

- [ ] **Step 1: Run the gate fresh**

| Run | Expected |
|---|---|
| `uv run --locked ruff check src/ tests/ benchmarks/ ops/ .github/scripts/` | `All checks passed!` |
| `uv run --locked pyright src/ benchmarks/` | `0 errors, 0 warnings, 0 informations` |
| `uv run --locked pytest --tb=short -q` | all tests pass |
| `ISO=/tmp/pd-pr1-index; UV_CACHE_DIR="$(uv cache dir)" XDG_CACHE_HOME="${ISO:?}" uv run --locked python-docs-mcp-server doctor` | `All checks passed.` |

  Run `doctor` against the rebuilt index. Against an index built before this
  branch it reports `FAIL: Index format` by design (D2). Say so in the PR body.

- [ ] **Step 2: Get the owner's go-ahead before pushing.**
- [ ] **Step 3: Push and open the PR.**
  - Push `fix/index-search-quality`.
  - Open a PR against `main` titled
    `fix: find sentence-final words and file code examples under their own section`,
    with the `supervisor-review` label. Open it as a draft if Task 4 recorded a
    lost hit.
  - Follow `.github/PULL_REQUEST_TEMPLATE/agent.md`. No issue tracks the audit,
    so write
    `Refs docs/plans/2026-10-30-audit-fixes-roadmap.md (PR 1)` in place of
    `Closes #`.
  - The body includes:
    - one acceptance line per task, each with the command and its result;
    - the gate output, plus the `validate-corpus` result and the real-data
      check from Task 4;
    - a **Retrieval regression** section: the Task 4 Step 6 summary, and the
      changed cases from Step 7 with before and after values (or "no case
      changed"). If any case changed, add: "Baseline change recorded with
      `--record`; it needs independent review."
    - a **Ranking shift** note. API headings such as `json. dumps ( obj )` now
      match a bare `json` in the weight-10 heading column, so with
      `kind="section"` they can rank above the module section. Give the
      single-page numbers from "Checked on" (2nd before, 4th after) and the
      full-index rank from Task 4 Step 5. The default `kind="auto"` answer is
      unchanged (`test_bare_module_name_still_resolves_to_the_module`), and
      the regression gate covers the real corpus.
    - a **Rebuild required** note. `doctor` prints the `build-index` command
      for the versions each index holds.
    - a **Read-only probe** note. The doctor probe opens `index.db`
      read-only, which leaves `-wal` and `-shm` files beside a WAL-mode
      index, as the server already does. PR 4 switches new indexes to
      `journal_mode=DELETE`.
    - a **Docstring-only edits** line. The edits in `tests/test_schema.py` and
      `tests/test_retrieval.py` leave every assertion unchanged.
    - `## Why this triggered supervisor review`. It names:
      - the `schema.sql` change: the `sections_fts` tokenizer and
        `PRAGMA user_version = 1`;
      - the fact that doctor FAILs for every existing index until rebuild
        (D2);
      - the owner's approval: "The owner (Aymen Hammouda) asked on 2026-10-30
        to implement all audit findings; decisions in
        `docs/plans/2026-10-30-audit-fixes-roadmap.md`."
    - the product-evidence fields:
      - kind: bugfix;
      - the problem, with the research numbers: 3.2–6.7% of word tokens end
        in `.`, and 32 of 32 blocks fell back on the 3.13 page;
      - non-goals: no query-side change, no `examples_fts` change, no startup
        refusal;
      - the head and base SHAs.
    - the closing line
      `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

  Expected: `gh pr view --json url,labels,isDraft` prints the PR URL, the
  `supervisor-review` label, and `isDraft` false. `isDraft` is true only when
  Task 4 recorded a lost hit.
