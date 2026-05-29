# Agent Context — ADR-001 (Source Adapters)

> One-read working context for issue `[v0.3.0] docs — write ADR-001 (Source Adapters)`.
> A **writing** task. Every claim must match the code — verify before you assert.

## 1. Roadmap excerpts (the principles you are recording)

- **Principle 2.1:** Canonical source only. CPython at a pinned tag for stdlib
  docs; PyPI metadata API for package URLs. No scraped mirrors. No third-party indexers.
- **Principle 2.2:** Offline-first *runtime*. No network access at query time.
- **Principle 2.7:** Layered design with stable contracts; the **source
  connector** is layer 1 of 8 and is what makes the pattern cloneable.

## 2. The two source adapters that exist today (describe these)

1. **CPython documentation source** (`src/mcp_server_python_docs/ingestion/`):
   - `cpython_versions.py` — pinned build targets (`CPYTHON_DOCS_BUILD_CONFIG`:
     per-version `tag` + `sphinx_pin`). Five versions: 3.10–3.14.
   - `__main__.py` `build-index` path — `git clone --depth 1 --branch <tag>` of
     `python/cpython`, builds docs with `sphinx-build -b json` in a dedicated venv.
   - `sphinx_json.py` — parses the Sphinx JSON output into the index; also loads
     `synonyms.yaml`. `inventory.py` — parses `objects.inv` for exact symbol resolution.
2. **PyPI metadata source** (`src/mcp_server_python_docs/services/package_docs.py`):
   - Backs `lookup_package_docs`. A **controlled** PyPI metadata lookup
     (`GET /pypi/<project>/json`) that returns only project/docs/homepage/source
     URLs — not a generic web fetch, not scraped docs.

## 3. The one documented exception to "offline-first"

- `lookup_package_docs` performs a network call to PyPI's metadata API. This is
  **not** a docs-*query*-time call against the canonical stdlib index — it is a
  controlled, narrowly-scoped metadata lookup. The ADR must state this exception
  explicitly so the offline-first invariant (2.2) stays honest. (See README's
  "Why not Context7" section and `SECURITY.md` scope for the existing framing.)

## 4. Known pitfalls

- **Verify, don't assume.** Open each cited file and confirm the behavior before
  writing it into the ADR. An ADR that misstates current behavior is worse than none.
- Don't document adapters that don't exist (Rust/Go) beyond a single "future
  adopters clone this contract" sentence — that's the cloneability point, not a claim.
- No code, schema, or workflow changes — writing only.
- Keep it factual; "reference architecture" is not claimed externally (5.6).

## 5. Decision log

- File path:
- Claims you verified against code (file:line):
- Anything ambiguous about the layer contract that you flagged for the maintainer:
