# Project Research Summary

**Project:** mcp-server-python-docs
**Domain:** Local MCP stdio retrieval server over Python stdlib documentation (FastMCP, SQLite+FTS5, sphobjinv, uvx-distributed Python package)
**Researched:** 2026-04-15
**Confidence:** HIGH
**Research mode:** Validation pass against pre-existing `python-docs-mcp-server-build-guide.md` — not greenfield derivation. Every finding is framed as "the guide got this right / wrong / incomplete," because the guide is the authority the roadmap executes.

## Executive Summary

The build guide is **directionally correct and can be executed essentially as written**. All four locked technical decisions (FastMCP + SQLite/FTS5 + sphobjinv + Sphinx JSON + uvx distribution) validated against the April 2026 ecosystem — zero "must redo" findings at the stack level. The 3-tool market surface (`search_docs`, `get_docs`, `list_versions`) matches every comparable docs retrieval MCP in 2026 (Context7, DeepWiki, Microsoft Learn MCP, Ref.tools), and the 3-service / pure-retrieval-module split is the right factoring. The guide's 4-week plan shape is sound.

However, the research surfaced **two independently-identified blockers that the guide misses**, **four required MCP 2025-11-25 protocol additions** the guide predates, and **one architectural pattern upgrade** (FastMCP `lifespan` + typed `AppContext` DI) that is now idiomatic in mcp 1.27.0. None of these invalidate the guide's core; all must be folded into Phase-level plans.

The highest-risk cross-cutting finding — surfaced independently by both ARCHITECTURE and PITFALLS research — is that the guide's "atomic rename publishes the new index" statement is **false from a running server's perspective**: POSIX rename does not invalidate existing read-only file descriptors, so a server with an open handle continues reading the old inode forever. This must be addressed in the publishing phase, either by documenting "restart required" or by implementing close-and-reopen on SIGHUP.

**Overall posture for the roadmap:** lock scope confidently from the guide's MVP, layer the 4 MCP-spec additions in Phase 1, put the FTS5 tokenizer fix and `fts5_escape()` routing in Phase 2 (before any ingestion), pull ingestion forward to Week 1 Day 2 so retrieval is tested against real data, and treat the atomic-swap reload as a first-class design decision (not a documentation note) in the publishing phase. One human decision remains open (resource templates — Option A vs B) and must be resolved during roadmap creation or at the latest Phase 4.

## Key Findings

### Recommended Stack — CONFIRMED with two gotchas

Every locked technology validated:
- **`mcp` 1.27.0 with `FastMCP`** — pin `>=1.27.0,<2.0.0` (v2 is pre-alpha)
- **`sphobjinv` 2.4** — pin `>=2.4,<3.0`
- **SQLite + FTS5** — `sqlite-vec` still pre-v1, defer to v1.1 as guide says
- **`uvx` distribution** — canonical MCP install pattern
- **Python 3.12 + 3.13** — both `objects.inv` verified live

**Two gotchas the guide misses:**
1. **CPython's `Doc/Makefile` has NO `json` target.** Must run `make venv` then invoke `./venv/bin/sphinx-build -b json . build/json` directly.
2. **Sphinx must be pinned per CPython branch**: 3.12 → `sphinx~=8.2.0`; 3.13 → `sphinx<9.0.0`. Sphinx 9.x is explicitly locked out upstream.

**One nuance:** `pysqlite3-binary` ships Linux x86-64 wheels ONLY — no macOS/ARM/Windows. `FTS5UnavailableError` message must be platform-aware.

### Expected Features — CONFIRMED, needs 6 MCP-spec additions

Market position: 3-tool surface is the norm (Context7: 2, DeepWiki: 3, MS Learn: 3, Ref.tools: 2). Guide's tool surface is validated.

**Must add — MCP 2025-11-25 features the guide predates (all additive, all low-cost):**
1. `structuredContent` + `outputSchema` on every result (~0 LOC via FastMCP + Pydantic return types)
2. Tool annotations: `readOnlyHint=True, destructiveHint=False, openWorldHint=False` (~3 LOC per tool)
3. `isError: true` mapping for LLM-recoverable errors (version-not-found, slug-not-found); keep protocol errors for startup failures
4. Markdown body in `get_docs.content` (convert Sphinx JSON body HTML to markdown at ingest; NOT raw HTML)
5. `_meta["anthropic/maxResultSizeChars"]: 16000` on `get_docs` (Claude Code vendor extension)
6. FTS5 `snippet()`-backed ~200-char highlighted excerpts on every `search_docs` hit

