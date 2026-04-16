"""Shared test fixtures for mcp-server-python-docs."""
from __future__ import annotations

import sqlite3
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


# ── Stability test fixtures (Phase 7, TEST-01) ──────────────────────

# Representative stdlib symbols for structural stability tests.
# Each tuple: (qualified_name, module, symbol_type, uri, anchor)
# fmt: off
_STABILITY_SYMBOLS = [  # noqa: E501 -- test data, readability over line length
    ("asyncio.TaskGroup", "asyncio", "class",
     "library/asyncio-task.html#asyncio.TaskGroup", "asyncio.TaskGroup"),
    ("asyncio.run", "asyncio", "function",
     "library/asyncio-runner.html#asyncio.run", "asyncio.run"),
    ("json.dumps", "json", "function",
     "library/json.html#json.dumps", "json.dumps"),
    ("json.loads", "json", "function",
     "library/json.html#json.loads", "json.loads"),
    ("os.path.join", "os.path", "function",
     "library/os.path.html#os.path.join", "os.path.join"),
    ("pathlib.Path", "pathlib", "class",
     "library/pathlib.html#pathlib.Path", "pathlib.Path"),
    ("collections.OrderedDict", "collections", "class",
     "library/collections.html#collections.OrderedDict", "collections.OrderedDict"),
    ("typing.Optional", "typing", "data",
     "library/typing.html#typing.Optional", "typing.Optional"),
    ("re.compile", "re", "function",
     "library/re.html#re.compile", "re.compile"),
    ("subprocess.run", "subprocess", "function",
     "library/subprocess.html#subprocess.run", "subprocess.run"),
    ("logging.getLogger", "logging", "function",
     "library/logging.html#logging.getLogger", "logging.getLogger"),
    ("sqlite3.connect", "sqlite3", "function",
     "library/sqlite3.html#sqlite3.connect", "sqlite3.connect"),
    ("http.server.HTTPServer", "http.server", "class",
     "library/http.server.html#http.server.HTTPServer", "http.server.HTTPServer"),
    ("urllib.parse.urlparse", "urllib.parse", "function",
     "library/urllib.parse.html#urllib.parse.urlparse", "urllib.parse.urlparse"),
    ("dataclasses.dataclass", "dataclasses", "function",
     "library/dataclasses.html#dataclasses.dataclass", "dataclasses.dataclass"),
    ("functools.lru_cache", "functools", "function",
     "library/functools.html#functools.lru_cache", "functools.lru_cache"),
    ("itertools.chain", "itertools", "function",
     "library/itertools.html#itertools.chain", "itertools.chain"),
    ("contextlib.contextmanager", "contextlib", "function",
     "library/contextlib.html#contextlib.contextmanager", "contextlib.contextmanager"),
    ("abc.ABC", "abc", "class",
     "library/abc.html#abc.ABC", "abc.ABC"),
    ("enum.Enum", "enum", "class",
     "library/enum.html#enum.Enum", "enum.Enum"),
    ("datetime.datetime", "datetime", "class",
     "library/datetime.html#datetime.datetime", "datetime.datetime"),
    ("hashlib.sha256", "hashlib", "function",
     "library/hashlib.html#hashlib.sha256", "hashlib.sha256"),
    ("socket.socket", "socket", "class",
     "library/socket.html#socket.socket", "socket.socket"),
    ("threading.Thread", "threading", "class",
     "library/threading.html#threading.Thread", "threading.Thread"),
    ("multiprocessing.Process", "multiprocessing", "class",
     "library/multiprocessing.html#multiprocessing.Process", "multiprocessing.Process"),
    ("sys.argv", "sys", "data",
     "library/sys.html#sys.argv", "sys.argv"),
    ("os.environ", "os", "data",
     "library/os.html#os.environ", "os.environ"),
    ("io.StringIO", "io", "class",
     "library/io.html#io.StringIO", "io.StringIO"),
    ("csv.reader", "csv", "function",
     "library/csv.html#csv.reader", "csv.reader"),
    ("argparse.ArgumentParser", "argparse", "class",
     "library/argparse.html#argparse.ArgumentParser", "argparse.ArgumentParser"),
]
# fmt: on

