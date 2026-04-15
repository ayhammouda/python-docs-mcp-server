# Stack Research — `mcp-server-python-docs`

**Domain:** Local MCP retrieval server over Python stdlib documentation (stdio transport, Python package, uvx-distributed, SQLite+FTS5 backed)
**Researched:** 2026-04-15
**Confidence:** HIGH
**Scope note:** This is a **validation pass** on `python-docs-mcp-server-build-guide.md`. The guide locks the stack (Python + FastMCP + SQLite/FTS5 + sphobjinv + Sphinx JSON + uvx). Below, each locked component is checked against current 2026 ecosystem reality and marked **CONFIRMED** or **DRIFT ALERT**.

---

## Validation Verdict Summary

| Locked Decision | Verdict | Headline |
|---|---|---|
| Official `mcp` SDK w/ `FastMCP` | **CONFIRMED + CAVEAT** | `mcp` 1.27.0; `FastMCP` still public and stable; Anthropic's own ref servers ironically use the *low-level* `Server` API. No action needed — FastMCP remains the right ergonomic pick for new projects. |
| `sphobjinv` over `objects.inv` | **CONFIRMED** | v2.4 (released 2026-03-23). Py 3.13 supported since 2.3.1.2. `Inventory(url=...)` + `.objects` iteration is still the blessed API. |
| SQLite + FTS5 (`unicode61 porter`) | **CONFIRMED** | Still the right primitive. `sqlite-vec` remains **pre-v1 (v0.1.9)** with explicit "expect breaking changes" warning — **defer to v1.1 stands**. |
| `pysqlite3-binary` fallback | **CONFIRMED with pin advice** | v0.5.4.post2 (2025-12-03). Linux x86-64 wheels only — **macOS/ARM users have no fallback**, so the startup FTS5 check must surface a clear error. |
| `uvx` distribution | **CONFIRMED** | Still the canonical MCP server install pattern. PEP 723 is for single-file scripts, not packaged distributions — not a competitor. |
| Sphinx JSON builder | **CONFIRMED with two gotchas** | JSON builder is alive in Sphinx 8.x/9.x. **Gotcha 1:** CPython's `Doc/Makefile` has **no `json` target** — you must invoke `sphinx-build -b json` directly after priming the venv. **Gotcha 2:** CPython 3.13 pins `sphinx<9.0.0`; 3.12 pins `sphinx~=8.2.0`. Build against each version's pinned Sphinx, not bleeding edge. |
| Python 3.12 + 3.13 as v0.1.0 targets | **CONFIRMED** | Both `objects.inv` artifacts verified live at `docs.python.org/{3.12,3.13}/objects.inv`. 3.13 is in full bugfix (until Oct 2026); 3.12 is security-only (until Oct 2028) — but doc artifacts stay published. |

**Overall:** Zero "must redo" findings. Two real gotchas (CPython Makefile + Sphinx pinning) and one important nuance (pysqlite3-binary wheel coverage) the build guide would otherwise blow past.