**Should NOT ship:** prompts, elicitation, sampling, roots, tasks primitive, MCP Apps UI, `ask_question`, fetch_url. Also: omit `nextCursor` on `tools/list` (Claude Code bug #24785).

### Architecture — 3-service split CONFIRMED, 3 refinements required

1. **Use FastMCP `lifespan` + typed `AppContext` dataclass as DI root** (replaces guide's implicit wiring with module globals). Add `app_context.py` module.
2. **Add `storage/reload.py`** for the atomic-swap reader-handle problem (OR document "restart required").
3. **Pull ingestion into Week 1 Day 2** so retrieval tests use real data, not fictional fixtures. Saves ~2 days of debugging.

**Dependency rules:** `__main__` → `server` → `services` → `retrieval` → `storage`. `ingestion` imports `storage` only, never `server`. `storage` never imports `mcp.*`.

### Critical Pitfalls — 20 identified, 7 blockers

1. **FTS5 `unicode61 porter` shreds Python identifiers (BLOCKER, Phase 2).** `asyncio.TaskGroup` indexes as `{asyncio, taskgroup}` with case stemmed away. **Fix:** `tokenize = "unicode61 remove_diacritics 2 tokenchars '._'"` and drop `porter`. Must be set before any ingestion.
2. **FTS5 query injection from user input (BLOCKER, Phase 3).** Unbalanced quote, `c++`, `-1` → `sqlite3.OperationalError`. Ship `fts5_escape()` utility; route 100% of user input; 50-input fuzz test.
3. **Stdio stdout pollution from non-obvious sources (BLOCKER, Phase 1).** Native C-extension writes to fd 1, `warnings` misconfig, `atexit`, background threads, `PYTHONASYNCIODEBUG=1`, Windows CP1252 BOMs. **Fix:** `os.dup2()` to remap fd 1 → fd 2 at process start, hand saved fd to MCP framer. Add subprocess stdout-sentinel test.
4. **Stale RO SQLite handle after atomic rename (BLOCKER for correctness, Phase 4).** POSIX rename does not invalidate open fds. Server reads old inode forever. `lru_cache` caches zombie results. **Fix:** document "restart required" (simplest) OR ship `ReloadableConnection` + SIGHUP.
5. **`sphinx-build -b json` against CPython is not "just run it" (BLOCKER, Phase 4).** Custom extensions, pending_xref leakage, `NotImplementedError: Unknown node: versionadded`. **Fix:** dedicated venv per CPython version, pin CPython to released tag, catch per-document errors, pre-validate.

**Other majors:** sphobjinv duplicate qualified names + `$` URI shorthand (#6), FastMCP lifespan runs per-process-spawn (#8), Pydantic schema generation on non-BaseModel types (#9), `data/synonyms.yaml` missing from wheel (#10), multi-version URI collisions (#11), Windows path handling (#15), lifespan errors swallowed (#14).

## Cross-Cutting Findings

### CONFIRMED (guide stands as written)

3-tool surface, FastMCP + `mcp` 1.27.0, `sphobjinv`, SQLite+FTS5, `uvx`, Python 3.12+3.13, synonym table, RO/RW connection split, XDG cache location, stability tests, Click group with 3 subcommands, 3-service split, Sphinx JSON sole content path, atomic index publishing with rollback (modulo reload), stderr logging, FTS5 capability check, 4-week plan shape, Claude Desktop + Cursor integration test as ship gate.

### DRIFT / ADDITIONS (22 items the guide needs folded in)

| # | Change | Phase |
|---|---|---|
| D1 | FTS5 tokenizer: `tokenchars '._'`, drop porter | Phase 2 |
| D2 | `FastMCP(lifespan=app_lifespan)` + typed `AppContext` | Phase 1 |
| D3 | Tool annotations on every tool | Phase 1 |
| D4 | `structuredContent` + `outputSchema` verified in tests | Phase 1 |
| D5 | Error → `isError: true` mapping | Phase 3 |
| D6 | Markdown body in `get_docs.content` (ingest-time conversion) | Phase 4 |
| D7 | `_meta["anthropic/maxResultSizeChars"]: 16000` | Phase 1 |
| D8 | FTS5 `snippet()` extracts on hits | Phase 3 |
| D9 | Schema `symbols`: `UNIQUE(doc_set_id, qualified_name, symbol_type)` + priority + `$` expansion | Phase 2+4 |
| D10 | Schema `sections`: drop `UNIQUE(uri)`, keep `UNIQUE(document_id, anchor)` | Phase 2 |
| D11 | `platformdirs.user_cache_dir("mcp-python-docs")` | Phase 2 |
| D12 | `synonyms.yaml` inside package + `importlib.resources` | Phase 1 |
| D13 | Ingestion moves to Week 1 Day 2 | Phase 1-2 |
| D14 | Platform-aware `FTS5UnavailableError` message | Phase 1 |
| D15 | Dedicated venv per CPython version; `sphinx-build -b json` direct | Phase 4 |
| D16 | Per-document failure isolation in ingestion | Phase 4 |
| D17 | `--version` flag + `doctor` subcommand | Phase 7 |
| D18 | README troubleshooting: FTS5, uvx cache, Windows MSIX, restart requirement | Phase 7 |
| D19 | SIGPIPE handler + BrokenPipeError-safe shutdown | Phase 1 |
| D20 | `os.dup2()`-based fd 1 redirection | Phase 1 |
| D21 | Lifespan `try/except` + `SystemExit(1)` + last-error.log | Phase 1 |
| D22 | Omit `nextCursor` on `tools/list` | Phase 1 |

### BLOCKERS (7 — must be addressed in specific phase)

| # | Blocker | Phase | If missed |
|---|---|---|---|
| B1 | FTS5 tokenizer fix (D1) | Phase 2 | Identifier search silently broken; requires full rebuild |
| B2 | `fts5_escape()` utility on 100% of user input | Phase 3 | Server crashes on unbalanced quote, `c++`, etc. |
| B3 | `os.dup2()` fd 1 redirection + sentinel test | Phase 1 | Silent client disconnects from native stdout writes |
| B4 | CPython Sphinx JSON build with pinned venv + per-doc failure handling | Phase 4 | Entire content ingestion blocked |
| B5 | Reader-handle stale after rename — document or reload | Phase 4 | Users rebuild, see stale results forever, no error |
| B6 | Pydantic schema snapshot test; all tool returns are `BaseModel` | Phase 1 | Tool silently fails to register |
| B7 | `synonyms.yaml` inside package + wheel content check in CI | Phase 6 | `uvx`-installed wheel fails with `FileNotFoundError` |

### OPEN DECISIONS (needs human input)

| # | Decision | Options | Recommendation | Resolve by |
|---|---|---|---|---|
| O1 | Resource templates `docs://python/{v}/...` | A: tools only, URIs as hit IDs / B: register as MCP resources | **Option A** (LOW confidence — 2026 roadmap could invert) | Roadmap / Phase 4 |
| O2 | Atomic-swap reload protocol | A: document restart / B: `ReloadableConnection` + SIGHUP | **Option A for v0.1.0** | Phase 4 |
| O3 | Default for `_meta["anthropic/maxResultSizeChars"]` | 8K/16K/25K | 16000 (empirical — measure) | Phase 4 or 8 |
| O4 | Pre-compile `synonyms.yaml` to Python literal? | pre-compile / runtime load | **Pre-compile** to cut lifespan cost | Phase 2-3 |

## Implications for Roadmap

Suggested 8 phases:

### Phase 1: Foundation + stdio hygiene + vertical symbol slice

**Rationale:** stdio hygiene + lifespan DI wiring most time-sensitive. Pulling `sphobjinv` ingestion here means DI pattern validated against real data.

**Delivers:** Package skeleton, Click group with 3 subcommands, `storage/db.py` with two-factory split + FTS5 check, `ingestion/inventory.py` (sphobjinv → symbols for 3.13), `AppContext` + `app_lifespan`, stub `search_docs`, then real symbol fast-path. Claude Desktop smoke test.

**Addresses:** D2, D3, D4, D7, D12, D14, D19-22
**Avoids pitfalls:** #7, #8, #9, #13, #14, #18, #20
**Blockers resolved:** B3, B6

### Phase 2: Schema + Storage (with FTS5 tokenizer fix)

**Rationale:** Schema must be correct BEFORE first ingestion (tokenizer changes require full rebuild).

**Delivers:** `schema.sql` with corrected tokenizer + composite symbol uniqueness + no `sections.uri UNIQUE`, repository classes, `platformdirs`, schema bootstrap test, FTS5 tokenizer regression test.

**Addresses:** D1, D9, D10, D11
**Avoids pitfalls:** #1 (BLOCKER), #6, #11, #15
**Blockers resolved:** B1

### Phase 3: Retrieval layer — synonym expansion, query escaping, ranking

**Rationale:** Retrieval is pure logic with highest test surface; isolate it now.

**Delivers:** `retrieval/query.py` (classify, expand, `fts5_escape`, build_fts_match), `retrieval/ranker.py` (BM25 + snippet), `retrieval/budget.py` (Unicode-safe truncation + pagination), 50-input fuzz suite.

**Addresses:** D5, D8
**Avoids pitfalls:** #2 (BLOCKER), #12, #19
**Blockers resolved:** B2

### Phase 4: Full content ingestion + atomic-swap publishing

**Rationale:** Where Sphinx JSON reality meets CPython customizations; atomic-swap reload decision lands here.

**Delivers:** `ingestion/sphinx_json.py`, dedicated venv-per-CPython-version build wrapper (direct `sphinx-build -b json`, NOT `make json`), per-document failure isolation, HTML→markdown conversion, `publish.py` atomic swap + SHA256 verify + `.previous` rollback, reload protocol decision (O2), ingestion-while-serving regression test.

**Addresses:** D6, D9, D15, D16
**Avoids pitfalls:** #3, #5 (BLOCKER), #6
**Blockers resolved:** B4, B5

**Research flag:** Deeper research likely needed before start. Re-verify: (a) Sphinx pins in `cpython/3.12|3.13/Doc/requirements.txt` haven't moved; (b) no new open Sphinx issues blocking JSON builder; (c) `pyspecific` custom nodes still serialize without `NotImplementedError`; (d) real ingestion time via dry run.

### Phase 5: Services + tool polish + caching

**Rationale:** Services are thin orchestrators; this phase wires `ContentService`, `VersionService`, full `SearchService`, LRU caching, error-to-`isError` adapter.

**Delivers:** `services/content.py`, `services/version.py`, full `services/search.py`, LRU cache with build-hash keying (or documented restart requirement), tool-level error adapter, structured logging decorator.

**Avoids pitfalls:** #4, #12, #13

### Phase 6: Multi-version + packaging correctness

**Rationale:** Multi-version is hardest correctness test. Packaging must be verified via built-wheel smoke test.

**Delivers:** `build-index --versions 3.12,3.13`, default version resolution (latest LTS = 3.13), cross-version probe in stability tests, `src/mcp_server_python_docs/data/synonyms.yaml` + `importlib.resources`, wheel content check in CI.

**Avoids pitfalls:** #10 (BLOCKER), #11
**Blockers resolved:** B7

### Phase 7: Observability, stability tests, release polish

**Delivers:** ~20 structural stability tests, `--version`, `doctor` subcommand, README with OS-tabbed config snippets + troubleshooting, PyPI Trusted Publishing via GitHub Actions with attestations.

**Addresses:** D17, D18
**Avoids pitfalls:** #16, #17, #20

### Phase 8: Ship — integration test + PyPI publish

**Delivers:** Manual integration test against Claude Desktop + Cursor, PyPI publish, tag v0.1.0.

### Phase Ordering Rationale

- **Phase 1 before 2:** stdio hygiene is more time-sensitive than schema; if server can't talk to Claude Desktop nothing else matters.
- **Phase 2 before 3:** tokenizer fix blocks ingestion (Phase 4); retrieval needs a schema.
- **Phase 3 before 4:** write retrieval against tiny Phase-1 symbol corpus first; validates the layer before Sphinx JSON complexity.
- **Phase 4 as its own phase:** hides most uncertainty; separable for `/gsd-research-phase` if upstream has drifted.
- **Phase 5 before 6:** can't test multi-version until `get_docs(anchor=...)` + full service layer exist.
- **Phase 7 before 8:** stability tests + observability must be in place so bug reports from integration testing are actionable.

### Research Flags

**Needs research:** Phase 4 (CPython Sphinx JSON build compatibility is the most fragile upstream dependency).

**Standard patterns (skip research):** Phase 1 (FastMCP lifespan in official README), Phase 2 (SQL + FTS5 + platformdirs mature), Phase 3 (pure Python, FTS5 docs), Phase 5-8 (standard patterns).

## Confidence Assessment

| Area | Confidence | Notes |
|---|---|---|
| Stack | **HIGH** | Every decision dual-verified (Context7 + PyPI + official repos) as of 2026-04-15. Zero "must redo." |
| Features | **HIGH** | MCP spec 2025-11-25 + 4 comparable servers + FastMCP docs cross-referenced. One LOW-confidence flag on Option A/B, routed to human. |
| Architecture | **HIGH** | FastMCP lifespan pattern authoritative in official SDK README. POSIX rename + SQLite WAL gotchas from SQLite's own forum + WAL docs. |
| Pitfalls | **HIGH** | 7 blockers and 8 majors all backed by concrete GitHub issues, official docs. Some UX pitfalls (MSIX, uvx cache) MEDIUM from community reports. |

**Overall:** HIGH. Guide can be executed with 22 drift/addition items folded in. No phase is research-blocked except Phase 4 (Sphinx JSON upstream drift).

### Gaps to Address

1. Empirical value for `_meta["anthropic/maxResultSizeChars"]` — measure in integration testing (O3).
2. Resource template decision (O1) — LOW confidence; human input required.
3. Atomic-swap reload decision (O2) — recommend document-restart for v0.1.0.
4. Synonym expansion tuning — starting list is a guess; iterate in v0.1.1+ based on eval data.
5. Real CPython Sphinx JSON build time + memory footprint — needs dry-run measurement before Phase 4.
6. SEP #799 (tool-result pagination) — watch item; adopt if it lands before ship.
7. **Windows support bar unclear** — architecture docs assume Windows first-class; PROJECT.md doesn't commit. Decide during roadmap.

## Sources

### Primary (HIGH confidence)

- `/modelcontextprotocol/python-sdk` (Context7)
- [mcp 1.27.0 on PyPI](https://pypi.org/project/mcp/1.27.0/)
- [MCP Specification 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25)
- [Tool Annotations — MCP Blog 2026-03-16](https://blog.modelcontextprotocol.io/posts/2026-03-16-tool-annotations/)
- [SQLite FTS5 Extension](https://www.sqlite.org/fts5.html)
- [SQLite WAL](https://www.sqlite.org/wal.html)
- [SQLite Forum: Renaming an open database](https://sqlite.org/forum/forumpost/3e62dce4e8)
- [sphobjinv 2.4 on PyPI](https://pypi.org/project/sphobjinv/)
- [sphobjinv syntax docs](https://sphobjinv.readthedocs.io/en/stable/syntax.html)
- [cpython/3.12/Doc/requirements.txt](https://github.com/python/cpython/blob/3.12/Doc/requirements.txt)
- [cpython/3.13/Doc/requirements.txt](https://github.com/python/cpython/blob/3.13/Doc/requirements.txt)
- [cpython/3.13/Doc/Makefile](https://raw.githubusercontent.com/python/cpython/3.13/Doc/Makefile) (confirmed no json target)
- [mcp-server-git/time/fetch reference sources](https://github.com/modelcontextprotocol/servers/tree/main/src)
- [docs.python.org/3.12/objects.inv](https://docs.python.org/3.12/objects.inv) and [3.13/objects.inv](https://docs.python.org/3.13/objects.inv)
- [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/)

### Secondary (MEDIUM confidence)

- [Context7 (upstash/context7)](https://github.com/upstash/context7) (2 tools)
- [DeepWiki MCP server](https://cognition.ai/blog/deepwiki-mcp-server) (3 tools)
- [Microsoft Learn MCP](https://github.com/MicrosoftDocs/mcp) (3 tools)
- [Ref.tools MCP](https://github.com/ref-tools/ref-tools-mcp) (2 tools)
- [FastMCP via Context7 `/prefecthq/fastmcp`](https://gofastmcp.com/)
- [sphinx-doc/sphinx#11615](https://github.com/sphinx-doc/sphinx/issues/11615) (closed)
- [jlowin/fastmcp#1115](https://github.com/jlowin/fastmcp/issues/1115)
- [modelcontextprotocol/python-sdk#1131](https://github.com/modelcontextprotocol/python-sdk/issues/1131)
- [astral-sh/uv#16196](https://github.com/astral-sh/uv/issues/16196)
- [anthropics/claude-code#24785](https://github.com/anthropics/claude-code/issues/24785)
- [SEP #799 tool-result pagination](https://github.com/modelcontextprotocol/modelcontextprotocol/discussions/799) (still open)
- [platformdirs on PyPI](https://pypi.org/project/platformdirs/)

### Tertiary (LOW confidence, flagged for validation)

- `_meta["anthropic/maxResultSizeChars"]` default (recommended 16000, empirical)
- Option A vs B for resource templates (LOW confidence market reading)
- Actual CPython Sphinx JSON build time / memory footprint (needs dry-run)

---

*Research completed: 2026-04-15*
*Research mode: validation against pre-existing `python-docs-mcp-server-build-guide.md`*
*Ready for roadmap: yes — 22 drift/addition items, 7 blockers mapped to phases, 4 open decisions routed to human*
