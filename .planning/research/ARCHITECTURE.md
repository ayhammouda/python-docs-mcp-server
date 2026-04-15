# Architecture Research

**Domain:** MCP (Model Context Protocol) retrieval server — stdio, read-only, SQLite+FTS5 backed, FastMCP-based Python package
**Researched:** 2026-04-15
**Confidence:** HIGH

## Executive Position

The build guide's 3-service architecture (Search / Content / Version) is **directionally correct and should be kept as-is** for v0.1.0. Three refinements are needed, and one part of the guide (SIGHUP-based reload of an atomically-swapped index file) has a real Unix/SQLite gotcha that needs to be addressed in implementation:

1. **Wire services through FastMCP's `lifespan` + `Context.lifespan_context`** (confirmed idiomatic in mcp 1.27.0). Do **not** use module-level singletons for DB handles.
2. **Add a thin `Container` / `AppContext` dataclass** as the single injection point. This is the de facto "DI system" for FastMCP — a typed dataclass held in `lifespan_context`.
3. **Split the "index file swap" from "reader reload"**: POSIX rename does not invalidate open file handles (readers stay on the old inode), so the running server must explicitly close-and-reopen its read-only connection — SIGHUP alone is not enough and path-based WAL/SHM companion files complicate this further.

The guide's Week 1–4 build order is sound but has one inversion worth calling out: the ingestion module must be built far enough in Week 1 to produce real data before retrieval logic is written, otherwise retrieval tests become fiction.

---

## Standard Architecture

### System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  Process Entry: python -m mcp_server_python_docs / uvx          │
│                                                                  │
│  __main__.py  (Click group)                                     │
│    ├── serve          ───────> server.py   (FastMCP app)        │
│    ├── build-index    ───────> ingestion.cli:build_index        │
│    └── validate-corpus ──────> ingestion.cli:validate_corpus    │
├─────────────────────────────────────────────────────────────────┤
│  FastMCP Interface Layer  (server.py)                            │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │  lifespan: async DI container bootstrap                  │    │
│  │    - open RO sqlite connection (mode=ro, WAL-compatible)│    │
│  │    - construct SearchService / ContentService / Version │    │
│  │    - assert FTS5 available                              │    │
│  │    - register SIGHUP handler -> schedule index reload   │    │
│  │                                                          │    │
│  │  @mcp.tool search_docs(...)  -> ctx.lifespan_context    │    │
│  │  @mcp.tool get_docs(...)     -> ctx.lifespan_context    │    │
│  │  @mcp.tool list_versions(...)-> ctx.lifespan_context    │    │
│  └─────────────────────────────────────────────────────────┘    │
├─────────────────────────────────────────────────────────────────┤
│  Services Layer  (services/)                                     │
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │ SearchService│  │ContentService│  │VersionService│          │
│  │  (search.py) │  │ (content.py) │  │ (version.py) │          │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘          │
│         │                 │                  │                  │
│         └────────┬────────┴──────────────────┘                  │
│                  │                                               │
├──────────────────┼──────────────────────────────────────────────┤
│  Retrieval Layer │ (retrieval/)                                  │
│                  ▼                                               │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  query.py  — parse, classify (symbol-ish vs. concept),    │  │
│  │              synonym expansion                            │  │
│  │  ranker.py — BM25 column weighting, snippet assembly      │  │
│  │  budget.py — apply_budget(text, max_chars, start_index)   │  │
│  └──────────────────────────────────────────────────────────┘  │
│                  │                                               │
├──────────────────┼──────────────────────────────────────────────┤
│  Storage Layer   ▼ (storage/)                                    │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  db.py     — open_readonly(), open_writable(),            │  │
│  │              assert_fts5_available(), pragmas            │  │
│  │  repos.py  — DocumentRepo, SectionRepo, SymbolRepo,       │  │
│  │              SynonymRepo, VersionRepo, RedirectRepo       │  │
│  │  schema.sql                                              │  │
│  └──────────────────────────────────────────────────────────┘  │
│                  │                                               │
│                  ▼                                               │
│          ┌──────────────────────┐                                │
│          │  ~/.cache/mcp-python │                                │
│          │  -docs/index.db      │                                │
│          │  (WAL, RO serving)   │                                │
│          └──────────▲───────────┘                                │
│                     │                                            │
├─────────────────────┼──────────────────────────────────────────┤
│  Ingestion Layer (ingestion/)  (offline; separate process)       │
│                     │                                            │
│  ┌──────────────────┴───────────────────────────────────────┐  │
│  │  cli.py            — build-index, validate-corpus         │  │
│  │  inventory.py      — sphobjinv wrapper (objects.inv)      │  │
│  │  sphinx_json.py    — parse .fjson (primary content path)  │  │
│  │  publish.py        — atomic swap + hash verify + rollback │  │
│  │                     writes to build-{ts}.db, renames      │  │
│  └───────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘

        Shared: config.py, errors.py, models.py (Pydantic I/O)
