"""Pinned CPython documentation build targets."""
from __future__ import annotations

from typing import Final, TypedDict


class CPythonDocsBuildConfig(TypedDict):
    """Build settings for one CPython documentation release."""

    tag: str
    sphinx_pin: str


SUPPORTED_DOC_VERSIONS: Final[tuple[str, ...]] = (
    "3.10",
    "3.11",
    "3.12",
    "3.13",
    "3.14",
)

SUPPORTED_DOC_VERSIONS_CSV: Final[str] = ",".join(SUPPORTED_DOC_VERSIONS)

# CPython git tags are pinned so content builds are reproducible and do not
# drift when a maintenance branch receives new commits.
CPYTHON_DOCS_BUILD_CONFIG: Final[dict[str, CPythonDocsBuildConfig]] = {
    "3.10": {"tag": "v3.10.20", "sphinx_pin": "sphinx==3.4.3"},
    "3.11": {"tag": "v3.11.15", "sphinx_pin": "sphinx~=7.2.0"},
    "3.12": {"tag": "v3.12.13", "sphinx_pin": "sphinx~=8.2.0"},
    "3.13": {"tag": "v3.13.13", "sphinx_pin": "sphinx<9.0.0"},
    "3.14": {"tag": "v3.14.4", "sphinx_pin": "sphinx<9.0.0"},
}
