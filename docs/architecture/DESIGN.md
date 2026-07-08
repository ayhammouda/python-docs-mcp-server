# Design: The Eight-Layer Architecture

- **Status:** Living document (v1)
- **Date:** 2026-07-08
- **Scope:** `python-docs-mcp-server` runtime and build-time architecture
- **Roadmap ref:** `STRATEGIC-ROADMAP-2026-05-29.md` §2.7, §4 (v0.5.0 table)

## Purpose

This document is the entry point for anyone adopting, auditing, or extending
`python-docs-mcp-server`. It ties together the Architecture Decision Records
(ADRs) that exist for this project and describes, layer by layer, how a
documentation query travels from an MCP client to canonical CPython or PyPI
source data and back.

It records existing decisions. It makes no new ones. Where an ADR is
ratified, this document links it and summarizes its Decision Outcome without
contradicting it. Where no ADR exists yet, this document describes shipped
behavior from the code, at file and line granularity, so the description can
be checked against `src/` directly.

## Document-level gap statement

> **ADR-002 through ADR-005 (ingestion, storage, retrieval, budget) do not
> exist yet.** They are scoped to v0.4.0 of the roadmap
> (`STRATEGIC-ROADMAP-2026-05-29.md` §4, "v0.4.0 — Phase 10 + Phase 11" table:
> "ADRs 2–5 | Ingestion, Storage, Retrieval, Budget"), which has not shipped
> as of this document's date. The four corresponding sections below (§3
> Ingestion, §4 Storage, §5 Retrieval, §6 Budget) describe **shipped code
> behavior**, verified against `src/` at the file:line level cited in each
> section. They are not a substitute for those ADRs and do not carry the same
> authority: nothing in those four sections should be read as a locked
> architectural decision, only as an accurate account of what the code does
> today. If code and this document diverge after future changes, the code is
> authoritative and this document is stale until updated. Each of those four
> sections repeats this note locally.

## Architectural principle behind this document

Principle 2.7 of the roadmap ("Layered design with stable contracts",
`STRATEGIC-ROADMAP-2026-05-29.md` §2) defines eight layers in a fixed
canonical order, first enumerated for this purpose in
[ADR-006](./ADR-006-serialization.md#layer-contract-principle-27) (lines
80–82):

1. Source connector
2. Ingestion
3. Storage
4. Retrieval
5. Budget
6. Serializer
7. Cache
8. Transport

Four of the eight layers have a ratified ADR as of this document's date:

| Layer | ADR | Status |
|---|---|---|
| 1. Source connector | [ADR-001](./ADR-001-source-adapters.md) | Accepted |
| 6. Serializer | [ADR-006](./ADR-006-serialization.md) | Accepted |
| 7. Cache | [ADR-007](./ADR-007-cache.md) | Accepted |
| 8. Transport | [ADR-008](./ADR-008-transport.md) | Accepted |

The remaining four (ingestion, storage, retrieval, budget) are the subject of
the gap statement above.

## Request-flow overview

Two flows exercise these layers: an offline **build flow** that populates the
local index, and an online **serve flow** that answers MCP tool calls against
that index. They are decoupled — the serve flow never reaches the network
except for the one documented exception in the source-connector layer
(`lookup_package_docs`, see §2).

**Build flow** (`python-docs-mcp-server build-index`, implemented in
[`src/mcp_server_python_docs/__main__.py`](../../src/mcp_server_python_docs/__main__.py)):
source connector → ingestion → storage, followed by a publish step that
atomically swaps the newly built database into place.

**Serve flow** (`python-docs-mcp-server serve`, same file): transport hands a
JSON-RPC tool call to one of six MCP tools defined in
[`src/mcp_server_python_docs/server.py`](../../src/mcp_server_python_docs/server.py)
(`search_docs`, `get_docs`, `lookup_package_docs`, `list_versions`,
`detect_python_version`, `compare_versions`). Each structured tool delegates
to a service (`SearchService`, `ContentService`, `CompareService`, ...) that
reads the storage layer, in the `get_docs` and `compare_versions` cases via
retrieval and budget, and returns a result model. The cache layer sits
alongside content and retrieval to short-circuit repeated work: a persistent
value cache can answer a repeat `get_docs` call before storage is touched at
all, and in-memory LRU caches skip repeated SQLite lookups while a fresh
result is being built. The serializer layer turns the result model into the
wire payload, and the transport layer writes it to the MCP client as a
JSON-RPC frame on stdout.

---

## 1. Source connector — [ADR-001](./ADR-001-source-adapters.md)

**Status:** Ratified.

ADR-001's Decision Outcome (lines 66–83): the source-connector layer is
limited to canonical upstream sources. CPython documentation builds are
pinned by version-specific CPython tags and Sphinx pins, then converted into
canonical ingestion artifacts by the build pipeline. PyPI package
documentation discovery is limited to the official PyPI JSON API and
allowlisted project metadata fields via `lookup_package_docs`, which is the
one documented exception to the offline-first principle (2.2): it is a
build/lookup-time metadata call, not a docs-query-time call against the local
index, and it is not a general-purpose web fetch.

Two adapters exist today, matching the ADR:

- **CPython documentation source.** Version pins live in
  [`ingestion/cpython_versions.py`](../../src/mcp_server_python_docs/ingestion/cpython_versions.py):
  `CPYTHON_DOCS_BUILD_CONFIG` maps each supported `X.Y` version (currently
  3.10–3.14) to a `{tag, sha, sphinx_pin}` record. The git SHA is treated as
  authoritative for content-build integrity; the tag is kept for
  human-readable version mapping, but a moved tag must fail verification.
  `build-index` uses this config to build each pinned CPython documentation
  tree with the configured Sphinx pin, then hands the resulting
  `objects.inv` and Sphinx JSON output to the ingestion layer (§3).
- **PyPI metadata source.**
  [`services/package_docs.py`](../../src/mcp_server_python_docs/services/package_docs.py)
  backs `lookup_package_docs` with a `GET` against the official PyPI JSON
  API, returning package-declared PyPI, documentation, homepage, source, and
  repository URLs from controlled metadata fields.

This document does not add to ADR-001's contract; see the ADR for the full
Decision Drivers, Considered Options, and Layer Contract.

---

## 2. Ingestion — no ratified ADR (pending ADR-002, v0.4.0)

> **Pending-ADR note:** ADR-002 is scoped to v0.4.0 and has not been written.
> This section describes shipped ingestion behavior from `src/`; it is not a
> locked architectural decision.

Ingestion turns source-connector artifacts (Sphinx JSON output, `objects.inv`
inventories) into rows in the storage layer's SQLite tables (§4). It runs
only during `build-index`, never during `serve`.

**Sphinx JSON ingestion**
([`ingestion/sphinx_json.py`](../../src/mcp_server_python_docs/ingestion/sphinx_json.py)):

- `ingest_sphinx_json_dir` (line 536) walks a Sphinx JSON output directory,
  skipping known non-documentation files (`globalcontext.json`,
  `searchindex.json`, and similar), calling `ingest_fjson_file` on each
  `.fjson` file it finds, and returning a `(success_count, failure_count)`
  tuple after logging progress every 100 files.
- `ingest_fjson_file` (line 416) ingests one `.fjson` file with per-document
  failure isolation: it parses the file, extracts `body`, `title`, and
  `current_page_name`, skips known non-documentation slugs, converts the HTML
  body to markdown, and inserts one row into `documents`. It then calls
  `extract_sections` and inserts one row per section into `sections`, and
  `extract_code_blocks` and inserts one row per block into `examples`
  (attached to the section whose anchor matches, falling back to the first
  section if no anchor matches). The whole per-file write is committed
  together; on any exception it logs a warning, rolls back, and returns
  `False` without propagating the exception, so one malformed page cannot
  abort the rest of the build.
