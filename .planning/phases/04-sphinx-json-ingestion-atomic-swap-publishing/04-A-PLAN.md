---
phase: 4
plan_id: 04-A
title: "Sphinx JSON parsing and content ingestion module"
wave: 1
depends_on: []
files_modified:
  - src/mcp_server_python_docs/ingestion/sphinx_json.py
  - pyproject.toml
requirements:
  - INGR-C-04
  - INGR-C-05
  - INGR-C-06
  - INGR-C-07
  - INGR-C-08
  - INGR-C-09
autonomous: true
---

<objective>
Create `ingestion/sphinx_json.py` — the core module that parses Sphinx JSON `.fjson` files, extracts documents/sections/examples, converts HTML to markdown via markdownify, populates `synonyms` from `synonyms.yaml`, and rebuilds FTS5 indexes. This module handles per-document failure isolation so one broken file never aborts the whole build.
</objective>

<tasks>

<task id="1">
<title>Add markdownify dependency to pyproject.toml</title>
<read_first>
- pyproject.toml (current dependencies list)
</read_first>
<action>
Add `markdownify>=0.14,<2.0` to the `dependencies` list in `pyproject.toml` under `[project]`. Place it after the existing `pyyaml` entry. Then run `uv sync` to install it.

The exact line to add:
```
"markdownify>=0.14,<2.0",
```

This pulls in `beautifulsoup4` and `six` transitively, which are also needed for HTML parsing in the ingestion module.
</action>
<acceptance_criteria>
- `pyproject.toml` contains `"markdownify>=0.14,<2.0"` in the dependencies list
- `uv sync` completes without error
- `python -c "from markdownify import markdownify"` succeeds
</acceptance_criteria>
</task>

<task id="2">
<title>Create ingestion/sphinx_json.py with fjson parsing and section extraction</title>
<read_first>
- src/mcp_server_python_docs/ingestion/inventory.py (analog — pattern for DB inserts, FTS rebuild)
- src/mcp_server_python_docs/storage/schema.sql (table definitions — documents, sections, examples, synonyms column names and types)
- src/mcp_server_python_docs/storage/db.py (bootstrap_schema, get_readwrite_connection)
- python-docs-mcp-server-build-guide.md §8 (ingestion strategy)
- .planning/phases/04-sphinx-json-ingestion-atomic-swap-publishing/04-RESEARCH.md (fjson file structure)
</read_first>
<action>
Create `src/mcp_server_python_docs/ingestion/sphinx_json.py` with the following functions:

**1. `parse_fjson(filepath: Path) -> dict`**
- Load a `.fjson` file via `json.load()`
- Return the parsed dict
- Raise `IngestionError` on JSON decode failure

**2. `html_to_markdown(html: str) -> str`**
- Convert HTML body fragment to markdown using `from markdownify import markdownify as md`
- Call `md(html, heading_style="ATX", strip=['img', 'script', 'style'])` to produce clean markdown
- Return the markdown string
- Strip leading/trailing whitespace

**3. `extract_sections(body_html: str, doc_uri: str) -> list[dict]`**
- Parse `body_html` with `BeautifulSoup(body_html, "html.parser")`
- Find all heading tags `h1, h2, h3, h4, h5, h6` that have an `id` attribute
- For each heading:
  - `anchor`: the heading's `id` attribute
  - `heading`: the heading's text content (stripped)
  - `level`: integer 1-6 from the tag name
  - `ordinal`: sequential position (0-based)
  - `content_html`: all sibling content until the next heading of same or higher level
  - `content_text`: `html_to_markdown(content_html)`
  - `char_count`: `len(content_text)`
  - `uri`: `f"{doc_uri}#{anchor}"`
- Return list of section dicts
- If no headings found, create a single section with anchor="" covering the whole body

**4. `extract_code_blocks(body_html: str) -> list[dict]`**
- Parse `body_html` with BeautifulSoup
- Find all `<div>` elements whose class list contains `highlight-pycon` (doctests) or `highlight-python3` or `highlight-default` or `highlight` (standalone examples)
- For each:
  - `code`: extract the text content of the inner `<pre>` element
  - `is_doctest`: `1` if class contains `highlight-pycon`, else `0`
  - `language`: `"python"`
  - `ordinal`: sequential position (0-based)
- Also find the nearest preceding heading (with `id`) to determine which section this code block belongs to → `section_anchor`
- Return list of code block dicts with `section_anchor` key

