# MCP Python SDK v2 migration research

- **Date:** 2026-10-25
- **Scope:** Move `python-docs-mcp-server` from `mcp>=1.30.0,<2.0.0` to the latest stable v2 release of the MCP Python SDK.
- **Status:** Research only. No product code was changed.
- **Sources:** Primary sources only: the `modelcontextprotocol/python-sdk` repository (release notes, `docs/migration.md`, and source at tag `v2.3.0`), PyPI JSON, and the MCP specification at modelcontextprotocol.io. Anything marked **(inferred)** is my reasoning, not text I read in a source.

## TL;DR

- The latest stable v2 is **`mcp` 2.3.0**, released 2026-10-02 and requiring Python `>=3.10`. It speaks protocol revision **2026-07-28** and every handshake revision from **2024-11-05 to 2025-11-25**, all from one server.
- Most of this repo's server code carries over. The decorator API, `lifespan=`, `ctx.request_context.lifespan_context`, `ToolError` semantics, Pydantic structured output and `run(transport="stdio")` are unchanged. The two `mcp.server.fastmcp` imports must move to `mcp.server.mcpserver`.
- **Empirical check:** I installed `mcp==2.3.0` in an isolated scratch venv and ran a scratch copy of this repo's test suite against it. With 3 import-line edits, **641 tests passed and 3 failed**. All 3 failures were camelCase attribute access in tests. After renaming those attributes to snake_case, **645 tests passed**. This includes the real stdio subprocess smoke tests, which still negotiate `2024-11-05`.
- **The biggest risk is in `benchmarks/adapters/`, not in the server.** The adapters read `result.structuredContent` and `result.isError`, unpack a 3-tuple from `streamable_http_client`, and `import httpx`. All of these break on v2. The unit tests use camelCase fakes, so they stay green while live benchmark runs fail.
- **One behaviour change needs a decision:** in v2, sync (`def`) tools run on worker threads. Six of the seven tools here are sync and share one `sqlite3.Connection`.
- **Interop:** a v2 `MCPServer` is "dual-era". It still answers `initialize` from 2025-era hosts (Claude Desktop, Codex, Cursor). It also answers `server/discover` from 2026-07-28 clients, which a v1 server cannot do.

---

## 1. Target version facts