- `extract_sections` (line 271) splits a page's HTML body into
  heading-anchored sections, producing ordinal-ordered records (`uri`,
  `anchor`, `heading`, `level`, `content_text`, `char_count`) that map
  directly onto the `sections` table columns (§4).
- `extract_code_blocks` (line 352) extracts code blocks from the same HTML
  body, classifies each as a doctest or plain example, and assigns each an
  ordinal within the section it is attached to via `section_anchor`.
- `populate_synonyms` (line 583) reads the packaged `data/synonyms.yaml` via
  `importlib.resources` and inserts each concept/expansion pair into the
  `synonyms` table with `INSERT OR REPLACE`, returning the count inserted.
- `rebuild_fts_indexes` (line 624) issues the FTS5 `rebuild` command against
  `sections_fts` and `examples_fts` after content ingestion, re-reading all
  content from the canonical tables into the external-content FTS5 index.
  `symbols_fts` is rebuilt separately, by the inventory path below.

**Symbol inventory ingestion**
([`ingestion/inventory.py`](../../src/mcp_server_python_docs/ingestion/inventory.py)):

- `ingest_inventory` (line 68) downloads `objects.inv` for one Python version
  via `sphobjinv`, upserts the corresponding `doc_sets` row, deletes any
  symbols already recorded for that doc set (so re-ingestion is safe), then
  filters inventory objects to the Python (`py`) domain. Duplicate qualified
  names are resolved by a fixed role-priority order (class, exception,
  function, method, attribute, data, module — highest priority first), `$`
  URI shorthand is expanded to the full qualified name, and each surviving
  symbol is inserted into `symbols`. The function finishes by issuing the
  FTS5 `rebuild` command against `symbols_fts`.

**Version pin data**
([`ingestion/cpython_versions.py`](../../src/mcp_server_python_docs/ingestion/cpython_versions.py)):
the same `CPYTHON_DOCS_BUILD_CONFIG` described in §1 is what ingestion
consumes indirectly — the tag/SHA/Sphinx-pin triple determines which
`objects.inv` and Sphinx JSON tree get ingested for a given version.

**Publish**
([`ingestion/publish.py`](../../src/mcp_server_python_docs/ingestion/publish.py))
sits between ingestion and the storage layer becoming visible to `serve`:

- `record_ingestion_run` (line 76) inserts a row into `ingestion_runs`
  (`source`, `version`, `status`, `artifact_hash`, `notes`), used to audit
  each build attempt regardless of outcome.
- `atomic_swap` (line 344) renames an existing `index.db` to
  `index.db.previous` (for rollback) and then `os.replace()`s the freshly
  built database into place as the new `index.db`; both operations rely on
  same-filesystem atomic rename semantics. `rollback()` reverses this by
  restoring `index.db.previous`.
- `publish_index` (line 419) orchestrates the sequence: compute the build
  artifact's SHA256, record the ingestion run, run smoke tests against the
  new database, and only call `atomic_swap` (plus print a restart-required
  message to stderr) if the smoke tests pass; on failure it marks the run
  failed and returns `False` without swapping anything into place.

---

## 3. Storage — no ratified ADR (pending ADR-003, v0.4.0)

> **Pending-ADR note:** ADR-003 is scoped to v0.4.0 and has not been written.
> This section describes shipped storage behavior from `src/`; it is not a
> locked architectural decision.

Storage is SQLite plus markdown, matching principle 2.4 of the roadmap. The
full schema lives in
[`storage/schema.sql`](../../src/mcp_server_python_docs/storage/schema.sql).

**Canonical tables** (source of truth, one `CREATE TABLE` each): `doc_sets`
(line 21), `documents` (line 33), `sections` (line 44), `symbols` (line 57),
`examples` (line 71), `synonyms` (line 80), `redirects` (line 87),
`ingestion_runs` (line 95). Notable constraints recorded as comments in the
schema itself: `sections.uri` has no standalone `UNIQUE` constraint, so
cross-version URI overlap is safe (uniqueness is enforced only by
`UNIQUE(document_id, anchor)`); `symbols` uses
`UNIQUE(doc_set_id, qualified_name, symbol_type)` rather than
`UNIQUE(doc_set_id, qualified_name)`, so the same name can appear as both a
function and a method; `doc_sets.language` defaults to `'en'`, reserving
space for future i18n without a migration.

**FTS5 virtual tables** (derived, rebuildable from the canonical tables, not
themselves a source of truth): `sections_fts` (line 120), `symbols_fts`
(line 126), `examples_fts` (line 132). All three use external-content mode
(`content='<table>', content_rowid='id'`) and the tokenizer
`unicode61 remove_diacritics 2 tokenchars '._'`, which treats `.` and `_` as
token characters rather than separators — so `asyncio.TaskGroup` indexes as
one token — and deliberately does not apply Porter stemming, to preserve
exact Python identifier search.

**Connection management**
([`storage/db.py`](../../src/mcp_server_python_docs/storage/db.py)):

- `get_readonly_connection` (line 48) opens the database with SQLite URI mode
  `?mode=ro` (preventing accidental writes at the connection level),
  `check_same_thread=False`, and `row_factory = sqlite3.Row`. This is the
  connection type `serve` uses.
- `get_readwrite_connection` (line 60) creates parent directories as needed,
  opens a normal read-write connection, and additionally sets
  `PRAGMA journal_mode = WAL`. This is the connection type `build-index`
  uses.
- `assert_fts5_available` (line 74) checks FTS5 support with a
  platform-aware error message: on a read-write connection it attempts to
  `CREATE`/`DROP` a temporary FTS5 table as a definitive check; on a
  read-only connection, where that `CREATE` fails for the unrelated reason
  that the connection is read-only, it falls back to checking
  `PRAGMA compile_options` for `ENABLE_FTS5`. If FTS5 is genuinely
  unavailable, it raises `FTS5UnavailableError` with a platform-specific
  remediation hint (the `pysqlite3-binary` extra on Linux x86-64; a
  `uv python install` or python.org build otherwise).
- `bootstrap_schema` (line 116) drops and recreates the three FTS5 virtual
  tables unconditionally — because FTS5 has no `ALTER` for tokenizer
  configuration, `IF NOT EXISTS` alone cannot pick up a tokenizer change —
  then executes the full `schema.sql` DDL via `executescript()`. Every
  canonical-table statement uses `CREATE TABLE IF NOT EXISTS`, so running
  `bootstrap_schema` twice against the same database is a no-op for those
  tables. The docstring warns that `executescript()` issues an implicit
  `COMMIT`, so this function must not be called while a transaction with
  uncommitted writes is in progress.

---

## 4. Retrieval — no ratified ADR (pending ADR-004, v0.4.0)

> **Pending-ADR note:** ADR-004 is scoped to v0.4.0 and has not been written.
> This section describes shipped retrieval behavior from `src/`; it is not a
> locked architectural decision.

Retrieval turns a `search_docs` or `get_docs` request into ranked hits or a
resolved page/section against the storage layer, without doing any
truncation itself (that is the budget layer, §5).

**Query processing**
([`retrieval/query.py`](../../src/mcp_server_python_docs/retrieval/query.py)),
pure functions with no storage or MCP imports:

- `fts5_escape` (line 16) wraps every whitespace-separated token of user
  input in double quotes, neutralizing FTS5 operators (`AND`, `OR`, `NOT`,
  `NEAR`), prefix matching (`*`), column filters (`:`), grouping
  (parentheses), and negation (`-`); empty or whitespace-only input returns
  `'""'`, which matches nothing rather than raising.