---

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|---|---|---|---|
| Python | **3.12, 3.13** (runtime) | Server + build-index CLI runtime | Matches PROJECT.md scope; 3.13 is full-bugfix until Oct 2026, 3.12 is security-only until 2028. Both are safe to target for a package published in Apr 2026. |
| `mcp` | `>=1.27.0,<2.0.0` | Official Python MCP SDK — provides `FastMCP`, `stdio_server`, tool/resource/prompt registration | Current PyPI release (2026-04-02). `FastMCP` stays at `from mcp.server.fastmcp import FastMCP` — import path unchanged. **Pin the major version.** The upstream repo has adopted a `main` = v2-development / `v1.x` = maintenance split as of v1.25 (Dec 2025), so `<2.0.0` is defensive. |
| `sphobjinv` | `>=2.4,<3.0` | Parse CPython `objects.inv` into symbol rows | v2.4 (2026-03-23) is current; API unchanged since 2.3.x. `Inventory(url=...)` + iteration over `.objects` yielding `DataObjStr(name, domain, role, uri, dispname)` is stable. |
| SQLite FTS5 | Bundled with CPython 3.12/3.13 | BM25 retrieval over sections / symbols / examples; external-content FTS5 tables | Still the right primitive for a read-only, single-machine, sub-100MB index. `unicode61 porter` tokenizer (for prose) and `unicode61` (for identifiers) remain the idiomatic choices. |
| Sphinx (build-time only) | `>=8.2,<9.0` for 3.12; `>=8.2,<9.0` for 3.13 | Build `.fjson` files from CPython source during `build-index` | **Match what CPython itself pins.** See "Sphinx version pinning by CPython branch" below. Sphinx 9.1.0 (Dec 2025) exists but CPython rejects it via `sphinx<9.0.0`. |
| `uv` / `uvx` | Latest stable from astral-sh | Primary distribution mechanism — `uvx mcp-server-python-docs` | Unchanged as the MCP server install norm. Anthropic's own reference servers (mcp-server-git, mcp-server-time, mcp-server-fetch) all ship this way. |

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---|---|---|---|
| `pydantic` | `>=2.0.0,<3.0` | Tool input/output models; MCP SDK already requires it | Always — the guide references Pydantic models in §13 Package structure. Reuse the Pydantic v2 version MCP pins transitively. |
| `click` | `>=8.1.7,<9.0` | CLI subcommand dispatch (`serve`, `build-index`, `validate-corpus`) | Matches `mcp-server-git`'s pattern; avoids DIY `argparse` for 3 subcommands with overlapping flags. |
| `pysqlite3-binary` | `>=0.5.4.post2` | **OPTIONAL** fallback when system SQLite lacks FTS5 | **Linux x86-64 only** — no macOS/ARM wheels. Treat as opt-in via extras (`pip install mcp-server-python-docs[pysqlite3]`), and make the startup check error tell users what to do on non-Linux. |
| `PyYAML` | `>=6.0,<7.0` | Read `data/synonyms.yaml` curated concept-expansion table | Once, at build-index time, to populate the `synonyms` SQLite table. |

### Development Tools

| Tool | Purpose | Notes |
|---|---|---|
| `uv` | Dependency resolution, virtualenv management, build frontend | Use `uv sync` during dev; `uv build` to produce sdist/wheel for PyPI. |
| `hatchling` | Build backend declared in `pyproject.toml` `[build-system]` | Same choice as `mcp-server-time` and `mcp-server-git`; zero-config, fast. |
| `pytest` + `pytest-asyncio` | Unit / storage / ingestion / smoke tests | Stability tests (structural, not golden) per guide §14. |
| `ruff` | Lint + format | Matches Anthropic reference servers' convention. |
| `pyright` | Type checking | Matches Anthropic reference servers' convention. Protects the typed Pydantic tool schemas FastMCP generates. |
| `freezegun` | Freeze time in ingestion/atomic-swap tests | Useful for `build-{timestamp}.db` naming and `ingestion_runs.started_at` assertions. |

---

## Installation

```bash
# Project dev bootstrap (one-time)
uv sync

# Add a new dep
uv add 'mcp>=1.27.0,<2.0.0'
uv add 'sphobjinv>=2.4,<3.0'
uv add 'pydantic>=2.0.0,<3.0'
uv add 'click>=8.1.7,<9.0'
uv add 'PyYAML>=6.0,<7.0'

# Dev-only
uv add --dev pytest pytest-asyncio ruff pyright freezegun

# Build-time only (used by build-index CLI, not at serve-time)
# NOTE: Sphinx is NOT a runtime dep of the served package. It is installed
# into an isolated venv by the build-index CLI when ingesting CPython source,
# OR declared as an optional extra: mcp-server-python-docs[build]
uv add --optional build 'sphinx>=8.2,<9.0'

# Optional FTS5 fallback (Linux x86-64 only)
uv add --optional pysqlite3 'pysqlite3-binary>=0.5.4.post2'
```

