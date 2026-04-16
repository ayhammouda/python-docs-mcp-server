# Phase 4: Sphinx JSON Ingestion & Atomic-Swap Publishing - Research

**Researched:** 2026-04-15
**Confidence:** HIGH
**Research mode:** Validation of upstream dependencies + implementation approach

## Research Questions & Findings

### RQ1: CPython Sphinx Pins (Re-verified)

**Status:** CONFIRMED — no drift since CLAUDE.md validation.

- **CPython 3.12** (`Doc/requirements.txt`): `sphinx~=8.2.0` (compatible release, allows 8.2.x)
- **CPython 3.13** (`Doc/requirements.txt`): `sphinx<9.0.0` (allows any 8.x)
- Both branches also require: `blurb`, `sphinxext-opengraph~=0.13.0`, `sphinx-notfound-page~=1.0.0`, `python-docs-theme>=2023.3.1,!=2023.7`
- A `-c constraints.txt` reference exists for additional build constraints

**Implication for build-index:** The dedicated venv must install ALL of `Doc/requirements.txt`, not just Sphinx. Custom extensions in `Doc/Tools/extensions/` also need to be importable (they live in the CPython source tree, added to `sys.path` by `Doc/conf.py`).

### RQ2: Sphinx JSON Builder Status

**Status:** CONFIRMED working, one non-blocking bug.

- The JSON builder is implemented as `JSONHTMLBuilder` in `sphinxcontrib.serializinghtml`.
- Builder name: `json`, output suffix: `.fjson`, global context file: `globalcontext.json`
- **Only open issue:** #13448 — JSON builder incorrectly caches translation when building multiple languages. **Does not affect us** (English-only path).
- The old regression #11615 (Sphinx 7.2.0 breakage) is closed/fixed.
- No new blocking issues found for Sphinx 8.x JSON builder.

### RQ3: .fjson File Structure

Each `.fjson` file is a JSON object with these keys (from `get_doc_context` + global context):

| Key | Type | Description |
|-----|------|-------------|
| `body` | string | HTML fragment of the page content (the main documentation body) |
| `title` | string | Page title rendered as HTML |
| `toc` | string | Local table of contents as HTML bullet list |
| `current_page_name` | string | Document name (e.g., `library/asyncio-task`) |
| `parents` | list | Navigation parent chain `[{link, title}]` |
| `prev` | object/null | Previous page `{link, title}` or null |
| `next` | object/null | Next page `{link, title}` or null |
| `meta` | dict | Document metadata |
| `display_toc` | bool | Whether TOC has more than one entry |
| `sourcename` | string | Source file name |
| `metatags` | string | HTML meta tags |

**Key insight for ingestion:** The `body` field contains the rendered HTML fragment. This is what we parse for sections (heading hierarchy with anchors) and code blocks (`highlight-pycon` for doctests, `highlight-python3` for examples). The `current_page_name` gives us the slug. The `title` gives us the document title.

### RQ4: Custom CPython Extensions Serialization

CPython's `Doc/conf.py` loads these custom extensions:
- `audit_events`, `availability`, `c_annotations`, `changes`, `glossary_search`
- `implementation_detail`, `issue_role`, `lexers`, `misc_news`, `pydoc_topics`, `pyspecific`

Optional (conditionally loaded): `linklint.ext`, `notfound.extension`, `sphinxext.opengraph`

**These extensions live in `Doc/Tools/extensions/` within the CPython source tree.** The `Doc/conf.py` adds this path to `sys.path` automatically, so they are importable when `sphinx-build` runs from within the `Doc/` directory.

**Serialization concern:** The `pyspecific` extension adds custom nodes (`versionadded`, `versionchanged`, `deprecated`, `availability`, etc.). The JSON builder serializes these as HTML fragments within the `body` field — the HTML rendering happens before JSON serialization. This means the custom nodes are already resolved to HTML by the time we read the `.fjson` files. **No `NotImplementedError: Unknown node` risk** as long as:
1. We use the same Sphinx version CPython pins
2. All CPython extensions are importable (they are, from the source tree)
3. We run `sphinx-build` from within the `Doc/` directory

### RQ5: markdownify Library

**Version:** 1.2.2 (released 2025-11-16)
**Dependencies:** `beautifulsoup4>=4.9,<5`, `six>=1.15,<2`
**API:** `from markdownify import markdownify as md` — `md(html_string)` returns markdown string
**Advanced:** `MarkdownConverter` class for subclassing custom conversion logic

**Decision confirmed:** markdownify is the right choice (D-01 in CONTEXT.md). It handles:
- Heading preservation (HTML headings → markdown headings)
- Link conversion (HTML anchors → markdown links)
- Code block preservation
- Table conversion
- Inline formatting (bold, italic, code)

**Dependency impact:** Adds `markdownify>=0.14,<2.0` + transitive `beautifulsoup4` + `six` to runtime deps.

