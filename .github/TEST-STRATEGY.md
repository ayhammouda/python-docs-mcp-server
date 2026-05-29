# Test Strategy

Canonical map of **what we test, at which layer, and where the gaps are**.
Pairs with the how-to-run instructions in `CONTRIBUTING.md` and the manual
client runbook in `.github/INTEGRATION-TEST.md`.

Last verified: 2026-05-29 — 284 tests passing, ruff clean, pyright 0 errors.

## 1. The pyramid (current shape)

```
        /  stdio E2E   \      9 tests   — real MCP process over stdio
       /  integration   \    ~38 tests  — multi-version, publish, cache, phase1
      /   unit / service  \  ~230 tests — services, retrieval, ingestion, compare
     /  contract + regression \  38 tests — schema snapshots, stability, curated cases
```

This is the right shape: a wide, fast base and a thin, slow top. Keep new
tests pushed **down** the pyramid — only add an stdio E2E test when a bug can
*only* manifest across the process boundary (framing, lifespan DI, stdout
hygiene).

## 2. Expected features → coverage map

The server exposes **6 MCP tools**. Every tool must have at least one
behavioral test and appear in the schema snapshot.

| Tool / feature         | Primary tests                                                        | Layer            | Status |
|------------------------|----------------------------------------------------------------------|------------------|--------|
| `search_docs`          | `test_services`, `test_retrieval`, `test_synonyms`, `test_stability` | unit + regression| STRONG |
| `get_docs`             | `test_services`, `test_retrieval`, `test_persistent_docs_cache`, `test_mcp_get_docs_cache_smoke` | unit + integration | STRONG |
| `list_versions`        | `test_services`, `test_multi_version`                                | unit + integration| GOOD   |
| `compare_versions`     | `test_compare_versions` (15), `test_services`                        | unit             | GOOD   |
| `lookup_package_docs`  | `test_package_docs` (8)                                              | unit only        | THIN   |
| `detect_python_version`| `test_detection` (12)                                               | unit             | GOOD   |

Cross-cutting coverage:

- **Schema contract**: `test_schema.py`, `test_schema_snapshot.py` — input/output
  JSON schemas for each tool are frozen as fixtures; a wire-shape change fails CI.
- **Multi-version routing**: `test_multi_version.py` — version param resolution and
  default fallback across indexed doc sets.
- **Regression**: `test_retrieval_regression.py` (curated query→expected cases) and
  `test_stability.py` (property-based invariants that survive CPython doc revisions).
- **Process hygiene**: `test_stdio_smoke.py`, `test_stdio_hygiene.py` — confirm a real
  stdio server starts, answers, and keeps stdout free of non-protocol noise.
- **Packaging / CI**: `test_packaging.py`, `test_ci_workflows.py` — installable
  artifact + workflow file invariants.

## 3. What to test, by component type

- **Services** (`services/`): business logic in isolation against a `tmp_path`
  SQLite fixture. Cover the happy path, every error branch (`DocsServerError`
  subclasses), and token-budget trimming.
- **Retrieval/ranking** (`retrieval/`): query parsing, FTS5 behavior, ranker
  ordering. Use property assertions (`>= 1 result`, substring match) over exact
  content so upstream doc edits don't break the suite.
- **Ingestion** (`ingestion/`): parse valid + deliberately broken `.fjson`
  fixtures; assert idempotency on re-publish.
- **Server layer** (`server.py`): thin — it only delegates to services and maps
  `DocsServerError → ToolError`. Cover that mapping via stdio smoke, not unit tests.
- **Detection** (`detection.py`): pure environment probing — see gap below.

## 4. Coverage targets

No line-coverage gate is enforced (no `pytest-cov` in the dev deps). The bar is
**behavioral**, not numeric:

- Every public tool has ≥1 happy-path + ≥1 error-path test.
- Every `errors.py` exception type is raised by at least one test.
- Every wire-facing model is pinned by a schema snapshot.

Adopt these as the definition of done for new tools. A line-coverage gate is
optional future work; if added, target the `services/` and `retrieval/`
packages, not `server.py` (intentionally thin) or `__main__.py`.

## 5. Known gaps

1. **`detection.py` — CLOSED (2026-05-29).** `tests/test_detection.py` now
   covers all three branches of the fallback chain (`.python-version` file →
   `python3` in PATH → `sys.version_info`), `_parse_major_minor` parsing, and
   `match_to_indexed` — 12 tests. The isolation pattern (`monkeypatch.chdir` to
   escape a real `.python-version`, `monkeypatch.setattr` on `subprocess.run`)
   is the reference for testing order-dependent environment probing.
2. **`lookup_package_docs` has no stdio smoke (LOW).** Covered at the service
   layer only; the PyPI-allowlist trust boundary is never exercised end-to-end.
3. **No negative version-resolution E2E (LOW).** Unknown-version errors are
   unit-tested but not asserted over the stdio boundary.

## 6. Reference cases — `detection.py` (now implemented in `test_detection.py`)

| Case                                   | Expectation                              |
|----------------------------------------|------------------------------------------|
| `.python-version` file present in cwd  | returns `(version, ".python-version file")` |
| `.python-version` malformed / empty    | falls through to next source, no crash   |
| no file, `python3` on PATH             | returns `(version, "python3 in PATH")`   |
| no file, no `python3`                  | returns runtime `(X.Y, "server runtime")`|
| `_parse_major_minor("Python 3.13.2")`  | `"3.13"`                                  |
| `_parse_major_minor("no digits here")` | `None`                                    |
| `match_to_indexed("3.13", ["3.13"])`   | `"3.13"`                                  |
| `match_to_indexed("3.9", ["3.13"])`    | `None`                                    |
