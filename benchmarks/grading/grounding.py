"""Design §9.2 grounding: a page the answer cites matches a page the trajectory retrieved.

Grounding never changes a score; it sets ``grounded`` and ``correct_but_ungrounded``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from benchmarks.grading.inputs import _DOCS_PATH_RE
from benchmarks.urls import _URL_RE, extract_urls

_DOCS_HOST = "docs.python.org"
_VERSIONED = re.compile(r"(\d\.\d+|3)/(.+)")
_TEXT_FIELDS = ("content", "snippet", "body")


@dataclass(frozen=True)
class PageRef:
    version: str | None  # None: "/3/" or no version stated
    page: str  # e.g. "library/json.html"
    anchor: str | None


def _ref(path: str, anchor: str | None, version: str | None = None) -> PageRef:
    path = path.strip("/").rstrip(".")
    match = _VERSIONED.fullmatch(path)
    if match is not None:
        path = match.group(2)
        version = None if match.group(1) == "3" else match.group(1)
    if path and not path.endswith(".html"):
        path += ".html"
    return PageRef(version, path, (anchor or "").rstrip(".") or None)


def _url_ref(url: str) -> PageRef | None:
    parts = urlsplit(url)
    if parts.hostname != _DOCS_HOST:
        return None
    return _ref(parts.path, parts.fragment)


def answer_citations(answer: str) -> list[PageRef]:
    """Every docs.python.org URL and every relative docs path in the answer."""
    refs = [ref for url in extract_urls(answer) if (ref := _url_ref(url)) is not None]
    for match in _DOCS_PATH_RE.finditer(_URL_RE.sub(" ", answer)):
        path, _, anchor = match.group(0).partition("#")
        refs.append(_ref(path, anchor))
    return refs


def _objects(value: Any) -> Iterator[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _objects(child)


def _object_ref(obj: Mapping[str, Any]) -> PageRef | None:
    if not any(isinstance(obj.get(f), str) and obj[f] for f in _TEXT_FIELDS):
        return None
    version = obj.get("version") if isinstance(obj.get("version"), str) else None
    if isinstance(obj.get("uri"), str) and obj["uri"]:
        path, _, anchor = obj["uri"].partition("#")
        return _ref(path, anchor, version)
    if isinstance(obj.get("slug"), str) and obj["slug"]:
        anchor = obj.get("anchor") if isinstance(obj.get("anchor"), str) else None
        return _ref(obj["slug"], anchor, version)
    return None


def retrieved_pages(tool_calls: Sequence[Mapping[str, Any]]) -> list[PageRef]:
    """Pages that successful calls returned with non-empty documentation text."""
    refs: list[PageRef] = []
    for call in tool_calls:
        result = call.get("result")
        if call.get("is_error") or not isinstance(result, str) or not result.strip():
            continue
        arguments = call.get("arguments") or {}
        if call.get("tool") == "fetch":
            url = arguments.get("url")
            if isinstance(url, str) and (ref := _url_ref(url)) is not None:
                refs.append(ref)
            continue
        try:
            data = json.loads(result)
        except ValueError:
            continue
        if call.get("tool") == "whatsnew_for_version":
            version = arguments.get("version")
            sections = data.get("sections") if isinstance(data, dict) else None
            if isinstance(version, str) and any(
                isinstance(s, dict) and s.get("body") for s in sections or []
            ):
                refs.append(PageRef(version, f"whatsnew/{version}.html", None))
            continue
        refs += [ref for obj in _objects(data) if (ref := _object_ref(obj)) is not None]
    return refs


def matches(cited: PageRef, retrieved: PageRef) -> bool:
    if cited.page != retrieved.page:
        return False
    if cited.anchor and retrieved.anchor and cited.anchor != retrieved.anchor:
        return False
    return not (cited.version and retrieved.version and cited.version != retrieved.version)


def is_grounded(answer: str, tool_calls: Sequence[Mapping[str, Any]]) -> bool:
    pages = retrieved_pages(tool_calls)
    return any(matches(c, r) for c in answer_citations(answer) for r in pages)
