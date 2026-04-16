"""Sphinx JSON ingestion: parse .fjson files, extract sections/examples, convert to markdown.

Handles per-document failure isolation (INGR-C-06) so one broken file never
aborts the whole build. Converts HTML body fragments to markdown (INGR-C-05),
extracts code blocks with doctest classification (INGR-C-07), populates
FTS5 indexes (INGR-C-08), and seeds the synonyms table (INGR-C-09).
"""
from __future__ import annotations

import importlib.resources
import json
import logging
import re
import sqlite3
from pathlib import Path

import yaml

from mcp_server_python_docs.errors import IngestionError

# Build-only dependencies (I-4): these are moved to the [build] extras group so
# the serve-time runtime stays lean. At import time we probe them and fall back
# to None sentinels — _ensure_build_deps() re-raises a clear ImportError at the
# public entry points that actually need them.
try:
    from bs4 import BeautifulSoup, Tag  # type: ignore[assignment]
except ImportError:  # pragma: no cover - depends on install-time extras
    BeautifulSoup = None  # type: ignore[assignment,misc]
    Tag = None  # type: ignore[assignment,misc]

try:
    from markdownify import markdownify as md  # type: ignore[assignment]
except ImportError:  # pragma: no cover - depends on install-time extras
    md = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def _ensure_build_deps() -> None:
    """Raise a clear ImportError if build-only deps are missing (I-4)."""
    missing = []
    if BeautifulSoup is None:
        missing.append("beautifulsoup4")
    if md is None:
        missing.append("markdownify")
    if missing:
        raise ImportError(
            f"build-index requires optional build deps: {', '.join(missing)}. "
            "Install with: pip install 'mcp-server-python-docs[build]'"
        )

# Files to skip during directory ingestion (not documentation pages)
_SKIP_FILES = {
    "globalcontext.json",
    "searchindex.json",
}

_SKIP_SLUGS = {
    "genindex",
    "py-modindex",
    "search",
    "contents",
}


