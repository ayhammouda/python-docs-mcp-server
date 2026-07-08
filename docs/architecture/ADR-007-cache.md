# ADR-007: Cache Layer

- **Status:** Accepted
- **Date:** 2026-07-08
- **Deciders:** @ayhammouda
- **Roadmap refs:** principle 2.7; decisions 5.7, 5.16

## Context and Problem Statement

The cache layer is layer seven of the eight-layer contract in principle 2.7:
source connector, ingestion, storage, retrieval, budget, serializer, cache,
transport. Two read paths need caching for different reasons. First, `get_docs`
call arguments repeat: the same slug, version, anchor, and pagination window
are asked for again within a session and across MCP server restarts, and
recomputing the full storage-retrieval-budget pipeline for an identical answer
is wasted work. Second, building a fresh `get_docs` or `compare_versions`
result still does repeated section and symbol lookups against the read-only
SQLite index within a single running process.

Any caching layer here has to respect two constraints that are easy to get
wrong. It must never let a rebuilt index leak into a cache entry built from the
previous index, and a cache malfunction (a locked file, a corrupt row, a full
disk) must never turn a working documentation lookup into an error. This ADR
records how the shipped code satisfies both constraints, and records decision
5.7 (locked) governing the persisted value cache's on-disk format.

## Decision Drivers

- Principle 2.7: layered design with stable contracts. The cache layer needs
  an explicit contract so the serializer, transport, and tool-handling code
  above it do not depend on cache-specific behavior.
- Decision 5.7 (locked, recorded verbatim below): the persisted value cache
  must use app-level zstd compression with no config gate, behind a versioned
  codec column.
- Decision 5.16: compression is an engineering footnote, never a headline;
  this ADR states the codec choice inside the decision outcome, not as the
  reason the cache layer exists.
- Repeat `get_docs` calls (same slug, anchor, and pagination window) should
  skip storage, retrieval, and budget work entirely, including across MCP
  server restarts, not just within one process.
- An index rebuild must not let a persisted cache entry built from the prior
  index silently answer a query against the new index.
- A cache failure of any kind — init, read, or write — must degrade to a
  cache miss, never to a `get_docs`, section, or symbol lookup failure.

## Considered Options

1. No persistent `get_docs` value cache; rely only on the read-only SQLite
   index and the existing process-lifetime in-memory lookup caches.
   - Rejected because identical `get_docs` calls would repeat storage,
     retrieval, and budget work on every call, including immediately after a
     restart, with no way to skip work already finished once.
2. A persistent SQLite-backed value cache, in a table separate from the docs
   index, keyed by index fingerprint plus request identity, storing the
   codec-encoded `GetDocsResult` with app-level zstd compression by default
   behind a versioned codec column, and disabling itself cleanly on any
   init/read/write failure. (chosen — decision 5.7)
   - Accepted because it reuses SQLite (no new storage dependency), ties cache
     validity to the exact index build via the fingerprint so a rebuild can
     never return stale content, keeps the on-disk codec as a swappable,
     versioned detail rather than a fixed format, and cannot turn a cache
     problem into a `get_docs` failure.
3. Store the persistent cache value uncompressed, or make compression a
   configurable/gated option.
   - Rejected because decision 5.7 locks in app-level zstd with no gate; an
     uncompressed-only cache would not need the versioned codec column at all,
     and a config gate would reopen a decision that is already closed.

## Decision Outcome

The cache layer has two independent mechanisms with different lifetimes.

The persistent value cache (`PersistentDocsCache` in
`services/persistent_cache.py`) stores completed `get_docs` results in a
dedicated SQLite table, `retrieved_docs_cache`, with primary key
`(index_fingerprint, version, slug, anchor, max_chars, start_index)`. The
database is opened with `PRAGMA journal_mode = WAL` and
`PRAGMA synchronous = NORMAL`. `content.py`'s `get_docs` checks this cache
before doing any document, section, or budget work and returns a hit
unchanged. `index_fingerprint` is computed once at construction from the
resolved index path, its size, and its `mtime_ns`; rows carrying any other
fingerprint are deleted at construction, so a rebuilt index can never be
answered from a cache entry built against the previous index. If
construction, a read, or a write fails (`OSError` or `sqlite3.Error`), the
cache closes its connection, sets itself to a disabled state, and every
subsequent `get`/`put` call becomes a no-op miss with a logged warning — the
cache never raises into `get_docs`. A single `threading.Lock` serializes every
execute/commit call and the hit/miss/write counters, since the sqlite3
connection is shared across threads with `check_same_thread=False` but the
Python sqlite3 docs still require the application to serialize writes.

Cached values are the result's `model_dump_json()` output, codec-encoded
before storage; a `compression` column (added to older databases through a
legacy `ALTER TABLE` migration) records exactly which codec produced each row,
so decoding always uses the codec stored with the row rather than the
server's current default. Per decision 5.7 (recorded verbatim): "App-level
zstd on retrieved-docs cache, no gate. Versioned codec column for
forward-compat." The default codec is `zstd`
(`DEFAULT_RETRIEVED_DOCS_CACHE_CODEC`), applied unconditionally with no
configuration flag to disable it. The codec registry (`cache/codec.py`) also
defines `none`, a plain passthrough used by rows written before compression
existed, and `zstd-dict-v1`, a dictionary-based codec kept as test-only
forward-compatibility scaffolding: encoding or decoding with it raises unless
an explicit dictionary object is supplied, and no trained dictionary ships
with the project today. Per decision 5.16, this codec detail is an
implementation footnote of the cache layer, not the reason the layer exists.

The second mechanism is a pair of in-memory LRU caches (`services/cache.py`)
used while building a fresh result: a section cache (`create_section_cache`,
`maxsize=512`) keyed by section id, used by `ContentService` for
anchor-scoped `get_docs` lookups, and a symbol cache
(`create_symbol_cache`, `maxsize=128`) keyed by `(qualified_name, version)`,
used by `CompareService` to resolve symbols across doc sets. Both are plain
`functools.lru_cache` closures over a captured read-only `sqlite3.Connection`,
scoped to the process lifetime, with no TTL and no invalidation logic; a
documentation rebuild is picked up by restarting the server, not by any
in-process cache-clearing signal.

`server.py` constructs the `PersistentDocsCache` once during application
startup and injects it into `ContentService`; the lifespan's `finally` block
closes it on shutdown (best-effort, swallowing close errors) alongside the
read-only database connection.

### Consequences

**Positive:** Repeat `get_docs` calls skip storage, retrieval, and budget work
entirely, including across MCP server restarts, because the value cache
persists on disk and is keyed by exact request identity. The persistent
cache degrades safely: any failure to open, read, or write it falls back to
recomputing the answer rather than failing the tool call. The versioned codec
column means a future default codec (or a dictionary-based one, once a
dictionary ships) can be introduced without a schema migration or without
invalidating rows already written under the current codec, since decode always
reads the codec recorded on the row. The in-memory LRU caches remove repeated
round trips to SQLite for section and symbol lookups within one running
process, and their process-lifetime, no-invalidation design matches how the
project already expects operators to pick up a rebuilt index — by restarting.

**Negative / risks:** The persistent cache's best-effort posture is
deliberately silent: a disk that is read-only, full, or corrupted degrades to
"always miss" with only a warning log, which can mask an operational problem
if nobody reads the logs. The `retrieved_docs_cache` table has no size cap or
eviction policy beyond `INSERT OR REPLACE` on an exact key collision and the
fingerprint-based delete at startup, so it can grow without bound between
index rebuilds under heavy, varied query traffic. The in-memory LRU caches
have no invalidation path other than process restart, so any content change
that does not go through a full index rebuild-and-restart cycle would not be
reflected in cached section or symbol lookups until the process restarts.
`zstd-dict-v1` exists in the codec registry without a shipped dictionary, so
it is unusable in production today; it must not be described as an available
feature outside of tests.

## Layer Contract (principle 2.7)

- **Inputs:** Persistent value cache — `version`, `slug`, `anchor`,
  `max_chars`, and `start_index` for a lookup, or a completed `GetDocsResult`
  plus `max_chars`/`start_index` for a write; `cache_path` and `index_path`
  at construction. In-memory LRU caches — a `section_id` (section cache) or a
  `(qualified_name, version)` pair (symbol cache), plus the read-only
  `sqlite3.Connection` captured at construction.
- **Outputs:** Persistent value cache — a `GetDocsResult` on a hit or `None`
  on a miss or when disabled. In-memory LRU caches — a `CachedSection | None`
  or a `CachedSymbol | None`.
- **Invariants:** Persistent cache entries are scoped to one index build via
  `index_fingerprint`; entries from any other fingerprint are deleted at
  construction, so a lookup never returns content built from a stale index.
  Any persistent-cache failure — init, read, or write — degrades to "no
  cache" and never raises into a tool response. Decoding always uses the
  codec recorded on the row, never the server's current default codec, so
  changing the default codec never corrupts or misreads already-cached rows.
  A single process-wide lock guards every persistent-cache database mutation
  and its stats counters. In-memory LRU caches are process-lifetime, have no
  TTL, and are bounded only by entry count (512 / 128); they hold no state
  across restarts, so index rebuilds are picked up by restarting the server,
  not by any invalidation signal.

## Links

- STRATEGIC-ROADMAP-2026-05-29.md §2.7; §5.7, §5.16
- [`src/mcp_server_python_docs/services/persistent_cache.py`](../../src/mcp_server_python_docs/services/persistent_cache.py)
- [`src/mcp_server_python_docs/cache/codec.py`](../../src/mcp_server_python_docs/cache/codec.py)
- [`src/mcp_server_python_docs/services/cache.py`](../../src/mcp_server_python_docs/services/cache.py)
- [`src/mcp_server_python_docs/services/content.py`](../../src/mcp_server_python_docs/services/content.py)
- [`src/mcp_server_python_docs/services/compare.py`](../../src/mcp_server_python_docs/services/compare.py)
- [`src/mcp_server_python_docs/server.py`](../../src/mcp_server_python_docs/server.py)
