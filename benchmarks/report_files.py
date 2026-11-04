"""Read only named, regular artifacts from a saved benchmark export."""

from __future__ import annotations

import os
import stat
from contextlib import ExitStack
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from benchmarks.agentic.inputs import json_mapping
from benchmarks.agentic.protocol import MAX_FRAME_BYTES, KeyLeakError, assert_no_key_shaped
from benchmarks.runner import BenchmarkValidationError


def checked_path(root: Path, path: Path) -> Path:
    """Reject redirects, including internal links to unrelated or hidden files."""
    assert_no_key_shaped(str(path), where="artifact path")
    if root.is_symlink() or not path.is_relative_to(root):
        raise BenchmarkValidationError("artifact path is outside the run directory")
    current = root
    for part in path.relative_to(root).parts:
        if part.startswith("."):
            raise BenchmarkValidationError("invalid artifact path component")
        current /= part
        if current.is_symlink():
            raise BenchmarkValidationError(f"artifact path contains a symlink: {current}")
    return path


def artifact_paths(root: Path, base: Path, pattern: str) -> list[Path]:
    """Expand a fixed report layout without traversing linked or hidden directories."""
    current = [checked_path(root, base)]
    for component in pattern.split("/"):
        current = [
            checked_path(root, child)
            for parent in current
            if parent.is_dir()
            for child in parent.iterdir()
            if not child.name.startswith(".") and fnmatchcase(child.name, component)
        ]
    return sorted(current)


def artifact_bytes(root: Path, path: Path) -> bytes:
    """Read a bounded regular file, checking the opened file before reading it."""
    checked_path(root, path)
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise BenchmarkValidationError(f"artifact is not a regular file: {path}")
        if os.open in os.supports_dir_fd:
            from benchmarks.agentic.egress import open_log_dir, read_log_at

            # Anchor each lookup to its open parent. The shared reader rejects links,
            # non-regular files, oversize artifacts and changes of length during a read.
            with ExitStack() as stack:
                parent = stack.enter_context(open_log_dir(root))
                for component in path.relative_to(root).parts[:-1]:
                    parent = stack.enter_context(open_log_dir(Path(component), dir_fd=parent))
                body = read_log_at(parent, path.name, max_bytes=MAX_FRAME_BYTES)

        else:
            # Fixed-flow reporting also supports platforms without directory descriptors.
            with path.open("rb") as file:
                opened = os.fstat(file.fileno())
                if not os.path.samestat(info, opened) or opened.st_size > MAX_FRAME_BYTES:
                    raise BenchmarkValidationError(
                        f"artifact changed or exceeds the size limit: {path}"
                    )
                body = file.read(opened.st_size + 1)
                if len(body) != opened.st_size:
                    raise BenchmarkValidationError(f"artifact size changed during read: {path}")
        assert_no_key_shaped(body, where="report artifact")
        return body
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise BenchmarkValidationError(f"cannot read artifact: {path}") from exc


def optional_json(root: Path, path: Path) -> dict[str, Any] | None:
    """Treat missing or malformed evidence as unavailable, but propagate unsafe reads."""
    try:
        content = artifact_bytes(root, path)
    except FileNotFoundError:
        return None
    try:
        return json_mapping(content, "report evidence")
    except KeyLeakError:
        raise
    except BenchmarkValidationError:
        return None