def parse_fjson(filepath: Path) -> dict:
    """Load and parse a .fjson file.

    Args:
        filepath: Path to the .fjson file.

    Returns:
        Parsed JSON as a dict.

    Raises:
        IngestionError: If the file cannot be parsed as JSON.
    """
    try:
        with open(filepath, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise IngestionError(f"Failed to parse {filepath}: {e}") from e


def html_to_markdown(html: str) -> str:
    """Convert an HTML body fragment to markdown (INGR-C-05).

    Uses markdownify with ATX-style headings and strips non-content
    elements (images, scripts, styles).

    Args:
        html: HTML string to convert.

    Returns:
        Markdown string with leading/trailing whitespace stripped.
    """
    _ensure_build_deps()
    if not html or not html.strip():
        return ""
    result = md(html, heading_style="ATX", strip=["img", "script", "style"])
    return result.strip()


def extract_sections(body_html: str, doc_uri: str) -> list[dict]:
    """Extract sections from HTML body based on heading hierarchy (INGR-C-04).

    Walks heading tags (h1-h6) that have an ``id`` attribute, treating each
    as a section boundary. Content between headings becomes the section's
    ``content_text`` after markdown conversion.

    Args:
        body_html: HTML body fragment from an .fjson file.
        doc_uri: Document URI (e.g., ``library/asyncio-task.html``).

    Returns:
        List of section dicts with keys: anchor, heading, level, ordinal,
        content_text, char_count, uri.
    """
    _ensure_build_deps()
    if not body_html or not body_html.strip():
        return []

    soup = BeautifulSoup(body_html, "html.parser")
    heading_tags = soup.find_all(re.compile(r"^h[1-6]$"), id=True)

    if not heading_tags:
        # No headings with id — create a single section covering the whole body
        content = html_to_markdown(body_html)
        if not content:
            return []
        return [
            {
                "anchor": "",
                "heading": "Introduction",
                "level": 1,
                "ordinal": 0,
                "content_text": content,
                "char_count": len(content),
                "uri": doc_uri,
            }
        ]

    sections: list[dict] = []
    for i, tag in enumerate(heading_tags):
        anchor = tag.get("id", "")
        heading_text = tag.get_text(strip=True)
        # Remove pilcrow/paragraph marks that Sphinx adds
        heading_text = heading_text.rstrip("\u00b6").strip()
        level = int(tag.name[1])

        # Collect content: everything between this heading and the next
        content_parts: list[str] = []
        sibling = tag.next_sibling
        next_heading = heading_tags[i + 1] if i + 1 < len(heading_tags) else None

        while sibling is not None:
            if next_heading is not None and sibling is next_heading:
                break
            if isinstance(sibling, Tag):
                # Check if this tag contains the next heading
                if next_heading is not None and sibling.find(
                    re.compile(r"^h[1-6]$"), id=str(next_heading.get("id", ""))
                ):
                    break
                content_parts.append(str(sibling))
            sibling = sibling.next_sibling

        content_html = "".join(content_parts)
        content_text = html_to_markdown(content_html)

        sections.append(
            {
                "anchor": anchor,
                "heading": heading_text,
                "level": level,
                "ordinal": i,
                "content_text": content_text,
                "char_count": len(content_text),
                "uri": f"{doc_uri}#{anchor}",
            }
        )

    return sections


def extract_code_blocks(body_html: str) -> list[dict]:
    """Extract code blocks from HTML body, classifying doctests vs examples (INGR-C-07).

    Finds ``<div class="highlight-pycon">`` (doctests) and
    ``<div class="highlight-python3">`` or ``<div class="highlight-default">``
    (standalone examples).

    Args:
        body_html: HTML body fragment from an .fjson file.

    Returns:
        List of code block dicts with keys: code, is_doctest, language,
        ordinal, section_anchor.
    """
    _ensure_build_deps()
    if not body_html or not body_html.strip():
        return []

    soup = BeautifulSoup(body_html, "html.parser")
    blocks: list[dict] = []

    # Find all highlight divs (M-6: anchored on both ends within a class token
    # so 'highlight-pythonfoo' / 'xhighlight-python' don't spuriously match).
    highlight_divs = soup.find_all(
        "div",
        class_=re.compile(r"^highlight-(pycon3?|python3?|default)$"),
    )

    for i, div in enumerate(highlight_divs):
        classes = div.get("class") or []

        # M-6: use an explicit class-name list for doctest detection instead of
        # a loose substring check; the class list is already known-safe thanks
        # to the anchored regex above, but defense-in-depth is cheap.
        is_doctest = (
            1 if any(c in ("highlight-pycon", "highlight-pycon3") for c in classes) else 0
        )

        # Extract code text from the <pre> element
        pre = div.find("pre")
        if pre is None:
            continue
        code = pre.get_text()
        if not code.strip():
            continue

        # Find nearest preceding heading to determine section
        section_anchor = ""
        element = div
        while element:
            element = element.find_previous(re.compile(r"^h[1-6]$"), id=True)
            if element:
                section_anchor = element.get("id", "")
                break

        blocks.append(
            {
                "code": code,
                "is_doctest": is_doctest,
                "language": "python",
                "ordinal": i,
                "section_anchor": section_anchor,
            }
        )

    return blocks


def ingest_fjson_file(
    conn: sqlite3.Connection,
    filepath: Path,
    doc_set_id: int,
    base_uri: str = "",
) -> bool:
    """Ingest a single .fjson file with per-document failure isolation (INGR-C-06).

    On ANY exception, logs a warning and returns False without aborting
    the overall build.

    Args:
        conn: Read-write SQLite connection.
        filepath: Path to the .fjson file.
        doc_set_id: Foreign key into doc_sets.
        base_uri: Base URI prefix (usually empty for relative URIs).

    Returns:
        True if ingestion succeeded, False if the file was skipped.
    """
    _ensure_build_deps()
    try:
        data = parse_fjson(filepath)

        # Extract core fields
        body_html = data.get("body", "")
        title_html = data.get("title", "")
        current_page_name = data.get("current_page_name", "")

        if not current_page_name:
            logger.warning("Skipping %s: no current_page_name", filepath)
            return False

        # Skip non-documentation pages
        slug = current_page_name
        if any(slug.endswith(skip) or slug == skip for skip in _SKIP_SLUGS):
            return False

        # Clean title (strip HTML tags if present)
        title = BeautifulSoup(title_html, "html.parser").get_text(strip=True)
        if not title:
            title = slug.split("/")[-1]

        # Build document URI
        doc_uri = f"{current_page_name}.html"

        # Convert full body to markdown for document content_text
        content_text = html_to_markdown(body_html)
        char_count = len(content_text)

        # Insert document
        cursor = conn.execute(
            "INSERT OR REPLACE INTO documents "
            "(doc_set_id, uri, slug, title, content_text, char_count) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (doc_set_id, doc_uri, slug, title, content_text, char_count),
        )
        document_id = cursor.lastrowid

        # Extract and insert sections
        sections = extract_sections(body_html, doc_uri)
        section_id_by_anchor: dict[str, int] = {}

        for section in sections:
            cursor = conn.execute(
                "INSERT OR REPLACE INTO sections "
                "(document_id, uri, anchor, heading, level, ordinal, "
                "content_text, char_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    document_id,
                    section["uri"],
                    section["anchor"],
                    section["heading"],
                    section["level"],
                    section["ordinal"],
                    section["content_text"],
                    section["char_count"],
                ),
            )
            row_id = cursor.lastrowid
            assert row_id is not None
            section_id_by_anchor[section["anchor"]] = row_id

        # Extract and insert code blocks
        code_blocks = extract_code_blocks(body_html)
        for block in code_blocks:
            section_id = section_id_by_anchor.get(block["section_anchor"])
            if section_id is None:
                # Fall back to first section if anchor not found
                if section_id_by_anchor:
                    section_id = next(iter(section_id_by_anchor.values()))
                else:
                    continue  # No sections to attach to

            conn.execute(
                "INSERT INTO examples "
                "(section_id, code, language, is_doctest, ordinal) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    section_id,
                    block["code"],
                    block["language"],
                    block["is_doctest"],
                    block["ordinal"],
                ),
            )

        conn.commit()
        return True

    except Exception as e:
        logger.warning("Skipping %s: %s", filepath, e)
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def ingest_sphinx_json_dir(
    conn: sqlite3.Connection,
    json_dir: Path,
    doc_set_id: int,
) -> tuple[int, int]:
    """Walk a Sphinx JSON output directory and ingest all .fjson files.

    Skips non-documentation files (globalcontext.json, searchindex.json, etc.).
    Reports progress every 100 files.

    Args:
        conn: Read-write SQLite connection.
        json_dir: Path to the Sphinx JSON output directory.
        doc_set_id: Foreign key into doc_sets.

    Returns:
        Tuple of (success_count, failure_count).
    """
    _ensure_build_deps()
    success = 0
    failures = 0
    total = 0

    fjson_files = sorted(json_dir.rglob("*.fjson"))

    for filepath in fjson_files:
        # Skip non-documentation files
        if filepath.name in _SKIP_FILES:
            continue

        total += 1
        if ingest_fjson_file(conn, filepath, doc_set_id):
            success += 1
        else:
            failures += 1

        if total % 100 == 0:
            logger.info("Processed %d documents (%d ok, %d failed)...", total, success, failures)

    logger.info(
        "Ingestion complete: %d documents (%d ok, %d failed)",
        total,
        success,
        failures,
    )
    return success, failures


