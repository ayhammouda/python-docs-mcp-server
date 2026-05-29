---
phase: 09-compare-versions
plan: 03
type: execute
wave: 1
depends_on:
  - 09-01-data-shape-spike
  - 09-02-result-models
files_modified:
  - src/mcp_server_python_docs/services/compare.py
  - tests/test_compare_versions.py
autonomous: true
requirements:
  - CMPR-01
  - CMPR-02
  - CMPR-03
must_haves:
  truths:
    - "`CompareService.compare(symbol, v1, v2)` returns a `CompareVersionsResult` whose `change` field is one of `added | removed | changed | unchanged`."
    - "Algorithm ordering is FIXED per cross-AI review H2: (1) `validate_version` BOTH `v1` and `v2` first → raises `VersionNotFoundError` for unknown versions, (2) resolve symbol in BOTH versions via `create_symbol_cache`, (3) if symbol is missing in BOTH versions raise `SymbolNotFoundError`, (4) ONLY THEN handle the `v1 == v2` identical-versions case as `change='unchanged'`. Identical versions return `unchanged` ONLY when the symbol exists in that version."
    - "Calling `compare('does.not.exist', '3.11', '3.11')` raises `SymbolNotFoundError` — NOT `change='unchanged'`. This is the explicit fix for cross-AI review H2 and prevents the false-negative where identical-versions short-circuit bypasses symbol-existence validation."
    - "Calling `compare(symbol, '3.99', v2)` raises `VersionNotFoundError` with the message containing BOTH the missing version string `'3.99'` AND at least one currently-indexed version string (e.g. `'3.10'` and `'3.11'`) — verifying success criterion #3's actionable-error-with-list requirement (H3-related M3 fix)."
    - "Calling `compare('asyncio.TaskGroup', '3.10', '3.11')` against the test fixture returns `change='added'` and `new_in='3.11'`."
    - "When both versions have the symbol but `ContentService.get_docs` raises `PageNotFoundError` for one or both, the result is `change='changed'` with `section_diff=None` and `note='docs page not available for one or both versions'` — NOT `change='unchanged'`. This is the explicit fix for cross-AI review M2 (false-negative when slug/anchor mismatch hides real regressions)."
    - "The see-also delta is real: a symbol that gains a `See also` reference between v1 and v2 produces `see_also_added != []`; the inverse produces `see_also_removed != []`. CMPR-01's see-also requirement has corresponding test coverage (per cross-AI review H3)."
    - "The deprecation marker is real: a symbol deprecated in v2 produces `deprecated_in == '<extracted version>'`. CMPR-01's deprecation requirement has corresponding test coverage (per cross-AI review H3)."
    - "The signature heuristic field is named `signature_delta` (NOT `signature_change`, per Plan 02 / M1) and its emission is exercised by at least two test cases: one where line 1 IS a function signature (asserts `signature_delta` is non-None and includes signature-like text), one where line 1 is prose (asserts the field is populated but documented as advisory)."
    - "The 1200-byte byte-count ceiling in the token-frugality test is a REGRESSION SMOKE CHECK per cross-AI review L1, not a literal token guarantee. The test docstring/comment makes this explicit."
    - "`compare.py` does NOT import `VersionNotFoundError` (per cross-AI review H4 fix option (a)) — `validate_version` raises it from its own module; tests import the exception type from `mcp_server_python_docs.errors`. This avoids ruff F401."
    - "The serialized JSON of any returned `CompareVersionsResult` is under 1200 bytes (proxy for <300 tokens) for the headline 'added' case — assertion remains a smoke check."
  artifacts:
    - path: "src/mcp_server_python_docs/services/compare.py"
      provides: "CompareService class with compare(symbol, v1, v2) method"
      contains: "class CompareService:"
    - path: "tests/test_compare_versions.py"
      provides: "Pytest tests for all four diff cases, both error paths, the see-also/deprecation/signature-delta heuristic, the PageNotFoundError → changed+note fallback, and the token-frugality smoke check"
      contains: "def test_compare_added_in_v2"
  key_links:
    - from: "src/mcp_server_python_docs/services/compare.py"
      to: "src/mcp_server_python_docs/services/cache.py (create_symbol_cache)"
      via: "import: `from mcp_server_python_docs.services.cache import create_symbol_cache`"
      pattern: "create_symbol_cache"
    - from: "src/mcp_server_python_docs/services/compare.py"
      to: "src/mcp_server_python_docs/services/content.py (ContentService.get_docs)"
      via: "constructor accepts `ContentService` instance; calls `self._content.get_docs(slug, version, anchor)`"
      pattern: "content_service.get_docs"
    - from: "src/mcp_server_python_docs/services/compare.py"
      to: "src/mcp_server_python_docs/services/version_resolution.py (validate_version)"
      via: "import: `from mcp_server_python_docs.services.version_resolution import validate_version`"
      pattern: "validate_version"
    - from: "src/mcp_server_python_docs/services/compare.py"
      to: "src/mcp_server_python_docs/services/observability.py (log_tool_call)"
      via: "decorator: `@log_tool_call('compare_versions')` on the `compare` method"
      pattern: "@log_tool_call"