### RQ6: CPython Makefile Targets

**CONFIRMED: No `json` target in `Doc/Makefile`.** Available build targets: help, build, html, htmlhelp, latex, text, texinfo, epub, changes, linkcheck, coverage, doctest, pydoc-topics, gettext, htmlview, htmllive, clean, clean-venv, venv, dist-*.

**Correct approach:**
1. Run `make venv` (or equivalent) to create `Doc/venv/` with pinned deps
2. Invoke `./venv/bin/sphinx-build -b json Doc/ Doc/build/json/` directly

**Alternative (our approach):** Since we do a shallow git clone, we create our OWN venv rather than using `make venv`. This avoids Makefile dependencies (like `uv` detection logic in the Makefile) and gives us full control over the Sphinx version.

### RQ7: Build Time & Disk Estimates

Based on known CPython doc build characteristics:
- **Shallow clone:** ~50-80MB download (single tag, depth 1)
- **Sphinx JSON build time:** ~3-8 minutes depending on hardware (comparable to HTML build)
- **Output size:** ~2000+ .fjson files for the full stdlib
- **Memory footprint:** ~300-500MB during sphinx-build (Sphinx loads all cross-references)
- **Final index.db size:** ~20-40MB (text content + FTS indexes)

### RQ8: Atomic Swap Protocol

POSIX `os.rename()` is atomic on the same filesystem. The protocol:
1. Build to `build-{timestamp}.db` (temp path in same cache dir)
2. SHA256 hash → `ingestion_runs.artifact_hash`
3. Smoke test against the new DB
4. `os.rename(current_index, index.db.previous)` — backup
5. `os.rename(new_db, index.db)` — atomic swap
6. Print restart message to stderr

**Critical note:** A running server with an open RO fd continues reading the OLD inode after rename. This is the documented v0.1.0 limitation — "restart required" (PUBL-05). The WAL mode + RO connection means the server won't crash, just serve stale data.

## Validation Architecture

### Dimension 1: Input Validation
- fjson file parsing: malformed JSON, missing keys, empty body
- Version string validation in CLI
- Shallow clone failure (network, bad tag)

### Dimension 2: Output Correctness
- Sections extracted match heading hierarchy in source HTML
- Code blocks correctly classified (doctest vs example)
- Markdown conversion preserves structure (no data loss)
- FTS rebuild populates searchable content

### Dimension 3: Error Handling
- Per-document failure isolation (INGR-C-06)
- Sphinx build failure (bad extension, missing dep)
- Git clone failure (network, tag not found)
- Atomic swap failure (disk full, permissions)

### Dimension 4: Integration
- New index works with existing retrieval layer
- Schema compatibility with Phase 2 DDL
- WAL mode allows concurrent read during build

### Dimension 5: Performance
- Build completes in reasonable time (<15 min)
- Index size stays under reasonable bounds (<50MB)
- Smoke tests complete quickly (<5s)

### Dimension 6: Security
- No code execution from doc content
- Temp directories cleaned up
- No secrets in index

### Dimension 7: Edge Cases
- Empty document (no sections)
- Document with only code blocks
- Very large document (exceeds memory?)
- Unicode in headings/content
- Duplicate anchors within a document

### Dimension 8: Regression
- Ingestion-while-serving test (PUBL-06)
- Schema backward compatibility
- FTS index consistency after rebuild

## Implementation Approach

### Module Structure

```
src/mcp_server_python_docs/ingestion/
    __init__.py          (existing)
    inventory.py         (existing — objects.inv ingestion)
    sphinx_json.py       (NEW — Sphinx JSON build + fjson parsing)
    publish.py           (NEW — atomic swap + smoke tests)
    cli.py               (NEW — enhanced build-index with content ingestion)
```

### Key Technical Decisions

1. **Sphinx venv:** Create in temp directory alongside the clone. Cleaned up with the clone after ingestion. No caching of venvs (simplicity over speed for v0.1.0).

2. **fjson parsing:** `json.load()` each file, extract `body` (HTML), `title`, `current_page_name`. Parse `body` with beautifulsoup4 (already a markdownify dep) for heading extraction and code block classification.

3. **Section extraction:** Walk HTML heading tags (h1-h6) to build section hierarchy. Each heading with an `id` attribute becomes a section row. Content between headings becomes `content_text` (after markdownify conversion).

4. **Code block extraction:** Find `<div class="highlight-pycon">` (doctest) and `<div class="highlight-python3">` (example). Extract inner text as `code`, set `is_doctest` accordingly.

5. **Synonym population:** Read `synonyms.yaml` via `importlib.resources`, insert each entry into `synonyms` table.

6. **Transaction strategy:** All document/section/example inserts within a single transaction per document. FTS rebuild after all documents are inserted.

## Blockers

None. All research questions answered satisfactorily. Upstream dependencies are stable.

## RESEARCH COMPLETE
