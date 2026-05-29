# Strategic Roadmap — python-docs-mcp-server

**Adopted:** 2026-05-29
**Status:** Active. This document is the canonical forward-looking strategy; review at each minor release.
**Supersedes / consolidates:** the four prior strategy artifacts listed in §7.

---

## 1. Mission

**The canonical, token-frugal Python stdlib oracle for AI coding agents — architected to be cloned.**

Said longer: be the server AI coding agents reach for first when a Python stdlib question comes up, returning exact symbols, exact sections, and exact versions from CPython source itself — offline, always free, always MIT, token-efficient by design. Ship the architecture clearly enough that adopters can clone the pattern for other documentation ecosystems (Rust, Go, Node) without reinventing the design.

Two audiences, one product:

- **AI coding agents and their users.** Claude, Cursor, Codex, and the developers using them get a fast, deterministic, canonical answer to any Python stdlib question.
- **MCP authors building docs servers for other languages.** The project's ADRs, layered architecture, and (eventually) template repo make cloning the pattern a weekend's work instead of a quarter's.

The label *"reference architecture"* is **not** claimed externally. If the writing earns it as a community verdict over 12 months, the label sticks for free; if it doesn't, the project is not on the hook for an overclaim.

### 1.1 How we know we won

| Signal | Target by v0.5.0 | Target by v1.0.0 |
|---|---|---|
| PyPI installs / month | 5,000 | 25,000 |
| GitHub stars | 1,000 | 5,000 |
| Citations by other MCP authors / blog posts | 5 | 25 |
| External adopter cloning the architecture for another language | 0 (acceptable) | ≥1 |
| Default-listed in at least one major coding agent's setup docs | 0 (acceptable) | ≥1 |
| Token / correctness benchmark cited as canonical for Python docs MCPs | 1 (own publication) | 3+ (third-party references) |

These targets are deliberately aggressive on the high end and forgiving on the low end. The v1.0.0 numbers correspond to "credibly top-tier for the docs-MCP category"; the v0.5.0 numbers correspond to "moving in the right direction post-launch."

---

## 2. Architectural Principles (locked)

These are the principles future decisions must respect. Reopening any of them requires a deliberate amendment to this roadmap.

| # | Principle | Why |
|---|---|---|
| 2.1 | **Canonical source only.** CPython at a pinned tag for stdlib docs; PyPI metadata API for package URLs. No scraped mirrors. No third-party indexers. | Correctness and version-accuracy are the moat. |
| 2.2 | **Offline-first runtime.** No network access at query time. The server is a local CDN edge over canonical docs. | Determinism, no rate limits, no API key surface. |
| 2.3 | **Always MIT, always free.** No paid tier, no closed-source extensions, no usage caps — ever. | Permanent positioning anchor (decision 5.1). |
| 2.4 | **Storage stays SQLite + markdown.** Storage format is closed; not re-openable in v0.x. | Universal, debuggable, greppable; FTS5 needs uncompressed text; markdown remains the right canonical body format for prose. |
| 2.5 | **Wire format is explicit and pluggable on structured tools only.** Compact JSON default; TOON opt-in if and only if the empirical study supports it. `get_docs` stays markdown. | Token economy is empirical, not architectural. |
| 2.6 | **Cache-first as a mental model.** Cold origin → warm index → hot derived-response cache → in-memory LRU. Every layer rebuildable from the layer above. | Justifies the architecture as "a CDN edge for docs in your editor." |
| 2.7 | **Layered design with stable contracts.** Eight layers: source connector / ingestion / storage / retrieval / budget / serializer / cache / transport. Contracts between layers are documented and stable. | What makes the pattern cloneable for other doc ecosystems. |
| 2.8 | **Strong trust posture.** MIT, OpenSSF Scorecard, CodeQL clean, attested releases via PyPI Trusted Publishing, build-time supply-chain threat model documented. | Differentiation vs cloud-first competitors who can't verify equivalently. |

---

## 3. Where We Are (v0.2.1 baseline)

**Shipped (2026-04 → 2026-05-29):**

