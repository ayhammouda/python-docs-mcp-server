"""Publishing must reject off-main, unverified, spoofed and failing revisions."""

import runpy
from pathlib import Path

import pytest

GATE = runpy.run_path(str(Path(__file__).parents[1] / ".github/scripts/release_gate.py"))


def test_release_eligibility_rejects_incomplete_or_wrong_identity_evidence():
    sha, head = "a" * 40, "b" * 40
    checks = [
        {"id": n, "name": name, "app": {"id": 15368}, "conclusion": "success"}
        for n, name in enumerate(GATE["REQUIRED"])
    ]
    review = {
        "id": 99,
        "name": "Independent verification",
        "app": {"id": 123},
        "conclusion": "success",
        "external_id": "c" * 40 + ":" + head,
    }
    state = {
        f"git/commits/{sha}": {"parents": [{"sha": "c" * 40}]},
        f"compare/{sha}...main": {"status": "ahead"},
        f"commits/{sha}/pulls?per_page=100": [
            {
                "merged_at": "2026-10-01",
                "base": {"ref": "main"},
                "merge_commit_sha": sha,
                "head": {"sha": head},
            }
        ],
        f"commits/{head}/check-runs?per_page=100": {"check_runs": [review]},
        f"commits/{sha}/check-runs?per_page=100": {"check_runs": checks},
    }
    gate = GATE["require_eligible"]
    gate(state.__getitem__, sha, 123)
    with pytest.raises(ValueError):
        gate(state.__getitem__, sha, 0)
    with pytest.raises(ValueError, match="independent"):
        gate(state.__getitem__, sha, 456)
    state[f"compare/{sha}...main"]["status"] = "diverged"
    with pytest.raises(ValueError, match="not on main"):
        gate(state.__getitem__, sha, 123)
    state[f"compare/{sha}...main"]["status"] = "ahead"
    review["conclusion"] = "failure"
    with pytest.raises(ValueError, match="independent"):
        gate(state.__getitem__, sha, 123)
    review["conclusion"] = "success"
    review["external_id"] = "d" * 40 + ":" + head
    with pytest.raises(ValueError, match="independent"):
        gate(state.__getitem__, sha, 123)
    review["external_id"] = "c" * 40 + ":" + head
    checks.pop()
    with pytest.raises(ValueError, match="main checks"):
        gate(state.__getitem__, sha, 123)
