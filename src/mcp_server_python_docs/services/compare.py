"""Version-diff service for the compare_versions MCP tool (CMPR-01/02/03).

Phase 09. Implements ``CompareService.compare(symbol, v1, v2)`` returning a
``CompareVersionsResult``: the four diff branches (added / removed / changed /
unchanged), the version/symbol error paths, and the see-also / deprecation /
signature-delta heuristics.

No structured signature/deprecation/see-also metadata is stored in the index,
so this service derives those signals from section *text* using the regex
literals locked by the Plan 09-01 data-shape spike, plus symbol presence/absence
across the two doc_sets (via ``create_symbol_cache``).

Algorithm ordering is FIXED per cross-AI review H2:
  1. validate BOTH versions (validate_version raises the unknown-version error
     from version_resolution; compare.py never names that exception type — H4)
  2. resolve the symbol in BOTH versions
  3. if missing in BOTH -> SymbolNotFoundError
  4. ONLY THEN handle the v1 == v2 identical-versions case as 'unchanged'

This ordering ensures ``compare('does.not.exist', '3.11', '3.11')`` raises
SymbolNotFoundError rather than falsely returning 'unchanged'.
"""
from __future__ import annotations

import difflib
import re
import sqlite3
from typing import TYPE_CHECKING

from mcp_server_python_docs.errors import PageNotFoundError, SymbolNotFoundError
from mcp_server_python_docs.models import CompareVersionsResult
from mcp_server_python_docs.services.cache import create_symbol_cache
from mcp_server_python_docs.services.observability import log_tool_call
from mcp_server_python_docs.services.version_resolution import validate_version

if TYPE_CHECKING:
    from mcp_server_python_docs.services.content import ContentService

# --- Locked extractor regexes (verbatim from 09-01-data-shape-spike-SUMMARY) ---
# All four HOLD against the spike fixture (A1/A2/sibling probes). Scalar
# extractors return None on no-match; _extract_see_also returns [].
_NEW_IN_RE = re.compile(r"New in version\s+(\d+\.\d+)")
_CHANGED_IN_RE = re.compile(r"Changed in version\s+(\d+\.\d+)")
_DEPRECATED_IN_RE = re.compile(r"Deprecated since version\s+(\d+\.\d+)")
# Markdown link label extractor; MUST be applied only within a "See also" window
# (locate case-insensitive "see also", read forward to next ATX heading / window
# end), not against the whole section, or it captures unrelated body links.
_SEE_ALSO_LINK_RE = re.compile(r"\[([^\]]+)\]\(")

# M2 note text emitted when a docs page is unfetchable in the both-present branch.
_PAGE_UNAVAILABLE_NOTE = "docs page not available for one or both versions"

# section_diff truncation ceiling (token-frugality, CMPR-03).
_SECTION_DIFF_MAX_CHARS = 600
# signature_delta heuristic: first-line snippet length cap.
_SIGNATURE_LINE_MAX = 80


def _extract_version(pattern: re.Pattern[str], text: str) -> str | None:
    """Return the captured version from the first match of ``pattern``, or None.

    Shared by the New-in / Changed-in / Deprecated-since extractors, which differ
    only by their (spike-locked) pattern literal (``_NEW_IN_RE`` etc.).
    """
    match = pattern.search(text)
    return match.group(1) if match else None


def _extract_see_also(text: str) -> list[str]:
    """Extract see-also link labels from the 'See also' window only.

    Locates the first case-insensitive 'see also' line, then reads forward to the
    next ATX heading ('#') or up to 20 lines, and matches markdown link labels in
    that window. Returns [] when there is no 'See also' section (fallback policy).
    """
    lines = text.splitlines()
    start = None
    for idx, line in enumerate(lines):
        if "see also" in line.lower():
            start = idx
            break
    if start is None:
        return []

    # WR-01: a Sphinx "See also" admonition is one contiguous block. markdownify
    # rarely emits an ATX heading after it, so an ATX-only break over-captures
    # unrelated body links. Skip leading blanks, then read the block until the
    # first blank line AFTER content has started (or an ATX heading, or the cap).
    window: list[str] = []
    started = False
    for line in lines[start + 1 : start + 1 + 20]:
        if line.lstrip().startswith("#"):
            break
        if line.strip():
            started = True
            window.append(line)
        elif started:
            break  # blank line ends the admonition block

    return _SEE_ALSO_LINK_RE.findall("\n".join(window))


