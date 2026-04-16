# Roadmap: mcp-server-python-docs

## Overview

v0.1.0 decomposes the 4-week build guide into 8 phases that each deliver a verifiable slice of a FastMCP stdio server over CPython stdlib documentation. The journey goes from hardened stdio hygiene + a real sphobjinv symbol slice (Phase 1), through a locked SQLite/FTS5 schema (Phase 2), a pure retrieval layer with FTS5 injection protection (Phase 3), the highest-risk Sphinx JSON ingestion path with atomic-swap publishing (Phase 4), full service wiring with `get_docs`/`list_versions` and caching (Phase 5), multi-version correctness and packaging verification (Phase 6), stability tests and release polish (Phase 7), to manual integration against Claude Desktop and Cursor plus the PyPI publish (Phase 8). Phase ordering is load-bearing and follows the research ordering constraints in `.planning/research/SUMMARY.md`.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Foundation & Stdio Hygiene & Symbol Slice** - Package skeleton, FastMCP lifespan DI, stdio hygiene, sphobjinv symbol vertical slice talking to Claude Desktop
- [x] **Phase 2: Schema & Storage** - schema.sql with corrected FTS5 tokenizer, composite symbol uniqueness, platformdirs cache resolution (completed 2026-04-16)
- [ ] **Phase 3: Retrieval Layer** - fts5_escape injection guard, synonym query expansion, BM25 + snippet ranking, Unicode-safe budget enforcement
- [ ] **Phase 4: Sphinx JSON Ingestion & Atomic-Swap Publishing** - Dedicated-venv CPython builds, per-doc failure isolation, HTML-to-markdown conversion, atomic rename with rollback
- [ ] **Phase 5: Services, Tool Polish & Caching** - get_docs / list_versions wiring, LRU caches, structured observability logging, validate-corpus CLI
- [ ] **Phase 6: Multi-Version & Packaging Correctness** - 3.12 + 3.13 co-ingestion, default-version resolution, cross-version URI collision test, wheel content verification
- [ ] **Phase 7: Stability Tests & Release Polish** - ~20 structural stability tests, doctor subcommand, README install / troubleshooting / config snippets
- [ ] **Phase 8: Ship** - Manual integration test against Claude Desktop + Cursor, PyPI Trusted Publishing, tag v0.1.0

## Phase Details

### Phase 1: Foundation & Stdio Hygiene & Symbol Slice
**Goal**: A FastMCP stdio server installable from source, wired with typed lifespan DI, survives Claude Desktop tool invocation without a single byte of stdout pollution, and returns a real symbol hit for `asyncio.TaskGroup` backed by `sphobjinv`-parsed `objects.inv` data.
**Depends on**: Nothing (first phase)
**Requirements**: HYGN-01, HYGN-02, HYGN-03, HYGN-04, HYGN-05, HYGN-06, SRVR-01, SRVR-02, SRVR-05, SRVR-06, SRVR-09, SRVR-10, SRVR-11, SRVR-12, STOR-06, STOR-07, STOR-08, STOR-10, INGR-I-01, INGR-I-02, INGR-I-03, INGR-I-04, INGR-I-05, INGR-I-06, CLI-01, CLI-03
**Success Criteria** (what must be TRUE):
  1. `mcp-server-python-docs serve` launches under Claude Desktop and lists exactly `search_docs` as a tool (get_docs + list_versions land in later phases), with zero non-MCP bytes on stdout across a full round-trip (subprocess stdout-sentinel test passes).
  2. `mcp-server-python-docs build-index --versions 3.13` downloads `docs.python.org/3.13/objects.inv` via `sphobjinv` and populates a local SQLite index with ~13K+ symbol rows, including `asyncio.TaskGroup` with an expanded URI.
  3. `search_docs(query="asyncio.TaskGroup")` returns a hit whose `uri` contains `asyncio-task.html` and whose `kind` is a symbol type, sourced from the symbol table (not FTS).
  4. A missing `index.db` produces a copy-paste `build-index` invocation on stderr; a startup without FTS5 raises a platform-aware error (Linux x86-64 mentions `pysqlite3-binary`, macOS/Windows/ARM mentions `uv python install`).
  5. A committed JSON schema fixture for every registered tool's `inputSchema` and `outputSchema` passes the Pydantic schema-snapshot drift-guard test.
**Plans**: TBD
**UI hint**: no