```

### Component Responsibilities

| Component | Responsibility | Implementation Notes |
|-----------|----------------|----------------------|
| `__main__.py` (Click group) | Process entry; dispatch to `serve` / `build-index` / `validate-corpus` | Click group, thin — parse args, set up stderr logging, delegate |
| `server.py` (FastMCP) | Tool registration, `lifespan` bootstrap, dependency wiring, stdio transport | `FastMCP("python-docs", lifespan=app_lifespan)`; 3 `@mcp.tool` functions only |
| `SearchService` | Orchestrates `search_docs` across `kind="auto"/"page"/"symbol"/"section"/"example"` | Owns the symbol fast-path decision; delegates FTS to retrieval module |
| `ContentService` | Orchestrates `get_docs` for page vs. section retrieval | Anchor presence → section lookup; else page + budget |
| `VersionService` | Lists `doc_sets` rows | Single SELECT; kept as class for symmetry + test seams |
| `retrieval/query.py` | Parse query, classify as symbol-ish, apply synonym expansion, build FTS5 MATCH expression | Pure; no DB access beyond `SynonymRepo` |
| `retrieval/ranker.py` | BM25 column weighting, snippet trimming, result merging | Pure functions |
| `retrieval/budget.py` | `apply_budget(text, max_chars, start_index) -> (text, truncated, next_start_index)` | Pure; Unicode-safe; respects section boundaries when possible |
| `storage/db.py` | `open_readonly()`, `open_writable()`, `assert_fts5_available()`, pragma setup | The **only** module that calls `sqlite3.connect`. Two factory functions, clear intent |
| `storage/repos.py` | One repository class per entity (`DocumentRepo`, `SectionRepo`, `SymbolRepo`, `SynonymRepo`, `VersionRepo`, `RedirectRepo`) | Thin SQL; no business logic; accept a `sqlite3.Connection` in `__init__` |
| `ingestion/cli.py` | `build-index`, `validate-corpus` Click commands | Imports writable connection + repos; never imports `server.py` or FastMCP |
| `ingestion/inventory.py` | `sphobjinv`-based symbol extraction | Network-capable (downloads `objects.inv`) |
| `ingestion/sphinx_json.py` | Parse `.fjson` files into documents/sections/examples | Pure file I/O + parsing |
| `ingestion/publish.py` | Atomic-swap protocol: build temp DB, hash, verify, rename, keep `.previous` | Coordinates the offline side of the swap; does **not** signal the server |
| `config.py` | Resolve `~/.cache/mcp-python-docs/` paths, read env overrides, XDG compliance | `dataclass`, no I/O beyond path resolution |
| `errors.py` | `DocsServerError` hierarchy | Maps to MCP error responses in `server.py` |
| `models.py` | Pydantic input/output models used by FastMCP to auto-generate schemas | `SearchDocsResult`, `GetDocsResult`, `ListVersionsResult`, plus nested hit models |

### Dependency Direction Rule (hard)

```
__main__  ──>  server  ──>  services  ──>  retrieval  ──>  storage
                                              │              │
                                              └─> (repos)  <──┘

__main__  ──>  ingestion  ──>  storage

models, errors, config  ──  shared by all layers above

NO: services -> SQL directly
NO: server -> retrieval directly (must go through a service)
NO: storage -> mcp.* imports (storage must be FastMCP-agnostic)
NO: ingestion -> server or services
```

These rules are enforceable via `import-linter` or simple module-prefix checks in CI.

---

## Recommended Project Structure

Mirrors the build guide's section 13 with three concrete refinements marked `*`:

```
mcp-server-python-docs/
├── pyproject.toml
├── README.md
├── data/
│   └── synonyms.yaml                  # curated concept expansion table
├── src/
│   └── mcp_server_python_docs/
│       ├── __init__.py                # version string only
│       ├── __main__.py                # Click group: serve, build-index, validate-corpus
│       ├── server.py                  # FastMCP app, lifespan, 3 @mcp.tool decorators
│       ├── app_context.py             # * AppContext dataclass (typed DI bundle)
│       ├── config.py
│       ├── errors.py
│       ├── models.py                  # Pydantic I/O schemas
│       ├── services/
│       │   ├── __init__.py
│       │   ├── search.py              # SearchService
│       │   ├── content.py             # ContentService
│       │   └── version.py             # VersionService
│       ├── retrieval/
│       │   ├── __init__.py
│       │   ├── query.py
│       │   ├── ranker.py
│       │   └── budget.py
│       ├── storage/
│       │   ├── __init__.py
│       │   ├── db.py                  # open_readonly, open_writable, FTS5 check
│       │   ├── reload.py              # * handle-swap logic for SIGHUP reload
│       │   ├── schema.sql
│       │   └── repos.py
│       └── ingestion/
│           ├── __init__.py
│           ├── cli.py
│           ├── inventory.py
│           ├── sphinx_json.py
│           └── publish.py             # atomic swap + hash verify
└── tests/
    ├── conftest.py                    # tiny on-disk DB fixtures
    ├── test_retrieval.py
    ├── test_storage.py
    ├── test_ingestion.py
    ├── test_services.py               # * services wired through a test AppContext
    └── test_server_smoke.py