- PyPI publish path live (v0.1.5 → v0.1.6 → v0.2.0 → v0.2.1).
- Six MCP tools: `search_docs`, `get_docs`, `lookup_package_docs`, `list_versions`, `detect_python_version`, `compare_versions`.
- Python versions 3.10 – 3.14 indexed.
- Local SQLite + FTS5 index built from CPython source via `sphinx-build -b json`.
- Retrieved-docs cache, request-keyed, scoped to `index.db` fingerprint.
- Trusted Publishing with Sigstore attestations; OpenSSF Scorecard published; CodeQL clean.
- Proactive transitive bumps for CVE-2026-45409 (`idna` ReDoS) and PYSEC-2026-161 (`starlette` BadHost — explicitly affects MCP servers).
- Python 3.14 `fork`→`forkserver` regression patched (Sphinx parallel-build pickling issue).
- Positioning anchor: `.planning/POSITIONING.md` with per-surface adapter contract.

**Not yet shipped (the road ahead):**

- Empirical token study on Claude's tokenizer, with client-rewrap measurement.
- App-level zstd compression on the retrieved-docs cache.
- `format` parameter on the three structured tools.
- Architecture documentation (ADRs + design document).
- Public benchmark harness against all eligible docs MCPs.
- Personal blog + launch post.
- Phases 10 and 11 (`whatsnew_for_version`, `detect_python_version` v2 venv-aware).
- README / PyPI description refresh to reflect the 6-tool surface (still lists 5 in some surfaces).

---

## 4. Milestone Roadmap

Versioning follows semver. Behavior-additive changes (new tools, new optional parameters) are minor; bug fixes are patch.

### v0.3.0 — Measurement, Compression, Hygiene  *(target: 4 weeks)*

The "instrument and tighten" release. Lays the empirical and operational foundation for everything that follows. **This is the most important release on the roadmap** because its outputs gate the v0.3.x and v0.5.0 decisions.

| Deliverable | Notes |
|---|---|
| **Empirical token study** | One afternoon. Uses Anthropic's free token-counting API as the primary instrument (accepts the full structured-message envelope including tools). Measures both **token cost and serialization latency** per tool family. **Crucially measures client-side rewrap** — sends the same tool response through Claude Desktop / Cursor / Codex and observes what actually lands in the model context. Output: `docs/architecture/TOKEN-STUDY.md`. |
| **Workstream J — app-level zstd cache compression** | Targets retrieved-docs cache value column only. Trained dict on representative `get_docs` corpus. Codec column for forward-compat. Expected ratio strong because zstd's dictionary mode is documented as especially effective on small correlated records — exactly the cache-entry shape. |
| **30-minute TOON Python port audit** | Decides whether `format="toon"` is operationally viable in v0.3.x. If the port is unmaintained, ship JSON-only. |
| **README + PyPI + glama.json refresh** | Reflect the 6-tool surface including `compare_versions`. Adopt as a release-cycle discipline going forward (decision 5.8): every release updates the public-facing tool table. |
| **Build-time supply-chain hardening** | Pin CPython source by SHA, not by tag. Document the threat model in SECURITY.md (the `build-index` CPython clone is the largest non-runtime attack surface). Verify Sphinx-build environment isolation. |
| **PyYAML safe-loader audit** | `synonyms.yaml` is loaded at startup; confirm only `yaml.safe_load` is used; document the trust boundary. |
| **ADR-001 (Source Adapters) and ADR-006 (Serialization)** | First two of the eight ADRs. Establishes the layer-contract pattern. ADR-006 specifically enables the v0.3.x format parameter work. |

### v0.3.x — Format Parameter  *(timing: gated by v0.3.0 study)*

The "selective serialization" release(s). Adds the `format` parameter to the three structured tools per locked decision 5.4.

| Deliverable | Notes |
|---|---|
| `format` on `search_docs`, `list_versions`, `compare_versions` | JSON default. Always available. Existing clients see no behavior change unless they opt in. |
| `format="toon"` opt-in | **Only if** v0.3.0 study shows a meaningful token win on Claude's tokenizer **after client rewrap**, with acceptable latency. If the study fails this bar, the `format` parameter ships JSON-only and TOON is deferred indefinitely. |
| ADR-006 published as a standalone blog post | First post on the new blog. Anchors the personal brand on the architecture work. |

