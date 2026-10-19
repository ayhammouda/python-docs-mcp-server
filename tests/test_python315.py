"""Separate 3.15 coverage: production ingestion and unchanged service contracts."""
from __future__ import annotations

import html
import json
import socket
from pathlib import Path

import pytest

from mcp_server_python_docs.errors import VersionNotFoundError
from mcp_server_python_docs.ingestion.cpython_versions import CPYTHON_DOCS_BUILD_CONFIG
from mcp_server_python_docs.ingestion.sphinx_json import ingest_fjson_file, rebuild_fts_indexes
from mcp_server_python_docs.services.compare import CompareService
from mcp_server_python_docs.services.content import ContentService
from mcp_server_python_docs.services.search import SearchService
from mcp_server_python_docs.services.version import VersionService
from mcp_server_python_docs.services.whatsnew import WhatsNewService
from mcp_server_python_docs.storage.db import bootstrap_schema, get_readwrite_connection

FIXTURE = json.loads((Path(__file__).parent / "fixtures/python315.json").read_text())


@pytest.fixture
def docs315(tmp_path, monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("Offline query attempted network access")

    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket.socket, "connect", deny)
    db = get_readwrite_connection(tmp_path / "index.db")
    bootstrap_schema(db)

    def ingest(ds, slug, body):
        path = tmp_path / "page.fjson"
        path.write_text(json.dumps(dict(current_page_name=slug, title=slug, body=body)))
        assert ingest_fjson_file(db, path, ds)

    for version in ("3.14", "3.15"):
        ds = db.execute(
            "INSERT INTO doc_sets (version, label, is_default, base_url) VALUES (?, ?, ?, ?)",
            (version, f"Python {version}", int(version == "3.14"),
             f"https://docs.python.org/{version}/"),
        ).lastrowid
        for case in FIXTURE["symbols"]:
            if version not in case["bodies"]:
                continue
            body = case["bodies"][version]
            ingest(ds, case["slug"],
                   f'<section id="{case["anchor"]}"><h2>{case["name"]}</h2>'
                   f'<p>{html.escape(body)}</p></section>')
            db.execute(
                "INSERT INTO symbols (doc_set_id, qualified_name, normalized_name, module, "
                "symbol_type, uri, anchor) VALUES (?, ?, ?, ?, 'method', ?, ?)",
                (ds, case["name"], case["name"].lower(), case["name"].split(".")[0],
                 f'{case["slug"]}.html#{case["anchor"]}', case["anchor"]),
            )
        if version == "3.15":
            parts = []
            for anchor, title, body in FIXTURE["whatsnew"]:
                parts.append(f'<section id="{anchor}"><h2>{title}</h2><p>{body}</p></section>')
            ingest(ds, "whatsnew/3.15", "".join(parts))
    rebuild_fts_indexes(db)
    db.commit()
    yield db
    db.close()


def test_source_pin():
    pin = CPYTHON_DOCS_BUILD_CONFIG["3.15"]
    assert pin["tag"] == FIXTURE["provenance"]["tag"]
    assert pin["sha"] == FIXTURE["provenance"]["commit"]
    assert pin["sphinx_pin"] == "sphinx==8.2.3"


def test_actual_index_and_preserved_default(docs315):
    versions = VersionService(docs315).list_versions().versions
    assert {v.version for v in versions} == {"3.14", "3.15"}
    assert [v.version for v in versions if v.is_default] == ["3.14"]
    result = ContentService(docs315).get_docs("library/base64", anchor="base64.z85encode")
    assert result.version == "3.14"


@pytest.mark.parametrize("case", FIXTURE["symbols"], ids=lambda c: c["name"])
def test_offline_search_content_comparison(docs315, case):
    content = ContentService(docs315)
    result = CompareService(docs315, content).compare(case["name"], "3.14", "3.15")
    assert result.change == case["change"]
    assert result.note is None
    version = "3.14" if case["change"] == "removed" else "3.15"
    hits = SearchService(docs315, {}).search(case["name"], version, kind="symbol").hits
    assert hits
    assert all(hit.version == version for hit in hits)
    assert hits[0].anchor == case["anchor"]
    assert hits[0].uri == f'{case["slug"]}.html#{case["anchor"]}'
    page = content.get_docs(case["slug"], version, case["anchor"])
    assert page.content.strip()
    assert page.version == version


def test_unindexed_explicit315_never_falls_back(docs315):
    docs315.execute("DELETE FROM doc_sets WHERE version='3.15'")
    assert [v.version for v in VersionService(docs315).list_versions().versions] == ["3.14"]
    with pytest.raises(VersionNotFoundError, match="3.15.*not found"):
        SearchService(docs315, {}).search("base64.z85encode", "3.15")
    with pytest.raises(VersionNotFoundError, match="3.15.*not found"):
        ContentService(docs315).get_docs("library/base64", "3.15")
    with pytest.raises(VersionNotFoundError, match="3.15.*not found"):
        CompareService(docs315, ContentService(docs315)).compare("base64.z85encode", "3.14", "3.15")


def test_whatsnew315_gap_free_bounded(docs315):
    service = WhatsNewService(docs315)
    expected = [anchor for anchor, _, body in FIXTURE["whatsnew"] if body]
    anchors = []
    start = 0
    while True:
        page = service.get("3.15", start_index=start, max_sections=2)
        assert len(page.sections) <= 2
        assert len(page.model_dump_json()) <= 20_000
        anchors.extend(section.anchor for section in page.sections)
        for section in page.sections:
            assert ContentService(docs315).get_docs(
                "whatsnew/3.15", "3.15", section.anchor
            ).content.strip()
        if page.next_start_index is None:
            break
        assert page.next_start_index > start
        start = page.next_start_index
    assert anchors == expected
    assert len(set(anchors)) == len(anchors)
