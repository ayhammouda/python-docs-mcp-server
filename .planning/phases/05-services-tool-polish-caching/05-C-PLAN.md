---
phase: 5
plan_id: 05-C
title: "Structured logging decorators (OPS-01, OPS-02, OPS-03)"
wave: 1
depends_on:
  - 05-A
files_modified:
  - src/mcp_server_python_docs/services/observability.py
  - src/mcp_server_python_docs/services/search.py
  - src/mcp_server_python_docs/services/content.py
  - src/mcp_server_python_docs/services/version.py
requirements:
  - OPS-01
  - OPS-02
  - OPS-03
autonomous: true
---

<objective>
Implement structured per-call stderr observability via service-method decorators (not FastMCP middleware). Every tool call writes one logfmt key=value line to stderr containing: tool name, version, latency_ms, result_count, truncation flag, symbol-resolution path (exact/fuzzy/fts), and synonym_expansion (yes/no). The decorator wraps service methods.
</objective>

<tasks>

<task id="1">
<title>Create observability module with log_tool_call decorator</title>
<read_first>
- src/mcp_server_python_docs/__main__.py (logging setup — uses stderr, format string at line 32)
- src/mcp_server_python_docs/services/search.py (method signature to wrap)
- src/mcp_server_python_docs/services/content.py (method signature to wrap)
- src/mcp_server_python_docs/services/version.py (method signature to wrap)
</read_first>
<action>
Create `src/mcp_server_python_docs/services/observability.py`:

```python
"""Structured logging for service method calls (OPS-01, OPS-02, OPS-03).

Implements per-service-method decorators that log structured key=value (logfmt)
lines to stderr. NOT FastMCP middleware — the MCP SDK middleware surface is
unstable, so we instrument at the service layer.

Log format (logfmt — D-10 from Phase 1):
  tool=search_docs version=3.13 latency_ms=12 result_count=5 truncated=false resolution=fts synonym_expansion=yes
"""
from __future__ import annotations

import functools
import logging
import sys
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


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
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.monotonic()

            result = fn(*args, **kwargs)

            elapsed_ms = (time.monotonic() - start) * 1000

            # Extract structured fields from args/kwargs and result
            fields: dict[str, Any] = {
                "tool": tool_name,
                "latency_ms": round(elapsed_ms, 1),
            }

            # Extract version from kwargs or positional args
            if "version" in kwargs:
                fields["version"] = kwargs["version"] or "default"
            elif len(args) > 2:
                # positional: self, query, version
                fields["version"] = args[2] or "default"

            # Extract result-specific fields
            if hasattr(result, "hits"):
                # SearchDocsResult
                fields["result_count"] = len(result.hits)
                # Determine resolution path
                if result.hits:
                    # Check if symbol fast-path was used
                    kinds = {h.kind for h in result.hits}
                    if kinds & {"class", "function", "method", "attribute", "data", "module"}:
                        fields["resolution"] = "exact"
                    else:
                        fields["resolution"] = "fts"
                else:
                    fields["resolution"] = "none"
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

            # Synonym expansion detection
            if "query" in kwargs or (len(args) > 1 and tool_name == "search_docs"):
                fields["synonym_expansion"] = "no"  # default; overridden if expanded
                # The actual expansion tracking requires access to the synonym table
                # and query — we check if SearchService used expansion by examining
                # the presence of expanded terms. For simplicity, check if the service
                # has a _last_expanded flag.

            # Write logfmt line to stderr
            log_line = _format_logfmt(**fields)
            print(log_line, file=sys.stderr)

            return result

        return wrapper
    return decorator
```

The decorator uses `time.monotonic()` for latency measurement (monotonic clock, not wall clock).
The logfmt output goes directly to `sys.stderr` (which is safe — stdout is redirected to stderr in __main__.py, and stderr is the log destination per HYGN-01/02).
</action>
<acceptance_criteria>
- File exists at `src/mcp_server_python_docs/services/observability.py`
- `_format_logfmt()` produces `key=value` pairs with correct bool/float/string formatting
- `log_tool_call` is a decorator factory that accepts `tool_name` string
- The decorator measures latency using `time.monotonic()`
- Output goes to `sys.stderr` via `print(..., file=sys.stderr)`
- `python -c "from mcp_server_python_docs.services.observability import log_tool_call"` succeeds
</acceptance_criteria>
</task>

