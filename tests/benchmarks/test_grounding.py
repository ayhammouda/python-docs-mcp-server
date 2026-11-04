"""Design §9.2 grounding: a cited page must match a page the trajectory retrieved (PR 5 Task 6)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from benchmarks.grading.grounding import (
    PageRef,
    answer_citations,
    is_grounded,
    matches,
    retrieved_pages,
)


def _call(tool: str, result: Any, *, is_error: bool = False, **arguments: Any) -> dict[str, Any]:
    text = result if isinstance(result, str) else json.dumps(result)
    return {"tool": tool, "arguments": arguments, "result": text, "is_error": is_error}


GET_DOCS = _call(
    "get_docs",
    {
        "content": "json.dumps(obj) serializes obj.",
        "slug": "library/json.html",
        "title": "json.dumps",
        "version": "3.13",
        "anchor": "json.dumps",
        "char_count": 30,
        "truncated": False,
    },
    slug="library/json.html",
    anchor="json.dumps",
    version="3.13",
)
SEARCH = _call(
    "search_docs",
    {
        "hits": [
            {
                "uri": "library/itertools.html#itertools.batched",
                "title": "itertools.batched",
                "kind": "function",
                "snippet": "Batch data from the iterable.",
                "version": "3.12",
                "slug": "library/itertools.html",
                "anchor": "itertools.batched",
            },
            {
                "uri": "library/glob.html#glob.translate",
                "title": "glob.translate",
                "kind": "function",
                "snippet": "",
                "version": "3.13",
                "slug": "library/glob.html",
                "anchor": "glob.translate",
            },
        ],
        "note": None,
    },
    query="batched",
)
WHATSNEW = _call(
    "whatsnew_for_version",
    {"sections": [{"heading": "copy", "body": "Add copy.replace()."}], "next_start_index": None},
    version="3.13",
)


@pytest.mark.parametrize(
    "text,ref",
    [
        (
            "https://docs.python.org/3.13/library/json.html#json.dumps.",
            PageRef("3.13", "library/json.html", "json.dumps"),
        ),
        ("https://docs.python.org/3/library/json.html", PageRef(None, "library/json.html", None)),
        ("see library/json.html", PageRef(None, "library/json.html", None)),
        ("see 3.12/library/json.html.", PageRef("3.12", "library/json.html", None)),
    ],
)
def test_answer_citations_normalize_urls_and_paths(text: str, ref: PageRef) -> None:
    assert answer_citations(text) == [ref]


def test_answer_citations_ignore_other_hosts() -> None:
    assert answer_citations("See https://example.com/library/json.html and nothing else.") == []


@pytest.mark.parametrize(
    "citation,page",
    [
        ("/3.13/library/json.html#json.dumps", "library/json.html#json.dumps"),
        ("glossary.html#term-iterator", "glossary.html#term-iterator"),
        ("/glossary.html#term-iterator", "glossary.html#term-iterator"),
        ("3/glossary.html#term-iterator", "glossary.html#term-iterator"),
        ("./glossary.html#term-iterator", "glossary.html#term-iterator"),
        ("library/concurrent.futures", "library/concurrent.futures.html"),
        (
            "../library/itertools.html#itertools.accumulate",
            "library/itertools.html#itertools.accumulate",
        ),
        (
            "/3/library/itertools.html#itertools.accumulate",
            "library/itertools.html#itertools.accumulate",
        ),
        (
            "https://docs.python.org/3.13/library/json.html#json.dumps",
            "library/json.html#json.dumps",
        ),
    ],
)
def test_docs_path_grounds_against_the_canonical_fetch(citation: str, page: str) -> None:
    fetch = _call("fetch", "Page text", url=f"https://docs.python.org/3.13/{page}")
    assert is_grounded(f"See ({citation}).", [fetch])
    assert not is_grounded(f"See ({citation}).", [{**fetch, "is_error": True}])


@pytest.mark.parametrize(
    "citation",
    [
        "/3.12/library/json.html#json.dumps",
        "/3.13/library/json.html#json.loads",
        "ftp://docs.python.org/3.13/library/json.html#json.dumps",
        "mailto:library/json.html#json.dumps",
        "examples/library/json.html#json.dumps",
    ],
)
def test_nonmatching_paths_do_not_ground_against_json(citation: str) -> None:
    assert not is_grounded(f"See {citation}.", [GET_DOCS])


def test_retrieved_pages_from_python_docs_results() -> None:
    assert retrieved_pages([GET_DOCS, SEARCH, WHATSNEW]) == [
        PageRef("3.13", "library/json.html", "json.dumps"),
        PageRef("3.12", "library/itertools.html", "itertools.batched"),
        PageRef("3.13", "whatsnew/3.13.html", None),
    ]
    page = _call("get_docs", {"content": "x", "slug": "library/json", "version": "3.13"})
    assert retrieved_pages([page]) == [PageRef("3.13", "library/json.html", None)]
    fetch = _call("fetch", "Page text", url="https://docs.python.org/3.13/library/csv.html#x")
    assert retrieved_pages([fetch]) == [PageRef("3.13", "library/csv.html", "x")]


def test_retrieved_pages_skip_errors_and_empty_text() -> None:
    errored = {**GET_DOCS, "is_error": True}
    empty_doc = _call("get_docs", {"content": "", "slug": "library/json.html", "version": "3.13"})
    empty_fetch = _call("fetch", "", url="https://docs.python.org/3.13/library/csv.html")
    failed_fetch = _call(
        "fetch", "404 Not Found", is_error=True, url="https://docs.python.org/3.13/library/csv.html"
    )
    empty_news = _call(
        "whatsnew_for_version", {"sections": [{"heading": "h", "body": ""}]}, version="3.13"
    )
    not_json = _call("get_docs", "plain text with library/json.html in it")
    assert (
        retrieved_pages([errored, empty_doc, empty_fetch, failed_fetch, empty_news, not_json]) == []
    )


def test_match_rules_for_version_and_anchor() -> None:
    page = PageRef("3.13", "library/json.html", "json.dumps")
    assert matches(page, page)
    assert matches(PageRef(None, "library/json.html", None), page)
    assert matches(PageRef("3.13", "library/json.html", None), page)
    assert not matches(PageRef("3.12", "library/json.html", None), page)
    assert not matches(PageRef("3.13", "library/json.html", "json.loads"), page)
    assert not matches(PageRef("3.13", "library/csv.html", None), page)
    assert matches(
        PageRef("3.13", "library/json.html", "x"), PageRef(None, "library/json.html", None)
    )


def test_an_answer_citing_a_page_whose_fetch_errored_is_ungrounded() -> None:
    answer = "Use csv.DictReader; see https://docs.python.org/3.13/library/csv.html."
    failed = _call(
        "fetch",
        "connection refused",
        is_error=True,
        url="https://docs.python.org/3.13/library/csv.html",
    )
    assert not is_grounded(answer, [failed])
    ok = _call("fetch", "csv text", url="https://docs.python.org/3.13/library/csv.html")
    assert is_grounded(answer, [failed, ok])
    assert not is_grounded("No citation at all.", [ok])
    assert is_grounded("See library/json.html#json.dumps.", [GET_DOCS])
