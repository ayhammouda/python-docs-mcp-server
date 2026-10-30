"""Fail closed unless this tag names an independently verified main commit.

The verification record is an unedited PR comment by the repository owner whose
first line is exactly ``Independent verification: PASS`` and which has a
``head: <sha>`` line for the merged PR head. Anyone who can post as the owner
account can write it, and the owner account also merges, so it is a process
tripwire, not a security boundary. Tag creation rules, immutable tags, the
``pypi`` environment and the required checks carry security.

A credential-bound check needs a verifier identity separate from the merger: a
dedicated GitHub App with ``checks: write``, or a machine account. Restoring one
is the path back to that stronger control, and it is Aymen's decision.
"""

import json
import os
import re
import urllib.request

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
    "Product regression",
}
# First line of the owner's PR comment that records independent verification.
RECORD = "Independent verification: PASS"


def is_record(comment, owner: str, head: str) -> bool:
    lines = comment["body"].replace("\r\n", "\n").split("\n")
    return (
        (comment["user"] or {}).get("login") == owner
        # An edited record could name a head nobody verified, so only unedited ones count.
        and comment["updated_at"] == comment["created_at"]
        and lines[0] == RECORD
        and f"head: {head}" in lines
    )


def require_eligible(api, sha: str, owner: str) -> None:
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
    if len(api(f"git/commits/{sha}")["parents"]) != 1:
        raise ValueError("Release commit must be a single-parent squash merge")
    # shortcut: reads the first 100 comments only and fails closed; paginate if PRs outgrow it.
    comments = api(f"issues/{prs[0]['number']}/comments?per_page=100")
    if not any(is_record(c, owner, head) for c in comments):
        raise ValueError("Missing owner independent verification record for merged PR head")
    checks = api(f"commits/{sha}/check-runs?per_page=100")["check_runs"]
    latest = {}
    for check in sorted(checks, key=lambda c: c["id"]):
        if check["app"]["id"] == 15368:
            latest[check["name"]] = check
    failed = sorted(
        name for name in REQUIRED if latest.get(name, {}).get("conclusion") != "success"
    )
    if failed:
        raise ValueError(f"Missing or failing main checks: {', '.join(failed)}")


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

    require_eligible(api, os.environ["GITHUB_SHA"], os.environ.get("GITHUB_REPOSITORY_OWNER", ""))
    print("Verified main commit, owner verification record and required main checks.")


if __name__ == "__main__":
    main()
