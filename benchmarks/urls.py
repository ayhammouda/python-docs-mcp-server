"""Parse URLs and labeled values out of tool or answer text.

Shared by the competitor adapters and, later, the agentic harness's grounding
step. Stdlib ``re`` only.

URL rules:

- RFC 3986 appendix C: delimiters around a URI (whitespace, ``<>``, quotes)
  are not part of it. Square brackets are legal only around an IP-literal
  host, so a URL here ends at ``[`` or ``]``.
- GFM extended autolinks: a trailing ``?!.,:*~`` is dropped, and a trailing
  ``)`` is dropped only while closing parentheses outnumber opening ones.
  ``)`` is a legal RFC 3986 sub-delimiter, so it is never dropped blindly.
- Unlike GFM, a trailing ``_`` is kept on purpose: docs.python.org anchors
  end in ``__`` (e.g. ``#object.__init__``).
"""

from __future__ import annotations

import re

_URL_RE = re.compile(r"https?://[^\s<>\"'`{}|\\^\[\]]+")
_GFM_TRAILING = "?!.,:*~"

# Match a standalone docs path, never a suffix of a host, address or local path.
# Capture the corpus-relative path so masking and grounding consume the same span.
_DOC_SECTIONS = (
    "library|reference|tutorial|howto|whatsnew|using|c-api|faq|glossary|extending|"
    "install|installing|deprecations|distributing|distutils"
)
_TOP_LEVEL = (
    "glossary|index|genindex(?:-[A-Z_]|-Symbols|-all)?|py-modindex|contents|search|about|bugs|"
    "copyright|license|download|improve-page(?:-nojs)?"
)
# Dotted library slugs in the local docs index. Without .html, these would
# otherwise be indistinguishable from filenames such as library/example.py.
_DOTTED_LIBRARY_PAGES = """
    collections.abc compression.zstd concurrent.futures concurrent.interpreters curses.ascii
    curses.panel email.charset email.compat32-message email.contentmanager email.encoders
    email.errors email.examples email.generator email.header email.headerregistry email.iterators
    email.message email.mime email.parser email.policy email.utils html.entities html.parser
    http.client http.cookiejar http.cookies http.server importlib.metadata importlib.resources
    importlib.resources.abc logging.config logging.handlers multiprocessing.shared_memory os.path
    string.templatelib sys.monitoring tkinter.colorchooser tkinter.dnd tkinter.font
    tkinter.messagebox tkinter.scrolledtext tkinter.tix tkinter.ttk unittest.mock
    unittest.mock-examples urllib.error
    urllib.parse urllib.request urllib.robotparser xml.dom xml.dom.minidom xml.dom.pulldom
    xml.etree.elementtree xml.sax xml.sax.handler xml.sax.reader xml.sax.utils xmlrpc.client
    xmlrpc.server
""".split()
_DOTTED_LIBRARY_PATTERN = "|".join(map(re.escape, _DOTTED_LIBRARY_PAGES))
DOCS_PATH_RE = re.compile(
    r"""(?<![^\s(\[<"'`*])(?:/|(?:\.\.?/)+)?"""
    r"(?P<path>(?:(?:\d\.\d+|3)/)?"
    rf"(?:(?:{_DOC_SECTIONS})/(?:[\w-]+/)*(?:[\w.-]+\.html|[\w-]+)"
    rf"|library/(?:{_DOTTED_LIBRARY_PATTERN})|whatsnew/\d\.\d+"
    rf"|deprecations/(?:c-api-)?pending-removal-in-\d\.\d+|(?:{_TOP_LEVEL})\.html))"
    r"(?:#(?P<anchor>[\w.:-]*[\w-]))?"
    r"""(?=[?!.,:;*~]*(?:$|[\s)\]>"'`]))"""
)


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
