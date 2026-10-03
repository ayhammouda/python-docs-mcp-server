"""Offline release-note discovery over production-shaped indexed sections."""
from __future__ import annotations

import socket

import pytest

from mcp_server_python_docs.errors import PageNotFoundError, VersionNotFoundError
from mcp_server_python_docs.services.whatsnew import WhatsNewService
from mcp_server_python_docs.storage.db import bootstrap_schema, get_readwrite_connection


@pytest.fixture
def release_db(tmp_path):
    db = get_readwrite_connection(tmp_path / "release.db")
    bootstrap_schema(db)
    cases = {
        "3.12": [
            (1, "what-s-new-in-python-3-12", "What's New In Python 3.12", "Intro"),
            (2, "new-features", "New Features", ""),
            (3, "pep-695-type-parameter-syntax", "PEP 695: Type Parameter Syntax", "Syntax detail"),
            (2, "improved-modules", "Improved Modules", ""),
            (3, "asyncio", "asyncio", "Asyncio improvements"),
            (2, "deprecated", "Deprecated", "D" * 9000),
            (
                3, "pending-removal-in-python-3-14",
                "Pending Removal in Python 3.14", "Future warning",
            ),
            (2, "c-api-changes", "C API Changes", ""),
            (3, "id6", "Deprecated", "C API deprecation"),
            (4, "id7", "Pending Removal in Python 3.14", "C API future warning"),
            (3, "id10", "Removed", "C API removal"),
            (2, "removed", "Removed", ""),
            (3, "imp", "imp", "Removed module"),
        ],
        "3.13": [
            (1, "what-s-new-in-python-3-13", "What's New In Python 3.13", "Intro"),
            (2, "new-modules", "New Modules", "New module summary"),
            (2, "improved-modules", "Improved Modules", ""),
            (3, "asyncio", "asyncio", "Asyncio improvements"),
            (2, "new-deprecations", "New Deprecations", "Deprecation summary"),
            (
                3, "pending-removal-in-python-3-14",
                "Pending Removal in Python 3.14", "Future warning",
            ),
        ],
    }
    for version, sections in cases.items():
        db.execute(
            "INSERT INTO doc_sets (version, label) VALUES (?, ?)",
            (version, f"Python {version}"),
        )
        ds_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        slug = f"whatsnew/{version}"
        db.execute(
            "INSERT INTO documents (doc_set_id, uri, slug, title, content_text, char_count) "
            "VALUES (?, ?, ?, ?, '', 0)",
            (ds_id, f"{slug}.html", slug, f"What's New {version}"),
        )
        doc_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        for ordinal, (level, anchor, heading, body) in enumerate(sections):
            db.execute(
                "INSERT INTO sections (document_id, uri, anchor, heading, level, ordinal, "
                "content_text, char_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (doc_id, f"{slug}.html#{anchor}", anchor, heading, level, ordinal, body, len(body)),
            )
    db.commit()
    yield db
    db.close()


def test_312_hierarchy_empty_headings_and_bounded_body(release_db):
    result = WhatsNewService(release_db).get("3.12")
    assert all(section.body for section in result.sections)
    assert "new-features" not in {section.anchor for section in result.sections}
    kinds = {section.anchor: section.kind for section in result.sections}
    assert kinds["id6"] == "deprecation"
    assert kinds["id7"] == "deprecation"
    assert kinds["pending-removal-in-python-3-14"] == "deprecation"
    assert kinds["id10"] == "removal"
    assert kinds["imp"] == "removal"
    assert kinds["pep-695-type-parameter-syntax"] == "syntax"
    deprecated = next(section for section in result.sections if section.anchor == "deprecated")
    assert len(deprecated.body) < 8000
    assert "Section truncated" in deprecated.body
    assert "get_docs(slug='whatsnew/3.12', version='3.12', anchor='deprecated')" in deprecated.body


def test_313_asyncio_and_filter(release_db):
    service = WhatsNewService(release_db)
    result = service.get("3.13")
    asyncio = next(section for section in result.sections if section.anchor == "asyncio")
    assert asyncio.kind == "new_feature"
    filtered = service.get("3.13", kind="deprecation")
    assert filtered.sections
    assert all(section.kind == "deprecation" for section in filtered.sections)
    assert [section.anchor for section in filtered.sections] == [
        "new-deprecations", "pending-removal-in-python-3-14"
    ]


def test_pagination_counts_filtered_nonempty_sections(release_db):
    service = WhatsNewService(release_db)
    complete = service.get("3.12", kind="deprecation", max_sections=20)
    first = service.get("3.12", kind="deprecation", max_sections=2)
    assert first.next_start_index == 2
    second = service.get("3.12", kind="deprecation", start_index=2, max_sections=2)
    assert [s.anchor for s in first.sections + second.sections] == [
        s.anchor for s in complete.sections
    ]
    assert second.next_start_index is None
    assert service.get("3.12", start_index=100).sections == []


def test_skipped_levels_discard_previous_sibling_category(release_db):
    doc_id = release_db.execute(
        "SELECT id FROM documents WHERE slug = 'whatsnew/3.13'"
    ).fetchone()[0]
    for ordinal, (level, anchor, heading) in enumerate(
        [
            (1, "new-features-skipped", "New Features"),
            (3, "removed-skipped", "Removed"),
            (4, "child-skipped", "Opaque child"),
            (3, "sibling-skipped", "asyncio"),
        ],
        start=100,
    ):
        release_db.execute(
            "INSERT INTO sections (document_id, uri, anchor, heading, level, ordinal, "
            "content_text, char_count) VALUES (?, ?, ?, ?, ?, ?, 'Excerpt', 7)",
            (doc_id, f"whatsnew/3.13.html#{anchor}", anchor, heading, level, ordinal),
        )
    result = WhatsNewService(release_db).get("3.13")
    kinds = {section.anchor: section.kind for section in result.sections}
    assert kinds["removed-skipped"] == "removal"
    assert kinds["child-skipped"] == "removal"
    assert kinds["sibling-skipped"] == "new_feature"


def test_aggregate_budget_preserves_excerpts_and_pagination(release_db):
    doc_id = release_db.execute(
        "SELECT id FROM documents WHERE slug = 'whatsnew/3.13'"
    ).fetchone()[0]
    body = 'A long "release-note" excerpt with \\ escapes. ' * 400
    for ordinal in range(25):
        anchor = f"budget-{ordinal}"
        release_db.execute(
            "INSERT INTO sections (document_id, uri, anchor, heading, level, ordinal, "
            "content_text, char_count) VALUES (?, ?, ?, ?, 2, ?, ?, ?)",
            (doc_id, f"whatsnew/3.13.html#{anchor}", anchor, "Performance", 100 + ordinal,
             body, len(body)),
        )
    service = WhatsNewService(release_db)
    first = service.get("3.13")
    second = service.get("3.13", start_index=first.next_start_index)
    assert len(first.sections) == 20
    assert first.next_start_index == 20
    assert second.next_start_index is None
    assert len(first.model_dump_json()) <= 20_000
    assert len(second.model_dump_json()) <= 20_000
    assert [section.anchor for section in first.sections + second.sections][-25:] == [
        f"budget-{ordinal}" for ordinal in range(25)
    ]
    long_sections = [section for section in first.sections if section.anchor.startswith("budget-")]
    assert all(len(section.body) >= 300 for section in long_sections)
    assert all("get_docs(slug='whatsnew/3.13'" in section.body for section in long_sections)


def test_missing_version_and_missing_release_page(release_db):
    service = WhatsNewService(release_db)
    with pytest.raises(VersionNotFoundError, match="available:.*3.12.*3.13"):
        service.get("3.14")
    release_db.execute("INSERT INTO doc_sets (version, label) VALUES ('3.14', 'Python 3.14')")
    with pytest.raises(PageNotFoundError, match="rebuild the full documentation index"):
        service.get("3.14")


def test_query_never_opens_network(release_db, monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("runtime network call")

    monkeypatch.setattr(socket, "create_connection", reject)
    monkeypatch.setattr(socket.socket, "connect", reject)
    assert WhatsNewService(release_db).get("3.12").sections


def test_stdio_tool_registration_and_round_trip(tmp_path):
    """Real JSON-RPC transport exposes the seventh tool and structured result."""
    from mcp_server_python_docs.storage.db import get_readwrite_connection
    from tests.test_stdio_smoke import (
        _assert_protocol_on_stdout_only,
        _create_test_index,
        _find_response,
        _isolated_cache_env,
        _make_notification,
        _make_request,
        _run_server_until_responses,
    )

    env, cache_dir = _isolated_cache_env(tmp_path)
    db_path = _create_test_index(cache_dir)
    db = get_readwrite_connection(db_path)
    db.execute(
        "INSERT INTO documents (doc_set_id, uri, slug, title, content_text, char_count) "
        "VALUES (1, 'whatsnew/3.13.html', 'whatsnew/3.13', 'Whats New', '', 0)"
    )
    doc_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute(
        "INSERT INTO sections (document_id, uri, anchor, heading, level, ordinal, "
        "content_text, char_count) VALUES (?, 'whatsnew/3.13.html#asyncio', "
        "'asyncio', 'asyncio', 2, 0, 'New asyncio behavior', 20)",
        (doc_id,),
    )
    db.commit()
    db.close()
    stdin_data = (
        _make_request("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "test", "version": "0.1"},
        }, req_id=1)
        + _make_notification("notifications/initialized")
        + _make_request("tools/list", {}, req_id=2)
        + _make_request("tools/call", {
            "name": "whatsnew_for_version", "arguments": {"version": "3.13"}
        }, req_id=3)
    )
    responses = _assert_protocol_on_stdout_only(_run_server_until_responses(stdin_data, env))
    listed = _find_response(responses, 2)
    assert listed is not None
    assert [tool["name"] for tool in listed["result"]["tools"]] == [
        "search_docs", "get_docs", "lookup_package_docs", "list_versions",
        "detect_python_version", "compare_versions", "whatsnew_for_version",
    ]
    called = _find_response(responses, 3)
    assert called is not None
    assert called["result"]["structuredContent"]["sections"][0]["anchor"] == "asyncio"