### v0.4.0 — Phase 10 + Phase 11  *(target: 8 weeks after v0.3.0)*

The "venv-aware" release. Adds the two remaining differentiating tools from the competitive brief.

| Deliverable | Notes |
|---|---|
| `whatsnew_for_version(version)` | Section-sliced "What's New" page sourced from CPython `whatsnew/*.rst`. Reuses the multi-version index plumbing. |
| `detect_python_version` v2 (venv-aware) | Reads `VIRTUAL_ENV`, `.venv/pyvenv.cfg`, `pyproject.toml` `requires-python`, `.python-version`. Auto-routes subsequent queries to the detected version. |
| ADRs 2 – 5 | Ingestion, Storage, Retrieval, Budget. |

### v0.5.0 — Architecture Documentation & Launch  *(target: 12 weeks after v0.3.0)*

The "design out loud" release. The architecture documentation becomes complete enough to support external adoption.

| Deliverable | Notes |
|---|---|
| ADRs 7 and 8 | Cache, Transport. |
| `docs/architecture/DESIGN.md` | 5-page design document tying the ADRs together. |
| **Public benchmark harness** | All eight target docs MCPs + no-MCP baseline; 50-question Python eval covering symbols, concepts, cross-version, and PEP-adjacent. Reproducible from a clean clone. Methodology disclosure mandatory. |
| **Launch post: "Canonical Python stdlib for your AI agent"** | Lede is the `compare_versions` demo + benchmark headline. Cross-posted to dev.to and Show HN. Published on the personal blog (live since the ADR-006 post). |
| PyCon / EuroPython CFP submitted | Talk anchored on the architecture work. |

### v1.0.0 — API Freeze  *(target: ~6 months from now)*

The "stable" release. Public API frozen; breaking changes would require v2.

| Deliverable | Notes |
|---|---|
| API freeze across all tools | Semver discipline kicks in fully. |
| Deprecation policy + security disclosure docs | Lifecycle commitments visible. |
| `docs-mcp-template` (decision gate, §6 q1) | Ship **only if** at least one external adopter has signaled interest by v0.5.0. Otherwise defer indefinitely. |
| Optional Streamable HTTP transport (§6 q3) | Ship behind a flag if there is a clear remote-server use case by v0.5.0. The architectural separation already supports both. |

---

## 5. Locked Decisions

Consolidated from prior artifacts and this consolidation.

| # | Decision | Source / Date |
|---|----------|---------------|
| 5.1 | Always MIT, always free, no paid tier ever. | Change request §9.5 (2026-05-14) |
| 5.2 | Repo rename to `python-stdlib-mcp` deliberately dropped in v0.1.5; revisit no earlier than v1.0. | Change request §9.2 reversed (2026-05-14) |
| 5.3 | Storage stays SQLite + markdown. TOON-as-storage killed. | Brainstorm §0.1 (2026-05-29) |
| 5.4 | Empirical Claude-tokenizer study gates the `format="toon"` decision. | Brainstorm §0.2 (2026-05-29) |
| 5.5 | `format` parameter on `search_docs`, `list_versions`, `compare_versions` only. JSON default; TOON opt-in. `get_docs` stays markdown. | Brainstorm §0.3 (2026-05-29) |
| 5.6 | "Reference architecture" label dropped externally; the writing work ships anyway. | Brainstorm §0.4 (2026-05-29) |
| 5.7 | App-level zstd on retrieved-docs cache, no gate. Versioned codec column for forward-compat. | Brainstorm §0.5 (2026-05-29) |
| 5.8 | Empirical study measures **client-side rewrap**, not just raw payload tokens. Uses Anthropic's free token-counting API as primary instrument. Reports **tokens AND latency** per tool family. | Deep-research integration (2026-05-29) |
| 5.9 | README / PyPI description / glama.json refresh to reflect the 6-tool surface; this becomes a release-cycle discipline going forward. | Deep-research integration (2026-05-29) |
| 5.10 | Build-time supply chain (the `build-index` CPython clone) is an explicit risk area; threat model documented in SECURITY.md; CPython source pinned by SHA. | Deep-research integration (2026-05-29) |
| 5.11 | PyYAML safe-loader-only discipline; `synonyms.yaml` is the only YAML input and is packaged with the wheel. | Deep-research integration (2026-05-29) |
| 5.12 | Autonomous agents work only via the issue-and-PR flow defined in `AGENT-EXECUTION-PIPELINE.md`. Direct commits to `main` are forbidden; auto-merge is forbidden. | Agent-pipeline addition (2026-05-29) |
| 5.13 | Forbidden-territory list in `AGENT-EXECUTION-PIPELINE.md` §2 is binding on all agents. | Agent-pipeline addition (2026-05-29) |
| 5.14 | Every agent-targetable issue must have a per-issue context file under `.planning/agent-context/<issue-slug>.md`. | Agent-pipeline addition (2026-05-29) |

