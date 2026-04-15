# mcp-server-python-docs

## What This Is

A read-only, version-aware, token-efficient MCP retrieval server over the Python standard library documentation. It runs over stdio, is backed by a locally-built SQLite + FTS5 index, and is distributed as a Python package installable via `uvx`. Its clients are LLMs (Claude, Cursor) — not browsers — so the optimization target is high-signal evidence per token spent.

## Core Value

LLMs can answer Python stdlib questions with precise, section-level evidence instead of flooding their context with full doc pages — closing a specific gap that general-purpose doc MCPs (Context7, DeepWiki) do not cover well for the Python stdlib.

## Requirements

### Validated

<!-- Shipped and confirmed valuable. -->

(None yet — ship to validate)

### Active

<!-- Current scope. Building toward these. -->

- [ ] Ship a FastMCP-based stdio server exposing exactly 3 tools: `search_docs`, `get_docs`, `list_versions`
- [ ] Build a local SQLite + FTS5 index from CPython `objects.inv` + Sphinx JSON output
- [ ] Support Python 3.12 and 3.13 in the v0.1.0 index
- [ ] Implement symbol fast-path (objects.inv lookup before FTS) for identifier-shaped queries
- [ ] Implement section windowing: when `anchor` is provided, return the section only
- [ ] Implement budget enforcement (`max_chars` + `start_index` pagination) on every content tool
- [ ] Ship a curated synonym/concept expansion table (~100–200 entries) for concept-search mitigation
- [ ] Implement atomic-swap index publishing with rollback (`build-{timestamp}.db` → `index.db`)
- [ ] Enforce MCP stdio protocol hygiene: stderr-only logging, FTS5 capability check at startup, WAL mode, RO/RW connection split
- [ ] Expose three CLI subcommands: `serve` (default), `build-index`, `validate-corpus`
- [ ] Publish to PyPI as `mcp-server-python-docs`, installable via `uvx`
- [ ] Ship README with a copy-paste `mcpServers` config snippet for Claude Desktop
- [ ] Manual integration test against Claude Desktop AND Cursor before tagging v0.1.0
- [ ] Stability test harness (~20 structural tests that survive CPython doc revisions)

### Out of Scope

<!-- Explicit boundaries. Includes reasoning to prevent re-adding. -->

- HTTP / SSE transport — stdio is the local-server norm; HTTP brings auth, rate limiting, and origin-validation concerns that are out of v1 scope
- OAuth or any auth — stdio + local-only, no surface to authenticate
- Arbitrary internet fetch at runtime — server only reads its local index, so runtime is fully deterministic and sandboxable
- Third-party documentation (NumPy, Django, etc.) — v1 is Python stdlib only; generalizing to any Sphinx-built site is a v1.1+ candidate
- Write operations — read-only simplifies trust, security, and cache invalidation
- PDF / ePub / zip artifact serving — this is a retrieval server, not a documentation hosting platform
- Browser-facing UI — clients are LLMs, not humans with browsers
- Multi-tenant or multi-user deployment — single-machine local install via `uvx`
- Embeddings / semantic search in v1 — synonym table first; revisit with `sqlite-vec` if usage data shows it's insufficient
- Version-diff capability (`versionadded`/`versionchanged` surfaced as a tool) — schema captures the raw data, but exposing a diff tool waits for v1.1
- Internationalization — `doc_sets.language` column reserved for future i18n without migration, but ingestion stays English-only
- Differential / incremental ingestion — full per-version rebuild is fast enough; incremental ingestion is v1.1+
- HTML-scraping fallback ingestion path — cut from v1 to reduce parser test surface; Sphinx JSON is the sole content path
- PyPI-bundled index — PyPI 100 MB limit makes bundling impractical; build to `~/.cache/mcp-python-docs/` on first run
- Custom tool registry — FastMCP decorators handle schema generation; no drift risk, no wheel reinvention
- Golden tests — structural stability tests instead; exact-content snapshots rot across CPython doc revisions

## Context