**5. `ingest_fjson_file(conn: sqlite3.Connection, filepath: Path, doc_set_id: int, base_uri: str) -> bool`**
- Wraps parse_fjson + extract_sections + extract_code_blocks with per-document failure isolation (INGR-C-06)
- On ANY exception: log `logger.warning(f"Skipping {filepath}: {e}")` and return `False`
- On success:
  - Extract `current_page_name` from fjson data for slug (e.g., `library/asyncio-task`)
  - Extract `title` from fjson data (strip HTML tags if present)
  - Build `doc_uri` as `f"{current_page_name}.html"`
  - Convert full body HTML to markdown for `content_text`
  - INSERT into `documents` table: `(doc_set_id, uri=doc_uri, slug=current_page_name, title, content_text, char_count)`
  - Get the new `document_id` from `cursor.lastrowid`
  - For each section from `extract_sections`:
    - INSERT into `sections` table: `(document_id, uri, anchor, heading, level, ordinal, content_text, char_count)`
  - For each code block from `extract_code_blocks`:
    - Find the matching `section_id` by looking up the section with matching anchor
    - INSERT into `examples` table: `(section_id, code, language, is_doctest, ordinal)`
  - Return `True`

**6. `ingest_sphinx_json_dir(conn: sqlite3.Connection, json_dir: Path, doc_set_id: int) -> tuple[int, int]`**
- Walk `json_dir` recursively for all `.fjson` files (excluding `globalcontext.json`, `searchindex.json`, `genindex.fjson`, `py-modindex.fjson`)
- Call `ingest_fjson_file()` for each
- Return `(success_count, failure_count)`
- Log progress every 100 files: `logger.info(f"Processed {n} documents...")`

**7. `populate_synonyms(conn: sqlite3.Connection) -> int`**
- Load `synonyms.yaml` via `importlib.resources.files("mcp_server_python_docs") / "data" / "synonyms.yaml"`
- Parse YAML, iterate entries
- For each `concept: expansion` pair:
  - If expansion is a list, join with space: `" ".join(expansion)`
  - INSERT OR REPLACE INTO `synonyms` table: `(concept, expansion)`
- Return count of entries inserted
- INGR-C-09

**8. `rebuild_fts_indexes(conn: sqlite3.Connection) -> None`**
- Rebuild all three FTS5 indexes after content ingestion:
  ```python
  conn.execute("INSERT INTO sections_fts(sections_fts) VALUES('rebuild')")
  conn.execute("INSERT INTO examples_fts(examples_fts) VALUES('rebuild')")
  ```
- Note: `symbols_fts` is rebuilt by `inventory.py`, not here
- `conn.commit()`
- INGR-C-08

All functions use module-level `logger = logging.getLogger(__name__)`.
Import `IngestionError` from `mcp_server_python_docs.errors`.
</action>
<acceptance_criteria>
- `src/mcp_server_python_docs/ingestion/sphinx_json.py` exists
- File contains `def parse_fjson(`
- File contains `def html_to_markdown(`
- File contains `def extract_sections(`
- File contains `def extract_code_blocks(`
- File contains `def ingest_fjson_file(`
- File contains `def ingest_sphinx_json_dir(`
- File contains `def populate_synonyms(`
- File contains `def rebuild_fts_indexes(`
- File contains `from markdownify import markdownify`
- File contains `from bs4 import BeautifulSoup`
- File contains `logger.warning` (per-document failure isolation)
- File does NOT contain `import mcp` or `from mcp` (ingestion never imports MCP)
- File does NOT contain `from mcp_server_python_docs.server` (no server imports)
- File does NOT contain `from mcp_server_python_docs.retrieval` (no retrieval imports)
- `python -c "from mcp_server_python_docs.ingestion.sphinx_json import parse_fjson, html_to_markdown, extract_sections, extract_code_blocks, ingest_fjson_file, ingest_sphinx_json_dir, populate_synonyms, rebuild_fts_indexes"` succeeds
</acceptance_criteria>
</task>

</tasks>

<verification>
- [ ] sphinx_json.py exists with all 8 functions
- [ ] markdownify is in pyproject.toml dependencies
- [ ] Module imports successfully
- [ ] No MCP or server or retrieval imports in the file
- [ ] Per-document failure isolation via try/except in ingest_fjson_file
- [ ] FTS rebuild covers sections_fts and examples_fts (INGR-C-08)
- [ ] Synonyms populated from synonyms.yaml (INGR-C-09)
- [ ] Code blocks distinguished by highlight-pycon vs highlight-python3 (INGR-C-07)
- [ ] HTML body converted to markdown via markdownify (INGR-C-05)
</verification>

<must_haves>
- Per-document failure isolation (INGR-C-06) — broken fjson never aborts build
- HTML-to-markdown conversion (INGR-C-05) — no raw HTML in content_text
- Code block extraction with doctest classification (INGR-C-07)
- FTS5 index population in same transaction as canonical tables (INGR-C-08)
- Synonyms table population from synonyms.yaml (INGR-C-09)
</must_haves>
