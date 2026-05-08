# PR #9 MCP Test Plan — Persistent `get_docs` Cache + `lookup_package_docs`

PR: https://github.com/ayhammouda/python-docs-mcp-server/pull/9  
Branch: `fix/open-issues-cache-pypi-docs`  
Purpose: validate the PR through actual MCP/tool-level behavior, not only unit tests.

## Goals

Confirm that:

1. Existing stdlib docs tools still work.
2. `get_docs` returns correct content.
3. Persistent cache is written and reused across server restarts.
4. Cache identity is correct: version, slug, anchor, `max_chars`, `start_index`, and index fingerprint matter.
5. Cache failure is best-effort and does not break retrieval.
6. `lookup_package_docs` returns controlled PyPI-declared docs/homepage/source/repository links.
7. PyPI error modes return controlled notes rather than internal errors.
8. MCP annotations and tool count are coherent.

## Preconditions

```bash
cd /srv/openclaw/.openclaw/workspace/tmp/python-docs-mcp-review
git checkout fix/open-issues-cache-pypi-docs || git checkout review-pr-9
git pull --ff-only origin fix/open-issues-cache-pypi-docs
uv sync --all-extras
```

Baseline gates:

```bash
uv run ruff check src/ tests/
uv run pyright src/
uv run pytest --tb=short -q
uv build
```

Expected:

- Ruff passes.
- Pyright passes for `src/`.
- Pytest passes: currently expected `254 passed, 3 skipped`.
- Build succeeds.

## Test Area 1 — Tool Registration / MCP Contract

### 1.1 Verify five tools are registered

Run a small introspection script or use the MCP client harness if available.

Expected tools:

- `search_docs`
- `get_docs`
- `lookup_package_docs`
- `list_versions`
- `detect_python_version`

Expected annotations:

- stdlib tools: `readOnlyHint=True`, `openWorldHint=False`
- `lookup_package_docs`: `readOnlyHint=True`, `openWorldHint=True`

Pass criteria:

- Exactly five tools are exposed.
- `lookup_package_docs` is visibly open-world because it calls PyPI.

## Test Area 2 — `get_docs` Functional Retrieval

### 2.1 Full page retrieval

Call:

```text
get_docs(slug="library/json.html", version="3.12", max_chars=1000, start_index=0)
```

Expected:

- Result contains JSON documentation content.
- `slug == "library/json.html"`
- `version == "3.12"`
- `anchor is null`
- `char_count > 0`

### 2.2 Section retrieval

First use `search_docs` to find a valid section anchor for `json`, then call:

```text
get_docs(slug="library/json.html", version="3.12", anchor=<valid_anchor>, max_chars=1000, start_index=0)
```

Expected:

- Result is section-scoped.
- `anchor == <valid_anchor>`
- Content is not the full page.

### 2.3 Empty anchor remains invalid

Call:

```text
get_docs(slug="library/json.html", version="3.12", anchor="", max_chars=1000, start_index=0)
```

Expected:

- Controlled tool error / page-not-found style response.
- It must **not** return a cached full-page response.

This specifically verifies the `anchor=None` vs `anchor=""` cache fix.

## Test Area 3 — Persistent Cache Behavior

Before running, locate cache path from platform cache dir. Expected filename:

```text
retrieved-docs-cache.sqlite3
```

Likely under:

```text
~/.cache/mcp-python-docs/retrieved-docs-cache.sqlite3
```

or the platform cache directory used by the app.

### 3.1 Cache file creation

1. Delete the cache file if present.
2. Start the MCP server/client.
3. Call `get_docs` for `library/json.html`.
4. Stop the server.

Expected:

- Cache file exists.
- SQLite table `retrieved_docs_cache` exists.
- At least one row is present.

Suggested inspection:

```bash
sqlite3 <cache-path>/retrieved-docs-cache.sqlite3 \
  "SELECT version, slug, anchor, max_chars, start_index, length(result_json) FROM retrieved_docs_cache;"
```

### 3.2 Cache survives restart

1. Start server again.
2. Call the same `get_docs` request.

Expected:

- Same response content.
- No user-visible behavior change.
- If logs expose cache hits/misses, second call should be a hit.

### 3.3 Cache key separates pagination/budget

Call:

```text
get_docs(slug="library/json.html", version="3.12", max_chars=500, start_index=0)
get_docs(slug="library/json.html", version="3.12", max_chars=1000, start_index=0)
get_docs(slug="library/json.html", version="3.12", max_chars=500, start_index=100)
```

