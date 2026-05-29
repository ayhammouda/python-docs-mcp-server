# Agent Context — zstd cache codec

> One-read working context for issue `[v0.3.0] cache — add zstd codec layer`.
> Everything you need is here; do not go fishing in `.planning/` archive material.

## 1. Roadmap excerpt (the goal — do not re-derive)

> **Workstream J — app-level zstd cache compression** (roadmap §4, v0.3.0):
> Targets the retrieved-docs cache *value column only*. Trained dict on a
> representative `get_docs` corpus. Codec column for forward-compat. Expected
> ratio strong because zstd's dictionary mode is especially effective on small
> correlated records — exactly the cache-entry shape.
>
> **Decision 5.7 (locked):** App-level zstd on retrieved-docs cache, *no gate*.
> Versioned codec column for forward-compat.

## 2. Code touch-points (file paths + symbols)

- `src/mcp_server_python_docs/services/persistent_cache.py`
  - `retrieved_docs_cache` table — created inline with `CREATE TABLE IF NOT EXISTS` at **line ~47**. Columns today: `index_fingerprint, version, slug, anchor, max_chars, start_index, result_json TEXT, created_at`. PK is the first six columns.
  - `put(...)` — **line ~118**, `INSERT OR REPLACE ... result_json` = `result.model_dump_json()`.
  - `get(...)` — **line ~80**, `SELECT result_json ...` then `GetDocsResult.model_validate_json(row[0])`.
  - This is **best-effort**: every read/write is wrapped in try/except and the cache disables cleanly (`self._conn = None`) on error. Preserve that posture.
- **New module:** `src/mcp_server_python_docs/cache/codec.py` (create the `cache/` package with `__init__.py`). Public API the pipeline §4 example expects:
  - `list_supported() -> list[str]` → `['none', 'zstd', 'zstd-dict-v1']`
  - `encode(text: str, codec: str, *, dictionary=None) -> bytes`, `decode(blob: bytes, codec: str, *, dictionary=None) -> str`.
- **Tests:** new dir `tests/cache/` with `__init__.py` and `test_codec.py`.

## 3. Existing test patterns to follow

- `tests/test_persistent_docs_cache.py` shows the established cache-test idiom:
  the `_cache(tmp_path, marker)` helper builds an index fingerprint file +
  `PersistentDocsCache`, and `populated_db` (a fixture from `tests/conftest.py`)
  provides a live SQLite index. Reuse this shape for the no-regression test.
- The "survives restart" test (`test_cache_survives_restart_and_miss_falls_back`)
  is the exact pattern for your "value reads back identically after restart" criterion:
  build a `PersistentDocsCache`, write, construct a *second* instance on the same
  files, assert `hits == 1` and equality.
- Tests are plain `pytest` functions, `from __future__ import annotations`, type-annotated args. Match that.

## 4. Known pitfalls

- **The cache table is NOT `storage/schema.sql`.** `schema.sql` is the canonical
  *index* schema and is forbidden territory. The cache table is owned by
  `persistent_cache.py` and is yours to evolve per 5.7. Do not confuse them.
- **Existing on-disk caches lack the new column.** `CREATE TABLE IF NOT EXISTS`
  will *not* add a column to an existing table. Detect the column
  (`PRAGMA table_info(retrieved_docs_cache)`) and `ALTER TABLE ... ADD COLUMN
  compression TEXT NOT NULL DEFAULT 'none'` when missing, inside the same
  try/except that already tolerates a broken cache.
- **Value column type.** `result_json` is `TEXT`. Compressed output is `bytes`.
  Either store the blob in a new `BLOB` column and keep `result_json` for the
  `'none'` path, or store all payloads as `BLOB` and record the codec. Simplest
  forward-compatible design: keep `result_json` semantics for `'none'`, add a
  `result_blob BLOB` for compressed codecs, and let `compression` select which
  column to read. Document whichever you choose in the decision log below.
- **`zstandard` must already be importable** (maintainer pre-req). If
  `import zstandard` fails, STOP and comment — do not edit `pyproject.toml`/`uv.lock`.
- **`zstd-dict-v1` has no production dictionary in this issue.** Make the codec
  *work* only when an explicit dictionary object is supplied by tests. The
  cache's default production codec is `'zstd'`. Shipping a trained dictionary
  artifact is a separate, human-gated follow-up.
- Decode must dispatch off the stored `compression` value, never off the current
  default — otherwise old `'none'` rows break the day the default flips.

## 5. Decision log (fill this in as you work)

- Chosen value-storage layout (`result_blob` vs reuse `result_json`):
- How `zstd-dict-v1` round-trips in tests (dict training approach):
- Default production codec wired into `put()`:
- Anything you deferred or escalated:
