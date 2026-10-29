# MCP Python SDK v2 Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move `python-docs-mcp-server` from `mcp` 1.30.0 to 2.3.0 without
changing any tool's name, parameters or results.

**Architecture:** One PR on branch `feat/mcp-sdk-v2`, cut from `main` in a git
worktree (superpowers:using-git-worktrees), so that this checkout stays on the
docs branch. Task 1 is the atomic bump. The lockfile, server, tests and
benchmark adapters must move together because the v1 and v2 type surfaces are
incompatible. Tasks 2 and 3 are separate behavior decisions that a reviewer can
reject on their own. Task 4 updates the docs, and Task 5 verifies everything
and opens the PR.

**Tech Stack:**
- `mcp` 2.3.0: `MCPServer`, and `mcp.types` as an alias of `mcp_types`
- Pydantic ≥ 2.12
- `httpx2`, which `mcp` installs
- uv
- pytest with pytest-asyncio (`asyncio_mode = "auto"`)
- Pyright in standard mode
- Ruff

**Spec:** `docs/benchmarks/AGENTIC-BENCHMARK-DESIGN.md` §4. The file-by-file
checklist is §5 of `docs/research/2026-10-25-mcp-python-sdk-v2-migration.md`.
Its line numbers were rechecked against `main` at `e4f8b71` on 2026-10-29.

## Global Constraints

- `pyproject.toml` dependencies are `"mcp>=2.3.0,<3"` and `"pydantic>=2.12,<3.0"`.
  Nothing else in `[project]` changes.
- Tool names, parameters and result shapes stay the same on the wire
  (`AGENT-EXECUTION-PIPELINE.md` §2).
- No runtime dependency beyond what `mcp` 2.3.0 installs, and no new runtime
  network access.
- `MCPServer(...)` gets everything after `name` as keywords, because v2
  changed the positional order.
- `lookup_package_docs` stays as it is.
- Existing tests may only be renamed. Never delete a test or weaken an
  assertion.
- Do not touch `.github/workflows/`.
- Commits use the repo's conventional prefixes and end with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Gate (`AGENTS.md`), run fresh before the PR:
  - `uv run --locked ruff check src/ tests/ benchmarks/ ops/ .github/scripts/`
  - `uv run --locked pyright src/ benchmarks/`
  - `uv run --locked pytest --tb=short -q`

## Checked on 2026-10-29 against `mcp` 2.3.0 (scratch venv, Python 3.12)

- **Pyright on `server.py` reports 15 errors under v2.**
  - 8 errors come from `ToolAnnotations` camelCase keywords. They still build
    at runtime, but Pyright rejects them.
  - 7 errors come from a bare `Context`, which types `lifespan_context` as
    `dict[str, Any]`.
  - With snake_case keywords and `ctx: Context[AppContext]`, Pyright reports
    0 errors.
- **Pyright on `benchmarks/` reports 7 errors.**
  - 2 are unresolved `import httpx`.
  - 5 are a `ClientSession` that no longer matches the adapters' camelCase
    `_CallToolSession` protocol.
  - Pyright does not catch the 3-value `streamable_http_client` unpacking,
    because the transport factories return `Any`.
- **`initialize` at `2025-11-25` negotiates `2025-11-25`.** `serverInfo.version`
  is:
  - the SDK version (`1.30.0`) on v1;
  - `""` on v2 without `version=`;
  - the package version on v2 with `version=`.
- **On v2, `server/discover` with `_meta` works.** It returns
  `supportedVersions: ["2026-07-28"]` and
  `_meta["io.modelcontextprotocol/serverInfo"]`. A `tools/call` that carries
  the same `_meta` and skips `initialize` returns `structuredContent` and
  `isError`. v1 answers both requests with error `-32602`.
- **Client disconnect leaves a traceback today.** Test setup: the parent closes
  its read end, then sends `initialize`.
  - The server exits with code 120 on v1 and 1 on v2.
  - Both print a traceback of an `ExceptionGroup` that wraps `BrokenPipeError`.
  - With `except* BrokenPipeError`, v2 exits 0 with no traceback in 3 runs out
    of 3.
- **`httpx` is not installed under v2.**
  `httpx2.AsyncClient(headers=..., timeout=httpx2.Timeout(30, read=300))`
  builds. `streamable_http_client(url, http_client=client)` returns a context
  manager without connecting.

## Review Focus

1. **2025-era handshake.** A host sends `initialize` at `2025-11-25`, which is
   what `ClientSession.initialize()` sends. Expected: that version is
   negotiated and `serverInfo.version` is the package version. Pinned by
   Task 1, `test_handshake_2025_11_25_reports_package_version`.
2. **2026-07-28 client.** The client skips `initialize` and calls
   `server/discover`, then `tools/call`. Expected: `2026-07-28` is advertised
   and the call succeeds. Pinned by Task 1,
   `test_server_discover_serves_2026_07_28_clients`.
3. **Pipelined calls.** A host sends several tool calls without waiting.
   Expected: the calls run one at a time on the event-loop thread, never
   concurrently on the shared `sqlite3.Connection`. Pinned by Task 2,
   `test_docs_tools_are_async`.
4. **Client drops the pipe mid-session.** Expected: exit code 0 and no
   traceback in the host's log. Pinned by Task 3,
   `test_client_disconnect_exits_cleanly`.
5. **A tool fails, for example on an unknown version.** Expected: an
   `isError: true` result with the message, not a JSON-RPC error. Already
   pinned by `tests/test_mcp_get_docs_cache_smoke.py:118`, which runs unchanged
   under v2, so no new test.

---

### Task 1: Move to `mcp` 2.3.0

**Files:**
- Modify: `pyproject.toml:26,28`, `uv.lock` (regenerated), `CHANGELOG.md` (new top section)
- Modify: `src/mcp_server_python_docs/server.py:22-23,121,223-234,294-299` and the seven `ctx` parameters
- Modify: `tests/test_services.py:441-448,470,492,502`, `tests/test_package_docs.py:77-78`
- Modify: `benchmarks/adapters/` `context7_adapter.py`, `deepwiki_adapter.py`, `gitmcp_adapter.py`, `ref_tools_adapter.py`, `python_docs_mcp_adapter.py`
- Modify: `tests/benchmarks/test_python_docs_mcp_adapter.py:38-39,64-88,171`, `tests/benchmarks/test_competitor_adapters.py:85-86,116,193,196,316,319,393,438,441`
- Test: `tests/test_stdio_smoke.py` (two new tests in `TestStdioSmoke`)

**Interfaces:**
- Produces:
  - `create_server() -> MCPServer`
  - `app_lifespan(server: MCPServer) -> AsyncIterator[AppContext]`
  - each tool's `ctx` parameter typed `Context[AppContext]`
  - all other signatures unchanged

- [ ] **Step 1: Write the failing smoke tests.** Add them to `TestStdioSmoke`
  and add `from mcp_server_python_docs import __version__` to the imports.

```python
    def test_handshake_2025_11_25_reports_package_version(self):
        """A 2025-era host gets its handshake version and our package version."""
        stdin_data = (
            _make_request("initialize", {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.1"},
            }, req_id=1)
            + _make_notification("notifications/initialized")
            + _make_request("tools/call", {"name": "list_versions", "arguments": {}}, req_id=2)
        )
        responses = _assert_protocol_on_stdout_only(self._run_server_with_input(stdin_data))

        init = _find_response(responses, 1)["result"]
        assert init["protocolVersion"] == "2025-11-25"
        assert init["serverInfo"]["name"] == "python-docs-mcp-server"
        assert init["serverInfo"]["version"] == __version__
        call = _find_response(responses, 2)["result"]
        assert call.get("isError") is not True
        assert call["structuredContent"]["versions"]

    def test_server_discover_serves_2026_07_28_clients(self):
        """A 2026-07-28 client skips initialize: server/discover, then tools/call."""
        meta = {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {},
        }
        stdin_data = (
            _make_request("server/discover", {"_meta": meta}, req_id=1)
            + _make_request("tools/call", {
                "name": "list_versions", "arguments": {}, "_meta": meta,
            }, req_id=2)
        )
        responses = _assert_protocol_on_stdout_only(self._run_server_with_input(stdin_data))

        discover = _find_response(responses, 1)["result"]
        assert "2026-07-28" in discover["supportedVersions"]
        assert discover["_meta"]["io.modelcontextprotocol/serverInfo"]["version"] == __version__
        call = _find_response(responses, 2)["result"]
        assert call.get("isError") is not True
        assert call["structuredContent"]["versions"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --locked pytest tests/test_stdio_smoke.py -q -k "handshake_2025 or server_discover"`

Expected: 2 failed.
- The first fails with `assert '1.30.0' == '<package version>'`.
- The second fails with `KeyError: 'result'`, because v1 answers
  `server/discover` with error `-32602`.