### Phase 2: Schema & Storage
**Goal**: A locked-in SQLite schema that preserves Python identifier search, eliminates cross-version URI collisions, resolves cache paths via `platformdirs`, and can be bootstrapped idempotently — verified before any content ingestion so the tokenizer choice never triggers a full rebuild later.
**Depends on**: Phase 1
**Requirements**: STOR-01, STOR-02, STOR-03, STOR-04, STOR-05, STOR-09
**Success Criteria** (what must be TRUE):
  1. An FTS5 tokenizer regression fixture indexes `asyncio.TaskGroup`, `json.dumps`, and `collections.OrderedDict` and retrieves each via exact-token search (no Porter stemming collapse) — proves `unicode61 remove_diacritics 2 tokenchars '._'` is applied to `sections_fts`, `symbols_fts`, and `examples_fts`.
  2. Inserting `json.dumps` as both `function` and `method` into `symbols` succeeds under the composite `UNIQUE(doc_set_id, qualified_name, symbol_type)` constraint.
  3. A cross-version URI collision fixture (same slug present in 3.12 and 3.13 rows) inserts cleanly because `sections.uri` is no longer globally unique — only `UNIQUE(document_id, anchor)` enforces uniqueness.
  4. Running `schema_bootstrap.py` twice against the same file is a no-op and leaves `doc_sets.language` defaulting to `'en'`.
  5. `platformdirs.user_cache_dir("mcp-python-docs")` is used everywhere the cache path is computed — `rg '~/.cache'` returns zero hits in the source tree.
**Plans**: TBD
**UI hint**: no

### Phase 3: Retrieval Layer
**Goal**: A pure-logic retrieval module that never crashes SQLite on adversarial FTS input, expands synonym queries, classifies symbol-shaped queries to fast-path before FTS, ranks via BM25 with column weights + `snippet()` excerpts, and enforces Unicode-safe budget truncation — with all known domain errors surfaced as `isError: true` content rather than protocol errors.
**Depends on**: Phase 2
**Requirements**: RETR-01, RETR-02, RETR-03, RETR-04, RETR-05, RETR-06, RETR-07, RETR-08, RETR-09, SRVR-08
**Success Criteria** (what must be TRUE):
  1. A 50+ input fuzz suite (`c++`, `"unbalanced`, `*`, `(`, empty string, single char, `AND OR NOT NEAR`, etc.) runs `fts5_escape()` end-to-end into a real FTS5 `MATCH` and never raises `sqlite3.OperationalError`.
  2. A grep audit shows zero call sites that build FTS5 `MATCH` strings without routing through `fts5_escape()` — every query path is guarded.
  3. `search_docs(query="asyncio.TaskGroup")` short-circuits to the symbol table (classifier detects the `.`-qualified shape) and returns results with the locked hit shape `{uri, title, kind, snippet, score, version, slug, anchor}` — identical schema whether the hit came from the symbol fast-path or from FTS5.
  4. A BM25 ranking test confirms heading > content_text and qualified_name > module column weights; every hit carries a ~200-char FTS5 `snippet()` excerpt.
  5. `apply_budget(text, max_chars, start_index)` never splits a Unicode codepoint across the truncation boundary (4-byte emoji + combining-character fixtures pass); raising `VersionNotFoundError` / `SymbolNotFoundError` / `PageNotFoundError` surfaces as `isError: true` with an informative content message, not a protocol error.
**Plans**: TBD
**UI hint**: no

### Phase 4: Sphinx JSON Ingestion & Atomic-Swap Publishing
**Goal**: `build-index` clones CPython source at a pinned tag, stands up a dedicated venv with the branch-pinned Sphinx, invokes `sphinx-build -b json` directly, parses every `.fjson` with per-document failure isolation, converts section HTML bodies to markdown, populates `sections_fts` / `examples_fts` / `synonyms`, then atomically swaps the new `index.db` into place with a `.previous` rollback — without crashing a running RO server.

