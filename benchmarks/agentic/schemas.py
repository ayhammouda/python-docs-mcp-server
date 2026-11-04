"""MCP tool -> provider tool conversion (design §6, "Tool schemas").

Anthropic and OpenAI take the MCP input schema verbatim (OpenAI with
``strict`` explicitly false). Gemini accepts an OpenAPI subset, so its
schemas are reduced to the keys the pinned SDK's ``Schema`` model knows, and
every dropped key is recorded.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

from google.genai import types as genai_types

from benchmarks.agentic.types import ToolSpec

#: JSON names (pydantic aliases) of google.genai.types.Schema fields.
GEMINI_SCHEMA_KEYS: frozenset[str] = frozenset(
    field.alias or name for name, field in genai_types.Schema.model_fields.items()
)


def to_anthropic(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in tools
    ]


def to_openai_responses(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": t.name,
            "description": t.description,
            "parameters": t.input_schema,
            "strict": False,  # MCP schemas do not meet strict-mode rules
        }
        for t in tools
    ]


def _reduce(schema: Any, path: str, dropped: list[str]) -> Any:
    if not isinstance(schema, dict):
        return schema
    reduced: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in GEMINI_SCHEMA_KEYS:
            dropped.append(f"{path}.{key}")
        elif key == "properties" and isinstance(value, dict):
            # Property names are data, not keywords: keep every one, reduce its schema.
            reduced[key] = {
                name: _reduce(sub, f"{path}.properties.{name}", dropped)
                for name, sub in value.items()
            }
        elif key == "items":
            reduced[key] = _reduce(value, f"{path}.items", dropped)
        elif key == "anyOf" and isinstance(value, list):
            reduced[key] = [
                _reduce(member, f"{path}.anyOf.{index}", dropped)
                for index, member in enumerate(value)
            ]
        else:
            reduced[key] = value
    return reduced


def to_gemini(tools: Sequence[ToolSpec]) -> tuple[list[dict[str, Any]], list[str]]:
    """Function declarations, and the dotted paths of every key dropped."""
    dropped: list[str] = []
    declarations = [
        {
            "name": t.name,
            "description": t.description,
            "parameters": _reduce(t.input_schema, t.name, dropped),
        }
        for t in tools
    ]
    return declarations, dropped


def schemas_sha256(converted: Any) -> str:
    canonical = json.dumps(converted, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
