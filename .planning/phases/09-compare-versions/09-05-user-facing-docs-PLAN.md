---
phase: 09-compare-versions
plan: 05
type: execute
wave: 3
depends_on:
  - 09-04-mcp-tool-wiring
files_modified:
  - README.md
  - .github/INTEGRATION-TEST.md
autonomous: true
requirements:
  - CMPR-01
must_haves:
  truths:
    - "`README.md` 'Tools' section lists six tools (currently lists five), with `compare_versions` as a new row."
    - "`README.md` no longer says 'currently exposes five MCP tools' or 'five read-only MCP tools' — both prose mentions are updated to six."
    - "`.github/INTEGRATION-TEST.md` Test 1 (MCP Inspector quick loop) tool-list checklist includes ALL six current tools, in this order: `search_docs`, `get_docs`, `lookup_package_docs`, `list_versions`, `detect_python_version`, `compare_versions`. Per cross-AI review L2 option (a), `lookup_package_docs` is added to the checklist as part of this edit — since we are touching the list, we close the pre-existing inconsistency rather than papering over it."
    - "Test 1 includes one Inspector-callable verification step for `compare_versions`: `compare_versions(symbol=\"asyncio.TaskGroup\", v1=\"3.10\", v2=\"3.11\")` expecting `change=\"added\"` and `new_in=\"3.11\"`."
    - "All updates honor `POSITIONING.md` — the locked positioning sentence and the three key phrases are not altered."
    - "Issue #32 hygiene: `git log --oneline origin/main..HEAD | rg 'Closes|Fixes|Resolves #32'` returns ZERO matches before PR creation. The `Closes #32` keyword belongs in the PR BODY only (per the #35 incident retrospective). The plan executor must run this pre-PR check explicitly and surface zero matches in the SUMMARY before the PR is opened."
  artifacts:
    - path: "README.md"
      provides: "User-facing tool list reflecting the six-tool surface"
      contains: "compare_versions"
    - path: ".github/INTEGRATION-TEST.md"
      provides: "Manual MCP QA runbook entry for ALL six tools (compare_versions + lookup_package_docs gap closed)"
      contains: "compare_versions"
  key_links:
    - from: "README.md (Tools section, line ~178-188)"
      to: "src/mcp_server_python_docs/server.py::compare_versions"
      via: "table row labelled `compare_versions`"
      pattern: "\\| `compare_versions` \\|"
    - from: ".github/INTEGRATION-TEST.md (Test 1)"
      to: "the running MCP server"
      via: "checklist entry under `Confirm the tool list includes:` and a follow-up `Call compare_versions ...` step"
      pattern: "compare_versions"
---

<objective>
Update the two user-facing surfaces that lag behind the new MCP tool: `README.md` (which still says "five" tools in two places, lines ~46 and ~180) and `.github/INTEGRATION-TEST.md` (which lists four of the current tools in Test 1's checklist without `compare_versions` and also without `lookup_package_docs`).

Per cross-AI review L2: since we are editing the INTEGRATION-TEST.md tool list, we close BOTH gaps in one pass — adding `compare_versions` AND `lookup_package_docs` — rather than leaving a pre-existing inconsistency in place.

Per cross-AI review issue-#32 hygiene: the plan executor runs `git log --oneline origin/main..HEAD | rg 'Closes|Fixes|Resolves #32'` as a pre-PR check and records zero matches in the SUMMARY before opening the PR. This prevents an accidental issue closure recurrence of the #35 incident.

Purpose: Satisfy the AGENTS.md "Done Means" gate — "user-facing docs reflect the current behavior" — and complete CMPR-01 from the user's perspective (the tool isn't really shipped if nobody knows it exists).

Output: Two file modifications, no source code touched. Final task runs the full quality gate one more time AND the issue-#32 hygiene grep to confirm Phase 09 is complete.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@AGENTS.md
@.planning/POSITIONING.md
@.planning/phases/09-compare-versions/09-CONTEXT.md
@.planning/phases/09-compare-versions/09-REVIEWS.md
@README.md
@.github/INTEGRATION-TEST.md

<interfaces>
<!-- README hook points (current state, line numbers verified) -->

README.md line 46 (in "What you get" section):
> - five read-only MCP tools

README.md line 180 (in "Tools" section):
> The server currently exposes five MCP tools:

README.md lines 182-188 (the Tools table):
```
| Tool | Description |
|------|-------------|
| `search_docs` | Search Python stdlib docs by query. ... |
| `get_docs` | Retrieve a specific documentation page or section by slug and optional anchor. ... |
| `lookup_package_docs` | Look up official PyPI package metadata ... |
| `list_versions` | List all indexed Python versions with metadata. |
| `detect_python_version` | Detect the user's local Python version ... |
```

.github/INTEGRATION-TEST.md lines 38-50 (Test 1 — MCP Inspector quick loop) — current state (the L2 finding: lookup_package_docs is absent):
```
- [ ] Connect successfully over stdio
- [ ] Confirm the tool list includes:
  - `search_docs`
  - `get_docs`
  - `list_versions`
  - `detect_python_version`
- [ ] Call `search_docs` with query `asyncio.TaskGroup`, ...
- [ ] Call `get_docs` for the returned slug and anchor
- [ ] Call `list_versions`
- [ ] Call `detect_python_version`
```

POSITIONING.md (locked sentence — do NOT alter):
> For AI coding agents writing Python, python-docs-mcp-server is the canonical Python stdlib oracle: exact symbols, exact sections, exact versions — offline, **always free, always MIT**, token-frugal.
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Update README.md Tools table and prose mentions of "five tools"</name>
  <read_first>
    - README.md lines 40-50 ("What you get" list — verify the "five" mention is on line ~46)
    - README.md lines 175-200 (Tools section — full table)
    - .planning/POSITIONING.md (locked sentence — for guardrail)
    - .planning/phases/09-compare-versions/09-CONTEXT.md (CMPR-01 tool description)
  </read_first>
  <action>
    Make three edits to `README.md`:

    (1) Line ~46 in "What you get" — change `- five read-only MCP tools` to `- six read-only MCP tools`.

    (2) Line ~180 — change `The server currently exposes five MCP tools:` to `The server currently exposes six MCP tools:`.

    (3) Tools table — append one new row immediately after the `detect_python_version` row (currently the last row, ~line 188). New row:

    `| `compare_versions` | Diff a Python stdlib symbol between two indexed versions. Returns `change=added|removed|changed|unchanged` with optional `new_in`, `changed_in`, `deprecated_in`, `signature_delta` (advisory heuristic), `see_also_added/removed`, `section_diff`, and `note` deltas. Token-frugal — emits only changed fields, not full content. |`

    (Note: per cross-AI review M1, the field is `signature_delta` not `signature_change`; per M2 the row includes the new `note` field.)

    Do NOT alter the hero sentence (line 5 — locked positioning per POSITIONING.md). Do NOT alter any other section. Do NOT update the v0.1.4 MCP Registry badge in the header — that badge tracks the registry's last-published version and is out of scope for this phase.

    After editing, manually re-read the Tools section to confirm Markdown table syntax is intact (pipe alignment is not load-bearing for renderers, but a missing pipe will break the table).
  </action>
  <verify>
    <automated>grep -c "^- six read-only MCP tools$" README.md | grep -q "^1$" && grep -c "^The server currently exposes six MCP tools:$" README.md | grep -q "^1$" && grep -c "^| \`compare_versions\` |" README.md | grep -q "^1$" && ! grep -q "five read-only MCP tools" README.md && ! grep -q "currently exposes five MCP tools" README.md && grep -q "signature_delta" README.md && ! grep -q "signature_change" README.md</automated>
  </verify>
  <acceptance_criteria>
    - Source: `README.md` contains exactly one line `- six read-only MCP tools` and zero lines `- five read-only MCP tools`.
    - Source: `README.md` contains exactly one line `The server currently exposes six MCP tools:` and zero lines `The server currently exposes five MCP tools:`.
    - Source: `README.md` Tools table contains a row starting `| \`compare_versions\` |`.
    - Source: `README.md` contains `signature_delta` (M1 — the new advisory field name) and does NOT contain `signature_change` (the old name).
    - Source: the locked positioning sentence on README.md line 5 is unchanged (verify by `grep -c "canonical Python stdlib oracle" README.md` returns at least 1).
    - Behavior: rendering README.md in a markdown viewer shows a 6-row Tools table with `compare_versions` as the last row.
  </acceptance_criteria>
  <done>README accurately reflects the six-tool surface; prose and table are consistent; field names match Plan 02 (signature_delta + note).</done>
</task>

