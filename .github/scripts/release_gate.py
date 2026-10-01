"""Fail closed unless this tag names an independently verified main commit."""

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


def require_eligible(api, sha: str, verifier_app_id: int) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or verifier_app_id <= 0:
        raise ValueError("Exact commit and configured verifier App ID are required")
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
    reviews = api(f"commits/{head}/check-runs?per_page=100")["check_runs"]
    reviews = [
        c
        for c in reviews
        if c["name"] == "Independent verification" and c["app"]["id"] == verifier_app_id
    ]
    parents = api(f"git/commits/{sha}")["parents"]
    latest = max(reviews, key=lambda c: c["id"], default={})
    if (
        len(parents) != 1
        or latest.get("conclusion") != "success"
        or latest.get("external_id") != f"{parents[0]['sha']}:{head}"
    ):
        raise ValueError("Missing successful independent verification for merged PR head")
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

    require_eligible(api, os.environ["GITHUB_SHA"], int(os.environ.get("VERIFIER_APP_ID") or "0"))
    print("Verified main commit, independent review and required main checks.")


if __name__ == "__main__":
    main()
