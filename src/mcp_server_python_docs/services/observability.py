"""Structured logging for service method calls (OPS-01, OPS-02, OPS-03).

Implements per-service-method decorators that log structured key=value (logfmt)
lines to stderr. NOT FastMCP middleware — the MCP SDK middleware surface is
unstable, so we instrument at the service layer.

Log format (logfmt — D-10 from Phase 1):
  tool=search_docs version=3.13 latency_ms=12 result_count=5 truncated=false resolution=fts synonym_expansion=yes
"""
from __future__ import annotations

import functools
import sys
import time
from collections.abc import Callable
from typing import Any


def _format_logfmt(**fields: Any) -> str:
    """Format fields as logfmt key=value pairs.

    Handles None (omitted), bool (lowercase), and strings with spaces (quoted).
    """
    parts = []
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, bool):
            parts.append(f"{key}={str(value).lower()}")
        elif isinstance(value, float):
            parts.append(f"{key}={value:.1f}")
        elif isinstance(value, str) and " " in value:
            parts.append(f'{key}="{value}"')
        else:
            parts.append(f"{key}={value}")
    return " ".join(parts)


def log_tool_call(tool_name: str) -> Callable:
    """Decorator that logs structured info for every service method call.

    Extracts version, result_count, truncated, resolution path, and
    synonym_expansion from the method arguments and return value.

    Args:
        tool_name: Name of the MCP tool (search_docs, get_docs, list_versions).
    """

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            start = time.monotonic()

            result = fn(self, *args, **kwargs)

            elapsed_ms = (time.monotonic() - start) * 1000

            # Extract structured fields from args/kwargs and result
            fields: dict[str, Any] = {
                "tool": tool_name,
                "latency_ms": round(elapsed_ms, 1),
            }

            # Extract version from kwargs or positional args
            version_val = kwargs.get("version")
            if version_val is None and args:
                # For search: (query, version, kind, max_results)
                # For get_docs: (slug, version, anchor, ...)
                if len(args) >= 2:
                    version_val = args[1]
            fields["version"] = version_val or "default"

            # Extract result-specific fields
            if hasattr(result, "hits"):
                # SearchDocsResult
                fields["result_count"] = len(result.hits)
                # Resolution path from service state
                if hasattr(self, "_last_resolution"):
                    fields["resolution"] = self._last_resolution
                else:
                    fields["resolution"] = "fts"
                fields["truncated"] = False
            elif hasattr(result, "truncated"):
                # GetDocsResult
                fields["result_count"] = 1 if result.content else 0
                fields["truncated"] = result.truncated
                fields["resolution"] = "exact"
            elif hasattr(result, "versions"):
                # ListVersionsResult
                fields["result_count"] = len(result.versions)
                fields["truncated"] = False
                fields["resolution"] = "exact"

            # Synonym expansion detection from service state
            if hasattr(self, "_last_synonym_expanded"):
                fields["synonym_expansion"] = (
                    "yes" if self._last_synonym_expanded else "no"
                )

            # Write logfmt line to stderr (HYGN-01 safe — stderr only)
            log_line = _format_logfmt(**fields)
            print(log_line, file=sys.stderr)

            return result

        return wrapper

    return decorator