```

**Three refinements (marked `*`) vs. the guide:**

- **`app_context.py`** — a single `AppContext` dataclass that holds `db: sqlite3.Connection`, `search_service`, `content_service`, `version_service`, `config`. This is the "DI container" for FastMCP: populated in `lifespan`, yielded to the context manager, read by tool handlers via `ctx.request_context.lifespan_context`. Keeping it in its own module prevents `server.py` from becoming a god-file and keeps tests independent of FastMCP.
- **`storage/reload.py`** — because POSIX rename does not invalidate open file handles (see "Atomic-Swap Reload" below), the running server needs a small helper that closes the old RO connection and re-opens a new one at the same canonical path after an index swap. This module owns that logic so `server.py` stays thin.
- **`tests/test_services.py`** — distinct from `test_server_smoke.py`. Services are testable without FastMCP; reserve smoke tests for the stdio round-trip. This dramatically cuts test iteration time.

### Structure Rationale

- **`services/` as a flat package of three modules (not classes-as-services):** Three services are small; a sub-package is enough. Each service class has one public method matching the corresponding tool. This matches how the official `mcp-server-git` and `mcp-server-fetch` reference servers structure their business logic.
- **`retrieval/` separate from `services/`:** Retrieval is pure logic with no domain vocabulary. This is the module that has the highest chance of growing in v1.1 (hybrid search, reranking) and benefits most from isolation.
- **`storage/` owns all `sqlite3` calls:** Single-module ownership makes the WAL/FTS5/RO invariants auditable. `grep -r "sqlite3.connect" src/` should return exactly two call sites — `open_readonly` and `open_writable` in `storage/db.py`.
- **`ingestion/` imports `storage/` but not `server/services/retrieval`:** Asymmetric dependency. Ingestion is a batch job, not a server concern. This lets `build-index` run from a minimal process without instantiating FastMCP.
- **`data/synonyms.yaml` at repo root, not inside `src/`:** Package data via `[tool.setuptools.package-data]` in `pyproject.toml`; keeps it human-editable and out of the Python import path.

---

## Architectural Patterns

### Pattern 1: FastMCP Lifespan as the DI Root

**What:** Use `FastMCP(lifespan=app_lifespan)` with a typed `AppContext` dataclass. All dependencies (DB handle, services, config) are constructed inside the async context manager and yielded. Tool handlers receive them via `ctx.request_context.lifespan_context`.

**When to use:** Always. This is the current idiomatic pattern in `mcp` 1.27.0 (April 2026). Module-level globals for DB connections are an anti-pattern because they can't be cleanly torn down in tests and don't participate in startup/shutdown ordering.

**Trade-offs:**
- (+) Single source of truth for live dependencies
- (+) Type-safe injection via `Context[ServerSession, AppContext]`
- (+) Automatic cleanup in `finally` block (connection close, background task cancel)
- (-) Every tool handler has a boilerplate `ctx: Context[ServerSession, AppContext]` parameter — acceptable
- (-) Slightly more vertical code than module-level globals — but tests become trivial

**Example:**

```python
# app_context.py
from dataclasses import dataclass
from sqlite3 import Connection
from .services.search import SearchService
from .services.content import ContentService
from .services.version import VersionService
from .config import AppConfig

@dataclass
class AppContext:
    config: AppConfig
    db: Connection
    search: SearchService
    content: ContentService
    version: VersionService
```

```python
# server.py
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from mcp.server.fastmcp import FastMCP, Context
from mcp.server.session import ServerSession

from .app_context import AppContext
from .config import load_config
from .storage.db import open_readonly, assert_fts5_available
from .services.search import SearchService
from .services.content import ContentService
from .services.version import VersionService
from .storage.repos import (
    SymbolRepo, SectionRepo, DocumentRepo,
    SynonymRepo, VersionRepo, RedirectRepo,
)

@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    config = load_config()
    db = open_readonly(config.index_path)  # sqlite3.connect with uri=True, mode=ro
    assert_fts5_available(db)

    symbol_repo = SymbolRepo(db)
    section_repo = SectionRepo(db)
    doc_repo = DocumentRepo(db)
    synonym_repo = SynonymRepo(db)
    version_repo = VersionRepo(db)
    redirect_repo = RedirectRepo(db)

    ctx = AppContext(
        config=config,
        db=db,
        search=SearchService(symbol_repo, section_repo, synonym_repo),
        content=ContentService(doc_repo, section_repo, redirect_repo),
        version=VersionService(version_repo),
    )
    try:
        yield ctx
    finally:
        db.close()

mcp = FastMCP("python-docs", lifespan=app_lifespan)

@mcp.tool()
def search_docs(
    query: str,
    ctx: Context[ServerSession, AppContext],
    version: str | None = None,
    kind: str = "auto",
    max_results: int = 5,
) -> "SearchDocsResult":
    return ctx.request_context.lifespan_context.search.search(
        query=query, version=version, kind=kind, max_results=max_results
    )
```

This pattern is authoritative — it appears verbatim in the official `modelcontextprotocol/python-sdk` README and is the documented way to do lifecycle + DI in FastMCP. (Confidence: HIGH — Context7 docs, version 1.27.0)

---

### Pattern 2: Service Classes as Thin Orchestrators, Retrieval as Pure Logic

**What:** A service class holds references to repositories and retrieval functions; its public method (e.g. `SearchService.search(...)`) is a ~20-line orchestration: classify → expand → query repos → rank → budget → return Pydantic model. All heavy logic lives in `retrieval/` as pure functions.

**When to use:** Always for this project. The service layer is the boundary where domain vocabulary (`slug`, `anchor`, `kind`) lives; retrieval is the boundary where algorithmic vocabulary (`BM25`, `column weight`, `truncation`) lives. Keeping them separate is the single best refactoring hedge for v1.1's hybrid-search addition.

**Trade-offs:**
- (+) Services are ~50–100 lines each, unit-testable with mock repos
- (+) Retrieval functions are pure → property-based tests are trivial
- (-) One extra indirection vs. inlining everything in tool handlers — worth it

**Example:**

```python
# services/search.py
from dataclasses import dataclass
from ..retrieval import query as q, ranker, budget
from ..models import SearchDocsResult, SearchHit

@dataclass
class SearchService:
    symbols: "SymbolRepo"
    sections: "SectionRepo"
    synonyms: "SynonymRepo"

    def search(self, query: str, version: str | None,
               kind: str, max_results: int) -> SearchDocsResult:
        resolved_version = version or self._default_version()
        if kind == "auto":
            kind = q.classify_kind(query)

        # Symbol fast-path — skip FTS entirely for identifier-shaped queries
        if kind == "symbol" and q.looks_like_symbol(query):
            hits = self.symbols.lookup_exact(query, resolved_version, limit=max_results)
            if hits:
                return SearchDocsResult(hits=hits, kind_used="symbol", path="fast")

        expanded = q.expand_with_synonyms(query, self.synonyms)
        fts_query = q.build_fts_match(expanded, kind)
        raw_hits = self.sections.fts_match(fts_query, resolved_version, limit=max_results * 2)
        ranked = ranker.apply_column_weights(raw_hits)
        return SearchDocsResult(hits=ranked[:max_results], kind_used=kind, path="fts")