---

## 6. Open Questions

Not yet locked. Each should be resolved within the next 2 – 4 weeks.

1. **`docs-mcp-template` ship/skip.** Adopt "defer; ship only if external adoption signals" (recommended), or commit now to building it by v0.5.0?
2. **Cross-tokenizer claims.** Run §3 study only on Claude's tokenizer (decisive for product behavior), or also GPT-5 and Gemini for comparative analysis in the design document?
3. **HTTP transport.** Stay stdio-only through v1.0 (recommended), or add a streamable HTTP adapter behind a flag in v0.5? Current architectural separation supports both; user-facing surface is bigger with HTTP.
4. **Pre-built index hosting.** Ship `python-docs-mcp-server install-index` that downloads a pre-built `index.db` from GitHub Release assets, so users skip the multi-minute Sphinx build? Worth doing in v0.3.0 if bandwidth cost is acceptable.

---

## 7. Supporting Artifacts

| Artifact | Role | Status |
|---|---|---|
| `AGENT-EXECUTION-PIPELINE.md` | Autonomous-agent policy, guardrails, validation gates, templates | Active; load-bearing for §9 |
| `OPENCLAW-FORGE-PROTOCOL.md` | OpenClaw role split for this MCP: Vision supervises, Gilfoyle implements, Heimdall verifies, Saga excluded by default because there is no UI | Active; operating layer for §9 |
| `competitive-brief.docx` | Original market positioning analysis (Context7, Ref.tools, arabold, DeepWiki, GitMCP, etc.) | Reference |
| `CHANGE-REQUEST-v0.1.5-launch.md` | Implementation plan for v0.1.5 launch — executed | Historical (rename dropped, otherwise complete) |
| `ARCHITECTURE-BRAINSTORM-FEEDBACK-2026-05-29.md` | TOON / cache-first / reference-architecture brainstorm with the original locked decisions | Superseded by §2 and §5 of this roadmap |
| Deep-research report (uploaded 2026-05-29) | Independent third-party audit; validation of locked decisions + §3 study methodology refinements | Folded into §4 v0.3.0 and §5.8 – 5.11 |
| `.planning/ROADMAP.md` | Engineering phase-by-phase plan (v0.1.0 execution) | Historical; phase 9 complete; 10 – 11 scaffolds active |
| `.planning/POSITIONING.md` | Per-surface adapter contract for the positioning sentence | Active; load-bearing |
| `CHANGELOG.md` | Keep-a-Changelog release history | Active |

---

## 8. Next Three Concrete Moves

1. **Run the empirical token study.** One afternoon. Anthropic token-counting API as the primary instrument; measures client-side rewrap by running the same tool response through Claude Desktop / Cursor / Codex; reports both tokens and latency per tool family. Output: `docs/architecture/TOKEN-STUDY.md`.
2. **Ship Workstream J (zstd cache).** Any free day. Trained dict on representative `get_docs` corpus; versioned codec column.
3. **Refresh README + PyPI + glama.json** to reflect the 6-tool surface. ~10-minute PR. Establishes the release-cycle discipline of decision 5.9.

After those three, the v0.3.0 milestone is unlocked end-to-end and the v0.3.x format-parameter work can begin.

---

## 9. Autonomous-Agent Execution

