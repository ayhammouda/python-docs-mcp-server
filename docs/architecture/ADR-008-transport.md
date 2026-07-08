# ADR-008: Transport

- **Status:** Accepted
- **Date:** 2026-05-29
- **Deciders:** @ayhammouda
- **Roadmap refs:** principle 2.7; roadmap §4 (v0.5.0 and v1.0.0 tables), §6 q3

## Context and Problem Statement

`python-docs-mcp-server` is a local MCP server that hosts such as Claude Code,
Claude Desktop, Cursor, and Codex spawn as a subprocess, speaking the Model
Context Protocol over a wire transport. FastMCP, the server framework this
project builds on, supports more than one wire transport. The eighth layer of
the eight-layer architecture (principle 2.7 — source connector, ingestion,
storage, retrieval, budget, serializer, cache, transport) is that transport
boundary: it must hand JSON-RPC frames to the client without letting anything
else from the process land on the same wire.

In stdio mode the wire is literally the process's stdout file descriptor (fd
1). That creates a hard hygiene requirement: any `print()`, third-party import
side effect, or log line that reaches fd 1 before the server starts serving
corrupts every JSON-RPC frame that follows. This was the original "B3
blocker" that motivated the fd-swap sequence in
[`src/mcp_server_python_docs/__main__.py`](../../src/mcp_server_python_docs/__main__.py).
This ADR records the transport that shipped for v0.5.0 and the hygiene
contract that makes stdio safe to use.

## Decision Drivers

- Every MCP host this project documents (Claude Code, Claude Desktop, Cursor,
  Codex — see [`README.md`](../../README.md) "Configure your MCP client")
  spawns the server locally and speaks stdio; none configure a remote HTTP or
  SSE endpoint.
- fd 1 is the literal MCP wire in stdio mode, so import-time or logging output
  must never reach it (HYGN-01, the "B3 blocker").
- Principle 2.7 layered design: transport needs explicit inputs, outputs, and
  invariants that the other seven layers do not need to know about.
- The roadmap keeps an HTTP/SSE transport as an open, unresolved question
  gated at v1.0.0, not a v0.5.0 decision: §4's v1.0.0 table lists "Optional
  Streamable HTTP transport (§6 q3) | Ship behind a flag if there is a clear
  remote-server use case by v0.5.0," and §6 q3 frames the choice as "stay
  stdio-only through v1.0 (recommended) vs HTTP adapter behind a flag."
- Shipping only the transport that current hosts actually use avoids an
  untested transport code path and an unneeded ASGI/HTTP server dependency in
  a tool that runs locally.

## Considered Options

1. Stdio-only via FastMCP, with an explicit fd-hygiene sequence around it.
   (chosen)
   - Matches how every documented MCP host spawns this server today.
   - Requires disciplined ordering at import time and at server start because
     stdout doubles as the wire.
2. Streamable HTTP/SSE transport, always-on or behind a flag.
   - Deferred, not rejected: roadmap §6 q3 keeps "HTTP adapter behind a flag"
     open as a v1.0.0 gate contingent on a clear remote-server use case. No
     such use case exists as of v0.5.0. This ADR does not resolve §6 q3.
3. Support both stdio and HTTP transports simultaneously now.
   - Rejected because it would pre-empt the still-open §6 q3 decision and add
     an untested transport plus an HTTP/ASGI dependency before any documented
     host needs one.

## Decision Outcome

The shipped transport is stdio-only via FastMCP:
`mcp_server.run(transport="stdio")` in the `serve` command of
[`src/mcp_server_python_docs/__main__.py`](../../src/mcp_server_python_docs/__main__.py).
Nothing else in `src/` implements an HTTP, SSE, streamable, or websocket
transport.

Because fd 1 is the literal wire in stdio mode, `__main__.py`'s module
docstring calls out that the hygiene sequence order is load-bearing, and it
runs in this order before anything else can write to stdout:

1. **HYGN-01** — at import time, before any other import, the module saves
   the real stdout fd (`_saved_stdout_fd: int | None = os.dup(1)`), then
   redirects fd 1 to stderr (`os.dup2(2, 1)`) and repoints the Python-level
   `sys.stdout` to `sys.stderr`. After this, any `print()` or accidental
   write to fd 1 during import lands on stderr, not the MCP pipe.
2. **HYGN-03** — SIGPIPE is set to `SIG_IGN` when the platform exposes it,
   guarded by a `getattr` check because Windows has no SIGPIPE attribute.
3. **HYGN-02** — `logging.basicConfig(stream=sys.stderr, ...)` forces all
   logging output to stderr, so log lines can never land on the MCP pipe
   either.

The `serve` command reverses the redirection immediately before serving: it
hands off the saved fd via `_consume_saved_stdout_fd()`, restores fd 1 with
`os.dup2(saved_stdout_fd, 1)` and closes the temporary duplicate, and restores
the Python-level stdout object with `sys.stdout = sys.__stdout__`. Both the fd
and the Python object must be restored — restoring only one leaves FastMCP
emitting JSON-RPC frames on stderr instead of the wire. Only after this
restoration does `mcp_server.run(transport="stdio")` run. A client
disconnecting while `run()` is in progress raises `BrokenPipeError`; `serve`
catches and swallows it (HYGN-03) so a normal client disconnect exits cleanly
instead of printing a stack trace.

HTTP/SSE transport is deliberately not shipped in v0.5.0. The roadmap holds
it open as a v1.0.0 gate, quoted above from §4 and §6 q3. This ADR records
stdio-only as the accepted v0.5.0 decision; it does not resolve the §6 q3
question of whether an HTTP adapter behind a flag ships at v1.0.0.

### Consequences

**Positive:** The transport boundary is narrow and matches what every
documented MCP host actually uses, so there is no unused or untested
transport code path. The fd-swap sequence is covered end to end by a
subprocess-based smoke test
([`tests/test_stdio_smoke.py`](../../tests/test_stdio_smoke.py), TEST-05)
that spawns the real server and asserts JSON-RPC frames appear only on
stdout. Deferring HTTP/SSE keeps the open §6 q3 question intact for a v1.0.0
decision instead of being foreclosed by an early implementation.

**Negative / risks:** The hygiene sequence is order-dependent and fragile to
refactors — moving an import above the fd-swap, or restoring only the fd or
only `sys.stdout` at `serve` time, silently reopens the "B3 blocker" and
corrupts the protocol stream without an obvious error at the call site.
Stdio-only means any future remote-server or multi-client use case is
unaddressed until the v1.0.0 gate is revisited; nothing in this ADR should be
read as ruling that out.

## Layer Contract (principle 2.7)

- **Inputs:** A ready `FastMCP` server instance from `create_server()`, plus
  the process's stdin/stdout file descriptors as the physical stdio channel.
- **Outputs:** JSON-RPC 2.0 frames written to fd 1 (stdout) for the MCP
  client, and a clean process exit (including on client disconnect via
  `BrokenPipeError`).
- **Invariants:** Fd 1 carries only JSON-RPC protocol frames once `serve()`
  restores it; all logging and any print output are on stderr for the whole
  process lifetime; SIGPIPE is ignored so client disconnect cannot crash the
  process with a signal; the fd-swap/restore ordering in `__main__.py` is
  preserved exactly (save and redirect before other imports, restore
  immediately before `run()`); no HTTP, SSE, or other network transport is
  active alongside stdio.

## Links

- STRATEGIC-ROADMAP-2026-05-29.md §4 (v0.5.0 and v1.0.0 tables), §6 q3
- [`src/mcp_server_python_docs/__main__.py`](../../src/mcp_server_python_docs/__main__.py)
- [`tests/test_stdio_smoke.py`](../../tests/test_stdio_smoke.py) (TEST-05)
- [`README.md`](../../README.md) "Configure your MCP client" and "Quality
  checks"
