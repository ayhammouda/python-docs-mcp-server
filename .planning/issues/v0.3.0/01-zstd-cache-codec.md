# [v0.3.0] cache — add zstd codec layer to the retrieved-docs cache

> **Confidence:** HIGH · **Wave:** lead · **Slug:** `zstd-cache-codec`
> Create on GitHub with: `gh issue create -F .planning/issues/v0.3.0/01-zstd-cache-codec.md -l agent-ready,area:runtime,priority:P2`
> Branch (after number assigned): `agent/<issue-number>-zstd-cache-codec`

## ⛔ Blocking pre-requisite (maintainer, before queueing)

This task needs the `zstandard` runtime dependency, and `pyproject.toml [project]`
is **forbidden territory** (pipeline §2) plus a §7 human-review trigger. The
maintainer must add it and refresh the lockfile **before** this issue is queued:

```toml
# pyproject.toml [project].dependencies
"zstandard>=0.23.0",
```
```bash
uv lock
```

If `python -c "import zstandard"` fails when the agent starts, the agent **stops
and comments** (pipeline §8) — it must not edit `pyproject.toml` or `uv.lock`.

## Context

- **Per-issue context file (read first):** [`.planning/agent-context/zstd-cache-codec.md`](../../agent-context/zstd-cache-codec.md)
- Pipeline: [`AGENT-EXECUTION-PIPELINE.md`](../../../AGENT-EXECUTION-PIPELINE.md)
- Roadmap: [`STRATEGIC-ROADMAP-2026-05-29.md`](../../../STRATEGIC-ROADMAP-2026-05-29.md) §4 (v0.3.0, "Workstream J"), decision **5.7**
- Touch-point: `src/mcp_server_python_docs/services/persistent_cache.py` (the `retrieved_docs_cache` table, `put()`, `get()`)
- New module: `src/mcp_server_python_docs/cache/codec.py` (path chosen to match the pipeline §4 acceptance example)

## Goal

Compress the retrieved-docs cache value column with an app-level, versioned zstd
codec that reads pre-existing uncompressed rows transparently.

## Acceptance criteria

- [ ] `python -c 'from mcp_server_python_docs.cache.codec import list_supported; print(list_supported())'` prints exactly `['none', 'zstd', 'zstd-dict-v1']`.
- [ ] `uv run pytest tests/cache/test_codec.py -q` passes with **at least 4** new tests covering: round-trip for `'none'`, round-trip for `'zstd'`, round-trip for `'zstd-dict-v1'` (dictionary trained from the committed fixture corpus at test time), and graceful decode of a value written under `compression='none'` by a prior server version.
- [ ] The `retrieved_docs_cache` table gains a `compression TEXT NOT NULL DEFAULT 'none'` column, added via `ALTER TABLE ... ADD COLUMN` when an older cache file lacks it (existence-checked), so an existing on-disk cache opens without error and serves its rows.
- [ ] `uv run pytest tests/test_persistent_docs_cache.py -q` still passes (no regression to the existing cache contract), and a new test asserts a value written by the current server reads back identically after a simulated restart with the default production codec.
- [ ] The cache writes new entries with a single configurable default codec (`'zstd'`); `get()` dispatches decode purely off the stored `compression` value, never off the default.

## Scope boundaries

**In scope:**
- New `cache/codec.py` with `list_supported()`, `encode(text, codec) -> bytes`, `decode(blob, codec) -> str`, and a registry mapping codec id → handler.
- `compression` column on `retrieved_docs_cache` + transparent migration of existing cache files.
- Wiring `put()`/`get()` in `persistent_cache.py` through the codec.
- Tests under `tests/cache/`.

**Out of scope (do NOT do these — stop and comment if they seem required):**
- Training and **packaging a production `zstd-dict-v1` dictionary** from a real `get_docs` corpus — corpus selection is a human judgment call per roadmap §4. The `zstd-dict-v1` codec must *function* (tests train an ephemeral dict from a fixture), but no production dictionary artifact ships in this issue.
- Any change to the **canonical index** schema (`src/mcp_server_python_docs/storage/schema.sql`).
- Any tool name, parameter, or return shape.
- Compressing `get_docs` markdown on the wire — this is cache-at-rest only.

## Forbidden-territory reminders (pipeline §2)

- `pyproject.toml [project]` — the `zstandard` dep is a maintainer pre-req; do not edit.
- `src/**/storage/schema.sql` and migrations — the *index* schema is off-limits. (The *cache* table in `persistent_cache.py` is NOT the index schema and is in scope per decision 5.7.)
- Existing tests — extend, never delete or weaken.

## Validation commands (pipeline §5)

```bash
uv run ruff check src/ tests/
uv run pyright src/
uv run pytest --tb=short -q
uv run python-docs-mcp-server doctor
# cache lives in the get_docs path — also run the wire smoke:
uv run pytest tests/integration/test_stdio_smoke.py -q
```

## PR template & recovery

- PR body uses `.github/PULL_REQUEST_TEMPLATE/agent.md`; title matches this issue verbatim.
- Adding a third-party runtime dep is a §7 trigger — but if the maintainer pre-added `zstandard`, the PR itself introduces no new dep; state that under "Why this triggered human review: None."
- Blocked? Stop, write `WORKING-NOTES.md`, comment per pipeline §8. No PR, no auto-merge.

## Effort estimate

~2–3 hours.
