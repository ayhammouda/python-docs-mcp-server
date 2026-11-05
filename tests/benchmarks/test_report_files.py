"""Confined report reads own every descriptor, including on interrupted reads."""

import errno
import os
import subprocess
import sys
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


def test_wide_output_batch_does_not_require_a_deeper_python_stack(tmp_path: Path) -> None:
    # Isolate the recursion ceiling from pytest and the other tests in its process.
    program = """
import sys
from pathlib import Path
from benchmarks.agentic import egress
from benchmarks.report_files import artifact_output_dirs

root = Path(sys.argv[1])
paths = [root / str(i) / "score.json" for i in range(64)]
sys.setrecursionlimit(64)
with artifact_output_dirs(root, paths) as directories:
    assert all(path.parent in directories for path in paths)
"""
    subprocess.run([sys.executable, "-c", program, str(tmp_path)], check=True)


@pytest.mark.parametrize("fault", ["none", "mkdir", "open", "leaf", "body", "cancel"])
def test_output_directories_close_every_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    paths = [tmp_path / "nested/deeper/a.json", tmp_path / "other/b.json"]
    opened: list[int] = []
    original_open, original_mkdir, original_stat = os.open, os.mkdir, os.stat

    def track_open(name, flags, *args, **kwargs):
        if fault == "open" and str(name) == "deeper":
            raise OSError(errno.EACCES, "controlled directory open failure")
        fd = original_open(name, flags, *args, **kwargs)
        opened.append(fd)
        return fd

    def fail_mkdir(name, *args, **kwargs):
        if fault == "mkdir" and str(name) == "deeper":
            raise OSError(errno.EACCES, "controlled mkdir failure")
        return original_mkdir(name, *args, **kwargs)

    def fail_stat(name, *args, **kwargs):
        if fault == "leaf" and name == "a.json" and kwargs.get("dir_fd") is not None:
            raise OSError(errno.EIO, "controlled leaf stat failure")
        return original_stat(name, *args, **kwargs)

    monkeypatch.setattr(os, "open", track_open)
    monkeypatch.setattr(os, "mkdir", fail_mkdir)
    monkeypatch.setattr(os, "stat", fail_stat)

    def publish():
        with report_files.artifact_output_dirs(tmp_path, paths) as directories:
            assert set(directories) == {
                tmp_path, tmp_path / "nested", tmp_path / "nested/deeper", tmp_path / "other",
            }
            for fd in directories.values():
                os.fstat(fd)
            if fault == "body":
                raise ValueError("controlled publication failure")
            if fault == "cancel":
                raise KeyboardInterrupt("controlled publication interruption")
            egress.write_new(directories[paths[0].parent], paths[0].name, b"{}")

    errors = {"mkdir": OSError, "open": BenchmarkValidationError, "leaf": OSError,
              "body": ValueError, "cancel": KeyboardInterrupt}
    if fault == "none":
        publish()
        assert paths[0].read_bytes() == b"{}"
    else:
        with pytest.raises(errors[fault]):
            publish()
    assert opened
    for fd in opened:
        with pytest.raises(OSError) as caught:
            os.fstat(fd)
        assert caught.value.errno == errno.EBADF


@pytest.mark.parametrize("fault", ["none", "fstat", "cancel", "oversize"])
def test_fallback_reader_closes_regular_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    path = tmp_path / "evidence.json"
    path.write_bytes(b"{}")
    files = []
    original_open, original_fstat = Path.open, os.fstat

    def track_open(target, *args, **kwargs):
        file = original_open(target, *args, **kwargs)
        files.append(file)
        return file

    def fail_stat(fd):
        if fault == "fstat":
            raise OSError(errno.EIO, "controlled file stat failure")
        if fault == "cancel":
            raise KeyboardInterrupt("controlled file interruption")
        info = original_fstat(fd)
        if fault == "oversize":
            values = list(info)
            values[6] = report_files.MAX_FRAME_BYTES + 1
            return os.stat_result(values)
        return info

    monkeypatch.setattr(os, "supports_dir_fd", set())
    monkeypatch.setattr(Path, "open", track_open)
    monkeypatch.setattr(os, "fstat", fail_stat)
    if fault == "none":
        assert report_files.artifact_bytes(tmp_path, path) == b"{}"
    else:
        expected = KeyboardInterrupt if fault == "cancel" else BenchmarkValidationError
        with pytest.raises(expected):
            report_files.artifact_bytes(tmp_path, path)
    assert len(files) == 1
    assert files[0].closed
