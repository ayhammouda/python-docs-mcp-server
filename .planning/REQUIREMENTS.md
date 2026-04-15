# Requirements: mcp-server-python-docs

**Defined:** 2026-04-15
**Core Value:** LLMs can answer Python stdlib questions with precise, section-level evidence instead of flooding their context with full doc pages — closing a specific gap that general-purpose doc MCPs (Context7, DeepWiki) do not cover well for the Python stdlib.

## v1 Requirements

Requirements for v0.1.0. Each maps to exactly one roadmap phase.

### Protocol Hygiene (HYGN)

Must land in Phase 1. If any of these break, the server silently fails against Claude Desktop or Cursor.

- [ ] **HYGN-01**: Server redirects `sys.stdout` → `sys.stderr` for the process lifetime using `os.dup2()` at process boot, preserving the real stdout fd for the MCP framer only
- [ ] **HYGN-02**: Server configures `logging.basicConfig(stream=sys.stderr, level=INFO)` before importing any other module
- [ ] **HYGN-03**: Server installs a SIGPIPE handler and swallows `BrokenPipeError` during shutdown so client disconnect doesn't raise a noisy traceback
- [ ] **HYGN-04**: Subprocess stdout-sentinel test asserts that a spawned server process writes zero bytes of non-MCP data to stdout during a full tool round-trip
- [ ] **HYGN-05**: Lifespan errors are caught, logged to stderr, written to a `last-error.log`, and the process exits with `SystemExit(1)` (never silently)
- [ ] **HYGN-06**: `tools/list` response omits `nextCursor` (works around Claude Code bug #24785)

### Server & Tool Surface (SRVR)

FastMCP-based stdio server with exactly 3 tools.

- [ ] **SRVR-01**: FastMCP server instance is created with a typed `app_lifespan` + `AppContext` dataclass for dependency injection — not module-level globals
- [ ] **SRVR-02**: `search_docs(query, version=None, kind="auto", max_results=5)` is registered as an MCP tool with `readOnlyHint=True`, `destructiveHint=False`, `openWorldHint=False`
- [ ] **SRVR-03**: `get_docs(slug, version=None, anchor=None, max_chars=8000, start_index=0)` is registered as an MCP tool with the same annotations
- [ ] **SRVR-04**: `list_versions()` is registered as an MCP tool with the same annotations
- [ ] **SRVR-05**: Every tool return type is a Pydantic `BaseModel` so FastMCP auto-generates `outputSchema` and returns `structuredContent` alongside the `content` text
- [ ] **SRVR-06**: A Pydantic schema-snapshot test asserts every tool's `inputSchema` and `outputSchema` match a committed JSON fixture (drift guard)
- [ ] **SRVR-07**: `get_docs` tool metadata includes `_meta = {"anthropic/maxResultSizeChars": 16000}`
- [ ] **SRVR-08**: Known domain errors (`VersionNotFoundError`, `SymbolNotFoundError`, `PageNotFoundError`) are surfaced as `isError: true` with an informative `content` message, not as protocol errors
- [ ] **SRVR-09**: Server runs over stdio transport only — no HTTP, no SSE
- [ ] **SRVR-10**: Server fails fast at startup if the SQLite index is missing, printing a copy-paste `build-index` invocation to stderr
- [ ] **SRVR-11**: `app_lifespan` loads the pre-compiled synonym table into memory once at startup, not per-request
- [ ] **SRVR-12**: Synonym data ships inside the package at `src/mcp_server_python_docs/data/synonyms.yaml` and is loaded via `importlib.resources`

### Storage & Schema (STOR)

Must land in Phase 2, BEFORE any ingestion (tokenizer changes would require a full rebuild).

- [ ] **STOR-01**: `schema.sql` defines `doc_sets`, `documents`, `sections`, `symbols`, `examples`, `synonyms`, `redirects`, `ingestion_runs` tables per build guide §7
- [ ] **STOR-02**: `sections_fts`, `symbols_fts`, `examples_fts` use `tokenize = "unicode61 remove_diacritics 2 tokenchars '._'"` — Porter stemming is NOT applied (preserves Python identifier search)
- [ ] **STOR-03**: `symbols` table uses `UNIQUE(doc_set_id, qualified_name, symbol_type)` instead of `UNIQUE(doc_set_id, qualified_name)` (allows e.g. `json.dumps` as both function and method)
- [ ] **STOR-04**: `sections` table drops `UNIQUE(uri)`; uniqueness is enforced only by `UNIQUE(document_id, anchor)` (allows cross-version URI overlap in a single DB)
- [ ] **STOR-05**: `doc_sets.language` column exists with default `'en'` (reserved for future i18n, no migration debt)
- [ ] **STOR-06**: Connection factory opens two distinct handles: a read-only serving handle (`sqlite3.connect(uri=True)` with `?mode=ro`) and a read-write ingestion handle
- [ ] **STOR-07**: Both handles set `PRAGMA journal_mode = WAL`, `PRAGMA synchronous = NORMAL`, `PRAGMA foreign_keys = ON` at open time
- [ ] **STOR-08**: `assert_fts5_available()` runs at server startup and raises `FTS5UnavailableError` with a **platform-aware** message (`pysqlite3-binary` only suggested on Linux x86-64)
- [ ] **STOR-09**: Schema bootstrap is idempotent — running it twice on the same file is a no-op
- [ ] **STOR-10**: Cache directory path is resolved via `platformdirs.user_cache_dir("mcp-python-docs")` — no hardcoded `~/.cache/`

### Retrieval Layer (RETR)

Pure-logic module. No MCP types, no SQL — sits between services and storage.

- [ ] **RETR-01**: `fts5_escape(query)` utility wraps every user-supplied FTS5 `MATCH` query, escaping `"`, `(`, `)`, `:`, `-`, `*`, and collapsing keywords (`AND`, `OR`, `NOT`, `NEAR`) so they cannot crash SQLite
- [ ] **RETR-02**: Every path that issues FTS5 `MATCH` queries routes through `fts5_escape()` — no raw concatenation anywhere
- [ ] **RETR-03**: 50+ input fuzz test asserts `fts5_escape()` never raises `sqlite3.OperationalError` on adversarial input (`c++`, `"unbalanced`, `*`, `(`, empty string, single char, etc.)
- [ ] **RETR-04**: Query classifier recognizes symbol-shaped queries (contains `.`, or matches `^[a-z_][a-z0-9_]*$`) and short-circuits to the `objects.inv`-backed symbol table BEFORE hitting FTS
- [ ] **RETR-05**: Query expansion applies the synonym table (space-separated term expansion) before building the FTS5 `MATCH` expression
- [ ] **RETR-06**: Ranker uses BM25 with column weights (heading > content_text; qualified_name > module)
- [ ] **RETR-07**: Search results include FTS5 `snippet()`-backed highlighted excerpts (~200 chars) on every hit
- [ ] **RETR-08**: `apply_budget(text, max_chars, start_index)` is the single function that enforces truncation + pagination for every content-returning tool; truncation is Unicode-safe (never splits a codepoint)
- [ ] **RETR-09**: Result hit shape is locked: `uri, title, kind, snippet, score, version, slug, anchor` — identical whether the hit came from symbol fast-path or FTS5

### Ingestion — Inventory (INGR-I)

`objects.inv` symbol ingestion. Lands in Phase 1 (pulled from Week 2 so retrieval code tests against real data).

- [ ] **INGR-I-01**: `build-index` CLI subcommand downloads `docs.python.org/{version}/objects.inv` via `sphobjinv` for each requested version
- [ ] **INGR-I-02**: Symbol rows populate `qualified_name`, `module`, `symbol_type` (role), `uri`, `anchor` from `sphobjinv.Inventory` objects
- [ ] **INGR-I-03**: Ingestion expands `$` URI shorthand from `objects.inv` into the full URL
- [ ] **INGR-I-04**: Ingestion falls back to `obj.name` when `obj.dispname == '-'`
- [ ] **INGR-I-05**: Duplicate `(qualified_name, role)` pairs within one version are handled via priority rules (class > function > method > attribute > data)
- [ ] **INGR-I-06**: Ingestion populates `symbols_fts` virtual table as part of the same transaction

### Ingestion — Content (INGR-C)

Sphinx JSON full-content ingestion. Lands in Phase 4 — highest upstream risk.

- [ ] **INGR-C-01**: `build-index` clones CPython source for the target version to a temp directory (pinned to a released tag, e.g. `v3.13.2`)
- [ ] **INGR-C-02**: `build-index` creates a dedicated venv per CPython version and installs the docs requirements (Sphinx pinned per CPython branch: 3.12 → `sphinx~=8.2.0`, 3.13 → `sphinx<9.0.0`)
- [ ] **INGR-C-03**: `build-index` invokes `./venv/bin/sphinx-build -b json Doc/ Doc/build/json/` directly (does NOT rely on `make json` — that target does not exist in CPython's Makefile)
- [ ] **INGR-C-04**: Ingestion parses each `.fjson` file, extracts `documents` (title, body, char_count) and `sections` (anchor, heading, level, ordinal, content_text, char_count)
- [ ] **INGR-C-05**: Ingestion converts section body HTML to markdown via a library (`markdownify` or `html2text`) before storing as `content_text` — NOT raw HTML
- [ ] **INGR-C-06**: Per-document ingestion failures are caught and logged; one broken `.fjson` file does not abort the whole build
- [ ] **INGR-C-07**: Ingestion extracts code blocks into `examples`, distinguishing doctests (`highlight-pycon`) from standalone examples (`highlight-python3`)
- [ ] **INGR-C-08**: Ingestion populates `sections_fts` and `examples_fts` in the same transaction as the canonical tables
- [ ] **INGR-C-09**: Ingestion populates `synonyms` table from `src/mcp_server_python_docs/data/synonyms.yaml` (100–200 curated entries)

### Publishing & Atomic Swap (PUBL)

Index artifact publishing with rollback. Lands in Phase 4.

- [ ] **PUBL-01**: `build-index` writes the new index to `~/.cache/mcp-python-docs/build-{timestamp}.db`, never directly to `index.db`
- [ ] **PUBL-02**: After build, SHA256 of the new DB is computed and recorded in `ingestion_runs.artifact_hash`
- [ ] **PUBL-03**: Smoke tests run against the new DB (basic queries return expected row counts) before the swap
- [ ] **PUBL-04**: On success, the new DB is atomically renamed to `index.db`; the previous DB is kept as `index.db.previous` for rollback
- [ ] **PUBL-05**: After the swap, `build-index` prints to stderr: "Index rebuilt. Restart your MCP client to pick up the new index." (v0.1.0 acknowledges the POSIX rename / open-FD limitation)
- [ ] **PUBL-06**: Ingestion-while-serving regression test verifies the server holding an RO handle is NOT crashed by a rebuild (WAL + RO+mode=ro) — stale reads are acceptable in v0.1.0, crashes are not
- [ ] **PUBL-07**: `validate-corpus` CLI subcommand runs the same smoke tests standalone against the current `index.db`

### Multi-Version (MVER)

v0.1.0 ships 3.12 + 3.13.

- [ ] **MVER-01**: `build-index --versions 3.12,3.13` builds BOTH versions into the same `index.db` in a single invocation
- [ ] **MVER-02**: When `version` is omitted from a tool call, server resolves to the latest stable (3.13 in v0.1.0) — the `doc_sets.is_default` row
- [ ] **MVER-03**: When `version` is specified explicitly and doesn't exist in the index, server returns `isError: true` with message "version X not found; available: [...]"
- [ ] **MVER-04**: `list_versions()` returns every row in `doc_sets` with `{version, language, label, is_default, built_at}`
- [ ] **MVER-05**: Cross-version URI collision test: same slug in 3.12 and 3.13 does not violate any `UNIQUE` constraint

### Packaging & Distribution (PKG)

- [ ] **PKG-01**: `pyproject.toml` declares `[project.scripts] mcp-server-python-docs = "mcp_server_python_docs.__main__:main"`
- [ ] **PKG-02**: Runtime deps pinned: `mcp>=1.27.0,<2.0.0`, `sphobjinv>=2.4,<3.0`, `pydantic>=2,<3`, `click>=8,<9`, `platformdirs>=4`, `pyyaml>=6`, plus markdown converter
- [ ] **PKG-03**: Package is installable via `uvx mcp-server-python-docs` and `pipx install mcp-server-python-docs`
- [ ] **PKG-04**: Built wheel contains `src/mcp_server_python_docs/data/synonyms.yaml` (verified via `unzip -l` check in CI)
- [ ] **PKG-05**: PyPI Trusted Publishing via GitHub Actions with attestations — no manual token upload
- [ ] **PKG-06**: `--version` flag on the CLI prints the installed version
- [ ] **PKG-07**: Package is published to PyPI under the name `mcp-server-python-docs` at version `0.1.0`

### CLI Surface (CLI)

- [ ] **CLI-01**: Click group `mcp-server-python-docs` exposes three subcommands: `serve` (default when no subcommand given), `build-index`, `validate-corpus`
- [ ] **CLI-02**: `doctor` subcommand inspects the environment (Python version, SQLite FTS5, cache dir, index presence, disk space) and prints a pass/fail report
- [ ] **CLI-03**: Running the CLI with no arguments defaults to `serve` via `@click.pass_context` + `ctx.invoke(serve)`

### Observability (OPS)

Minimal but structured. No metrics endpoint, no Prometheus.

- [ ] **OPS-01**: Every tool call logs to stderr: tool name, version, latency_ms, result_count, truncation flag, symbol-resolution path (exact/fuzzy/fts), synonym_expansion (yes/no)
- [ ] **OPS-02**: Logs are structured (JSON or equivalent key=value) to keep them greppable
- [ ] **OPS-03**: Observability is implemented as per-service-method decorators, NOT as FastMCP middleware (middleware surface is unstable in current SDK)
- [ ] **OPS-04**: LRU cache is used on hot reads: `get_section_cached(section_id)` (maxsize=512), `resolve_symbol_cached(qualified_name, version)` (maxsize=128)
- [ ] **OPS-05**: LRU cache is naturally scoped to process lifetime; no TTL, no invalidation (users restart on rebuild — see PUBL-05)

### Testing & Verification (TEST)

- [ ] **TEST-01**: ~20 stability tests assert structural properties of real results (`len(hits) >= 1`, `"asyncio" in hit.uri`), NOT exact content — so tests survive CPython doc revisions
- [ ] **TEST-02**: Unit tests cover `fts5_escape` (50-input fuzz), budget truncation (Unicode edge cases), synonym expansion, symbol classification
- [ ] **TEST-03**: Storage tests cover schema bootstrap idempotency, WAL mode, FTS5 availability check, repository queries against realistic fixtures
- [ ] **TEST-04**: Ingestion tests cover `objects.inv` parsing against a pinned fixture, section extraction from a small Sphinx JSON fixture, atomic swap (build-verify-rename-rollback)
- [ ] **TEST-05**: Stdio smoke test spawns the server as a subprocess, lists tools, issues one round-trip per tool, verifies zero stdout pollution
- [ ] **TEST-06**: All tests pass on macOS and Linux in CI (Windows is best-effort, not gate-blocking)

### Release & Integration (SHIP)

- [ ] **SHIP-01**: Manual integration test: configure Claude Desktop's `mcpServers` block with `uvx mcp-server-python-docs`, ask "what is asyncio.TaskGroup", verify a correct symbol hit is returned
- [ ] **SHIP-02**: Manual integration test: configure Cursor MCP settings, issue the same query, verify a correct response
- [ ] **SHIP-03**: README includes a copy-paste `mcpServers` config snippet for Claude Desktop (macOS, Linux, Windows paths)
- [ ] **SHIP-04**: README includes an install section (`uvx mcp-server-python-docs`), a first-run section (run `build-index --versions 3.12,3.13`), and a troubleshooting section covering: FTS5 unavailable, `uvx` cache stale, Claude Desktop MSIX on Windows, "restart after rebuild" requirement
- [ ] **SHIP-05**: Best-effort Windows support documented: "Tested on macOS and Linux; Windows should work (uses platformdirs + pathlib) but is not verified on every release"
- [ ] **SHIP-06**: v0.1.0 is tagged, published to PyPI, and the README install instructions are verified end-to-end

## v2 Requirements

Deferred to v1.1+. Tracked but not in v0.1.0 roadmap.

### Reload & UX

- **RELD-01**: `ReloadableConnection` pattern — server closes and reopens RO handles on SIGHUP so `build-index` no longer requires a client restart
- **RELD-02**: `build-index` sends SIGHUP to a running server process (via pidfile) after successful swap
- **RELD-03**: LRU cache is keyed by `(build_hash, key)` so stale entries are automatically invalidated when the underlying DB changes

### Extended Search

- **EXT-01**: `sqlite-vec` hybrid search with RRF fusion (activates when synonym table is insufficient based on usage data from v0.1.0)
- **EXT-02**: Intent router as a separate component (currently inlined into `search_docs(kind="auto")`)
- **EXT-03**: Result deduplication across page/section/symbol hits
- **EXT-04**: Progressive retrieval (return summary first, full content on request)

### Version Diff

- **VDIFF-01**: Ingestion extracts `versionadded` / `versionchanged` directives into a `changes` table
- **VDIFF-02**: `diff_versions` tool returns what changed between two Python versions for a given symbol or module

### Third-Party Docs

- **3RD-01**: Ingestion generalizes to any Sphinx-built docs given a URL (NumPy, Django, etc.)
- **3RD-02**: Multi-source support in the schema (the `source` column on `doc_sets` is already reserved)

### Internationalization

- **I18N-01**: Ingestion supports non-English CPython docs (French, Japanese, etc.)
- **I18N-02**: `language` parameter on tools for source selection

### Operations

- **OPS-RELD-01**: Differential ingestion — only reparse pages whose hash changed since last build
- **OPS-MET-01**: Metrics endpoint (if/when HTTP transport is added)

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| HTTP / SSE transport | Stdio is the local-server norm; HTTP brings auth, rate limiting, origin validation — all out-of-scope security surface |
| OAuth or any auth | Stdio + local-only, no surface to authenticate |
| Runtime internet fetch | Server only reads its local index — runtime is fully deterministic and sandboxable |
| Third-party library docs in v1 | v1 is Python stdlib only; generalizing is a v1.1+ candidate with a clear path |
| Write operations | Read-only simplifies trust, security, and cache invalidation |
| PDF / ePub / zip serving | This is a retrieval server, not a documentation hosting platform |
| Browser-facing UI | Clients are LLMs, not humans with browsers |
| Multi-tenant deployment | Single-machine, local install via `uvx` |
| Embeddings in v1 | Synonym table first; revisit with `sqlite-vec` if usage data shows it insufficient |
| HTML scraping ingestion path | Cut from v1 to reduce parser test surface; Sphinx JSON is the sole content path |
| PyPI-bundled index | PyPI 100 MB limit makes bundling impractical; build to `~/.cache/` on first run |
| Custom tool registry | FastMCP decorators handle schema generation; no drift risk |
| Golden tests | Structural stability tests instead; exact-content snapshots rot across CPython doc revisions |
| Resource templates (`docs://python/...`) | LLM clients reliably find tools, not resources, in 2026; URIs appear as identifier strings inside hits instead (Option A) |
| SIGHUP reload in v0.1.0 | Deferred to v1.1 — v0.1.0 documents "restart required" (see PUBL-05) |
| MCP prompts, elicitation, sampling, roots, tasks, MCP Apps | Out of scope for a read-only retrieval server; no user value in this domain |
| `ask_question` / synthesis tool | Out of scope — LLM clients synthesize answers themselves from retrieval evidence |
| MCP Fetch-style arbitrary URL tool | Anti-feature — we are NOT a fetch proxy |
| Verified Windows support in v0.1.0 | Best-effort via `platformdirs` + `pathlib`; no per-release Windows test matrix |

## Traceability

Empty initially — populated during roadmap creation.

**Coverage:**
- v1 requirements: 79 total
- Mapped to phases: 0 (to be filled by `gsd-roadmapper`)
- Unmapped: 79 ⚠️ (will resolve after roadmap)

---

*Requirements defined: 2026-04-15*
*Last updated: 2026-04-15 after initial definition*