Expected:

- Separate cache rows for each identity.
- Results are not cross-contaminated.

### 3.4 Corrupt cache is best-effort

1. Stop server.
2. Replace cache file with invalid bytes:

```bash
printf 'not sqlite' > <cache-path>/retrieved-docs-cache.sqlite3
```

3. Start server.
4. Call `get_docs(slug="library/json.html", version="3.12")`.

Expected:

- Docs retrieval still succeeds.
- Warning is logged about disabled/skipped persistent cache.
- No internal server error.

## Test Area 4 — `lookup_package_docs` Happy Path

### 4.1 Known package with docs/source

Call:

```text
lookup_package_docs(package="requests")
```

Expected:

- `metadata_source == "https://pypi.org/pypi/requests/json"`
- `trust_boundary == "pypi-declared-metadata"`
- `package` is canonical from PyPI if available.
- `version` is non-empty.
- `sources` includes PyPI project URL and likely homepage/source/docs links.
- Every source URL is `http://` or `https://`.
- No web search / unofficial mirror fallback.

### 4.2 Normalization

Call:

```text
lookup_package_docs(package="Sample_Project")
```

Expected:

- Metadata source normalizes to:

```text
https://pypi.org/pypi/sample-project/json
```

- Returned package may be PyPI canonical name.

### 4.3 Missing package

Call:

```text
lookup_package_docs(package="definitely-not-a-real-package-vision-test-xyz")
```

Expected:

- `sources == []`
- note contains package not found / PyPI 404 style message.
- No internal error.

## Test Area 5 — PyPI Failure Handling

These may require monkeypatching/fake fetcher or temporary network blocking if not practical via live MCP.

### 5.1 Non-404 HTTP errors

Simulate PyPI `429` or `503`.

Expected:

- Controlled result:

```text
sources=[]
note="PyPI returned HTTP 429."
```

or equivalent code.

### 5.2 Network/JSON failure

Simulate:

- `URLError`
- timeout
- invalid JSON body

Expected:

- Controlled result note:

```text
Unable to retrieve PyPI metadata: <ErrorType>.
```

- No internal server error.

### 5.3 Oversized PyPI JSON body

Simulate a response larger than 5 MiB.

Expected:

- The service reads at most `5 MiB + 1 byte`.
- Controlled result:

```text
sources=[]
note="PyPI metadata exceeded size limit."
```

## Test Area 6 — Scope / Trust Boundary

Use a package or fake response with broad `project_urls`, e.g. labels:

- `Documentation`
- `Homepage`
- `Source`
- `Repository`
- `Issues`
- `Changelog`
- `Community mirror`
- `Tutorial`

Expected:

Included:

- Documentation
- Homepage
- Source
- Repository

Excluded/skipped:

- Issues
- Changelog
- Community mirror
- Tutorial

Result note should mention ignored labels outside controlled allowlist.

## Suggested Manual MCP Smoke Script

If direct MCP client execution is awkward, use a minimal Python script that imports the services and mimics the tool layer:

```python
from mcp_server_python_docs.services.package_docs import PackageDocsService

for pkg in ["requests", "Sample_Project", "definitely-not-a-real-package-vision-test-xyz"]:
    print(PackageDocsService().lookup(pkg).model_dump())
```

For `get_docs`, prefer actual MCP invocation or server lifespan because cache path wiring happens there.

## Pass / Fail Summary Template

Return results in this format:

```markdown
## PR #9 MCP Test Results

### Environment
- Commit:
- OS:
- Python:
- Cache path:

### Gates
- ruff:
- pyright src:
- pytest:
- uv build:

### MCP Tool Tests
- Tool registration:
- get_docs full page:
- get_docs section:
- empty anchor behavior:
- cache file creation:
- cache survives restart:
- cache key separation:
- corrupt cache fallback:
- lookup_package_docs requests:
- missing package:
- failure simulation:
- scope/trust boundary:

### Verdict
PASS / FAIL

### Notes / Bugs Found
- ...
```

## Final Acceptance Criteria

The PR is considered MCP-smoke-test ready if:

- Local gates pass.
- Live MCP/tool invocation returns correct stdlib docs.
- Cache file is created and reused after restart.
- Corrupt cache does not break docs retrieval.
- PyPI lookup returns only controlled PyPI-declared metadata.
- PyPI expected failures return controlled notes.
- No internal errors are observed for expected failure modes.
