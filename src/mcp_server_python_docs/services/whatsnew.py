"""Offline, version-scoped discovery of sections on the official What's New page."""
from __future__ import annotations

import sqlite3

from mcp_server_python_docs.errors import DocsServerError, PageNotFoundError
from mcp_server_python_docs.models import WhatsNewKind, WhatsNewResult, WhatsNewSection
from mcp_server_python_docs.retrieval.budget import apply_budget
from mcp_server_python_docs.services.version_resolution import validate_version

# Cap the whole structured response, including metadata and continuation hints.
_RESULT_CHARS = 20_000
_BODY_CHARS = 7800


def _body_cost(section: WhatsNewSection, body: str) -> int:
    return (
        len(section.model_copy(update={"body": body}).model_dump_json())
        - len(section.model_copy(update={"body": ""}).model_dump_json())
    )


def _fit_body(section: WhatsNewSection, body: str, hint: str, share: int) -> str:
    # Full text has no hint: its cost is outside the monotonic truncated search.
    if len(body) <= _BODY_CHARS and _body_cost(section, body) <= share:
        return body
    low, high = 1, min(_BODY_CHARS, len(body) - 1)
    chosen = section.body
    while low <= high:
        middle = (low + high) // 2
        excerpt, truncated, _ = apply_budget(body, middle)
        candidate = excerpt + hint if truncated else excerpt
        if _body_cost(section, candidate) <= share:
            chosen = candidate
            low = middle + 1
        else:
            high = middle - 1
    return chosen


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
        # Reserve indexed text and any required hint before admitting each section.
        # Stop at the first non-fitting section; the next offset must never skip it.
        page: list[tuple[str, str]] = []
        sections: list[WhatsNewSection] = []
        minimum_costs: list[int] = []
        result = WhatsNewResult()
        for row, category in matches[start_index : start_index + max_sections]:
            section = WhatsNewSection(
                title=row["heading"], anchor=row["anchor"], body="", kind=category
            )
            next_index = start_index + len(sections) + 1
            candidate = WhatsNewResult(
                sections=[*sections, section],
                next_start_index=next_index if next_index < len(matches) else None,
            )
            if not sections and len(candidate.model_dump_json()) > _RESULT_CHARS:
                raise DocsServerError(
                    f"What's New section at start_index={start_index} has metadata "
                    "larger than the 20,000-character response limit"
                )
            body = row["content_text"].strip()
            hint = (
                "\n\n[Section truncated. Continue with "
                f"get_docs(slug={slug!r}, version={version!r}, "
                f"anchor={row['anchor']!r}).]"
            )
            excerpt, truncated, _ = apply_budget(body, 1)
            minimum = excerpt + hint if truncated else excerpt
            if len(body) <= _BODY_CHARS and (
                _body_cost(section, body) <= _body_cost(section, minimum)
            ):
                minimum = body
            section.body = minimum
            if len(candidate.model_dump_json()) > _RESULT_CHARS:
                if not sections:
                    raise DocsServerError(
                        f"What's New section at start_index={start_index} cannot fit "
                        "nonempty indexed text and its required continuation within "
                        "the 20,000-character response limit"
                    )
                break
            page.append((body, hint))
            minimum_costs.append(_body_cost(section, minimum))
            sections.append(section)
            result = candidate
        remaining = _RESULT_CHARS - len(result.model_dump_json()) + sum(minimum_costs)
        reserved = sum(minimum_costs)
        for index, (body, hint) in enumerate(page):
            section = sections[index]
            reserved -= minimum_costs[index]
            share = min(
                max(remaining // (len(page) - index), minimum_costs[index]),
                remaining - reserved,
            )
            section.body = _fit_body(section, body, hint, share)
            remaining -= _body_cost(section, section.body)
        return result
