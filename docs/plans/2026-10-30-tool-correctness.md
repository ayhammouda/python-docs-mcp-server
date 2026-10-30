# Tool Correctness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix four tool bugs that need no index rebuild, and expand search
synonyms once per query:
- `get_docs` slugs;
- `compare_versions` version notes;
- page splits inside an accent;
- per-call version detection.

**Architecture:** One PR on branch `fix/tool-correctness`, cut from `main`
after #162 merges (roadmap Wave 2). If PR 3 (`refactor/remove-dead-code`) has
merged, this PR also regenerates `tests/fixtures/tools-list.json`. Five
independent tasks, one commit each:

| Task | Change | Spec |
|---|---|---|
| 1 | `get_docs` slug resolution | finding 3 + N7, D4; changes tool description text |
| 2 | `compare_versions` notes | finding 4, D5 |
| 3 | The pager | finding 18, D6 |
| 4 | Version detection | finding 15 |
| 5 | The synonym refactor | research Search 5 |

Task 6 runs the regression gate on a full index and compares it with a run of
the base commit on the same index. Task 7 rebases, runs the gate and opens the
PR.

**Tech Stack:**
- `mcp` 2.3.0 (`MCPServer`), Pydantic ≥ 2.12
- stdlib `sqlite3`, `re`, `unicodedata`; anyio
- uv, pytest (`asyncio_mode = "auto"`), Ruff, Pyright (standard mode)

**Spec:**
- `docs/plans/2026-10-30-audit-fixes-roadmap.md`: D4, D5, D6, PR map row 2,
  Global Constraints.
- `docs/research/2026-10-30-audit-fix-best-practices.md`: "Search and
  retrieval" items 2, 3, 4 and 5, and "Ingestion, storage and runtime" item 5.
  Each item's "Recommended fix" and "Test that proves it" bind.

## Global Constraints

- **Precondition:** `docs/plans/2026-10-30-*.md` and `docs/research/2026-10-30-audit-fix-best-practices.md` are on `main` (roadmap Wave 0.5); stop if not.
- **Base:** `main` after #162 merges. Never merge `feat/mcp-sdk-v2` into this
  branch.
- **Locations** are given by symbol name. PR 1 and PR 3 land first and move
  lines in several of these files.
- **TDD:** follow superpowers:test-driven-development. Write each failing test
  and watch it fail before the fix. One finding is one commit, with its test
  and its fix. The refactor (Task 5) is its own commit.
- **D4:**
  - `get_docs` tries `dict.fromkeys((slug, slug.removesuffix(".html")))`, in
    order, against the database, before the persistent-cache lookup.
    `PersistentDocsCache.put` keys entries by `result.slug`.
  - It returns the canonical slug. An unknown slug gets an actionable error
    that names the slug form and `search_docs`.
  - A `#fragment` is never stripped silently.
  - The only description edit is `SlugParam` in `server.py`; do not touch
    `models.py`.
- **D5:**
  - `changed_in` is the newest "Changed in version" note that is present in v2
    and absent from v1.
  - `new_in` is the smallest "Added/New in version" in the v2 section.
  - `deprecated_in` keeps its meaning. No schema change. All 15 existing
    compare tests pass unchanged.
- **D6:**
  - Replace the `text[end - 1]` condition with `text[end]`. Do not add a
    second loop.
  - Add the comment
    `# shortcut: combining marks only, switch to unicodedata.iter_graphemes when the floor is 3.15`.
  - No new dependency: `regex` is not in `uv.lock`.
- `detect_python_version` stays `async def`, as `test_docs_tools_are_async`
  asserts.
- **Tool surface:** tool names, parameters and result shapes stay the same.
  The only text change is the `get_docs` `slug` description.
- **Existing tests:** never delete one or weaken an assertion. Task 3
  strengthens `test_budget_combining_character` and
  `test_budget_combining_at_boundary` by adding assertions; list both in the PR
  body.
- **Unicode in tests:** write every decomposed character as a `\u` escape, as
  `tests/test_retrieval.py` already does. Raw combining marks can be
  normalized away by editors and tools.
- **Forbidden territory:** tool description text (`AGENT-EXECUTION-PIPELINE.md`
  §2, tool surface). The PR gets the `supervisor-review` label and a
  `## Why this triggered supervisor review` section that cites: "The owner
  (Aymen Hammouda) asked on 2026-10-30 to implement all audit findings;
  decisions in `docs/plans/2026-10-30-audit-fixes-roadmap.md`."
- No new runtime dependency and no new runtime network access.
- **Retrieval check:** run
  `uv run --locked python -m benchmarks.regression --output <file>` on a full
  `build-index --versions 3.11,3.12,3.13` index. Record changed results with
  `--record`. The baseline change needs independent review, so this PR never
  edits `docs/benchmarks/regression-baseline.json`.
- **CHANGELOG:** `### Fixed` entries under `## [Unreleased]`. One of them
  covers the `slug` description change.
- **Gate (`AGENTS.md`),** run fresh before the PR, plus pipeline §5's
  tool-registration gate:
  - `uv run --locked ruff check src/ tests/ benchmarks/ ops/ .github/scripts/`
  - `uv run --locked pyright src/ benchmarks/`
  - `uv run --locked pytest --tb=short -q`
  - `uv run --locked pytest tests/test_stdio_smoke.py -q`
- **Commits and PR:** conventional prefixes; every commit ends with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. The PR body
  follows `.github/PULL_REQUEST_TEMPLATE/agent.md` and ends with
  `🤖 Generated with [Claude Code](https://claude.com/claude-code)`. Never
  self-merge.

## Checked on 2026-10-30 against `c03e497` (Python 3.13.10)

- **Whole plan on a scratch copy.** With this plan's code changes and its exact
  tests, the suite passed: 672 passed (650 existing, 22 new). Ruff (gate
  scope) and Pyright (`src/ benchmarks/`) were clean.