**Research flag**: Deeper research recommended before starting this phase. Re-verify (a) that `cpython/3.12/Doc/requirements.txt` still pins `sphinx~=8.2.0` and `cpython/3.13/Doc/requirements.txt` still pins `sphinx<9.0.0`; (b) that no new open Sphinx issues block the JSON builder; (c) that `pyspecific` and other custom CPython doc extensions still serialize through the JSON builder without `NotImplementedError: Unknown node`; (d) actual end-to-end build time + memory footprint via a dry run of both versions. Upstream Sphinx JSON has historically drifted — run `/gsd-research-phase 4` before `/gsd-plan-phase 4`.
**Depends on**: Phase 3
**Requirements**: INGR-C-01, INGR-C-02, INGR-C-03, INGR-C-04, INGR-C-05, INGR-C-06, INGR-C-07, INGR-C-08, INGR-C-09, PUBL-01, PUBL-02, PUBL-03, PUBL-04, PUBL-05, PUBL-06
**Success Criteria** (what must be TRUE):
  1. `build-index --versions 3.13` clones CPython at a pinned tag into a temp dir, creates a dedicated venv with `sphinx<9.0.0`, and invokes `./venv/bin/sphinx-build -b json Doc/ Doc/build/json/` directly (never `make json`) — producing `.fjson` files that parse into `documents` + `sections` rows.
  2. Ingestion converts section body HTML to markdown before persistence (not raw HTML), extracts doctest-vs-example code blocks into `examples`, populates `sections_fts` + `examples_fts` in the same transaction as canonical tables, and seeds `synonyms` from the packaged `synonyms.yaml`; a deliberately broken `.fjson` fixture does not abort the whole build — the error is logged and the run continues.
  3. New index is written to `~/.cache/mcp-python-docs/build-{timestamp}.db`, smoke-tested (row count + spot-check on a known section), SHA256-stamped into `ingestion_runs.artifact_hash`, atomically renamed to `index.db` with the previous kept as `index.db.previous` for rollback.
  4. After swap, `build-index` prints to stderr: "Index rebuilt. Restart your MCP client to pick up the new index." acknowledging the POSIX-rename open-FD limitation (v0.1.0 decision: document-restart, no SIGHUP).
  5. An ingestion-while-serving regression test spawns a server holding an RO WAL handle, runs a full rebuild in parallel, and asserts the server process is alive and still serving hits throughout (stale results are acceptable; crashes are not).
**Plans**: TBD
**UI hint**: no

### Phase 5: Services, Tool Polish & Caching
**Goal**: Full service layer wiring `SearchService` / `ContentService` / `VersionService`, registration of `get_docs` and `list_versions` as MCP tools with the same annotations + `_meta` hints as `search_docs`, LRU caching on hot reads, structured per-call stderr observability via service-method decorators, and a standalone `validate-corpus` CLI that runs swap-time smoke tests against the current `index.db`.
**Depends on**: Phase 4
**Requirements**: SRVR-03, SRVR-04, SRVR-07, OPS-01, OPS-02, OPS-03, OPS-04, OPS-05, PUBL-07
**Success Criteria** (what must be TRUE):
  1. `get_docs(slug="library/asyncio-task.html", anchor="asyncio.TaskGroup")` returns a windowed, budget-enforced markdown section, carries `_meta = {"anthropic/maxResultSizeChars": 16000}`, and is registered with `readOnlyHint=True, destructiveHint=False, openWorldHint=False`.
  2. `list_versions()` is registered with the same annotations and dispatches through `VersionService`.
  3. Every tool call writes one structured log line to stderr containing tool name, version, latency_ms, result_count, truncation flag, symbol-resolution path (`exact|fuzzy|fts`), and `synonym_expansion=yes|no` — implemented via per-service-method decorators, not FastMCP middleware.
  4. A cache-hit benchmark shows `get_section_cached` (maxsize=512) and `resolve_symbol_cached` (maxsize=128) are hit on repeat calls; caches are process-lifetime-scoped with no TTL and no invalidation (user restart on rebuild is documented).
  5. `mcp-server-python-docs validate-corpus` runs the same smoke-test suite Phase 4 runs at swap time against the currently-live `index.db` and exits 0 on pass, non-zero on fail.
**Plans**: TBD
**UI hint**: no

### Phase 6: Multi-Version & Packaging Correctness
**Goal**: `build-index --versions 3.12,3.13` co-ingests both versions into a single `index.db`, default-version resolution returns 3.13, cross-version URI collisions are harmless, and a built wheel installed via `uvx` or `pipx` is verifiably self-contained (synonyms.yaml inside the wheel).
**Depends on**: Phase 5
**Requirements**: MVER-01, MVER-02, MVER-03, MVER-04, MVER-05, PKG-01, PKG-02, PKG-03, PKG-04, PKG-06
**Success Criteria** (what must be TRUE):
  1. `build-index --versions 3.12,3.13` in a single invocation produces an `index.db` containing two `doc_sets` rows — `is_default=True` on 3.13 — and a cross-version URI-collision fixture (same slug in both versions) does not violate any `UNIQUE` constraint.
  2. Calling `search_docs` without a `version` resolves to 3.13; calling with `version="3.99"` returns `isError: true` with `"version 3.99 not found; available: [3.12, 3.13]"`; `list_versions()` returns both rows with `{version, language, label, is_default, built_at}`.
  3. `uv build` produces a wheel whose `unzip -l` output contains `src/mcp_server_python_docs/data/synonyms.yaml`, and the CI wheel-content check fails the build if the file is missing.
  4. `uvx mcp-server-python-docs --version` and `pipx run mcp-server-python-docs --version` both print `0.1.0` from a fresh environment, exercising the `[project.scripts]` entry-point declared in `pyproject.toml` with pinned runtime deps (`mcp>=1.27.0,<2.0.0`, `sphobjinv>=2.4,<3.0`, `pydantic>=2,<3`, `click>=8,<9`, `platformdirs>=4`, `pyyaml>=6`, markdown converter).
**Plans**: TBD
**UI hint**: no

### Phase 7: Stability Tests & Release Polish
**Goal**: ~20 structural stability tests that survive CPython doc revisions, a `doctor` CLI subcommand for first-run diagnostics, and a README with copy-paste `mcpServers` snippets + install / first-run / troubleshooting sections — so that when integration testing finds something broken in Phase 8, reports are actionable and the fix is mechanical.
**Depends on**: Phase 6
**Requirements**: TEST-01, TEST-02, TEST-03, TEST-04, TEST-05, TEST-06, CLI-02, SHIP-03, SHIP-04, SHIP-05
**Success Criteria** (what must be TRUE):
  1. ~20 stability tests assert structural properties (`len(hits) >= 1`, `"asyncio" in hit.uri`) — not exact content — and pass on macOS and Linux in CI against a freshly built 3.12+3.13 index; Windows is exercised best-effort but not gate-blocking.
  2. The full test pyramid is green: unit (fts5_escape 50-input fuzz, Unicode-safe budget, synonym expansion, symbol classification), storage (schema idempotency, WAL, FTS5 check, repo queries), ingestion (objects.inv fixture, Sphinx JSON fixture, atomic swap), and a stdio smoke test that spawns the server as a subprocess and asserts zero stdout pollution across a full round-trip for every registered tool.
  3. `mcp-server-python-docs doctor` inspects the environment (Python version, SQLite FTS5 availability, cache dir, `index.db` presence, free disk) and prints a PASS/FAIL report for each probe.
  4. The README ships copy-paste `mcpServers` config snippets for Claude Desktop on macOS/Linux/Windows paths, an install section (`uvx mcp-server-python-docs`), a first-run section (`build-index --versions 3.12,3.13`), and a troubleshooting section covering FTS5 unavailable, `uvx` cache stale, Claude Desktop MSIX on Windows, and the "restart after rebuild" requirement.
  5. A `Support` section in the README documents "Tested on macOS and Linux; Windows should work (uses `platformdirs` + `pathlib`) but is not verified on every release."
**Plans**: TBD
**UI hint**: no

### Phase 8: Ship
**Goal**: v0.1.0 is manually verified end-to-end against both Claude Desktop and Cursor on the target query, published to PyPI via GitHub Actions Trusted Publishing with attestations, tagged, and the README install instructions are re-verified end-to-end against the published package.
**Depends on**: Phase 7
**Requirements**: SHIP-01, SHIP-02, SHIP-06, PKG-05, PKG-07
**Success Criteria** (what must be TRUE):
  1. A human operator configures Claude Desktop's `mcpServers` block to launch `uvx mcp-server-python-docs`, asks "what is asyncio.TaskGroup", and receives a correct symbol hit pointing at `library/asyncio-task.html#asyncio.TaskGroup` within the token budget.
  2. The same query against Cursor MCP settings returns an equivalently correct response.
  3. A GitHub Actions release workflow with PyPI Trusted Publishing + attestations publishes `mcp-server-python-docs==0.1.0` to PyPI with zero manual token upload.
  4. v0.1.0 is tagged in Git, and a fresh machine (or throwaway virtualenv) installs via `uvx mcp-server-python-docs` using README instructions verbatim and produces a working Claude Desktop hit on `asyncio.TaskGroup`.
**Plans**: TBD
**UI hint**: no

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation & Stdio Hygiene & Symbol Slice | 0/TBD | Not started | - |
| 2. Schema & Storage | 3/3 | Complete    | 2026-04-16 |
| 3. Retrieval Layer | 0/TBD | Not started | - |
| 4. Sphinx JSON Ingestion & Atomic-Swap Publishing | 0/TBD | Not started | - |
| 5. Services, Tool Polish & Caching | 0/TBD | Not started | - |
| 6. Multi-Version & Packaging Correctness | 0/TBD | Not started | - |
| 7. Stability Tests & Release Polish | 0/TBD | Not started | - |
| 8. Ship | 0/TBD | Not started | - |
