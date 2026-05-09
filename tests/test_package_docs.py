"""Controlled PyPI package documentation lookup tests."""
from __future__ import annotations

import json
from urllib.error import HTTPError, URLError

from mcp_server_python_docs.services.package_docs import (
    _PYPI_METADATA_MAX_BYTES,
    PackageDocsService,
)


class _Resp:
    def __init__(self, payload: dict | bytes):
        self._payload = payload
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        return False
    def read(self, size: int = -1) -> bytes:
        if isinstance(self._payload, bytes):
            data = self._payload
        else:
            data = json.dumps(self._payload).encode()
        return data if size < 0 else data[:size]


def test_package_docs_uses_official_pypi_metadata_and_declared_urls():
    seen: list[str] = []
    def fetch(url: str, timeout: float):
        seen.append(url)
        return _Resp({"info": {"name": "SampleProject", "version": "4.0.0",
            "summary": "sample", "project_url": "https://pypi.org/project/sampleproject/",
            "home_page": "https://github.com/pypa/sampleproject",
            "docs_url": "https://sampleproject.pypa.io/",
            "project_urls": {"Documentation": "https://sampleproject.pypa.io/",
                "Source": "https://github.com/pypa/sampleproject"}}})

    result = PackageDocsService(fetcher=fetch).lookup("Sample_Project")

    assert seen == ["https://pypi.org/pypi/sample-project/json"]
    assert result.package == "SampleProject"
    assert result.trust_boundary == "pypi-declared-metadata"
    assert {s.label for s in result.sources} >= {
        "PyPI project", "Documentation", "Homepage", "Source",
    }
    assert all(s.declared_by == "PyPI project metadata" for s in result.sources)


def test_package_docs_filters_uncontrolled_urls_and_has_no_web_search_fallback():
    def fetch(url: str, timeout: float):
        return _Resp({"info": {"name": "demo", "version": "1.0.0",
            "home_page": "https://demo.example/",
            "description": "See https://random-blog.example/demo",
            "project_urls": {"Community mirror": "https://mirror.example/demo"}}})

    result = PackageDocsService(fetcher=fetch).lookup("demo")
    urls = [s.url for s in result.sources]
    assert "https://demo.example/" in urls
    assert "https://random-blog.example/demo" not in urls
    assert "https://mirror.example/demo" not in urls
    assert "community mirror" in (result.note or "").lower()


def test_package_docs_not_found_and_tool_annotation():
    def missing(url: str, timeout: float):
        raise HTTPError(url, 404, "Not Found", hdrs=None, fp=None)

    result = PackageDocsService(fetcher=missing).lookup("missing-package")
    assert result.sources == []
    assert "not found" in (result.note or "").lower()

    from mcp_server_python_docs.server import create_server
    tool = create_server()._tool_manager._tools["lookup_package_docs"]
    assert tool.annotations.readOnlyHint is True
    assert tool.annotations.openWorldHint is True


def test_package_docs_reports_non_404_pypi_http_errors():
    def rate_limited(url: str, timeout: float):
        raise HTTPError(url, 429, "Too Many Requests", hdrs=None, fp=None)

    result = PackageDocsService(fetcher=rate_limited).lookup("busy-package")

    assert result.sources == []
    assert result.metadata_source == "https://pypi.org/pypi/busy-package/json"
    assert result.note == "PyPI returned HTTP 429."


def test_package_docs_rejects_oversized_pypi_metadata_without_unbounded_read():
    class LargeResp:
        requested_size: int | None = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, size: int = -1) -> bytes:
            self.requested_size = size
            assert size == _PYPI_METADATA_MAX_BYTES + 1
            return b"x" * size

    response = LargeResp()

    def fetch(url: str, timeout: float):
        return response

    result = PackageDocsService(fetcher=fetch).lookup("huge-package")

    assert response.requested_size == _PYPI_METADATA_MAX_BYTES + 1
    assert result.sources == []
    assert result.note == "PyPI metadata exceeded size limit."


def test_package_docs_reports_retrieval_and_json_errors():
    def unreachable(url: str, timeout: float):
        raise URLError("network down")

    network_result = PackageDocsService(fetcher=unreachable).lookup("demo")
    assert network_result.sources == []
    assert network_result.note == "Unable to retrieve PyPI metadata: URLError."

    def invalid_json(url: str, timeout: float):
        return _Resp(b"not json")

    json_result = PackageDocsService(fetcher=invalid_json).lookup("demo")
    assert json_result.sources == []
    assert json_result.note == "Unable to retrieve PyPI metadata: JSONDecodeError."

    def invalid_utf8(url: str, timeout: float):
        return _Resp(b"\xff\xfe\xfd")

    utf8_result = PackageDocsService(fetcher=invalid_utf8).lookup("demo")
    assert utf8_result.sources == []
    assert utf8_result.note == "Unable to retrieve PyPI metadata: UnicodeDecodeError."