```

---

### Pattern 3: Two Connection Factories, Never Shared

**What:** `storage/db.py` exports exactly two functions — `open_readonly(path)` and `open_writable(path)`. The serving process only ever calls the first; ingestion only ever calls the second. Both enforce pragmas and check FTS5 availability.

**When to use:** Always. This is the guide's intent; the refinement is making it a code-level invariant rather than a documentation one.

**Trade-offs:**
- (+) Impossible to accidentally open a writable handle from server code (test assertion: server process never holds a writable connection)
- (+) Pragma drift is eliminated — all pragmas set in one of two functions
- (-) None meaningful

**Example:**

```python
# storage/db.py
import sqlite3
from pathlib import Path

def _apply_common_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")  # 5s; avoids SQLITE_BUSY under concurrent ingestion
    conn.row_factory = sqlite3.Row

def open_readonly(path: Path) -> sqlite3.Connection:
    """Serving connection. Does NOT enable WAL (writable process did that).
    Does NOT use ?immutable=1 because WAL files may advance during our lifetime."""
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    _apply_common_pragmas(conn)
    # Read-only handle cannot set journal_mode; that was done at build time
    conn.execute("PRAGMA query_only = ON")
    return conn

def open_writable(path: Path) -> sqlite3.Connection:
    """Ingestion-only connection. Sets WAL mode. Exactly one at a time."""
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    _apply_common_pragmas(conn)
    return conn

