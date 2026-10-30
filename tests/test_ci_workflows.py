"""Tests for CI workflow coverage."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).parent.parent
# Keep this literal independent of SUPPORTED_DOC_VERSIONS_CSV so the test catches
# workflow drift even if the application constant changes at the same time.
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
        f"python-docs-mcp-server build-index --versions {SUPPORTED_VERSION_ARGS}"
        in content
    )
    assert "python-docs-mcp-server doctor" in content
    assert "python-docs-mcp-server validate-corpus" in content
    assert "actions/upload-artifact" in content


RELEASE = PROJECT_ROOT / ".github" / "workflows" / "release.yml"
DIST_ID = "${{ needs.build.outputs.dist-id }}"
DIST_SHA256 = "${{ needs.build.outputs.dist-sha256 }}"
SHIPPING = (
    "actions/attest-build-provenance@",
    "pypa/gh-action-pypi-publish@",
    "softprops/action-gh-release@",
)


def release_jobs() -> dict:
    return yaml.safe_load(RELEASE.read_text(encoding="utf-8"))["jobs"]


def step_id(output: str, suffix: str) -> str:
    """`${{ steps.X.outputs.<suffix> }}` -> X."""
    return output.removeprefix("${{ steps.").removesuffix(f".outputs.{suffix} }}}}")


def test_release_build_exports_the_dist_id_and_checksums() -> None:
    build = release_jobs()["build"]
    ids = [step.get("id") for step in build["steps"]]
    upload = ids.index(step_id(build["outputs"]["dist-id"], "artifact-id"))
    sums = ids.index(step_id(build["outputs"]["dist-sha256"], "dist-sha256"))
    assert build["steps"][upload]["uses"].startswith("actions/upload-artifact@")
    assert build["steps"][upload]["with"] == {
        "name": "dist",
        "path": "dist/",
        "if-no-files-found": "error",
    }
    assert build["steps"][sums]["run"].startswith("set -o pipefail\n")
    assert "sha256sum dist/* | LC_ALL=C sort" in build["steps"][sums]["run"]
    assert sums == upload - 1  # no step runs between checksum and upload
    for i in (sums, upload):
        assert not {"if", "continue-on-error", "shell"} & build["steps"][i].keys()


@pytest.mark.parametrize("job_name", ["publish", "github-release"])
def test_release_ships_only_the_bytes_the_build_checksummed(job_name: str) -> None:
    job = release_jobs()[job_name]
    steps = job["steps"]
    assert not {"if", "continue-on-error", "defaults", "container"} & job.keys()
    assert "build" in job["needs"]
    downloads = [i for i, s in enumerate(steps) if "download-artifact@" in s.get("uses", "")]
    assert downloads == [0]
    for index in downloads:
        assert steps[index]["with"] == {"artifact-ids": DIST_ID, "path": "dist/"}
    verify = [i for i, s in enumerate(steps) if s.get("env", {}).get("EXPECTED") == DIST_SHA256]
    assert len(verify) == 1
    assert "sha256sum dist/* | LC_ALL=C sort" in steps[verify[0]]["run"]
    assert steps[verify[0]]["run"] == release_scripts()[1]  # one script for both jobs
    shipping = [i for i, s in enumerate(steps) if s.get("uses", "").startswith(SHIPPING)]
    assert shipping
    assert max(downloads) < verify[0] < min(shipping)
    assert verify[0] == max(downloads) + 1
    assert list(range(verify[0] + 1, len(steps))) == shipping
    assert not any({"if", "continue-on-error"} & s.keys() for s in steps[max(downloads):])
    assert "shell" not in steps[verify[0]]
    assert steps[verify[0]]["run"].startswith("set -o pipefail\n")


def test_release_keeps_trusted_publishing_and_the_named_e2e_input() -> None:
    jobs = release_jobs()
    assert jobs["publish"]["environment"] == "pypi"
    assert jobs["publish"]["permissions"]["id-token"] == "write"
    assert "uses" not in jobs["publish"]
    assert jobs["integration"]["with"] == {"artifact-name": "dist"}


def run_step(script: str, cwd: Path, **env: str) -> subprocess.CompletedProcess[str]:
    """Run a workflow `run:` block the way GitHub runs a step without a shell: key."""
    return subprocess.run(
        ["bash", "-e", "-c", script],
        cwd=cwd,
        env={"PATH": os.environ["PATH"], **env},
        capture_output=True,
        text=True,
    )


def release_scripts() -> tuple[str, str]:
    jobs = release_jobs()
    record = next(s for s in jobs["build"]["steps"] if s.get("id") == "dist-sha256")["run"]
    verify = next(s for s in jobs["publish"]["steps"] if "EXPECTED" in s.get("env", {}))["run"]
    return record, verify


needs_sha256sum = pytest.mark.skipif(shutil.which("sha256sum") is None, reason="no sha256sum")


@needs_sha256sum
@pytest.mark.parametrize(
    "tamper", ["none", "extra_file", "changed_byte", "missing_file", "no_checksums"]
)
def test_release_verify_step_refuses_any_change_to_dist(tmp_path: Path, tamper: str) -> None:
    record, verify = release_scripts()
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "pkg-1.0-py3-none-any.whl").write_bytes(b"wheel")
    (dist / "pkg-1.0.tar.gz").write_bytes(b"sdist")
    output = tmp_path / "github_output"
    assert run_step(record, tmp_path, GITHUB_OUTPUT=str(output)).returncode == 0
    lines = output.read_text().splitlines()
    assert lines[0] == "dist-sha256<<DIST_SHA256_EOF" and lines[-1] == "DIST_SHA256_EOF"
    expected = "" if tamper == "no_checksums" else "\n".join(lines[1:-1])
    if tamper == "extra_file":
        (dist / "evil-1.0-py3-none-any.whl").write_bytes(b"evil")
    elif tamper == "changed_byte":
        (dist / "pkg-1.0.tar.gz").write_bytes(b"sdisT")
    elif tamper == "missing_file":
        (dist / "pkg-1.0.tar.gz").unlink()
    result = run_step(verify, tmp_path, EXPECTED=expected)
    assert result.returncode == (0 if tamper == "none" else 1), result.stdout
    assert ("build exported no checksums" in result.stdout) == (tamper == "no_checksums")


@needs_sha256sum
def test_release_checksum_step_fails_on_an_empty_dist(tmp_path: Path) -> None:
    record, _ = release_scripts()
    (tmp_path / "dist").mkdir()
    output = tmp_path / "github_output"
    assert run_step(record, tmp_path, GITHUB_OUTPUT=str(output)).returncode != 0


def test_build_constraints_pin_every_build_requirement() -> None:
    import tomllib

    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    text = (PROJECT_ROOT / ".github" / "build-constraints.txt").read_text(encoding="utf-8")
    pinned = {
        line.split("==")[0]
        for line in text.splitlines()
        if "==" in line and not line.startswith("#")
    }
    requires = pyproject["build-system"]["requires"]
    names = {re.split(r"[\s<>=!~;\[]", r, maxsplit=1)[0].lower() for r in requires}
    assert names <= pinned


def test_release_build_pins_hatchling_and_its_dependencies_by_hash() -> None:
    build = next(s for s in release_jobs()["build"]["steps"] if s.get("name") == "Build package")
    assert build["run"] == (
        "uv build --build-constraint .github/build-constraints.txt --require-hashes"
    )
    text = (PROJECT_ROOT / ".github" / "build-constraints.txt").read_text(encoding="utf-8")
    hashed = re.findall(
        r"^([a-z0-9_.-]+)==\S+ \\\n(?:    --hash=sha256:[0-9a-f]{64}(?: \\)?\n)+", text, re.M
    )
    pinned = [line for line in text.splitlines() if "==" in line and not line.startswith("#")]
    assert "hatchling" in hashed
    assert len(hashed) == len(pinned) >= 6


def test_release_build_job_never_builds_the_project_with_an_unpinned_backend() -> None:
    steps = release_jobs()["build"]["steps"]
    names = [s.get("name") for s in steps]
    commands = [line.strip() for s in steps for line in s.get("run", "").splitlines()]
    assert [c for c in commands if c.startswith("uv sync")] == [
        "uv sync --locked --dev --no-install-project --no-build"
    ]
    uv_runs = [c for c in commands if "uv run" in c]
    assert uv_runs and all("--no-sync" in c for c in uv_runs)  # a re-sync rebuilds editable
    assert [c for c in commands if c.startswith("uv build")] == [
        "uv build --build-constraint .github/build-constraints.txt --require-hashes"
    ]
    assert [c for c in commands if c.startswith("uv pip install")] == [
        "uv pip install --no-deps dist/*.whl"
    ]
    build = names.index("Build package")
    assert steps[build + 1]["run"] == "uv pip install --no-deps dist/*.whl"
    tests = names.index("Run tests")
    assert build < tests
    # tests/test_packaging.py builds a wheel too; pin its backend the same way
    assert steps[tests]["env"] == {
        "UV_BUILD_CONSTRAINT": ".github/build-constraints.txt",
        "UV_REQUIRE_HASHES": "1",
    }
