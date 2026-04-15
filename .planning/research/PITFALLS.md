# Pitfalls Research

**Domain:** Python MCP stdio retrieval server over Sphinx-indexed documentation (SQLite + FTS5 + sphobjinv)
**Researched:** 2026-04-15
**Confidence:** HIGH (Context7 FastMCP docs, official SQLite FTS5 docs, CPython/Sphinx issue trackers, PyPA packaging docs, PyPI MCP release evidence). Some MCP-specific UX pitfalls are MEDIUM (community reports, GitHub issues).

The build guide's section 9 covers the "classic three" (stdout pollution, FTS5 capability check, WAL mode) and section 8 covers atomic swap. This document surfaces the **additional 2026-era pitfalls** that are not yet in the guide — the ones that bite a Python-stdlib-over-MCP retrieval server between a working prototype and a shippable v0.1.0.

---

## Critical Pitfalls

### Pitfall 1: `unicode61 porter` tokenizer shreds Python identifiers

**What goes wrong:**
The build guide's schema uses `tokenize='unicode61 porter'` for `sections_fts` and `tokenize='unicode61'` for `symbols_fts`. By default, `unicode61` treats **all punctuation** — including `.` and `_` — as separator characters. This means:

- `asyncio.TaskGroup` is indexed as the three tokens `asyncio`, `taskgroup` — the **dot is gone**. A user who searches `"asyncio.TaskGroup"` as a phrase hits FTS5 phrase-search, which requires adjacent tokens, and the literal dot never existed as a token boundary so the phrase match behaves unintuitively.
- `str.format_map` → tokens `str`, `format`, `map` (the underscore in `format_map` is ALSO a separator by default).
- `concurrent.futures.ThreadPoolExecutor` explodes into 4+ unrelated tokens.
- The Porter stemmer then stems `futures` → `futur`, `classes` → `class`, which is fine for prose but turns `TaskGroup` into `taskgroup` and loses the original casing forever.

The symbol fast-path (objects.inv lookup) saves you when the identifier is literal, but the moment a user's query passes to FTS5 ("how does asyncio.gather behave" → falls through to BM25 because it's not a bare identifier), the retrieval ranks pages that happen to mention `asyncio` and `gather` anywhere, not pages about `asyncio.gather` specifically.

**Why it happens:**
The SQLite FTS5 docs describe `unicode61` as "case-insensitive according to Unicode 6.1" and the default `separators` treats punctuation per Unicode tables. `tokenchars` is an **opt-in** option — it is not the default. Most tutorial examples use plain `unicode61` because they index English prose, not code.

**How to avoid:**
Override the token characters explicitly:
```sql
-- For sections_fts (prose + code blocks mixed)
tokenize = "unicode61 remove_diacritics 2 tokenchars '._'"

-- For symbols_fts (qualified names are the whole value)
tokenize = "unicode61 remove_diacritics 0 tokenchars '._'"
```
Notes:
- `remove_diacritics 2` (not `0`, not `1`) is the recommended modern default; `0` ignores combining diacritics, `1` is buggy on some codepoints, `2` is correct for Unicode 15+.
- Drop `porter` for `sections_fts` as well — stemming is a net negative for API docs because it destroys case and doesn't help for technical terms. The synonym table already does the work a stemmer would.
- Add a unit test that asserts: `SELECT ... FROM sections_fts WHERE sections_fts MATCH 'asyncio.TaskGroup'` returns ≥1 row for a known fixture.
- Add a unit test that asserts `tokenchars` is active by `INSERT INTO sections_fts(sections_fts) VALUES('integrity-check')` and by introspecting `SELECT * FROM sections_fts WHERE sections_fts MATCH 'format_map'` → ≥1 hit on the `str` page.

**Warning signs:**
- "BM25 is returning irrelevant pages for identifier queries" during eval harness.
- Stability test `test_resolve_asyncio_taskgroup` passes via symbol fast-path but `search_docs("how does asyncio.TaskGroup handle exceptions")` returns `asyncio` top page with 0% semantic relevance to TaskGroup.
- Logs show `symbol resolution path = fallback_to_fts` for queries that contain dotted identifiers.

**Phase to address:** Phase 2 (Schema + Storage) — must be set before the first ingestion run, because changing tokenizer requires a full rebuild. **Severity: BLOCKER for search quality.**

---

### Pitfall 2: FTS5 MATCH query injection from user input

**What goes wrong:**
User queries flow through `search_docs(query: str, ...)` directly into an FTS5 `MATCH` clause. FTS5 query syntax has **many special operators** — `AND`, `OR`, `NOT`, `NEAR`, `+`, `-`, `:`, `(`, `)`, `*`, `^`, `"` — and any unbalanced quote, stray parenthesis, or colon in user input raises `sqlite3.OperationalError: fts5: syntax error near …`. The server crashes the tool call with a 500-style error, the LLM retries with the same broken query, and the conversation spirals.

Worse, some queries **look like valid FTS5 but mean the wrong thing**: `json:loads` is interpreted as a column filter (`column:json` does not exist → error), `-1` is interpreted as `NOT 1`, and queries like `c++` trigger operator parsing.

**Why it happens:**
FTS5 has no "raw string" mode. Every string passed to `MATCH` is parsed as a query. The only official escape is to wrap each term in double quotes AND double up any internal double quotes. Most tutorials skip this because their test data is English-only.

**How to avoid:**
Ship an explicit `fts5_escape(query: str) -> str` utility in `retrieval/query.py` and **route 100% of user input through it**:
```python
def fts5_escape(query: str) -> str:
    """Convert arbitrary user input to a safe FTS5 phrase-or-OR query.
    Strategy: split on whitespace, quote each token, double internal quotes,
    drop empty tokens, return OR-joined."""
    tokens = [t for t in query.split() if t.strip()]
    if not tokens:
        return '""'  # matches nothing but does not error
    quoted = [f'"{t.replace(chr(34), chr(34)*2)}"' for t in tokens]
    return " OR ".join(quoted)
```
Then synonym expansion happens on the **raw** query, and the escape layer runs **last** before `MATCH`. Any reserved operator usage is thus user-facing behavior (documented) or neutralized (default).

Add a fuzzing test with a corpus of 50 adversarial inputs: `""`, `"`, `"a"b"`, `c++`, `json:`, `(foo`, `AND`, `NEAR("a" "b")`, `*`, `-1`, `a NOT b`, etc. Every one must return a structured result (even if empty), never an exception.

**Warning signs:**
- Eval harness logs `OperationalError: fts5: syntax error` for any input.
- First-time users report "my query crashed Claude Desktop".
- Tool call latency is bimodal: fast-path hits vs. exception-handler slow-path.

**Phase to address:** Phase 3 (Retrieval layer) — same PR that introduces synonym expansion. **Severity: BLOCKER for v0.1.0 — any crash on unescaped input breaks the LLM conversation loop.**

---

### Pitfall 3: Stale read-only SQLite handle after atomic rename

**What goes wrong:**
The build guide's section 8 describes atomic swap via POSIX `rename()`: `build-{ts}.db` → `index.db`. This is atomic **at the filesystem level** — but the server process has an **already-open file descriptor** pointing at the *old inode*. On Linux/macOS, the old file is unlinked from its directory entry but the fd stays valid, pinned to the now-deleted inode.

Consequence: the running server continues to read the **pre-swap** index indefinitely, with no error. New data is invisible. `functools.lru_cache` on `get_section_cached` now caches results from a zombie database. Worse, the `-wal` sidecar file may still be updated by checkpointing, but the checkpoint happens against the detached inode, and if any code path ever re-opens the path (e.g., WAL-index shared memory regeneration), SQLite can legitimately **replay an orphaned WAL against the fresh database file**, which is one of the known corruption modes the SQLite docs warn about.

**Why it happens:**
Developers assume "atomic rename" means "atomic cutover from the reader's perspective". It doesn't — only a fresh `sqlite3.connect()` sees the new inode. The read-only handle is "append-only consistent" from its own POV.