def assert_fts5_available(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("CREATE VIRTUAL TABLE _fts5_check USING fts5(x)")
        conn.execute("DROP TABLE _fts5_check")
    except sqlite3.OperationalError as e:
        raise RuntimeError(
            "SQLite FTS5 is not available. Install Python with FTS5 support, "
            "or add `pysqlite3-binary` as a fallback dependency."
        ) from e
```

**WAL + read-only gotcha:** The Python `sqlite3` docs and the SQLite WAL documentation confirm that a read-only connection to a WAL-mode database requires the `-wal` and `-shm` files to be present **and** the directory to be writable, **or** the connection must use `?immutable=1`. Using `?immutable=1` is wrong for a swappable index (the server is supposed to re-read the new file after swap). Using `?mode=ro` is correct **as long as the cache directory is writable** — which it always is for `~/.cache/mcp-python-docs/`. Document this in README's troubleshooting section.

---

### Pattern 4: Click Group Entry Point (`serve` / `build-index` / `validate-corpus`)

**What:** A single `click.Group` in `__main__.py`, with `serve` as the default command. `serve` is chosen as default so `uvx mcp-server-python-docs` with no args launches the MCP server (Claude Desktop and Cursor call it exactly this way).

**When to use:** Whenever an MCP server needs both a server mode and admin CLI. The reference implementations in `modelcontextprotocol/servers` use:
- `mcp-server-git`: Click with a single command (no subcommands) — simplest
- `mcp-server-fetch`: argparse with a single command — also simple
- `mcp-server-time`: argparse with a single command

None of the official Python reference servers ship multiple subcommands today, so this project sets the pattern rather than inheriting one. **Click is the right choice** because: (1) group-with-default-command is first-class in Click and awkward in argparse; (2) Click's type system (`click.Path(exists=True)`, `click.Choice`) carries weight for `build-index` flags; (3) Click is already in the MCP reference-server toolbox (`mcp-server-git` uses it), so the ecosystem precedent exists.

**Typer is a reasonable alternative** but adds a dependency on top of Click for little gain in a 3-command CLI. Pick Click unless there's another reason to pull in Typer.

**Trade-offs:**
- (+) `uvx mcp-server-python-docs` → runs `serve` (matches stdio MCP norm)
- (+) `uvx mcp-server-python-docs build-index --versions 3.12,3.13` → one-shot builder
- (+) Click's `@click.pass_context` integrates cleanly with stderr logging setup
- (-) Slightly more verbose than argparse for a single command

**Example:**

```python
# __main__.py
import asyncio
import logging
import sys
import click

@click.group(invoke_without_command=True)
@click.option("-v", "--verbose", count=True)
@click.pass_context
def cli(ctx: click.Context, verbose: int) -> None:
    """MCP server for Python stdlib documentation."""
    level = logging.WARNING
    if verbose == 1:
        level = logging.INFO
    elif verbose >= 2:
        level = logging.DEBUG
    logging.basicConfig(stream=sys.stderr, level=level,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    if ctx.invoked_subcommand is None:
        ctx.invoke(serve)

@cli.command()
def serve() -> None:
    """Run the MCP stdio server (default)."""
    from .server import mcp
    mcp.run(transport="stdio")

@cli.command("build-index")
@click.option("--versions", required=True,
              help="Comma-separated Python versions, e.g. 3.12,3.13")
@click.option("--cache-dir", type=click.Path(), default=None)
def build_index(versions: str, cache_dir: str | None) -> None:
    """Build or rebuild the local index."""
    from .ingestion.cli import run_build
    asyncio.run(run_build(versions.split(","), cache_dir))

@cli.command("validate-corpus")
def validate_corpus() -> None:
    """Validate an existing index against a fixture set."""
    from .ingestion.cli import run_validate
    asyncio.run(run_validate())

def main() -> None:  # pyproject [project.scripts] entry point
    cli()

if __name__ == "__main__":
    main()
```

**Critical detail:** Logging setup (`logging.basicConfig(stream=sys.stderr, ...)`) happens in the `cli` callback, **before** any subcommand runs. This guarantees stdout is never polluted by any library that might log at import time — a non-negotiable rule for stdio MCP servers per the guide's section 9.

**Second critical detail:** The tool imports (`from .server import mcp`, `from .ingestion.cli import run_build`) are **lazy** — they happen inside subcommand functions, not at module top. This keeps `build-index` from importing FastMCP and vice-versa, shortening cold start and isolating failure modes.

---

### Pattern 5: Observability as a Decorator on Services (not Middleware)

**What:** Instrument latency, tool-call rates, synonym hits, and symbol fast-path ratios as **per-service-method decorators**, not as FastMCP middleware. Log to stderr using a structured JSON formatter; aggregate via `logging.getLogger("mcp_server_python_docs.metrics")`.

**When to use:** For v0.1.0. Middleware-based instrumentation in FastMCP requires intercepting the transport layer, which is overkill for a local stdio server. A service-method decorator captures the right granularity:

```python
# retrieval/instrumentation.py
import logging
import time
from functools import wraps

_metrics = logging.getLogger("mcp_server_python_docs.metrics")

def instrumented(tool_name: str):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            truncated = False
            hits = 0
            path = "unknown"
            try:
                result = fn(*args, **kwargs)
                truncated = getattr(result, "truncated", False)
                hits = len(getattr(result, "hits", []) or [])
                path = getattr(result, "path", "normal")
                return result
            finally:
                dt_ms = (time.perf_counter() - t0) * 1000
                _metrics.info("tool_call", extra={
                    "tool": tool_name,
                    "latency_ms": round(dt_ms, 2),
                    "hit_count": hits,
                    "truncated": truncated,
                    "path": path,
                })
        return wrapper
    return decorator

# services/search.py
class SearchService:
    @instrumented("search_docs")
    def search(self, ...): ...
```

**Trade-offs:**
- (+) No FastMCP-internal dependencies; survives upgrades
- (+) Easy to unit-test (call the decorated method, assert on log records)
- (+) Per-service granularity matches the metrics the guide actually wants (symbol-path ratio, synonym-hit rate)
- (-) Doesn't capture FastMCP protocol-level errors — acceptable for v0.1.0; add middleware in v1.1 if HTTP transport appears

**Why not middleware?** FastMCP's current plugin/middleware surface is in flux and stdio doesn't need request-scoped middleware. The service decorator is stable Python, understands domain vocabulary (`hit_count`, `path`), and can't be broken by FastMCP SDK updates.

---

## Data Flow

### Request Flow (search_docs example)

```
Claude Desktop (stdio)
      │
      │ JSON-RPC: tools/call search_docs
      ▼
FastMCP stdio transport
      │
      │ pydantic validation (from type hints on search_docs)
      ▼
server.py @mcp.tool search_docs(query, version, kind, max_results, ctx)
      │
      │ ctx.request_context.lifespan_context  →  AppContext
      ▼
SearchService.search(query, version, kind, max_results)
      │
      │ 1. classify_kind(query)              retrieval/query.py
      │ 2. looks_like_symbol(query)?         retrieval/query.py
      │    └── YES → SymbolRepo.lookup_exact() ──► storage/repos.py ──► SELECT
      │    └── NO  → continue
      │ 3. expand_with_synonyms(query)       retrieval/query.py + SynonymRepo
      │ 4. build_fts_match(expanded)         retrieval/query.py
      │ 5. SectionRepo.fts_match()           storage/repos.py ──► FTS5 MATCH
      │ 6. apply_column_weights(raw_hits)    retrieval/ranker.py
      ▼
SearchDocsResult (Pydantic model)
      │
      │ auto-serialized by FastMCP
      ▼
JSON-RPC response over stdout ──► Claude Desktop
```

**Key invariants:**
- Every arrow from `server.py` downward traverses `lifespan_context`; no module globals.
- `SELECT` statements appear only in `storage/repos.py`.
- `retrieval/query.py` is the only place that manipulates FTS5 MATCH syntax.
- The symbol fast-path bypasses FTS but **not** the service layer — logging/instrumentation still captures it.

### Content Flow (get_docs with anchor)

```
Claude Desktop
      │  get_docs(slug="asyncio-task", anchor="taskgroup")
      ▼
ContentService.get_section(slug, version, anchor, max_chars, start_index)
      │
      │ 1. RedirectRepo.resolve(slug, version)  # handle old→new anchor drift
      │ 2. DocumentRepo.by_slug(slug, version)
      │ 3. SectionRepo.by_document_and_anchor(document_id, anchor)
      │ 4. retrieval/budget.apply_budget(section.content_text, max_chars, start_index)
      ▼
GetDocsResult { uri, title, heading, content, truncated, next_start_index }
```

When `anchor` is omitted, step 3 is skipped and step 4 operates on `document.content_text`.

### Ingestion Flow (build-index, offline)

```
$ mcp-server-python-docs build-index --versions 3.12,3.13
      │
      ▼
__main__.cli → ingestion/cli.py run_build()
      │
      │ for each version:
      │   ├── inventory.py        (sphobjinv fetch + parse objects.inv)
      │   ├── sphinx_json.py      (sphinx-build -b json || download pre-built .fjson)
      │   └── parse each .fjson into canonical model
      ▼
Writable DB at  ~/.cache/mcp-python-docs/build-{timestamp}.db
      │  1. DocumentRepo.insert_many()
      │  2. SectionRepo.insert_many()
      │  3. SymbolRepo.insert_many()
      │  4. FTS5 populate (sections_fts, symbols_fts, examples_fts)
      │  5. SynonymRepo.load_from_yaml(data/synonyms.yaml)
      │
      ▼
ingestion/publish.py:
      │  1. SHA256 of build-{ts}.db → ingestion_runs.artifact_hash
      │  2. Smoke-test queries (FTS returns rows, symbol lookup works)
      │  3. If previous index.db exists: mv index.db index.db.previous
      │  4. mv build-{ts}.db → index.db  (POSIX rename, atomic)
      ▼
DONE — but the running server still has an open handle to the old inode!
      ▼
Signal the server: SIGHUP  OR  user restarts the MCP process
```

The final step is where the guide's "atomic swap" framing runs into Unix reality. See the next section.

---

## Atomic-Swap Reload — The Gotcha Section

This is the **single highest-risk architectural detail** in the whole design, because if it's wrong, users will see stale search results after a rebuild and have no clue why.

### What POSIX Actually Does on Rename

On Linux / macOS, `rename("build-new.db", "index.db")` does **not** invalidate existing file descriptors. A running server that called `open("index.db", O_RDONLY)` before the swap continues to read the **old inode**. The file contents remain intact until the last open file descriptor is closed, then the old inode is deleted. (Sources: `sqlite.org/forum/forumpost/3e62dce4e8`, Linux rename(2), Unix file-handle semantics.)

### What This Means for SQLite Specifically

SQLite in WAL mode maintains a mapping between the opened DB path and the `-wal` / `-shm` companion files. Those companion files are named by **path**, not inode. After the swap:

1. The running RO connection still reads from the old inode (good: consistent view).
2. But `index.db-wal` and `index.db-shm` now belong to the **new** DB — if the server somehow re-reads them, corruption is possible.
3. If the old `.previous` is deleted while the server still has the old inode open, the inode lingers in the filesystem (fine), but any new connection opened at `index.db` sees the new DB.

The guide says "server reloads via SIGHUP or process restart" but doesn't specify the mechanics. **This needs implementation.**

### The Right Implementation

Two options, in preference order:

**Option A (recommended): Explicit reload, signalled by SIGHUP**

```python
# storage/reload.py
import logging
import signal
import threading
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

class ReloadableConnection:
    """Holds a single RO sqlite3 connection; supports atomic replacement
    triggered by SIGHUP. Tool handlers go through `.conn` each call."""
    def __init__(self, path: Path, opener):
        self._path = path
        self._opener = opener
        self._lock = threading.Lock()
        self._conn = opener(path)

    @property
    def conn(self) -> sqlite3.Connection:
        with self._lock:
            return self._conn

    def reload(self) -> None:
        with self._lock:
            old = self._conn
            try:
                new = self._opener(self._path)
                self._conn = new
                logger.info("index reloaded", extra={"path": str(self._path)})
            finally:
                try:
                    old.close()
                except Exception:
                    logger.exception("old connection close failed")

def install_sighup_reload(reloader: ReloadableConnection) -> None:
    def _handler(signum, frame):
        reloader.reload()
    signal.signal(signal.SIGHUP, _handler)
```

`AppContext.db` becomes a `ReloadableConnection`; repositories take `reloader.conn` at call time rather than caching a connection. The SIGHUP handler is installed in `app_lifespan`. The ingestion `publish.py` script can optionally send SIGHUP to the server PID if it knows it (via pidfile); otherwise users just restart the client, which is fine for stdio.

**Option B: "Restart only"**

Simpler. Document that `build-index` requires the client to be restarted to pick up changes. No SIGHUP handler, no reload module. Acceptable for v0.1.0 because stdio MCP servers are cheap to restart — Claude Desktop and Cursor spawn them per-session anyway.

**Recommendation:** Ship Option B for v0.1.0, add Option A in v0.1.1 if users report the reload annoyance. The guide's section 8 says "Server reloads via SIGHUP or process restart" which already allows this interpretation.

**Whichever option is chosen, the guide is wrong on one detail:** the rename alone does not update the server's in-memory view. The PITFALLS research file should flag this as a foot-gun.

### Background Build Caveat

One more catch: if `build-index` is running **while** the server is running, both processes hold file handles to `~/.cache/mcp-python-docs/`. WAL mode permits this (concurrent readers + one writer). But the writer is operating on a **different file** (`build-{ts}.db`), not the same file, so this is degenerate — there is no concurrent access to `index.db` during build. Atomic rename is the only moment of contact.

**Test this:** `tests/test_ingestion.py` should include a case that:
1. Opens a RO server connection to `index.db`
2. Runs `build-index` in a subprocess (writes `build-new.db`, renames to `index.db`)
3. Asserts the server connection is still valid (still serves old data)
4. Calls `reloader.reload()` and asserts new data is visible

---

## Build Order — Refinements to the Guide's Week 1–4 Plan

The guide's section 16 plan is reasonable but has one inversion that burns two days if followed literally. Here's the refined order:

### Refined Week 1: Foundation + Vertical Slice

| Day | Task | Why / Note |
|-----|------|------------|
| 1 | Package skeleton, `pyproject.toml` with `[project.scripts]` entry point, Click group with three stub subcommands, stderr logging wiring | Locks in CLI shape before any business logic |
| 1 | `storage/db.py` with `open_readonly` / `open_writable` + FTS5 check + schema.sql bootstrap | Single-module SQL gate established from day 1 |
| 2 | `ingestion/inventory.py` — download `objects.inv` for 3.13, parse via sphobjinv, insert into `symbols` table. End-to-end with `build-index`. | **Ingestion must come before retrieval** — otherwise tests use fake data and bugs hide |
| 2 | `AppContext` dataclass + `app_lifespan` + FastMCP registration of a **stub** `search_docs` returning `"not implemented"` | Proves the DI wiring before tool logic is written |
| 3 | `SymbolRepo.lookup_exact` + `SearchService.search` symbol fast-path + real `search_docs` response | First working tool |
| 3 | stdio smoke test against Claude Desktop — "what is asyncio.TaskGroup" returns URL | Confirms protocol hygiene (no stdout print) |
| 4 | `ingestion/sphinx_json.py` — parse one `.fjson` fixture into documents/sections | Second ingestion tier; uses fixture, not full CPython build |
| 5 | Second tool `get_docs` with page-level retrieval (no anchor, no budget yet) | Two-tool smoke test |

**Milestone 1:** Claude Desktop can ask `search_docs("asyncio.TaskGroup")` AND `get_docs("asyncio-task")` and get real results from one Python version.

### Refined Week 2: Content Depth + Ingestion Scale

| Day | Task |
|-----|------|
| 1 | Full CPython 3.13 ingestion (`sphinx-build -b json` externally; parser ingests the directory) |
| 2 | Section extraction with anchor stability, code-block extraction (doctest vs. standalone) |
| 3 | FTS5 population for sections / symbols / examples; `SectionRepo.fts_match` |
| 3 | Synonym table loaded from `data/synonyms.yaml` at build time |
| 4 | `retrieval/query.py` classifier + synonym expansion; `kind="auto"` in SearchService |
| 5 | BM25 column weighting in `retrieval/ranker.py`; tune against ~10 evaluation queries |

**Milestone 2:** "How do I run code in parallel?" returns relevant `concurrent.futures` / `asyncio` / `multiprocessing` results.

### Refined Week 3: Polish + Publishing

| Day | Task |
|-----|------|
| 1 | `ContentService` section retrieval when `anchor` provided; `retrieval/budget.py` with Unicode-safe truncation |
| 2 | Pagination via `start_index`; `VersionService.list_versions` |
| 3 | `ingestion/publish.py` atomic swap protocol + SHA256 verify + `.previous` rollback |
| 3 | **Write the ingestion-while-serving test** (the atomic-swap regression case above) |
| 4 | Error taxonomy wired into `server.py` (mapping `DocsServerError` subclasses to MCP errors); instrumentation decorator on services |
| 5 | Test pass — unit, storage, ingestion, smoke, stability |

**Milestone 3:** Full tool surface working; index rebuild does not corrupt a running server (even if reload requires restart).

### Refined Week 4: Multi-version + Ship

| Day | Task |
|-----|------|
| 1 | Multi-version support; `build-index --versions 3.12,3.13`; version selection in SearchService/ContentService |
| 2 | LRU caching on hot reads (`resolve_symbol`, `get_section`) |
| 3 | README with troubleshooting section explicitly documenting FTS5 fallback and reload semantics |
| 4 | Integration testing with Claude Desktop AND Cursor; fix UX papercuts |
| 5 | PyPI publish; tag v0.1.0 |

**Key delta from guide's plan:** Ingestion of real data is pulled into Day 2 of Week 1, before any retrieval code is written. This is the biggest practical improvement — writing retrieval against fake data is a trap.

### Build-Order Rationale

```
CLI skeleton + stderr logging        ─┐
storage/db.py (2 factories + FTS5 check) ─┼─ pre-requisite for everything
schema.sql                            ─┘
        │
        ▼
ingestion/inventory.py (sphobjinv)    ──► real symbol data early
        │
        ▼
AppContext + lifespan + stub tool    ──► DI wiring validated
        │
        ▼
SymbolRepo + SearchService fast-path ──► first real tool
        │
        ▼
ingestion/sphinx_json.py             ──► content data
        │
        ▼
Section retrieval + retrieval/query  ──► full search
        │
        ▼
publish.py atomic swap              ──► rebuild safety
        │
        ▼
Multi-version + polish              ──► ship gate
```

Each step has a testable milestone. No step is blocked by a parallel track.

---

## Scaling Considerations

This is a **single-user local stdio server**. Scaling in the traditional sense (more users) is explicitly out of scope. But there are three "scale" dimensions that matter:

| Dimension | 1 Python version | 3 versions | 6 versions |
|-----------|------------------|------------|------------|
| Index size | ~80–120 MB | ~240–360 MB | ~500–700 MB |
| Build time | ~90–180s | ~5–10 min | ~10–20 min |
| Query latency (p50) | <5 ms | <5 ms | <10 ms |
| Query latency (p95) | <20 ms | <20 ms | <40 ms |

**First bottleneck (if one appears): ingestion time.** Full rebuild of all versions is single-threaded in the guide. Fix = parallelize by version (each version is independent; use `concurrent.futures.ProcessPoolExecutor`).

**Second bottleneck: FTS5 snippet generation** for long sections. Fix = store pre-computed abstracts in the sections table, skip snippet generation when the section is short.

**Third bottleneck (never expected): query concurrency.** stdio is single-connection so this is impossible by construction.

---

## Anti-Patterns

### Anti-Pattern 1: Module-Level DB Connection Global

**What people do:**
```python
# storage/db.py
import sqlite3
CONN = sqlite3.connect("~/.cache/mcp-python-docs/index.db")  # at import time
```

**Why it's wrong:**
- Connects at import, not at startup — can't check FTS5 availability gracefully
- Can't be re-opened on SIGHUP → atomic-swap reload is impossible
- Tests can't substitute a test DB without monkeypatching
- Startup failures become ImportError, which FastMCP can't surface as a clean error

**Do this instead:** Open inside `app_lifespan` and pass via `AppContext`.

---

### Anti-Pattern 2: `print()` Anywhere in the Serving Path

**What people do:** Debug-print a query result, a health-check, a version number, anything.

**Why it's wrong:** stdout is reserved for JSON-RPC. One stray byte and the client disconnects with no useful error. The symptom is "my server silently stops working after 10 queries" — unwatchable from the user side.

**Do this instead:** `logging.getLogger(...).info(...)` with stderr handler configured in `__main__.py` **before** any other import. Add a lint rule (`ruff` `T20` for `print()` calls) for `src/mcp_server_python_docs/`.

---

### Anti-Pattern 3: Services Importing from `mcp.*`

**What people do:** A service catches a SQLite error and raises `mcp.types.ErrorData` directly.

**Why it's wrong:** Couples the service to FastMCP internals. In v1.1 if HTTP transport appears or the SDK's error shape changes, every service needs refactoring.

**Do this instead:** Services raise `DocsServerError` subclasses. `server.py` is the **only** module that translates domain errors to MCP errors. This matches the dependency rule in the architecture diagram.

---

### Anti-Pattern 4: Service-per-Tool Factoring That Doesn't Match Tool Semantics

**What people do:** Add a `RankerService`, `SynonymService`, `BudgetService`, `QueryClassifierService`, etc. — one service per noun in the retrieval pipeline.

**Why it's wrong:** Premature factoring. Retrieval is pure logic with no lifecycle, no state, no dependencies. Services are the layer where cross-cutting concerns (logging, error translation, repository orchestration) live. Nothing in the retrieval pipeline has those concerns.

**Do this instead:** Keep `services/` at exactly 3 modules matching 3 tools. Put all retrieval logic in `retrieval/` as pure functions or small dataclasses. The guide's 3-service split is correct — resist the urge to re-factor it mid-build.

---

### Anti-Pattern 5: Atomic Rename Without Reader Reload

**What people do:** Implement `publish.py` with `os.rename(build, index)`, ship it, assume the running server picks up the new data on next query.

**Why it's wrong:** Unix file handles track the inode, not the path. Running server reads stale data forever until restarted. Users think "my rebuild worked — why am I still seeing 3.12 docs?" — they won't think to restart the client.

**Do this instead:** Either (a) document that rebuilds require a client restart and print a loud message at the end of `build-index`, or (b) implement `ReloadableConnection` with SIGHUP handler. Option (a) is fine for v0.1.0.

---

### Anti-Pattern 6: `immutable=1` on the Read-Only Serving Connection

**What people do:** Notice the "WAL + mode=ro requires writable directory" caveat, panic, and add `?immutable=1` to the URI.

**Why it's wrong:** `immutable=1` tells SQLite the DB file will **never change**. With that flag set, SQLite may cache pages aggressively and will not notice when the file is renamed/swapped. Reload becomes impossible.

**Do this instead:** Use `?mode=ro` (not `?immutable=1`). The cache directory is writable by definition (user owns it), so SQLite can create the transient `-shm` file. Only use `?immutable=1` for truly read-only media (a CD-ROM, a `squashfs` mount).

---

## Integration Points

### External Processes

| Integration | Pattern | Notes |
|-------------|---------|-------|
| Claude Desktop / Cursor | stdio JSON-RPC | Client spawns the MCP server as a child process; server reads stdin, writes stdout; both use line-delimited JSON-RPC 2.0 |
| `sphinx-build` | Invoked by user before `build-index`, or by ingestion script | Ingestion expects `.fjson` files in a directory; it does not run sphinx itself in v0.1.0 (avoids pulling sphinx as a runtime dep) |
| `objects.inv` (python.org) | HTTP download via `sphobjinv` during ingestion | Network access only during `build-index`, never during `serve` |
| PyPI (for `pysqlite3-binary` fallback) | Installed by user as optional dep if system SQLite lacks FTS5 | Documented in README troubleshooting |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| `server.py` ↔ `services/*` | Direct Python call through `AppContext` | Services are sync today (SQLite queries are fast); can become async if a future repo uses async I/O |
| `services/*` ↔ `retrieval/*` | Direct function call; retrieval is pure | No shared state beyond function parameters |
| `services/*` ↔ `storage/repos.py` | Direct method call on repo class; repos wrap a `sqlite3.Connection` | Services never receive a raw `Connection` |
| `ingestion/cli.py` ↔ `storage/db.py` | Via `open_writable()` | Writable connection never crosses into the server process |
| `ingestion/publish.py` ↔ filesystem | POSIX `rename(2)` | Assumes same filesystem (no cross-FS rename); enforce at `build-index` start |

---

## Sources

### Authoritative (HIGH confidence)
- [Model Context Protocol Python SDK README](https://github.com/modelcontextprotocol/python-sdk/blob/main/README.md) — `FastMCP(lifespan=...)`, `Context[ServerSession, AppContext]`, `ctx.request_context.lifespan_context`, structured output models, stdio transport
- [mcp 1.27.0 on PyPI](https://pypi.org/project/mcp/) — current version as of 2026-04-02; confirms current API surface
- [SQLite WAL Documentation](https://www.sqlite.org/wal.html) — WAL + read-only requirements, `-wal`/`-shm` companion files
- [SQLite Forum: Renaming a database](https://sqlite.org/forum/forumpost/3e62dce4e8) — authoritative statement that renaming an open DB is undefined and readers need to reopen
- [mcp-server-git source](https://github.com/modelcontextprotocol/servers/blob/main/src/git/src/mcp_server_git/__init__.py) — reference for Click-based entry point, stderr logging setup
- [mcp-server-fetch source](https://github.com/modelcontextprotocol/servers/blob/main/src/fetch/src/mcp_server_fetch/__init__.py) — argparse alternative for simple single-command servers
- [Click documentation — Command Groups](https://click.palletsprojects.com/en/stable/commands/) — `@click.group()` + subcommands pattern

### Secondary (MEDIUM confidence)
- [DeepWiki: Context Injection & Lifespan Management](https://deepwiki.com/modelcontextprotocol/python-sdk/2.5-context-injection-and-lifespan) — summarizes the same patterns as the SDK README, useful cross-reference
- [gofastmcp.com: FastMCP Server](https://gofastmcp.com/python-sdk/fastmcp-server-server) — confirms no explicit DI container; lifespan + Context is the pattern
- [Charles Leifer: Going Fast with SQLite and Python](https://charlesleifer.com/blog/going-fast-with-sqlite-and-python/) — WAL mode best practices in Python 3
- [Simon Willison TIL: Enabling WAL mode for SQLite](https://til.simonwillison.net/sqlite/enabling-wal-mode) — practical notes on WAL mode adoption

---

*Architecture research for: MCP stdio server, read-only SQLite+FTS5 retrieval over Python stdlib docs*
*Researched: 2026-04-15*