- **Failure messages.** Each new test failed on the unchanged code, with the
  message given in its task. There are two exceptions: the guards
  `test_compare_ignores_soft_deprecation` and
  `test_budget_progresses_from_a_stale_start_on_a_mark` pass before and after.
- **No snapshot breaks yet.** `tests/fixtures/tools-list.json` does not exist
  at `c03e497`. The current snapshot test checks the unused `models.py`
  `GetDocsInput`, so the `server.py` description edit breaks no snapshot until
  PR 3 lands.
- **Callers of the returned slug.**
  - `benchmarks/regression.py` (line 63) compares `result.slug` with the slug
    it asked for. It stays safe only because it asks with `documents.slug`,
    which is already canonical.
  - No other caller in `src/` or `benchmarks/` reads the returned slug.
  - `compare_db` in `tests/test_compare_versions.py` stores production-shaped
    slugs (`library/json` with uri `library/json.html`).
  - `test_section_text_resolves_extensionless_slug_cr01` keeps covering
    `_section_text` after its loop goes.
- **CI reuses main's index.** "Product regression" in
  `.github/workflows/product-quality.yml` keys its cached index on
  `ingestion/`, `storage/`, `__main__.py` and `uv.lock`. This PR changes none
  of them, so CI reuses main's index.
- **Real data.** On the live 3.12 `library/itertools` page (fetched
  2026-10-30), the `itertools.batched` entry carries "Added in version 3.12."
  CPython 3.12 builds its docs with Sphinx ~= 8.2, which writes "Added", so
  today `new_in` is None for it.
- **Cache isolation.** platformdirs 4.11.0 honours `XDG_CACHE_HOME` on macOS
  as well as Linux. It treats an empty value as unset
  (`platformdirs/_xdg.py:54`), so a lost variable silently points at the live
  index. uv follows `XDG_CACHE_HOME` too, so pin `UV_CACHE_DIR` as well.

## Review Focus

1. **A slug that carries a `#fragment`** (`library/json.html#json.dumps`).
   Expected: an error that tells the caller to pass the fragment as `anchor`,
   never a silent strip. Pinned by Task 1,
   `test_get_docs_does_not_strip_a_fragment`.
2. **A section call with a `.html` slug** (`anchor="json.dumps"`). Expected:
   the section, with the canonical slug. Pinned by Task 1,
   `test_get_docs_html_slug_reaches_a_section`.
3. **v2 only repeats v1's "Changed in" notes.** Expected: `changed_in is None`,
   but `change == "changed"` with a `section_diff`. Pinned by Task 2,
   `test_compare_changed_in_is_none_when_v2_adds_no_note`.
4. **"Soft deprecated since version X"**, which CPython 3.15 renders.
   Expected: `deprecated_in is None`. Pinned by Task 2,
   `test_compare_ignores_soft_deprecation`.
5. **A `next_start_index` from the old pager that points at a mark.** Pages
   in the persistent cache keep their old boundaries. Expected: the next call
   still makes progress and returns the rest. Pinned by Task 3,
   `test_budget_progresses_from_a_stale_start_on_a_mark`.

---

### Task 1: `get_docs` accepts `.html` and returns the canonical slug

**Files:**
- Modify: `src/mcp_server_python_docs/services/content.py`
  (`ContentService.get_docs`)
- Modify: `src/mcp_server_python_docs/services/compare.py`
  (`CompareService._section_text`)
- Modify: `src/mcp_server_python_docs/server.py` (`SlugParam`; forbidden
  territory: tool description text)
- Modify, only if it exists: `tests/fixtures/tools-list.json`
- Modify: `docs/architecture/DESIGN.md` (the `ContentService.get_docs`
  sequence paragraph in §5), `CHANGELOG.md`
- Test: `tests/test_services.py`:
  - a new fixture after `populated_with_content`;
  - `TestContentService`;
  - `TestToolRegistration`.

**Interfaces:**
- Produces:
  - `ContentService.get_docs(slug, version=None, anchor=None, max_chars=8000, start_index=0) -> GetDocsResult`
    keeps its signature. It accepts `.html`. `result.slug` is the canonical
    `documents.slug`. An unknown page raises `PageNotFoundError` with the
    message from Step 3.
  - `CompareService._section_text(uri, anchor, version) -> str` keeps its
    signature and makes one `get_docs` call.

- [ ] **Step 1: Write the failing tests.** Add
  `from mcp_server_python_docs.services.persistent_cache import PersistentDocsCache`
  to the imports, and this fixture after `populated_with_content`:

```python
@pytest.fixture
def production_slug_db(populated_db):
    """A page stored the way Sphinx ingestion stores it: slug without '.html'."""
    db = populated_db
    doc_set_id = db.execute("SELECT id FROM doc_sets LIMIT 1").fetchone()[0]
    db.execute(
        "INSERT INTO documents (doc_set_id, uri, slug, title, content_text, char_count) "
        "VALUES (?, 'library/json.html', 'library/json', 'json', 'json page content', 17)",
        (doc_set_id,),
    )
    doc_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute(
        "INSERT INTO sections (document_id, uri, anchor, heading, level, ordinal, "
        "content_text, char_count) VALUES (?, 'library/json.html#json.dumps', "
        "'json.dumps', 'json.dumps', 2, 0, 'Serialize obj to a JSON formatted str.', 38)",
        (doc_id,),
    )
    db.commit()
    return db
```

  Add these to `TestContentService`:

```python
    def test_get_docs_accepts_html_suffix_and_returns_canonical_slug(self, production_slug_db):
        result = ContentService(production_slug_db).get_docs("library/json.html", "3.13")
        assert result.slug == "library/json"
        assert result.content == "json page content"

    def test_get_docs_html_slug_reaches_a_section(self, production_slug_db):
        result = ContentService(production_slug_db).get_docs(
            "library/json.html", "3.13", anchor="json.dumps"
        )
        assert (result.slug, result.anchor) == ("library/json", "json.dumps")
        assert "Serialize obj" in result.content

    def test_get_docs_unknown_slug_error_is_actionable(self, production_slug_db):
        with pytest.raises(PageNotFoundError) as exc_info:
            ContentService(production_slug_db).get_docs("library/nope", "3.13")
        message = str(exc_info.value)
        assert "'library/asyncio-task'" in message
        assert "search_docs" in message

    def test_get_docs_does_not_strip_a_fragment(self, production_slug_db):
        with pytest.raises(PageNotFoundError, match="anchor"):
            ContentService(production_slug_db).get_docs("library/json.html#json.dumps", "3.13")

    def test_get_docs_html_caller_hits_persistent_cache(self, production_slug_db, tmp_path):
        """N7: an '.html' caller must hit the entry stored under the canonical slug."""
        index_path = tmp_path / "index.db"
        index_path.write_bytes(b"index")
        cache = PersistentDocsCache(tmp_path / "retrieved.sqlite3", index_path)
        svc = ContentService(production_slug_db, cache)
        first = svc.get_docs("library/json.html", "3.13", max_chars=500)
        second = svc.get_docs("library/json.html", "3.13", max_chars=500)
        assert second == first
        assert first.slug == "library/json"
        stats = cache.stats()
        assert (stats.hits, stats.misses, stats.writes) == (1, 1, 1)
```

  Add this to `TestToolRegistration`:

```python
    def test_get_docs_slug_description_shows_canonical_form(self):
        import anyio

        from mcp_server_python_docs.server import create_server

        tools = anyio.run(create_server().list_tools)
        schemas = {tool.name: tool.input_schema for tool in tools}
        assert schemas["get_docs"]["properties"]["slug"]["description"] == (
            "Page slug from a search_docs hit, e.g. 'library/asyncio-task' "
            "('.html' suffix accepted)"
        )
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --locked pytest tests/test_services.py -q -k "html or fragment or actionable or slug_description"`

Expected: 6 failed.
- The three `.html` tests raise
  `PageNotFoundError: Page 'library/json.html' not found for version 3.13`.
- The unknown-slug test fails:
  `assert "'library/asyncio-task'" in "Page 'library/nope' not found for version 3.13"`.
- The fragment test fails with `Regex pattern did not match`.
- The description test shows the old text,
  `Page slug (e.g. 'library/asyncio-task.html')`.

- [ ] **Step 3: Resolve the page before the cache in `ContentService.get_docs`.**
  1. Right after the version is resolved, loop over
     `dict.fromkeys((slug, slug.removesuffix(".html")))`. For each candidate,
     run the existing `documents` query, but select only
     `d.id, d.title, d.slug`. Stop at the first row.
  2. If no candidate matches, raise exactly:
     `PageNotFoundError(f"Page {slug!r} not found for version {resolved_version}. Use a page slug such as 'library/asyncio-task' ('.html' accepted) and pass any '#fragment' as anchor; search_docs returns both.")`
  3. Rebind `slug = doc_row["slug"]`. The existing persistent-cache lookup,
     the section-not-found errors and `GetDocsResult(slug=...)` then all use
     the canonical slug.
  4. In the page branch (the `else` that reads `doc_row["content_text"]`),
     read the text with `SELECT content_text FROM documents WHERE id = ?`.
     That way a cache hit never reads the page text: it costs one indexed
     lookup and nothing more.

- [ ] **Step 4: Drop the candidate loop in `CompareService._section_text`.**
  - The body becomes
    `return self._content.get_docs(slug=uri.split("#", 1)[0], version=version, anchor=anchor).content`.
  - Rewrite the CR-01 paragraph of its docstring: `get_docs` now resolves the
    `.html` form itself.
  - PR 3 edits the `symbols.section_id` sentence of the same docstring. If it
    has landed, keep its wording.
  - Keep `ranker._document_candidates`, which runs its own SQL.

- [ ] **Step 5: Change the `slug` description (forbidden territory: tool
  description text).** In `server.py`, `SlugParam` becomes
  `Field(max_length=500, description="Page slug from a search_docs hit, e.g. 'library/asyncio-task' ('.html' suffix accepted)")`.
  Split the string over two lines to stay within 100 columns. Do not touch
  `models.py`.

- [ ] **Step 6: Regenerate the served-tools snapshot, if it exists.** Run
  `test -f tests/fixtures/tools-list.json`. If that exits 1 (PR 3 has not
  merged), skip this step. Otherwise:
  - Run: `UPDATE_SCHEMAS=1 uv run --locked pytest tests/test_schema_snapshot.py -q`
  - Then run: `git diff tests/fixtures/tools-list.json`
  - Expected: the only changed line is the `get_docs` `slug` description.

- [ ] **Step 7: Run the affected tests**

Run: `uv run --locked pytest tests/test_services.py tests/test_compare_versions.py tests/test_persistent_docs_cache.py tests/test_mcp_get_docs_cache_smoke.py tests/test_schema_snapshot.py -q`

Expected: all passed.

- [ ] **Step 8: Update the docs.**
  - In `docs/architecture/DESIGN.md`, the paragraph in §5 that starts
    "`ContentService.get_docs` (line 45)" describes the call sequence. Change
    it to the new order. First, look up the page by slug and version (the slug
    as given, then without `.html`), and fail with the actionable error if
    none matches. Then check the persistent cache under the canonical slug.
  - Add this under `## [Unreleased]` → `### Fixed` in `CHANGELOG.md`:

```markdown
- `get_docs` now accepts the `.html` slug form that its description showed and
  that hit URIs carry. It returns the canonical slug, and repeat `.html` calls
  are served from the persistent cache. An unknown slug now gets an error that
  shows the slug form and points to `search_docs`. The `slug` description now
  reads "Page slug from a search_docs hit, e.g. 'library/asyncio-task'
  ('.html' suffix accepted)".
```

- [ ] **Step 9: Commit**