---

<objective>
Implement `CompareService.compare(symbol, v1, v2)` in a new file `src/mcp_server_python_docs/services/compare.py` and exercise it from a new test file `tests/test_compare_versions.py`. This plan owns the core logic; Plan 04 wires it into the MCP tool surface.

Purpose: Deliver the behavioral substance of Phase 09 — the four diff branches (added / removed / changed / unchanged), the three error/fallback paths (VersionNotFoundError, SymbolNotFoundError, PageNotFoundError → changed+note), the see-also/deprecation/signature-delta heuristics, and the token-frugality assertion — in one focused, testable unit, without touching server-side wiring.

This plan was revised in response to cross-AI review (09-REVIEWS.md). Key changes:
- H2: Branch ordering is validate-versions → resolve-symbols → check-both-missing → then-handle-identical-versions (NOT identical-first).
- H3: See-also added/removed and deprecation tests added explicitly.
- H4: `VersionNotFoundError` is NOT imported into `compare.py` — it propagates from `validate_version`.
- M1: Field is `signature_delta`, advisory; tested with both signature-line-1 and prose-line-1 fixtures.
- M2: `PageNotFoundError` in both-present branch returns `change='changed'` + `note=...`, not `unchanged`.
- M3: Missing-version test asserts indexed-version list appears in the error message.
- L1: Token-frugality test is documented as a smoke check.

Output: One new service module, one new test file. All `uv run pytest tests/test_compare_versions.py -x` tests green; `uv run ruff check` and `uv run pyright src/` exit 0.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@AGENTS.md
@.planning/phases/09-compare-versions/09-CONTEXT.md
@.planning/phases/09-compare-versions/09-RESEARCH.md
@.planning/phases/09-compare-versions/09-REVIEWS.md
@.planning/phases/09-compare-versions/09-01-data-shape-spike-SUMMARY.md
@src/mcp_server_python_docs/models.py
@src/mcp_server_python_docs/services/cache.py
@src/mcp_server_python_docs/services/version_resolution.py
@src/mcp_server_python_docs/services/observability.py
@tests/test_multi_version.py
@tests/conftest.py

<interfaces>
<!-- Existing primitives Plan 03 composes with. All HIGH confidence — verified against codebase. -->

From src/mcp_server_python_docs/services/cache.py:
```python
class CachedSymbol(NamedTuple):
    qualified_name: str
    symbol_type: str
    uri: str        # e.g. "library/asyncio-task.html#asyncio.TaskGroup"
    anchor: str     # e.g. "asyncio.TaskGroup"
    module: str
    version: str

def create_symbol_cache(db: sqlite3.Connection) -> Callable[[str, str], CachedSymbol | None]:
    """LRU(128) closure: resolve_symbol(qualified_name, version) -> CachedSymbol | None."""
```

From src/mcp_server_python_docs/services/version_resolution.py:
```python
def validate_version(db: sqlite3.Connection, version: str) -> str:
    """Raises VersionNotFoundError(f'version {version!r} not found; available: {available}').

    The error message embeds the available-version list — directly satisfies CMPR-02 /
    success criterion #3. Tests import VersionNotFoundError from `..errors` and pattern-match
    BOTH the missing version AND at least one indexed version in str(exc.value).
    """
```

From src/mcp_server_python_docs/services/content.py:
```python
class ContentService:
    def __init__(self, db, persistent_cache=None): ...
    @log_tool_call("get_docs")
    def get_docs(self, slug, version=None, anchor=None, max_chars=8000, start_index=0) -> GetDocsResult: ...
    # Raises PageNotFoundError when slug or (slug, anchor) is not present in the index.
```

From src/mcp_server_python_docs/models.py (added by Plan 02):
```python
ChangeKind = Literal["added", "removed", "changed", "unchanged"]

class CompareVersionsResult(BaseModel):
    symbol: str; v1: str; v2: str; change: ChangeKind
    new_in: str | None = None; removed_in: str | None = None
    changed_in: str | None = None; deprecated_in: str | None = None
    signature_delta: str | None = None  # renamed from signature_change per M1
    see_also_added: list[str] = Field(default_factory=list)
    see_also_removed: list[str] = Field(default_factory=list)
    section_diff: str | None = None
    note: str | None = None  # new per M2
```

