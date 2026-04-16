"""Service layer — SearchService, ContentService, VersionService.

Services sit between FastMCP tool handlers and the retrieval/storage layers.
Dependency rule: server -> services -> retrieval/storage.
No service touches SQL directly (except through storage/retrieval functions).
No service imports MCP types.
"""