```bash
git add src/mcp_server_python_docs/services/content.py src/mcp_server_python_docs/services/compare.py src/mcp_server_python_docs/server.py tests/test_services.py docs/architecture/DESIGN.md CHANGELOG.md
git add tests/fixtures/tools-list.json  # only if Step 6 regenerated it
git commit -m "fix: accept .html slugs in get_docs and return the canonical slug" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Expected: `git status --short` prints nothing, and `git log -1 --format=%s`
prints `fix: accept .html slugs in get_docs and return the canonical slug`.

### Task 2: `compare_versions` reads current Sphinx version notes

**Files:**
- Modify: `src/mcp_server_python_docs/services/compare.py`:
  - the module docstring;
  - `_NEW_IN_RE`, `_CHANGED_IN_RE`, `_DEPRECATED_IN_RE` and the comment above
    them;
  - the `_extract_version` docstring;
  - the `new_in` line in the "added" branch of `CompareService.compare`;
  - the `changed_in` line in the both-present branch of
    `CompareService.compare`.
- Modify: `CHANGELOG.md`
- Test: `tests/test_compare_versions.py` (append at the end)

**Interfaces:**
- Produces: `_vt(version: str) -> tuple[int, ...]`, a module-level function
  that turns a version into an integer tuple, so that `3.10` sorts after `3.9`.

- [ ] **Step 1: Write the failing tests.** `_service` and `compare_db` already
  exist in this module.

```python
def _service_with_texts(db, monkeypatch, texts: dict[str, str]) -> CompareService:
    """A CompareService that reads fixed section text per version."""
    svc = _service(db)
    monkeypatch.setattr(svc, "_section_text", lambda uri, anchor, version: texts[version])
    return svc


def test_compare_new_in_reads_added_in_version(compare_db, monkeypatch):
    """Sphinx 7.3+ (the docs of Python 3.12 onwards) writes "Added in version"."""
    texts = {"3.11": "Holds tasks.\n\nAdded in version 3.11."}
    svc = _service_with_texts(compare_db, monkeypatch, texts)
    assert svc.compare("asyncio.TaskGroup", "3.10", "3.11").new_in == "3.11"


def test_compare_new_in_keeps_patch_version(compare_db, monkeypatch):
    texts = {"3.11": "Holds tasks.\n\nAdded in version 3.5.1."}
    svc = _service_with_texts(compare_db, monkeypatch, texts)
    assert svc.compare("asyncio.TaskGroup", "3.10", "3.11").new_in == "3.5.1"


def test_compare_new_in_takes_smallest_added_version(compare_db, monkeypatch):
    """A class section also carries its members' notes; the class note is the oldest."""
    texts = {"3.11": "create_task(coro)\n\nAdded in version 3.13.\n\nAdded in version 3.11."}
    svc = _service_with_texts(compare_db, monkeypatch, texts)
    assert svc.compare("asyncio.TaskGroup", "3.10", "3.11").new_in == "3.11"


def test_compare_changed_in_is_newest_note_new_in_v2(compare_db, monkeypatch):
    texts = {
        "3.10": "Serialize obj.\n\nChanged in version 3.3: x.",
        "3.11": "Serialize obj.\n\nChanged in version 3.3: x.\n\nChanged in version 3.11: y.",
    }
    svc = _service_with_texts(compare_db, monkeypatch, texts)
    assert svc.compare("json.dumps", "3.10", "3.11").changed_in == "3.11"


def test_compare_changed_in_is_none_when_v2_adds_no_note(compare_db, monkeypatch):
    texts = {
        "3.10": "Old prose.\n\nChanged in version 3.3: x.",
        "3.11": "New prose.\n\nChanged in version 3.3: x.",
    }
    svc = _service_with_texts(compare_db, monkeypatch, texts)
    result = svc.compare("json.dumps", "3.10", "3.11")
    assert (result.change, result.changed_in) == ("changed", None)
    assert result.section_diff is not None


def test_compare_ignores_soft_deprecation(compare_db, monkeypatch):
    texts = {"3.10": "Old API.", "3.11": "Old API.\n\nSoft deprecated since version 3.11."}
    svc = _service_with_texts(compare_db, monkeypatch, texts)
    assert svc.compare("json.dumps", "3.10", "3.11").deprecated_in is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --locked pytest tests/test_compare_versions.py -q -k "new_in or changed_in or soft_deprecation"`

Expected: 5 failed, 1 passed.
- The three `new_in` tests: `assert None == '3.11'`, `assert None == '3.5.1'`,
  `assert None == '3.11'`.
- `test_compare_changed_in_is_newest_note_new_in_v2`:
  `assert '3.3' == '3.11'`.
- `test_compare_changed_in_is_none_when_v2_adds_no_note`:
  `assert ('changed', '3.3') == ('changed', None)`.
- `test_compare_ignores_soft_deprecation` passes. It is a guard against
  making the pattern case-insensitive.

- [ ] **Step 3: Replace the three patterns** with these exact values:
  - `_NEW_IN_RE = re.compile(r"\b(?:New|Added) in version\s+(\d+(?:\.\d+)+)")`
  - `_CHANGED_IN_RE = re.compile(r"Changed in version\s+(\d+(?:\.\d+)+)")`
  - `_DEPRECATED_IN_RE = re.compile(r"Deprecated since version\s+(\d+(?:\.\d+)+)")`.
    It stays case-sensitive, so "Soft deprecated since" stays out.

  Then correct the text that calls them spike literals:
  - Rewrite the comment above the patterns. It says that Sphinx before 7.3
    writes "New in version" and 7.3 and later write "Added in version", which
    covers CPython 3.12 onwards; and that a version can carry a patch part, as
    in 3.5.1.
  - In the module docstring, replace "the regex literals locked by the Plan
    09-01 data-shape spike". The new text says the version-note patterns
    follow the 2026-10-30 audit research (Search 3).
  - Leave `tests/test_compare_versions_spike.py` alone. Its `NEW_IN_RE`,
    `CHANGED_IN_RE` and `DEPRECATED_IN_RE` literals (lines 41-43) keep the old
    forms on purpose: it is a standalone evidence script that records the Plan
    09-01 spike.

- [ ] **Step 4: Compute `new_in` and `changed_in` from all notes.**
  - Add `_vt(version: str) -> tuple[int, ...]`.
  - In the "added" branch:
    `new_in = min(_NEW_IN_RE.findall(text_v2), key=_vt, default=None)`.
  - In the both-present branch:
    `changed_in = max(set(_CHANGED_IN_RE.findall(text_v2)) - set(_CHANGED_IN_RE.findall(text_v1)), key=_vt, default=None)`.
  - `deprecated_in` keeps `_extract_version(_DEPRECATED_IN_RE, text_v2)`.
    Correct `_extract_version`'s docstring: it now serves `deprecated_in`
    only.

- [ ] **Step 5: Run the compare tests**

Run: `uv run --locked pytest tests/test_compare_versions.py -q`

Expected: 21 passed: the 15 existing tests, unchanged, and the 6 new ones.

- [ ] **Step 6: Add this under `## [Unreleased]` → `### Fixed` in
  `CHANGELOG.md`:**

