"""Pinned CPython documentation build targets."""
from __future__ import annotations

from typing import Final, TypedDict


class CPythonDocsBuildConfig(TypedDict):
    """Build settings for one CPython documentation release."""

    tag: str
    sha: str
    sphinx_pin: str


SUPPORTED_DOC_VERSIONS: Final[tuple[str, ...]] = (
    "3.10",
    "3.11",
    "3.12",
    "3.13",
    "3.14",
)

SUPPORTED_DOC_VERSIONS_CSV: Final[str] = ",".join(SUPPORTED_DOC_VERSIONS)

# CPython git SHAs are authoritative for content build integrity. Tags are kept
# for human-readable version mapping, but a moved tag must fail verification.
CPYTHON_DOCS_BUILD_CONFIG: Final[dict[str, CPythonDocsBuildConfig]] = {
    "3.10": {
        "tag": "v3.10.20",
        "sha": "842e987df856a5d4db37933c62a3456930a19092",
        "sphinx_pin": "sphinx==3.4.3",
    },
    "3.11": {
        "tag": "v3.11.15",
        "sha": "2340a037f7450e70fccfe411e6531afb4d57a312",
        "sphinx_pin": "sphinx~=7.2.0",
    },
    "3.12": {
        "tag": "v3.12.13",
        "sha": "3bb231a6a5dc02b95658877318bf61501a7209e9",
        "sphinx_pin": "sphinx~=8.2.0",
    },
    "3.13": {
        "tag": "v3.13.13",
        "sha": "01104ce1beb3135c2e0c01ec835b994c1f55a1c0",
        "sphinx_pin": "sphinx<9.0.0",
    },
    "3.14": {
        "tag": "v3.14.4",
        "sha": "23116f998f6789d8c2fbe5ed5b8146854c8c2a4f",
        "sphinx_pin": "sphinx<9.0.0",
    },
}
