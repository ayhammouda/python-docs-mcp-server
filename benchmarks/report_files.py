"""Confined reads and atomic publication of named benchmark artifacts."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterable, Iterator
from contextlib import ExitStack, contextmanager
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from benchmarks.agentic.inputs import json_mapping
from benchmarks.agentic.protocol import MAX_FRAME_BYTES, KeyLeakError, assert_no_key_shaped
from benchmarks.runner import BenchmarkValidationError


def _read_artifact_at(parent: int, parts: tuple[str, ...]) -> bytes:
    """Own each open directory until the shared bounded reader has finished."""
    from benchmarks.agentic.egress import open_log_dir, read_log_at

    if len(parts) == 1:
        return read_log_at(parent, parts[0], max_bytes=MAX_FRAME_BYTES)
    with open_log_dir(Path(parts[0]), dir_fd=parent) as directory:
        return _read_artifact_at(directory, parts[1:])


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


def check_output_paths(root: Path, paths: Iterable[Path]) -> None:
    """Preflight the whole publication/removal set without changing derived state."""
    for path in paths:
        checked_path(root, path)
        if path == root:
            raise BenchmarkValidationError("an artifact cannot replace the run directory")
        for parent in (root, *path.parents):
            if parent != root and not parent.is_relative_to(root):
                continue
            if parent.exists() and not parent.is_dir():
                raise BenchmarkValidationError(f"artifact parent is not a directory: {parent}")
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(info.st_mode):
            raise BenchmarkValidationError(f"artifact is not a regular file: {path}")


@contextmanager
def artifact_output_dirs(root: Path, paths: Iterable[Path]) -> Iterator[dict[Path, int]]:
    """Open every output parent beneath the run before publishing any artifact.

    Reuse the evidence spool's nonfollowing directory walk and atomic writer. Existing
    regular files (including hardlinks) may be replaced, never opened for mutation.
    """
    from benchmarks.agentic.egress import open_log_dir

    paths = tuple(paths)
    check_output_paths(root, paths)
    with ExitStack() as stack:
        directories = {root: stack.enter_context(open_log_dir(root))}
        for path in paths:
            parent = root
            for component in path.relative_to(root).parts[:-1]:
                child = parent / component
                if child not in directories:
                    try:
                        os.mkdir(component, dir_fd=directories[parent])
                    except FileExistsError:
                        pass
                    directories[child] = stack.enter_context(
                        open_log_dir(Path(component), dir_fd=directories[parent])
                    )
                parent = child
        # Repeat the leaf check against the opened parents, before the first write.
        for path in paths:
            try:
                info = os.stat(path.name, dir_fd=directories[path.parent], follow_symlinks=False)
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(info.st_mode):
                raise BenchmarkValidationError(f"artifact is not a regular file: {path}")
        yield directories


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
            from benchmarks.agentic.egress import open_log_dir

            # Anchor each lookup to its open parent. The shared reader rejects links,
            # non-regular files, oversize artifacts and changes of length during a read.
            with open_log_dir(root) as parent:
                body = _read_artifact_at(parent, path.relative_to(root).parts)

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