| Fact | Value | Source |
|---|---|---|
| Latest stable v2 | **2.3.0** (`info.version` on PyPI; GitHub marks `v2.3.0` as "Latest") | https://pypi.org/pypi/mcp/json ; `gh release list -R modelcontextprotocol/python-sdk` ; https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.3.0 |
| Release date | GitHub release 2026-10-02T22:02:27Z; PyPI wheel upload 2026-10-02T22:06:52Z | same as above ; https://pypi.org/pypi/mcp/2.3.0/json |
| Tag commit | `2118f14f8a19bc158d8a1cf90af58d85d187f849` | `gh api repos/modelcontextprotocol/python-sdk/commits/v2.3.0` |
| `requires_python` | `>=3.10`. This repo requires `>=3.12`, so no conflict. | https://pypi.org/pypi/mcp/json |
| Other 2.x releases | 2.0.0 (2026-07-28), 2.0.1 (2026-08-26, `v2.0.x` backport), 2.1.0 (2026-08-24), 2.1.1 (2026-08-25), 2.2.0 (2026-09-07). Pre-releases ran from 2.0.0a1 (2026-06-11) to 2.0.0rc1. | https://github.com/modelcontextprotocol/python-sdk/releases |
| v1 line | Still maintained in parallel: 1.30.0 shipped 2026-09-07, the same day as 2.2.0. VERSIONING.md says 1.x gets "critical bug fixes and security fixes". The 2.0.0 notes say 1.x "will only receive security fixes". The two wordings differ slightly. | https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/VERSIONING.md ; https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.0.0 |
| Runtime deps of 2.3.0 | `anyio>=4.9` (`>=4.10` on 3.14), `httpx2>=2.10.0`, `jsonschema>=4.20.0`, `mcp-types==2.3.0`, `opentelemetry-api>=1.28.0`, `pydantic>=2.12.0`, `pyjwt[crypto]>=2.10.1`, `python-multipart`, `sse-starlette>=3.0.0`, `starlette`, `typing-extensions>=4.13.0`, `typing-inspection`, `uvicorn` | https://pypi.org/pypi/mcp/json |
| Deps dropped vs 1.30.0 | `httpx`, `httpx-sse`, `pydantic-settings`. (1.30.0 deps: https://pypi.org/pypi/mcp/1.30.0/json) | https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md#L64-L100 |
| Pydantic | `>=2.12.0`, and the v1 `<3` cap is gone. This repo pins `pydantic>=2.0.0,<3.0` and locks 2.13.5, so it is compatible. The effective floor becomes 2.12. | https://pypi.org/pypi/mcp/json ; https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md#L68-L72 |

### Protocol revisions and default negotiation

| Question | Answer | Source |
|---|---|---|
| Revisions implemented | `KNOWN_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25", "2026-07-28")`. Handshake (legacy) revisions are the first four; `MODERN_PROTOCOL_VERSIONS = ("2026-07-28",)`. | https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp-types/mcp_types/version.py#L24-L56 |
| Headline | The 2.0.0 notes say it "supports the 2026-07-28 revision of the Model Context Protocol and serves every earlier revision from the same server". | https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.0.0 |
| Server: legacy `initialize` | The server echoes the requested version if it is in `HANDSHAKE_PROTOCOL_VERSIONS`. Otherwise it counters with `LATEST_HANDSHAKE_VERSION` = **`2025-11-25`**. | https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/runner.py#L421-L426 |
| Empirical check of the server | A raw stdio `initialize` with `2024-11-05` negotiated `2024-11-05`. With `2099-01-01` it negotiated `2025-11-25`. `server/discover` returned `supportedVersions: ["2026-07-28"]`. | Scratch probe (Section 7) |
| Client: high-level `Client` | Defaults to `mode="auto"`. It probes `server/discover` at `2026-07-28`, then falls back to `initialize`. `mode="legacy"` forces the handshake. | https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/protocol-versions.md ; https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md#L1790-L1798 |
| Client: low-level `ClientSession.initialize()` | Always offers `LATEST_HANDSHAKE_VERSION` (`2025-11-25`). This is what this repo's benchmarks use. | https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/client/session.py#L651-L666 |
| `LATEST_PROTOCOL_VERSION` | Its meaning changed. In v1 it was `"2025-11-25"`. In v2 it is `"2026-07-28"`, which "the initialize handshake cannot negotiate". Use `LATEST_HANDSHAKE_VERSION` for handshake code. | https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md#L552-L556 |
| HTTP without a version header | `DEFAULT_NEGOTIATED_VERSION = "2025-03-26"`. This does not apply to this repo, which is stdio-only. | https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp-types/mcp_types/_types.py#L26 |
| Spec status | 2026-07-28 is released. Its main changes: no `initialize` handshake, per-request `_meta` versioning, `server/discover`, `subscriptions/listen`, removal of `ping` and `logging/setLevel`, and multi-round-trip requests. | https://modelcontextprotocol.io/specification/2026-07-28/changelog |

---

## 2. Breaking changes from 1.x that touch this repo

The table covers only changes that hit code in this repo. The source column links the migration guide at tag `v2.3.0` (`docs/migration.md`). The same page is published at https://py.sdk.modelcontextprotocol.io/migration/.

| # | Area | This repo's v1 usage | v2 change | Impact here | Source |
|---|---|---|---|---|---|
| B1 | FastMCP rename | `server.py:22` `from mcp.server.fastmcp import Context, FastMCP`; `tests/test_services.py:492` | `FastMCP` is now `MCPServer`. The new import is `from mcp.server.mcpserver import MCPServer, Context`. Importing `mcp.server.fastmcp` raises `ModuleNotFoundError` with a pointer to the guide (2.1.1+, backported in 2.0.1). | **Blocking.** Import fails at startup. | https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md#L645-L675 ; shim: https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/fastmcp.py ; https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.1.1 |
| B2 | `ToolError` path | `server.py:23` `from mcp.server.fastmcp.exceptions import ToolError` | Now `from mcp.server.mcpserver.exceptions import ToolError`. `FastMCPError` is renamed `MCPServerError`. Semantics are the same: `is_error=True`, the message reaches the model with the prefix `Error executing tool <name>: …`, and the server logs it at INFO. | **Blocking** (import). Behaviour matches v1. | https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md#L672-L673 ; https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/mcpserver/exceptions.py#L43-L58 ; https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/mcpserver/tools/base.py#L132-L161 |
| B3 | `mcp.types` | `server.py:24` `from mcp.types import ToolAnnotations` | `mcp.types` is a **permanent alias** of the new `mcp_types` package, so the import is unchanged. | None for the import. | https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md#L202-L217 |
| B4 | camelCase to snake_case | Tests read `annotations.readOnlyHint` / `.destructiveHint` / `.openWorldHint` (`tests/test_services.py:440-448`, `tests/test_package_docs.py:77-78`) and `tool.inputSchema` (`tests/test_services.py:470`). Benchmarks read `result.structuredContent` / `result.isError` (all 5 adapters). | Field attributes are snake_case: `read_only_hint`, `input_schema`, `structured_content`, `is_error`, and so on. Constructors still accept camelCase kwargs, so `ToolAnnotations(readOnlyHint=True)` still works. **Attribute access by the camelCase name raises `AttributeError`.** The JSON wire format is unchanged. | **Tests fail; benchmark adapters fail at runtime.** I verified this empirically. | https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md#L287-L340 ; https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp-types/mcp_types/_types.py#L45-L48 ; https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp-types/mcp_types/_types.py#L1365-L1401 |
| B5 | Sync handlers on worker threads | 6 of 7 tools are `def` (`server.py:302,323,363,377,399,420`). They share one `sqlite3.Connection` opened in the lifespan (`storage/db.py:54`, `check_same_thread=False`). | v2 runs sync tool functions via `anyio.to_thread.run_sync()`. They "can run concurrently with each other". `async def` handlers stay on the event loop. | **Behaviour change.** Concurrent DB access becomes possible. See risk R1. | https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md#L909-L927 ; https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/mcpserver/utilities/func_metadata.py#L164 |
| B6 | Unversioned server | `FastMCP("python-docs-mcp-server", lifespan=...)` passes no version (`server.py:296-299`) | v1 reported the **mcp package version** as `serverInfo.version`. v2 reports `""`. The constructor gains a `version=` parameter. | Cosmetic, but clients show it. Pass `version=__version__`. | https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md#L736-L742 ; https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/mcpserver/server.py#L157-L183 |
| B7 | Positional constructor params | The name is the only positional argument here | The new positional order is `name, title, description, instructions, website_url, icons, version`. A positional `instructions` silently becomes `title`. | None today. Keep everything after `name` as keywords. | https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md#L711-L734 |
| B8 | `Context` generics | `ctx: Context = None` (bare) | The type parameters dropped from 3 to 2 (`Context[LifespanT, RequestT]`), both with defaults. Bare `Context` still works. `ctx: Context[AppContext]` gives a typed `lifespan_context`. Detection works by annotation (`typing.get_type_hints`), so `from __future__ import annotations` is fine. | None at runtime (verified). There is an optional typing improvement. | https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md#L1701-L1715 ; https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/mcpserver/utilities/context_injection.py ; https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/handlers/lifespan.md |
| B9 | Lifespan | `app_lifespan(server: FastMCP)` yields `AppContext`; tools read `ctx.request_context.lifespan_context` | "The `lifespan=` constructor argument and `ctx.request_context.lifespan_context` work as before". Only the annotation type changes. The streamable-HTTP-only change (lifespan entered once) does not apply to stdio. | Rename the annotation only. | https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md#L677-L687 ; https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md#L865-L869 |
| B10 | Structured output | Tools return Pydantic `BaseModel`s (`models.py`) and the SDK derives `outputSchema` / `structuredContent` | Unchanged: "dict, list, scalar, and model returns are wrapped into `content` and `structured_content` by the same rules". New: results are **validated against the negotiated protocol schema** before they are sent. All legacy versions use the 2025-11-25 result surface, which includes `structuredContent`. `outputSchema` `$ref`s must resolve within the schema (2.2.0). | None (verified: `structuredContent` with `null` fields is preserved on a 2024-11-05 session). | https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md#L682 ; https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md#L2648-L2652 ; https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp-types/mcp_types/methods.py#L247 ; https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.2.0 |
| B11 | Tool decorator | `@mcp.tool(annotations=...)` | Same signature: `name, title, description, annotations, icons, meta, structured_output`. | None. Note that `meta=` already existed in **v1.30.0**, so the "SRVR-07 deferred" comment at `server.py:442-446` is stale on both lines. | https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/mcpserver/server.py#L669-L736 ; https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/src/mcp/server/fastmcp/server.py#L462-L471 |
| B12 | Stdio entrypoint | `__main__.py:110` `mcp_server.run(transport="stdio")` after a manual fd-1 / `sys.stdout` restore | `run(transport="stdio")` is unchanged (`anyio.run(self.run_stdio_async)`). **New:** while serving, `stdio_server` moves the wire to a private dup of fd 1 and points fd 0 at `/dev/null` and fd 1 at stderr, restoring both on exit. It claims fd 1 only if `sys.stdout` is backed by fd 1. Otherwise it serves `sys.stdout.buffer` in place, "exactly as v1 did". | Compatible. The `sys.stdout = sys.__stdout__` restore stays **required**, because without it frames would go to stderr. ADR-008's invariant text needs an update. | https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md#L2273-L2301 ; https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/stdio.py#L65-L162 ; https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/mcpserver/server.py#L404-L425 |
| B13 | Private internals in tests | `server._tool_manager._tools[...]` (`tests/test_services.py:415,435,454`; `tests/test_package_docs.py:76`; `tests/test_retrieval_regression.py:212`), plus `tool.fn(...)` | These still exist in 2.3.0: `self._tool_manager`, `ToolManager._tools`, `Tool.fn`. But the SemVer promise "does not cover underscore-prefixed names". `mcp._mcp_server` is now `mcp._lowlevel_server`. This repo does not use it. | Works today (verified). It is a fragility risk in any 2.x minor release. | https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/VERSIONING.md ; https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/mcpserver/server.py#L200 ; https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/mcpserver/tools/tool_manager.py#L23 ; https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md#L880 |
| B14 | `list_tools()` | `await server.list_tools()` then `.inputSchema` (`tests/test_services.py:466-470`) | Still public and still returns `list[Tool]`, but the attribute is `input_schema`. | Test fails until the attribute is renamed (verified). | https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/mcpserver/server.py#L516-L531 |
| B15 | `streamable_http_client` return | `deepwiki_adapter.py:203`, `gitmcp_adapter.py:270` and `ref_tools_adapter.py:278` unpack `(read, write, _get_session_id)` | The function now yields a **2-tuple**, and the `get_session_id` callback is removed. | **Runtime `ValueError`** in 3 adapters. `context7_adapter.py:333-334` indexes `streams[0], streams[1]`, so it is fine. | https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md#L2113-L2117 ; https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/client/streamable_http.py#L713-L788 |
| B16 | `httpx` replaced by `httpx2` | `context7_adapter.py:240-250` and `ref_tools_adapter.py:207-217` do `import httpx` and pass `httpx.AsyncClient(...)` as `http_client=` | The SDK no longer installs `httpx` (verified: it is absent in the scratch venv). `http_client` must be `httpx2.AsyncClient`. An `httpx.AsyncClient` "degrades in subtle ways". Redirects within the same origin are now followed by the transport itself (2.2.0), so `follow_redirects=True` is unnecessary. The guide suggests `httpx2.Timeout(30, read=300)` to keep v1's timeouts. TLS now uses the OS trust store (`truststore`) instead of certifi. | **`ModuleNotFoundError`** in 2 adapters on the HTTP path. httpx is not a direct dependency of this repo (it is only transitive via mcp in `uv.lock`). | https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md#L102-L188 ; https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md#L2103-L2107 ; https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.2.0 |
| B17 | Client error semantics | Adapters treat `result.isError` as a tool failure. Any raised exception becomes `mcp_protocol_crash`. | A remote tool that raises `MCPError` now surfaces as a raised `MCPError` on the client, not as `isError=True`. HTTP non-2xx responses now raise per-request `MCPError` instead of escaping the context as `httpx.HTTPStatusError`. Timeouts use code `-32001` instead of `408`. | **Benchmark classification drift (inferred):** some failures move from `tool_failure` to `mcp_protocol_crash`. | https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md#L984-L1017 ; https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md#L2176-L2229 ; https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md#L1931-L1965 |
| B18 | `ClientSession` / `stdio_client` / `StdioServerParameters` | Benchmarks use `ClientSession(read, write)`, `await session.initialize()`, `session.call_tool(name, args)`, `stdio_client(StdioServerParameters(command=..., args=..., env=...))` | The public surface is unchanged. Only the timeouts changed (`float` instead of `timedelta`), and this repo uses none. `env` is still merged over `get_default_environment()`, as in v1.30. Callbacks now run concurrently. | None. | https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md#L1967-L1985 ; https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/client/stdio.py#L94-L129 ; https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/src/mcp/client/stdio/__init__.py |
| B19 | `stdio_client` shutdown | Spawns `python-docs-mcp-server` and competitor npm servers | On POSIX, children of a server that exits gracefully are left alive. A failed write surfaces as `CONNECTION_CLOSED`. | Low. Watch for orphaned npm children in Context7 stdio runs (inferred). | https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md#L2239-L2271 |
| B20 | Handler exception text | All tools catch `Exception` and raise `ToolError(f"Internal error: {type(e).__name__}")` | Since 2.1.0, an *unexpected* exception reaches the client only as `Error executing tool <name>` and is logged at ERROR with a traceback. | None. The explicit wrapping already does the right thing and keeps the type name. | https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.1.0 |
| B21 | OpenTelemetry on by default | none | Tracing middleware is installed on every server. It is a no-op without an OTel SDK. `opentelemetry-api` is a new hard dependency. | Low. Possible cold-start cost (not measured). | https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md#L2662-L2666 |
| B22 | `MCPDeprecationWarning` | none (no logging, sampling or roots usage) | It subclasses `UserWarning`, so it is visible by default and would become an error under `filterwarnings=error`. | None today. | https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md#L2718-L2732 |

Removed or deprecated features this repo does **not** use, so none need work: `get_context()`, `ProgressContext`, `Context.client_id`, `Context.log(message=)`, experimental Tasks, the WebSocket transport, `mount_path`, transport kwargs on the constructor, `MCP_*` env vars, `create_connected_server_and_client_session`, `streamablehttp_client`, `McpError` (renamed to `MCPError`), and the low-level `Server` decorators. Each is listed in https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md.

---

## 3. Official migration guide

- **Published:** https://py.sdk.modelcontextprotocol.io/migration/
- **Source at the tag:** https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md
- **Companion page:** "What's new in v2", https://py.sdk.modelcontextprotocol.io/whats-new/
- **v1 docs (kept):** https://py.sdk.modelcontextprotocol.io/v1/

The guide describes its own scope as: "This guide covers the breaking changes introduced in v2 of the MCP Python SDK and how to update your code." Any API it does not mention "behaves as it did in v1".

Its "Suggested migration order" ([L52-L60](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md#L52-L60)), paraphrased:

1. Update dependency pins and CLI usage.
2. Apply the mechanical renames and import moves.
3. Port the server surface (`MCPServer`).
4. Port client code.
5. Update transport setup and auth.
6. Run the tests, and check new errors against "Stricter protocol validation" and "Testing utilities".
7. Address deprecation warnings.

For projects not ready to migrate, the guide's advice is to "keep a `<2` upper bound". This repo already does that.

---

## 4. Interop with 1.x / 2025-era clients (Claude Desktop, Codex, Cursor)

**Short answer:** yes. A v2 server keeps serving handshake-era clients with no configuration. It also adds support for 2026-07-28 clients.

- **What the SDK says:** the 2.0.0 release notes say v2 "still serves every 2025-era client from the same `MCPServer`, over Streamable HTTP and stdio, with nothing to configure". The legacy-clients page says there is "no way to reject or disable an era". https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.0.0 ; https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/run/legacy-clients.md
- **The negotiation rule (source):** a legacy client sends `initialize` with `protocolVersion`. The server accepts it if it is one of `2024-11-05`, `2025-03-26`, `2025-06-18` or `2025-11-25`. Otherwise it counters with `2025-11-25`. https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/runner.py#L421-L426
- **What the spec says:** the compatibility matrix lists "Legacy client + Dual-era server: Works. The server answers `initialize` and serves the client according to the negotiated legacy revision." It also lists "Modern client + Legacy server: Fails". https://modelcontextprotocol.io/specification/2026-07-28/basic/lifecycle (section "Backward Compatibility with Initialization-Based Versions")
- **Today's v1 server:** v1.30.0 negotiates only handshake revisions. Its `SUPPORTED_PROTOCOL_VERSIONS` ends at `LATEST_PROTOCOL_VERSION`, which was `2025-11-25` in v1 ([v1.30.0 `shared/version.py`](https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/src/mcp/shared/version.py); [migration.md#L556](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md#L556)). So a modern-only client cannot reach the current server **(inferred: I did not check whether v1.30 answers `server/discover`)**. Dual-era clients fall back to `initialize` and work. Migrating to v2 therefore adds forward compatibility.
- **Wire-level checks I ran (scratch probe, raw JSON-RPC over stdio against an `MCPServer` 2.3.0 process):**
  - `initialize` with `2024-11-05` negotiated `2024-11-05`.
  - `tools/list` returned `inputSchema`, `outputSchema` and `annotations` in camelCase.
  - `tools/call` returned `structuredContent` with `null` fields intact.
  - `server/discover` with `_meta` returned a `DiscoverResult` with `supportedVersions: ["2026-07-28"]`.
- **Effect on this repo's own tests:** this repo's real stdio smoke tests (`tests/test_stdio_smoke.py`, `tests/test_mcp_get_docs_cache_smoke.py`, `tests/test_whatsnew.py`) negotiate `2024-11-05`. All of them passed against 2.3.0.
- **Not verified:** which protocol version each named host currently sends, and whether any host already probes `server/discover`. I did not read the hosts' own sources. For legacy hosts, the matrix row above applies whatever version they send.
- **Side effects for 2026-07-28 clients (inferred, low impact):**
  - `server/discover` advertises `tools.listChanged: true`, `prompts`/`resources` `listChanged`, and `resources.subscribe: true`, even though this server never changes its lists.
  - `MCPServer(subscriptions=False)` (new in 2.3.0) stops serving `subscriptions/listen` and advertises those as false.
  - Optional `cache_hints=` lets 2026 clients cache `tools/list`. Without it the observed hint was `ttlMs: 0`, `cacheScope: "private"`.
  - Sources: https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.3.0 ; https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/mcpserver/server.py#L157-L183

---

## 5. Migration checklist mapped to this repo

Risk key: **H** = breaks at import time, or breaks silently at runtime. **M** = behaviour change that needs a decision or a test. **L** = mechanical or cosmetic.

### 5.1 Packaging

- [ ] **H** `pyproject.toml:26`: change `"mcp>=1.30.0,<2.0.0"` to `"mcp>=2.3.0,<3"`.
  - Why the floor of 2.3.0: it stops sending empty `_meta` and `params` on legacy requests and omits an empty `experimental` from `initialize`. Both matter when benchmarking competitor servers. It also fixes `Context[T]` typing on prompts. https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.3.0
  - Why the `<3` cap: VERSIONING.md promises that breaking changes land only in a major. https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/VERSIONING.md
- [ ] **L** `pyproject.toml:28`: raise `pydantic>=2.0.0,<3.0` to `pydantic>=2.12,<3.0` so the floor matches mcp's real requirement (`pydantic>=2.12.0`). This is optional but more honest.
- [ ] **L** Regenerate `uv.lock` (`uv lock --upgrade-package mcp`). Expect `httpx`, `httpx-sse` and `pydantic-settings` to drop out, and `httpx2`, `mcp-types` and `opentelemetry-api` to come in. Do not pin `mcp-types` separately; it is exact-pinned by `mcp`. https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md#L100
- [ ] **L** `CHANGELOG.md:53` says the project is "retaining MCP v1". Add an Unreleased entry for the v2 move.

### 5.2 Server: `src/mcp_server_python_docs/`

- [ ] **H** `server.py:22`: `from mcp.server.mcpserver import Context, MCPServer`
- [ ] **H** `server.py:23`: `from mcp.server.mcpserver.exceptions import ToolError`
- [ ] **L** `server.py:24`: keep `from mcp.types import ToolAnnotations` (a permanent alias).
- [ ] **L** `server.py:223-234`: optionally switch to snake_case kwargs (`read_only_hint=True`, …). camelCase kwargs still construct.
- [ ] **L** `server.py:121`: `app_lifespan(server: FastMCP)` becomes `app_lifespan(server: MCPServer)`.
- [ ] **L** `server.py:294`: the return annotation becomes `-> MCPServer`.
- [ ] **L** `server.py:296-299`: use `MCPServer("python-docs-mcp-server", version=__version__, lifespan=app_lifespan)`. Without `version=`, `serverInfo.version` becomes `""` (B6). `__version__` is already exported (see `__main__.py:83`). The name is unchanged, so `tests/test_services.py:420-429` keeps passing (`MCPServer.name` exists at [server.py#L275](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/mcpserver/server.py#L275)).
- [ ] **L** `server.py:307,329,348,364,378,403,425`: `ctx: Context = None` works unchanged (verified).
  - Optional: use `ctx: Context[AppContext]` and drop the per-tool `app_ctx: AppContext = …` re-annotation. https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/handlers/lifespan.md
- [ ] **M** `server.py:301-440`: sync tools now run on anyio worker threads (B5). The scratch probe confirmed a sync tool runs on a different thread from the lifespan. Pick one option:
  - **(a)** Accept it. The connection already has `check_same_thread=False` (`storage/db.py:54`). `sqlite3.threadsafety == 3` on the local CPython build (SQLite 3.50.4), which means connections may be shared across threads (https://docs.python.org/3/library/sqlite3.html#sqlite3.threadsafety). The `lru_cache` wrappers in `services/cache.py` are thread-safe for their own bookkeeping, and `PersistentDocsCache` already holds a lock.
  - **(b)** Make the tools `async def` to keep v1's single-thread semantics, at the cost of blocking the loop during queries.
  - **(c)** Add a `threading.Lock` around DB access in the services.
  - Whichever you pick, add a concurrency test. See R1 for the `pysqlite3-binary` extra.
- [ ] **L** `server.py:345-360` `lookup_package_docs`: the existing `async def` + `asyncio.to_thread` still works. Optionally make it plain `def`, since v2 offloads sync tools to threads.
- [ ] **L** `server.py:442-446`: the "SRVR-07" comment saying FastMCP lacks `_meta` on tool definitions is stale. `@mcp.tool(meta=...)` exists in v1.30.0 and v2.3.0 (B11). Either implement the hint or reword the comment.
- [ ] **L** `__main__.py:94-112` (`serve()`): no code change. Keep the fd-1 and `sys.stdout` restore. v2's `stdio_server` only claims fd 1 when `sys.stdout` is backed by fd 1 (B12). Keep `except BrokenPipeError`. Its v2 behaviour on mid-write disconnect is unverified (Q2).
- [ ] **L** Docstrings that name FastMCP: `app_context.py:1-5`, `models.py:3-4`, `server.py:1`.

### 5.3 Tests: `tests/`

- [ ] **H** `tests/test_services.py:440-448`: `annotations.readOnlyHint` / `.destructiveHint` / `.openWorldHint` become `.read_only_hint` / `.destructive_hint` / `.open_world_hint`. (These fail on v2; verified.)
- [ ] **H** `tests/test_package_docs.py:77-78`: same rename. (Fails on v2; verified.)
- [ ] **H** `tests/test_services.py:470`: `tool.inputSchema` becomes `tool.input_schema`. (Fails on v2; verified.)
- [ ] **H** `tests/test_services.py:492,502`: `from mcp.server.fastmcp import FastMCP` / `FastMCP("test")` become `MCPServer`.
- [ ] **M** `tests/test_services.py:415,435,454`, `tests/test_package_docs.py:76`, `tests/test_retrieval_regression.py:212-218`: these use the private `_tool_manager._tools` and `tool.fn(ctx=SimpleNamespace(...))`. They work on 2.3.0 (verified) but sit outside SemVer.
  - Prefer `await server.list_tools()` for names and annotations.
  - For direct invocation, prefer `async with Client(server, mode="legacy") as c: await c.call_tool(...)` with a lifespan, or extract the tool bodies into plain functions that take `AppContext` (inferred design suggestion). https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md#L2670-L2694
- [ ] **H (silent)** `tests/benchmarks/test_python_docs_mcp_adapter.py:38-39,64-88` and `tests/benchmarks/test_competitor_adapters.py:85-116,193-196,…`: the fake results use `structuredContent=` / `isError=`. Update them to `structured_content` / `is_error` alongside the adapters. Otherwise the tests keep passing while real runs crash.
- [ ] **L** `tests/test_stdio_smoke.py:288,315,340,362`, `tests/test_mcp_get_docs_cache_smoke.py:94`, `tests/test_whatsnew.py:322`: `"protocolVersion": "2024-11-05"` is still supported (verified). Consider adding:
  - one `2025-11-25` case (`LATEST_HANDSHAKE_VERSION`), and
  - one modern `server/discover` frame with `_meta` (`io.modelcontextprotocol/protocolVersion`, `io.modelcontextprotocol/clientCapabilities`), so the 2026-era path is covered.
- [ ] **L** `tests/test_stdio_smoke.py:309`: `assert "nextCursor" not in ...` still holds, because v2 serializes with `exclude_none` / `exclude_unset` (verified: the test passes).

### 5.4 Benchmarks: `benchmarks/adapters/`

- [ ] **H** Every adapter's `_CallToolResult` Protocol and the code that reads it: `structuredContent` becomes `structured_content`, and `isError` becomes `is_error`. Locations:
  - `context7_adapter.py:91-92,118-119,178-202`
  - `deepwiki_adapter.py:79-80,102-103,128-131`
  - `gitmcp_adapter.py:83-84,106-107,155-198`
  - `ref_tools_adapter.py:90-91,113-114,162-186`
  - `python_docs_mcp_adapter.py:87-88,122-128,157-184`

  Verified: `CallToolResult(...).isError` raises `AttributeError` on 2.3.0. The broad `except Exception` turns this into `mcp_protocol_crash` on every cell.
- [ ] **H** `deepwiki_adapter.py:203`, `gitmcp_adapter.py:270`, `ref_tools_adapter.py:278`: unpack `(read, write)` instead of `(read, write, _get_session_id)` (B15).
- [ ] **H** `context7_adapter.py:240-250` and `ref_tools_adapter.py:207-217`: use `import httpx2` and `httpx2.AsyncClient(headers=..., timeout=httpx2.Timeout(30, read=300))`. Drop `follow_redirects` (B16). Also update the `create_mcp_http_client` comments.
- [ ] **M** Review how `tool_failure` vs `mcp_protocol_crash` is classified now that remote `MCPError` and HTTP non-2xx responses raise `MCPError` (B17). A targeted `except MCPError` in each `run()` would keep categories stable (inferred).
- [ ] **L** `ClientSession(read, write)` + `initialize()` (`context7_adapter.py:333-336`, `deepwiki_adapter.py:204`, `gitmcp_adapter.py:271`, `ref_tools_adapter.py:279`, `python_docs_mcp_adapter.py:256-258`) still works. It negotiates `2025-11-25`, which mirrors today's hosts.
  - Alternative: `Client(StdioServerParameters(...), mode="legacy")` (2.1.0+). Do not switch to `mode="auto"` for benchmark parity unless you want to measure the 2026 path. https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.1.0

### 5.5 Docs

- [ ] **L** `docs/architecture/ADR-008-transport.md`:
  - Lines 12, 49, 65, 90 and 123: FastMCP becomes MCPServer.
  - Lines 128-134, the invariant "Fd 1 carries only JSON-RPC protocol frames once `serve()` restores it": under v2, while `run()` is serving, the SDK writes frames to a private duplicate of the restored fd 1 and points fd 1 itself at stderr (B12). Record that the SDK now also guards the wire during serving, and that the import-time fd swap (HYGN-01) is still needed for the pre-serve window.
- [ ] **L** `docs/architecture/DESIGN.md:415,471`: FastMCP becomes MCPServer.
- [ ] **L** Run `.github/INTEGRATION-TEST.md` manual QA against Claude Desktop, Codex and Cursor after the bump.

### 5.6 Risk notes

- **R1 (M): concurrency.** Worker-thread execution (B5) is the only change that alters runtime semantics for the server itself.
  - The optional `pysqlite3-binary` extra may report a different `threadsafety`. I did not check this.
  - Default anyio thread-pool concurrency also applies (inferred: anyio's default limiter).
- **R2 (H, silent): benchmarks.** Benchmark adapters break only on live runs, while the unit tests (camelCase fakes) stay green. Fix the adapters and the fakes in one change.
- **R3 (L): private API.** Private-API test hooks (`_tool_manager`) work today but are unprotected by SemVer.
- **R4 (L): startup failure path.** Behaviour is at parity. With a missing index and auto-index disabled, the server exits with code 1 and a traceback on stderr under both 1.30.0 and 2.3.0. Verified with the same command on each.

---

## 6. Open questions / not verified

1. **Q1. Host protocol versions.** I did not verify which protocol version Claude Desktop, Codex and Cursor currently send, or whether any of them probe `server/discover`. Per the spec matrix and the SDK's legacy routing, any handshake version works. A real-host QA pass is still needed.
2. **Q2. Mid-write disconnect.** I did not test client disconnect mid-write under v2 stdio (the `BrokenPipeError` path in `__main__.py:111`). v2 diverts fd 1 and writes through a private fd from an anyio worker. The exception type that escapes `run()` may differ, for example an `ExceptionGroup` (inferred). The existing smoke tests (stdin EOF) pass.
3. **Q3. Concurrency.** I did not stress-test concurrent sync tool calls on the shared `sqlite3.Connection` (R1). I did not check `pysqlite3.dbapi2.threadsafety`.
4. **Q4. Pyright.** I did not run Pyright against 2.3.0 types (`Context` TypeVar defaults, `# type: ignore[assignment]` on `ctx: Context = None`, `MCPServer[AppContext]` generics).
5. **Q5. Version skew in the scratch run.** The scratch venv resolved `pydantic` 2.14.1 and newer transitive versions, not the repo's `uv.lock` pins (`pydantic` 2.13.5, `anyio` 4.14.2). The 645-pass result is indicative, not a locked-env CI result.
6. **Q6. Cold start.** I did not measure the cold-start and import-time cost of `opentelemetry-api` plus the tracing middleware (B21). This is relevant for hosts that spawn the server per session.
7. **Q7. Wording conflict on v1 support.** The 2.0.0 release notes say v1.x gets "only … security fixes". VERSIONING.md and the migration guide say "critical bug fixes and security fixes". Treat v1 as maintenance-only either way.
8. **Q8. v1.30 and `server/discover`.** I did not check whether v1.30.0 answers `server/discover`. The forward-compat claim in Section 4 is inferred from v1's handshake-only version list.

---

## 7. How the empirical checks were run

All checks used the session scratchpad, outside the repo. No repo file was modified except this one.

- **Environment:** `uv venv --python 3.12`, then `uv pip install mcp==2.3.0`. This resolved `mcp-types` 2.3.0, `pydantic` 2.14.1 and `httpx2` 2.16.0. `httpx` was **not** installed.
- **API probe** (`probe_v2.py`). It confirmed:
  - the `mcp.server.fastmcp` import error text;
  - the `HANDSHAKE_PROTOCOL_VERSIONS` tuple;
  - `ToolAnnotations(readOnlyHint=True).read_only_hint` works and `.readOnlyHint` raises `AttributeError`;
  - `_tool_manager._tools` and `Tool.fn` still exist, and `list_tools()[i].input_schema` works;
  - a sync tool runs on a different thread than the lifespan;
  - `ToolError` returns `is_error=True` with `"Error executing tool failing: boom"` under both `Client(mode="legacy")` (2025-11-25) and `mode="auto"` (2026-07-28);
  - `structured_content` keeps `null` fields.
- **Stdio probe** (`stdio_server_probe.py` and `stdio_client_probe.py`): sent raw newline-delimited JSON-RPC to an `MCPServer` stdio subprocess, using the results summarized in Section 4. A flushed `print()` inside a tool landed on stderr, not on the wire.
- **Repo test suite:** I copied `src/`, `tests/`, `benchmarks/`, `docs/`, `ops/` and `.github/` into scratch and made these edits:
  - `server.py:22-23` imports changed to `mcp.server.mcpserver` (aliasing `MCPServer as FastMCP`);
  - `tests/test_services.py:492` import changed the same way;
  - the package was installed with `--no-deps -e`.

  Results:
  - **641 passed, 4 failed.** Three failures were genuine v2 breaks (B4/B14). One was a copy artifact: a missing `.github/workflows`.
  - After the snake_case renames in `tests/test_services.py` and `tests/test_package_docs.py`, and copying `.github/workflows`: **645 passed, 0 failed**.
- **Missing-index parity check:** ran `python -m mcp_server_python_docs serve` with an empty `HOME`/`XDG_CACHE_HOME` and `PYTHON_DOCS_MCP_DISABLE_AUTO_INDEX=1`. Both the repo `.venv` (mcp 1.30.0) and the scratch 2.3.0 env exited with code 1, nothing on stdout, and a `SystemExit` traceback on stderr.

## Sources

- PyPI: https://pypi.org/pypi/mcp/json ; https://pypi.org/pypi/mcp/2.3.0/json ; https://pypi.org/pypi/mcp/1.30.0/json ; https://pypi.org/pypi/mcp-types/json ; https://pypi.org/pypi/httpx2/json
- Releases: https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.0.0 ; …/v2.0.1 ; …/v2.1.0 ; …/v2.1.1 ; …/v2.2.0 ; …/v2.3.0 ; …/v1.30.0
- Migration guide: https://py.sdk.modelcontextprotocol.io/migration/ ; https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md
- SDK docs at the tag:
  - `docs/protocol-versions.md`
  - `docs/run/legacy-clients.md`
  - `docs/handlers/lifespan.md`
  - `docs/servers/handling-errors.md`
  - `docs/servers/structured-output.md`
  - `docs/get-started/testing.md`
  - `VERSIONING.md`

  All are under https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/
- SDK source at `v2.3.0`:
  - `src/mcp-types/mcp_types/version.py`
  - `src/mcp-types/mcp_types/_types.py`
  - `src/mcp-types/mcp_types/methods.py`
  - `src/mcp/server/fastmcp.py`
  - `src/mcp/server/mcpserver/{__init__,server,context,exceptions}.py`
  - `src/mcp/server/mcpserver/tools/{base,tool_manager}.py`
  - `src/mcp/server/mcpserver/utilities/{context_injection,func_metadata}.py`
  - `src/mcp/server/stdio.py`
  - `src/mcp/server/runner.py`
  - `src/mcp/client/{session,stdio,streamable_http}.py`
  - `src/mcp/__init__.py`
- SDK source at `v1.30.0`: `src/mcp/server/fastmcp/server.py`, `src/mcp/server/fastmcp/tools/base.py`, `src/mcp/shared/version.py`, `src/mcp/client/stdio/__init__.py`
- MCP spec 2026-07-28: https://modelcontextprotocol.io/specification/2026-07-28/changelog ; https://modelcontextprotocol.io/specification/2026-07-28/basic/lifecycle
- Python stdlib: https://docs.python.org/3/library/sqlite3.html#sqlite3.threadsafety
