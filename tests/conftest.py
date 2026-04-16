"""Shared test fixtures for mcp-server-python-docs."""
from __future__ import annotations

from pathlib import Path

import pytest

from mcp_server_python_docs.storage.db import bootstrap_schema, get_readwrite_connection

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    """Path to test fixtures directory."""
    return FIXTURES_DIR


@pytest.fixture
def sample_fjson_path() -> Path:
    """Path to the sample valid .fjson fixture."""
    return FIXTURES_DIR / "sample_library.fjson"


@pytest.fixture
def broken_fjson_path() -> Path:
    """Path to the deliberately broken .fjson fixture."""
    return FIXTURES_DIR / "sample_broken.fjson"


@pytest.fixture
def test_db(tmp_path):
    """A fresh test database with schema bootstrapped."""
    db_path = tmp_path / "test.db"
    conn = get_readwrite_connection(db_path)
    bootstrap_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def populated_db(test_db):
    """A test database with a doc_set and minimal data."""
    test_db.execute(
        "INSERT INTO doc_sets (source, version, language, label, is_default, base_url) "
        "VALUES ('python-docs', '3.13', 'en', 'Python 3.13', 1, "
        "'https://docs.python.org/3.13/')"
    )
    test_db.commit()
    return test_db