- `classify_query` (line 52) routes a query to the `"symbol"` fast path if it
  contains a dot (for example `asyncio.TaskGroup`), or if it is a single
  lowercase identifier-shaped token that a caller-supplied callback confirms
  exists in the symbols table (avoiding false positives on ordinary words
  like "test" or "list"); otherwise it returns `"fts"`.
- `expand_synonyms` (line 100) expands single-word tokens by exact match and
  multi-word concepts by word-boundary regex against a synonyms map loaded
  from `synonyms.yaml`, returning the union of original and expansion terms.
- `build_match_expression` (line 149) OR-joins the escaped original query
  with escaped expansion terms when synonym expansion changed the token set,
  and falls back to the plain escaped query (implicit `AND` across its
  tokens) when it did not.

**Ranking**
([`retrieval/ranker.py`](../../src/mcp_server_python_docs/retrieval/ranker.py)):

- `search_sections` (line 136) queries `sections_fts` with BM25 column
  weights (`heading` weighted 10.0 against `content_text` weighted 1.0),
  optionally filtered by version, ordered by BM25 score, and returns hits
  carrying a `snippet()`-generated excerpt; an `sqlite3.OperationalError`
  from a malformed match expression is caught, logged, and returns an empty
  list rather than raising.
- `search_symbols` (line 197) queries `symbols_fts` with column weights
  (`qualified_name` 10.0, `module` 1.0) the same way.
- `search_examples` (line 256) queries `examples_fts` for code-sample hits.
- `lookup_symbols_exact` (line 315) is the exact-match fast path against the
  `symbols` table directly, bypassing FTS5 entirely.
- Raw BM25 scores (negative, more negative is better) are rescaled by a
  helper into a `0.1`–`1.0` band before being returned to callers, so the
  best hit in a result set always has score `1.0`.

**Orchestration**
([`services/search.py`](../../src/mcp_server_python_docs/services/search.py)):
`SearchService` (line 27) composes the above. `search` (line 59) resolves the
version permissively — `None` is intentionally preserved as "search across
all versions" rather than defaulted, a documented design decision (`CR-01`
in the code) so an LLM client can see and compare results across versions —
expands synonyms to record an observability flag, classifies the query, and
then either takes the exact-symbol fast path (skipping FTS5 entirely when
`kind="symbol"` or `kind="auto"` classifies as symbol-shaped) or builds an
FTS5 match expression and dispatches to `search_sections`, `search_examples`,
or `search_symbols` based on the requested `kind`; the default `kind="auto"`
path tries `search_sections` first and falls back to `search_symbols` if
that returns no hits.

---

## 5. Budget — no ratified ADR (pending ADR-005, v0.4.0)

> **Pending-ADR note:** ADR-005 is scoped to v0.4.0 and has not been written.
> This section describes shipped budget behavior from `src/`; it is not a
> locked architectural decision.

Budget is a single, narrow function:
`apply_budget` (line 16) in
[`retrieval/budget.py`](../../src/mcp_server_python_docs/retrieval/budget.py).
The module docstring states its scope directly: "Pure logic — no MCP types,
no storage imports." Given `(text, max_chars, start_index)`, it returns
`(truncated_text, is_truncated, next_start_index)`. If the remaining text
(from `start_index` onward) fits within `max_chars`, it is returned unchanged
with `is_truncated=False` and `next_start_index=None`. Otherwise the text is
cut at the `max_chars` boundary and then walked backward past any Unicode
combining marks (category `M*`) so a base character is never separated from
its diacritics; in the degenerate case where backing up would collapse the
slice to zero length, it instead walks forward from the base character
through its combining marks. `next_start_index` is set to the resulting cut
point whenever the text was truncated, which is what callers use to
paginate.