def populate_synonyms(conn: sqlite3.Connection) -> int:
    """Populate the synonyms table from the packaged synonyms.yaml (INGR-C-09).

    Reads ``data/synonyms.yaml`` via ``importlib.resources`` and inserts
    each concept-expansion pair into the ``synonyms`` table.

    Args:
        conn: Read-write SQLite connection.

    Returns:
        Number of synonym entries inserted.
    """
    ref = importlib.resources.files("mcp_server_python_docs") / "data" / "synonyms.yaml"
    with importlib.resources.as_file(ref) as path:
        data = yaml.safe_load(path.read_text())

    if not isinstance(data, dict):
        from mcp_server_python_docs.errors import IngestionError

        raise IngestionError(
            f"synonyms.yaml must be a YAML mapping, got {type(data).__name__}"
        )

    count = 0
    for concept, expansion in data.items():
        if isinstance(expansion, list):
            expansion_str = " ".join(str(e) for e in expansion)
        else:
            expansion_str = str(expansion)

        conn.execute(
            "INSERT OR REPLACE INTO synonyms (concept, expansion) VALUES (?, ?)",
            (concept, expansion_str),
        )
        count += 1

    conn.commit()
    logger.info("Populated %d synonym entries", count)
    return count


def rebuild_fts_indexes(conn: sqlite3.Connection) -> None:
    """Rebuild sections_fts and examples_fts indexes after content ingestion (INGR-C-08).

    Uses the FTS5 'rebuild' command to re-read all content from the
    canonical tables and repopulate the FTS indexes atomically.

    Note: symbols_fts is rebuilt by inventory.py, not here.

    Args:
        conn: Read-write SQLite connection.
    """
    conn.execute("INSERT INTO sections_fts(sections_fts) VALUES('rebuild')")
    conn.execute("INSERT INTO examples_fts(examples_fts) VALUES('rebuild')")
    conn.commit()
    logger.info("Rebuilt sections_fts and examples_fts indexes")
