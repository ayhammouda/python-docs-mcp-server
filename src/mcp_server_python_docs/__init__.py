"""MCP server for Python standard library documentation."""

import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def _read_version() -> str:
    try:
        return version("python-docs-mcp-server")
    except PackageNotFoundError:
        pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
        with pyproject_path.open("rb") as fh:
            return tomllib.load(fh)["project"]["version"]


__version__ = _read_version()
