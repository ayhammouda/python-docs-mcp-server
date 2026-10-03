"""Offline, version-scoped discovery of sections on the official What's New page."""
from __future__ import annotations

import sqlite3

from mcp_server_python_docs.errors import PageNotFoundError
from mcp_server_python_docs.models import WhatsNewKind, WhatsNewResult, WhatsNewSection
from mcp_server_python_docs.retrieval.budget import apply_budget
from mcp_server_python_docs.services.version_resolution import validate_version

# Cap the whole structured response, including metadata and continuation hints.
_RESULT_CHARS = 20_000
_BODY_CHARS = 7800


def _classify(headings: tuple[str, ...]) -> WhatsNewKind:
    """Classify only when an explicit heading or its ancestry supports it.

    A leaf such as ``asyncio`` or an opaque ``#id6`` cannot classify itself.
    Nearest relevant ancestor wins, so a future pending removal inside a
    deprecations chapter remains a deprecation, not a removal in this release.
    """
    for heading in reversed(headings):
        title = heading.casefold().strip()
        if title.startswith("pending removal"):
            return "deprecation"
        if title in {"deprecated", "new deprecations", "deprecated c apis"}:
            return "deprecation"
        if title in {"removed", "removed modules and apis", "removed c apis"}:
            return "removal"
        if title in {"new modules"}:
            return "new_module"
        if title in {"optimizations", "performance"}:
            return "performance"
        if "syntax" in title or title == "syntax changes":
            return "syntax"
        if title in {"new features", "new features related to type hints", "improved modules"}:
            return "new_feature"
    return "other"


class WhatsNewService:
    """Read the already-indexed release page; never fetch at query time."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def get(
        self,
        version: str,
        kind: WhatsNewKind | None = None,
        start_index: int = 0,
        max_sections: int = 20,
    ) -> WhatsNewResult:
        validate_version(self._db, version)
        if start_index < 0 or not 1 <= max_sections <= 20:
            raise ValueError("start_index must be nonnegative and max_sections must be 1..20")
        slug = f"whatsnew/{version}"
        document = self._db.execute(
            "SELECT d.id FROM documents d JOIN doc_sets ds ON ds.id = d.doc_set_id "
            "WHERE ds.version = ? AND ds.source = 'python-docs' "
            "AND ds.language = 'en' AND d.slug = ?",
            (version, slug),
        ).fetchone()
        if document is None:
            raise PageNotFoundError(
                f"official What's New page {slug!r} is not indexed for Python {version}; "
                "rebuild the full documentation index (without --skip-content)"
            )

        rows = self._db.execute(
            "SELECT anchor, heading, level, content_text FROM sections "
            "WHERE document_id = ? ORDER BY ordinal, id",
            (document["id"],),
        ).fetchall()
        ancestors: list[tuple[int, str]] = []
        matches: list[tuple[sqlite3.Row, WhatsNewKind]] = []
        for row in rows:
            level = max(1, int(row["level"]))
            # Sphinx levels may skip; compare actual levels, not stack depth.
            while ancestors and ancestors[-1][0] >= level:
                ancestors.pop()
            ancestors.append((level, row["heading"]))
            body = row["content_text"].strip()
            if not body:
                continue
            category = _classify(tuple(title for _, title in ancestors))
            if kind is not None and category != kind:
                continue
            matches.append((row, category))
        page = matches[start_index : start_index + max_sections]
        next_index = start_index + len(page)
        sections = [
            WhatsNewSection(title=row["heading"], anchor=row["anchor"], body="", kind=category)
            for row, category in page
        ]
        result = WhatsNewResult(
            sections=sections,
            next_start_index=next_index if next_index < len(matches) else None,
        )
        remaining = _RESULT_CHARS - len(result.model_dump_json())
        for index, (row, _) in enumerate(page):
            section = sections[index]
            body = row["content_text"].strip()
            hint = (
                "\n\n[Section truncated. Continue with "
                f"get_docs(slug={slug!r}, version={version!r}, "
                f"anchor={row['anchor']!r}).]"
            )
            share = remaining // (len(page) - index)
            empty_length = len(section.model_dump_json())
            # Binary-search the excerpt so escaped JSON plus the follow-up hint
            # fits a fair share. Short sections leave more space for later ones.
            low, high = 0, min(_BODY_CHARS, len(body))
            chosen = ""
            while low <= high:
                middle = (low + high) // 2
                excerpt, truncated, _ = apply_budget(body, middle)
                candidate = excerpt + hint if truncated else excerpt
                cost = (
                    len(section.model_copy(update={"body": candidate}).model_dump_json())
                    - empty_length
                )
                if cost <= share:
                    chosen = candidate
                    low = middle + 1
                else:
                    high = middle - 1
            section.body = chosen
            remaining -= len(section.model_dump_json()) - empty_length
        return result
