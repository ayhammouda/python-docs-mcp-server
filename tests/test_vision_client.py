"""pdctl publication sends exactly the committed tree, from any directory."""

import base64
import os
import runpy
import subprocess
from pathlib import Path

import pytest

CLIENT = runpy.run_path(str(Path(__file__).parents[1] / "ops/vision/client.py"))


def git(repo, *args, env=None, input=None):
    result = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, env=env, input=input
    )
    return result.stdout.decode().strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Base commit, then a rename, deletes (file and symlink), chmod +x, edits and new files."""
    for name, value in {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }.items():
        monkeypatch.setenv(name, value)
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "src/old.py").write_text("a\nb\nc\n")
    (root / "docs/readme.txt").write_text("x\n")
    (root / "tool.sh").write_text("#!/bin/sh\necho hi\n")
    (root / "gone.txt").write_text("keep\n")
    (root / "link").symlink_to("docs/readme.txt")
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "base")
    base = git(root, "rev-parse", "HEAD")
    git(root, "mv", "src/old.py", "src/new.py")
    (root / "tool.sh").chmod(0o755)
    git(root, "rm", "-q", "gone.txt", "link")
    (root / "docs/readme.txt").write_text("y\n")
    (root / "docs/café notes.txt").write_text("é\n")
    (root / "assets").mkdir()
    (root / "assets/blob.bin").write_bytes(bytes(range(256)))
    git(root, "add", "-A")
    git(root, "commit", "-qm", "change")
    return root, base


def rebuild(root, base, changes):
    """Apply the change list to base's tree in a scratch index, as the broker does."""
    env = {**os.environ, "GIT_INDEX_FILE": str(root / ".git/rebuild-index")}
    git(root, "read-tree", base, env=env)
    for change in changes:
        if change["content"] is None:
            git(root, "update-index", "--force-remove", "--", change["path"], env=env)
            continue
        blob = git(root, "hash-object", "-w", "--stdin", input=base64.b64decode(change["content"]))
        git(root, "update-index", "--add", "--cacheinfo",
            f"{change['mode']},{blob},{change['path']}", env=env)
    return git(root, "write-tree", env=env)


@pytest.mark.parametrize("where", [".", "docs"])
def test_publication_rebuilds_the_committed_tree_from_any_directory(repo, monkeypatch, where):
    root, base = repo
    monkeypatch.chdir(root / where)
    data = CLIENT["publication"]("codex/x", base, "m")
    deleted = sorted(c["path"] for c in data["changes"] if c["content"] is None)
    assert deleted == ["gone.txt", "link", "src/old.py"]
    head_tree = git(root, "rev-parse", "HEAD^{tree}")
    assert rebuild(root, base, data["changes"]) == head_tree
    assert data["tree_sha"] == head_tree
    modes = {c["path"]: c["mode"] for c in data["changes"] if c["content"] is not None}
    assert modes["tool.sh"] == "100755"


def test_publication_refuses_a_new_symlink(repo, monkeypatch):
    root, base = repo
    (root / "new-link").symlink_to("tool.sh")
    git(root, "add", "new-link")
    git(root, "commit", "-qm", "link")
    monkeypatch.chdir(root)
    with pytest.raises(ValueError, match="120000"):
        CLIENT["publication"]("codex/x", base, "m")
