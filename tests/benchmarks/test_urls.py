"""Tests for ``benchmarks.urls``: URLs and labeled values in tool text."""

from __future__ import annotations

import pytest

from benchmarks.urls import extract_urls, first_web_url, labeled_value

ITERTOOLS = "https://docs.python.org/3/library/itertools.html#itertools.pairwise"
README = "https://github.com/python/cpython/blob/main/README.rst"
OS = "https://docs.python.org/3/library/os.html"
RE = "https://docs.python.org/3/library/re.html"
WIKIPEDIA = "https://en.wikipedia.org/wiki/Python_(programming_language)"
INIT = "https://docs.python.org/3/reference/datamodel.html#object.__init__"

#: Context7 3.2.3 ``resolve-library-id`` text when team library filters apply:
#: the policy note comes first, then the labeled result lines.
CONTEXT7_WITH_FILTER_NOTE = (
    "Note: results are filtered by your team's library policies. "
    "Manage them at https://context7.com/dashboard?tab=policies\n"
    "\n"
    "Available Libraries:\n"
    "- Title: Python\n"
    "- Context7-compatible library ID: /python/cpython\n"
    "- Description: ...\n"
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(f"[pairwise]({ITERTOOLS}).", ITERTOOLS, id="markdown-link-then-period"),
        pytest.param(f"#### (README.rst)[{README}] (Score: 0.53)", README, id="gitmcp-doc-search"),
        pytest.param(f"<{OS}>,", OS, id="angle-autolink-then-comma"),
        pytest.param(f"**{RE}**", RE, id="bold-markers"),
        pytest.param(WIKIPEDIA, WIKIPEDIA, id="balanced-parenthesis-kept"),
    ],
)
def test_first_web_url_drops_delimiters_but_keeps_balanced_parentheses(
    text: str, expected: str
) -> None:
    assert first_web_url(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(f"[constructor]({INIT})", id="markdown-link"),
        pytest.param(f"<{INIT}>", id="angle-autolink"),
        pytest.param(f"#### (datamodel.rst)[{INIT}] (Score: 1)", id="gitmcp-doc-search"),
        pytest.param(f"see {INIT}.", id="bare-then-period"),
    ],
)
def test_first_web_url_keeps_a_python_dunder_anchor(text: str) -> None:
    assert first_web_url(text) == INIT


def test_labeled_value_finds_the_context7_id_behind_a_filter_note() -> None:
    assert (
        labeled_value(CONTEXT7_WITH_FILTER_NOTE, "- Context7-compatible library ID")
        == "/python/cpython"
    )


def test_labeled_value_reads_the_url_line_not_the_overview() -> None:
    text = f"overview: see https://example.org/blog.\nurl: {OS}  \r\nmoduleId: py\n"

    assert labeled_value(text, "url") == OS
    assert labeled_value(text, "title") is None


def test_extract_urls_returns_bare_urls_in_text_order() -> None:
    assert extract_urls(f"see {OS}?q=walk! and <{RE}>") == [f"{OS}?q=walk", RE]
