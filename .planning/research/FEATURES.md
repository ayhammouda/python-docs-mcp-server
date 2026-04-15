# Feature Research — mcp-server-python-docs

**Domain:** Python stdlib documentation retrieval over MCP (stdio, read-only, version-aware)
**Researched:** 2026-04-15
**Confidence:** HIGH (MCP spec + 3+ active comparable servers verified; one LOW-confidence item flagged inline)

---

## Executive answer to the guide's question

> "Is the locked 3-tool surface (`search_docs`, `get_docs`, `list_versions`) still the market norm in April 2026?"

**Yes, it is the market norm.** The 3-tool design is in the middle of the observed range (2–3 tools) and is **consistent with every currently-active docs retrieval MCP server examined**:

| Server | Active tool count | Tool names |
|---|---|---|
| **Context7** (upstash/context7) | **2** | `resolve-library-id`, `query-docs` |
| **DeepWiki** (Cognition) | **3** | `read_wiki_structure`, `read_wiki_contents`, `ask_question` |
| **Microsoft Learn MCP** (MicrosoftDocs/mcp) | **3** | `microsoft_docs_search`, `microsoft_docs_fetch`, `microsoft_code_sample_search` |
| **Ref.tools** (ref-tools/ref-tools-mcp) | **2** | `ref_search_documentation`, `ref_read_url` |
| **Anthropic Fetch reference server** | **1** | `fetch` |
| **arabold/docs-mcp-server** | **~3** | `search`, `fetch-url`, `scrape` (not served at runtime for this project) |
| **mcp-server-python-docs** (this project) | **3** | `search_docs`, `get_docs`, `list_versions` |

The two most relevant design precedents are **Microsoft Learn MCP** and **Ref.tools** — both are read-only documentation retrieval servers targeting LLM clients, and both converge on **search + fetch** as the core pair. Our guide's surface matches this pattern exactly, plus `list_versions` as a trivial discovery helper (which is the only thing the comparables don't have — because they don't need version awareness). **The guide's tool surface is validated.**

No mature server has done anything that suggests "3 is wrong." The only interesting variant is that Context7 and Ref.tools both accept a `query` string on their "read/fetch" tool, not just a URL/ID — so the LLM can say "from this page, tell me about X" and get a filtered slice. Our `get_docs(anchor=...)` achieves the same outcome structurally (by section), which is more deterministic and a legitimate differentiator.

---

## Feature Landscape

### Table Stakes (Users Expect These)

Features users assume exist in a 2026 MCP retrieval server. Missing these = product feels incomplete or misbehaves in LLM clients.

