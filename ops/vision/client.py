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
    fields = git("diff-tree", "-r", "-z", "--no-renames", base, "HEAD").split(b"\0")[:-1]
    for meta, raw in zip(fields[0::2], fields[1::2], strict=True):
        path = raw.decode()
        _, mode, _, sha, _ = meta.decode().split(" ")
        if mode == "000000":
            changes.append({"path": path, "content": None})
        elif mode in {"100644", "100755"}:
            content = base64.b64encode(git("cat-file", "blob", sha)).decode()
            changes.append({"path": path, "mode": mode, "sha": sha, "content": content})
        else:
            raise ValueError(f"Mode {mode} is not publishable: {path}")
    return {
        "operation": "publish",
        "branch": branch,
        "base_sha": base,
        "tree_sha": git("rev-parse", "HEAD^{tree}").decode().strip(),
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
