"""Pydantic input/output models for all MCP tools.

These models define the tool contracts. FastMCP auto-generates outputSchema
from BaseModel return types, providing structuredContent for free (SRVR-05).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# --- search_docs models ---


class SearchDocsInput(BaseModel):
    """Input parameters for search_docs tool."""

    query: str = Field(
        max_length=500,
        description="Search query - Python symbol (asyncio.TaskGroup) or concept (parse json)",
    )
    version: str | None = Field(
        default=None,
        description="Python version (e.g. '3.13'). Defaults to latest.",
    )
    kind: Literal["auto", "page", "symbol", "section", "example"] = Field(
        default="auto",
        description=(
            "Search type. Use 'symbol' for API lookups, "
            "'example' for code samples, 'auto' otherwise."
        ),
    )
    max_results: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum number of results to return.",
    )


class SymbolHit(BaseModel):
    """A single search result hit."""

    uri: str = Field(description="Documentation URI path")
    title: str = Field(description="Display title")
    kind: str = Field(description="Hit type: class, function, method, module, etc.")
    snippet: str = Field(default="", description="Brief excerpt or description")
    score: float = Field(default=0.0, description="Relevance score")
    version: str = Field(description="Python version this hit belongs to")
    slug: str = Field(default="", description="Page slug for get_docs follow-up")
    anchor: str = Field(default="", description="Section anchor for get_docs follow-up")


class SearchDocsResult(BaseModel):
    """Output from search_docs tool."""

    hits: list[SymbolHit] = Field(
        default_factory=list,
        description="Search result hits",
    )
    note: str | None = Field(
        default=None,
        description="Informational note (e.g., limited search mode)",
    )


# --- get_docs models ---


class GetDocsInput(BaseModel):
    """Input parameters for get_docs tool."""

    slug: str = Field(
        max_length=500,
        description="Page slug (e.g. 'library/asyncio-task.html')",
    )
    version: str | None = Field(
        default=None,
        description="Python version. Defaults to latest.",
    )
    anchor: str | None = Field(
        default=None,
        description="Section anchor for section-only retrieval",
    )
    max_chars: int = Field(
        default=8000,
        ge=100,
        le=50000,
        description="Maximum characters to return",
    )
    start_index: int = Field(
        default=0,
        ge=0,
        description="Start position for pagination",
    )


class GetDocsResult(BaseModel):
    """Output from get_docs tool."""

    content: str = Field(description="Documentation content in markdown")
    slug: str = Field(description="Page slug")
    title: str = Field(description="Page or section title")
    version: str = Field(description="Python version")
    anchor: str | None = Field(
        default=None,
        description="Section anchor if section-level",
    )
    char_count: int = Field(description="Total character count of full content")
    truncated: bool = Field(
        default=False,
        description="Whether content was truncated",
    )
    next_start_index: int | None = Field(
        default=None,
        description="Next start_index for pagination, if truncated",
    )


# --- list_versions models ---


class VersionInfo(BaseModel):
    """Information about an available Python version."""

    version: str = Field(description="Python version string (e.g. '3.13')")
    language: str = Field(default="en", description="Documentation language")
    label: str = Field(description="Display label")
    is_default: bool = Field(description="Whether this is the default version")
    built_at: str = Field(description="When this version's index was built")


class ListVersionsResult(BaseModel):
    """Output from list_versions tool."""

    versions: list[VersionInfo] = Field(
        description="Available documentation versions",
    )


# --- detect_python_version models ---


class DetectPythonVersionResult(BaseModel):
    """Output from detect_python_version tool."""

    detected_version: str = Field(
        description="Python major.minor detected from the user's environment (e.g. '3.13')"
    )
    source: str = Field(
        description="How the version was detected: '.python-version file', 'python3 in PATH', or 'server runtime'"
    )
    matched_index_version: str | None = Field(
        default=None,
        description="The detected version if it matches an indexed doc set, otherwise null",
    )
    is_default: bool = Field(
        description="Whether this detected version is being used as the default for get_docs"
    )
