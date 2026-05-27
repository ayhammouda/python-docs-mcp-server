---
phase: 09-compare-versions
plan: 02
type: execute
wave: 0
depends_on: []
files_modified:
  - src/mcp_server_python_docs/models.py
autonomous: true
requirements:
  - CMPR-01
  - CMPR-03
must_haves:
  truths:
    - "`models.py` exports a Pydantic `CompareVersionsResult` BaseModel whose fields cover the four diff cases the tool emits (added, removed, changed, unchanged)."
    - "The result model is JSON-serializable via `model_dump()` and emits None-valued optional fields as `null` (not omitted) so the diff shape is predictable across all four cases."
    - "All new fields carry explicit `Field(description=...)` so FastMCP's auto-generated outputSchema is informative for MCP clients."
    - "The signature-delta field is named `signature_delta` (NOT `signature_change`) and its description marks it explicitly as a best-effort first-non-empty-line heuristic that MAY be a docstring change — i.e. advisory, not authoritative. This naming + description prevents callers from over-trusting it."
    - "The `note: str | None = None` field exists on `CompareVersionsResult` so the service can flag partial-data situations (e.g. `\"docs page not available for one or both versions\"`) without pretending no change occurred."
  artifacts:
    - path: "src/mcp_server_python_docs/models.py"
      provides: "CompareVersionsResult Pydantic model + nested types"
      contains: "class CompareVersionsResult(BaseModel):"
  key_links:
    - from: "src/mcp_server_python_docs/models.py"
      to: "src/mcp_server_python_docs/services/compare.py (Plan 03)"
      via: "import: `from mcp_server_python_docs.models import CompareVersionsResult`"
      pattern: "class CompareVersionsResult"
    - from: "src/mcp_server_python_docs/models.py"
      to: "src/mcp_server_python_docs/server.py (Plan 04)"
      via: "import (existing batch import on line ~37): `from mcp_server_python_docs.models import (... CompareVersionsResult ...)`"
      pattern: "from mcp_server_python_docs.models"
---

<objective>
Add `CompareVersionsResult` (and any nested types it needs) to `src/mcp_server_python_docs/models.py`. The model is the typed contract that `CompareService.compare` returns and that FastMCP auto-derives `outputSchema` from for the new `compare_versions` MCP tool.

Purpose: Lock the wire shape before Plan 03 implements the service. Independent of Plan 01 (the spike) because the result model's surface is the same regardless of whether the regex extractors find values or fall back to None.

Output: One file modification — `models.py` — adding the new Pydantic types with `Field(description=...)` on every field, including the renamed `signature_delta` (M1) and new optional `note` field (M2) per cross-AI review.
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

<interfaces>
<!-- Existing models.py pattern (line 41 in current file). New types must follow this pattern. -->

From src/mcp_server_python_docs/models.py:
```python
# Existing models use this pattern: BaseModel + per-field Field(description=...)
class SymbolHit(BaseModel):
    uri: str = Field(description="...")
    title: str = Field(description="...")
    # ...

class SearchDocsResult(BaseModel):
    hits: list[SymbolHit] = Field(default_factory=list, description="...")
    note: str | None = Field(default=None, description="...")
```

Tool result models in models.py already in place (do NOT duplicate or modify):
- `SearchDocsInput`, `SearchDocsResult`, `SymbolHit`
- `GetDocsInput`, `GetDocsResult`
- `VersionInfo`, `ListVersionsResult`
- `DetectPythonVersionResult`
- `PackageDocsResult`, `PackageDocsSource`, `PackageKind`