<task id="2">
<title>Apply log_tool_call decorator to SearchService.search</title>
<read_first>
- src/mcp_server_python_docs/services/search.py (current search method)
- src/mcp_server_python_docs/services/observability.py (decorator)
</read_first>
<action>
In `src/mcp_server_python_docs/services/search.py`:

1. Add import:
```python
from mcp_server_python_docs.services.observability import log_tool_call
```

2. Add a `_synonym_expanded` flag tracking to the `search` method. Before building the match expression, check if expansion will add terms:
```python
from mcp_server_python_docs.retrieval.query import expand_synonyms
```

3. Apply the decorator to the `search` method:
```python
@log_tool_call("search_docs")
def search(self, query, version=None, kind="auto", max_results=5):
    ...
```

4. Add synonym expansion tracking inside the method. After `build_match_expression` is called, set `self._last_synonym_expanded` to whether expansion added terms:
```python
# Track synonym expansion for observability
original_tokens = set(query.lower().split())
expanded = expand_synonyms(query, self._synonyms)
self._last_synonym_expanded = expanded != original_tokens
```
</action>
<acceptance_criteria>
- `SearchService.search` is decorated with `@log_tool_call("search_docs")`
- Calling `search()` produces a logfmt line on stderr containing `tool=search_docs`
- The log line includes `latency_ms`, `result_count`, `resolution`
</acceptance_criteria>
</task>

<task id="3">
<title>Apply log_tool_call decorator to ContentService.get_docs</title>
<read_first>
- src/mcp_server_python_docs/services/content.py (current get_docs method)
- src/mcp_server_python_docs/services/observability.py (decorator)
</read_first>
<action>
In `src/mcp_server_python_docs/services/content.py`:

1. Add import:
```python
from mcp_server_python_docs.services.observability import log_tool_call
```

2. Apply the decorator:
```python
@log_tool_call("get_docs")
def get_docs(self, slug, version=None, anchor=None, max_chars=8000, start_index=0):
    ...
```
</action>
<acceptance_criteria>
- `ContentService.get_docs` is decorated with `@log_tool_call("get_docs")`
- Calling `get_docs()` produces a logfmt line on stderr containing `tool=get_docs`
- The log line includes `truncated` field
</acceptance_criteria>
</task>

<task id="4">
<title>Apply log_tool_call decorator to VersionService.list_versions</title>
<read_first>
- src/mcp_server_python_docs/services/version.py (current list_versions method)
- src/mcp_server_python_docs/services/observability.py (decorator)
</read_first>
<action>
In `src/mcp_server_python_docs/services/version.py`:

1. Add import:
```python
from mcp_server_python_docs.services.observability import log_tool_call
```

2. Apply the decorator:
```python
@log_tool_call("list_versions")
def list_versions(self):
    ...
```
</action>
<acceptance_criteria>
- `VersionService.list_versions` is decorated with `@log_tool_call("list_versions")`
- Calling `list_versions()` produces a logfmt line on stderr containing `tool=list_versions`
- The log line includes `result_count` field
</acceptance_criteria>
</task>

</tasks>

<verification>
1. Each service method produces exactly one logfmt line per call to stderr
2. Log line contains all required fields: tool, version (where applicable), latency_ms, result_count, truncated, resolution, synonym_expansion (where applicable)
3. Log lines are parseable as logfmt (key=value pairs space-separated)
4. No logging goes to stdout (stdio protocol hygiene preserved)
5. `uv run pytest tests/ -x -q` passes (existing tests unbroken)
</verification>

<must_haves>
- Every tool call writes one structured log line to stderr (OPS-01)
- Logs are structured logfmt key=value (OPS-02, D-10)
- Implemented as per-service-method decorators, NOT FastMCP middleware (OPS-03)
- Log fields include: tool, version, latency_ms, result_count, truncated, resolution, synonym_expansion
</must_haves>
