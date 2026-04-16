"""Schema snapshot drift-guard tests (SRVR-06).

Compares Pydantic model JSON schemas against committed fixtures.
When schemas change intentionally, run with UPDATE_SCHEMAS=1 to update fixtures:
    UPDATE_SCHEMAS=1 pytest tests/test_schema_snapshot.py
"""
import json
import os
from pathlib import Path

import pytest

from mcp_server_python_docs.models import (
    GetDocsInput,
    GetDocsResult,
    ListVersionsResult,
    SearchDocsInput,
    SearchDocsResult,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
UPDATE = os.environ.get("UPDATE_SCHEMAS", "") == "1"

SCHEMA_PAIRS = [
    ("schema-search_docs-input.json", SearchDocsInput),
    ("schema-search_docs-output.json", SearchDocsResult),
    ("schema-get_docs-input.json", GetDocsInput),
    ("schema-get_docs-output.json", GetDocsResult),
    ("schema-list_versions-output.json", ListVersionsResult),
]


@pytest.fixture(autouse=True)
def ensure_fixtures_dir():
    """Create fixtures directory if it doesn't exist."""
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)


@pytest.mark.parametrize("fixture_name,model_class", SCHEMA_PAIRS)
def test_schema_snapshot(fixture_name: str, model_class: type) -> None:
    """Verify model schema matches committed fixture."""
    fixture_path = FIXTURES_DIR / fixture_name
    current_schema = model_class.model_json_schema()

    if UPDATE or not fixture_path.exists():
        fixture_path.write_text(json.dumps(current_schema, indent=2) + "\n")
        pytest.skip(f"Fixture {fixture_name} written/updated")

    committed_schema = json.loads(fixture_path.read_text())
    assert current_schema == committed_schema, (
        f"Schema drift detected for {model_class.__name__}. "
        f"Run UPDATE_SCHEMAS=1 pytest tests/test_schema_snapshot.py to update."
    )