- [ ] **Step 3: Bump the pins and the lock.**
  - In `pyproject.toml`, change `"mcp>=1.30.0,<2.0.0"` to `"mcp>=2.3.0,<3"`.
  - Change `"pydantic>=2.0.0,<3.0"` to `"pydantic>=2.12,<3.0"`.
  - Run: `uv lock --upgrade-package mcp && uv sync --locked --dev`

  Expected:
  - `mcp` goes from 1.30.0 to 2.3.0.
  - `mcp-types`, `httpx2` and `opentelemetry-api` are added.
  - `httpx`, `httpx-sse` and `pydantic-settings` are removed.

  Don't pin `mcp-types`, because `mcp` pins it exactly.

- [ ] **Step 4: Port `server.py`.**
  - Change lines 22-23 to `from mcp.server.mcpserver import Context, MCPServer`
    and `from mcp.server.mcpserver.exceptions import ToolError`.
  - Keep line 24, because `mcp.types` is a permanent alias.
  - Add `from mcp_server_python_docs import __version__`.
  - Use `app_lifespan(server: MCPServer)` and `create_server() -> MCPServer`.
  - Make the constructor
    `MCPServer("python-docs-mcp-server", version=__version__, lifespan=app_lifespan)`.
  - `_TOOL_ANNOTATIONS` and `_PYPI_TOOL_ANNOTATIONS` take `read_only_hint`,
    `destructive_hint`, `idempotent_hint` and `open_world_hint`, with the same
    values.
  - Change every `ctx: Context = None,  # type: ignore[assignment]` to
    `ctx: Context[AppContext] = None,  # type: ignore[assignment]`.

- [ ] **Step 5: Port the existing tests (renames only).**
  - In `tests/test_services.py:441-448` and `tests/test_package_docs.py:77-78`,
    rename `.readOnlyHint`, `.destructiveHint` and `.openWorldHint` to
    `.read_only_hint`, `.destructive_hint` and `.open_world_hint`.
  - In `tests/test_services.py:470`, rename `tool.inputSchema` to
    `tool.input_schema`.
  - In `tests/test_services.py:492,502`, change
    `from mcp.server.fastmcp import FastMCP` to
    `from mcp.server.mcpserver import MCPServer`, and `FastMCP("test")` to
    `MCPServer("test")`.

- [ ] **Step 6: Port the five benchmark adapters and their fakes.**
  - In each `_CallToolResult` protocol and every reader of it, rename
    `structuredContent` to `structured_content` and `isError` to `is_error`.
    The adapter lines are listed in research §5.4.
  - v2 yields a 2-tuple, so unpack `as (read, write)` in
    `deepwiki_adapter.py:203`, `gitmcp_adapter.py:270` and
    `ref_tools_adapter.py:278`.
  - In `context7_adapter.py:240-255` and `ref_tools_adapter.py:207-221`, build
    the client with `import httpx2` and
    `httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(30, read=300))`.
    Drop `follow_redirects`, because the v2 transport follows same-origin
    redirects itself. Reword the comment above each client to match.
  - In the docstring at `python_docs_mcp_adapter.py:122-125`, write
    `structured_content` instead of `structuredContent` and MCPServer instead
    of FastMCP.
  - In both test files, rename the same two fields in `_FakeCallToolResult`
    and in every keyword argument that builds one.
  - Leave the `tool_failure` / `mcp_protocol_crash` classification alone
    (research B17). Design §11 deletes these adapters.

- [ ] **Step 7: Add the changelog entry.** Put it directly above
  `## [0.4.0] — Unreleased`. Version 0.4.0 is already tagged (`ee79c2b`) and on
  PyPI. Leave its header alone.

```markdown
## [Unreleased]

### Changed

- Moved to version 2 of the MCP Python SDK (`mcp>=2.3.0,<3`), which needs
  `pydantic>=2.12`. The server still answers 2025-era clients through the
  `initialize` handshake. It now also serves 2026-07-28 clients through
  `server/discover`. It reports its own package version as
  `serverInfo.version` instead of the SDK's. Tool names, parameters and
  results are unchanged.
```

- [ ] **Step 8: Run the gate.**

