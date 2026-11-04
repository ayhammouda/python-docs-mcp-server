"""MCP tool -> provider tool conversion (design §6, "Tool schemas")."""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.agentic.schemas import (
    GEMINI_SCHEMA_KEYS,
    schemas_sha256,
    to_anthropic,
    to_gemini,
    to_openai_chat,
    to_openai_responses,
)
from benchmarks.agentic.types import ToolSpec

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS = [
    ToolSpec(t["name"], t["description"], t["inputSchema"])
    for t in json.loads((REPO_ROOT / "tests/fixtures/tools-list.json").read_text())
]


def test_anthropic_tools_keep_name_description_and_input_schema_verbatim() -> None:
    assert to_anthropic(TOOLS)[0] == {
        "name": TOOLS[0].name,
        "description": TOOLS[0].description,
        "input_schema": TOOLS[0].input_schema,
    }
    assert len(to_anthropic(TOOLS)) == 7


def test_openai_tools_set_strict_false_explicitly() -> None:
    converted = to_openai_responses(TOOLS)
    assert all(t["type"] == "function" and t["strict"] is False for t in converted)
    assert converted[0]["parameters"] == TOOLS[0].input_schema
    assert converted[0]["name"] == TOOLS[0].name


def test_gemini_reduction_drops_unsupported_keywords_and_records_them() -> None:
    spec = ToolSpec(
        "fetch",
        "d",
        {
            "type": "object",
            "properties": {
                "max_length": {
                    "type": "integer",
                    "exclusiveMaximum": 1000000,
                    "exclusiveMinimum": 0,
                    "default": 5000,
                }
            },
            "required": [],
        },
    )
    decls, dropped = to_gemini([spec])
    assert decls[0]["parameters"]["properties"]["max_length"] == {
        "type": "integer",
        "default": 5000,
    }
    assert dropped == [
        "fetch.properties.max_length.exclusiveMaximum",
        "fetch.properties.max_length.exclusiveMinimum",
    ]
    assert decls[0]["name"] == "fetch" and decls[0]["description"] == "d"


def test_gemini_reduction_walks_items_and_any_of() -> None:
    spec = ToolSpec(
        "t",
        "d",
        {
            "type": "object",
            "properties": {
                "a": {"type": "array", "items": {"type": "string", "const": "x"}},
                "b": {"anyOf": [{"type": "string", "$comment": "c"}, {"type": "null"}]},
            },
        },
    )
    decls, dropped = to_gemini([spec])
    assert decls[0]["parameters"]["properties"]["a"]["items"] == {"type": "string"}
    assert dropped == ["t.properties.a.items.const", "t.properties.b.anyOf.0.$comment"]


def test_gemini_reduction_keeps_a_property_named_like_a_keyword() -> None:
    props = {
        "format": {"type": "string"},
        "pattern": {"type": "string"},
        "const": {"type": "string"},
    }
    decls, dropped = to_gemini([ToolSpec("t", "d", {"type": "object", "properties": props})])
    assert decls[0]["parameters"]["properties"] == props and dropped == []


def test_gemini_reduction_of_the_real_tool_list_drops_nothing() -> None:
    assert to_gemini(TOOLS)[1] == []


def test_gemini_schema_keys_come_from_the_pinned_sdk() -> None:
    assert {"anyOf", "enum", "minimum", "maxLength", "description", "default"} <= GEMINI_SCHEMA_KEYS
    assert "exclusiveMinimum" not in GEMINI_SCHEMA_KEYS
    assert "max_length" not in GEMINI_SCHEMA_KEYS  # aliases, not Python field names


def test_schemas_sha256_ignores_key_order() -> None:
    assert schemas_sha256([{"a": 1, "b": "é"}]) == schemas_sha256([{"b": "é", "a": 1}])
    assert schemas_sha256([{"a": 1}]) != schemas_sha256([{"a": 2}])


def test_to_openai_chat_wraps_each_tool_with_strict_false() -> None:
    converted = to_openai_chat(TOOLS)
    assert len(converted) == 7
    assert converted[0] == {
        "type": "function",
        "function": {
            "name": TOOLS[0].name,
            "description": TOOLS[0].description,
            "parameters": TOOLS[0].input_schema,
            "strict": False,
        },
    }