### `pyproject.toml` entry point (verified pattern)

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "mcp-server-python-docs"
version = "0.1.0"
requires-python = ">=3.12"   # matches v0.1.0 scope — not 3.10
dependencies = [
    "mcp>=1.27.0,<2.0.0",
    "sphobjinv>=2.4,<3.0",
    "pydantic>=2.0.0,<3.0",
    "click>=8.1.7,<9.0",
    "PyYAML>=6.0,<7.0",
]

[project.optional-dependencies]
build = ["sphinx>=8.2,<9.0"]
pysqlite3 = ["pysqlite3-binary>=0.5.4.post2"]

[project.scripts]
mcp-server-python-docs = "mcp_server_python_docs.__main__:main"
```

This is the exact pattern `mcp-server-time` 0.6.2 uses (verified 2026-04-15). `uvx mcp-server-python-docs` invokes the `[project.scripts]` entry, which dispatches to `serve` (default), `build-index`, or `validate-corpus` via Click.

---

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|---|---|---|
| Official `mcp` SDK's `FastMCP` (`from mcp.server.fastmcp import FastMCP`) | Standalone `fastmcp` package (PrefectHQ, v3.2.4) | **Not for this project.** The standalone `fastmcp` (Jeremiah Lowin / PrefectHQ) is a separate v3 line now — richer feature set, "70% of MCP servers across all languages" claim, migration guides from `mcp.server.fastmcp`. **But the PROJECT.md decision explicitly pins "official `mcp` package with `FastMCP`"** and Anthropic's reference servers use `mcp`, so the ecosystem validator signal is clear: stay on `mcp`. Re-evaluate at v1.1 only if FastMCP in `mcp` lags features the guide needs. |
| `FastMCP` high-level API | Low-level `mcp.server.Server` + `mcp.server.stdio.stdio_server` | **Ironic note:** Anthropic's own reference servers (`mcp-server-git`, `mcp-server-time`, `mcp-server-fetch`) use the **low-level** `Server` class, not `FastMCP`. They give up decorator-based schema generation in exchange for handler control. **Recommendation: stick with `FastMCP`** — the guide's architectural simplicity gain (3 tools, typed Pydantic schemas, zero tool-registry boilerplate) is worth more than parity with reference servers for a 3-tool scope. If FastMCP later blocks a critical feature, the guide's 3-service layer makes dropping to the low-level API mechanical. |
| SQLite FTS5 BM25 + synonym table | `sqlite-vec` hybrid (BM25 + vector) | **Defer to v1.1 as the guide says.** Current `sqlite-vec` is **v0.1.9 (2026-03-31)** and the project README still states "pre-v1, so expect breaking changes." No stable v1 timeline announced. Pinning a pre-v1 C extension in a package distributed via `uvx` is a recipe for "works on my machine" issues. Schema already reserves room for it — no migration cost to add later. |
| System SQLite | `pysqlite3-binary` | Fallback only. `pysqlite3-binary` ships **Linux x86-64 wheels only**, so macOS and Linux ARM users cannot fall back this way. Keep the startup FTS5 capability check, but phrase the error message with platform-specific guidance. |
| Sphinx JSON builder (primary, no HTML fallback) | HTML scraping with `beautifulsoup4` | **Out of scope for v1 per PROJECT.md.** Not researched. Noted only to explain the absence — the build guide originally listed BS4 as a fallback; PROJECT.md cut it. |
| `uvx` | `pipx`, `pip install --user`, PEP 723 single-file scripts | `pipx install` is documented as a secondary path for persistent installs. **PEP 723** (accepted; embedded script metadata) is **not a competitor** — it targets single-file scripts that explicitly don't want a `pyproject.toml`. Our project is multi-file, PyPI-distributed, typed; pyproject.toml + `uvx` entry-point remains right. |

---

## What NOT to Use

| Avoid | Why | Use Instead |
|---|---|---|
| `Sphinx>=9.0.0` when building CPython 3.12 or 3.13 docs | Both `cpython/main` and `cpython/3.13` branches pin `sphinx<9.0.0` in `Doc/requirements.txt`. `cpython/3.12` pins `sphinx~=8.2.0`. Using Sphinx 9.x will produce build warnings or hard failures on CPython's configured extensions. | Pin Sphinx to match what the CPython branch you're ingesting pins. Per-version Sphinx version: 3.12 → `sphinx~=8.2.0`; 3.13 → `sphinx>=8.2,<9.0`. The `build-index` CLI should install Sphinx into an isolated venv based on the target version. |
| `make json` via CPython's `Doc/Makefile` | **There is no `json` target in `Doc/Makefile`.** Only html, htmlhelp, latex, text, texinfo, epub, changes, linkcheck, coverage, doctest, pydoc-topics, gettext. A naive `cd Doc && make json` will fail. | Use the Makefile's `venv` target to create `Doc/venv/` with the pinned Sphinx and deps, then invoke `sphinx-build -b json . build/json` directly: `./venv/bin/sphinx-build -b json . build/json`. Or call `Doc/venv/bin/python -m sphinx -b json ...`. |
| `mcp<1.23.0` | `main` became v2-development as of v1.25, `v1.x` is the maintenance branch. Older 1.x versions predate the SDK's current lifespan / tool-registration patterns. | Pin `>=1.27.0,<2.0.0`. v1.27.0 is the current head of the v1 line (released 2026-04-02). |
| Global `sys.stdout` usage anywhere on the serve-time import path | Stdout is the MCP protocol channel. Any `print()` corrupts JSON-RPC frames and disconnects the client. | Guide §9 protocol hygiene stands verbatim. Reinforce with a CI check: `ruff` rule or a pytest that spawns the server as a subprocess and asserts no non-JSON-RPC bytes reach stdout. |
| `sphinx_rtd_theme` or any HTML theme dep | Irrelevant for JSON output — pure bloat if added "just in case." | Install only `sphinx` + the extensions CPython's `Doc/conf.py` actually imports. Most are custom extensions under `Doc/Tools/extensions/` and come with the CPython source itself. |
| `sqlite-vec` in v0.1.0 | Pre-v1, explicit breaking-change warning, no macOS/ARM wheel guarantees on every release. | Synonym table per guide §6; revisit in v1.1 with usage data. |

---

## Stack Patterns by Variant

**If the user's system SQLite lacks FTS5 (common on some Linux distros, Alpine, etc.):**
- Detect at server startup via the `assert_fts5_available` probe from guide §9.
- On Linux x86-64: error message points user to `pip install 'mcp-server-python-docs[pysqlite3]'`.
- On macOS / Linux ARM / Windows: error message points user to install Python from python.org or `uv python install`, which ship with FTS5-enabled SQLite.
- **Do not** silently swap to `pysqlite3_binary` in code — the guide's "fail loudly at startup" rule is right.

**If the user wants to index a version beyond 3.12/3.13 (e.g., 3.11 or 3.14):**
- Out of scope for v0.1.0. The `doc_sets` table supports it schematically; the ingestion CLI just needs to accept the version string.
- **Note:** 3.14 is out (released 2025-10-07, 3.14.4 was released 2026-04-07) but PROJECT.md defers it to post-v0.1.0 because "3.14 still moving, broader is premature." That rationale was correct in the build guide's original writing and is still defensible — 3.14's `objects.inv` is available, but running CPython's doc build with a compatible Sphinx is an untested path here.

**If `sphinx-build -b json` throws on a new CPython patch release:**
- The old regression (issue #11615, Sphinx 7.2.0 breakage) is **closed**. No known current blockers on Sphinx 8.2.x building CPython 3.13 docs to JSON.
- But: multilingual builds have a known bug (sphinx issue #13448) where translation caches leak across languages. **Our English-only path is not affected.**
- Mitigation: `validate-corpus` CLI should spot-check a known section (e.g., `library/asyncio-task.html` → `asyncio.TaskGroup`) after every build and fail the atomic swap if missing.

---

## Version Compatibility Matrix

| Package | Version | Compatible With | Notes |
|---|---|---|---|
| `mcp` | 1.27.0 | Python 3.10–3.13 | We require ≥3.12; any 3.10/3.11 compat in `mcp` is irrelevant to us. |
| `sphobjinv` | 2.4 | Python 3.13 added in 2.3.1.2 | Stable iteration API (`Inventory.objects` → `DataObjStr`). |
| `Sphinx` | 8.2.x | Python ≥3.11 | Required for CPython 3.12 (pinned `~=8.2.0`) and 3.13 (pinned `<9.0.0`). |
| `Sphinx` | 9.1.0 | Python ≥3.12 | **Do not use** — CPython branches reject it. |
| `pysqlite3-binary` | 0.5.4.post2 | Python 3.8–3.14 | Binary wheels **Linux x86-64 only**. No source dist. |
| `sqlite-vec` | 0.1.9 | Python 3.x via `pip install sqlite-vec` | **Pre-v1, expect breakage** — do not ship in v0.1.0. |
| `pydantic` | 2.x | Transitive via `mcp` | MCP SDK requires Pydantic v2; reuse. |

---

## Per-Component Deep Dive

### 1. Official `mcp` SDK with `FastMCP` → **CONFIRMED + CAVEAT**

**Verified against:**
- PyPI: `mcp` 1.27.0 released 2026-04-02, requires Python ≥3.10, MIT, maintained by Anthropic (David Soria Parra).
- Context7 (`/modelcontextprotocol/python-sdk`): `from mcp.server.fastmcp import FastMCP` is still the canonical import; `@mcp.tool()` decorator, `@mcp.resource("...")`, `@mcp.prompt()` unchanged.
- Lifespan pattern confirmed: `@asynccontextmanager async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]` with `mcp = FastMCP("My App", lifespan=app_lifespan)`. **This is exactly the pattern our 3-service DI wiring needs.**
- Transport: `mcp.run()` with no args defaults to stdio; `mcp.run(transport="streamable-http", ...)` exists for future HTTP (out of scope).

**The one thing the guide doesn't mention but we should use:**
Use FastMCP's **lifespan parameter** to inject the `Storage` layer (read-only SQLite connection, WAL pragmas, FTS5 check) at server startup. This gives us a clean typed `AppContext` with `db: Database` that every tool handler can access via `ctx.request_context.lifespan_context`, instead of module-level globals or threading a connection through every call.

**Caveat — Anthropic's reference servers use the low-level API, not FastMCP:**
- `mcp-server-git` 0.6.2 → `from mcp.server import Server` + `from mcp.server.stdio import stdio_server`
- `mcp-server-time` 0.6.2 → same pattern
- `mcp-server-fetch` 0.6.3 → same pattern

This is a historical choice (these servers predate FastMCP's stability) and not a statement against FastMCP. **FastMCP remains the right choice for our 3-tool scope** — we gain automatic schema generation from Pydantic types, which is exactly what PROJECT.md's "No custom tool registry — FastMCP decorators handle schema generation; no drift risk, no wheel reinvention" decision is buying.

**Action:** None. Guide stands. Add lifespan-context-driven DI to the architecture research as a refinement.

### 2. `sphobjinv` → **CONFIRMED**

**Verified against:**
- PyPI: v2.4 released 2026-03-23. Actively maintained (20 releases total).
- GitHub: `bskinn/sphobjinv`. Python 3.13 support was added in v2.3.1.2.
- API: `Inventory(url="https://docs.python.org/3.13/objects.inv")` returns an inventory whose `.objects` is an iterable of `DataObjStr` with `.name`, `.domain`, `.role`, `.uri`, `.dispname`, `.priority`. Matches guide §8 Tier 1 snippet exactly.

**Pin:** `sphobjinv>=2.4,<3.0`. No API breakage between 2.3.x and 2.4; treat 2.4 as the floor so we get any bugfixes landed in 2026.

**Edge case the guide doesn't mention:** `Inventory(url=...)` does the HTTP fetch synchronously on construction. For `build-index --versions 3.12,3.13` this is fine, but put it behind `validate-corpus`'s network-check gate so offline builds fail with a helpful message.

### 3. SQLite + FTS5 (`unicode61 porter`) → **CONFIRMED**

**Verified against:**
- `sqlite-vec` v0.1.9 (2026-03-31) — still **pre-v1**, project README still says "expect breaking changes!"
- No v1 roadmap date announced as of research date.
- The guide's §6 "defer to v1.1" call remains correct. Schema leaves room for an `embedding` column on `sections` — no migration needed when v1.1 revisits.

**FTS5 tokenizer choice is still right:**
- `unicode61 porter` for `sections_fts` (prose — porter stemming helps "parse/parses/parsing" collapse)
- `unicode61` (no porter) for `symbols_fts` and `examples_fts` (identifiers — stemming would mangle `asyncio.TaskGroup` into `asyncio.taskgroup`)

**One thing the guide under-specifies:** External-content FTS5 tables require **manual sync triggers** or an explicit `INSERT INTO sections_fts(rowid, heading, content_text) SELECT id, heading, content_text FROM sections` after each batch insert during ingestion. Since ingestion is a one-shot build-to-temp-file operation with no runtime mutations, a single `INSERT ... SELECT` at end-of-ingestion is sufficient — no triggers needed. Flag this for the ingestion phase of the roadmap.

### 4. `pysqlite3-binary` fallback → **CONFIRMED with an important caveat**

**Verified against:** PyPI `pysqlite3-binary` v0.5.4.post2 (2025-12-03). Supports Python 3.8–3.14 declaratively, **but only ships Linux x86-64 wheels** — no source dist, no macOS wheels, no Linux ARM wheels.

**Implication for our startup FTS5 check:**
- On Linux x86-64 without FTS5 → "install `mcp-server-python-docs[pysqlite3]`" is a valid recovery path.
- On macOS / Linux ARM / Windows → `pysqlite3-binary` won't install. The error message must say so and point users to install a Python that bundles FTS5 (python.org builds, `uv python install`, or Homebrew Python all have FTS5 enabled).

**Guide update:** §9's `FTS5UnavailableError` message should be platform-aware. Two-sentence fix.

### 5. `uvx` distribution → **CONFIRMED**

**Verified against:**
- All three Anthropic-maintained Python reference servers (`mcp-server-git`, `mcp-server-time`, `mcp-server-fetch`) ship via `uvx` as the primary method.
- Claude Desktop config example `{"command": "uvx", "args": ["mcp-server-python-docs"]}` is the canonical pattern.
- `[project.scripts] mcp-server-python-docs = "mcp_server_python_docs.__main__:main"` is the idiomatic entry-point declaration (verified against `mcp-server-time`'s `pyproject.toml`).

**PEP 723 is not a competitor:** PEP 723 (Final, Jan 2024) enables single-file scripts with inline TOML metadata. It targets scripts that *don't* become pyproject.toml projects. Our package is multi-file, typed, PyPI-distributed — pyproject.toml remains right. No action.

**One secondary install path to document:** `pipx install mcp-server-python-docs` for users who want a persistent, version-pinned install. `uvx` runs a fresh environment every invocation; `pipx` caches. Both are in the reference-server READMEs.

### 6. Sphinx JSON builder → **CONFIRMED with two gotchas**

**Gotcha 1: CPython's `Doc/Makefile` has no `json` target.**
Verified against `cpython/3.13/Doc/Makefile` — available targets are `html, htmlhelp, latex, text, texinfo, epub, changes, linkcheck, coverage, doctest, pydoc-topics, gettext`. **No `json`.**

Workaround (this is how ingestion must actually work):
```bash
cd cpython/Doc
make venv                         # creates Doc/venv/ with pinned Sphinx + deps
./venv/bin/sphinx-build -b json . build/json
# OR:
./venv/bin/python -m sphinx -b json . build/json
```
The `venv` target installs exactly the Sphinx version CPython pins for that branch — which is critical for avoiding build warnings on custom extensions.

**Gotcha 2: CPython pins Sphinx per branch, and it matters.**
- `cpython/3.12/Doc/requirements.txt` → `sphinx~=8.2.0`
- `cpython/3.13/Doc/requirements.txt` → `sphinx<9.0.0`
- `cpython/main/Doc/requirements.txt` → `sphinx<9.0.0`

Sphinx 9.1.0 (released 2025-12-31) is explicitly locked out. Building CPython 3.12 docs with Sphinx 9.1.0 will likely fail on the 13+ custom extensions CPython ships in `Doc/Tools/extensions/` because Sphinx 9 moved some internal APIs.

**Historical regression we verified is fixed:** sphinx-doc/sphinx#11615 (JSON builder broke in Sphinx 7.2.0 with `'pathto'` handler error) is **closed**. We won't hit it at 8.2.x.

**Multilingual bug sphinx-doc/sphinx#13448:** Translation caching leaks across language builds in a single Python process. **Does not affect us** — we build one language (English) per invocation and the CLI spawns a fresh Python process per version anyway.

**Action for ingestion phase:** The `build-index` CLI must (a) clone the target CPython branch, (b) run its `make venv` to get the right Sphinx, (c) invoke `sphinx-build -b json` directly, (d) ingest `.fjson` files. Do not try to layer "install Sphinx globally and point it at any source tree" — that's how the custom-extension warnings bite you.

### 7. Python 3.12 + 3.13 as v0.1.0 targets → **CONFIRMED**

**Verified against:**
- `https://docs.python.org/3.12/objects.inv` → returns 200, 135.1 KB, zlib-compressed Sphinx inventory v2
- `https://docs.python.org/3.13/objects.inv` → returns 200, 142.5 KB, zlib-compressed Sphinx inventory v2
- Python 3.13.12 (full bugfix mode until Oct 2026) and 3.12.13 are current patch releases.
- Python 3.14 is out (3.14.4 released 2026-04-07) but **PROJECT.md correctly defers** until 3.14's doc build is proven with a stable Sphinx pin.