| Run | Expected |
|---|---|
| `uv run --locked ruff check src/ tests/ benchmarks/ ops/ .github/scripts/` | `All checks passed!` |
| `uv run --locked pyright src/ benchmarks/` | `0 errors` |
| `uv run --locked pytest --tb=short -q` | All tests pass, including the two new ones |
| `uv lock --check` | Exit code 0 |

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml uv.lock CHANGELOG.md src/ tests/ benchmarks/
git commit -m "feat: move to MCP Python SDK v2 (mcp 2.3.0)"
```

### Task 2: Keep the database-backed tools on the event loop

**Why:** v2 runs sync (`def`) tools on worker threads, where they "can run
concurrently with each other". Six tools share one `sqlite3.Connection`. With
`async def`, they run one at a time on the event loop, exactly as v1 ran them.
That holds on every SQLite build, including the optional `pysqlite3-binary`
extra, whose thread safety nobody has checked.

**Files:**
- Modify: `src/mcp_server_python_docs/server.py` (the six tool definitions)
- Modify: `tests/test_retrieval_regression.py:218` (+ `import asyncio`)
- Test: `tests/test_services.py` (`TestToolRegistration`)

**Interfaces:**
- Consumes: `create_server() -> MCPServer` (Task 1).
- Produces: `search_docs`, `get_docs`, `list_versions`, `detect_python_version`,
  `compare_versions` and `whatsnew_for_version` become coroutine functions.
  Their parameters, return types and bodies don't change.

- [ ] **Step 1: Write the failing test**

```python
    def test_docs_tools_are_async(self):
        """MCP SDK v2 runs sync tools on worker threads. These six share one
        sqlite3.Connection, so they stay async and run on the event loop."""
        import inspect

        from mcp_server_python_docs.server import create_server

        tools = create_server()._tool_manager._tools
        for name in (
            "search_docs", "get_docs", "list_versions",
            "detect_python_version", "compare_versions", "whatsnew_for_version",
        ):
            assert inspect.iscoroutinefunction(tools[name].fn), name
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --locked pytest tests/test_services.py -q -k docs_tools_are_async`
Expected: FAIL with `AssertionError: search_docs`

- [ ] **Step 3: Change `def` to `async def` on the six definitions in `create_server()`.**
  Change nothing else. The bodies stay synchronous and block the loop on
  purpose, as v1 did.

- [ ] **Step 4: Await the raw tool call in the regression test.**
  `tests/test_retrieval_regression.py:218` calls the tool function directly, and
  it now returns a coroutine. Make it
  `result = asyncio.run(tool.fn(ctx=ctx, **case["input"]))` and add
  `import asyncio`.

- [ ] **Step 5: Run the affected tests**

Run: `uv run --locked pytest tests/test_services.py tests/test_retrieval_regression.py tests/test_stdio_smoke.py -q`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add src/mcp_server_python_docs/server.py tests/test_services.py tests/test_retrieval_regression.py
git commit -m "fix: keep database-backed tools off MCP SDK v2 worker threads"
```

### Task 3: Exit cleanly when the client disconnects

This bug was found while planning and also exists on v1. ADR-008 promises "a
clean process exit (including on client disconnect via `BrokenPipeError`)".
The SDK's stdout writer raises `BrokenPipeError` inside an `ExceptionGroup`, so
the `except BrokenPipeError` in `serve()` never matches.

**Files:**
- Modify: `src/mcp_server_python_docs/__main__.py:111-112`, `CHANGELOG.md`
- Test: `tests/test_stdio_smoke.py` (`TestStdioSmoke`)

**Interfaces:** none. `serve()` keeps its signature.

- [ ] **Step 1: Write the failing test**

```python
    def test_client_disconnect_exits_cleanly(self):
        """HYGN-03: a client that drops its end of the pipe gets exit 0, no traceback."""
        proc = subprocess.Popen(
            [sys.executable, "-m", "mcp_server_python_docs", "serve"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=self.env,
        )
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdout.close()  # the server's first reply hits a closed pipe
        proc.stdin.write(_make_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0.1"},
        }, req_id=1))
        proc.stdin.flush()
        _, stderr = proc.communicate(timeout=20)

        assert proc.returncode == 0, stderr.decode()[-2000:]
        assert b"Traceback" not in stderr
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --locked pytest tests/test_stdio_smoke.py -q -k client_disconnect`

Expected: FAIL with `assert 1 == 0`. The stderr tail shows `ExceptionGroup`
wrapping `BrokenPipeError`.

- [ ] **Step 3: Change `except BrokenPipeError:` to `except* BrokenPipeError:`.**
  This form matches the bare exception and one wrapped in a group. Add one
  clause to the trailing comment saying that the SDK raises it inside an
  `ExceptionGroup`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run --locked pytest tests/test_stdio_smoke.py -q`
Expected: all passed

- [ ] **Step 5: Add the changelog entry.** Put it under Task 1's
  `## [Unreleased]`, after `### Changed`.

```markdown
### Fixed

- The server now exits cleanly, with exit code 0 and no traceback, when a
  client closes the connection mid-session.
```

- [ ] **Step 6: Commit**

```bash
git add src/mcp_server_python_docs/__main__.py tests/test_stdio_smoke.py CHANGELOG.md
git commit -m "fix: exit cleanly when an MCP client disconnects mid-session"
```