def _first_nonempty_line(text: str) -> str:
    """Return the first non-blank line of a section text, or '' if none."""
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


class CompareService:
    """Compute a structured version diff for a stdlib symbol (CMPR-01).

    Composes existing primitives: ``validate_version`` (version existence +
    actionable error), ``create_symbol_cache`` (version-scoped symbol
    resolution), and ``ContentService.get_docs`` (section text retrieval).
    The method is intentionally SYNC — SQLite reads on the open connection are
    non-blocking; no asyncio.to_thread is used.
    """

    def __init__(
        self,
        db: sqlite3.Connection,
        content_service: ContentService,
    ) -> None:
        self._db = db
        self._content = content_service
        # Private per-service LRU symbol resolver (own cache, RESEARCH §Q7).
        self._resolve = create_symbol_cache(db)

    def _section_text(self, uri: str, anchor: str, version: str) -> str:
        """Fetch the section text for a symbol via ContentService.

        Derives the slug from the symbol URI (everything before '#') and uses the
        symbol anchor — never joins symbols.section_id directly (RESEARCH Pitfall 2).

        CR-01: symbol URIs carry ".html" (``library/json.html#...``) but real
        Sphinx ingestion stores ``documents.slug`` EXTENSIONLESS
        (``library/json``), and ``get_docs`` matches ``documents.slug`` exactly.
        Try the extensionless form first, then the raw ".html" page, mirroring
        ``retrieval.ranker._document_candidates`` so resolution holds on a real
        index. Raises PageNotFoundError only when every candidate misses.
        """
        page = uri.split("#", 1)[0]
        candidates = (page[:-5], page) if page.endswith(".html") else (page,)
        last_exc: PageNotFoundError | None = None
        for slug in candidates:
            try:
                result = self._content.get_docs(
                    slug=slug, version=version, anchor=anchor
                )
            except PageNotFoundError as exc:
                last_exc = exc
                continue
            return result.content
        # candidates is non-empty, so last_exc is always set when we reach here.
        assert last_exc is not None
        raise last_exc

    @log_tool_call("compare_versions")
    def compare(self, symbol: str, v1: str, v2: str) -> CompareVersionsResult:
        """Compare a symbol's documentation between two Python versions.

        See module docstring for the FIXED branch ordering (H2). Returns one of
        the four ``change`` cases; validate_version raises the unknown-version
        error from its own module, or raises SymbolNotFoundError when the symbol
        is absent in both versions.
        """
        # 1. Validate BOTH versions first. validate_version raises the
        #    unknown-version error (with the indexed-version list) from its own
        #    module — compare.py never references that exception type (H4).
        validate_version(self._db, v1)
        validate_version(self._db, v2)

        # 2. Resolve the symbol in BOTH versions.
        sym_v1 = self._resolve(symbol, v1)
        sym_v2 = self._resolve(symbol, v2)

        # 3. Missing in BOTH versions -> SymbolNotFoundError. This fires BEFORE
        #    the v1 == v2 check so identical-versions with a non-existent symbol
        #    raises rather than returning 'unchanged' (H2 fix).
        if sym_v1 is None and sym_v2 is None:
            raise SymbolNotFoundError(
                f"symbol {symbol!r} not found in v{v1} or v{v2}"
            )

        # 4. Identical versions, symbol known to exist -> 'unchanged'.
        if v1 == v2:
            return CompareVersionsResult(
                symbol=symbol, v1=v1, v2=v2, change="unchanged"
            )

        # 5. Branch on symbol presence across the two versions.
        if sym_v1 is None and sym_v2 is not None:
            # Added in v2. Best-effort new_in extraction; swallow
            # PageNotFoundError (structural 'added' is sound without the section).
            new_in: str | None = None
            try:
                text_v2 = self._section_text(sym_v2.uri, sym_v2.anchor, v2)
                new_in = _extract_version(_NEW_IN_RE, text_v2)
            except PageNotFoundError:
                new_in = None
            return CompareVersionsResult(
                symbol=symbol, v1=v1, v2=v2, change="added", new_in=new_in
            )

        if sym_v1 is not None and sym_v2 is None:
            # Removed in v2 — no section fetch needed.
            return CompareVersionsResult(
                symbol=symbol, v1=v1, v2=v2, change="removed", removed_in=v2
            )

        # Both present. Narrow for the type checker.
        assert sym_v1 is not None and sym_v2 is not None

        # M2: if either section is unfetchable, report 'changed' + note rather
        # than the previous false-negative 'unchanged'.
        try:
            text_v1 = self._section_text(sym_v1.uri, sym_v1.anchor, v1)
            text_v2 = self._section_text(sym_v2.uri, sym_v2.anchor, v2)
        except PageNotFoundError:
            return CompareVersionsResult(
                symbol=symbol,
                v1=v1,
                v2=v2,
                change="changed",
                section_diff=None,
                note=_PAGE_UNAVAILABLE_NOTE,
            )

        changed_in = _extract_version(_CHANGED_IN_RE, text_v2)
        deprecated_in = _extract_version(_DEPRECATED_IN_RE, text_v2)

        # signature_delta (M1, advisory): first non-empty line comparison.
        first_v1 = _first_nonempty_line(text_v1)
        first_v2 = _first_nonempty_line(text_v2)
        signature_delta: str | None = None
        if first_v1 != first_v2:
            signature_delta = (
                f"line 1 differs (v{v1}: {first_v1[:_SIGNATURE_LINE_MAX]} "
                f"-> v{v2}: {first_v2[:_SIGNATURE_LINE_MAX]})"
            )

        # See-also delta (set difference of link labels in each "See also" window).
        see_v1 = set(_extract_see_also(text_v1))
        see_v2 = set(_extract_see_also(text_v2))
        see_also_added = sorted(see_v2 - see_v1)
        see_also_removed = sorted(see_v1 - see_v2)

        # Unified diff of the two section texts, truncated for token frugality.
        diff_lines = list(
            difflib.unified_diff(
                text_v1.splitlines(),
                text_v2.splitlines(),
                lineterm="",
                n=2,
            )
        )
        # WR-02: truncate on LINE boundaries (not mid-line) with an explicit
        # marker, so the emitted diff stays a parseable unified diff.
        section_diff: str | None = None
        if diff_lines:
            kept: list[str] = []
            used = 0
            truncated = False
            for line in diff_lines:
                projected = used + len(line) + (1 if kept else 0)
                if projected > _SECTION_DIFF_MAX_CHARS:
                    truncated = True
                    break
                kept.append(line)
                used = projected
            section_diff = "\n".join(kept)
            if truncated:
                marker = "... (diff truncated)"
                section_diff = f"{section_diff}\n{marker}" if section_diff else marker

        # If nothing changed at all, the symbol is genuinely 'unchanged'.
        has_delta = (
            section_diff is not None
            or changed_in is not None
            or deprecated_in is not None
            or signature_delta is not None
            or bool(see_also_added)
            or bool(see_also_removed)
        )
        if not has_delta:
            return CompareVersionsResult(
                symbol=symbol, v1=v1, v2=v2, change="unchanged"
            )

        return CompareVersionsResult(
            symbol=symbol,
            v1=v1,
            v2=v2,
            change="changed",
            changed_in=changed_in,
            deprecated_in=deprecated_in,
            signature_delta=signature_delta,
            see_also_added=see_also_added,
            see_also_removed=see_also_removed,
            section_diff=section_diff,
        )
