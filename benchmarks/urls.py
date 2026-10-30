"""Parse URLs and labeled values out of tool or answer text.

Shared by the competitor adapters and, later, the agentic harness's grounding
step. Stdlib ``re`` only.

URL rules:

- RFC 3986 appendix C: delimiters around a URI (whitespace, ``<>``, quotes)
  are not part of it. Square brackets are legal only around an IP-literal
  host, so a URL here ends at ``[`` or ``]``.
- GFM extended autolinks: a trailing ``?!.,:*_~`` is dropped, and a trailing
  ``)`` is dropped only while closing parentheses outnumber opening ones.
  ``)`` is a legal RFC 3986 sub-delimiter, so it is never dropped blindly.
"""

from __future__ import annotations

import re

_URL_RE = re.compile(r"https?://[^\s<>\"'`{}|\\^\[\]]+")
_GFM_TRAILING = "?!.,:*_~"


def _trim(url: str) -> str:
    while True:
        if url[-1] in _GFM_TRAILING:
            url = url[:-1]
        elif url[-1] == ")" and url.count(")") > url.count("("):
            url = url[:-1]
        else:
            return url


def extract_urls(text: str) -> list[str]:
    """Return every ``http(s)://`` URL in ``text``, in text order, trimmed."""
    return [_trim(match.group(0)) for match in _URL_RE.finditer(text)]


def first_web_url(text: str) -> str | None:
    """Return the first ``http(s)://`` URL in ``text``, or ``None``."""
    urls = extract_urls(text)
    return urls[0] if urls else None


def labeled_value(text: str, label: str) -> str | None:
    """Return the value of the first ``<label>: <value>`` line, or ``None``.

    A line matches only as a whole (trailing whitespace and ``\\r`` aside), so
    the label must start the line and the value is one non-space token.
    """
    pattern = re.compile(re.escape(label) + r": (\S+)")
    for line in text.splitlines():
        match = pattern.fullmatch(line.rstrip())
        if match:
            return match.group(1)
    return None
