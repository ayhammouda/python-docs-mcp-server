"""Schema snapshot drift guard (SRVR-06).

Pins the served ``tools/list`` (names, descriptions, input and output schemas,
annotations) against ``tests/fixtures/tools-list.json``. The fixture pins
normalized description text: each passes through ``inspect.cleandoc``,
because Python 3.12 serves docstring indentation and 3.13+ serves a trailing
newline on lookup_package_docs and whatsnew_for_version. When the tool surface
changes on purpose, regenerate the fixture:
    UPDATE_SCHEMAS=1 uv run --locked pytest tests/test_schema_snapshot.py
"""
import inspect
import json
import os
from pathlib import Path

import anyio
import pytest

from mcp_server_python_docs.server import create_server

FIXTURE = Path(__file__).parent / "fixtures" / "tools-list.json"
UPDATE = os.environ.get("UPDATE_SCHEMAS", "") == "1"


def _served_tools() -> list[dict]:
    """Dump what tools/list sends; list_tools() skips the lifespan and its index."""
    tools = []
    for tool in anyio.run(create_server().list_tools):
        dumped = tool.model_dump(by_alias=True, mode="json", exclude_none=True)
        # Python 3.12 keeps docstring indentation that 3.13+ strips at compile time.
        dumped["description"] = inspect.cleandoc(dumped["description"])
        tools.append(dumped)
    return tools


def test_tools_list_snapshot() -> None:
    served = _served_tools()
    if UPDATE:
        FIXTURE.write_text(json.dumps(served, indent=2) + "\n")
        pytest.skip("tools-list.json regenerated")
    assert served == json.loads(FIXTURE.read_text()), (
        "tools/list drift; if intended, regenerate with "
        "UPDATE_SCHEMAS=1 uv run --locked pytest tests/test_schema_snapshot.py"
    )