**Consumption**
([`services/content.py`](../../src/mcp_server_python_docs/services/content.py)):
`ContentService.get_docs` (line 45), with defaults `max_chars: int = 8000`
and `start_index: int = 0` (lines 50–51), is the sole caller in the current
codebase. Its sequence: resolve the version strictly via
`resolve_version_strict` (an explicit unknown version raises
`VersionNotFoundError`; `None` resolves to the doc set marked
`is_default=1`, falling back to the highest version string if none is
marked default); check
the persistent cache (§6) and return immediately on a hit, bypassing storage,
retrieval, and budget entirely; otherwise look up the document by slug and
version; if an `anchor` was given, resolve exactly that section's text
through an in-memory section cache, else concatenate every section for the
page in `ordinal` order (each rendered as `## {heading}\n\n{content}`) into
one `full_text`; call `apply_budget(full_text, max_chars, start_index)`;
build a `GetDocsResult` whose `char_count` is the *pre-truncation* length of
`full_text`; write the result into the persistent cache; and return it.

---

## 6. Serializer — [ADR-006](./ADR-006-serialization.md)

**Status:** Ratified.

ADR-006's Decision Outcome (lines 49–58): compact JSON is the default wire
format; `format="toon"` is opt-in and gated by the v0.3.0 empirical study;
the `format` parameter, if and when it ships, exists on `search_docs`,
`list_versions`, and `compare_versions` only; `get_docs` stays markdown
because markdown is the canonical documentation body, not a structured
result needing alternate serialization; TOON-as-storage was considered and
rejected (decision 5.3) because it would mix storage and wire-format
concerns.

As shipped today, verified against
[`src/mcp_server_python_docs/server.py`](../../src/mcp_server_python_docs/server.py)
(tool definitions at lines 297–410), none of the six MCP tools — including
the three the ADR names — currently accept a `format` parameter; each
returns its structured Pydantic result model directly, which FastMCP
serializes to JSON. This is consistent with the ADR's own framing: the
`format` parameter is described there as something the v0.3.x implementation
"may expose... only after the study in decision 5.8 confirms" a real win
survives client-side rewrap. That study-gated rollout has not landed in
`src/` as of this document's date. This is a statement of current code
state, not a reopening of ADR-006's decision.

This document does not add to ADR-006's contract; see the ADR for the full
Decision Drivers, Considered Options, and Layer Contract.

---

## 7. Cache — [ADR-007](./ADR-007-cache.md)

**Status:** Ratified.

ADR-007's Decision Outcome (lines 69–122): the cache layer has two
independent mechanisms with different lifetimes.

The persistent value cache, `PersistentDocsCache` in
[`services/persistent_cache.py`](../../src/mcp_server_python_docs/services/persistent_cache.py),
stores completed `get_docs` results in a dedicated SQLite table,
`retrieved_docs_cache`, keyed by
`(index_fingerprint, version, slug, anchor, max_chars, start_index)`.
`content.py`'s `get_docs` checks this cache before doing any document,
section, or budget work (see §5). `index_fingerprint` is derived from the
resolved index path's size and `mtime_ns`, so a rebuilt index can never be
answered from a cache entry built against the previous index — rows carrying
any other fingerprint are deleted at construction. Any init, read, or write
failure degrades the cache to a disabled no-op state with a logged warning;
it never turns into a `get_docs` error. Cached values are compressed with
app-level zstd by default, per locked decision 5.7, behind a versioned codec
column so decoding always uses the codec recorded on the row rather than the
server's current default.

The second mechanism is a pair of process-lifetime, no-TTL in-memory LRU
caches in
[`services/cache.py`](../../src/mcp_server_python_docs/services/cache.py): a
section cache (`maxsize=512`) used by `ContentService` for anchor-scoped
lookups, and a symbol cache (`maxsize=128`) used by `CompareService`. Both
are picked up only by restarting the server, not by any in-process
invalidation signal.

This document does not add to ADR-007's contract; see the ADR for the full
Decision Drivers, Considered Options, and Layer Contract, including the
codec registry details (`none`, `zstd`, `zstd-dict-v1`) and the operational
risks recorded in its Consequences section.

