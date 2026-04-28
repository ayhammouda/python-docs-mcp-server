"""Tests for CI workflow coverage."""
from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
SUPPORTED_VERSION_ARGS = "3.10,3.11,3.12,3.13,3.14"


def test_slow_e2e_workflow_runs_installed_build_index() -> None:
    """Slow E2E workflow should validate installed-package build-index behavior."""
    workflow = PROJECT_ROOT / ".github" / "workflows" / "e2e.yml"

    content = workflow.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in content
    assert "schedule:" in content
    assert 'python-version: ["3.13", "3.14"]' in content
    assert "uv build" in content
    assert "python -m venv" in content
    assert "python -m pip install dist/" in content
    assert (
        f"mcp-server-python-docs build-index --versions {SUPPORTED_VERSION_ARGS}"
        in content
    )
    assert "mcp-server-python-docs doctor" in content
    assert "mcp-server-python-docs validate-corpus" in content
    assert "actions/upload-artifact" in content