### Task 4: Update docs and comments for MCPServer

**Files:**
- Modify: `docs/architecture/ADR-008-transport.md:12,49,65,90,123,128-134`, `docs/architecture/DESIGN.md:415,471`
- Modify these comments and docstrings that name FastMCP:
  - `src/mcp_server_python_docs/server.py:1,442-446`
  - `__main__.py:104-106`
  - `models.py:3-4,227`
  - `app_context.py:1`
  - `services/__init__.py:3`
  - `services/observability.py:4`
  - `tests/test_services.py:422-428`
  - `tests/test_retrieval_regression.py:145`

- [ ] **Step 1: Replace FastMCP in prose.** Write "MCPServer" at each listed
  location. Where a sentence names the framework rather than the class, write
  "the MCP Python SDK".
- [ ] **Step 2: Reword the ADR-008 invariant.** Replace the first clause of the
  Invariants bullet (lines 128-129) with:
  > The client's stdout pipe carries only JSON-RPC protocol frames. Before `run()`, the import-time fd swap keeps stray output off fd 1. During `run()`, the MCP SDK (v2) writes frames through a private duplicate of the restored fd 1 and points fd 1 itself at stderr, so even a stray `print()` inside a tool lands on stderr.

  Keep the rest of the bullet. After "SIGPIPE is ignored so client disconnect
  cannot crash the process with a signal", add "; `serve()` catches
  `BrokenPipeError` with `except*` because the SDK raises it inside an
  `ExceptionGroup`". Drop that clause if Task 3 was dropped.
- [ ] **Step 3: Correct the comment at `__main__.py:104-106`.** It becomes:
  > The SDK serves the wire on fd 1 only when `sys.stdout` is backed by fd 1; otherwise it writes through `sys.stdout` itself. Restore it as well as fd 1, otherwise protocol frames go to stderr because module import redirected `sys.stdout` there.
- [ ] **Step 4: Correct the comment at `server.py:442-446`.** It is wrong on v1
  as well as v2. It becomes:
  > SRVR-07: a `_meta` hint for get_docs could be set with `@mcp.tool(meta=...)`. It is not set; the hint is advisory and clients work correctly without it.
- [ ] **Step 5: Verify.**
  - Run: `grep -rn -E "FastMCP|fastmcp" src tests benchmarks docs/architecture`
    Expected: no output.
  - Run: `uv run --locked ruff check src/ tests/ benchmarks/ ops/ .github/scripts/`
    Expected: `All checks passed!`
- [ ] **Step 6: Commit**

```bash
git add docs/architecture src tests
git commit -m "docs: describe the MCP SDK v2 server and stdio wire"
```

### Task 5: Verify, run QA, open the PR

- [ ] **Step 1: Run the full gate in a fresh shell.** Expected: every command
  passes or exits 0.
  - the three `AGENTS.md` commands
  - `uv run --locked python-docs-mcp-server doctor`
  - `uv run --locked pytest tests/test_stdio_smoke.py -q`
  - `uv lock --check`
  - `uv pip compile --quiet pyproject.toml -o /tmp/requirements-check.txt`
- [ ] **Step 2: Manual QA (a human step).** Follow `.github/INTEGRATION-TEST.md`
  with MCP Inspector and one real client (Claude Desktop, Codex or Cursor).
  Record for the PR the client, its version, and a pass or fail per tool.
- [ ] **Step 3: Rebase if needed.** `feat/minors-fixes` also rewrites `uv.lock`
  and adds changelog lines. If it reached `main` first:
  - rebase onto `main`;
  - rerun `uv lock --upgrade-package mcp`;
  - rerun Step 1.
- [ ] **Step 4: Get the user's go-ahead before pushing.** The PR cites the
  design as the owner's approval, so `docs/agentic-benchmark-design` must be on
  GitHub first, in its own PR. Then push `feat/mcp-sdk-v2` and open a PR
  against `main` titled `feat: move to MCP Python SDK v2 (mcp 2.3.0)`. The body
  includes:
  - a summary and `Refs #63`;
  - an acceptance checklist covering the gate, the smoke tests and QA;
  - the gate output;
  - a "Why this approach" section covering the async tools, `httpx2` and
    `except*`;
  - a `## Why this triggered supervisor review` section listing three
    triggers: the `[project]` dependencies changed, `mcp` jumped a major
    version (1 to 2), and six tools moved from sync to async code. It cites
    `docs/benchmarks/AGENTIC-BENCHMARK-DESIGN.md` §4 as the owner's approval;
  - the CodeRabbit triage, once CodeRabbit comments;
  - the closing line `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
