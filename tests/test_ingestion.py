"""Tests for Sphinx JSON ingestion module.

Covers fjson parsing (INGR-C-04), HTML-to-markdown (INGR-C-05),
per-document failure isolation (INGR-C-06), code block extraction
(INGR-C-07), FTS population (INGR-C-08), and synonym population
(INGR-C-09).
"""
from __future__ import annotations

import io
import os
import runpy
import shutil
import sys
import types

import pytest

from mcp_server_python_docs.errors import IngestionError
from mcp_server_python_docs.ingestion.cpython_versions import (
    CPYTHON_DOCS_BUILD_CONFIG,
    SUPPORTED_DOC_VERSIONS,
    SUPPORTED_DOC_VERSIONS_CSV,
)
from mcp_server_python_docs.ingestion.sphinx_json import (
    build_sphinx_bootstrap_requirements,
    build_sphinx_json_command,
    extract_code_blocks,
    extract_sections,
    html_to_markdown,
    ingest_fjson_file,
    ingest_sphinx_json_dir,
    make_sphinx_json_env,
    parse_fjson,
    populate_synonyms,
    rebuild_fts_indexes,
    write_json_build_requirements,
    write_sphinx_json_sitecustomize,
)


class TestCPythonVersionConfig:
    def test_supports_python_3_10_through_3_14(self):
        assert SUPPORTED_DOC_VERSIONS == ("3.10", "3.11", "3.12", "3.13", "3.14")
        assert SUPPORTED_DOC_VERSIONS_CSV == "3.10,3.11,3.12,3.13,3.14"

    def test_supported_versions_have_pinned_docs_build_config(self):
        assert set(CPYTHON_DOCS_BUILD_CONFIG) == set(SUPPORTED_DOC_VERSIONS)

        for version in SUPPORTED_DOC_VERSIONS:
            config = CPYTHON_DOCS_BUILD_CONFIG[version]
            assert config["tag"].startswith(f"v{version}.")
            assert config["sphinx_pin"].startswith("sphinx")


class TestJsonBuildRequirements:
    def test_omits_html_only_sphinx_extensions(self, tmp_path):
        source = tmp_path / "requirements.txt"
        output = tmp_path / "json-requirements.txt"
        source.write_text(
            "\n".join([
                "# CPython docs requirements",
                "sphinx==8.2.3",
                "sphinxext-opengraph>=0.9.1",
                "python-docs-theme>=2025.8",
                "sphinx-notfound-page==1.0.0",
                "-c constraints.txt",
                "blurb",
            ])
            + "\n",
            encoding="utf-8",
        )

        omitted = write_json_build_requirements(source, output)

        assert omitted == [
            "sphinxext-opengraph",
            "python-docs-theme",
            "sphinx-notfound-page",
        ]
        assert output.read_text(encoding="utf-8") == (
            "# CPython docs requirements\n"
            "sphinx==8.2.3\n"
            "-c constraints.txt\n"
            "blurb\n"
        )

    def test_matches_requirement_names_case_and_separator_insensitively(self, tmp_path):
        source = tmp_path / "requirements.txt"
        output = tmp_path / "json-requirements.txt"
        source.write_text(
            "SphinxExt.OpenGraph>=0.9; python_version >= '3.12'\n"
            "SPHINX_NOTFOUND_PAGE==1.0\n",
            encoding="utf-8",
        )

        omitted = write_json_build_requirements(source, output)

        assert omitted == ["sphinxext-opengraph", "sphinx-notfound-page"]
        assert output.read_text(encoding="utf-8") == ""


class TestSphinxJsonSitecustomize:
    def test_writes_translation_proxy_json_patch(self, tmp_path):
        output_dir = tmp_path / "compat"

        sitecustomize = write_sphinx_json_sitecustomize(output_dir)

        assert sitecustomize == output_dir / "sitecustomize.py"
        content = sitecustomize.read_text(encoding="utf-8")
        assert "_TranslationProxy" in content
        assert "SphinxJSONEncoder.default" in content

    def test_writes_imghdr_compat_module(self, tmp_path):
        output_dir = tmp_path / "compat"

        write_sphinx_json_sitecustomize(output_dir)

        content = (output_dir / "imghdr.py").read_text(encoding="utf-8")
        assert "tests = []" in content
        assert "stdlib imghdr extension hook" in content
        assert "def what" in content
        assert "jpeg" in content

    def test_imghdr_compat_module_detects_sphinx_image_formats(self, tmp_path):
        output_dir = tmp_path / "compat"
        write_sphinx_json_sitecustomize(output_dir)
        namespace = runpy.run_path(str(output_dir / "imghdr.py"))

        what = namespace["what"]

        assert what(io.BytesIO(b"\xff\xd8\xff\xe0")) == "jpeg"
        assert what(io.BytesIO(b"\x89PNG\r\n\x1a\nextra")) == "png"
        assert what(io.BytesIO(b"GIF89aextra")) == "gif"

    def test_imghdr_compat_module_preserves_tests_hook(self, tmp_path):
        output_dir = tmp_path / "compat"
        write_sphinx_json_sitecustomize(output_dir)
        namespace = runpy.run_path(str(output_dir / "imghdr.py"))

        tests = namespace["tests"]
        tests.append(lambda header, _file: "bmp" if header.startswith(b"BM") else None)

        assert namespace["what"](io.BytesIO(b"BMfake")) == "bmp"

    def test_translation_proxy_patch_stringifies_proxy_objects(
        self, tmp_path, monkeypatch
    ):
        output_dir = tmp_path / "compat"
        sitecustomize = write_sphinx_json_sitecustomize(output_dir)

        class ProbeEncoder:
            def default(self, obj):
                raise TypeError(type(obj).__name__)

        jsonimpl = types.ModuleType("sphinxcontrib.serializinghtml.jsonimpl")
        jsonimpl.SphinxJSONEncoder = ProbeEncoder

        serializinghtml = types.ModuleType("sphinxcontrib.serializinghtml")
        serializinghtml.jsonimpl = jsonimpl

        sphinxcontrib = types.ModuleType("sphinxcontrib")
        sphinxcontrib.serializinghtml = serializinghtml

        monkeypatch.setitem(sys.modules, "sphinxcontrib", sphinxcontrib)
        monkeypatch.setitem(
            sys.modules, "sphinxcontrib.serializinghtml", serializinghtml
        )
        monkeypatch.setitem(
            sys.modules, "sphinxcontrib.serializinghtml.jsonimpl", jsonimpl
        )

        runpy.run_path(str(sitecustomize))

        translation_proxy = type(
            "_TranslationProxy",
            (),
            {"__str__": lambda self: "translated text"},
        )()

        assert ProbeEncoder().default(translation_proxy) == "translated text"
        with pytest.raises(TypeError):
            ProbeEncoder().default(object())

    def test_sphinx_json_env_prepends_compat_dir(self, tmp_path):
        compat_dir = tmp_path / "compat"

        env = make_sphinx_json_env(compat_dir, {"PYTHONPATH": "/existing"})

        assert env["PYTHONPATH"] == f"{compat_dir}{os.pathsep}/existing"

    def test_sphinx_json_env_sets_compat_dir_without_existing_pythonpath(self, tmp_path):
        compat_dir = tmp_path / "compat"

        env = make_sphinx_json_env(compat_dir, {})

        assert env["PYTHONPATH"] == str(compat_dir)


class TestSphinxJsonCommand:
    def test_bootstrap_requirements_include_setuptools_before_sphinx(self):
        requirements = build_sphinx_bootstrap_requirements("sphinx==3.4.3")

        assert requirements == ["setuptools<70", "sphinx==3.4.3"]

    def test_bootstrap_requirements_include_setuptools_for_sphinx_4(self):
        requirements = build_sphinx_bootstrap_requirements("Sphinx < 5")

        assert requirements == ["setuptools<70", "Sphinx < 5"]

    def test_bootstrap_requirements_skip_setuptools_for_modern_sphinx(self):
        requirements = build_sphinx_bootstrap_requirements("sphinx~=8.2.0")

        assert requirements == ["sphinx~=8.2.0"]

    def test_build_command_uses_json_builder_and_classic_theme(self, tmp_path):
        sphinx_build = tmp_path / "bin" / "sphinx-build"
        doc_dir = tmp_path / "cpython" / "Doc"
        json_out = doc_dir / "build" / "json"

        command = build_sphinx_json_command(sphinx_build, doc_dir, json_out)

        assert command == [
            str(sphinx_build),
            "-b",
            "json",
            "-D",
            "html_theme=classic",
            "-j",
            "auto",
            str(doc_dir),
            str(json_out),
        ]


# ── fjson parsing tests (INGR-C-04) ──


class TestParseFjson:
    def test_parse_fjson_valid(self, sample_fjson_path):
        """parse_fjson loads a valid .fjson file."""
        data = parse_fjson(sample_fjson_path)
        assert "body" in data
        assert "title" in data
        assert "current_page_name" in data
        assert data["current_page_name"] == "library/asyncio-task"

    def test_parse_fjson_broken(self, broken_fjson_path):
        """parse_fjson raises IngestionError on invalid JSON."""
        with pytest.raises(IngestionError):
            parse_fjson(broken_fjson_path)


# ── HTML-to-markdown tests (INGR-C-05) ──


class TestHtmlToMarkdown:
    def test_headings(self):
        """html_to_markdown converts HTML headings."""
        html = '<h2 id="foo">Section Title</h2><p>Content here.</p>'
        result = html_to_markdown(html)
        assert "Section Title" in result
        assert "Content here." in result

    def test_code_preserved(self):
        """html_to_markdown preserves inline code."""
        html = "<p>Use <code>asyncio.run()</code> to start.</p>"
        result = html_to_markdown(html)
        assert "asyncio.run()" in result

    def test_links(self):
        """html_to_markdown converts HTML links."""
        html = '<p>See <a href="https://docs.python.org">docs</a>.</p>'
        result = html_to_markdown(html)
        assert "docs" in result

    def test_empty_input(self):
        """html_to_markdown returns empty string for empty input."""
        assert html_to_markdown("") == ""
        assert html_to_markdown("   ") == ""

    def test_paragraph(self):
        """html_to_markdown handles plain paragraphs."""
        html = "<p>Hello world.</p>"
        result = html_to_markdown(html)
        assert "Hello world." in result


# ── Section extraction tests (INGR-C-04) ──


class TestExtractSections:
    def test_from_fixture(self, sample_fjson_path):
        """extract_sections parses headings with id attributes from fixture HTML."""
        data = parse_fjson(sample_fjson_path)
        sections = extract_sections(data["body"], "library/asyncio-task.html")

        # Should find 3 headings: h1 (module-asyncio), h2 (TaskGroup), h3 (create_task)
        assert len(sections) >= 3

        # Check first section
        assert sections[0]["anchor"] == "module-asyncio"
        assert "asyncio" in sections[0]["heading"]
        assert sections[0]["level"] == 1
        assert sections[0]["ordinal"] == 0

        # Check second section
        assert sections[1]["anchor"] == "asyncio.TaskGroup"
        assert "TaskGroup" in sections[1]["heading"]
        assert sections[1]["level"] == 2

        # Check third section
        assert sections[2]["anchor"] == "asyncio.TaskGroup.create_task"
        assert "create_task" in sections[2]["heading"]
        assert sections[2]["level"] == 3

        # All sections should have non-empty content (markdown, not HTML)
        for section in sections:
            assert "<" not in section["content_text"] or "&" in section["content_text"]

    def test_empty_body(self):
        """extract_sections handles empty HTML body gracefully."""
        sections = extract_sections("", "test.html")
        assert len(sections) == 0

    def test_no_headings(self):
        """extract_sections creates single section for body with no headed content."""
        sections = extract_sections("<p>Just a paragraph.</p>", "test.html")
        assert len(sections) == 1
        assert sections[0]["anchor"] == ""
        assert "Just a paragraph" in sections[0]["content_text"]

    def test_ordinals_sequential(self, sample_fjson_path):
        """Section ordinals are sequential starting from 0."""
        data = parse_fjson(sample_fjson_path)
        sections = extract_sections(data["body"], "test.html")
        ordinals = [s["ordinal"] for s in sections]
        assert ordinals == list(range(len(sections)))

    def test_uri_includes_anchor(self, sample_fjson_path):
        """Section URIs include the anchor fragment."""
        data = parse_fjson(sample_fjson_path)
        sections = extract_sections(data["body"], "library/asyncio-task.html")
        for section in sections:
            if section["anchor"]:
                assert "#" in section["uri"]
                assert section["anchor"] in section["uri"]


# ── Code block extraction tests (INGR-C-07) ──


class TestExtractCodeBlocks:
    def test_from_fixture(self, sample_fjson_path):
        """extract_code_blocks finds both doctest and example blocks."""
        data = parse_fjson(sample_fjson_path)
        blocks = extract_code_blocks(data["body"])

        # Should find at least 2 code blocks
        assert len(blocks) >= 2

        # Check we have both types
        doctests = [b for b in blocks if b["is_doctest"] == 1]
        examples = [b for b in blocks if b["is_doctest"] == 0]
        assert len(doctests) >= 1, "Should have at least one doctest (highlight-pycon)"
        assert len(examples) >= 1, "Should have at least one example (highlight-python3)"

        # All code blocks should have non-empty code
        for block in blocks:
            assert block["code"].strip()
            assert block["language"] == "python"

    def test_empty_body(self):
        """extract_code_blocks returns empty list for body with no code."""
        blocks = extract_code_blocks("<p>No code here.</p>")
        assert blocks == []

    def test_section_anchor_association(self, sample_fjson_path):
        """Code blocks are associated with their nearest heading."""
        data = parse_fjson(sample_fjson_path)
        blocks = extract_code_blocks(data["body"])
        # At least one block should have a section_anchor
        anchored = [b for b in blocks if b["section_anchor"]]
        assert len(anchored) >= 1


