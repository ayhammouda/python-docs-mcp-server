"""Controlled package docs lookup using official PyPI JSON metadata.

Source: https://docs.pypi.org/api/json/ documents GET /pypi/<project>/json.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from mcp_server_python_docs.models import (
    PackageDocsResult,
    PackageDocsSource,
    PackageKind,
)
from mcp_server_python_docs.services.observability import log_tool_call

_ALLOWED = {
    "documentation",
    "docs",
    "homepage",
    "home page",
    "source",
    "source code",
    "repository",
    "repo",
}
_PYPI_METADATA_MAX_BYTES = 5 * 1024 * 1024


class _HTTPResponse(Protocol):
    def read(self, size: int = -1) -> bytes: ...
    def __enter__(self) -> "_HTTPResponse": ...
    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool | None: ...


Fetcher = Callable[[str, float], _HTTPResponse]


def _default_fetcher(url: str, timeout: float) -> _HTTPResponse:
    req = Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "mcp-server-python-docs"},
    )
    return urlopen(req, timeout=timeout)


def _normalize(name: str) -> str:
    return quote(re.sub(r"[-_.]+", "-", name.strip().lower()), safe="-")


def _http_url(url: object) -> str | None:
    if not isinstance(url, str):
        return None
    parsed = urlparse(url.strip())
    return url.strip() if parsed.scheme in {"http", "https"} and parsed.netloc else None


def _source(label: str, url: object, kind: PackageKind) -> PackageDocsSource | None:
    valid = _http_url(url)
    if valid is None:
        return None
    return PackageDocsSource(label=label, url=valid, kind=kind, declared_by="PyPI project metadata")


def _read_limited(response: _HTTPResponse) -> bytes | None:
    data = response.read(_PYPI_METADATA_MAX_BYTES + 1)
    if len(data) > _PYPI_METADATA_MAX_BYTES:
        return None
    return data


def _empty_result(package: str, metadata_source: str, note: str) -> PackageDocsResult:
    return PackageDocsResult(
        package=package,
        version="",
        metadata_source=metadata_source,
        sources=[],
        note=note,
    )


class PackageDocsService:
    """Return package-declared docs/homepage/source URLs from PyPI metadata only."""

    def __init__(self, fetcher: Fetcher = _default_fetcher, timeout: float = 10.0) -> None:
        self._fetcher = fetcher
        self._timeout = timeout

    @log_tool_call("lookup_package_docs")
    def lookup(self, package: str) -> PackageDocsResult:
        project = _normalize(package)
        metadata_source = f"https://pypi.org/pypi/{project}/json"
        try:
            with self._fetcher(metadata_source, self._timeout) as response:
                data = _read_limited(response)
                if data is None:
                    return _empty_result(
                        package, metadata_source, "PyPI metadata exceeded size limit."
                    )
                payload = json.loads(data.decode("utf-8"))
        except HTTPError as e:
            note = (
                "Package not found on PyPI." if e.code == 404 else f"PyPI returned HTTP {e.code}."
            )
            return _empty_result(package, metadata_source, note)
        except (URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as e:
            return _empty_result(
                package,
                metadata_source,
                f"Unable to retrieve PyPI metadata: {type(e).__name__}.",
            )

        info = payload.get("info") if isinstance(payload, dict) else {}
        info = info if isinstance(info, dict) else {}
        sources = [
            s
            for s in (
                _source(
                    "PyPI project",
                    info.get("project_url") or f"https://pypi.org/project/{project}/",
                    "pypi",
                ),
                _source("Documentation", info.get("docs_url"), "docs"),
                _source("Homepage", info.get("home_page"), "homepage"),
            )
            if s is not None
        ]
        skipped: list[str] = []
        project_urls = info.get("project_urls")
        if isinstance(project_urls, dict):
            for label, url in project_urls.items():
                lowered = str(label).strip().lower()
                if lowered in _ALLOWED:
                    # Runtime-safe: members of `_ALLOWED` (with spaces → underscores)
                    # are exactly the non-pypi entries in the PackageKind Literal.
                    derived_kind = cast(PackageKind, lowered.replace(" ", "_"))
                    found = _source(str(label), url, derived_kind)
                    if found is not None and found not in sources:
                        sources.append(found)
                else:
                    skipped.append(str(label))
        note = None
        if skipped:
            note = (
                "Ignored project URL labels outside the controlled allowlist: "
                f"{', '.join(sorted(skipped))}."
            )
        return PackageDocsResult(
            package=str(info.get("name") or package),
            version=str(info.get("version") or ""),
            summary=str(info.get("summary") or ""),
            metadata_source=metadata_source,
            sources=sources,
            note=note,
        )
