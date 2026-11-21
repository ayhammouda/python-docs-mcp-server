"""Fail closed unless this tag names an independently verified main commit.

The owner's verification record is a process tripwire, not a security boundary;
see the 2026-10-30 amendment in ``AGENT-EXECUTION-PIPELINE.md``.
"""

import json
import os
import re
import urllib.request
from pathlib import Path

RECOVERY_TAG = "v0.5.0-recovery.1"
RECOVERY_TARGET = "v0.5.0"
RECOVERY_SHA = "3a48f832404a11fb18de7d56c27c8e84ba57c2cc"

REQUIRED = {
    "Test (Python 3.12, ubuntu-latest)",
    "Test (Python 3.13, ubuntu-latest)",
    "Test (Python 3.14, ubuntu-latest)",
    "Test (Python 3.12, macos-latest)",
    "Test (Python 3.13, macos-latest)",
    "Test (Python 3.14, macos-latest)",
    "Dependency audit",
    "Analyze",
    "Installed package smoke",
}
# First line of the owner's PR comment that records an independent verification.
RECORD = re.compile(r"Independent verification: (PASS|FAIL)")


def verdict(comment, owner: str, head: str, merged_at: str) -> str | None:
    """Return PASS or FAIL for an owner record of ``head``, else None."""
    lines = comment["body"].replace("\r\n", "\n").split("\n")
    if (
        (comment["user"] or {}).get("login") != owner
        # Verification gates the merge; a record written afterwards does not count.
        or comment["created_at"] > merged_at
        or f"head: {head}" not in lines
    ):
        return None
    # An edit could revive or retarget a decision, so any edited owner record fails closed.
    if comment["updated_at"] != comment["created_at"]:
        return "FAIL"
    match = RECORD.fullmatch(lines[0])
    return match[1] if match else None


def require_eligible(api, sha: str, owner: str, *, required=None) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or not owner:
        raise ValueError("Exact commit and repository owner are required")
    if api(f"compare/{sha}...main")["status"] not in {"ahead", "identical"}:
        raise ValueError("Release commit is not on main")
    prs = [
        p
        for p in api(f"commits/{sha}/pulls?per_page=100")
        if p["merged_at"] and p["merge_commit_sha"] == sha and p["base"]["ref"] == "main"
    ]
    if len(prs) != 1:
        raise ValueError("Release must be a uniquely identified merged PR")
    head = prs[0]["head"]["sha"]
    head_checks = api(f"commits/{head}/check-runs?per_page=100")["check_runs"]
    codeql = [c for c in head_checks if c["name"] == "CodeQL" and c["app"]["id"] == 57789]
    if not codeql or max(codeql, key=lambda c: c["id"])["conclusion"] != "success":
        raise ValueError("Missing or failing CodeQL findings check on merged PR head")
    # The main ruleset allows only squash merges (allowed_merge_methods: ["squash"]).
    if len(api(f"git/commits/{sha}")["parents"]) != 1:
        raise ValueError("Release commit must be a single-parent commit")
    comments, page = [], 1
    while batch := api(f"issues/{prs[0]['number']}/comments?per_page=100&page={page}"):
        comments += batch
        page += 1
    # The newest owner decision for this head wins, so a later FAIL revokes an earlier PASS.
    records = [
        (c["created_at"], c["id"], v)
        for c in comments
        if (v := verdict(c, owner, head, prs[0]["merged_at"]))
    ]
    if max(records, default=(None, None, None))[2] != "PASS":
        raise ValueError("Newest owner verification record for merged PR head is missing or FAIL")
    checks = api(f"commits/{sha}/check-runs?per_page=100")["check_runs"]
    latest = {}
    for check in sorted(checks, key=lambda c: c["id"]):
        if check["app"]["id"] == 15368:
            latest[check["name"]] = check
    failed = sorted(
        name for name in (REQUIRED if required is None else required)
        if latest.get(name, {}).get("conclusion") != "success"
    )
    if failed:
        raise ValueError(f"Missing or failing main checks: {', '.join(failed)}")


def tag_commit(api, tag: str) -> str:
    """Peel lightweight or annotated tags, failing closed on malformed chains."""
    obj = api(f"git/ref/tags/{tag}")["object"]
    for _ in range(10):
        if not re.fullmatch(r"[0-9a-f]{40}", obj["sha"]):
            break
        if obj["type"] == "commit":
            return obj["sha"]
        if obj["type"] != "tag":
            break
        obj = api(f"git/tags/{obj['sha']}")["object"]
    raise ValueError("Tag does not peel to an exact commit")


def release_target(api, env) -> tuple[str, str]:
    """Only the named immutable recovery event may build a different commit."""
    sha, tag = env["GITHUB_SHA"], env["GITHUB_REF_NAME"]
    owner = env.get("GITHUB_REPOSITORY_OWNER", "")
    require_eligible(api, sha, owner)
    if tag == RECOVERY_TAG:
        if (
            env.get("GITHUB_EVENT_NAME") != "push"
            or env.get("GITHUB_REF") != f"refs/tags/{RECOVERY_TAG}"
            or env.get("GITHUB_REF_PROTECTED") != "true"
        ):
            raise ValueError("Recovery requires the exact protected tag push")
        if tag_commit(api, RECOVERY_TAG) != sha:
            raise ValueError("Recovery tag no longer names the event commit")
        if tag_commit(api, RECOVERY_TARGET) != RECOVERY_SHA:
            raise ValueError("Original v0.5.0 tag moved")
        # Recheck the original frozen gate, including its then-required benchmark
        # status. This does not restore a benchmark gate for new releases.
        require_eligible(api, RECOVERY_SHA, owner, required=REQUIRED | {"Product regression"})
        return RECOVERY_SHA, RECOVERY_TARGET
    return sha, tag


def main() -> None:
    repo = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GH_TOKEN"]

    def api(path):
        request = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/{path}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "release-eligibility",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    sha, tag = release_target(api, os.environ)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.write(f"build-sha={sha}\ntarget-tag={tag}\n")
    print("Verified main commit, owner verification record and required main checks.")


if __name__ == "__main__":
    main()
