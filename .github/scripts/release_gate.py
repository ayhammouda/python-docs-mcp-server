"""Fail closed unless this tag names an independently verified main commit.

The verification record is an unedited PR comment, posted no later than the
merge, by the repository owner. Its first line is exactly
``Independent verification: PASS`` and it has a ``head: <sha>`` line for the
merged PR head. Anyone who can post as the owner
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
# First line of the owner's PR comment that records an independent verification.
RECORD = re.compile(r"Independent verification: (PASS|FAIL)")


def verdict(comment, owner: str, head: str, merged_at: str) -> str | None:
    """Return PASS or FAIL for an owner record of ``head``, else None."""
    lines = comment["body"].replace("\r\n", "\n").split("\n")
    match = RECORD.fullmatch(lines[0])
    if (
        match
        and (comment["user"] or {}).get("login") == owner
        # An edited record could name a head nobody verified, so only unedited ones count.
        and comment["updated_at"] == comment["created_at"]
        # Verification gates the merge; a record written afterwards does not count.
        and comment["created_at"] <= merged_at
        and f"head: {head}" in lines
    ):
        return match[1]
    return None


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
