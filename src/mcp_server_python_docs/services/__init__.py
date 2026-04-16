"""Service layer — SearchService, ContentService, VersionService.

Services sit between FastMCP tool handlers and the retrieval/storage layers.
Dependency rule: server -> services -> retrieval/storage.
Services receive sqlite3.Connection via constructor and execute queries directly.
No service imports MCP types.
"""
