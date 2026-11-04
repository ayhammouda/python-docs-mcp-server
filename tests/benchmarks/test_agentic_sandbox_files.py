"""Static checks of the sandbox's committed files: no Docker and no network."""

from __future__ import annotations

import os
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


PYTHON_IMAGE = (
    "python:3.14.8-slim-trixie"
    "@sha256:fbecd461fc72d21b2143c6eaba25143738079154a6d0ea3da3aec724f50eed4a"
)
UV_IMAGE = (
    "ghcr.io/astral-sh/uv:0.13.4"
    "@sha256:9f130421e930bfbdc563c00740466603f8723158e90297d07957c2294640262b"
)


def test_dockerfile_pins_every_base_by_digest_and_runs_unprivileged() -> None:
    text = (SANDBOX / "Dockerfile").read_text(encoding="utf-8")
    lines = [line.strip() for line in text.splitlines()]
    assert [line for line in lines if line.startswith("FROM ")][0] == f"FROM {PYTHON_IMAGE}"
    copies = [line for line in lines if line.startswith("COPY --from=")]
    assert copies == [f"COPY --from={UV_IMAGE} /uv /uvx /bin/"]
    assert not [line for line in lines if line.lower().startswith("# syntax")]
    assert [line for line in lines if line.startswith("USER ")][-1] == "USER 10001:10001"
    env = " ".join(line for line in lines if line.startswith("ENV "))
    assert not any(name in env for name in ("PROXY", "SSL_CERT", "REQUESTS_CA"))
    for needed in ("uv sync --locked", "command -v cat", "! command -v node"):
        assert needed in text


def test_run_sh_builds_from_git_archive_and_defaults_to_a_dry_run() -> None:
    path = SANDBOX / "run.sh"
    text = path.read_text(encoding="utf-8")
    assert os.access(path, os.X_OK)
    assert "git archive --format=tar HEAD" in text and "set -euo pipefail" in text
    assert "--live" not in text  # live only when the operator passes it
    for banned in ("git status", "printenv", "env |", "set -x", "cat .env"):
        assert banned not in text
    for tool in ("git", "docker", "uv", "cat"):
        assert tool in text.split("for tool in", 1)[1].split(";", 1)[0]