A material portion of this roadmap will be executed by autonomous coding agents (Claude Code or similar) working unattended against GitHub issues. The execution policy, guardrails, forbidden territory, validation gates, and per-issue context-file requirements live in a companion document:

[`AGENT-EXECUTION-PIPELINE.md`](AGENT-EXECUTION-PIPELINE.md)

That file is **mandatory reading** before any agent-targetable issue is generated. It defines:

- **Forbidden territory** (the don't-touch list — public API, schema, workflows, brand assets).
- **Issue structure** every agent-ready issue must contain.
- **Acceptance-criteria patterns** that are testable rather than vague.
- **The canonical validation gate** (ruff → pyright → pytest → doctor) that must pass before any PR.
- **Human-review triggers** that force a pause even when the agent thinks it's done.
- **Recovery procedures** when an agent gets stuck.
- **Per-issue context files** in `.planning/agent-context/` that give the agent everything it needs in one read.

OpenClaw's concrete role split for this repo lives in:

[`OPENCLAW-FORGE-PROTOCOL.md`](OPENCLAW-FORGE-PROTOCOL.md)

Default execution is Vision → Gilfoyle → Heimdall → Vision/Aymen. Saga is not
part of the default loop because this MCP has no UI surface to review.

### 9.1 Deliverable annotations

Each v0.3.0 deliverable in §4 is classified by agent-friendliness:

| Deliverable (v0.3.0) | Agent-friendly? | Lead |
|---|---|---|
| Workstream J — zstd cache codec | **Yes (high)** | Agent |
| README / PyPI / glama.json refresh to 6-tool surface | **Yes (high)** | Agent |
| PyYAML safe-loader audit | **Yes (medium)** | Agent |
| ADR-001 (Source Adapters) draft | **Yes (medium)** | Agent w/ strict template |
| ADR-006 (Serialization) draft | **Yes (medium)** | Agent w/ strict template |
| Build-time supply-chain: CPython SHA pin | **Yes (partial)** | Agent for the pin; human for SECURITY.md prose |
| 30-minute TOON Python port audit | **No** | Human (subjective quality judgment) |
| Empirical token study | **No** | Human (methodology + corpus selection); agent may scaffold the harness |

The recommended overnight wave is the four high-confidence agent issues first — they produce obvious morning wins and de-risk the harder ones.

### 9.2 Pre-flight before unleashing agents

Before the first agent-ready issue is queued, the pre-flight checklist in `AGENT-EXECUTION-PIPELINE.md` §10 must be green. In particular:

- `.github/CODEOWNERS`, `.github/ISSUE_TEMPLATE/autonomous-agent.yml`, and `.github/PULL_REQUEST_TEMPLATE/agent.md` must exist on `main`.
- Branch protection on `main` must require ≥1 human approval.
- The `🛑 needs-human-review` and `agent-ready` labels must exist.
- The canonical validation gate must pass on `main` from a clean clone.

### 9.3 Additional locked decisions for the pipeline

| # | Decision |
|---|----------|
| 5.12 | Autonomous agents work only via the issue-and-PR flow defined in `AGENT-EXECUTION-PIPELINE.md`. Direct commits to `main` are forbidden; auto-merge is forbidden. |
| 5.13 | The forbidden-territory list in `AGENT-EXECUTION-PIPELINE.md` §2 is binding. Any agent change touching those paths must pause for human review. |
| 5.14 | Every agent-targetable issue must have a per-issue context file under `.planning/agent-context/<issue-slug>.md` so the agent reads one source of truth instead of fishing across `.planning/` archive material. |

---

## 10. Review Triggers

This roadmap is reviewed at:

- Each minor release (v0.3.0, v0.4.0, v0.5.0, v1.0.0).
- Any material change in the MCP ecosystem (e.g., Anthropic ships first-party docs retrieval; Context7 announces a Python-stdlib mode; a competitor MCP cracks 10k stars).
- Owner's discretion when new external information arrives (e.g., another deep-research report; a sufficiently sharp critique from the community).

Out-of-cycle amendments are tracked at the bottom of this file as `## Amendment YYYY-MM-DD` sections, preserving the original text. The locked-decisions table (§5) is the authoritative current state.
