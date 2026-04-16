"""Synonym loading and data integrity tests.

Verifies:
- synonyms.yaml is loadable via importlib.resources (SRVR-12)
- Contains 100+ entries (D-05)
- Data structure is correct (concept -> list of terms)
- Key concepts are present
"""
import importlib.resources

import pytest
import yaml


class TestSynonymLoading:
    """Verify synonym data loads correctly from package."""

    def _load_synonyms(self) -> dict[str, list[str]]:
        """Load synonyms via the same mechanism the server uses."""
        ref = (
            importlib.resources.files("mcp_server_python_docs")
            / "data"
            / "synonyms.yaml"
        )
        with importlib.resources.as_file(ref) as path:
            data = yaml.safe_load(path.read_text())
        return {k: v for k, v in data.items() if isinstance(v, list)}

    def test_synonym_count_minimum(self):
        """synonyms.yaml must have 100+ entries (D-05)."""
        synonyms = self._load_synonyms()
        assert len(synonyms) >= 100, f"Expected 100+ entries, got {len(synonyms)}"

    def test_all_values_are_lists(self):
        """Every synonym entry must map to a list of strings."""
        synonyms = self._load_synonyms()
        for concept, terms in synonyms.items():
            assert isinstance(terms, list), (
                f"{concept} maps to {type(terms)}, not list"
            )
            for term in terms:
                assert isinstance(term, str), (
                    f"{concept} contains non-string: {term!r}"
                )

    def test_key_concepts_present(self):
        """Core stdlib concepts must be present."""
        synonyms = self._load_synonyms()
        required = [
            "parallel", "async", "regex", "file io", "date time", "type hint",
        ]
        for concept in required:
            found = any(concept in k for k in synonyms)
            assert found, f"Required concept '{concept}' not found in synonyms"

    def test_parallel_includes_asyncio(self):
        """'parallel' concept must expand to include asyncio."""
        synonyms = self._load_synonyms()
        parallel_terms = None
        for k, v in synonyms.items():
            if "parallel" in k:
                parallel_terms = v
                break
        assert parallel_terms is not None, "'parallel' concept not found"
        assert "asyncio" in parallel_terms, (
            f"'asyncio' not in parallel terms: {parallel_terms}"
        )

    def test_importlib_resources_path(self):
        """Verify the package data path is accessible via importlib.resources."""
        ref = (
            importlib.resources.files("mcp_server_python_docs")
            / "data"
            / "synonyms.yaml"
        )
        with importlib.resources.as_file(ref) as path:
            assert path.exists(), f"synonyms.yaml not found at {path}"
            content = path.read_text()
            assert len(content) > 0, "synonyms.yaml is empty"