# ── Per-document failure isolation tests (INGR-C-06) ──


class TestFailureIsolation:
    def test_broken_fjson_does_not_abort(self, populated_db, broken_fjson_path):
        """A broken .fjson file is logged and skipped, not fatal."""
        result = ingest_fjson_file(populated_db, broken_fjson_path, doc_set_id=1)
        assert result is False
        # No documents should have been inserted
        count = populated_db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        assert count == 0

    def test_valid_fjson_ingests(self, populated_db, sample_fjson_path):
        """A valid .fjson file is ingested successfully."""
        result = ingest_fjson_file(populated_db, sample_fjson_path, doc_set_id=1)
        assert result is True
        count = populated_db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        assert count == 1

    def test_ingest_mixed_directory(self, populated_db, tmp_path, fixtures_dir):
        """ingest_sphinx_json_dir handles mix of valid and broken files."""
        # Copy fixtures to tmp_path
        test_dir = tmp_path / "json_out"
        test_dir.mkdir()
        shutil.copy(fixtures_dir / "sample_library.fjson", test_dir / "asyncio.fjson")
        shutil.copy(fixtures_dir / "sample_broken.fjson", test_dir / "broken.fjson")

        success, failures = ingest_sphinx_json_dir(populated_db, test_dir, doc_set_id=1)
        assert success == 1
        assert failures == 1

        # Only the valid document should be in the DB
        count = populated_db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        assert count == 1


# ── FTS population tests (INGR-C-08) ──


class TestFTSPopulation:
    def test_sections_fts_populated(self, populated_db, sample_fjson_path):
        """sections_fts is searchable after ingestion + rebuild."""
        ingest_fjson_file(populated_db, sample_fjson_path, doc_set_id=1)
        rebuild_fts_indexes(populated_db)

        row = populated_db.execute(
            "SELECT 1 FROM sections_fts WHERE sections_fts MATCH '\"asyncio\"' LIMIT 1"
        ).fetchone()
        assert row is not None

    def test_examples_fts_populated(self, populated_db, sample_fjson_path):
        """examples_fts is searchable after ingestion + rebuild."""
        ingest_fjson_file(populated_db, sample_fjson_path, doc_set_id=1)
        rebuild_fts_indexes(populated_db)

        row = populated_db.execute(
            "SELECT 1 FROM examples_fts WHERE examples_fts MATCH '\"asyncio\"' LIMIT 1"
        ).fetchone()
        assert row is not None


# ── Synonym population tests (INGR-C-09) ──


class TestSynonymPopulation:
    def test_synonyms_populated(self, test_db):
        """populate_synonyms loads from synonyms.yaml into DB."""
        count = populate_synonyms(test_db)
        assert count >= 10

        # Verify at least one known synonym exists
        row = test_db.execute(
            "SELECT expansion FROM synonyms LIMIT 1"
        ).fetchone()
        assert row is not None
        assert row[0]  # Non-empty expansion


# ── Document content tests ──


class TestDocumentContent:
    def test_document_has_markdown_not_html(self, populated_db, sample_fjson_path):
        """Ingested document content_text is markdown, not raw HTML (INGR-C-05)."""
        ingest_fjson_file(populated_db, sample_fjson_path, doc_set_id=1)
        row = populated_db.execute(
            "SELECT content_text FROM documents LIMIT 1"
        ).fetchone()
        content = row[0]
        # Should not contain HTML tags (except possibly in code blocks)
        assert "<h1" not in content
        assert "<h2" not in content
        assert "<div" not in content

    def test_sections_have_correct_hierarchy(self, populated_db, sample_fjson_path):
        """Sections preserve heading hierarchy levels."""
        ingest_fjson_file(populated_db, sample_fjson_path, doc_set_id=1)
        rows = populated_db.execute(
            "SELECT anchor, heading, level FROM sections ORDER BY ordinal"
        ).fetchall()
        assert len(rows) >= 3
        levels = [r[2] for r in rows]
        # First should be h1, second h2, third h3
        assert levels[0] == 1
        assert levels[1] == 2
        assert levels[2] == 3

    def test_examples_classified_correctly(self, populated_db, sample_fjson_path):
        """Examples are correctly classified as doctest or standalone."""
        ingest_fjson_file(populated_db, sample_fjson_path, doc_set_id=1)
        rows = populated_db.execute(
            "SELECT code, is_doctest FROM examples ORDER BY ordinal"
        ).fetchall()
        assert len(rows) >= 2

        doctests = [r for r in rows if r[1] == 1]
        examples = [r for r in rows if r[1] == 0]
        assert len(doctests) >= 1
        assert len(examples) >= 1