**How to avoid:**
1. **Do not hold a long-lived read-only handle across a swap.** Instead, open a fresh read-only connection per-request (cheap; SQLite connection open is ~microseconds) OR use a connection pool that can be invalidated atomically.
2. Publish a `build-version` sentinel in `ingestion_runs` AND write a `~/.cache/mcp-python-docs/CURRENT_HASH` file alongside `index.db`. Ingestion writes the new hash **after** rename. Serving reads the hash before every tool call (cheap; single-file stat + read). If the hash changed, `lru_cache.cache_clear()` and reopen the connection.
3. Alternatively, make the server **never overwrite a running index**: write to `index-{hash}.db`, and use a symlink `index.db → index-{hash}.db`. The symlink swap is atomic AND the old file stays on disk until the server restarts. This is more disk cost but simpler reasoning.
4. Document clearly: "for simplest correctness, restart the server after `build-index`". Make `build-index` print this instruction to stderr.

**Warning signs:**
- Tests pass locally but integration testing shows "new index not taking effect".
- Users report "I rebuilt with `--versions 3.14` but `list_versions` still says `3.12, 3.13`".
- Sporadic `database disk image is malformed` errors after a `build-index` run.

**Phase to address:** Phase 4 (Ingestion + atomic publish). **Severity: MAJOR — manifests in v0.1.0 timeframe if users ever rebuild without restarting.**

---

### Pitfall 4: `functools.lru_cache` is not invalidation-aware

**What goes wrong:**
The build guide section 12 uses `@lru_cache(maxsize=512)` on `get_section_cached(section_id: int)` and `resolve_symbol_cached(qualified_name: str, version: str) -> Symbol`. These caches are process-lifetime, keyed on the arguments only — they have **no knowledge** of the underlying DB. When the index is swapped (see Pitfall 3), every cached entry references stale data keyed by a `section_id` that might now point at a completely different section in the new index.

Additionally, `lru_cache` decorated functions are singletons on the module — they can't be reset per-request, and `cache_clear()` clobbers the whole cache unconditionally.

**Why it happens:**
`lru_cache` is the smallest-possible caching primitive. It was never designed for "data behind the cache changed underneath me". MCP stdio servers are long-running processes (the client may hold the process open for the entire session), so any mid-session data change exposes the trap.

**How to avoid:**
- **Key the cache on `(version, build_hash, id)`**, not just `id`. When the index swaps, `build_hash` changes, so old entries become unreachable (but still occupy slots until LRU evicts them). Combine with `.cache_clear()` when `build_hash` changes.
- Prefer an explicit cache wrapper over the decorator:
  ```python
  class VersionedCache:
      def __init__(self, maxsize: int):
          self._cache: dict = {}
          self._build_hash: str | None = None
          self._maxsize = maxsize
      def get(self, key, loader, current_hash):
          if current_hash != self._build_hash:
              self._cache.clear()
              self._build_hash = current_hash
          if key not in self._cache:
              if len(self._cache) >= self._maxsize:
                  self._cache.pop(next(iter(self._cache)))
              self._cache[key] = loader()
          return self._cache[key]
  ```
- Or accept the constraint: **document that `build-index` requires a server restart**, and keep `lru_cache` as-is. This is the guide's current intent; just make it explicit.

**Warning signs:**
- "I rebuilt the index but I'm still getting old results" user reports (this is the dominant symptom of both this pitfall and Pitfall 3).
- Memory usage grows over a session for no reason (cached stale entries never evicted because not accessed).

**Phase to address:** Phase 5 (Caching + polish). **Severity: MAJOR, degrades UX, manifests only after first rebuild.**

---

### Pitfall 5: `sphinx-build -b json` against CPython is not "just run it"

**What goes wrong:**
The guide says "download CPython source for the target version, run `sphinx-build -b json Doc/ build/json/`". In practice:

1. **CPython's `Doc/` requires its own virtualenv** — `Doc/requirements.txt` pins specific Sphinx versions, and CPython's `conf.py` imports `pydoctheme` (or, for 3.12+, the newer `python-docs-theme`) plus a handful of **custom extensions** under `Doc/tools/extensions/` (c_annotations, peg_highlight, pyspecific, etc.). Missing any of them crashes the build with a cryptic import error.
2. **Since Sphinx v7.2.0 there is an active regression** (issue #11615) where `sphinx-build -b json` crashes inside the `html-page-context` handler because the JSON builder doesn't set up the html page resource paths the HTML builder's event handlers assume. CPython's Sphinx version tracks the upstream and has been affected.
3. **CPython-specific directives produce nodes the JSON builder doesn't know how to serialize.** `versionadded`, `versionchanged`, `deprecated-removed`, `availability`, `audit-event`, `impl-detail` all expand to nodes from `pyspecific` — the JSON builder defaults to rendering them via the HTML translator, and if any custom node escapes without a JSON-compatible `visit_*` method, you get `NotImplementedError: Unknown node: <custom_node>`.
4. **`pending_xref` leakage** — if intersphinx resolution fails (e.g., offline builds), unresolved cross-references persist as `pending_xref` nodes, which blow up the JSON builder in multiple Sphinx versions.
5. **Full build memory** — a complete CPython Doc build uses ~2–3 GB resident memory and takes 3–10 minutes on an M-series Mac. Not a problem for a one-time ingest, but it **is** a problem for CI test matrices.

**Why it happens:**
CPython's docs pipeline is a bespoke build with custom extensions. It's designed to produce HTML at docs.python.org — the JSON builder is a side-of-desk feature that catches less CI love.

**How to avoid:**
1. **Create a dedicated ingest virtualenv** per CPython version. Pin Sphinx and install `Doc/requirements.txt` exactly. Do not use the `mcp-server-python-docs` runtime venv for builds.
2. **Test the JSON build against a pinned CPython tag** (e.g., `v3.12.7`, `v3.13.2`) in CI, not against `main`. Upstream regressions will sometimes break ingestion; pinning decouples us.
3. **Add an ingestion failure mode** that catches `NotImplementedError` from the JSON builder, logs the offending node type and document, and **continues**. A pitfall on page `foo.rst` should not kill the entire 200-document ingest. Track per-document success/failure in `ingestion_runs.notes`.
4. **Pre-validate** by running `sphinx-build -b json Doc/ tmp/` on the fixture CPython source as a prerequisite step in `validate-corpus`. If it fails, the tool exits with a clear error pointing at the upstream Sphinx/CPython mismatch, not a vague "ingest failed".
5. **Have the HTML fallback ready in code**, even though the guide defers it to v1.1. It's the only escape hatch if JSON builder breaks on a given CPython release.

**Warning signs:**
- `ModuleNotFoundError: No module named 'pydoctheme'` or `No module named 'python_docs_theme'`.
- `NotImplementedError: Unknown node: versionadded` / `pending_xref`.
- `sphinx-build` exits 0 but `build/json/` is empty (silent failure mode).
- Ingest works on Python 3.12 but fails on 3.13 (or vice versa) because of extension drift.

**Phase to address:** Phase 4 (Ingestion path) — Day 1 of that phase. **Severity: BLOCKER until resolved; the entire content path depends on it.**

---

### Pitfall 6: `sphobjinv` duplicate qualified names and role ambiguity

**What goes wrong:**
`objects.inv` is **not unique by `name`**. A single qualified name can legitimately appear with multiple roles — e.g., `open` exists as both `py:function` (the builtin) and `py:method` on various file-like classes, and `Path` is a `py:class` in `pathlib` AND a `py:class` redirect in `os.PathLike`. The schema's `UNIQUE(doc_set_id, qualified_name)` constraint in the `symbols` table will **silently drop** duplicates (via `INSERT OR IGNORE`) or **fail ingestion** (via plain `INSERT`), depending on which you pick.

Additionally:
- `sphobjinv` `DataObjStr` objects have a `priority` field — Python's inventory uses `1` for "primary" symbols, `-1` for hidden, others for ranked alternatives. The guide doesn't mention priority; ignoring it means you dedupe arbitrarily (first-seen wins), which randomly loses either the canonical or the variant.
- Some entries have `dispname == '-'`, meaning "display name == object name". Copying `dispname` directly produces a literal `-` in symbol results.
- The `uri` field uses `$` as a shorthand for "anchor == name", expanded by intersphinx consumers. Raw `sphobjinv` output contains the `$`. If you pass that to downstream tools without expansion, users see URIs like `library/asyncio-task.html#$` — broken.

**Why it happens:**
The Sphinx `objects.inv` format is documented but obscure. `sphobjinv`'s `Inventory.objects` exposes the raw records; most tutorials use it as a flat symbol list without reading the [syntax docs](https://sphobjinv.readthedocs.io/en/stable/syntax.html).

**How to avoid:**
1. **Change the uniqueness constraint** to `UNIQUE(doc_set_id, qualified_name, symbol_type)` — this reflects the Sphinx domain reality.
2. **Expand `$` in URIs** during ingestion:
   ```python
   uri = obj.uri.replace('$', obj.name)
   ```
3. **Use `dispname` only if ≠ `-`**, else fall back to `name`.
4. **Respect `priority`** — when multiple rows collide on `(doc_set_id, qualified_name)`, pick the one with highest priority. Store `priority` on the `symbols` table as an integer column so the ranker can use it as a tiebreaker.
5. **Log duplicates at ingest time**, not silently drop. If `asyncio.open_connection` appears twice, emit an INFO line and keep both if role differs. This is a research signal if duplicates ever explode.

**Warning signs:**
- `UNIQUE constraint failed: symbols.qualified_name, symbols.doc_set_id` during ingestion.
- Symbol lookup returns a result with `uri` containing `$`.
- `search_docs("open", kind="symbol")` returns `builtins.open` but never `os.open` because the first was `INSERT OR IGNORE`'d.

**Phase to address:** Phase 2 (Schema) + Phase 4 (Ingestion). **Severity: MAJOR — corrupts symbol corpus subtly.**

---

### Pitfall 7: Stdio stdout pollution from non-obvious sources

**What goes wrong:**
The guide covers `print()` and `logging` going to stdout. But in a real Python process, stdout gets polluted from **multiple non-obvious sources**:

1. **Native extensions** — `sphobjinv` uses `zlib` internally; some native libraries emit warnings via C's `stderr`, which in Python gets *re-captured* if you did `sys.stderr = sys.stdout` earlier, or not captured at all if C writes directly to fd 1. Any C-level `printf` from a linked library goes to fd 1 unconditionally and corrupts the protocol.
2. **`warnings` module** — `warnings.warn()` by default writes to `sys.stderr`, but if any dependency calls `warnings.simplefilter('always', ...)` with a custom showwarning that writes to stdout, the protocol stream corrupts.
3. **`atexit`-registered printers** — some libraries (notably profiling tools, some data-science libs) register `atexit` handlers that print summary statistics to stdout at process shutdown. For a long-running stdio server this doesn't affect serving, but Claude Desktop keeps the process open and `atexit` fires only on shutdown — yet shutdown happens **while Claude Desktop is still reading** the stream, and the final message the client sees is garbage.
4. **Background thread output** — `threading.excepthook` defaults to printing tracebacks to `sys.stderr`, but ambiguous configurations can write to stdout. If you spawn any background task (the guide doesn't, but Pydantic/httpx/anyio may), unhandled exceptions in those threads print tracebacks via the hook.
5. **`uvloop` / `asyncio` debug mode** — if `PYTHONDEVMODE=1` or `PYTHONASYNCIODEBUG=1` is set in the environment (some Claude Desktop debug configs do this!), asyncio prints slow-task warnings to **stdout** via `warnings`, polluting the stream.
6. **BOM on Windows** — on Windows, if `sys.stdout.encoding` is `cp1252` or similar, writing a Python str with non-ASCII characters (e.g., `é` in a docstring) can inject encoding error markers or BOMs that confuse the JSON-RPC framing.
7. **`print(..., file=sys.__stderr__)` vs `print(..., file=sys.stderr)`** — if any code path uses the former and your code redirected `sys.stderr` but not `sys.__stderr__`, the write bypasses your redirection.

**Why it happens:**
`print()` is not the only way to reach stdout. Anything that writes to fd 1 — native code, C extensions, misconfigured `warnings`, redirected file handles — corrupts the protocol.

**How to avoid:**
1. **Reassign `sys.stdout` to `sys.stderr` at import-time, before ANY third-party library loads:**
   ```python
   # server.py — FIRST lines
   import sys, os
   _real_stdout = sys.stdout
   sys.stdout = sys.stderr  # neutralize Python-level prints
   os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
   # …later, hand _real_stdout to the MCP protocol layer explicitly:
   ```
2. **Use `os.dup2()` to remap fd 1** (real stdout at the OS level) to point at fd 2, so even C-level `printf` is neutralized. Then pass the original fd to the MCP framer as a separate file object:
   ```python
   import os, sys
   real_stdout_fd = os.dup(1)   # save
   os.dup2(2, 1)                # fd 1 now writes to stderr
   real_stdout = os.fdopen(real_stdout_fd, "wb", buffering=0)
   # Feed real_stdout to the MCP stdio writer explicitly.
   ```
   This is the **only** hygiene strategy that actually neutralizes native-code writes.
3. **Set `warnings` to stderr explicitly and early**:
   ```python
   import warnings
   warnings.simplefilter("default")   # reset any third-party meddling
   logging.captureWarnings(True)      # route warnings through logging (→ stderr)
   ```
4. **Force `PYTHONUNBUFFERED=1`** for stdio mode to avoid partial-line buffering issues on unexpected shutdown.
5. **On Windows, force stdout binary mode and utf-8**: `sys.stdout.reconfigure(encoding="utf-8", newline="")`.
6. **Add a "stdout sentinel" test**: in CI, spawn the server as a subprocess, send it a tool request, capture stdout, and assert every byte parses as a valid JSON-RPC frame with no leading/trailing garbage.

**Warning signs:**
- Client disconnects mid-session with "parse error" but server logs show no crash.
- Works in isolation (`python -m mcp_server_python_docs serve < request.json`) but fails in Claude Desktop.
- Intermittent failures correlated with triggering rarely-exercised code paths (e.g., `PYTHONDEVMODE=1`).

**Phase to address:** Phase 1 (Foundation) — in `server.py` entry point, before ANY other import. **Severity: BLOCKER for reliability.**

---

### Pitfall 8: FastMCP lifespan runs more than expected, or never

**What goes wrong:**
FastMCP's `lifespan` is the standard way to wire up resources (DB connection, synonym table load, FTS5 capability check). The build guide's approach implicitly assumes lifespan fires once at startup and once at shutdown. This is **not what FastMCP does in several cases**:

1. **On stdio, the client (Claude Desktop, Cursor) spawns a NEW server process per session** — the server is not persistent. Lifespan runs on every process spawn, not once. This is fine by itself, but means any expensive startup work (FTS5 check, DB open, synonym load) is paid on every new conversation, adding 100–500ms of latency to the "first tool call".
2. **GitHub issue jlowin/fastmcp#1115 (open in 2026)**: in certain mount configurations, lifespan startup and shutdown sections are executed for every tool call instead of once. This bug is primarily an HTTP transport bug but has been reported for stdio when FastMCP is mixed with FastAPI mounting.
3. **Lifespan errors are swallowed**: if DB open fails inside lifespan, FastMCP's default behavior is to log the error and continue serving — the tool call later fails with a misleading "tool not found" or "NoneType has no attribute".
4. **`@lifespan` vs `lifespan=` parameter confusion**: FastMCP supports both a decorator AND a constructor parameter. Using both, or using the decorator but forgetting to pass the result to the constructor, silently no-ops.

**Why it happens:**
`lifespan` semantics differ between Starlette (one run per server), FastAPI (inherited from Starlette, one run per app), and FastMCP stdio mode (one run per process, which maps to one run per Claude Desktop conversation). Developers assume Starlette semantics.

**How to avoid:**
1. **Keep lifespan startup cheap** — open the DB connection, run the FTS5 check, done. Do NOT load synonyms from YAML in lifespan; pre-compile to a Python literal at build time and import it.
2. **Fail loudly on lifespan errors**: wrap lifespan body in `try/except`, log the full traceback to stderr, then `raise SystemExit(1)`. Claude Desktop shows the server as crashed (with a hint in the UI) instead of silently showing "no tools".
3. **Use the constructor parameter, not the decorator** — it's the path with better documentation:
   ```python
   @asynccontextmanager
   async def app_lifespan(server: FastMCP) -> AsyncIterator[AppState]:
       state = AppState(...)
       try:
           yield state
       finally:
           state.close()
   mcp = FastMCP("python-docs", lifespan=app_lifespan)
   ```
4. **Verify lifespan runs once per process** via a log line with a UUID; count invocations during Phase 1 smoke tests.

**Warning signs:**
- Latency on first tool call is 500ms+ but subsequent calls are 5ms.
- "Tool not found" errors with no traceback in stderr.
- Logs show lifespan startup printed multiple times in a single conversation.

**Phase to address:** Phase 1 (Foundation) — lifespan is set up when `server.py` is first written. **Severity: MAJOR.**

---

### Pitfall 9: FastMCP schema generation fails on Pydantic-incompatible type hints

**What goes wrong:**
FastMCP generates JSON schemas from `@mcp.tool()` function signatures using Pydantic. The guide's tool signatures include `Literal["auto", "page", "symbol", "section", "example"]`, `str | None`, and custom return types like `SearchDocsResult`. Common pitfalls:

1. **`Literal` with non-hashable values** or `Literal[None]` alone crashes schema generation.
2. **`str | None = None` works on Python 3.10+** (the target) but **fails on Python 3.9** (if someone accidentally targets 3.9 in `pyproject.toml`).
3. **Custom dataclasses without Pydantic adaptation** raise `PydanticSchemaGenerationError: Unable to generate pydantic-core schema for <class 'SearchDocsResult'>` — this is modelcontextprotocol/python-sdk issue #1131, still open as of 2026.
4. **`datetime`, `pathlib.Path`, `Decimal`** in the return schema serialize inconsistently between versions.
5. **Forward references** (`"SearchDocsResult"` as a string due to `from __future__ import annotations`) sometimes fail to resolve if the type is defined in a different module than the tool.
6. **Pydantic `Field` constraints** (`Field(..., ge=1, le=100)`) work, but **`Annotated[int, "description"]` with a bare string** does NOT — FastMCP needs `Annotated[int, Field(description="...")]`.
7. **Docstrings with `Args:` sections that don't match parameter names** silently lose descriptions — the LLM sees a stripped schema.

**Why it happens:**
Pydantic v2's schema inference is strict. FastMCP adds a layer of MCP-specific schema that must be compatible. Edge cases in the type system (unions, forward refs, custom types) each have specific incantations.

**How to avoid:**
1. **All tool return types MUST be Pydantic `BaseModel` subclasses**, not dataclasses. Make this a lint rule.
2. **Use `Annotated[T, Field(description=...)]`** for every parameter, so the schema has rich descriptions that the LLM can use.
3. **Pin `python-requires = ">=3.11"`** in `pyproject.toml` to avoid the 3.9 pipe-union bug.
4. **Add a schema-snapshot test**: serialize the generated schema with `mcp.get_tools()` and compare against a committed JSON file. Any drift breaks CI, any intentional change requires updating the snapshot.
5. **Avoid forward references** — define models in the same module as the tool, or import them unconditionally.
6. **Write docstrings in the Google style** that Pydantic understands: `Args:` with exact parameter names and type hints matching.

**Warning signs:**
- `PydanticSchemaGenerationError` at FastMCP startup (visible in stderr as a traceback from `@mcp.tool()` decoration).
- Tool list shows fewer tools than registered, because decoration silently failed for one.
- LLM complains "I don't know how to call this tool" because the schema lacks descriptions.

**Phase to address:** Phase 1 (Foundation) + every new tool added. **Severity: BLOCKER if it occurs.**

---

### Pitfall 10: Data files (`synonyms.yaml`) missing from the wheel

**What goes wrong:**
`data/synonyms.yaml` lives in the project root but outside `src/mcp_server_python_docs/`. With modern `pyproject.toml` (setuptools, hatchling, pdm-backend, or uv-native), **data files outside the package directory are not included in the wheel by default**. Symptoms:
- Local dev with `pip install -e .` works (file is still on disk).
- `uvx mcp-server-python-docs` fails on first run with `FileNotFoundError: data/synonyms.yaml`.
- Bug report says "works when I clone the repo but not when I uvx".

Further gotchas:
- `MANIFEST.in` only affects sdist, not wheel, unless `include-package-data = true` is set.
- With PEP 621 + setuptools, `[tool.setuptools.package-data]` keys are **package names**, not paths. `mcp_server_python_docs = ["data/*.yaml"]` expects `src/mcp_server_python_docs/data/synonyms.yaml`, not `project-root/data/synonyms.yaml`.
- `importlib.resources` is the 2026-correct way to load the file, but `open("data/synonyms.yaml")` (the naive approach) works in dev and fails in an installed wheel.

**Why it happens:**
Python packaging has three subtly-different mechanisms (MANIFEST.in, package-data, include-package-data) that do different things at different stages. Developers copy examples without understanding which applies.

**How to avoid:**
1. **Move the file into the package**: `src/mcp_server_python_docs/data/synonyms.yaml`. This is the only path-independent-of-build-backend solution.
2. **Load via `importlib.resources`**:
   ```python
   from importlib.resources import files
   synonym_text = files("mcp_server_python_docs.data").joinpath("synonyms.yaml").read_text()
   ```
3. **Add a smoke test that runs the built wheel**: `uv build`, `uv tool install ./dist/*.whl`, `uvx mcp-server-python-docs --help`, then invoke a tool that touches synonyms. Catch packaging regressions in CI.
4. **Verify before publishing**: `unzip -l dist/*.whl | grep synonyms.yaml` — a one-liner that never lies.

**Warning signs:**
- "It works on my machine" reports.
- `FileNotFoundError` on `data/synonyms.yaml` only from `uvx`-installed users.
- Tests pass locally but release builds fail integration.

**Phase to address:** Phase 6 (Packaging + release). **Severity: BLOCKER at release time.**

---

## Moderate Pitfalls

### Pitfall 11: Version URI collisions and anchor drift across Python versions

**What goes wrong:**
The same symbol exists in Python 3.12 and 3.13 with potentially different anchors. CPython docs sometimes rename sections between versions (e.g., `contextlib` restructured its section headings in 3.12 → 3.13). A URI like `library/contextlib.html#contextlib.asynccontextmanager` is stable across versions by convention, but section anchors like `#using-a-context-manager-as-a-function-decorator` can drift.

If the schema stores `sections.uri UNIQUE` without version in the URI, two `doc_sets` for 3.12 and 3.13 collide on the same section URI, and ingestion fails. The guide's schema scopes `sections.UNIQUE(document_id, anchor)` which is correct — BUT `sections.uri UNIQUE` (also in the schema) is wrong: different `document_id`s can legitimately have the same `uri` if you're not embedding the version.

Also: the `symbols.UNIQUE(doc_set_id, qualified_name)` is version-scoped, but a tool that calls `search_docs("asyncio.TaskGroup")` without `version` has to pick — and ordering is implementation-defined unless the guide explicitly says "default is latest version".

**How to avoid:**
- Make `sections.uri` embed the version: `docs://python/3.13/section/library/asyncio-task.html#asyncio.TaskGroup`. Drop the `UNIQUE` on the bare `uri` column, keep it on the composite `(document_id, anchor)`.
- Make the default version **explicit** (latest LTS = 3.13 in v0.1.0) and surface the defaulting decision in responses: `"resolved_version": "3.13"`.
- Add a cross-version probe in the stability test suite: "asyncio.gather exists in both 3.12 and 3.13, and both versions return a plausible URI".

**Warning signs:**
- `UNIQUE constraint failed: sections.uri` during multi-version ingest.
- `search_docs("asyncio.TaskGroup")` returns a 3.12 result for one user and 3.13 for another with no deterministic reason.

**Phase to address:** Phase 7 (Multi-version support). **Severity: MAJOR for multi-version correctness.**

---

### Pitfall 12: FastMCP async/sync mixing — don't call sync from async without `run_in_executor`

**What goes wrong:**
Tool handlers decorated with `@mcp.tool()` can be sync or async. If you write an async handler and then call `sqlite3.connect(...).execute(...)` directly, you **block the event loop** for the duration of the query. For a single-client stdio server this is usually fine, but:
- SQLite FTS5 queries on a few-MB index take 1–20ms each, which compounds with synonym expansion and ranker pipelines.
- Any other concurrent tool calls (FastMCP supports concurrent requests) get serialized behind the blocking call.
- `asyncio` slow-callback warnings fire with `PYTHONASYNCIODEBUG=1` and, depending on config, print to stdout (see Pitfall 7).

Inverse mistake: writing a **sync** handler that internally calls an async HTTP client. Some developers reach for `asyncio.run()` inside a sync handler, which raises `RuntimeError: asyncio.run() cannot be called from a running event loop` inside FastMCP's already-running loop.

**How to avoid:**
- **Go sync for v1**. All services touch SQLite only; sync handlers are simpler and correct for this workload. FastMCP auto-wraps sync handlers in `run_in_executor`.
- If you must go async: **wrap all sync DB calls** in `await asyncio.get_running_loop().run_in_executor(None, ...)`.
- **Never call `asyncio.run()` inside a tool handler**. Use `await` or stay sync.

**Warning signs:**
- `RuntimeError: asyncio.run() cannot be called from a running event loop`.
- Slow-callback warnings in debug mode.

**Phase to address:** Phase 3 (Services + retrieval). **Severity: MODERATE, mostly prevents future mistakes.**

---

### Pitfall 13: Missing `readOnlyHint` annotation confuses clients

**What goes wrong:**
MCP clients (Claude Desktop, Cursor) use tool annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) to decide whether to run a tool without confirmation. FastMCP's `ToolAnnotations` is how you declare them. `search_docs`, `get_docs`, and `list_versions` are all read-only + idempotent + closed-world (bounded by the local index). Without these hints, some clients may prompt the user for confirmation on every call, ruining UX.

**How to avoid:**
Set annotations explicitly on every tool:
```python
from mcp.types import ToolAnnotations
@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=True,
    idempotentHint=True,
    openWorldHint=False,
))
def search_docs(...): ...
```

**Warning signs:**
- Claude Desktop shows a confirmation prompt on every `search_docs` call.

**Phase to address:** Phase 1 (Foundation). **Severity: MINOR, but critical for polished UX.**

---

### Pitfall 14: Native exceptions in lifespan crash with no trace

**What goes wrong:**
If the FTS5 capability check raises `FTS5UnavailableError` inside lifespan, FastMCP bubbles the exception to the MCP transport, which translates it to a JSON-RPC error. Claude Desktop displays this as "Server failed to start: None" with no body. Users have no way to know the problem is FTS5.

**How to avoid:**
- Catch any `DocsServerError` subclass in lifespan, log the full message to stderr (**and** a file at `~/.cache/mcp-python-docs/last-error.log` for postmortem debugging), then re-raise as a `RuntimeError` with a human-readable message.
- Have the README's "Troubleshooting" section tell users where to look: `tail ~/.cache/mcp-python-docs/last-error.log`.
- For FTS5 specifically, suggest `pip install pysqlite3-binary` and `export SQLITE_BIN=pysqlite3` (or whatever env toggle the guide uses).

**Warning signs:**
- "Server failed to start" with no explanation.
- Users posting bug reports with "doesn't work" and no other details.

**Phase to address:** Phase 1 (Foundation) + Phase 8 (README & troubleshooting). **Severity: MAJOR for UX.**

---

### Pitfall 15: Windows path separators and home dir expansion

**What goes wrong:**
The guide references `~/.cache/mcp-python-docs/` (XDG-compliant on Linux). On Windows:
- `~` expands to `C:\Users\NAME`, not `C:\Users\NAME\AppData\Local`.
- `.cache` is a hidden dir convention foreign to Windows.
- Some antivirus tools quarantine SQLite journal files in `AppData\Local`.
- Path separators in logged URIs look like `library\asyncio-task.html` — downstream LLMs may treat backslashes as escapes.

**How to avoid:**
- Use `platformdirs` (pure Python, zero-dep, standardized XDG+Windows+macOS paths):
  ```python
  from platformdirs import user_cache_dir
  cache = Path(user_cache_dir("mcp-python-docs"))
  ```
  This gives `~/Library/Caches/mcp-python-docs` on macOS, `%LOCALAPPDATA%\mcp-python-docs` on Windows, `~/.cache/mcp-python-docs` on Linux.
- Normalize all URIs to forward slashes before storing:
  ```python
  uri = uri.replace(os.sep, "/")
  ```
- Test on Windows in CI (GitHub Actions `windows-latest`).

**Phase to address:** Phase 2 (Storage layer). **Severity: MAJOR if Windows is a target audience.**

---

### Pitfall 16: `uvx` stale cache: "I updated the server but it's still running the old version"

**What goes wrong:**
Users install via `uvx mcp-server-python-docs` in their Claude Desktop config. When you publish v0.1.1 with a bug fix, `uvx` (which is `uv tool run`) checks the cache first — if your version spec is `mcp-server-python-docs` (unpinned), uv caches the package and **does not re-check PyPI on every run**. The user runs the buggy version for days without knowing.

Documented in astral-sh/uv#16196: "uvx does not invalidate cache when source code changes". The workaround `@latest` (e.g., `uvx mcp-server-python-docs@latest`) forces a re-check, but adds 500ms–2s per start.

Worse: `uv cache clean` requires manual invocation, and most users don't know it exists.

**How to avoid:**
- README shows two install options: **stable** (`uvx mcp-server-python-docs`) and **always-latest** (`uvx mcp-server-python-docs@latest`), with tradeoffs explained.
- Tag every release on PyPI with semver. Bug-fix releases are `0.1.1`, `0.1.2`, etc. Users who want pinning can.
- Document `uv cache clean mcp-server-python-docs` in the troubleshooting section.
- Ship a `--version` flag that prints the installed version so users can confirm which they're running.

**Warning signs:**
- "I updated but the bug is still there" bug reports from users who aren't technical enough to rebuild their uv cache.

**Phase to address:** Phase 8 (README + release). **Severity: MODERATE.**

---

### Pitfall 17: Windows MSIX Claude Desktop config location gotcha

**What goes wrong:**
On Windows with the MSIX-packaged Claude Desktop, `Settings → Developer → Edit Config` opens `%APPDATA%\Claude\claude_desktop_config.json`, **but the app actually reads from a virtualized filesystem location**. Edits made via the button are silently ignored (GitHub issue anthropics/claude-code#26073).

This isn't our bug, but it's our user's pain when they install `mcp-server-python-docs` and it "doesn't work".

**How to avoid:**
- README "Troubleshooting" section lists known Claude Desktop bugs and workarounds by OS.
- Provide a copy-paste config snippet that works for the canonical macOS/Linux path AND a note pointing Windows users to the upstream bug.
- Consider shipping a `mcp-server-python-docs doctor` subcommand that prints diagnostics (FTS5 check, index presence, config file candidates).

**Phase to address:** Phase 8 (README). **Severity: MINOR but amplifies bug reports.**

---

### Pitfall 18: `BrokenPipeError` / SIGPIPE on client disconnect

**What goes wrong:**
When Claude Desktop closes the conversation mid-stream (user cancels, app force-quits, client OOMs), the server writes to a closed pipe. Python does **not** ignore SIGPIPE by default — it converts it to `BrokenPipeError`, which propagates up if unhandled. If this happens inside a tool call writing output, you get a traceback to stderr and (worse) the `atexit` handlers run AFTER the pipe is broken, potentially printing more tracebacks and filling logs.

Additionally: Python 3 has a longstanding "bug" where the interpreter tries to flush stdout on exit, which raises `BrokenPipeError: [Errno 32] Broken pipe` as the final message before exit. This clutters logs without causing harm, but looks alarming.

**How to avoid:**
1. In `server.py` entry point, install a SIGPIPE handler for graceful exit:
   ```python
   import signal
   signal.signal(signal.SIGPIPE, signal.SIG_DFL)  # clean exit, no traceback
   ```
   Note: this is **incompatible with arbitrary background threads** — the first thread to write to a broken pipe dies silently, which is fine for stdio but not for web servers. Since MCP stdio has one connection, it's safe.
2. Wrap the top-level MCP loop in a `try/except BrokenPipeError` that logs "client disconnected" and exits cleanly.
3. Flush stdout at exit manually to avoid the interpreter's own BrokenPipeError on close:
   ```python
   import atexit
   def _final_flush():
       try:
           sys.stdout.flush()
       except BrokenPipeError:
           pass
   atexit.register(_final_flush)
   ```

**Warning signs:**
- Stderr logs full of "BrokenPipeError: [Errno 32] Broken pipe" tracebacks.
- Claude Desktop shows the server as "crashed" when the user was just closing the conversation.

**Phase to address:** Phase 1 (Foundation). **Severity: MODERATE.**

---

### Pitfall 19: Synonym table expansion explodes FTS5 query into MATCH OR bomb

**What goes wrong:**
Naive synonym expansion turns `"parallel"` into `"parallel OR concurrent OR multiprocessing OR threading OR asyncio OR concurrent.futures"`. For 10+ synonym groups combined with user terms, you can end up with a MATCH expression containing 50–100 OR branches, which:
- FTS5 handles but gets slow (BM25 re-scoring scales with result-set size per branch).
- Ranker column weights lose meaning because everything matches.
- The top result becomes whatever common term appears in the most pages, not what the user asked about.
- Query parse errors if any branch contains a character that requires escaping (see Pitfall 2).

**How to avoid:**
- Cap expansion at **3 additional terms** per user token, not the full synonym list.
- Use synonym expansion to **boost** via a second query stage, not to union into the same MATCH call. Issue the raw query first; if result count < 3, re-issue with expansion.
- Unit test the actual FTS5 query string produced for each of the ~100 synonym entries; fail if any exceeds 500 characters.

**Warning signs:**
- Query latency p95 > 100ms for concept queries.
- "Top result is always `os.path`" (or some other high-frequency page).

**Phase to address:** Phase 3 (Retrieval + synonym table). **Severity: MODERATE.**

---

### Pitfall 20: No `--version` flag, no `doctor` subcommand

**What goes wrong:**
Every bug report starts with "which version are you running?" and the user has to `pip show` the tool. A well-designed CLI has:
- `--version` that prints version and dependencies.
- `doctor` / `diagnose` that prints: Python version, sqlite3 version, FTS5 available, index presence, index build hash, expected vs. actual DB schema version, config file candidates.
- Logs the output of `doctor` to stderr on every server startup (at DEBUG level).

Without this, "first 5 minutes" debugging is painful: the user has no self-service path and opens issues for things they could diagnose themselves.

**How to avoid:**
Ship the `doctor` subcommand in v0.1.0. The code is ~40 lines. It pays for itself at the first bug report.

**Phase to address:** Phase 8 (Release + polish). **Severity: MINOR but high-ROI.**

---

## Technical Debt Patterns

Shortcuts that seem reasonable but create long-term problems.

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Using `unicode61` without `tokenchars` | Default, one less option to configure | Identifier search silently broken; rebuild entire index to fix | **Never** — set `tokenchars '._'` from day one |
| Loading `synonyms.yaml` via `open("data/...")` | Works in dev | Breaks in installed wheel | **Never** — use `importlib.resources` from the start |
| Naive `query.replace('"', '""')` escape | Works for 90% of cases | 10% of cases crash FTS5 with `syntax error` | **Never** — always tokenize + quote-wrap |
| `@lru_cache` without version keying | One decorator, done | Stale after atomic swap | Only if `build-index` always prints "restart required" and server never reopens mid-life |
| Using `sqlite3.connect(path)` without `?mode=ro` for serving | Simpler | Accidental writes possible from a tool bug | **Never** — always RO for serving |
| Sync handlers everywhere | Simpler to reason about | May block if a future tool does IO | Fine for v1 — revisit if adding HTTP transport |
| Store version inside URIs, not in schema column | No schema change needed | URIs are opaque to ORM queries | **Never** — use a `doc_set_id` FK |
| Not implementing `doctor` command | Skip ~40 LOC | Every bug report requires 5 round-trips of diagnosis | Ship it in v0.1.0 |
| Using default `atexit` behavior | Zero config | Random stdout writes on unclean shutdown | **Never** — neutralize fd 1 at process start |
| Using default Claude Desktop config path in README without OS tabs | One snippet instead of three | Windows users are stuck | Fine for Linux/macOS-only v0 release; add Windows at v0.1 |

---

## Integration Gotchas

Common mistakes when connecting to external services and clients.

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| Claude Desktop (macOS) | Edit `~/Library/Application Support/Claude/claude_desktop_config.json` and **close** Claude (not Cmd+Q) | Full quit via `Cmd+Q` — menu close leaves the process running with old config |
| Claude Desktop (Windows) | Edit config via UI "Edit Config" button on MSIX install | Known broken; edit the file directly at the real (non-virtualized) path |
| Cursor | Forget config location | `~/.cursor/mcp.json` — Cursor picks up changes automatically, no restart needed (unlike Claude Desktop) |
| `uvx` on LM Studio | Assume `uvx` works everywhere | LM Studio has a shorter stdio handshake timeout; `uvx` bootstrap exceeds it. Document "direct Python path" alternative for LM Studio users |
| `npx`-style installers | Expect `-y` flag behavior | `uvx` does not prompt on first run (unlike `npx` without `-y`), but if PyPI index is slow, the client's stdio handshake times out silently |
| FastMCP stdio on macOS | Assume codesigning isn't needed | Gatekeeper may quarantine a Python binary spawned by a downloaded app; test on a fresh Mac |
| FTS5 extension | Assume all distro `python` has FTS5 | Some Linux distros (Alpine, older Debian) ship Python with FTS5 missing. Startup capability check is non-optional |
| PyPI upload | Upload with `twine` and manual token | Use Trusted Publishing via GitHub Actions + attestation bundles (2026 standard, no token rotation pain) |
| Intersphinx/`sphobjinv` | Use `inv.objects` as a flat list | It's a list of `DataObjStr`; read `priority`, handle `$` URI shorthand, and respect duplicates-by-role |
| Sphinx JSON builder | Run against CPython `main` | Pin to a released CPython tag; `main` can break the JSON builder at any time |

---

## Performance Traps

Patterns that work at small scale but fail as usage grows.

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| FTS5 query with 50+ OR branches from synonym expansion | p95 latency > 100ms | Cap expansion depth, use a two-stage fallback | Triggered by any concept query with 5+ synonyms |
| BM25 ranking without column weighting | Low-signal pages dominate results | Use `sections_fts.heading` with weight 10, `content_text` with weight 3 in `bm25()` | Triggered as soon as content_text pages have repeated terms |
| `lru_cache(maxsize=512)` with unbounded keyspace | Memory grows without limit on long sessions | Small maxsize + per-version keying | ~10K requests in a session |
| Opening new SQLite connection per request without pooling | Overhead on first call of new conversation | Connection pool with 1 RO connection, reused | Only a problem if conversation is very chatty (~100+ calls) |
| Loading synonyms from YAML on every tool call | 50ms extra on every call | Load once at startup, store in module global | At second tool call |
| Full page fetch when section anchor was available | 8K tokens wasted per call | Always prefer sections when anchor present | Every call that returns a full page |
| No truncation on `content_text` | Pages >50K chars overflow LLM context | Hard `max_chars` with truncation flag | When user requests `typing` page (very large) |
| Sphinx JSON full rebuild every ingest | 3–10 minutes per version, 2–3 GB memory | Pin CPython source, cache between runs, use a Docker layer | Every ingest if CI runs this |
| `SELECT * FROM symbols WHERE qualified_name LIKE 'prefix%'` without index | Full table scan | Index on `normalized_name` | ~1K symbols, ~100ms scan |
| Token-wasteful JSON responses | 40-60% token overhead vs CSV | Consider CSV option for bulk results | Every tool response |

---

## Security Mistakes

Domain-specific security issues beyond general software security.

| Mistake | Risk | Prevention |
|---------|------|------------|
| FTS5 query injection from LLM-generated input | Crash, DoS via pathological regex, potential data exfil if results are templated into error messages | Escape all user input via `fts5_escape()`; never interpolate into error strings |
| Path traversal via `slug` in `get_docs(slug=...)` | Read any file on disk if slug is passed unchecked to `open()` | Never `open()` from `slug`; always look up in SQLite; reject any slug with `..` or absolute path |
| LLM-generated `version` bypasses index scope | Query hits a doc_set not meant to exist, returns wrong-version results | Validate `version` against `list_versions()` set; raise `VersionNotFoundError` on miss |
| Writing to `~/.cache` without validating path | If `$HOME` is manipulated (CI, shared hosts), cache could write outside intended dir | Always canonicalize via `platformdirs`, reject non-absolute paths |
| Serving symlinks in `~/.cache` | A malicious `index.db` symlink could point outside the cache dir | Resolve symlinks during `build-index` publish, refuse if target leaves cache dir |
| Ingestion fetches arbitrary URLs | `build-index --source https://evil.com/` could download hostile content | Whitelist only known CPython URLs (`docs.python.org/{version}/`) in v1 |
| FTS5 queries used for pattern matching on user paths | Possible bypass of access controls | No access controls in v1; read-only server is the whole safety model |
| Credentials in environment leaked via `doctor` | `os.environ` dump could expose secrets | `doctor` prints allow-listed keys only |
| Loading `synonyms.yaml` with `yaml.load` instead of `yaml.safe_load` | YAML deserialization attacks | Use `yaml.safe_load` — the YAML file is shipped with the package but still |
| Trust PyPI without attestations | Supply chain attack vector | Use Trusted Publishing with attestations; document verification for downstream |

---

## UX Pitfalls

Common user experience mistakes in this domain.

| Pitfall | User Impact | Better Approach |
|---------|-------------|-----------------|
| No feedback when index doesn't exist | Silent crash on first tool call | Print clear instructions to stderr + return a structured MCP error with the build command to run |
| `build-index` prints progress to stdout | Corrupts stdio if invoked in wrong context | Always print to stderr; use tqdm with `file=sys.stderr` |
| First `build-index` takes 10 minutes with no output | User thinks it's hung | Progress bar per stage (download, parse, index, publish) |
| README only shows macOS config path | Windows/Linux users can't follow | Tabs/sections for each OS |
| No `--version` flag | Bug reports without version info | Add `--version` + doctor subcommand |
| "FTS5 not available" error with no fix | User stuck | Include the `pysqlite3-binary` install instruction in the error message itself |
| Symbol not found → "Symbol not found" | LLM retries same query | Return nearest matches by normalized name + Levenshtein distance |
| Concept query returns 0 results | LLM has nothing to work with | Always return top 3 by BM25 even if score is low; surface score in response for LLM to self-evaluate |
| Tool responses include duplicate fields | Token waste | Use CSV or compact JSON for tabular tool results; document the shape explicitly |
| No indication of which version results are from | LLM may mix 3.12 and 3.13 info | Every result explicitly tagged with `version` field |
| Config file path instructions assume root | Users read from `~/` | Use `$HOME`/`~` explicitly and expand in docs |
| Error messages swallowed by Claude Desktop | User sees "Server crashed" with no info | Write all errors to a file log, reference it in the README troubleshooting |

---

## "Looks Done But Isn't" Checklist

Things that appear complete but are missing critical pieces.

- [ ] **FTS5 tokenizer set to `tokenchars '._'`** — verify with `SELECT * FROM sections_fts WHERE sections_fts MATCH 'asyncio.TaskGroup'` returning results for a fixture.
- [ ] **FTS5 query escape function covers adversarial inputs** — 50-input fuzz suite passes.
- [ ] **`data/synonyms.yaml` included in wheel** — `unzip -l dist/*.whl | grep synonyms.yaml` shows it.
- [ ] **stdout at fd 1 is redirected to stderr** — subprocess test captures ONLY valid JSON-RPC on stdout.
- [ ] **FastMCP tool annotations set** — `readOnlyHint=True`, `idempotentHint=True`, `openWorldHint=False` on every tool.
- [ ] **FTS5 capability check fails with actionable message** — test passes with `pysqlite3` not installed.
- [ ] **Schema uses composite URI uniqueness scoped by version** — `UNIQUE(document_id, anchor)`, not `UNIQUE(uri)`.
- [ ] **Symbol lookup handles duplicate qualified names** — test that `open` exists as both function and method.
- [ ] **CPython Sphinx JSON ingestion pinned to released version** — not `main`.
- [ ] **Ingest tolerates per-document failures** — a broken page doesn't abort the run.
- [ ] **Server restart required after `build-index` is documented** — or cache invalidation is implemented.
- [ ] **Integration test against Claude Desktop AND Cursor** — manual, but checklisted.
- [ ] **`--version` flag + `doctor` subcommand** — bug reports self-diagnose.
- [ ] **Windows path handling via `platformdirs`** — tested in CI or explicitly marked Linux/macOS-only.
- [ ] **BrokenPipeError handling** — client disconnect doesn't log a traceback.
- [ ] **README troubleshooting covers: FTS5 missing, uvx cache stale, Windows MSIX bug, lifespan errors**.
- [ ] **Pydantic schema generation tested** — schema snapshot in tests/.
- [ ] **PyPI Trusted Publishing configured with attestations** — not a plaintext token.
- [ ] **Version selection when `version=None` is documented and deterministic** — defaults to latest.
- [ ] **stdout / stderr separation works on Windows** — `sys.stdout.reconfigure(encoding="utf-8")`.

---

## Recovery Strategies

When pitfalls occur despite prevention, how to recover.

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Tokenizer wrong (`unicode61` without `tokenchars`) | MEDIUM | Change schema, rebuild index (`build-index --rebuild`); ship as patch release; users must re-run `build-index` |
| FTS5 query crash on user input | LOW | Hotfix `fts5_escape()`, patch release, cache clean; no data migration |
| Stale cache after swap | LOW | Server restart; next release invalidates caches on hash change |
| `synonyms.yaml` missing from wheel | MEDIUM | Patch release with correct packaging; yank broken version |
| Sphinx build breaks on new CPython | HIGH | Pin older CPython tag, wait for upstream fix, fall back to HTML scraper if needed |
| `sphobjinv` duplicates crash ingest | LOW | Migrate to composite key, rebuild; yank broken index format |
| Stdout pollution detected post-release | MEDIUM | Hotfix with `os.dup2()` redirection, patch release; affected users must reinstall |
| FastMCP lifespan not running | LOW | Switch from `@lifespan` decorator to `lifespan=` parameter |
| Schema generation fails on new tool | LOW | Rewrite tool signature with `Annotated[T, Field(...)]`; unit test snapshot |
| Multi-version URI collision | MEDIUM | Schema migration, rebuild; maintain backward-compat view via redirects table |
| Index corruption from WAL orphan | HIGH | Delete `index.db`, `index.db-wal`, `index.db-shm`; rebuild from scratch |
| Claude Desktop MSIX config bug | LOW (upstream) | Document the workaround in README; can't fix from our side |
| `uvx` stale cache | LOW | `uv cache clean mcp-server-python-docs`; document `@latest` suffix |

---

## Pitfall-to-Phase Mapping

How roadmap phases should address these pitfalls. (Phases are suggested based on a natural order; the roadmap agent will fit these to actual phase names.)

| # | Pitfall | Prevention Phase | Verification |
|---|---------|------------------|--------------|
| 1 | `unicode61 porter` shreds identifiers | Phase 2 (Schema + Storage) | Fixture test: `SELECT ... MATCH 'asyncio.TaskGroup'` returns ≥1 row |
| 2 | FTS5 query injection | Phase 3 (Retrieval) | 50-input adversarial fuzz suite passes; no `OperationalError` ever |
| 3 | Stale RO handle after atomic rename | Phase 4 (Ingestion + publish) | End-to-end test: build, swap, next request reads new data; hash-gate fires |
| 4 | `lru_cache` not invalidation-aware | Phase 5 (Caching + polish) | Test: mid-session swap + cache access returns new data |
| 5 | CPython Sphinx JSON build breaks | Phase 4 (Ingestion path) | CI matrix: pinned CPython tag for each target version; per-document failure isolation |
| 6 | `sphobjinv` duplicate names / `$` URIs | Phase 2 + Phase 4 | Test: `open` exists as both roles; no literal `$` in stored URIs |
| 7 | Stdout pollution from non-obvious sources | Phase 1 (Foundation) | Subprocess test captures only JSON-RPC on stdout |
| 8 | FastMCP lifespan semantics | Phase 1 (Foundation) | Count log line: lifespan startup runs exactly once per process |
| 9 | Pydantic schema generation | Phase 1 (Foundation) + ongoing | Schema snapshot test committed, diff in CI on drift |
| 10 | Data files missing from wheel | Phase 6 (Packaging) | CI step: `unzip -l dist/*.whl` includes `synonyms.yaml` |
| 11 | Multi-version URI collisions | Phase 7 (Multi-version) | Test: 3.12 and 3.13 both ingest cleanly, cross-version query works |
| 12 | Async/sync mixing | Phase 3 (Services) | Code review gate: all handlers sync for v1; `run_in_executor` if async |
| 13 | Missing `readOnlyHint` | Phase 1 (Foundation) | Tool introspection test asserts annotations present |
| 14 | Lifespan errors have no trace | Phase 1 + Phase 8 | Test: simulate FTS5 missing; stderr contains full traceback + actionable message |
| 15 | Windows path handling | Phase 2 (Storage) | CI: `windows-latest` runs smoke tests |
| 16 | `uvx` stale cache | Phase 8 (Release + README) | README has the `@latest` / `cache clean` workaround |
| 17 | Windows MSIX Claude Desktop bug | Phase 8 (README troubleshooting) | README troubleshooting section referenced by the `doctor` command |
| 18 | `BrokenPipeError` on disconnect | Phase 1 (Foundation) | Test: subprocess closed mid-write; exit clean, no traceback |
| 19 | Synonym expansion OR bomb | Phase 3 (Retrieval) | Test: every synonym entry produces <500-char MATCH expression |
| 20 | No `--version` / `doctor` | Phase 8 (Release + polish) | Both commands present; `doctor` output captured in README |

---

## Severity Summary

| Severity | Count | Pitfalls |
|----------|-------|----------|
| **Blocker** (must fix before ship) | 7 | #1 tokenizer, #2 FTS5 injection, #5 Sphinx JSON build, #7 stdout pollution, #9 schema generation (if occurs), #10 wheel data files, #14 lifespan error visibility |
| **Major** (significant UX/correctness) | 8 | #3 stale handle, #4 lru_cache, #6 sphobjinv duplicates, #8 lifespan semantics, #11 multi-version URIs, #14 lifespan errors, #15 Windows paths, #19 synonym explosion |
| **Moderate** (papercuts) | 3 | #12 async/sync, #16 uvx stale, #18 BrokenPipeError |
| **Minor** (polish) | 2 | #13 tool annotations, #17 MSIX, #20 `doctor` |

---

## Sources

### Official documentation (HIGH confidence)
- [SQLite FTS5 Extension](https://www.sqlite.org/fts5.html) — tokenizer options, query syntax, external content
- [SQLite URI Filenames](https://sqlite.org/uri.html) — `mode=ro` for read-only, SHM/WAL locking semantics
- [SQLite WAL](https://sqlite.org/wal.html) — atomic commit, shared-memory index, stale cache warnings
- [SQLite "How To Corrupt" docs](https://sqlite.org/howtocorrupt.html) — orphaned WAL, rename-after-unlink warnings
- [Python sqlite3 module](https://docs.python.org/3/library/sqlite3.html) — URI connect, PRAGMA, bind semantics
- [FastMCP docs via Context7 `/prefecthq/fastmcp`](https://gofastmcp.com/) — lifespan, stdio transport, Context injection, ToolAnnotations
- [sphobjinv docs](https://sphobjinv.readthedocs.io/en/stable/) — `DataObjStr` structure, `$` URI shorthand, priority field
- [CPython Doc/Makefile & Doc/README.rst](https://github.com/python/cpython/tree/main/Doc) — CPython Sphinx build requirements, pydoctheme/python-docs-theme
- [Python Developer's Guide: Building the docs](https://devguide.python.org/documentation/start-documenting/) — venv, requirements, tools/extensions
- [Python Packaging User Guide: pyproject.toml / package-data](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/) — wheel vs sdist, MANIFEST.in vs package-data
- [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/) — 2026 norm, attestations, no tokens
- [PyPI Attestations v1](https://docs.pypi.org/attestations/publish/v1/) — Trusted Publisher attestation bundles
- [platformdirs](https://pypi.org/project/platformdirs/) — cross-platform cache dir
- [MCP Debugging docs](https://modelcontextprotocol.io/docs/tools/debugging) — stderr logging, DevTools inspection

### Upstream issues + regressions (HIGH confidence, specific bugs)
- [sphinx-doc/sphinx#11615 — `sphinx-build -b json` fails in sphinx.builders.html since v7.2.0](https://github.com/sphinx-doc/sphinx/issues/11615)
- [sphinx-doc/sphinx#9240 — Unknown node: `pending_xref_condition`](https://github.com/sphinx-doc/sphinx/issues/9240)
- [sphinx-doc/sphinx#6166 — Unknown node: pending_xref recurring](https://github.com/sphinx-doc/sphinx/issues/6166)
- [jlowin/fastmcp#1115 — Lifespan executed for every tool call](https://github.com/jlowin/fastmcp/issues/1115)
- [jlowin/fastmcp#775 — fastmcp uses lifespan, but lifespan does not take effect](https://github.com/jlowin/fastmcp/issues/775)
- [jlowin/fastmcp#2012 — Logging in lifespan never emitted](https://github.com/jlowin/fastmcp/issues/2012)
- [modelcontextprotocol/python-sdk#1131 — Pydantic schema generation fails for non-standard output types](https://github.com/modelcontextprotocol/python-sdk/issues/1131)
- [astral-sh/uv#16196 — uvx does not invalidate cache when source code changes](https://github.com/astral-sh/uv/issues/16196)
- [anthropics/claude-code#26073 — Windows MSIX "Edit Config" opens wrong file](https://github.com/anthropics/claude-code/issues/26073)
- [anthropics/claude-code#31864 — Claude Desktop auto-update silent MCP conflict](https://github.com/anthropics/claude-code/issues/31864)
- [SQLite Forum — FTS5 syntax error with punctuation / best practices for invalid symbols](https://sqlite.org/forum/forumpost/576d6cc2d2)
- [SQLite Forum — FTS5 External Content Update Statement doc issue](https://sqlite.org/forum/info/8ecb8f7b27953a1f8c084b941a854f4889a9eb56fec67d98c83174feba0bcc58)
- [simonw/sqlite-utils#246 — Escaping FTS search strings](https://github.com/simonw/sqlite-utils/issues/246)
- [simonw/datasette#651 — fts5 syntax error when using punctuation](https://github.com/simonw/datasette/issues/651)

### Community posts and guides (MEDIUM confidence, verified against official docs where possible)
- [BigData Boutique — Building MCP Servers with FastMCP: 7 Mistakes to Avoid](https://bigdataboutique.com/blog/building-mcp-servers-with-fastmcp-7-mistakes-to-avoid) — readOnlyHint, safe defaults, schema docs
- [Nearform — Implementing MCP: Tips, tricks and pitfalls](https://nearform.com/digital-community/implementing-model-context-protocol-mcp-tips-tricks-and-pitfalls/) — stdio hygiene field report
- [audrey.feldroy.com — SQLite FTS5 Tokenizers: unicode61 and ascii](https://audrey.feldroy.com/articles/2025-01-13-SQLite-FTS5-Tokenizers-unicode61-and-ascii) — tokenchars gotcha walkthrough
- [blog.haroldadmin.com — Escape your Full Text Search queries](https://blog.haroldadmin.com/posts/escape-fts-queries) — escape strategies
- [runebook.dev — Don't Crash Your Server: Graceful SIGPIPE Handling in Python](https://runebook.dev/en/docs/python/library/signal/note-on-sigpipe) — SIGPIPE semantics
- [chiark.greenend.org.uk — Python SIGPIPE handling](https://www.chiark.greenend.org.uk/~cjwatson/blog/python-sigpipe.html) — authoritative Python SIGPIPE background
- [Simon Willison — Enabling WAL mode for SQLite database files](https://til.simonwillison.net/sqlite/enabling-wal-mode) — WAL + Python specifics
- [charlesleifer.com — Going Fast with SQLite and Python](https://charlesleifer.com/blog/going-fast-with-sqlite-and-python/) — connection patterns, WAL
- [jwodder.github.io/kbits — Common Python Packaging Mistakes](https://jwodder.github.io/kbits/posts/pypkg-mistakes/) — MANIFEST.in vs package-data
- [mcpplaygroundonline.com — Complete Guide to MCP Config Files](https://mcpplaygroundonline.com/blog/complete-guide-mcp-config-files-claude-desktop-cursor-lovable) — client-specific paths
- [agenticmarket.dev — MCP Server Not Working: Troubleshooting Guide](https://agenticmarket.dev/blog/mcp-server-not-working) — "no output" symptoms, full quit requirement

### Training-data-derived knowledge (flagged LOW where not otherwise verified)
- Python `atexit` semantics and thread interactions — HIGH (Python official docs)
- `functools.lru_cache` limitations for cache invalidation — HIGH (Python official docs)
- POSIX `rename()` atomicity and fd-after-unlink semantics — HIGH (POSIX spec)
- `importlib.resources` vs `open()` in installed wheels — HIGH (packaging guide)
- Pydantic v2 schema generation for discriminated unions and Literal types — MEDIUM (Pydantic docs, cross-checked)

---
*Pitfalls research for: Python MCP stdio retrieval server over Sphinx-indexed documentation (SQLite + FTS5 + sphobjinv)*
*Researched: 2026-04-15*
