# Agent Context — ADR-006 (Serialization)

> One-read working context for issue `[v0.3.0] docs — write ADR-006 (Serialization)`.
> This is a **writing** task. You are recording locked decisions, not making new ones.

## 1. Roadmap excerpts (the decisions you are recording — verbatim)

- **Principle 2.5:** Wire format is explicit and pluggable on structured tools
  only. Compact JSON default; TOON opt-in *if and only if* the empirical study
  supports it. `get_docs` stays markdown. *Token economy is empirical, not architectural.*
- **Principle 2.7:** Layered design with stable contracts — eight layers, the
  **serializer** being one of them.
- **Decision 5.3:** Storage stays SQLite + markdown. **TOON-as-storage killed.**
- **Decision 5.4:** Empirical Claude-tokenizer study **gates** the `format="toon"` decision.
- **Decision 5.5:** `format` parameter on `search_docs`, `list_versions`,
  `compare_versions` **only**. JSON default; TOON opt-in. `get_docs` stays markdown.
- **Decision 5.8:** The study measures **client-side rewrap**, not just raw
  payload tokens; reports tokens AND latency per tool family.

## 2. Code touch-points (for accuracy — describe, do NOT change)

- Tool results are Pydantic models in `src/mcp_server_python_docs/models.py`
  (e.g. `GetDocsResult`); tools live in `server.py` and return those models,
  which FastMCP serializes. The "serializer layer" is the conceptual seam where
  a structured result becomes a wire string — that's what the `format` parameter
  will eventually parameterize. You are documenting that seam, not building it.
- `get_docs` returns markdown content (`GetDocsResult.content`) — this is why it
  is carved out of the `format` parameter (markdown is already the canonical body).

## 3. Pattern to follow

- There is no `docs/architecture/` ADR yet — you are establishing the house
  style. Use the exact skeleton embedded in the issue. Keep it tight (1–2 pages).
- Number/name the file `docs/architecture/ADR-006-serialization.md` to match the
  roadmap's ADR numbering (ADR-001 and ADR-006 are the first two written).

## 4. Known pitfalls

- **Do not invent.** If you find yourself making a serialization choice that is
  not in §2 above, that's a pipeline §7 trigger ("cites a design choice not in
  the issue") — stop and comment.
- **Do not implement `format`.** That is v0.3.x and is gated by the study.
- Don't claim a TOON token win — the study hasn't run. The ADR records that TOON
  is *opt-in and gated*, with the bar being "win holds after client rewrap" (5.8).
- "Reference architecture" is **not** claimed externally (decision 5.6) — keep
  the ADR factual, not promotional.

## 5. Decision log

- Final file path:
- Any wording you were unsure mapped to a locked decision (and how you resolved it):
- Open follow-ups (e.g. link to TOKEN-STUDY.md once it exists):
