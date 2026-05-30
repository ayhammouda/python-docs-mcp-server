# Agent Context — CPython source SHA pin

> One-read working context for issue `[v0.3.0] ingestion — pin CPython source by commit SHA`.
> PARTIAL issue: you do the pin + verification; Vision handles the SECURITY.md prose.

## 1. Roadmap excerpt

> **Build-time supply-chain hardening** (roadmap §4, v0.3.0): Pin CPython source
> by SHA, not by tag. Document the threat model in SECURITY.md (the `build-index`
> CPython clone is the largest non-runtime attack surface). Verify Sphinx-build
> environment isolation.
>
> **Decision 5.10 (locked):** Build-time supply chain (the `build-index` CPython
> clone) is an explicit risk area; threat model documented in SECURITY.md;
> CPython source pinned by SHA.

## 2. Code touch-points

- `src/mcp_server_python_docs/ingestion/cpython_versions.py`
  - `CPythonDocsBuildConfig(TypedDict)` — add `sha: str`.
  - `CPYTHON_DOCS_BUILD_CONFIG` — five entries, currently `{"tag": ..., "sphinx_pin": ...}`:
    `3.10→v3.10.20`, `3.11→v3.11.15`, `3.12→v3.12.13`, `3.13→v3.13.13`, `3.14→v3.14.4`.
    Add the resolved SHA to each. Resolve with:
    `git ls-remote https://github.com/python/cpython.git refs/tags/<tag>`
    (use the dereferenced commit — the `<tag>^{}` line — not the annotated-tag object).
- `src/mcp_server_python_docs/__main__.py:210–226` — the clone:
  `git clone --depth 1 --branch config["tag"] https://github.com/python/cpython.git <clone_dir>`.
  After it, add: `rev = git -C <clone_dir> rev-parse HEAD`; if `rev != config["sha"]`,
  log a clear error and **abort this version's build** (raise / skip-with-failure —
  match the existing error-handling style in this function; do not silently continue).
- `tests/test_ingestion.py:53` — existing assertion
  `config["tag"].startswith(f"v{version}.")`. Add a sibling assertion that
  `config["sha"]` matches `^[0-9a-f]{40}$`.

## 3. Patterns to follow

- `tests/test_ingestion.py` iterates `CPYTHON_DOCS_BUILD_CONFIG.items()` for the
  tag assertion — extend that same loop for the SHA assertion. No new fixtures.
- The clone block already uses `subprocess.run([...], check=True, capture_output=True, text=True)`
  — reuse that idiom for the `rev-parse` call.

## 4. Known pitfalls

- **`--branch <tag>` cannot take a raw SHA** on a shallow clone against GitHub by
  default. Keep the tag-based shallow fetch; make the **SHA a post-clone
  verification gate**, not the fetch ref. That is the integrity win: a moved/re-tagged
  tag now fails the build instead of silently changing canonical content.
- Use the **dereferenced commit SHA** (peeled tag), not the annotated tag object's
  own SHA — `rev-parse HEAD` after checkout gives the commit; match that.
- **Do not edit `SECURITY.md`** (forbidden). Draft the threat-model paragraph in
  the PR body + decision log below for Vision to apply.
- A full `build-index` clones over the network and takes minutes — do not gate the
  PR on it. The unit tests cover the config + verification logic offline.
- Don't bump any tag to a newer CPython point release; pin the SHA of the
  **current** tag only.

## 5. Decision log

- Resolved SHAs (tag → 40-hex commit), one line each:
  - 3.10 / v3.10.20 → 842e987df856a5d4db37933c62a3456930a19092
  - 3.11 / v3.11.15 → 2340a037f7450e70fccfe411e6531afb4d57a312
  - 3.12 / v3.12.13 → 3bb231a6a5dc02b95658877318bf61501a7209e9
  - 3.13 / v3.13.13 → 01104ce1beb3135c2e0c01ec835b994c1f55a1c0
  - 3.14 / v3.14.4 → 23116f998f6789d8c2fbe5ed5b8146854c8c2a4f
- Where/how the verification aborts on mismatch: after the shallow
  tag-based clone in `build-index`, `git -C <clone_dir> rev-parse HEAD` is
  compared to the authoritative config SHA. A mismatch logs the version, tag,
  actual SHA, and expected SHA, then raises `SystemExit(1)` before Sphinx setup
  or content ingestion can proceed.
- **Draft SECURITY.md threat-model paragraph (for Vision to apply):**
  > The largest build-time supply-chain input is the `build-index` clone of the
  > upstream CPython repository, which provides the source tree used to generate
  > canonical documentation content. Each supported CPython docs release is pinned
  > to the exact commit SHA that its human-readable release tag currently resolves
  > to; the tag is retained for operator readability, but the SHA is the
  > authoritative integrity anchor. If a tag is reissued, moved, or otherwise
  > resolves to different source content, the build fails before Sphinx setup or
  > content ingestion rather than silently publishing changed documentation.