| Feature | Why Expected | Complexity | Notes / In guide? |
|---------|--------------|------------|-------|
| **3-tool surface (search/fetch/list_versions)** | Matches market norm (Context7, DeepWiki, MS Learn, Ref.tools all in 2–3 tool range). LLMs pick from small surfaces more reliably. | LOW | **In guide §3** — validated. |
| **`structuredContent` field on every tool result** | MCP 2025-11-25 added first-class structured output. Claude Desktop, Cursor, Windsurf, ChatGPT all prefer it. FastMCP generates it automatically from Pydantic return types — the feature is free if type hints are correct. | LOW | **Not explicitly in guide.** Guide shows Pydantic result types but doesn't mention `structuredContent` / `outputSchema`. **Add to requirements.** |
| **Dual content + structuredContent (backwards compat)** | MCP spec: "tool that returns structured content SHOULD also return the serialized JSON in a TextContent block." FastMCP does this automatically; just document it. | LOW | **Implicit in FastMCP behavior** — make it explicit in tests. |
| **`isError: true` for tool execution errors (not protocol errors)** | MCP 2025-11-25 spec distinguishes protocol errors (JSON-RPC) from tool execution errors. Execution errors go in the result with `isError: true` so the LLM can self-correct (e.g., "version not found → try another"). | LOW | **Not in guide §10.** Guide defines exceptions but doesn't specify mapping to `isError: true`. **Add explicit mapping.** |
| **`readOnlyHint: true`, `destructiveHint: false`, `openWorldHint: false` annotations** | Spec-recommended `ToolAnnotations`. Signals to Claude Desktop / ChatGPT / Cursor that no user confirmation is needed, improving UX. Cost: ~3 lines per tool. | LOW | **Not in guide.** FastMCP supports via `@mcp.tool(annotations=...)`. **Add.** |
| **Search result hit shape: `{uri, title, kind, snippet, score}`** | Every mature docs MCP (MS Learn, Ref, DeepWiki) returns this rough shape. LLMs parse it consistently. Without `uri`, the LLM can't call `get_docs` on a hit. Without `snippet`, the LLM can't decide whether to fetch. | LOW | Guide §3 implies `SearchDocsResult` exists but doesn't lock field names. **Lock the hit shape: `uri, title, kind, snippet, score, version`.** |
| **FTS5 snippets with `<b>...</b>` or `…` markers** | SQLite FTS5 has built-in `snippet()` function. Mature servers return ~120–250 char snippets with highlighted match terms; LLMs use these to rank. | LOW | **Not called out in guide.** Low-risk addition to retrieval module. |
| **URI that round-trips: search hit `uri` → `get_docs` call** | Users (LLMs) call `get_docs` with the `slug` / `uri` from a search hit. Broken if search returns one format and `get_docs` requires another. | LOW | **Verify in guide §3 result shapes.** Guide's `search_docs` returns hits; `get_docs` takes `slug + anchor`. These must match structurally. **Add consistency test.** |
| **`max_chars` budget on every content tool with `truncated: true` flag** | Claude Code warns at 10K tokens and caps at 25K; Cursor and Claude Desktop have similar budgets. Servers that blow past silently get blacklisted. | LOW | **In guide §3, §5** — explicit. |
| **Pagination via `start_index` / `next_start_index` in result body** | MCP spec-level pagination only covers `tools/list` and `resources/list` (opaque cursors). **There is NO spec-level pagination for tool results as of 2025-11-25** (SEP discussion #799 is still open, no consensus). Every server rolls its own; `start_index`/`next_start_index` is the most common pattern. | LOW | **In guide §3, §16** — explicit and consistent with community practice. |
| **Version-aware: every tool accepts `version: str \| None` and defaults sanely** | Python stdlib changes meaningfully across 3.12/3.13/3.14. No other docs MCP handles this well (Context7 defers to upstream, not consistent across libs). This is the table-stake differentiator for this project specifically. | LOW | **In guide §3** — `version` on all tools. |
| **First-run UX: clear "no index" message on stderr with build command** | LLM clients silently disconnect on stdout corruption and show nothing helpful on server failure. Only path the user sees is stderr. The README + first-run error is 90% of OSS install UX. | LOW | **In guide §15** — explicit. |
| **Claude Desktop + Cursor integration test before shipping** | Every other 3rd-party MCP server that skips this gets filed "doesn't work in X" issues in week one. | MEDIUM | **In guide §16 Day 4** — explicit. |
| **Structured logs to stderr with latency/truncation counters** | Observability is the only debugging channel for stdio. Users and maintainers both need it. | LOW | **In guide §11** — explicit. |
| **`list_versions` returns the default version flag** | LLM uses the default to answer "which version are we looking at?" without another round-trip. Schema already has `is_default` column. | LOW | **In guide §7** — schema has `is_default`; result shape should surface it. |

### Differentiators (Competitive Advantage for a Python-stdlib-specific MCP)

Features that set this project apart. Align with the Core Value stated in PROJECT.md: "precise, section-level evidence per token spent."

| Feature | Value Proposition | Complexity | Notes / In guide? |
|---------|-------------------|------------|-------|
| **Section-anchor-based retrieval (`get_docs(anchor=...)`)** | Returns 300–1500 chars of a section vs. 40K+ chars of a full page. None of Context7 / DeepWiki / Ref.tools / MS Learn do this precisely — they return pre-computed batches or full pages. This is the core token-efficiency win. | MEDIUM | **In guide §3, §5.2** — core locked feature. |
| **Symbol fast-path from `objects.inv`** | Sub-millisecond lookup of `asyncio.TaskGroup`-style queries without FTS. None of the comparables have this because they don't target Sphinx-built docs specifically. Cheap 10x win for the most common LLM query shape. | LOW | **In guide §5.1** — core locked feature. |
| **Version-aware index (3.12 + 3.13 at v0.1.0)** | Context7, DeepWiki, MS Learn, Ref.tools are all version-fuzzy. LLMs get stale answers when they say "is X in 3.12?". Our `doc_sets.version` + per-tool `version` param is the only way to get this right. | MEDIUM | **In guide §2, §3, §7** — locked. |
| **Synonym-expansion for concept queries (~100–200 entries)** | "How do I run code in parallel?" → `concurrent`, `multiprocessing`, `threading`, `asyncio`. BM25 is weak here; embeddings are overkill. Curated YAML table is ~50 LOC for 60–70% of the gap. Differentiator because few docs MCPs do this without embeddings. | LOW | **In guide §6** — core locked feature. |
| **Atomic-swap index publishing with rollback** | Index rebuilds never disrupt a running stdio session. Mature servers don't advertise this; most just restart hard. Differentiator for stability / daemon UX. | MEDIUM | **In guide §8** — locked. |
| **Stability tests (survive CPython doc revisions)** | Ensures the server isn't brittle to upstream changes. Most MCP servers die when upstream docs format-drifts. | MEDIUM | **In guide §14** — locked. |

### Protocol-Level Table Stakes (New or Clarified in MCP 2025-11-25)

These are MCP spec details that were **not** fully clarified in the build guide but are required for clean behavior in 2026 clients. Each is table stakes (not a differentiator) — skipping them causes real UX defects today.

| Feature | Why Required | Cost |
|---|---|---|
| **Declare `tools` capability with `listChanged: false`** | MCP 2025-11-25 §Tools — required. `listChanged: false` is fine; our tool list doesn't change at runtime. FastMCP does this by default. | ~0 LOC |
| **Declare `resources` capability with `subscribe: false, listChanged: false`** | If we expose resource URI templates (§Resource URIs below), we must declare the capability. | ~0 LOC |
| **`tools/list` pagination support (`cursor` + `nextCursor`)** | MCP spec-level: even if we only have 3 tools, the spec says `tools/list` supports pagination. Claude Code specifically has bugs when servers return `nextCursor` for `tools/list` (see anthropics/claude-code#24785). **Recommendation: do NOT return `nextCursor` — return all 3 tools in a single page.** FastMCP default behavior with `list_page_size` unset = no pagination. | ~0 LOC |
| **`outputSchema` declared per tool** | MCP 2025-11-25 §Tools — optional but strongly recommended. FastMCP auto-generates from Pydantic return type. This enables clients to pre-validate and display structured tool output. | LOW (automatic via FastMCP) |
| **Tool execution errors via `isError: true`, not thrown exceptions** | Spec says clients SHOULD surface `isError: true` results to the model for self-correction; protocol errors MAY NOT be. A `VersionNotFoundError` should become `{isError: true, content: [{type: "text", text: "Version 3.14 not in index. Available: 3.12, 3.13."}]}`, not a JSON-RPC error. | LOW |
| **`_meta["anthropic/maxResultSizeChars"]` set to ~16000 on `get_docs`** | Claude Code vendor extension. Lets `get_docs` return larger sections than Claude Code's default 25K-token cap when explicitly needed. Only apply to `get_docs`; keep `search_docs` under the default. Cap at ~16000 to stay well under Claude Code's 500K hard ceiling. | LOW |

### Anti-Features (Deliberately NOT Shipping)

Features that look smart or are commonly shipped by other docs MCPs that we should explicitly NOT build. Each includes why we reject it + what to do instead.

| Anti-Feature | Why Commonly Requested | Why Problematic for This Project | Do Instead |
|---------|---------------|-----------------|-------------|
| **MCP resources exposing page-level `docs://python/{v}/page/{slug}`** | The guide §3 proposes it and it looks clean. "Resources are the right primitive for content." | **LLMs find tools much more reliably than resources** (Claude Desktop resources UX is manual picker; LLMs don't auto-select resources). MCP Apps extension (Jan 2026) is pulling the ecosystem further toward tools-first. Resources are an orthogonal concept for human-curated context, not LLM-driven retrieval. [Note: Guide §3 proposes this; see "Anti-feature conflict with guide §3" below.] | **Serve all content via `get_docs` tool only.** Keep the `docs://` URI scheme as **canonical identifiers inside `search_docs` result hits** (so hits have stable URIs), but don't register them as MCP resources in v0.1.0. If v1.1 wants to add resource templates for IDE pickers, do it then. |
| **Resource subscriptions (`resources/subscribe`)** | "Notify client when docs change!" | Our docs don't change at runtime — `atomic-swap` rebuilds are manual. No client subscribes to stdlib docs. Adds capability negotiation + update notifications for zero benefit. | Don't declare `subscribe: true`. If in v1.1 we ever notify on index reload, use `notifications/resources/updated` opt-in. |
| **Prompt templates (`prompts/*`)** | Every reference server ships one; "feels complete." | Prompts are for workflows (e.g., "debug this error"). A docs retrieval server has no workflows to template — the LLM drives the search. Each prompt adds surface area the LLM has to reason about. | Don't declare `prompts` capability. Zero prompts shipped. |
| **Elicitation (`elicitation/create`)** | New & shiny in 2026 spec. Human-in-the-loop pause-for-input. | Only useful for destructive/mutating tools (dry-runs, confirmations) and OAuth flows. We're read-only local stdio; no flow needs user input mid-call. Adds complexity and weird state machines. | Don't declare `elicitation` capability. |
| **Sampling (`sampling/createMessage`)** | "Server asks LLM to summarize things" — tempting for search result re-ranking. | (a) Requires the client to opt-in and support sampling (Claude Desktop does not reliably). (b) Turns our local-deterministic server into a dependency on the LLM's compute budget. (c) Violates our "no runtime LLM calls" architecture. (d) Breaks offline use. | Rely on FTS5 BM25 + synonym expansion + curated ranking. Defer RRF fusion with `sqlite-vec` to v1.1 only if usage data demands it (guide §6). |
| **Roots (`roots/list`)** | Filesystem-scoped servers use it. | We have one filesystem root (`~/.cache/mcp-python-docs/`) and it's fixed. Roots adds client-driven path configuration we don't need. | Don't declare `roots` capability. |
| **MCP Apps UI extension (SEP-1865)** | Anthropic + OpenAI launched January 2026. Very hot. Iframe-based UI in the chat. | Out of scope: PROJECT.md locks "no browser-facing UI." MCP Apps requires HTML rendering, which breaks our "clients are LLMs, not humans" optimization target. | Don't implement. Revisit in v2.0 *only* if a clear human-facing use case emerges. |
| **Tasks primitive (`tasks/*`)** | Experimental in 2025-11-25. Long-running operations with retry/expiry. | Our tools are synchronous, sub-second, and stateless. Tasks is for remote HTTP servers with long workflows. Zero benefit for local stdio docs retrieval. | Set `execution.taskSupport: "forbidden"` or omit it (default). |
| **Streaming / progress notifications during search** | "Stream results as they come!" feels fast. | FTS5 queries return in 5–50ms. Streaming adds partial-state complexity for zero perceived benefit. All our operations are latency-imperceptible to humans anyway. | Return complete results synchronously. Don't implement `notifications/progress`. |
| **`ask_question` / natural language synthesis tool** | DeepWiki has one. "LLM-friendly!" | It requires server-side LLM inference or a pre-built index of answers. Turns us into a RAG backend, not a retrieval server. Expensive and out of scope per PROJECT.md "no embeddings in v1." | Keep LLM doing the synthesis; we provide the evidence. |
| **Server-generated code examples (synthesis)** | "Here, let me write a TaskGroup example." | We expose what CPython docs already contain (`examples` table from doctest blocks). Generating new ones is inference, not retrieval. | Surface existing doc-resident examples via `search_docs(kind="example")`. No generation. |
| **`curl`-style URL fetcher tool (`fetch_url`)** | Context7, DeepWiki, Ref.tools all have one. | Arbitrary internet fetch is explicitly out of scope (PROJECT.md). Also breaks offline / sandbox / deterministic guarantees that make this server safe to enable by default. | No runtime fetch. Only `get_docs(slug, anchor)` over the local index. |
| **Semantic/embedding search in v1** | "BM25 is too dumb for concept queries." | Guide §6 is explicit: synonym table first, `sqlite-vec` only in v1.1 if usage data shows insufficiency. | Ship synonym table in v0.1.0. Keep `sqlite-vec` column-space reserved but unused. |
| **Tool surface expansion: separate `resolve_symbol` / `find_examples` tools** | Feels "clean" — one tool per concept. | The guide §3 explicitly collapses these into `search_docs(kind=...)` for a reason: LLM tool-selection load scales poorly with tool count. 3 tools is the sweet spot; separating `kind` values into separate tools goes backward. | Keep `search_docs(kind="auto"\|"page"\|"symbol"\|"section"\|"example")` as one tool. |

### Anti-feature conflict with guide §3 — requires decision

The guide's §3 proposes exposing three MCP resource URI templates:

```
docs://python/{version}/page/{slug}
docs://python/{version}/section/{slug}#{anchor}
docs://python/{version}/symbol/{qualified_name}
```

**Finding:** As of April 2026, LLM-driven clients (Claude Desktop, Cursor, Windsurf, ChatGPT, Claude Code) select tools much more reliably than resources. Resources are primarily surfaced via manual picker UIs for human curation, not auto-included by the LLM. Every comparable retrieval MCP (Context7, DeepWiki, MS Learn, Ref.tools) exposes content **only** through tools in 2026, not through resource templates.

**Recommendation — two options, both legitimate:**

- **Option A (minimal, recommended):** Keep `docs://python/...` URIs as **stable identifiers in result hits** (so `search_docs` hits have round-trip-able `uri` fields), but **do not register them as MCP resource templates** in v0.1.0. The server only declares the `tools` capability. This aligns with the observed market and cuts ~50 LOC of resource-template wiring.
- **Option B (ship as designed):** Implement the resource templates exactly as the guide specifies, accepting that they're currently not used much by LLM clients but cheap to maintain and forward-compatible. If Claude Desktop / Cursor start auto-including resource content in context (which is a 2026 roadmap direction per MCP Apps momentum), we benefit for free.

**The guide's choice is defensible either way.** My recommendation is **Option A** because it matches the 2026 market and has lower maintenance surface. This is a LOW-confidence recommendation — the 2026 MCP roadmap does hint at resources becoming more LLM-visible, and this could invert by v1.1. **Route to human decision in the roadmap phase.**

---

## Result-shape recommendations (from research, not locked in guide)

The guide §3 names `SearchDocsResult`, `GetDocsResult`, `ListVersionsResult` but doesn't pin field names. Here is the recommended shape based on market convention + spec requirements.

### `search_docs` result

```python
class SearchHit(BaseModel):
    uri: str              # stable identifier: "docs://python/3.13/section/asyncio-task#taskgroup"
    title: str            # "asyncio.TaskGroup"
    kind: Literal["page", "section", "symbol", "example"]
    snippet: str          # FTS5 highlighted snippet, ~200 chars
    score: float          # BM25-derived relevance, for LLM tie-breaking
    version: str          # "3.13" — important when `version` param was None
    slug: str             # for round-tripping to `get_docs`
    anchor: str | None    # set if hit is section-level

class SearchDocsResult(BaseModel):
    hits: list[SearchHit]
    total_found: int                        # may be > len(hits) if truncated
    next_start_index: int | None            # None if no more results
    query_expanded_with: list[str]          # synonym terms added, for LLM explainability
    truncated: bool                         # True if hits were capped
```

**Rationale:**
- `uri` + `slug` + `anchor` together let the LLM call `get_docs` on any hit without re-parsing URIs.
- `score` is surprisingly useful — LLMs use it for tie-breaking when two hits seem equal.
- `query_expanded_with` is a small differentiator — gives the LLM debuggability: "oh, my 'parallel' query hit `concurrent.futures` because you expanded to `concurrent`."
- `next_start_index` matches the guide's explicit pagination model (§3, §16).
- FastMCP will auto-generate `outputSchema` from this Pydantic model — **no extra code required for MCP 2025-11-25 compliance**.

### `get_docs` result

```python
class GetDocsResult(BaseModel):
    uri: str                   # matches the `uri` format from search hits
    title: str
    version: str
    slug: str
    anchor: str | None
    content: str               # markdown or plain text — see "markdown vs JSON" below
    char_count: int
    start_index: int           # echo for pagination continuity
    next_start_index: int | None
    truncated: bool            # True if content > max_chars
    kind: Literal["page", "section"]
```

### `list_versions` result

```python
class VersionInfo(BaseModel):
    version: str               # "3.13"
    label: str                 # "Python 3.13 (stable)"
    is_default: bool
    built_at: str              # ISO 8601
    language: str              # "en"

class ListVersionsResult(BaseModel):
    versions: list[VersionInfo]
    default_version: str       # convenience — same as the version where is_default=true
```

### Markdown vs JSON vs structured: the actual recommendation

**Recommendation: Structured content + markdown body inside content blocks.**

Specifically:
- **Top-level:** Always return `structuredContent` (Pydantic model auto-serialized by FastMCP) so the LLM gets typed fields.
- **Also return `content: [TextContent]`** — the FastMCP default behavior — with a **markdown-rendered version** of the same data. This is the "backwards compat" path the MCP spec §Tools recommends.
- **Inside `GetDocsResult.content`**, use **markdown** (not HTML, not JSON-wrapped). Sphinx JSON gives us body HTML fragments; convert them to markdown at ingest time via a lightweight converter (`markdownify` or `html2text`). Markdown is what Claude Desktop, Cursor, Windsurf, Claude Code all render natively in their chat windows, and it's token-efficient (no tag bloat).

**Do NOT return raw HTML.** Claude Code's chat display doesn't render it, and it roughly doubles token count for no benefit.

Evidence: MS Learn's `microsoft_docs_fetch` explicitly returns markdown. Ref.tools' `ref_read_url` returns markdown. Context7 returns pre-formatted structured chunks that read as markdown. Every mature retrieval MCP converges on markdown-in-text + structured-alongside-it.

---

## MCP Protocol Features to Adopt for v0.1.0

Based on MCP spec 2025-11-25 (the current version as of April 2026) and observed client behavior:

### Must adopt (required for clean v0.1.0)

| Feature | Why | Cost |
|---|---|---|
| **`tools` capability with `listChanged: false`** | Required by spec for servers exposing tools. | 0 LOC (FastMCP default) |
| **`inputSchema` on every tool** | Required. FastMCP auto-generates from type hints. | 0 LOC |
| **`outputSchema` on every tool** | Optional in spec but unlocks client validation + typed rendering. FastMCP auto-generates from return type annotations. | 0 LOC if we use Pydantic return types |
| **`structuredContent` in every result** | FastMCP auto-populates when return is a Pydantic model. | 0 LOC |
| **`content: [TextContent]` mirror of structured** | Backwards compat per spec. FastMCP default. | 0 LOC |
| **Tool annotations: `readOnlyHint: true`, `destructiveHint: false`, `openWorldHint: false`** | Signals to all 2026 clients that confirmation prompts aren't needed. | ~3 LOC total |
| **Tool execution errors via `isError: true`** | Spec requires this pattern for "LLM can self-correct" errors like version-not-found. | ~10 LOC in an error adapter |
| **`tools/list` without pagination (omit `nextCursor`)** | Claude Code bug #24785: some clients silently drop tools when `nextCursor` is present. Only 3 tools — return them all. | 0 LOC (FastMCP default) |

### Should adopt (table stakes for UX)

| Feature | Why | Cost |
|---|---|---|
| **`_meta["anthropic/maxResultSizeChars"]: 16000` on `get_docs`** | Lets `get_docs` return larger sections than Claude Code's default cap. Vendor extension but safe. | ~2 LOC |
| **Content `annotations.priority: 0.9` on primary content blocks** | Spec-defined. Helps clients prioritize in context. Cheap. | ~3 LOC |
| **Content `annotations.audience: ["assistant"]` on search hits, `["user", "assistant"]` on `get_docs` body** | Helps Claude Desktop decide what to show vs. inject. | ~3 LOC |

### Must NOT adopt (anti-features; see table above)

- `prompts` capability
- `resources/subscribe`
- `elicitation` capability
- `sampling` capability
- `roots` capability
- `tasks/*` primitive (`execution.taskSupport: "forbidden"`)
- `notifications/progress`
- MCP Apps UI extension
- `resources` capability **(defer per Option A above)**

---

## Error Taxonomy Mapping to MCP 2025-11-25

The guide §10 defines Python exception classes. Here is the required mapping to MCP error semantics so LLM clients behave well.

| Guide Exception | Cause | MCP Treatment | JSON-RPC Code | `isError` | Example result |
|---|---|---|---|---|---|
| `VersionNotFoundError` | Client asked for version not in index | **Tool execution error** | N/A | `true` | `{content: [text: "Version '3.14' not in index. Available: 3.12, 3.13."], isError: true}` |
| `SymbolNotFoundError` | `search_docs(kind="symbol")` finds nothing | **Tool execution error** (not an exception — search returned 0 hits) | N/A | `false` | `{hits: [], total_found: 0, ...}` — empty is a valid result, not an error |
| `PageNotFoundError` | `get_docs(slug=X)` where slug doesn't exist | **Tool execution error** | N/A | `true` | `{content: [text: "Page 'asyncfoo' not found in 3.13. Use search_docs to discover."], isError: true}` |
| `IndexNotBuiltError` | No index on disk; server starts but can't serve | **Protocol error** on every tool call | `-32603` (internal error) | N/A | `{error: {code: -32603, message: "Index not built. Run: mcp-server-python-docs build-index --versions 3.13"}}` |
| `IngestionError` | Only raised in CLI paths, never in `serve` | Not an MCP error; CLI exit code | N/A | N/A | CLI only |
| `FTS5UnavailableError` | Startup-time environment check fails | Server refuses to start; structured stderr message | N/A | N/A | Pre-MCP, stderr only |
| (new) `BudgetExceededError` | Content exceeds `max_chars` | Not an exception — surfaced as `truncated: true` in result | N/A | `false` | `{content: "...", truncated: true, next_start_index: 8000}` |

**Key pattern:** errors the **LLM can fix** (wrong version, wrong slug) should be `isError: true` with a helpful message. Errors the **LLM can't fix** (no index, FTS5 missing) should be protocol errors or startup failures. This is per MCP spec §Error Handling guidance. **Add to requirements.**

---

## Feature Dependencies

```
search_docs(kind="auto") ──requires──> FTS5 available + synonym table populated
    └──requires──> build-index completed ──requires──> sphobjinv + Sphinx JSON parser
    └──uses──> symbol fast-path ──requires──> objects.inv ingested
    └──uses──> BM25 ranker with column weights
    └──uses──> snippet extraction ──requires──> FTS5 snippet()

get_docs(anchor=...) ──requires──> sections table populated with stable anchors
    └──requires──> Sphinx JSON parser extracts section hierarchy correctly
    └──uses──> apply_budget() ──provides──> truncated + next_start_index

list_versions ──requires──> doc_sets table has ≥1 row
    └──displayed──> is_default flag surfaced in result

Tool annotations (readOnlyHint, etc) ──enhances──> every tool
Tool outputSchema ──enhances──> every tool (auto via FastMCP + Pydantic)
structuredContent ──enhances──> every tool result (auto via FastMCP + Pydantic)
isError mapping ──enhances──> error paths in every tool

First-run UX error ──requires──> startup check for index existence
    └──requires──> stderr-only logging discipline (§9)
```

**Phase-ordering implication for roadmap:**
1. Schema + FTS5 check + stderr logging **first** (nothing works without).
2. `sphobjinv` + symbol fast-path → first working `search_docs(kind="symbol")` end-to-end.
3. Sphinx JSON ingestion + sections table → unlocks `get_docs(anchor=...)`.
4. Synonym table + BM25 ranking → `search_docs(kind="auto")`.
5. Pagination + `isError` mapping + tool annotations + structured output → polish pass.
6. Atomic publishing + multi-version → ship readiness.
7. Integration test with Claude Desktop + Cursor → ship gate.

This matches the guide's 4-week plan exactly — no reordering needed.

---

## MVP Definition (aligned with PROJECT.md Active list)

### Launch With (v0.1.0)

- [x] **3 tools: `search_docs`, `get_docs`, `list_versions`** — in guide, market-validated
- [x] **Python 3.12 + 3.13 in the index** — in PROJECT.md
- [ ] **Locked result shapes (Pydantic models) with `outputSchema`** — add to guide
- [ ] **`structuredContent` + text mirror on every result** — auto via FastMCP, document it
- [ ] **Tool annotations: `readOnlyHint`, `destructiveHint: false`, `openWorldHint: false`** — add to guide
- [ ] **Error-to-`isError` mapping per the table above** — add to guide
- [ ] **Markdown body in `get_docs.content`** — convert at ingest time
- [x] **Symbol fast-path** — in PROJECT.md
- [x] **Section windowing via `anchor`** — in PROJECT.md
- [x] **Budget enforcement (`max_chars` + `start_index` pagination)** — in PROJECT.md
- [x] **Synonym table (100–200 entries)** — in PROJECT.md
- [x] **Atomic-swap index publishing + rollback** — in PROJECT.md
- [x] **Stderr-only logging + FTS5 capability check + WAL + RO/RW split** — in PROJECT.md
- [x] **Stability tests** — in PROJECT.md
- [x] **Claude Desktop + Cursor integration test** — in PROJECT.md
- [x] **`uvx` + PyPI distribution** — in PROJECT.md
- [ ] **README shows a `_meta.anthropic/maxResultSizeChars` example for clients that honor it** — document it

### Add After Validation (v1.1)

- [ ] **Resource templates (`docs://python/...` as MCP resources)** — add if Option B wins the roadmap decision, or if client UX in 2026 shifts resources back into LLM auto-context
- [ ] **`versionadded`/`versionchanged` diff tool** — schema already captures it
- [ ] **`sqlite-vec` hybrid search** — only if synonym table proves insufficient (usage-data-driven)
- [ ] **Third-party Sphinx docs** — extend ingestion; design generalizes naturally
- [ ] **HTML-fallback content path** — only if Sphinx JSON breaks
- [ ] **Differential / incremental ingestion** — if full rebuild becomes too slow
- [ ] **Tool-level pagination SEP #799 adoption** — if it lands and clients support it; until then, keep our `start_index` pattern

### Future Consideration (v2+)

- [ ] **HTTP / SSE transport** — with OAuth, origin validation, rate limiting
- [ ] **Multi-language (i18n)** — `language` column already reserved
- [ ] **Sampling-driven re-ranking** — only if clients support sampling reliably
- [ ] **MCP Apps UI extension** — only if a human-facing surface becomes a priority

---

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| `search_docs`, `get_docs`, `list_versions` tool shell | HIGH | LOW | **P1** |
| Symbol fast-path via objects.inv | HIGH | LOW | **P1** |
| Section windowing via `anchor` | HIGH | LOW | **P1** |
| Budget enforcement + `start_index` pagination | HIGH | LOW | **P1** |
| Synonym table (~150 entries) | HIGH | LOW | **P1** |
| `structuredContent` + `outputSchema` via FastMCP/Pydantic | HIGH | ~0 | **P1** |
| Tool annotations (readOnlyHint etc) | MEDIUM | ~0 | **P1** |
| `isError` mapping for tool execution errors | MEDIUM | LOW | **P1** |
| Markdown-formatted body in `get_docs.content` | HIGH | LOW | **P1** |
| `_meta.anthropic/maxResultSizeChars` on `get_docs` | MEDIUM | ~0 | **P1** |
| FTS5 snippet extraction in search hits | HIGH | LOW | **P1** |
| Version-aware retrieval (3.12 + 3.13) | HIGH | MEDIUM | **P1** |
| Atomic-swap index publishing | HIGH | MEDIUM | **P1** |
| Stability tests (~20 structural) | HIGH | MEDIUM | **P1** |
| Claude Desktop + Cursor integration test | HIGH | MEDIUM | **P1** |
| MCP resource templates (`docs://python/...`) | LOW | MEDIUM | **P3** (defer; see Option A) |
| `sqlite-vec` hybrid search | LOW | HIGH | **P3** |
| HTML-fallback ingestion | LOW | HIGH | **P3** |
| `versionadded`/`versionchanged` diff tool | MEDIUM | MEDIUM | **P2** |
| Prompt templates | LOW | LOW | **not shipping** |
| Elicitation / sampling / roots / tasks | LOW | MEDIUM | **not shipping** |
| Ask-question / summarization tool | LOW | HIGH | **not shipping** |

---

## Competitor Feature Analysis

| Feature | Context7 | DeepWiki | MS Learn MCP | Ref.tools | **This project** |
|---|---|---|---|---|---|
| Tool count | 2 | 3 | 3 | 2 | **3** |
| Search tool | `resolve-library-id` + `query-docs` | `ask_question` | `microsoft_docs_search` | `ref_search_documentation` | **`search_docs` (unified)** |
| Content tool | `query-docs` returns batches | `read_wiki_contents` | `microsoft_docs_fetch` | `ref_read_url` | **`get_docs` (section or page)** |
| Version awareness | Library ID can encode version | No (GitHub repo-level only) | No (Learn tracks latest) | No | **Yes — `version` param on all tools** |
| Section-level retrieval | No (fixed batches) | No (topic-level) | Markdown page | Full page | **Yes — via `anchor`** |
| Result format | Structured text | Markdown | Markdown | Markdown | **Structured + markdown** |
| Pagination | Batch-only | N/A | N/A | N/A | **`start_index` / `next_start_index`** |
| Symbol fast-path | No | No | No | No | **Yes — objects.inv** |
| Synonym expansion | Unknown | No | Semantic (LLM-side) | Smart filtering | **Curated YAML table** |
| Local / offline | No (hosted) | No (hosted) | No (hosted) | No (hosted) | **Yes — stdio + local SQLite** |
| Auth required | API key | No | No | API key | **None** |
| Transport | stdio + HTTP | HTTP | HTTP | stdio + HTTP | **stdio only** |

**Unique positioning:** This project is the **only docs retrieval MCP in 2026 that is (a) local-first / offline, (b) version-aware at tool level, (c) section-level precision via anchors, and (d) targeted at the Python stdlib specifically.** No comparable server does all four. Even Ref.tools, which is the closest competitor in design philosophy ("minimum tokens, precise retrieval"), is a hosted service that can't work offline and doesn't have version awareness at Python-stdlib granularity.

---

## Sources

**MCP specification (HIGH confidence):**
- [MCP Specification 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25) — current spec as of April 2026
- [MCP Tools §spec 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/server/tools) — CallToolResult, content blocks, structuredContent, outputSchema, error handling
- [MCP Resources §spec 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/server/resources) — URI schemes, resource templates, annotations
- [MCP 2026 Roadmap](https://blog.modelcontextprotocol.io/posts/2026-mcp-roadmap/) — Tasks primitive status, transport evolution
- [Tool Annotations as Risk Vocabulary — MCP Blog 2026-03-16](https://blog.modelcontextprotocol.io/posts/2026-03-16-tool-annotations/) — readOnlyHint / destructiveHint / openWorldHint guidance
- [MCP Apps Extension — Jan 2026 announcement](https://blog.modelcontextprotocol.io/posts/2026-01-26-mcp-apps/) — SEP-1865, UI extension scope

**Comparable servers (HIGH confidence):**
- [Context7 (upstash/context7)](https://github.com/upstash/context7) — 2 tools: `resolve-library-id`, `query-docs`
- [Context7 API reference on DeepWiki](https://deepwiki.com/upstash/context7/4-api-reference)
- [DeepWiki MCP server (Cognition)](https://cognition.ai/blog/deepwiki-mcp-server) — 3 tools: `read_wiki_structure`, `read_wiki_contents`, `ask_question`
- [Microsoft Learn MCP (MicrosoftDocs/mcp)](https://github.com/MicrosoftDocs/mcp) — 3 tools: `microsoft_docs_search`, `microsoft_docs_fetch`, `microsoft_code_sample_search`
- [Ref.tools MCP (ref-tools/ref-tools-mcp)](https://github.com/ref-tools/ref-tools-mcp) — 2 tools: `ref_search_documentation`, `ref_read_url`
- [Ref MCP tools reference](https://docs.ref.tools/mcp/tools)
- [arabold/docs-mcp-server](https://github.com/arabold/docs-mcp-server) — open-source "search + fetch-url + scrape" retrieval server
- [Anthropic reference servers (modelcontextprotocol/servers)](https://github.com/modelcontextprotocol/servers) — Fetch, Filesystem, Git, Memory

**Client behavior / result shape (HIGH–MEDIUM confidence):**
- [Claude Code MCP docs](https://code.claude.com/docs/en/mcp) — 10K token warning, MAX_MCP_OUTPUT_TOKENS env var, `_meta["anthropic/maxResultSizeChars"]` up to 500K
- [FastMCP — Tools documentation](https://gofastmcp.com/servers/tools) — auto `outputSchema` from type hints, Pydantic + dataclass support, `ToolResult`
- [FastMCP — Pagination](https://gofastmcp.com/servers/pagination) — confirms tool-result pagination is NOT built in; only `tools/list` etc.
- [Spec Proposal #799 — Tool request/response pagination](https://github.com/modelcontextprotocol/modelcontextprotocol/discussions/799) — still open; no consensus on standardized tool-result pagination as of April 2026
- [Claude Code #24785 — MCP tools/list pagination bug](https://github.com/anthropics/claude-code/issues/24785) — reason to omit `nextCursor` on our 3-tool list

**Error handling (MEDIUM confidence):**
- [MCP Error Codes — mcpevals.io](https://www.mcpevals.io/blog/mcp-error-codes) — JSON-RPC 2.0 error code reservations, protocol vs execution vs transport
- [Error Handling in MCP Tools — apxml.com](https://apxml.com/courses/getting-started-model-context-protocol/chapter-3-implementing-tools-and-logic/error-handling-reporting) — `isError` convention for LLM self-correction

**Ecosystem / trends (MEDIUM confidence):**
- [MCP in 2026 — DEV Community](https://dev.to/pooyagolchian/mcp-in-2026-the-protocol-that-replaced-every-ai-tool-integration-1ipc) — 50+ official, 150+ community servers as of Q1 2026
- [Top 7 Context7 Alternatives in 2026](https://medium.com/@moshesimantov/top-7-mcp-alternatives-for-context7-in-2026-58038413a20f) — comparison of Context7, Ref, DeepWiki

---

## Research flags & open questions

1. **(LOW confidence)** Option A vs Option B for the `docs://python/...` resource-template question. The market says "tools only, not resources" in April 2026, but the MCP 2026 roadmap hints at resources becoming more LLM-visible. Route to human decision in the roadmap phase — both are defensible.
2. **(LOW confidence)** Exact default value for `_meta["anthropic/maxResultSizeChars"]` on `get_docs`. I recommended 16000 but that's a guess based on Claude Code's 10K warning / 25K default. The right answer is empirical — measure once during integration testing.
3. **(MEDIUM confidence)** Tool-level pagination SEP #799 could land in a post-2025-11-25 spec bump before we ship. If it does, we should adopt the standard shape instead of our hand-rolled `start_index`. Low probability over a 4-week build window — proceed with `start_index` and watch for the SEP.
4. **(HIGH confidence, flag only)** FastMCP versioning — the research surfaced that mcp 1.27.0 is current (April 2026) and mcp 2.x is in pre-alpha. Stick with 1.27.x for v0.1.0. v2.0 is explicitly out of scope for ship.

---

*Feature research for: Python stdlib documentation MCP retrieval server*
*Researched: 2026-04-15*
