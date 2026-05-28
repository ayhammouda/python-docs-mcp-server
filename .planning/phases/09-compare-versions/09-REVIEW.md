---
phase: 09-compare-versions
reviewed: 2026-05-28T21:55:00Z
depth: standard
files_reviewed: 10
files_reviewed_list:
  - src/mcp_server_python_docs/models.py
  - src/mcp_server_python_docs/services/compare.py
  - src/mcp_server_python_docs/app_context.py
  - src/mcp_server_python_docs/server.py
  - tests/test_compare_versions.py
  - tests/test_compare_versions_spike.py
  - tests/test_retrieval_regression.py
  - tests/test_services.py
  - README.md
  - .github/INTEGRATION-TEST.md
findings:
  critical: 1
  warning: 4
  info: 3
  total: 8
status: issues_found
---

# Phase 9: Code Review Report

**Reviewed:** 2026-05-28T21:55:00Z
**Depth:** standard
**Files Reviewed:** 10
**Status:** issues_found

## Summary

Phase 09 adds the `compare_versions` MCP tool: `CompareService.compare`, the
`CompareVersionsResult` model, FastMCP wiring, behavioral tests, a spike script,
and docs. The wiring, branch ordering (H2), and error delegation (H4) are sound,
and the test suite is thorough for the seeded fixture.

The dominant defect is a **slug-derivation mismatch** between `CompareService`
and the rest of the retrieval stack. `compare.py` naively strips the symbol URI
at `#` to get a `get_docs` slug, but the search path deliberately does NOT do
this — it normalizes `.html` vs extensionless slugs through
`ranker._resolve_symbol_location` / `_document_candidates`. Because real
ingestion stores extensionless document slugs (`library/json`) while symbol URIs
carry `.html` (`library/json.html`), `compare`'s section fetch will miss in
production for typical stdlib symbols. The behavioral tests do not catch this
because the test fixture stores `.html` slugs in `documents`, which does not
mirror the production ingestion shape. Net effect in production: most
`change="changed"`/both-present comparisons silently fall into the M2
`PageNotFoundError` fallback — reporting `change="changed"` + a "docs page not
available" note even for genuinely unchanged symbols, and never populating
`changed_in` / `deprecated_in` / `signature_delta` / `see_also_*` / `section_diff`.
This is a correctness regression that degrades the tool to "presence diff only"
on real data while tests stay green.

## Critical Issues

### CR-01: compare's slug derivation bypasses the .html/extensionless slug normalization the rest of the stack relies on

**File:** `src/mcp_server_python_docs/services/compare.py:130-139`
**Issue:**
`_section_text` derives the document slug with
`slug = uri.split("#", 1)[0]` and passes it directly to
`ContentService.get_docs(slug=slug, ...)`, which queries
`documents WHERE slug = ?` (`content.py:73-77`). Symbol URIs come from
`objects.inv` and use `.html` paths (e.g. `library/json.html#json.dumps`), but
Sphinx JSON content is ingested with **extensionless** document slugs (e.g.
`library/json`). This is exactly the mismatch that the search path handles
deliberately via `ranker._document_candidates` (`ranker.py:24-34`) and
`_resolve_symbol_location` (`ranker.py:37-103`), which try both the `.html` form
and the stripped form and also fall back on `uri` matches. `compare.py`
re-implements slug derivation but skips that normalization entirely.

Consequences in production (real index):
- both-present branch: `get_docs` raises `PageNotFoundError`, so the M2 fallback
  at `compare.py:199-210` fires and returns `change="changed"` +
  `note="docs page not available for one or both versions"` for **every**
  both-present symbol — including symbols that did not actually change. All of
  `changed_in`, `deprecated_in`, `signature_delta`, `see_also_*`, and
  `section_diff` are never computed.
- `added` branch: `new_in` extraction is silently swallowed
  (`compare.py:179-183`), so `new_in` is always `None` for real "added" symbols.

The behavioral tests pass only because `tests/test_compare_versions.py:136-140`
inserts `documents` with `slug = slug` where `slug` is already the `.html` form
(e.g. `library/asyncio-runner.html`), so the naive split happens to match. The
fixture does not reproduce the production extensionless-slug shape documented for
the real ingestion path, so the suite gives false confidence.

**Fix:** Resolve the section via the same path the search stack uses instead of
hand-rolling slug derivation. Two viable options:

```python
# Option A — resolve through the document candidates the ranker already uses.
from mcp_server_python_docs.retrieval.ranker import _document_candidates

def _section_text(self, uri: str, anchor: str, version: str) -> str:
    for slug in _document_candidates(uri):
        try:
            return self._content.get_docs(
                slug=slug, version=version, anchor=anchor
            ).content
        except PageNotFoundError:
            continue
    raise PageNotFoundError(f"no document for symbol uri {uri!r} v{version}")
```

Prefer promoting `_document_candidates` / `_resolve_symbol_location` to a shared,
non-underscore helper so `compare` and `ranker` cannot drift. Additionally, add a
behavioral test whose fixture stores **extensionless** document slugs (matching
production ingestion) so this class of bug is caught by CI rather than masked.

## Warnings

### WR-01: see-also link extraction captures unrelated body links within the 20-line window