<task type="auto">
  <name>Task 2: Add compare_versions AND lookup_package_docs to INTEGRATION-TEST.md Test 1 (L2 fix)</name>
  <read_first>
    - .github/INTEGRATION-TEST.md lines 25-52 (Test 1 — MCP Inspector quick loop section)
    - .planning/phases/09-compare-versions/09-CONTEXT.md (Success criterion 1 — `compare_versions("asyncio.TaskGroup", "3.10", "3.11")` is the canonical end-to-end smoke test)
    - .planning/phases/09-compare-versions/09-REVIEWS.md (L2 option (a) — close the pre-existing `lookup_package_docs` gap while we're here)
  </read_first>
  <action>
    Modify `.github/INTEGRATION-TEST.md` Test 1 — "MCP Inspector quick loop" (the section starting around line 25). Make THREE edits (per cross-AI review L2 option (a) — close the pre-existing inconsistency too):

    **(1) In the "Confirm the tool list includes:" checklist (currently lines 39-43), the existing list is:**
    ```
      - `search_docs`
      - `get_docs`
      - `list_versions`
      - `detect_python_version`
    ```

    Replace with the complete six-tool list, in this order (matching `server.py` registration order):
    ```
      - `search_docs`
      - `get_docs`
      - `lookup_package_docs`
      - `list_versions`
      - `detect_python_version`
      - `compare_versions`
    ```

    Per cross-AI review L2 option (a): we are adding BOTH `lookup_package_docs` (the pre-existing gap) AND `compare_versions` (the Phase 09 addition). The rationale: since this edit touches the checklist anyway, closing the pre-existing inconsistency costs one extra line and prevents a future "the list is supposed to be complete but isn't" reviewer-confusion incident.

    **(2) After the existing `- [ ] Call \`detect_python_version\`` line (~line 49) and before the `- [ ] Observe no protocol corruption ...` line (~line 51), insert a new checklist item for compare_versions:**

    `- [ ] Call \`compare_versions\` with \`symbol="asyncio.TaskGroup"\`, \`v1="3.10"\`, \`v2="3.11"\`` (newline) `  - Expected: result with \`change="added"\` and \`new_in="3.11"\`, JSON serialization under ~1200 bytes`

    (Use the same two-space indent for the Expected sub-bullet as the rest of Test 1.)

    **(3) Optional consistency edit — do NOT add a `lookup_package_docs` Call step:**

    Adding the tool to the enumeration list (Edit 1) is enough to close the L2 gap. Adding a Call step would expand scope beyond Phase 09. Document in the SUMMARY: "L2 closure adds `lookup_package_docs` to the enumeration list only; the Call-step for it is deferred to a future cleanup."

    Do NOT add a separate top-level test section for compare_versions — keep it inline in Test 1. The other tests (Test 2 Claude Desktop, Test 3 Cursor, Test 4 Fresh install, Test 5 Slow E2E) do NOT need updates — they ask domain-style questions of the model rather than enumerating tools by name.
  </action>
  <verify>
    <automated>grep -c "  - \`compare_versions\`" .github/INTEGRATION-TEST.md | grep -q "^1$" && grep -c "  - \`lookup_package_docs\`" .github/INTEGRATION-TEST.md | grep -q "^1$" && grep -c "Call \`compare_versions\` with" .github/INTEGRATION-TEST.md | grep -q "^1$" && grep -c "change=\"added\".*new_in=\"3.11\"" .github/INTEGRATION-TEST.md | grep -q "^1$"</automated>
  </verify>
  <acceptance_criteria>
    - Source: `.github/INTEGRATION-TEST.md` Test 1 checklist contains a sub-bullet `  - \`compare_versions\`` exactly once.
    - Source: `.github/INTEGRATION-TEST.md` Test 1 checklist contains a sub-bullet `  - \`lookup_package_docs\`` exactly once (L2 fix).
    - Source: `.github/INTEGRATION-TEST.md` contains a checklist item starting `- [ ] Call \`compare_versions\` with` exactly once.
    - Source: that checklist item includes `change="added"` and `new_in="3.11"` in its Expected line.
    - Behavior: an operator following Test 1 can verify ALL SIX TOOLS are enumerated by the MCP server before any release (gap-closed checklist).
  </acceptance_criteria>
  <done>The manual MCP QA runbook teaches an operator how to verify the new tool over the stdio protocol, AND the pre-existing `lookup_package_docs` omission is closed (L2).</done>
</task>

<task type="auto">
  <name>Task 3: Final phase quality gate + issue-#32 hygiene check</name>
  <read_first>
    - AGENTS.md "Done Means" lines 67-73 — the four required-clean gates
    - .planning/phases/09-compare-versions/09-REVIEWS.md (issue-#32 hygiene paragraph — pre-PR keyword check)
  </read_first>
  <action>
    Run the full AGENTS.md "Done Means" gate one last time, plus an end-to-end import smoke check that confirms the package builds cleanly with all Phase 09 additions and the new tool is enumerable. Then run the issue-#32 hygiene check.

    **Issue-#32 hygiene check (per cross-AI review):** before opening the PR that closes #32, verify that NO intermediate commit message contains the GitHub closing keywords for issue #32. The keywords `Closes`, `Fixes`, and `Resolves` followed by `#32` ANYWHERE in the commit message body will auto-close the issue at merge time even if the PR is reverted later — and the #35 incident retrospective established that the closing keyword belongs in the PR body only, not in intermediate commits. The check:

    ```
    git log --oneline origin/main..HEAD | rg 'Closes|Fixes|Resolves #32'
    ```

    The expected result is ZERO output lines (i.e. `rg` exits 1 because nothing matched). Record the exact command and its output in the SUMMARY before PR creation. If any matches appear, do NOT open the PR — work with the user to rewrite the offending commit message(s) (e.g. via `git commit --amend` for the most recent, or `git rebase -i` for older — but only if the user explicitly approves the rebase).

    This is the final phase verification — if anything fails here, do NOT mark Phase 09 complete; record the failure in the SUMMARY and open a follow-up issue.
  </action>
  <verify>
    <automated>uv run ruff check src/ tests/ && uv run pyright src/ && uv run pytest --tb=short -q && uv run python -c "import asyncio; from mcp_server_python_docs.server import create_server; mcp = create_server(); tools = asyncio.run(mcp.list_tools()); names = sorted(t.name for t in tools); assert 'compare_versions' in names, names; assert len(names) == 6, f'expected 6 tools, got {len(names)}: {names}'; print('Phase 09 tools registered:', names)" && (git log --oneline origin/main..HEAD | grep -E 'Closes|Fixes|Resolves' | grep -E '#32' | wc -l | tr -d ' ' | grep -q '^0$' || (echo 'ISSUE-32-HYGIENE-FAIL: closing keyword leaked into intermediate commit message; rewrite before PR' && exit 1))</automated>
  </verify>
  <acceptance_criteria>
    - CLI: `uv run ruff check src/ tests/` exits 0.
    - CLI: `uv run pyright src/` exits 0.
    - CLI: `uv run pytest --tb=short -q` exits 0 (full suite, all tests including the new `tests/test_compare_versions.py` AND the updated `test_six_tools_registered`).
    - Behavior: the in-process tool enumeration confirms exactly 6 registered tools including `compare_versions`.
    - Behavior: the issue-#32 hygiene grep returns ZERO matches (no intermediate commit contains `Closes #32` / `Fixes #32` / `Resolves #32`).
    - Source: README.md and `.github/INTEGRATION-TEST.md` both mention `compare_versions` (verify with `grep -l compare_versions README.md .github/INTEGRATION-TEST.md`).
    - Source: SUMMARY records the issue-#32 hygiene check's exact command and zero-match outcome.
  </acceptance_criteria>
  <done>Every AGENTS.md "Done Means" gate is green, `compare_versions` is callable end-to-end, AND the issue-#32 hygiene grep confirms the PR body is the only place the `Closes #32` keyword will appear. Phase 09 is complete and ready for a PR that closes issue #32.</done>
</task>

</tasks>

<verification>
- README.md Tools table has six rows; the new row uses `signature_delta` (NOT `signature_change`) and mentions `note`.
- README.md prose says "six" tools in both places.
- INTEGRATION-TEST.md Test 1 enumerates ALL SIX tools including the previously-missing `lookup_package_docs` (L2 closed) and includes a Call-step for `compare_versions`.
- POSITIONING.md locked sentence is unchanged.
- Full repo quality gate green.
- Issue-#32 hygiene grep returns zero matches before PR creation.
</verification>

<success_criteria>
- An operator running the README "Tools" section can see the new tool without reading any commit history or planning docs.
- An operator running INTEGRATION-TEST.md Test 1 will catch a regression of `compare_versions` registration AND of any of the other five tools (the checklist is now complete).
- The PR closing issue #32 includes the file changes from Plans 02-05 plus the SUMMARY files from each plan.
- The PR body uses `Closes #32` exactly once. Intermediate commit messages do NOT include `Closes`/`Fixes`/`Resolves #32` — verified by the Task 3 hygiene grep (per the #35 incident retrospective).
</success_criteria>

<output>
Create `.planning/phases/09-compare-versions/09-05-user-facing-docs-SUMMARY.md` when done.
</output>