- **Primary audience:** OSS users installing via `uvx mcp-server-python-docs`. README UX matters; first-run error messages matter.
- **Problem this addresses:** Context7 covers third-party libraries broadly but the Python stdlib specifically is underserved. Existing docs MCPs either lack version awareness, token efficiency, or both. This project closes that specific gap.
- **Design already done:** Full architectural blueprint exists at `python-docs-mcp-server-build-guide.md`. Technical decisions are locked (see Key Decisions). Workflow phases should execute the guide, not rediscover its choices.
- **Target LLM clients:** Claude Desktop and Cursor are the integration-test bar for v0.1.0.
- **Documentation source:** CPython repository — download source for the target version, run `sphinx-build -b json Doc/ build/json/`, parse the resulting `.fjson` files.
- **Symbol source:** `objects.inv` parsed via `sphobjinv` gives ~13K–16K symbols per Python version for free.
- **Index location:** `~/.cache/mcp-python-docs/` (XDG-compliant).
- **Timeline shape:** The guide's 4-week plan is a rough shape, not a hard deadline — quality bars (integration testing, stability tests, README polish) take priority over ship date.

## Constraints

- **Tech stack**: Python, official `mcp` package with `FastMCP`, SQLite + FTS5, `sphobjinv` — All four were chosen deliberately in the build guide; substitutions would invalidate the locked architecture.
- **Transport**: stdio only — Standard for local MCP servers; HTTP would force a much larger security and ops surface.
- **Storage**: SQLite with FTS5 compiled in — Some distro SQLite builds ship without FTS5; server must check at startup and fail loudly. Document `pysqlite3-binary` as a fallback dep.
- **Stdio protocol hygiene**: No `print()` to stdout from any code path — Stdout is reserved for MCP protocol messages. All logging goes to stderr. Any third-party library that might print to stdout must be neutralized before MCP starts speaking.
- **Package size**: Must fit under PyPI 100 MB limit — Forces `~/.cache/` first-run build; index cannot be bundled.
- **Read-only serving**: Server never writes to the index at runtime — Ingestion uses a separate writable handle; serving uses a read-only handle (`sqlite3.connect(path, uri=True)` with `?mode=ro`).
- **Distribution**: Must be runnable via `uvx mcp-server-python-docs` — This is the norm for MCP servers; `pyproject.toml` entry point is required.
- **Scope discipline**: Every v1.1 candidate stays in v1.1 — No scope creep from the "explicit non-goals" list in the build guide unless an explicit decision moves it back into Active.

## Key Decisions

<!-- Decisions that constrain future work. Add throughout project lifecycle. -->

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Language: Python | Domain alignment, mature MCP SDK, `sphobjinv` ecosystem, `uvx` norm | — Pending |
| MCP SDK: official `mcp` with `FastMCP` | Decorator registration, automatic schema from type hints, transport swappability | — Pending |
| Transport: stdio only | Local-server norm; HTTP brings out-of-scope security surface | — Pending |
| Storage: SQLite + FTS5 (canonical tables, FTS as retrieval aid) | Portable, embedded, zero-ops, sufficient for read-only workload | — Pending |
| Search: BM25 (FTS5) + curated synonym expansion table | Cheap concept-search mitigation without an embeddings dependency | — Pending |
| Symbol source: `sphobjinv` over CPython `objects.inv` | 13K–16K symbols per version for free, battle-tested | — Pending |
| Content path: Sphinx JSON only (no HTML fallback in v1) | Halves parser test surface; HTML fallback is a v1.1 if JSON breaks | — Pending |
| Versions in v0.1.0: 3.12 + 3.13 | Proves version-switching is real; 3.14 still moving, broader is premature | — Pending |
| Tool surface: 3 tools (`search_docs`, `get_docs`, `list_versions`) | Matches market norm (Context7: 2, DeepWiki: 3); reduces LLM tool-selection load | — Pending |
| 3 services, 3-stage token pipeline | Cuts premature factoring; extra services and pipeline stages need usage data to tune | — Pending |
| Index location: `~/.cache/mcp-python-docs/` (XDG) | PyPI 100 MB limit forces first-run build | — Pending |
| Atomic-swap publishing with rollback | Reliability hygiene — ingestion never disrupts a running server | — Pending |
| Stability tests over golden tests | Structural asserts survive CPython doc revisions; exact-content snapshots don't | — Pending |
| Ship gate: PyPI + Claude Desktop + Cursor + README + eval harness | v0.1.0 must be installable and verified-working in both target clients | — Pending |
| Timeline: ~4 weeks rough shape | Milestones are quality bars, not deadlines | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-04-15 after initialization*