CONTEXT.md success criteria the model must support:
1. "newly introduced in 3.11" — `change="added"` + `new_in: str | None`
2. "identical versions return empty diff with explicit no-change marker" — `change="unchanged"` + all delta fields `None`
3. "Missing-version" — out of scope for this model; handled by `VersionNotFoundError` raised before any result is constructed
4. "<300 tokens" — drives token-frugality of the model: optional fields use `None` defaults and short field names; do NOT add verbose nested objects when a single string suffices
5. "3 representative symbols" (changed / unchanged / missing) — the model must shape each cleanly
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Add CompareVersionsResult model + change-discriminator Literal to models.py</name>
  <read_first>
    - src/mcp_server_python_docs/models.py (full file — current state of every existing model, especially `SearchDocsResult` (line 57) and `PackageDocsResult` (line 198) for the pattern)
    - .planning/phases/09-compare-versions/09-CONTEXT.md (Success criteria 1-5, requirements CMPR-01 and CMPR-03)
    - .planning/phases/09-compare-versions/09-RESEARCH.md (Question 2(c) for the four service-result branches; Question 6 for the token-frugality target)
    - .planning/phases/09-compare-versions/09-REVIEWS.md (M1: rename signature_change → signature_delta; M2: add `note: str | None` for partial-data flagging)
    - AGENTS.md (Done Means — `ruff check` and `pyright src/` must pass)
  </read_first>
  <action>
    Append a new `# --- compare_versions models ---` section at the end of `src/mcp_server_python_docs/models.py` (after the existing `lookup_package_docs` block, i.e. after the last existing class around line 217). Add a `ChangeKind` Literal alias: `ChangeKind = Literal["added", "removed", "changed", "unchanged"]`. Add a `CompareVersionsResult(BaseModel)` class with these fields, all carrying `Field(description=...)`:

    Required fields (always populated):
    - `symbol: str` — qualified name being compared
    - `v1: str` — source version
    - `v2: str` — target version
    - `change: ChangeKind` — discriminator

    Optional fields (None when not applicable to the `change` case):
    - `new_in: str | None = None` — version string extracted from the v2 section text via the locked `_NEW_IN_RE` regex; populated when `change == "added"`, may also be populated when `change == "changed"` if the v2 section carries a versionadded marker for a sub-feature
    - `removed_in: str | None = None` — populated when `change == "removed"`; equals `v2` (the version where the symbol is first absent)
    - `changed_in: str | None = None` — version extracted via `_CHANGED_IN_RE` from the v2 section; populated when `change == "changed"`
    - `deprecated_in: str | None = None` — version extracted via `_DEPRECATED_IN_RE` from the v2 section
    - `signature_delta: str | None = None` — RENAMED from `signature_change` per cross-AI review M1. Field description MUST mark it explicitly as advisory: `Field(default=None, description="Best-effort heuristic: first non-empty diff line between v1 and v2 section text. MAY be a docstring change or prose change rather than a true signature change — treat as advisory, not authoritative.")`. Populated when `change == "changed"` and the first non-empty lines of the two sections differ.
    - `see_also_added: list[str] = Field(default_factory=list)` — see-also link labels that appear in v2 but not v1
    - `see_also_removed: list[str] = Field(default_factory=list)` — see-also link labels that appear in v1 but not v2
    - `section_diff: str | None = None` — short unified-diff snippet from `difflib.unified_diff`, truncated to ~600 chars to honor the <300-token budget; populated only when `change == "changed"` and the diff is non-trivially short
    - `note: str | None = None` — NEW field per cross-AI review M2. Description: `Field(default=None, description="Optional advisory note about result completeness, e.g. when docs pages could not be fetched for one or both versions and the diff is therefore based on symbol presence alone.")`. Used by the service to honestly flag partial-data states (e.g. PageNotFoundError in the both-present branch) without forcing the result into the wrong `change` category.

    Add a docstring on `CompareVersionsResult` referencing CMPR-01 and CMPR-03. Do NOT add a `CompareVersionsInput` model — the existing pattern in `server.py` uses standalone `Annotated[..., Field(...)]` aliases (see `SymbolParam`/`CompareVersionParam` planned in Plan 04), not Input models (this matches the audit recorded in memory 1610: `PackageDocsInput` was removed as unused).

    Do NOT use the name `signature_change` anywhere. The previous design used that name; this plan supersedes it. Verify by grep: `grep -c "signature_change" src/mcp_server_python_docs/models.py` MUST return 0 after the edit.

    Do not touch any existing model in the file.
  </action>
  <verify>
    <automated>uv run ruff check src/mcp_server_python_docs/models.py && uv run pyright src/mcp_server_python_docs/models.py && uv run python -c "from mcp_server_python_docs.models import CompareVersionsResult, ChangeKind; r=CompareVersionsResult(symbol='asyncio.TaskGroup', v1='3.10', v2='3.11', change='added', new_in='3.11'); print(r.model_dump_json()); assert r.change == 'added' and r.new_in == '3.11' and r.see_also_added == [] and r.section_diff is None and r.signature_delta is None and r.note is None; r2=CompareVersionsResult(symbol='x', v1='3.10', v2='3.11', change='changed', signature_delta='line 1 differs', note='docs page not available for one or both versions'); assert r2.signature_delta == 'line 1 differs' and r2.note.startswith('docs page')" && ! grep -q "signature_change" src/mcp_server_python_docs/models.py</automated>
  </verify>
  <acceptance_criteria>
    - Source: `src/mcp_server_python_docs/models.py` contains the line `class CompareVersionsResult(BaseModel):`.
    - Source: `src/mcp_server_python_docs/models.py` contains the line `ChangeKind = Literal["added", "removed", "changed", "unchanged"]`.
    - Source: `src/mcp_server_python_docs/models.py` contains `signature_delta: str | None = Field(` (renamed per M1).
    - Source: `src/mcp_server_python_docs/models.py` contains `note: str | None = Field(` (new field per M2).
    - Source: `src/mcp_server_python_docs/models.py` does NOT contain the string `signature_change` (verified by `grep -c "signature_change" src/mcp_server_python_docs/models.py` returning 0).
    - CLI: `uv run ruff check src/mcp_server_python_docs/models.py` exits 0.
    - CLI: `uv run pyright src/mcp_server_python_docs/models.py` exits 0.
    - Behavior: `from mcp_server_python_docs.models import CompareVersionsResult` succeeds; constructing with `change='added', new_in='3.11'` returns a model where `signature_delta is None` and `note is None`. Constructing with `change='changed', signature_delta='line 1 differs', note='docs page not available for one or both versions'` returns a model where both new fields are populated.
    - Behavior: constructing with an invalid `change` value (e.g. `change='wat'`) raises `pydantic.ValidationError`.
  </acceptance_criteria>
  <done>The new types are exported from `models.py`, the file passes ruff and pyright, and the renamed `signature_delta` + new `note` field are both accepted by the model constructor.</done>