# Section data for FTS5 section searches.
# Each tuple: (slug, title, sections_list)
# where each section is (anchor, heading, level, ordinal, content_text)
_STABILITY_DOCUMENTS = [
    (
        "library/asyncio-task.html",
        "asyncio -- Tasks",
        [
            ("asyncio.TaskGroup", "asyncio.TaskGroup", 2, 1,
             "A task group is a modern API for managing multiple concurrent tasks. "
             "TaskGroup provides structured concurrency for asyncio applications."),
            ("asyncio.create_task", "asyncio.create_task", 2, 2,
             "Wrap the coroutine into a Task and schedule its execution."),
        ],
    ),
    (
        "library/json.html",
        "json -- JSON encoder and decoder",
        [
            ("json.dumps", "json.dumps", 2, 1,
             "Serialize obj to a JSON formatted str. Use sort_keys to produce sorted output."),
            ("json.loads", "json.loads", 2, 2,
             "Deserialize s (a str, bytes or bytearray) to a Python object."),
        ],
    ),
    (
        "library/os.path.html",
        "os.path -- Common pathname manipulations",
        [
            ("os.path.join", "os.path.join", 2, 1,
             "Join one or more path components intelligently. If a component is an absolute path, "
             "all previous components are thrown away."),
        ],
    ),
]


@pytest.fixture
def stability_db(tmp_path) -> sqlite3.Connection:
    """A test database populated with representative stdlib symbols and sections.

    Provides ~30 symbols and a few section documents for structural stability
    tests that assert properties (len >= 1, substring in field) rather than
    exact content. Survives CPython doc revisions.
    """
    db_path = tmp_path / "stability.db"
    conn = get_readwrite_connection(db_path)
    bootstrap_schema(conn)

    # Insert doc_set
    conn.execute(
        "INSERT INTO doc_sets (source, version, language, label, is_default, base_url) "
        "VALUES ('python-docs', '3.13', 'en', 'Python 3.13', 1, "
        "'https://docs.python.org/3.13/')"
    )
    doc_set_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Insert symbols
    for qname, module, stype, uri, anchor in _STABILITY_SYMBOLS:
        norm = qname.lower().replace(".", "_")
        conn.execute(
            "INSERT INTO symbols (doc_set_id, qualified_name, normalized_name, "
            "module, symbol_type, uri, anchor) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (doc_set_id, qname, norm, module, stype, uri, anchor),
        )

    # Insert documents and sections for FTS5 section search
    for slug, title, section_list in _STABILITY_DOCUMENTS:
        uri = slug
        conn.execute(
            "INSERT INTO documents (doc_set_id, uri, slug, title, content_text, char_count) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (doc_set_id, uri, slug, title, title, len(title)),
        )
        doc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        for anchor, heading, level, ordinal, content_text in section_list:
            section_uri = f"{slug}#{anchor}"
            conn.execute(
                "INSERT INTO sections (document_id, uri, anchor, heading, level, "
                "ordinal, content_text, char_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (doc_id, section_uri, anchor, heading, level, ordinal,
                 content_text, len(content_text)),
            )

    conn.commit()

    # Rebuild FTS indexes so search queries work
    conn.execute("INSERT INTO symbols_fts(symbols_fts) VALUES('rebuild')")
    conn.execute("INSERT INTO sections_fts(sections_fts) VALUES('rebuild')")
    conn.execute("INSERT INTO examples_fts(examples_fts) VALUES('rebuild')")
    conn.commit()

    yield conn
    conn.close()


@pytest.fixture
def search_service(stability_db):
    """SearchService backed by the stability_db fixture."""
    from mcp_server_python_docs.services.search import SearchService

    return SearchService(db=stability_db, synonyms={})