**Version support windows (for README):**
- 3.12: security-only until Oct 2028
- 3.13: full-bugfix until Oct 2026, then security-only until 2029

Both windows comfortably exceed any v0.1.0 release's lifespan. No action.

---

## Sources

### High confidence (Context7 + PyPI + official repos, dual-verified)

- `/modelcontextprotocol/python-sdk` (Context7) — FastMCP decorator API, lifespan context, stdio transport, mcp.run() signatures
- [PyPI: mcp 1.27.0](https://pypi.org/project/mcp/1.27.0/) — release date 2026-04-02, Python ≥3.10, Anthropic maintained
- [PyPI: mcp (index)](https://pypi.org/project/mcp/) — 1.27.0 current stable, FastMCP still recommended entry point
- [PyPI: sphobjinv](https://pypi.org/project/sphobjinv/) — v2.4 (2026-03-23), actively maintained
- [GitHub: bskinn/sphobjinv releases](https://github.com/bskinn/sphobjinv/releases) — v2.4 changelog confirms no breaking API changes from 2.3.x
- [PyPI: Sphinx 8.2.3](https://pypi.org/project/Sphinx/8.2.3/) — release 2025-03-02, requires Python ≥3.11, JSON builder still supported
- [PyPI: Sphinx (index)](https://pypi.org/project/Sphinx/) — 9.1.0 current but not usable for CPython 3.12/3.13
- [PyPI: pysqlite3-binary](https://pypi.org/project/pysqlite3-binary/) — v0.5.4.post2 (2025-12-03), Linux x86-64 wheels only
- [GitHub: asg017/sqlite-vec](https://github.com/asg017/sqlite-vec) — v0.1.9 (2026-03-31), pre-v1, "expect breaking changes!"
- [cpython/main/Doc/requirements.txt](https://github.com/python/cpython/blob/main/Doc/requirements.txt) — `sphinx<9.0.0` pinning confirmed
- [cpython/3.13/Doc/requirements.txt](https://github.com/python/cpython/blob/3.13/Doc/requirements.txt) — `sphinx<9.0.0`
- [cpython/3.12/Doc/requirements.txt](https://github.com/python/cpython/blob/3.12/Doc/requirements.txt) — `sphinx~=8.2.0`
- [cpython/main/Doc/Makefile](https://github.com/python/cpython/blob/main/Doc/Makefile) — no json target
- [cpython/3.13/Doc/Makefile](https://raw.githubusercontent.com/python/cpython/3.13/Doc/Makefile) — no json target
- [cpython/3.13/Doc/conf.py](https://raw.githubusercontent.com/python/cpython/3.13/Doc/conf.py) — custom extension list (audit_events, availability, c_annotations, etc.)
- [mcp-server-time pyproject.toml](https://raw.githubusercontent.com/modelcontextprotocol/servers/main/src/time/pyproject.toml) — canonical uvx entry-point pattern
- [mcp-server-git pyproject.toml](https://raw.githubusercontent.com/modelcontextprotocol/servers/main/src/git/pyproject.toml) — depends on `mcp`, NOT `fastmcp`
- [mcp-server-fetch pyproject.toml](https://raw.githubusercontent.com/modelcontextprotocol/servers/main/src/fetch/pyproject.toml) — depends on `mcp>=1.1.3`
- [mcp-server-time server.py](https://raw.githubusercontent.com/modelcontextprotocol/servers/main/src/time/src/mcp_server_time/server.py) — uses low-level `Server`, not FastMCP
- [mcp-server-git server.py](https://raw.githubusercontent.com/modelcontextprotocol/servers/main/src/git/src/mcp_server_git/server.py) — uses low-level `Server`, not FastMCP
- [docs.python.org/3.13/objects.inv](https://docs.python.org/3.13/objects.inv) — 200 OK, 142.5 KB, Sphinx inventory v2
- [docs.python.org/3.12/objects.inv](https://docs.python.org/3.12/objects.inv) — 200 OK, 135.1 KB, Sphinx inventory v2

### Medium confidence (WebSearch findings with corroboration)

- [sphinx-doc/sphinx#11615](https://github.com/sphinx-doc/sphinx/issues/11615) — closed (JSON builder regression fixed post-7.2.0)
- [sphinx-doc/sphinx#13448](https://github.com/sphinx-doc/sphinx/issues/13448) — open, multilingual-only, does not affect us
- [PEP 723](https://peps.python.org/pep-0723/) — Final (2024-01-08), single-file script metadata, not a pyproject.toml competitor
- [FastMCP standalone (PyPI)](https://pypi.org/project/fastmcp/) — v3.2.4 (2026-04-14), separate from `mcp.server.fastmcp`
- [GitHub: jlowin/fastmcp](https://github.com/jlowin/fastmcp) — standalone project; "powers 70% of MCP servers" claim
- [uv tools guide](https://docs.astral.sh/uv/guides/tools/) — `uvx` alias for `uv tool run`; still canonical in 2026
- [Python 3.13 release cycle](https://peps.python.org/pep-0745/) — bugfix until Oct 2026
- [endoflife.date/python](https://endoflife.date/python) — 3.12 security-only, 3.13 bugfix, 3.14 out

### Low confidence (single-source, flagged for validator)

- None. Every load-bearing claim above has at least two independent sources (Context7 + PyPI, or official repo + PyPI, or reference server + PyPI).

---

## Implications for Roadmap

Three phase-level flags come out of this research:

1. **Ingestion phase must budget time for the CPython Doc build**, not just parsing `.fjson` files. The actual `sphinx-build -b json` invocation requires cloning CPython source, running `make venv`, and invoking Sphinx directly (no Makefile target). Rough budget: add a half-day to the Week 2 "Sphinx JSON builder ingestion path" item for the build-orchestration wrapper.
2. **Startup-check phase must be cross-platform.** The `pysqlite3-binary` fallback only works on Linux x86-64. The `FTS5UnavailableError` message needs platform detection via `platform.system()` + `platform.machine()` and platform-specific recovery instructions.
3. **No stack drift blocks v0.1.0.** Every locked decision in `python-docs-mcp-server-build-guide.md` validated at current ecosystem state. The guide can be executed as written, with the gotchas above folded into the relevant phase plans.

---

*Stack research for: Python stdlib documentation MCP retrieval server*
*Researched: 2026-04-15*
*Validation pass against: `python-docs-mcp-server-build-guide.md`*