</task>

<task type="auto">
  <name>Task 2: Token-frugality smoke check on the new model</name>
  <read_first>
    - .planning/phases/09-compare-versions/09-RESEARCH.md (Question 6 — byte-budget proxy)
    - .planning/phases/09-compare-versions/09-REVIEWS.md (L1 — the 1200-byte ceiling is a regression smoke check, NOT a token guarantee)
    - src/mcp_server_python_docs/models.py (current state after Task 1 — already in context)
  </read_first>
  <action>
    Run an inline Python check that constructs a representative "added" result and a representative "changed" result and asserts `len(json.dumps(r.model_dump())) // 4 < 300` for the "added" case (the most token-frugal of the four). For "changed", construct with a 500-char `section_diff` plus a populated `signature_delta` and `note` and assert `< 300` (which lands at ~1200 bytes ceiling, leaving headroom). This is a smoke check on the model shape only — the real assertion lives in Plan 03's tests.

    Per cross-AI review L1: this byte-count proxy is a regression smoke check, not a token guarantee. Production tokenization may differ on unicode-heavy content; the proxy's job is to catch "the result accidentally got 3x bigger" regressions, not to certify a literal token count.

    Record the actual byte counts in a one-line comment-style stdout (`print(...)`) so Plan 03 has a baseline. Do not write a test file — Plan 03 owns that.
  </action>
  <verify>
    <automated>uv run python -c "import json; from mcp_server_python_docs.models import CompareVersionsResult; added=CompareVersionsResult(symbol='asyncio.TaskGroup', v1='3.10', v2='3.11', change='added', new_in='3.11'); b1=len(json.dumps(added.model_dump())); changed=CompareVersionsResult(symbol='asyncio.run', v1='3.10', v2='3.11', change='changed', changed_in='3.10', signature_delta='line 1 differs', section_diff='x'*500, note='docs page not available for one or both versions'); b2=len(json.dumps(changed.model_dump())); print(f'added={b1}B (~{b1//4}tok) changed={b2}B (~{b2//4}tok)'); assert b1//4 < 300, f'added case {b1//4} tokens'; assert b2//4 < 300, f'changed case {b2//4} tokens'"</automated>
  </verify>
  <acceptance_criteria>
    - Behavior: smoke check command exits 0 and prints `added=<N>B (~<M>tok) changed=<N>B (~<M>tok)` with both M values strictly less than 300.
    - Source: no test file is created in this task (Plan 03 owns the test suite).
    - Source: the model in `models.py` is unchanged by this task — this is a measurement only.
    - Behavior: the "changed" case smoke check successfully constructs a result with BOTH the new `signature_delta` and `note` fields populated, confirming Task 1's edit accepts both.
  </acceptance_criteria>
  <done>Both representative results serialize under 1200 bytes (i.e. under 300 approx tokens), confirming the model shape is token-frugal with the new `signature_delta` and `note` fields included.</done>
</task>

</tasks>

<verification>
- `models.py` exports `CompareVersionsResult` and `ChangeKind` with no impact on the other 5 existing tool models.
- The model uses `signature_delta` (NOT `signature_change`) and includes `note: str | None`.
- The model is JSON-serializable and stays well under the 300-token budget for the headline "added" case.
- `ruff check` and `pyright` both pass on the edited file.
</verification>

<success_criteria>
- Plan 03 can `from mcp_server_python_docs.models import CompareVersionsResult` and return instances from `CompareService.compare(...)` without any further model changes, including populating `signature_delta` and `note`.
- Plan 04 can include `CompareVersionsResult` in the existing batch import in `server.py` line 30-40 without circular-import problems (no new module-level state added to `models.py`).
- FastMCP's auto-derived `outputSchema` for the future `compare_versions` tool will include descriptions for every field, including the advisory-language description on `signature_delta`.
</success_criteria>

<output>
Create `.planning/phases/09-compare-versions/09-02-result-models-SUMMARY.md` when done.
</output>
