# Audit Fixes Roadmap

> **For agentic workers:** this file indexes nine PR plans. Execute each plan
> file on its own branch with superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans. Read this roadmap's Decisions
> and Global Constraints first: every plan inherits them.

**Goal:** Ship every fix from the 2026-10-30 whole-repo audit in nine
reviewable PRs, as refined by the best-practice research. This covers the 19
numbered findings, the 11 smaller ones, and 10 related issues the research
found.

**Spec:** `docs/research/2026-10-30-audit-fix-best-practices.md`. Its verdict
tables list every finding, and its sections give the evidence and the fix to
ship. Where it refines the audit, it wins. For benchmark work,
`docs/benchmarks/AGENTIC-BENCHMARK-DESIGN.md` (PR #161) §8, §10 and §11 also
bind.

**Priority set by the owner:** quality first, then token cost and speed.

## Decisions

Each decision states what it costs if it turns out wrong.

- **D1 Search fix.** Change the `sections_fts` tokenizer to
  `unicode61 remove_diacritics 2 tokenchars '_'`. Do not patch the query.
  - Research (Search 1): this fixes more cases, keeps the 319 related tests
    green and avoids a bm25 skew.
  - The cost is a `schema.sql` change, which is forbidden territory, plus an
    index rebuild.
  - If the owner refuses the schema change, ship the research's fallback:
    opt-in query expansion with explicit `AND`, in `SearchService` only.
  - Measured side effect (PR 1 plan): for a bare module name in
    `kind="section"` mode, entry headings such as `json. dumps ( obj )` now
    also match. The `module-json` overview drops from 2nd to 4th on the real
    3.13 page. The default `search_docs("json")` still resolves the module
    first through the symbol table. Listed below as an owner decision.
- **D2 Index format version.** PR 1 stamps `PRAGMA user_version = 1` in
  `schema.sql`. When an index's version differs (older or newer), `doctor`
  reports FAIL and prints the `build-index` command for the versions that
  index already holds.
  - A startup refusal waits until an old index becomes unreadable (YAGNI;
    research Ingestion 6).
  - Cost if wrong: users with an old index see a doctor FAIL until they
    rebuild. Their search still runs, with the old recall.
- **D3 One rebuild PR.** Findings 1 and 2 share PR 1. Both change what the
  index holds, and one regression run on one rebuilt index proves both.
- **D4 Slugs.** `get_docs` accepts `.html` and resolves the canonical slug
  before the persistent-cache lookup. It returns the canonical slug and gives
  an actionable not-found error (Search 2).
  - PR 2 edits only the description in `server.py`. The unused `models.py`
    copy is deleted in PR 3.
- **D5 Version notes.** `changed_in` is the newest note present in v2 and
  absent from v1. `new_in` is the smallest "Added/New in version" in the v2
  section (Search 3).
  - Structured note extraction at ingestion waits: it needs a schema change
    and a full re-ingestion.
- **D6 Pager.** Ship the minimal `text[end]` fix with a
  `shortcut: combining marks only, switch to unicodedata.iter_graphemes when
  the floor is 3.15` comment (Search 4).
- **D7 Dead code and schema.** A snapshot of the served `tools/list`
  replaces the input-model snapshots. Drop:
  - the `synonyms` and `redirects` tables;
  - `symbols.document_id` and `symbols.section_id`, together with the SELECTs
    and branches that read them;
  - `ingestion_runs.artifact_hash`, the misleading value from N8, together with
    `compute_sha256` and its tests;
  - `zstd-dict-v1`, keeping the `compression` column.

  Old indexes keep the unused tables and columns, which does no harm. No
  rebuild and no version bump.
  - Releases up to 0.4.0 still read the two `symbols` columns. So PR 3 ships
    in the same release as PR 1, and its CHANGELOG entry says to rebuild after
    a downgrade.
  - Vision may instead keep the two nullable columns and drop only their
    readers.
- **D8 Build hygiene.**
  - Close the connection before deleting the build, on every failure path.
  - Run `PRAGMA journal_mode = DELETE` in the last read-write step of
    `publish_index`.
  - Swap with a hard link, falling back to a copy.
  - Delete a failed build after logging its smoke messages at ERROR.
  - Sweep `build-*.db*` files older than one day at the start of `build-index`.
  - Keep readers on `mode=ro`; no `immutable=1` (Ingestion 3 and 4).
- **D9 pysqlite3.** Delete the extra; do not wire it in (Ingestion 2).
- **D10 Vision publish.** The client lists files with
  `git diff-tree -r -z --no-renames` and `git cat-file blob`, and sends
  `tree_sha`. The broker refuses a tree mismatch before the review. Client
  and broker change in the same PR (Ops 1).
- **D11 Installer.** Do the host user's file work as that user, through
  `runuser`. Keep `directory()` for root-owned trees. Validate the `.pub`
  format (Ops 4; covers N1 and N2).
- **D12 Verifier parsing.** Add `extract_verdict`: exactly one verdict
  object, found with `raw_decode`, with strict types, and `malformed_verdict`
  on failure (Ops 6; covers N3).
- **D13 Release.** Job outputs `dist-id` and `dist-sha256`. Download by
  artifact ID and compare the listing before attesting and publishing. Build
  constraints with hashes for `hatchling` (Ops 5).
  - SLSA L3 attestation in the build job waits.
- **D14 Benchmarks.**
  - Fix now where the code survives the redesign: `runner.py`, `report.py`,
    `scoring.py`, `claude_tokens.py`, `regression.py`, the shared failure
    helper, and a new shared URL module.
  - Adapter-only problems get the smallest fix now and become requirements
    for the new harness.
  - No adapter dedupe before the planned deletion (Benchmarks 5d).
- **D15 Token count model.** Use `claude-sonnet-5-5`, as the approved design
  §8 says. This replaces the audit's suggestion of `claude-opus-5-5`.
- **D16 Mutation testing.** It waits for a spike: the tests load
  `control.py` by path, a CI job is forbidden territory, and it would add a dev
  dependency. The tests that kill the 10 surviving hand-made mutants ship now,
  in PR 6.

## PR Map

| PR | Branch | Findings | Plan | Forbidden territory / supervisor review | Rebuild | Depends on |
|---|---|---|---|---|---|---|
| 1 | `fix/index-search-quality` | 1, 2; D2 version stamp and doctor check | `2026-10-30-index-search-quality.md` | `schema.sql` → yes | Yes | #162 |
| 2 | `fix/tool-correctness` | 3 (+N7), 4, 15, 18, synonym expansion once | `2026-10-30-tool-correctness.md` | tool description text → yes | No | #162 |
| 3 | `refactor/remove-dead-code` | 17 (+N4, N8) | `2026-10-30-dead-code-and-schema.md` | `schema.sql`, test deletions → yes | No | #162; rebase over PR 1 if it landed |
| 4 | `fix/build-publish-hygiene` | 13 (+N5, N11), `index.db` gap | `2026-10-30-build-publish-hygiene.md` | yes: its rulings go beyond D8 (§7, last row) | No | #162 |
| 5 | `chore/drop-pysqlite3-extra` | 12 | `2026-10-30-drop-pysqlite3-extra.md` | `pyproject.toml` `[project]` → yes | No | #162; re-lock over #159/#160 |
| 6 | `fix/vision-publish-review` | 5, 7, 19 (+N3), status receipt ids, branch rules and ref pre-check | `2026-10-30-vision-publish-review.md` | yes: relaxes one existing assertion (one ref GET before review) and goes beyond the research | No | — |
| 7 | `fix/vision-installer-setup` | 14 (+N1, N2), variable by name, one permission map, dead rerun entry | `2026-10-30-vision-installer-setup.md` | yes: design choices beyond the research (backup write, key check, `uv` copy) | No | rebase over PR 6 (`control.py`); deploy with PR 6 in one reinstall (policy hash) |
| 8 | `ci/release-artifact-integrity` | 16 | `2026-10-30-release-artifact-integrity.md` | `.github/workflows/` → yes | No | — |
| 9 | `fix/benchmark-integrity` | 6 (+N9), 8 (+N10), 9, 10 (+N6), 11, corpus snapshot, network tripwire | `2026-10-30-benchmark-integrity.md` | existing assertions change on purpose → yes | No | #162 |

**Order:**
- **Wave 0:** PR #162 (MCP SDK v2) merges. Every branch is cut from `main`
  after that. If a plan starts first, cut it from `feat/mcp-sdk-v2` and rebase
  onto `main` once #162 merges.
- **Wave 0.5:** a docs-only PR puts this roadmap, the nine plans and the
  research note on `main`. Every plan stops if they are missing, because
  `AGENTS.md` accepts only linked files that exist.
- **Wave 1, in parallel:** PRs 1, 3, 4, 5, 6, 8 and 9. They touch different
  files, apart from small, separate hunks in `__main__.py`, `storage/db.py`
  and `schema.sql`.
  - Merge PR 4 first, because it isolates the test that writes into the
    developer's real cache (N11).
  - Then PR 1, PR 3 and PR 5. Whichever lands last refreshes the line
    references in `DESIGN.md`.
- **Wave 2:** PR 2, which then regenerates `tests/fixtures/tools-list.json` if
  PR 3 has landed. PR 7 follows PR 6, because both edit `ops/vision/control.py`.

## Global Constraints

- **Precondition:** `docs/plans/2026-10-30-*.md` and `docs/research/2026-10-30-audit-fix-best-practices.md` are on `main` (roadmap Wave 0.5); stop if not.
These apply to every plan.

- **Base:** `main` after #162 merges. Never merge `feat/mcp-sdk-v2` into
  another branch.
- **TDD:** follow superpowers:test-driven-development. Write each failing
  test first and watch it fail. One finding is one commit, with its test and
  its fix together. Refactors stay separate from behaviour changes.
- **Gate (`AGENTS.md`):** run it fresh before opening the PR.
  - `uv run --locked ruff check src/ tests/ benchmarks/ ops/ .github/scripts/`
  - `uv run --locked pyright src/ benchmarks/`
  - `uv run --locked pytest --tb=short -q`
- **Existing tests:** never delete one or weaken an assertion, except where a
  plan says so explicitly (PRs 3 and 9). Those PRs list each change in the PR
  body.
- **Forbidden territory:** `AGENT-EXECUTION-PIPELINE.md` §2 and §7 apply.
  - Any PR that touches it gets the `supervisor-review` label.
  - Its PR body has a `## Why this triggered supervisor review` section. That
    section cites the owner approval: "The owner (Aymen Hammouda) asked on
    2026-10-30 to implement all audit findings; decisions in
    `docs/plans/2026-10-30-audit-fixes-roadmap.md`."
- **Dependencies and network:** no new runtime dependency and no new runtime
  network access. A dev-dependency addition needs owner approval; none is
  planned.
- **Tool surface:** tool names, parameters and result shapes stay the same.
  The only planned tool-surface text change is the `get_docs` slug
  description in PR 2.
- **Never touch the real cache:**
  - No plan step may build into, sweep or delete from the developer's real
    cache folder (`~/Library/Caches/mcp-python-docs` or
    `~/.cache/mcp-python-docs`).
  - Agent shells do not keep exported variables between calls, and
    platformdirs treats an empty `XDG_CACHE_HOME` as unset. So every
    isolated-index command:
    - either sets `ISO=/tmp/<name>;` in the same command and passes
      `XDG_CACHE_HOME="${ISO:?}"`, after a step that prints `get_index_path()`
      and stops on a real-cache path;
    - or patches both `get_cache_dir` bindings in a `python -c` one-liner.
  - Never delete files in the real cache by hand.
- **Retrieval checks:** PRs 1 and 2 change retrieval. Each runs
  `uv run --locked python -m benchmarks.regression --output <file>` on a full
  `build-index --versions 3.11,3.12,3.13` index. If results change, record
  them with `--record` and note that the baseline change needs independent
  review.
- **CHANGELOG:** add entries under `## [Unreleased]`, grouped as Fixed,
  Changed, Removed or Security. PR 1's entry states that an index rebuild is
  required, with the exact command.
- **Commits and PRs:**
  - Commits use conventional prefixes and end with
    `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
  - PR bodies follow `.github/PULL_REQUEST_TEMPLATE/agent.md` and end with
    `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
  - Never self-merge. Vision merges after independent verification.

## Carried Into The New Benchmark Harness

These are requirements, not separate PRs here (design §11):

- The bridge opens `httpx2.AsyncClient` with `async with`.
- It catches `MCPError` per call.
- It enforces trajectory caps with `asyncio.timeout()` and classifies them
  with `expired()`.
- It reuses PR 9's URL module for grounding.
- Token counting runs after the run, with `system` and `tools`.
- C1 evidence comes only from proxy logs.
- Context7 runs in a container installed from a committed lockfile:
  - `npm ci --ignore-scripts --omit=dev`, then `npm audit signatures`;
  - the image is pinned by digest;
  - it runs as non-root with a read-only root filesystem.
- The five fixed-flow adapters are deleted, not refactored.

## Deferred Until The Owner Decides

- Whether to accept D1's measured section-mode ranking shift, or to add a
  ranking tweak in a follow-up PR. PR 1 records the shift on the full index.
- A mutation-testing job (D16).
- Structured version notes at ingestion (D5).
- Grapheme-cluster paging (D6).
- Dropping `.` from `examples_fts` too (Search 1: measure separately).
- SLSA L3 attestation in the build job (D13).
- A startup refusal of old indexes (D2).

## Execution

Since quality comes first, use superpowers:subagent-driven-development for
every plan. Run each wave's plans in parallel sessions, one worktree per
branch.
