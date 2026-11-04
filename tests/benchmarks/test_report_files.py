"""Confined report reads own every descriptor, including on interrupted reads."""

import errno
import os
from pathlib import Path

import pytest

from benchmarks import report_files
from benchmarks.agentic import egress
from benchmarks.runner import BenchmarkValidationError


@pytest.mark.parametrize(
    "fault", ["none", "parent-open", "file-open", "fstat", "read", "cancel", "grow", "shrink"]
)
def test_artifact_read_closes_all_opened_descriptors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    path = tmp_path / "nested/deeper/evidence.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"{}")
    opened: list[int] = []
    file_fd: int | None = None
    original_open, original_read, original_fstat = os.open, os.read, os.fstat

    def tracked_open(name, flags, *args, **kwargs):
        nonlocal file_fd
        if (fault == "parent-open" and str(name) == "deeper") or (
            fault == "file-open" and str(name) == path.name
        ):
            raise OSError(errno.EACCES, "controlled open failure")
        fd = original_open(name, flags, *args, **kwargs)
        opened.append(fd)
        if str(name) == path.name:
            file_fd = fd
        return fd

    def tracked_fstat(fd):
        if fd == file_fd and fault == "fstat":
            raise OSError(errno.EIO, "controlled stat failure")
        return original_fstat(fd)

    def tracked_read(fd, size):
        if fd == file_fd:
            if fault == "read":
                raise OSError(errno.EIO, "controlled read failure")
            if fault == "cancel":
                raise KeyboardInterrupt("controlled interruption")
            if fault in {"grow", "shrink"}:
                path.write_bytes(b"longer" if fault == "grow" else b"")
        return original_read(fd, size)

    monkeypatch.setattr(os, "open", tracked_open)
    monkeypatch.setattr(os, "supports_dir_fd", {*os.supports_dir_fd, tracked_open})
    monkeypatch.setattr(os, "read", tracked_read)
    monkeypatch.setattr(os, "fstat", tracked_fstat)
    if fault == "none":
        body = report_files.artifact_bytes(tmp_path, path)
        assert body == b"{}"
    else:
        expected = KeyboardInterrupt if fault == "cancel" else BenchmarkValidationError
        with pytest.raises(expected):
            report_files.artifact_bytes(tmp_path, path)
    assert len(opened) >= 2
    for fd in opened:
        with pytest.raises(OSError) as caught:
            original_fstat(fd)
        assert caught.value.errno == errno.EBADF


@pytest.mark.parametrize("redirect", ["root", "parent", "file"])
def test_artifact_reader_refuses_links_swapped_after_path_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, redirect: str
) -> None:
    root = tmp_path / "run"
    path = root / "nested/evidence.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"{}")
    check = report_files.checked_path

    def swap(base: Path, target: Path) -> Path:
        result = check(base, target)
        linked = {"root": root, "parent": path.parent, "file": path}[redirect]
        moved = linked.rename(tmp_path / "moved")
        linked.symlink_to(moved)
        return result

    monkeypatch.setattr(report_files, "checked_path", swap)
    with pytest.raises(BenchmarkValidationError):
        report_files.artifact_bytes(root, path)


def test_artifact_reader_closes_parent_when_shared_reader_rejects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "evidence.json"
    path.write_bytes(b"{}")
    parents: list[int] = []

    def reject(parent: int, name: str, **kwargs) -> bytes:
        parents.append(parent)
        raise BenchmarkValidationError("controlled shared reader rejection")

    monkeypatch.setattr(egress, "read_log_at", reject)
    with pytest.raises(BenchmarkValidationError, match="shared reader rejection"):
        report_files.artifact_bytes(tmp_path, path)
    assert len(parents) == 1
    with pytest.raises(OSError):
        os.fstat(parents[0])


def test_artifact_reader_refuses_final_link_swapped_after_lstat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "evidence.json"
    path.write_bytes(b"{}")
    other = tmp_path / "other.json"
    other.write_bytes(b'{"unrelated": true}')
    original_open = os.open
    attempted: list[str] = []

    def swap_before_open(name, flags, *args, **kwargs):
        if str(name) == path.name:
            attempted.append(str(name))
            path.unlink()
            path.symlink_to(other)
        return original_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap_before_open)
    monkeypatch.setattr(os, "supports_dir_fd", {*os.supports_dir_fd, swap_before_open})
    with pytest.raises(BenchmarkValidationError, match="refused"):
        report_files.artifact_bytes(tmp_path, path)
    assert attempted == [path.name]