---

## 8. Transport — [ADR-008](./ADR-008-transport.md)

**Status:** Ratified.

ADR-008's Decision Outcome (lines 63–100): the shipped transport is
stdio-only via FastMCP —
`mcp_server.run(transport="stdio")` in the `serve` command of
[`src/mcp_server_python_docs/__main__.py`](../../src/mcp_server_python_docs/__main__.py)
(verified live at line 110). Nothing else in `src/` implements an HTTP, SSE,
streamable, or websocket transport. Because fd 1 is the literal wire in
stdio mode, `__main__.py` runs a load-bearing hygiene sequence before
anything else can write to stdout: it saves and redirects the real stdout fd
to stderr at import time (HYGN-01), ignores `SIGPIPE` where the platform
supports it (HYGN-03), and forces all logging to stderr (HYGN-02); the
`serve` command restores both the fd and the Python-level `sys.stdout`
immediately before calling `run()`, and catches `BrokenPipeError` from a
disconnecting client so a normal disconnect exits cleanly.

HTTP/SSE transport is deliberately not shipped in v0.5.0. The roadmap holds
it open as a v1.0.0 gate (§6 q3); ADR-008 records stdio-only as the accepted
v0.5.0 decision without resolving that later question.

This document does not add to ADR-008's contract; see the ADR for the full
Decision Drivers, Considered Options, and Layer Contract, including the
exact ordering invariants the fd-swap sequence depends on.

---

## Summary table

| # | Layer | ADR | Status | Primary code |
|---|---|---|---|---|
| 1 | Source connector | [ADR-001](./ADR-001-source-adapters.md) | Ratified | `ingestion/cpython_versions.py`, `services/package_docs.py` |
| 2 | Ingestion | *pending ADR-002 (v0.4.0)* | Code ground truth only | `ingestion/sphinx_json.py`, `ingestion/inventory.py`, `ingestion/publish.py` |
| 3 | Storage | *pending ADR-003 (v0.4.0)* | Code ground truth only | `storage/schema.sql`, `storage/db.py` |
| 4 | Retrieval | *pending ADR-004 (v0.4.0)* | Code ground truth only | `retrieval/ranker.py`, `retrieval/query.py`, `services/search.py` |
| 5 | Budget | *pending ADR-005 (v0.4.0)* | Code ground truth only | `retrieval/budget.py`, `services/content.py` |
| 6 | Serializer | [ADR-006](./ADR-006-serialization.md) | Ratified | `server.py` tool definitions, result models in `models.py` |
| 7 | Cache | [ADR-007](./ADR-007-cache.md) | Ratified | `services/persistent_cache.py`, `services/cache.py`, `cache/codec.py` |
| 8 | Transport | [ADR-008](./ADR-008-transport.md) | Ratified | `__main__.py` |

## Maintaining this document

This document should be revised, not silently left stale, whenever: an ADR
listed above changes status; ADR-002–005 are ratified (at which point the
corresponding sections above should be rewritten to link and summarize those
ADRs, and the document-level gap statement should be removed or narrowed);
or a code change alters the file:line references cited in the four
code-ground-truth sections. Until then, the pending-ADR notes in §2–§5 and
the document-level gap statement above are the authoritative record of what
is, and is not, an architectural decision in this codebase.

## Links

- `STRATEGIC-ROADMAP-2026-05-29.md` §2 (principle 2.7), §4 (v0.4.0 and
  v0.5.0 tables)
- [ADR-001: Source Adapters](./ADR-001-source-adapters.md)
- [ADR-006: Serialization & Wire Format](./ADR-006-serialization.md)
- [ADR-007: Cache Layer](./ADR-007-cache.md)
- [ADR-008: Transport](./ADR-008-transport.md)
- [`src/mcp_server_python_docs/`](../../src/mcp_server_python_docs/)
