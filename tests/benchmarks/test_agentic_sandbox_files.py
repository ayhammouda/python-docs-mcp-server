"""Static checks of the sandbox's committed files: no Docker and no network."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SANDBOX = REPO_ROOT / "benchmarks" / "sandbox"


def _requirements(path: Path) -> list[str]:
    """Logical requirement lines: continuations joined, comments and blanks dropped."""
    text = path.read_text(encoding="utf-8").replace("\\\n", " ")
    lines = (line.strip() for line in text.splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def test_fetch_requirements_pin_the_researched_release_with_hashes() -> None:
    requirements = _requirements(SANDBOX / "fetch-requirements.txt")
    assert any(line.startswith("mcp-server-fetch==2026.10.19 ") for line in requirements)
    assert requirements and all("--hash=sha256:" in line for line in requirements)
    assert all("==" in line.split()[0] for line in requirements)  # every package pinned