From src/mcp_server_python_docs/services/observability.py:
```python
def log_tool_call(tool_name: str) -> Callable:
    """Decorator. Logs logfmt to stderr. Extracts `version` kwarg if present."""
```

From src/mcp_server_python_docs/errors.py:
```python
class VersionNotFoundError(DocsServerError): ...
class SymbolNotFoundError(DocsServerError): ...
class PageNotFoundError(DocsServerError): ...
```

From tests/test_multi_version.py — the fixture pattern to clone (lines 17-81):
```python
@pytest.fixture
def multi_version_db(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_readwrite_connection(db_path)
    bootstrap_schema(conn)
    conn.execute("INSERT INTO doc_sets ...")  # 3.12 + 3.13
    # insert documents, sections, symbols, rebuild FTS
    yield conn
    conn.close()
```
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Implement CompareService with FIXED branch ordering (H2) and the renamed signature_delta heuristic (M1)</name>
  <read_first>
    - src/mcp_server_python_docs/services/cache.py (full file — CachedSymbol shape, create_symbol_cache)
    - src/mcp_server_python_docs/services/version_resolution.py (validate_version)
    - src/mcp_server_python_docs/services/content.py (ContentService — for the call pattern; do NOT modify)
    - src/mcp_server_python_docs/services/observability.py (log_tool_call decorator signature)
    - src/mcp_server_python_docs/errors.py (VersionNotFoundError, SymbolNotFoundError, PageNotFoundError)
    - src/mcp_server_python_docs/models.py (CompareVersionsResult from Plan 02 — including renamed `signature_delta` and new `note` fields — already in context)
    - .planning/phases/09-compare-versions/09-01-data-shape-spike-SUMMARY.md (locked regex patterns from Wave 0 spike)
    - .planning/phases/09-compare-versions/09-REVIEWS.md (H2 ordering + H4 import + M1 rename + M2 fallback semantics)
    - .planning/phases/09-compare-versions/09-RESEARCH.md (Question 2(c) — branch logic; Pitfall 2 — slug/anchor derivation; Pitfall 3 — keep sync)
  </read_first>
  <action>
    Create new file `src/mcp_server_python_docs/services/compare.py`. Module docstring should reference CMPR-01/02/03 and Phase 09.

    **Imports (H4 fix — drop `VersionNotFoundError`):**
    - stdlib: `sqlite3`, `difflib`, `re`, `typing.TYPE_CHECKING`
    - project: `CompareVersionsResult` from `..models`, `create_symbol_cache` from `.cache`, `validate_version` from `.version_resolution`, `log_tool_call` from `.observability`
    - project errors: `SymbolNotFoundError` + `PageNotFoundError` from `..errors`. **DO NOT import `VersionNotFoundError`** — it is raised by `validate_version` from its own module; `compare.py` never references the type by name (per cross-AI review H4 option (a): tests import the exception from `..errors`, not from `compare.py`). This avoids ruff F401 unused-import.
    - `from __future__ import annotations` (matches the rest of the codebase)

    **Module-level regex constants**, copied verbatim from `09-01-data-shape-spike-SUMMARY.md ## Locked regex patterns`. If the SUMMARY records A1 as FALSIFIED, the corresponding extractor returns `None` unconditionally; document that decision in the constant's adjacent comment.

    **Class `CompareService` with:**
    - `__init__(self, db: sqlite3.Connection, content_service: ContentService)` — store `db`, store `content_service`, build `self._resolve = create_symbol_cache(db)` (own private LRU per RESEARCH §Q7 Open Question 3 resolution).
    - `@log_tool_call("compare_versions")` decorated public method `compare(self, symbol: str, v1: str, v2: str) -> CompareVersionsResult`. Keep the method SYNC per RESEARCH Pitfall 3.

    **Inside `compare` — CRITICAL: the ordering below is the H2 fix and MUST be followed exactly:**

    1. **Validate BOTH versions first** (so `compare(symbol, '3.99', '3.11')` raises `VersionNotFoundError` regardless of symbol existence):
       ```
       validate_version(self._db, v1)
       validate_version(self._db, v2)
       ```
       Each call raises `VersionNotFoundError` from its own module — `compare.py` does not reference the type.

    2. **Resolve symbol in BOTH versions** (so `compare('does.not.exist', '3.11', '3.11')` can detect "missing in both" even when v1 == v2):
       ```
       sym_v1 = self._resolve(symbol, v1)
       sym_v2 = self._resolve(symbol, v2)
       ```

    3. **If symbol is missing in BOTH versions** → raise `SymbolNotFoundError(f"symbol {symbol!r} not found in v{v1} or v{v2}")`. This branch fires BEFORE the v1==v2 check, so identical-versions with a non-existent symbol correctly raises rather than returning unchanged.

    4. **If `v1 == v2`** (and we now know the symbol exists in at least one version, which because v1==v2 means in that one version): return `CompareVersionsResult(symbol=symbol, v1=v1, v2=v2, change="unchanged")` with all optional fields at default.

    5. **Otherwise, branch on (sym_v1, sym_v2) presence:**
       - `sym_v1 is None and sym_v2 is not None` → fetch v2 section text via `self._content.get_docs(slug=sym_v2.uri.split("#", 1)[0], version=v2, anchor=sym_v2.anchor)`. Extract `new_in` via `_NEW_IN_RE`. Return `CompareVersionsResult(symbol=symbol, v1=v1, v2=v2, change="added", new_in=<extracted or None>)`. **For the "added" branch only**, swallow `PageNotFoundError` and continue with `new_in=None` (the symbol exists in the symbols table, the section just isn't fetchable — degraded but honest; no `note` needed because the structural "added" is sound).

       - `sym_v1 is not None and sym_v2 is None` → return `CompareVersionsResult(symbol=symbol, v1=v1, v2=v2, change="removed", removed_in=v2)`. No section fetch needed; the symbol's gone.

       - `sym_v1 is not None and sym_v2 is not None` → fetch both sections via `ContentService.get_docs` (slug via `uri.split("#", 1)[0]`, version, anchor). **M2 fix:** if either fetch raises `PageNotFoundError`, return:
         ```
         CompareVersionsResult(
             symbol=symbol, v1=v1, v2=v2,
             change="changed",
             section_diff=None,
             note="docs page not available for one or both versions",
         )
         ```
         Do NOT return `unchanged` (that was the previous false-negative behavior identified by review M2).

         If both fetches succeed:
         - Compute `changed_in` via `_CHANGED_IN_RE` on the v2 section text.
         - Compute `deprecated_in` via `_DEPRECATED_IN_RE` on the v2 section text.
         - Compute `signature_delta` (renamed per M1): look at the first non-empty line of each section text; if they differ, set `signature_delta` to a short prose label like `f"line 1 differs (v{v1}: {first_v1[:80]} → v{v2}: {first_v2[:80]})"`. Otherwise `None`. The model field's description in Plan 02 already marks this as advisory; the implementation just emits it.
         - Compute `see_also_added` / `see_also_removed` by extracting markdown link labels (`_SEE_ALSO_LINK_RE`) from a window starting at the first case-insensitive "See also" line in each section (window size: up to next ATX heading or 20 lines). Set-difference: `see_v2 - see_v1` and `see_v1 - see_v2`. If either side has no "See also", the corresponding lists stay empty (this is the test case for H3 (b) — see-also removed). If A2 was FALSIFIED in 09-01, the extractor returns `[]` unconditionally and the see-also delta is silently empty.
         - Compute `section_diff` via `difflib.unified_diff(v1_lines, v2_lines, lineterm="", n=2)` joined into a single string. Truncate to 600 chars. If the diff is empty (sections identical) AND no extractor produced a value AND no see-also delta exists, return `change="unchanged"`. Otherwise return `change="changed"` with whichever subset of fields are populated.

    **Module-level private helpers**: `_extract_new_in(text: str) -> str | None`, `_extract_changed_in`, `_extract_deprecated_in`, `_extract_see_also(text: str) -> list[str]`. Each takes a section text string and returns the extracted value or fallback (None / []).

    Do NOT add any `asyncio.to_thread` — SQLite reads on the open connection are non-blocking per RESEARCH Pitfall 3. Do NOT join `symbols.section_id` directly; always derive slug and anchor from `CachedSymbol.uri` and `CachedSymbol.anchor` per RESEARCH Pitfall 2.

    Do NOT use the field name `signature_change` anywhere — Plan 02 renamed it to `signature_delta` per M1. Verify by grep: `grep -c "signature_change" src/mcp_server_python_docs/services/compare.py` MUST return 0.

    Do NOT import `VersionNotFoundError` — verify by grep: `grep -c "VersionNotFoundError" src/mcp_server_python_docs/services/compare.py` MUST return 0 (per H4 option (a)).
  </action>
  <verify>
    <automated>uv run ruff check src/mcp_server_python_docs/services/compare.py && uv run pyright src/mcp_server_python_docs/services/compare.py && uv run python -c "from mcp_server_python_docs.services.compare import CompareService; assert hasattr(CompareService, 'compare'); import inspect; sig=inspect.signature(CompareService.compare); assert list(sig.parameters.keys()) == ['self', 'symbol', 'v1', 'v2'], sig" && grep -c "signature_change" src/mcp_server_python_docs/services/compare.py | grep -q "^0$" && grep -c "VersionNotFoundError" src/mcp_server_python_docs/services/compare.py | grep -q "^0$"</automated>
  </verify>
  <acceptance_criteria>
    - Source: `src/mcp_server_python_docs/services/compare.py` contains `class CompareService:` and `def compare(self, symbol: str, v1: str, v2: str) -> CompareVersionsResult:`.
    - Source: file imports `create_symbol_cache`, `validate_version`, `log_tool_call`, `CompareVersionsResult`, `SymbolNotFoundError`, `PageNotFoundError`, `difflib`, `re`.
    - Source: file does NOT import `VersionNotFoundError` (H4 fix). `grep -c "VersionNotFoundError" src/mcp_server_python_docs/services/compare.py` returns 0.
    - Source: file does NOT contain the string `signature_change` (M1 rename). `grep -c "signature_change" src/mcp_server_python_docs/services/compare.py` returns 0.
    - Source: file contains the string `signature_delta` at least once.
    - Source: file does NOT import `asyncio`, `tiktoken`, or `anthropic`.
    - Source: file contains the string `"docs page not available for one or both versions"` (M2 note text).
    - Source: `grep -n "@log_tool_call" src/mcp_server_python_docs/services/compare.py` returns at least one match.
    - Source: in the `compare` method body, `validate_version` calls for both `v1` and `v2` appear BEFORE any `self._resolve(...)` call, and the `v1 == v2` early-return appears AFTER the both-symbols-missing → `SymbolNotFoundError` raise. Verify by reading the source: the line containing `if v1 == v2:` (or equivalent) appears after the line containing `raise SymbolNotFoundError`.
    - CLI: `uv run ruff check src/mcp_server_python_docs/services/compare.py` exits 0 (H4 verification — no F401 from unused VersionNotFoundError import).
    - CLI: `uv run pyright src/mcp_server_python_docs/services/compare.py` exits 0.
    - Behavior: import succeeds and `CompareService.compare`'s signature is exactly `(self, symbol, v1, v2)`.
  </acceptance_criteria>
  <done>The service module compiles cleanly, type-checks, exposes the documented public API with the H2 branch ordering, has no unused `VersionNotFoundError` import (H4), uses the renamed `signature_delta` field (M1), and emits the M2 note text on `PageNotFoundError` in the both-present branch.</done>
</task>

<task type="auto">
  <name>Task 2: Build the `compare_db` fixture and the FULL behavioral test suite (H2, H3, H4, M1, M2, M3, L1)</name>
  <read_first>
    - tests/test_multi_version.py (full file — clone the `multi_version_db` shape, line 17-81)
    - tests/conftest.py (lines 1-220 — fixture conventions, `_STABILITY_SYMBOLS` table for inspiration)
    - src/mcp_server_python_docs/services/compare.py (the implementation from Task 1 — already in context)
    - src/mcp_server_python_docs/models.py (CompareVersionsResult with signature_delta and note)
    - src/mcp_server_python_docs/errors.py (VersionNotFoundError, SymbolNotFoundError, PageNotFoundError — tests import these from here, NOT from compare.py)
    - .planning/phases/09-compare-versions/09-REVIEWS.md (H2, H3, M1, M2, M3, L1 — the full revision checklist)
    - .planning/phases/09-compare-versions/09-RESEARCH.md (Question 5 — test table; Question 6 — byte-budget proxy)
    - .planning/phases/09-compare-versions/09-CONTEXT.md (Success criteria 1, 2, 3, 4, 5)
  </read_first>
  <action>
    Create new file `tests/test_compare_versions.py`. Use `from __future__ import annotations`. Imports include `from mcp_server_python_docs.errors import VersionNotFoundError, SymbolNotFoundError` (the test file imports these from `..errors`, NOT from `compare.py`, satisfying H4 option (a)).

    Add an inline pytest fixture `compare_db` (do not add to conftest.py — keep it local because it's used only by this test module, matching `multi_version_db`'s inline placement). The fixture builds `tmp_path / "compare.db"` via `get_readwrite_connection` + `bootstrap_schema`, then inserts:

    **Two doc_sets:** `(3.10, is_default=0)` and `(3.11, is_default=1)`.

    **Symbols per version:**
    - In 3.10: `asyncio.run` (function, uri `library/asyncio-runner.html#asyncio.run`, anchor `asyncio.run`); `json.dumps` (function, uri `library/json.html#json.dumps`, anchor `json.dumps`); `pathlib.Path` (class, uri `library/pathlib.html#pathlib.Path`, anchor `pathlib.Path`); `functools.cache` (function, uri `library/functools.html#functools.cache`, anchor `functools.cache`); `some.old_func` (function, uri `library/somemodule.html#some.old_func`, anchor `some.old_func`).
    - In 3.11: `asyncio.run`, `json.dumps`, `pathlib.Path`, `functools.cache`, `some.old_func`, AND `asyncio.TaskGroup` (class, uri `library/asyncio-task.html#asyncio.TaskGroup`, anchor `asyncio.TaskGroup`).

    **Documents + sections (one section per anchor per version):**
    - `asyncio.run` in 3.10: `content_text = "Execute the coroutine and return the result.\n\nMore prose."`
    - `asyncio.run` in 3.11: `content_text = "def asyncio.run(coro, *, debug=None)\n\nExecute the coroutine and return the result.\n\nChanged in version 3.10: Improved behavior."` — line 1 IS a signature (M1 test fixture).
    - `json.dumps` in 3.10 AND 3.11: identical section `content_text = "Serialize obj to a JSON formatted str."` — triggers the unchanged-after-diff branch.
    - `asyncio.TaskGroup` in 3.11 only: `content_text = "An asynchronous context manager holding a group of tasks.\n\nNew in version 3.11."` — triggers `_NEW_IN_RE`.
    - `pathlib.Path` in 3.10: `content_text = "Concrete path classes.\n\nMore prose."` (no see-also)
    - `pathlib.Path` in 3.11: `content_text = "Concrete path classes.\n\nSee also\n\n[os.path](library/os.path.html) — Operating system path manipulation.\n[fnmatch](library/fnmatch.html) — Pattern matching."` — triggers see-also-ADDED (H3 (a)).
    - `functools.cache` in 3.10: `content_text = "Simple cache.\n\nSee also\n\n[lru_cache](library/lru_cache.html) — LRU cache."` (has see-also)
    - `functools.cache` in 3.11: `content_text = "Simple cache.\n\nMore prose only, no see-also."` (no see-also) — triggers see-also-REMOVED (H3 (b)).
    - `some.old_func` in 3.10: `content_text = "Old API."`
    - `some.old_func` in 3.11: `content_text = "Old API.\n\nDeprecated since version 3.11: use new_func() instead."` — triggers `_DEPRECATED_IN_RE` (H3 (c)).

    Rebuild FTS as in `multi_version_db` (the last 3 lines).

    **Write the following tests (one per finding-driven case):**

    1. `test_compare_added_in_v2(compare_db)` — instantiates `ContentService(compare_db)` then `CompareService(compare_db, content_svc)`, calls `compare("asyncio.TaskGroup", "3.10", "3.11")`. Asserts `result.change == "added"`, `result.symbol == "asyncio.TaskGroup"`, `result.v1 == "3.10"`, `result.v2 == "3.11"`, `result.new_in == "3.11"`. Maps to CMPR-01 + success criterion #1.

    2. `test_compare_identical_versions(compare_db)` — calls `compare("json.dumps", "3.11", "3.11")`. Asserts `result.change == "unchanged"`, `result.new_in is None`, `result.section_diff is None`, `result.see_also_added == []`, `result.note is None`. Maps to success criterion #2.

    3. `test_compare_changed_signature(compare_db)` — calls `compare("asyncio.run", "3.10", "3.11")`. Asserts `result.change == "changed"`, `result.changed_in == "3.10"`, and `result.signature_delta is not None`. Additionally assert `"def asyncio.run" in result.signature_delta` (the v2 section's line 1 starts with that signature, so the heuristic SHOULD include it in the delta label). Maps to CMPR-01 — the "changed" branch + M1 signature-delta-on-signature-line test.

    4. `test_compare_signature_delta_documents_prose_change(compare_db)` — calls `compare("pathlib.Path", "3.10", "3.11")` (line 1 of both is "Concrete path classes." — same — so `signature_delta` should be `None`). Asserts `result.signature_delta is None`. Then construct a SECOND in-fixture variant where line 1 differs in prose only (e.g. a section whose v2 line 1 is "Concrete pathy classes."). Assert `result.signature_delta is not None` and add a code comment documenting: "The heuristic flagged a prose change — this is expected advisory behavior per M1; production callers should NOT trust signature_delta as a definitive signature change indicator." Maps to M1 — both heuristic cases tested.

    5. `test_compare_see_also_added(compare_db)` — calls `compare("pathlib.Path", "3.10", "3.11")`. Asserts `result.change == "changed"`, `result.see_also_added` contains at least `"os.path"` (or both `os.path` and `fnmatch`), `result.see_also_removed == []`. Maps to CMPR-01 see-also requirement + H3 (a).

    6. `test_compare_see_also_removed(compare_db)` — calls `compare("functools.cache", "3.10", "3.11")`. Asserts `result.change == "changed"`, `result.see_also_removed` contains at least `"lru_cache"`, `result.see_also_added == []`. Maps to CMPR-01 see-also requirement + H3 (b).

    7. `test_compare_deprecated_in_v2(compare_db)` — calls `compare("some.old_func", "3.10", "3.11")`. Asserts `result.change == "changed"`, `result.deprecated_in == "3.11"`. Maps to CMPR-01 deprecation requirement + H3 (c).

    8. `test_compare_unknown_version_raises_with_indexed_list(compare_db)` (renamed from `test_compare_unknown_version_raises` per M3 — the test MUST assert the indexed-version list appears in the error message, not just `"not found"`): uses `pytest.raises(VersionNotFoundError) as exc_info`. Calls `compare("asyncio.run", "3.99", "3.11")`. Asserts:
       - `"3.99" in str(exc_info.value)` (the missing version is named)
       - `"3.10" in str(exc_info.value)` (at least one indexed version is named) — success criterion #3 actionable-error-with-list
       - `"3.11" in str(exc_info.value)` (the OTHER indexed version is also named)
       Maps to CMPR-02 + success criterion #3 + M3.

    9. `test_compare_identical_versions_missing_symbol_raises(compare_db)` (NEW per H2): uses `pytest.raises(SymbolNotFoundError)`. Calls `compare("does.not.exist", "3.11", "3.11")`. This is the critical H2 fix test — identical versions must NOT short-circuit past symbol-existence validation. Asserts the error is raised. Optional but recommended: `match="does.not.exist"` in the raises clause to verify the symbol name appears in the message.

    10. `test_compare_neither_version_has_symbol(compare_db)` — `pytest.raises(SymbolNotFoundError, match="does.not.exist")`. Calls `compare("does.not.exist", "3.10", "3.11")` (different versions, neither has it). Verifies RESEARCH §Q2(c) step 7.

    11. `test_compare_page_not_available_returns_changed_with_note(compare_db, monkeypatch)` (NEW per M2): the fixture inserts symbols for a fake anchor that has NO matching `documents`/`sections` row, so `ContentService.get_docs` raises `PageNotFoundError`. Alternatively, monkeypatch `ContentService.get_docs` to raise `PageNotFoundError("simulated page not found")` for one of the version calls. Call `compare("asyncio.run", "3.10", "3.11")` under that simulated condition (use a separate symbol if monkeypatching is cleaner). Asserts `result.change == "changed"`, `result.section_diff is None`, `result.note == "docs page not available for one or both versions"`. Maps to M2 — partial-data fallback is "changed + note", not "unchanged".

    12. `test_compare_diff_is_token_frugal(compare_db)` — calls `compare("asyncio.TaskGroup", "3.10", "3.11")`, then `serialized = json.dumps(result.model_dump())`, then `approx_tokens = len(serialized) // 4`, asserts `approx_tokens < 300` with an informative message including byte count. Test docstring MUST state explicitly: `"This is a REGRESSION SMOKE CHECK, not a literal token guarantee. Production tokenization may differ on unicode-heavy content; the assertion catches 'result accidentally got 3x bigger' regressions (per cross-AI review L1)."` Maps to CMPR-03 + success criterion #4 + L1.

    All tests run synchronously (do NOT mark with `@pytest.mark.asyncio` — `asyncio_mode="auto"` only affects async-def tests, and these are sync).
  </action>
  <verify>
    <automated>uv run pytest tests/test_compare_versions.py -x -v && uv run ruff check tests/test_compare_versions.py</automated>
  </verify>
  <acceptance_criteria>
    - CLI: `uv run pytest tests/test_compare_versions.py -x -v` exits 0 (the FULL file, all 12 tests).
    - CLI: `uv run ruff check tests/test_compare_versions.py` exits 0.
    - Source: `tests/test_compare_versions.py` contains each of the 12 required test function names exactly (verify with `grep -c "^def test_" tests/test_compare_versions.py` returns at least 12).
    - Source: fixture `compare_db` is defined at module scope (not in `conftest.py`).
    - Source: test 8 (`test_compare_unknown_version_raises_with_indexed_list`) asserts BOTH the missing version `"3.99"` AND at least one indexed version `"3.10"` or `"3.11"` appear in `str(exc.value)`. Verify by grep: `grep -A 10 "def test_compare_unknown_version_raises_with_indexed_list" tests/test_compare_versions.py | grep -c "3\.10\|3\.11"` returns at least 1.
    - Source: test 9 (`test_compare_identical_versions_missing_symbol_raises`) exists and calls `compare("does.not.exist", "3.11", "3.11")` and expects `SymbolNotFoundError`. Verify by grep: `grep -c "test_compare_identical_versions_missing_symbol_raises" tests/test_compare_versions.py` returns 1, and the test body contains both `"3.11"` and `SymbolNotFoundError`.
    - Source: test 11 (`test_compare_page_not_available_returns_changed_with_note`) asserts `result.note == "docs page not available for one or both versions"`. Verify by grep: `grep -c "docs page not available" tests/test_compare_versions.py` returns at least 1.
    - Source: test 12 docstring contains the phrase "regression smoke check" (case-insensitive — L1 documentation requirement).
    - Source: tests for see-also (`test_compare_see_also_added`, `test_compare_see_also_removed`), deprecation (`test_compare_deprecated_in_v2`), and signature-delta (`test_compare_changed_signature`, `test_compare_signature_delta_documents_prose_change`) all exist (H3 + M1 verification).
    - Behavior: the token-frugality test prints a byte count for the headline result that is under 1200 bytes.
  </acceptance_criteria>
  <done>All 12 tests pass, the file is ruff-clean, every cross-AI review finding (H2, H3, H4, M1, M2, M3, L1) has at least one corresponding test, and the fixture is self-contained.</done>
</task>

<task type="auto">
  <name>Task 3: Full-suite regression — no other test broke</name>
  <read_first>
    - AGENTS.md (Done Means section — full pytest + ruff + pyright is the phase gate)
  </read_first>
  <action>
    Run the full repository test suite plus full ruff and pyright passes to confirm Plan 03's additions did not break anything. If any pre-existing test fails, do NOT silence it — record the failure in the plan SUMMARY and stop. Plan 03 is not allowed to land if it regresses any pre-existing test.

    Note: `tests/test_services.py::test_five_tools_registered` will likely STILL fail at this point because Plan 04 has not yet added the 6th tool. Plan 04 owns updating that test. Plan 03's job is just to ensure NO new failures are introduced — pre-existing test counts (5 tools) are unchanged because Plan 03 doesn't touch `server.py` or `app_context.py`.
  </action>
  <verify>
    <automated>uv run pytest --tb=short -q && uv run ruff check src/ tests/ && uv run pyright src/</automated>
  </verify>
  <acceptance_criteria>
    - CLI: `uv run pytest --tb=short -q` exits 0 (full suite, including the new 12 tests AND the still-correct `test_five_tools_registered` because Plan 03 does not add the 6th tool yet).
    - CLI: `uv run ruff check src/ tests/` exits 0.
    - CLI: `uv run pyright src/` exits 0.
  </acceptance_criteria>
  <done>The full quality gate (AGENTS.md "Done Means") passes for Plan 03's additions in isolation. Plan 04 will re-run the same gate after wiring the tool into `server.py` and updating the 5-tools test to 6-tools.</done>
</task>

</tasks>

<verification>
- All twelve tests in `tests/test_compare_versions.py` pass.
- `CompareService.compare` covers the four diff branches (added / removed / changed / unchanged) with the H2 ordering (validate-versions → resolve-symbol → both-missing → identical-versions).
- The three error/fallback paths (VersionNotFoundError with indexed-list, SymbolNotFoundError including for identical-versions, PageNotFoundError → changed+note) are tested.
- The see-also added/removed, deprecation, and signature-delta heuristics each have a passing test (H3 + M1).
- `compare.py` does NOT import `VersionNotFoundError` (H4 ruff F401 fix verified).
- The token-frugality byte proxy assertion passes for the headline case and is documented as a smoke check (L1).
- The service is SYNC (no asyncio.to_thread, no async def — verified by `grep`).
- The `@log_tool_call("compare_versions")` decorator is on the public method.
- The full repo quality gate (ruff + pyright + pytest) is green.
</verification>

<success_criteria>
- Plan 04 can `from mcp_server_python_docs.services.compare import CompareService` and instantiate it with `(db, content_service)` without further changes.
- Each of the 5 CONTEXT.md success criteria has a corresponding green test command from this plan's test file.
- Every HIGH (H2, H3, H4) and MEDIUM (M1, M2, M3) cross-AI review finding has at least one passing test or source-level assertion that closes it.
- The `must_haves.truths` block is verifiable end-to-end by running `uv run pytest tests/test_compare_versions.py -v`.
</success_criteria>

<output>
Create `.planning/phases/09-compare-versions/09-03-compare-service-SUMMARY.md` when done.
</output>
