"""Error taxonomy for mcp-server-python-docs.

Named exceptions for predictable failure modes, mapped to MCP error responses.
See build guide section 10.
"""


class DocsServerError(Exception):
    """Base error for all docs server errors."""


class VersionNotFoundError(DocsServerError):
    """Requested Python version not found in index."""


class SymbolNotFoundError(DocsServerError):
    """Requested symbol not found in index."""


class PageNotFoundError(DocsServerError):
    """Requested page/slug not found in index."""


class IndexNotBuiltError(DocsServerError):
    """No index.db found at expected cache path."""


class IngestionError(DocsServerError):
    """Error during index building/ingestion."""


class FTS5UnavailableError(DocsServerError):
    """SQLite FTS5 extension not available in this build."""