```markdown
- `compare_versions` now reads "Added in version" notes, which docs built with
  Sphinx 7.3 or later use (Python 3.12 onwards), and keeps patch versions such
  as 3.5.1. `changed_in` is now the newest "Changed in version" note that v2
  adds over v1, and `new_in` the earliest "Added in version" note in the v2
  section.
```

- [ ] **Step 7: Commit**

```bash
git add src/mcp_server_python_docs/services/compare.py tests/test_compare_versions.py CHANGELOG.md
git commit -m "fix: read Added-in notes and patch versions in compare_versions" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Expected: `git status --short` prints nothing, and `git log -1 --format=%s`
prints `fix: read Added-in notes and patch versions in compare_versions`.

### Task 3: Never start a page on a combining mark

**Files:**
- Modify: `src/mcp_server_python_docs/retrieval/budget.py` (the backing-up
  loop in `apply_budget` and its comment)
- Modify (strengthen only): `tests/test_retrieval.py`
  (`test_budget_combining_character`, `test_budget_combining_at_boundary`)
- Modify: `docs/architecture/DESIGN.md` (the `apply_budget` paragraph in §5,
  "Budget is a single, narrow function"), `CHANGELOG.md`
- Test: `tests/test_retrieval.py` (new tests after
  `test_budget_flag_emoji_sequence`)

**Interfaces:**
- Produces: `apply_budget(text, max_chars, start_index=0)`, with the same
  signature and return shape. The invariant: a next page never starts with a
  Unicode category `M*` character, unless the text itself has a mark at
  `start_index`.

- [ ] **Step 1: Strengthen the two existing combining tests.** Add these lines
  at the end of `test_budget_combining_character` and at the end of
  `test_budget_combining_at_boundary`. Change nothing else in them.

```python
    # The next page must not start with an orphaned mark either.
    assert next_idx is not None
    assert not unicodedata.category(text[next_idx]).startswith("M")
```

- [ ] **Step 2: Add the new tests** after `test_budget_flag_emoji_sequence`.
  Write every decomposed character as a `\u` escape, exactly as shown.

```python
def test_budget_keeps_combining_mark_with_its_base():
    """Budget 1 cannot cut between 'e' and its accent; the cluster comes whole."""
    assert apply_budget("e\u0301x", 1) == ("e\u0301", True, 2)


@pytest.mark.parametrize(
    "text",
    ["e\u0301x", "caf\u0301e", "na\u0308ive", "ab\u0301c", "e\u0301\u0323\u0302x\u0301"],
)
def test_budget_pages_join_and_never_start_with_a_mark(text):
    """Paging at every budget rebuilds the text, and no page starts with a mark."""
    for budget in range(1, len(text) + 1):
        pages, start = [], 0
        while True:
            page, truncated, next_idx = apply_budget(text, budget, start)
            pages.append(page)
            # A page may exceed the budget only when it is one base char plus its marks.
            one_cluster = all(unicodedata.category(ch).startswith("M") for ch in page[1:])
            assert len(page) <= budget or one_cluster
            if not truncated:
                assert next_idx is None
                break
            assert page and next_idx == start + len(page)
            start = next_idx
        assert "".join(pages) == text
        assert not any(unicodedata.category(page[0]).startswith("M") for page in pages)


def test_budget_progresses_from_a_stale_start_on_a_mark():
    """Pages cached before this fix can hand out a start index that points at a mark."""
    assert apply_budget("caf\u0301e", 1, 3) == ("\u0301", True, 4)
```

- [ ] **Step 3: Run the budget tests to verify the new checks fail**

Run: `uv run --locked pytest tests/test_retrieval.py -q -k budget`

Expected: 8 failed, 12 passed.
- The two strengthened tests fail on their new `unicodedata.category` line,
  because `next_idx` points at the mark.
- `test_budget_keeps_combining_mark_with_its_base` fails with
  `('e', True, 1)` on the left and `('e\u0301', True, 2)` on the right.
- All 5 cases of the paging test fail.
- The stale-start guard passes.

- [ ] **Step 4: Fix the pager.** In `apply_budget`, the backing-up loop's
  condition becomes
  `while end > start_index and unicodedata.category(text[end]).startswith("M"):`.
  `text[end]` is in range there: the earlier `len(remaining) <= max_chars`
  return guarantees `end < len(text)`.
  - Replace the comment above the loop. The first line says the next page must
    not start with a combining mark.
  - The second line is
    `# shortcut: combining marks only, switch to unicodedata.iter_graphemes when the floor is 3.15`.
  - Keep the forward-advance branch; it guarantees progress.

- [ ] **Step 5: Run the budget and What's New tests**

Run: `uv run --locked pytest tests/test_retrieval.py tests/test_whatsnew.py -q`

Expected: all passed. `services/whatsnew.py` binary-searches on page length,
which still grows with the budget.

- [ ] **Step 6: Update the docs.**
  - In `docs/architecture/DESIGN.md`, the `apply_budget` paragraph in §5 says
    the text is cut and "walked backward past any Unicode combining marks".
    Change it to: the cut point moves back while the character at the cut is
    a combining mark, so no page starts with one. Keep the forward-walk
    sentence.
  - Add this under `## [Unreleased]` → `### Fixed` in `CHANGELOG.md`:

```markdown
- `get_docs` pagination no longer starts a page with a combining accent cut off
  from its letter.
```

- [ ] **Step 7: Commit.** The commit body says the two existing combining
  tests were strengthened, not weakened.

```bash
git add src/mcp_server_python_docs/retrieval/budget.py tests/test_retrieval.py docs/architecture/DESIGN.md CHANGELOG.md
git commit -m "fix: never start a get_docs page on a combining mark" -m "Strengthens test_budget_combining_character and test_budget_combining_at_boundary with a next-page assertion; no assertion removed." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Expected: `git status --short` prints nothing, and `git log -1 --format=%s`
prints `fix: never start a get_docs page on a combining mark`.

### Task 4: Return the startup detection from `detect_python_version`

**Files:**
- Modify: `src/mcp_server_python_docs/app_context.py` (`AppContext`)
- Modify: `src/mcp_server_python_docs/server.py`:
  - the `AppContext(...)` yield in `app_lifespan`;
  - the body of the `detect_python_version` tool in `create_server`.
- Modify: `README.md` (the `detect_python_version` row of the tools table),
  `CHANGELOG.md`
- Test: `tests/test_detection.py` (append at the end)

**Interfaces:**
- Produces: `AppContext.detected_python_raw: str = ""`, a new last field.
  `app_lifespan` fills it from its single `detect_python_version()` call.
- The tool keeps its signature, `async def`, and `DetectPythonVersionResult`.

- [ ] **Step 1: Write the failing tests.** Add
  `from types import SimpleNamespace` to the imports.

```python
async def test_detect_tool_returns_startup_values_without_reprobing(monkeypatch) -> None:
    """The tool reports what startup detected; it never spawns python3 per call."""
    from mcp_server_python_docs.server import create_server

    monkeypatch.setattr(
        detection, "detect_python_version", lambda: pytest.fail("tool re-ran detection")
    )
    tool = create_server()._tool_manager._tools["detect_python_version"]
    app_ctx = SimpleNamespace(
        detected_python_raw="3.9",
        detected_python_source="python3 in PATH",
        detected_python_version=None,
    )
    ctx = SimpleNamespace(request_context=SimpleNamespace(lifespan_context=app_ctx))
    result = await tool.fn(ctx=ctx)
    assert (
        result.detected_version, result.source, result.matched_index_version, result.is_default
    ) == ("3.9", "python3 in PATH", None, False)


