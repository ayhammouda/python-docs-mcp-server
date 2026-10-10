#!/usr/bin/python3 -I
"""Unprivileged broker client; publication sends committed file bytes, never executes them."""

import argparse
import base64
import json
import subprocess
import sys


def publication(branch, base, message):
    def git(*args):
        return subprocess.check_output(["git", *args])

    if git("status", "--porcelain").strip():
        raise ValueError("Commit the intended changes first; checkout must be clean")
    changes = []
    for path in git("diff", "--name-only", "-z", base, "HEAD").decode().split("\0"):
        if not path:
            continue
        entry = git("ls-tree", "-z", "HEAD", "--", path)
        change = {"path": path, "content": None}
        if entry:
            change.update(
                mode=entry.decode().split(" ", 1)[0],
                content=base64.b64encode(git("show", f"HEAD:{path}")).decode(),
            )
        changes.append(change)
    return {
        "operation": "publish",
        "branch": branch,
        "base_sha": base,
        "message": message,
        "changes": changes,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    commands.add_parser("status")
    rerun = commands.add_parser("rerun", help="Retry failed current-main CI jobs only")
    rerun.add_argument("run_id", type=int)
    rerun.add_argument("--failed", action="store_true", help="Default; only failed jobs rerun")
    threads = commands.add_parser("threads")
    threads.add_argument("pr", type=int)
    api = commands.add_parser("api")
    api.add_argument("method", choices=["GET", "POST", "PATCH"])
    api.add_argument("path")
    api.add_argument("--body", help="Path to JSON body")
    publish = commands.add_parser("publish")
    publish.add_argument("--branch", required=True)
    publish.add_argument("--base", required=True)
    publish.add_argument("--message", required=True)
    publish.add_argument("--decision", required=True, help="Path to acceptance evidence JSON")
    release = commands.add_parser("release")
    release.add_argument("sha")
    release.add_argument("tag")
    for name in ["verify", "merge", "resolve"]:
        command = commands.add_parser(name)
        command.add_argument("pr", type=int)
        command.add_argument("head_sha")
        if name == "resolve":
            command.add_argument("thread_id")
            command.add_argument("--reason", required=True)
        if name == "verify":
            command.add_argument("--decision", required=True, help="Path to product decision JSON")
    args = parser.parse_args()
    data = vars(args).copy()
    data.pop("failed", None)
    if args.operation == "publish":
        data = publication(args.branch, args.base, args.message)
        with open(args.decision) as source:
            data["decision"] = json.load(source)
    else:
        for key in ["body", "decision"]:
            if data.get(key):
                with open(data[key]) as source:
                    data[key] = json.load(source)
    result = subprocess.run(
        ["sudo", "-n", "/opt/python-docs-vision/control.py"], input=json.dumps(data), text=True
    )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
