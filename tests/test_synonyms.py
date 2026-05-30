"""Synonym loading and data integrity tests.

Verifies:
- synonyms.yaml is loadable via importlib.resources (SRVR-12)
- Contains 100+ entries (D-05)
- Data structure is correct (concept -> list of terms)
- Key concepts are present
"""
import importlib.resources
import re
from pathlib import Path

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


def test_yaml_loaded_only_via_safe_load():
    """Lock in the packaged-YAML trust boundary for synonyms.yaml."""
    repo_root = Path(__file__).resolve().parents[1]
    src_root = repo_root / "src"
    scan_roots = (src_root, repo_root / "tests")
    expected_yaml_input = (
        "src/mcp_server_python_docs/data/synonyms.yaml"
    )
    expected_safe_load_sites = {
        "src/mcp_server_python_docs/server.py",
        "src/mcp_server_python_docs/ingestion/sphinx_json.py",
    }

    unsafe_load_call = re.compile(r"\byaml[.]load\s*[(]")
    unsafe_loader_name = re.compile(r"\byaml[.]unsafe_load\b")
    safe_load_call = re.compile(r"\byaml[.]safe_load\s*[(]")

    violations: list[str] = []
    safe_load_sites: set[str] = set()

    for scan_root in scan_roots:
        for source_path in sorted(scan_root.rglob("*.py")):
            relative_path = source_path.relative_to(repo_root).as_posix()
            for line_number, line in enumerate(source_path.read_text().splitlines(), 1):
                if unsafe_load_call.search(line) or unsafe_loader_name.search(line):
                    violations.append(f"{relative_path}:{line_number}: unsafe YAML load")
                if source_path.is_relative_to(src_root) and safe_load_call.search(line):
                    safe_load_sites.add(relative_path)

    yaml_inputs = sorted(
        path.relative_to(repo_root).as_posix()
        for path in src_root.rglob("*")
        if path.suffix in {".yaml", ".yml"}
    )

    assert violations == []
    assert expected_safe_load_sites <= safe_load_sites
    assert yaml_inputs == [expected_yaml_input]