def test_lifespan_stores_startup_detection(tmp_path, monkeypatch) -> None:
    """app_lifespan keeps the raw detection, so the tool can return it."""
    import anyio
    from mcp.server.mcpserver import MCPServer

    from mcp_server_python_docs import server
    from mcp_server_python_docs.storage.db import bootstrap_schema, get_readwrite_connection

    index_path = tmp_path / "index.db"
    conn = get_readwrite_connection(index_path)
    bootstrap_schema(conn)
    conn.execute(
        "INSERT INTO doc_sets (source, version, language, label, is_default) "
        "VALUES ('python-docs', '3.13', 'en', 'Python 3.13', 1)"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(server, "get_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(server, "get_index_path", lambda: index_path)
    monkeypatch.setattr(server, "detect_python_version", lambda: ("3.9", "python3 in PATH"))

    async def enter():
        async with server.app_lifespan(MCPServer("test")) as app_ctx:
            return (
                app_ctx.detected_python_raw,
                app_ctx.detected_python_source,
                app_ctx.detected_python_version,
            )

    assert anyio.run(enter) == ("3.9", "python3 in PATH", None)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --locked pytest tests/test_detection.py -q -k "detect_tool or lifespan"`

Expected: 2 failed.
- The tool test fails with `Failed: tool re-ran detection`.
- The lifespan test fails with
  `AttributeError: 'AppContext' object has no attribute 'detected_python_raw'`.

- [ ] **Step 3: Add `detected_python_raw: str = ""`** to `AppContext`, after
  `detected_python_source`.
- [ ] **Step 4: Store the raw version at startup.** In `app_lifespan`, pass
  `detected_python_raw=detected_ver` to `AppContext(...)`. `detected_ver`
  already comes from the existing `detect_python_version()` call there.
- [ ] **Step 5: Return stored values from the tool.**
  - Delete the in-function import and the `_detect()` call.
  - Return `DetectPythonVersionResult` with these values:
    - `detected_version=app_ctx.detected_python_raw`;
    - `source=app_ctx.detected_python_source or ""`;
    - `matched_index_version=app_ctx.detected_python_version`;
    - `is_default=app_ctx.detected_python_version is not None`.
  - Keep `async def`. Keep the tool docstring unchanged: it is the served
    description, and PR 3's snapshot pins it.

- [ ] **Step 6: Run the tests**

Run: `uv run --locked pytest tests/test_detection.py tests/test_services.py -q`

Expected: all passed, including `test_docs_tools_are_async`.

- [ ] **Step 7: Update the docs.**
  - Change the README `detect_python_version` row to: "Report the Python
    version detected in the user's environment when the server started, and
    whether that version is indexed."
  - Add this under `## [Unreleased]` → `### Fixed` in `CHANGELOG.md`:

```markdown
- `detect_python_version` no longer runs `python3 --version` on every call,
  which could stall other tool calls for up to 5 seconds. It returns what the
  server detected at startup, the same value `get_docs` already uses.
```

- [ ] **Step 8: Commit**

```bash
git add src/mcp_server_python_docs/app_context.py src/mcp_server_python_docs/server.py tests/test_detection.py README.md CHANGELOG.md
git commit -m "fix: return the startup Python detection from detect_python_version" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Expected: `git status --short` prints nothing, and `git log -1 --format=%s`
prints `fix: return the startup Python detection from detect_python_version`.

### Task 5: Expand search synonyms once per query

A refactor: behaviour stays the same, so there is no CHANGELOG entry.

**Files:**
- Modify: `src/mcp_server_python_docs/services/search.py`:
  - the `retrieval.query` import;
  - `SearchService.__init__`;
  - the expansion and match-expression calls in `SearchService.search`.
- Modify: `src/mcp_server_python_docs/retrieval/query.py`
  (`build_match_expression`)
- Test: `tests/test_services.py` (`TestSearchService`)

**Interfaces:**
- Produces:
  - `SearchService._concept_patterns: dict[str, re.Pattern[str]]`, built once
    in `__init__` with `_build_concept_patterns(synonyms)`;
  - `build_match_expression(query, synonyms, *, _concept_patterns=None, _expanded: set[str] | None = None) -> str`.
    It gains `_expanded`; when that is given, the function uses it instead of
    calling `expand_synonyms`.

- [ ] **Step 1: Write the failing test** in `TestSearchService`:

```python
    def test_search_expands_synonyms_once_with_prebuilt_patterns(
        self, populated_with_content, monkeypatch
    ):
        from mcp_server_python_docs.retrieval import query
        from mcp_server_python_docs.services import search

        svc = SearchService(populated_with_content, {"parse json": ["json", "json.loads"]})
        calls = []
        real_expand = query.expand_synonyms

        def counting_expand(text, synonyms, **kwargs):
            calls.append(text)
            return real_expand(text, synonyms, **kwargs)

        monkeypatch.setattr(query, "expand_synonyms", counting_expand)
        monkeypatch.setattr(search, "expand_synonyms", counting_expand)
        monkeypatch.setattr(
            query,
            "_build_concept_patterns",
            lambda synonyms: pytest.fail("concept patterns rebuilt during search"),
        )
        svc.search("parse json", kind="section")
        assert calls == ["parse json"]
        assert svc._last_synonym_expanded is True
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --locked pytest tests/test_services.py -q -k expands_synonyms_once`

Expected: 1 failed, with `Failed: concept patterns rebuilt during search`.

- [ ] **Step 3: Precompile once and expand once.**
  - In `search.py`, import `_build_concept_patterns` and set
    `self._concept_patterns` in `__init__`.
  - In `search()`, the one expansion call becomes
    `expand_synonyms(query, self._synonyms, _concept_patterns=self._concept_patterns)`.
  - The match expression becomes
    `build_match_expression(query, self._synonyms, _expanded=expanded)`.
  - In `query.py`, add the keyword-only `_expanded: set[str] | None = None` to
    `build_match_expression`. Call `expand_synonyms` only when `_expanded is
    None`.

- [ ] **Step 4: Run the search and query tests**

Run: `uv run --locked pytest tests/test_services.py tests/test_retrieval.py tests/test_retrieval_regression.py tests/test_stability.py -q`

Expected: all passed. The `build_match_expression` tests pass unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/mcp_server_python_docs/services/search.py src/mcp_server_python_docs/retrieval/query.py tests/test_services.py
git commit -m "refactor: expand search synonyms once per query" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Expected: `git status --short` prints nothing, and `git log -1 --format=%s`
prints `refactor: expand search synonyms once per query`.

### Task 6: Run the retrieval regression on a full index

This PR changes no index content, so a run of the base commit on the same index
isolates its effect. This also keeps a baseline change from PR 1 that is still
waiting for review from being blamed on this PR.

**Interfaces:**
- Consumes: Tasks 1-5, committed.
- Produces: `/tmp/pd-pr2-index/head.json`, `/tmp/pd-pr2-index/base.json` and,
  if anything changed, `/tmp/pd-pr2-index/head-recorded.json`.

- [ ] **Step 1: Pin the isolated cache and prove it.** Agent shells do not keep exported variables, so every command in Tasks 6–7 starts with `ISO=/tmp/pd-pr2-index;` and passes `UV_CACHE_DIR="$(uv cache dir)" XDG_CACHE_HOME="${ISO:?}"` as a prefix. `${ISO:?}` aborts on an empty value.

Run: `ISO=/tmp/pd-pr2-index; mkdir -p "$ISO"; UV_CACHE_DIR="$(uv cache dir)" XDG_CACHE_HOME="${ISO:?}" uv run --locked python -c "from mcp_server_python_docs.storage.db import get_index_path; print(get_index_path())"`

Expected: `/tmp/pd-pr2-index/mcp-python-docs/index.db`. Stop if the path is
under `~/Library/Caches` or `~/.cache`: that is the live index.

- [ ] **Step 2: Build the full index.** This needs network and takes 10-30
  minutes. Run it in the background, or with a timeout of at least 45 minutes,
  and wait for it to exit. Do not interrupt it. Skip this step only if
  `/tmp/pd-pr2-index/mcp-python-docs/index.db` already holds a 3.11–3.13
  index built by an earlier run of this task on the same base.

Run: `ISO=/tmp/pd-pr2-index; UV_CACHE_DIR="$(uv cache dir)" XDG_CACHE_HOME="${ISO:?}" uv run --locked python-docs-mcp-server build-index --versions 3.11,3.12,3.13`

Expected: exit code 0, and stderr ends with
`Index rebuilt. Restart your MCP client to pick up the new index.`

- [ ] **Step 3: Check the fixes on real data**

Run: `ISO=/tmp/pd-pr2-index; UV_CACHE_DIR="$(uv cache dir)" XDG_CACHE_HOME="${ISO:?}" uv run --locked python -c "from mcp_server_python_docs.storage.db import get_index_path, get_readonly_connection as ro; from mcp_server_python_docs.services.content import ContentService; from mcp_server_python_docs.services.compare import CompareService; db = ro(get_index_path()); c = ContentService(db); print(c.get_docs('library/itertools.html', '3.12').slug, CompareService(db, c).compare('itertools.batched', '3.11', '3.12').new_in)"`

Expected: `library/itertools 3.12`. Before this PR, the first call raised
`PageNotFoundError` and `new_in` was `None`.

- [ ] **Step 4: Run the gate on this branch**

Run: `ISO=/tmp/pd-pr2-index; UV_CACHE_DIR="$(uv cache dir)" XDG_CACHE_HOME="${ISO:?}" uv run --locked python -m benchmarks.regression --output "${ISO:?}/head.json"`

Expected: stdout `{"cases": 65, "regressions": []}`.

- [ ] **Step 5: Check out the base commit next to the index**

Run: `git fetch origin main`, then
`ISO=/tmp/pd-pr2-index; git worktree add --detach "${ISO:?}/base" "$(git merge-base HEAD origin/main)"`

Expected: `Preparing worktree (detached HEAD ...)`.

- [ ] **Step 6: Run the gate on the base commit, against the same index**

Run: `ISO=/tmp/pd-pr2-index; UV_CACHE_DIR="$(uv cache dir)" XDG_CACHE_HOME="${ISO:?}" uv run --locked --directory "${ISO:?}/base" python -m benchmarks.regression --output "${ISO:?}/base.json"`

Expected: the same stdout as Step 4. A regression printed in both runs predates
this PR; name it in the PR body as such.

- [ ] **Step 7: List the cases this PR changed**

Run: `ISO=/tmp/pd-pr2-index; UV_CACHE_DIR="$(uv cache dir)" XDG_CACHE_HOME="${ISO:?}" uv run --locked python -c "import json,sys; a,b=(json.load(open(p))['cases'] for p in sys.argv[1:]); print(sorted(k for k in b if (a[k]['retrieval_hit_at_5'], set(a[k]['resolved_citations'])) != (b[k]['retrieval_hit_at_5'], set(b[k]['resolved_citations']))))" "${ISO:?}/head.json" "${ISO:?}/base.json"`

Expected: `[]`. This PR is meant to leave retrieval unchanged.

- [ ] **Step 8: Record the changes if the list is not empty**

Run: `ISO=/tmp/pd-pr2-index; UV_CACHE_DIR="$(uv cache dir)" XDG_CACHE_HOME="${ISO:?}" uv run --locked python -m benchmarks.regression --record --output "${ISO:?}/head-recorded.json"`

Expected: `Recorded evaluation; baseline changes require independent review.`
- List each changed case in the PR body.
- Do not edit `docs/benchmarks/regression-baseline.json`.

- [ ] **Step 9: Remove the base worktree**

Run: `ISO=/tmp/pd-pr2-index; git worktree remove "${ISO:?}/base"`

Expected: exit code 0, and `git worktree list` no longer shows it.

### Task 7: Rebase, run the gate and open the PR

- [ ] **Step 0: Rebase onto `main` and recheck the snapshot.** Run each command
  separately if the sandbox refuses chains.
  - Run: `git fetch origin main`, then `git rebase origin/main`.
  - If `tests/fixtures/tools-list.json` now exists, rerun Task 1 Step 6. Then
    commit:
    `git add tests/fixtures/tools-list.json` and
    `git commit -m "test: regenerate tools-list.json for the get_docs slug description" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"`.
  - If PR 3 is still open, say in the PR body that whichever PR lands second
    regenerates it.
  - If the rebase brought in commits that touch `src/` or `benchmarks/`, rerun
    Task 6 from Step 4. Rerun Step 2 too if those commits touch `ingestion/`,
    `storage/` or `__main__.py`.

  Expected: `git rebase` reports success or that the branch is up to date. If
  you made the snapshot commit, `git log -1 --format=%s` prints its subject
  and `git status --short` prints nothing.

- [ ] **Step 1: Run the gate fresh**

| Run | Expected |
|---|---|
| `uv run --locked ruff check src/ tests/ benchmarks/ ops/ .github/scripts/` | `All checks passed!` |
| `uv run --locked pyright src/ benchmarks/` | `0 errors, 0 warnings, 0 informations` |
| `uv run --locked pytest --tb=short -q` | all tests pass |
| `uv run --locked pytest tests/test_stdio_smoke.py -q` | all passed |
| `ISO=/tmp/pd-pr2-index; UV_CACHE_DIR="$(uv cache dir)" XDG_CACHE_HOME="${ISO:?}" uv run --locked python-docs-mcp-server doctor` | `All checks passed.` |

- [ ] **Step 2: Get the owner's go-ahead before pushing.**
- [ ] **Step 3: Push and open the PR.**
  - Push `fix/tool-correctness`.
  - Open a PR against `main` titled
    `fix: correct get_docs slugs, compare_versions notes, paging and version detection`,
    with the `supervisor-review` label.
  - Follow `.github/PULL_REQUEST_TEMPLATE/agent.md`. In place of `Closes #`,
    write `Refs docs/plans/2026-10-30-audit-fixes-roadmap.md (PR 2)`.
  - The body includes:
    - one acceptance line per task, each with the command and its result;
    - the gate output and the real-data check from Task 6 Step 3;
    - a **Retrieval regression** section with the head and base summaries, the
      Step 7 list (expected `[]`) and, if any case changed, "Baseline change
      recorded with `--record`; it needs independent review."
    - a **Tests strengthened, none weakened** line that names
      `test_budget_combining_character` and
      `test_budget_combining_at_boundary`;
    - a line saying whether `tests/fixtures/tools-list.json` was regenerated.
      If PR 3 is still open, the line says whichever PR lands second
      regenerates it.
    - `## Why this triggered supervisor review`. It names:
      - the `get_docs` `slug` description change (tool-surface text, pipeline
        §2);
      - the `tests/fixtures/tools-list.json` regeneration, or that it is still
        pending on PR 3;
      - the owner's approval: "The owner (Aymen Hammouda) asked on 2026-10-30
        to implement all audit findings; decisions in
        `docs/plans/2026-10-30-audit-fixes-roadmap.md`."
    - the product-evidence fields:
      - kind: bugfix;
      - the problem for each finding;
      - non-goals: no structured version notes at ingestion (D5), no grapheme
        clusters beyond combining marks (D6), no `models.py` edit;
      - the head and base SHAs.
    - the closing line
      `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

  Expected: `gh pr view --json url,labels` prints the PR URL and the
  `supervisor-review` label.