**File:** `src/mcp_server_python_docs/services/compare.py:77-99`
**Issue:**
`_extract_see_also` locates the first line containing "see also", then matches
**every** markdown link (`_SEE_ALSO_LINK_RE = r"\[([^\]]+)\]\("`) in the next 20
lines until an ATX (`#`) heading. In real markdownify output a Sphinx `seealso`
admonition is rendered as prose, not as an ATX heading, so the window is bounded
only by the 20-line cap. Any ordinary inline links that follow the see-also block
(cross-reference links, footnote links, body links) within those 20 lines are
captured as "see also" labels, polluting `see_also_added` / `see_also_removed`
with false positives. The model docstring itself warns the window must be honored
"not against the whole section," but the line cap is too coarse to enforce that.
**Fix:** Tighten the window: stop at the first blank line after the last
consecutive link-bearing line, or restrict capture to lines that *start* with a
list/link marker, rather than scanning a fixed 20-line block. At minimum,
document that captured labels are best-effort and may include adjacent body
links.

### WR-02: section_diff is truncated by raw character slice, producing a corrupted/misleading diff

**File:** `src/mcp_server_python_docs/services/compare.py:240-242`
**Issue:**
When the unified diff exceeds `_SECTION_DIFF_MAX_CHARS` (600), it is truncated
with `section_diff = section_diff[:600]`. This cuts mid-line, so the final diff
line is partial and may drop the trailing `+`/`-` context or split a hunk header,
yielding output that no longer parses as a unified diff and can mislead a model
consuming it. There is also no truncation marker, so the consumer cannot tell the
diff was cut. **Fix:** Truncate on a line boundary and append an explicit marker,
e.g.:

```python
if section_diff is not None and len(section_diff) > _SECTION_DIFF_MAX_CHARS:
    kept = section_diff[:_SECTION_DIFF_MAX_CHARS].rsplit("\n", 1)[0]
    section_diff = kept + "\n... (diff truncated)"
```

### WR-03: removed_in is set to v2 unconditionally, which can be factually wrong

**File:** `src/mcp_server_python_docs/services/compare.py:188-192`
**Issue:**
The `removed` branch sets `removed_in=v2`. But the symbol may have been removed
in some version *between* v1 and v2 (e.g. comparing 3.9 -> 3.13 for a symbol
dropped in 3.11 reports `removed_in="3.13"`). The model field is documented as
"version where the symbol is first absent," so reporting v2 is not the first
absent version and can mislead callers about *when* the removal happened. The
model docstring acknowledges `removed_in` "equals v2," but the
`CompareVersionsResult.removed_in` description claims "first absent," which is
contradictory. **Fix:** Either rename/redescribe the field to "absent as of v2"
to match behavior, or compute the earliest indexed version (> v1, <= v2) in which
the symbol is absent. At minimum, align the model description with the actual
semantics to avoid a misleading contract.

### WR-04: signature_delta heuristic emits noise for any prose change and is labeled "signature"

**File:** `src/mcp_server_python_docs/services/compare.py:215-223`
**Issue:**
`signature_delta` compares only the first non-empty line of each section. When a
section's first line is prose (not a signature) — common for class/module
sections like `pathlib.Path` whose first line is descriptive text — a pure
documentation/prose edit flips `signature_delta` to a non-`None` "line 1 differs"
message that reads like a signature change. The test
`test_compare_signature_delta_documents_prose_change` explicitly encodes this as
"expected advisory behavior," but a field literally named `signature_delta`
producing prose-diff noise is a quality/contract hazard for downstream models
that will weight it as a signature change despite the docstring caveat.
**Fix:** Gate the heuristic to lines that look like a signature (e.g. start with
`def `, `class `, or contain `(`), or rename the field to `line1_delta` /
`first_line_delta` to stop implying signature semantics. Keep the advisory note
in the description regardless.

## Info

### IN-01: assert used for type narrowing will be stripped under python -O

**File:** `src/mcp_server_python_docs/services/compare.py:195`
**Issue:**
`assert sym_v1 is not None and sym_v2 is not None` is used to narrow types for the
checker. Under `python -O` (assertions disabled) this is a no-op; while the
preceding branches make it logically unreachable when false, relying on `assert`
for control-flow invariants is fragile. **Fix:** This one is purely a
type-narrowing aid and the branch structure guarantees it, so it is low risk;
optionally replace with an explicit guard or a `typing.cast` to avoid depending
on assertions being enabled.

### IN-02: compare.py imports sqlite3 only for a type annotation

**File:** `src/mcp_server_python_docs/services/compare.py:27,120-128`
**Issue:**
`import sqlite3` is used solely for the `db: sqlite3.Connection` parameter
annotation. With `from __future__ import annotations` in effect, the annotation
is a string at runtime, so the import is only needed for static checking. This is
consistent with the rest of the codebase (other services import it the same way),
so it is not a defect — noting only for completeness. **Fix:** None required;
leave as-is for consistency with sibling services.

### IN-03: test_create_server_has_three_tools name is now misleading

**File:** `tests/test_services.py:411-419`
**Issue:**
`test_create_server_has_three_tools` only asserts that `search_docs`, `get_docs`,
and `list_versions` exist (a subset check), but the server now registers six
tools and a sibling test `test_six_tools_registered` asserts the count. The
"three tools" name is stale and can confuse future readers into thinking the
server has three tools. **Fix:** Rename to
`test_create_server_registers_core_tools` (or fold into the six-tool assertion)
to reflect current reality.

---

_Reviewed: 2026-05-28T21:55:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
