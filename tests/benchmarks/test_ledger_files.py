"""Spend ledger files stay within the directory whose lock the process holds."""

import errno
import fcntl
import json
import os
import stat
import subprocess
import sys
from decimal import Decimal

import pytest

from benchmarks import __main__ as cli
from benchmarks.agentic.ledger import Ledger
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks.test_grading_run import cli_args, config
from tests.benchmarks.test_grading_run import scripted_run as scripted_run


def reserve(ledger, provider="anthropic"):
    return ledger.reserve(
        provider,
        model_id="m",
        kind="model_call",
        bound=Decimal(1),
        input_bound=1,
        output_bound=1,
    )


def snapshot(path):
    info = path.stat()
    return (
        path.read_bytes(),
        info.st_mode,
        info.st_ino,
        info.st_nlink,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


@pytest.mark.parametrize("name", [".lock", "anthropic.jsonl"])
@pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory", "fifo"])
def test_open_refuses_unsafe_files_without_changing_external_sentinel(tmp_path, name, kind):
    directory = tmp_path / "ledger"
    directory.mkdir()
    sentinel = tmp_path / "benign-external"
    sentinel.write_bytes(b"\n")  # A valid empty legacy journal, not a parse failure.
    sentinel.chmod(0o644)
    path = directory / name
    if kind == "symlink":
        path.symlink_to(sentinel)
    elif kind == "hardlink":
        os.link(sentinel, path)
    elif kind == "directory":
        path.mkdir()
    else:
        os.mkfifo(path)
    before = snapshot(sentinel)
    if kind == "fifo":
        # A regression must fail with a timeout instead of hanging the test runner.
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                """
import sys
from pathlib import Path
from benchmarks.agentic.ledger import Ledger
from benchmarks.runner import BenchmarkValidationError
try:
    ledger = Ledger.open(Path(sys.argv[1]), run_id='fifo')
except BenchmarkValidationError:
    pass
else:
    ledger.close()
    raise AssertionError('FIFO accepted')
""",
                str(directory),
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 0, result.stderr
    else:
        with pytest.raises(BenchmarkValidationError, match="spend ledger"):
            ledger = Ledger.open(directory, run_id="unsafe")
            ledger.close()
    assert snapshot(sentinel) == before


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_journal_link_inserted_after_open_cannot_receive_a_reservation(tmp_path, kind):
    directory = tmp_path / "ledger"
    ledger = Ledger.open(directory, run_id="inserted")
    sentinel = tmp_path / "benign-external"
    sentinel.write_bytes(b"benign contents\n")
    path = directory / "anthropic.jsonl"
    if kind == "symlink":
        path.symlink_to(sentinel)
    else:
        os.link(sentinel, path)
    before = snapshot(sentinel)
    try:
        with pytest.raises(BenchmarkValidationError, match="spend ledger"):
            reserve(ledger)
        assert ledger.invocation_events() == []
        assert snapshot(sentinel) == before
        path.unlink()
        assert reserve(ledger)  # Refusal did not consume the provider's active unit.
    finally:
        ledger.close()


def test_ledger_directory_itself_must_not_be_a_symlink(tmp_path):
    target = tmp_path / "external"
    target.mkdir()
    alias = tmp_path / "ledger"
    alias.symlink_to(target, target_is_directory=True)
    with pytest.raises(BenchmarkValidationError, match="spend ledger"):
        ledger = Ledger.open(alias, run_id="alias")
        ledger.close()
    assert list(target.iterdir()) == []


@pytest.mark.parametrize("provider", ["../external", "absolute", "other", "..\\external"])
def test_recovery_refuses_provider_not_matching_its_journal_before_writing(tmp_path, provider):
    directory = tmp_path / "ledger"
    directory.mkdir()
    if provider == "absolute":
        provider = str(tmp_path / "external")
    journal = directory / "anthropic.jsonl"
    journal.write_text(
        json.dumps(
            {
                "event": "reserve",
                "run_id": "legacy",
                "provider": provider,
                "unit_id": "pending",
                "bound": "2",
            }
        )
        + "\n"
    )
    sentinel = tmp_path / "external.jsonl"
    sentinel.write_bytes(b"benign contents\n")
    before, history = snapshot(sentinel), snapshot(journal)
    with pytest.raises(BenchmarkValidationError, match="spend ledger"):
        ledger = Ledger.open(directory, run_id="recovery")
        ledger.close()
    assert snapshot(sentinel) == before
    assert snapshot(journal) == history
    assert sorted(p.name for p in directory.iterdir()) == [".lock", "anthropic.jsonl"]


def test_recovery_validates_all_files_before_settling_any_pending_unit(tmp_path):
    directory = tmp_path / "ledger"
    ledger = Ledger.open(directory, run_id="first")
    assert reserve(ledger)
    ledger.close()
    journal = directory / "anthropic.jsonl"
    before = snapshot(journal)
    (directory / "openai.jsonl").symlink_to(journal)
    with pytest.raises(BenchmarkValidationError, match="spend ledger"):
        ledger = Ledger.open(directory, run_id="blocked")
        ledger.close()
    assert snapshot(journal) == before
    (directory / "openai.jsonl").unlink()
    ledger = Ledger.open(directory, run_id="recovered")
    try:
        assert ledger.spent("anthropic") == 2
        assert ledger.invocation_events()[0]["outcome"] == "unsettled_at_load"
    finally:
        ledger.close()


def test_directory_replaced_after_lock_keeps_recovery_append_and_sync_on_original(
    tmp_path,
    monkeypatch,
):
    directory, held, replacement = (tmp_path / n for n in ("ledger", "held", "replacement"))
    ledger = Ledger.open(directory, run_id="first")
    assert reserve(ledger)
    ledger.close()
    replacement.mkdir()
    sentinel = replacement / "anthropic.jsonl"
    sentinel.write_bytes(b"\n")
    before = snapshot(sentinel)
    flock, fsync = fcntl.flock, os.fsync
    synced = []

    def swap(fd, operation):
        flock(fd, operation)
        if operation == fcntl.LOCK_EX | fcntl.LOCK_NB:
            directory.rename(held)
            directory.symlink_to(replacement, target_is_directory=True)

    def sync(fd):
        info = os.fstat(fd)
        if stat.S_ISDIR(info.st_mode):
            synced.append(info.st_ino)
        fsync(fd)

    with monkeypatch.context() as patch:
        patch.setattr(fcntl, "flock", swap)
        patch.setattr(os, "fsync", sync)
        ledger = Ledger.open(directory, run_id="after-swap")
        try:
            assert ledger.spent("anthropic") == 2
            assert reserve(ledger, "openai")
            assert reserve(ledger)
            with pytest.raises(BenchmarkValidationError, match=r"\.lock"):
                Ledger.open(held, run_id="contender")
        finally:
            ledger.close()
    assert snapshot(sentinel) == before
    assert sorted(p.name for p in replacement.iterdir()) == ["anthropic.jsonl"]
    assert synced == [held.stat().st_ino]
    assert len((held / "anthropic.jsonl").read_text().splitlines()) == 3
    # The descriptor lock protected the moved original directory until close.
    Ledger.open(held, run_id="reopen").close()


def test_safe_external_provider_legacy_history_and_parent_alias_still_share_caps(tmp_path):
    parent = tmp_path / "real-parent"
    parent.mkdir()
    alias = tmp_path / "parent-alias"
    alias.symlink_to(parent, target_is_directory=True)
    directory = alias / "ledger"
    provider = "external-provider_v1.2"
    ledger = Ledger.open(directory, run_id="first", caps={provider: Decimal(3)})
    assert reserve(ledger, provider)
    ledger.close()
    ledger = Ledger.open(parent / "ledger", run_id="next", caps={provider: Decimal(2)})
    try:
        assert ledger.spent(provider) == 2
        assert reserve(ledger, provider) is None
        assert ledger.stopped(provider) == "cap"
    finally:
        ledger.close()


def test_abrupt_exit_releases_lock_and_preserves_pending_booking(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os, sys
from pathlib import Path
from decimal import Decimal
from benchmarks.agentic.ledger import Ledger
ledger = Ledger.open(Path(sys.argv[1]), run_id='crashed')
ledger.reserve('anthropic', model_id='m', kind='model_call', bound=Decimal(3),
               input_bound=1, output_bound=1)
os._exit(17)
""",
            str(tmp_path / "ledger"),
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 17, result.stderr
    ledger = Ledger.open(tmp_path / "ledger", run_id="recovered")
    try:
        assert ledger.spent("anthropic") == 6
        with pytest.raises(BenchmarkValidationError, match=r"\.lock"):
            Ledger.open(tmp_path / "ledger", run_id="contender")
    finally:
        ledger.close()


@pytest.mark.parametrize("name", [".lock", "anthropic.jsonl"])
def test_normal_grading_cli_refuses_link_and_preserves_real_run_journal(
    scripted_run,
    capsys,
    name,
):
    cfg = config(scripted_run.parent)
    # A real safe scripted grading invocation registers its run and writes its journal.
    assert cli.main(cli_args(cfg, "--grading-run-id", "safe")) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "completed" and summary["records_written"] == 6
    assert summary["units"] and (scripted_run / "grading/runs/safe.json").is_file()
    external = scripted_run / "grading/ledger-events.jsonl"
    before = snapshot(external)
    directory = scripted_run.parent / "unsafe-ledger"
    directory.mkdir()
    (directory / name).symlink_to(external)
    with pytest.raises(SystemExit) as caught:
        cli.main(
            cli_args(
                cfg, "--ledger-dir", str(directory), "--grading-run-id", "unsafe", "--pass", "2"
            )
        )
    assert caught.value.code == 2
    output = capsys.readouterr()
    assert not output.out and "spend ledger" in output.err
    assert str(external) not in output.err
    assert snapshot(external) == before
    assert not (scripted_run / "grading/runs/unsafe.json").exists()


@pytest.mark.parametrize("fault", ["short", "fsync", "directory-sync", "interrupt"])
def test_failed_append_rolls_back_bytes_and_keeps_reservation_available(
    tmp_path, monkeypatch, fault
):
    ledger = Ledger.open(tmp_path, run_id="failed")
    write, fsync = os.write, os.fsync
    primary = KeyboardInterrupt("append interrupted") if fault == "interrupt" else OSError("disk")
    failed = False

    def broken_write(fd, data):
        nonlocal failed
        if fault == "short" and not failed:
            failed = True
            return write(fd, data[:7])
        if fault == "interrupt" and not failed:
            failed = True
            write(fd, data[:7])
            raise primary
        return write(fd, data)

    def broken_sync(fd):
        nonlocal failed
        if not failed and (
            fault == "fsync" or fault == "directory-sync" and stat.S_ISDIR(os.fstat(fd).st_mode)
        ):
            failed = True
            raise primary
        fsync(fd)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(os, "write", broken_write)
            patch.setattr(os, "fsync", broken_sync)
            with pytest.raises(KeyboardInterrupt if fault == "interrupt" else OSError) as caught:
                reserve(ledger)
        if fault != "short":
            assert caught.value is primary
        assert (tmp_path / "anthropic.jsonl").read_bytes() == b""
        assert ledger.invocation_events() == []
        assert reserve(ledger)
    finally:
        ledger.close()
    reopened = Ledger.open(tmp_path, run_id="recovered")
    try:
        assert reopened.spent("anthropic") == 2
    finally:
        reopened.close()


def test_rollback_failure_preserves_original_interruption_and_active_lock(tmp_path, monkeypatch):
    ledger = Ledger.open(tmp_path, run_id="interrupt")
    primary = KeyboardInterrupt("primary interruption")

    def broken_sync(fd):
        raise primary

    def broken_rollback(fd, length):
        raise OSError("rollback unavailable")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(os, "fsync", broken_sync)
            patch.setattr(os, "ftruncate", broken_rollback)
            with pytest.raises(KeyboardInterrupt) as caught:
                reserve(ledger)
        assert caught.value is primary
        assert ledger.invocation_events() == []
        with pytest.raises(BenchmarkValidationError, match=r"\.lock"):
            Ledger.open(tmp_path, run_id="contender")
    finally:
        ledger.close()
    reopened = Ledger.open(tmp_path, run_id="recovered")
    try:
        assert reopened.spent("anthropic") == 2
    finally:
        reopened.close()


@pytest.mark.parametrize("change", ["closed", "replaced-lock", "linked-lock"])
def test_reservation_requires_the_active_original_lock(tmp_path, change):
    ledger = Ledger.open(tmp_path, run_id="first")
    if change == "closed":
        ledger.close()
    elif change == "replaced-lock":
        (tmp_path / ".lock").unlink()
        (tmp_path / ".lock").touch()
    else:
        os.link(tmp_path / ".lock", tmp_path / "alias")
    try:
        with pytest.raises(BenchmarkValidationError, match="spend ledger"):
            reserve(ledger)
        assert not (tmp_path / "anthropic.jsonl").exists()
    finally:
        ledger.close()


def test_retry_after_directory_sync_failure_still_syncs_created_entry(tmp_path, monkeypatch):
    ledger = Ledger.open(tmp_path, run_id="retry")
    fsync = os.fsync
    synced = []

    def broken_directory_sync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("directory sync failed")
        fsync(fd)

    def record_sync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            synced.append(os.fstat(fd).st_ino)
        fsync(fd)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(os, "fsync", broken_directory_sync)
            with pytest.raises(OSError, match="directory sync failed"):
                reserve(ledger)
        assert (tmp_path / "anthropic.jsonl").read_bytes() == b""
        with monkeypatch.context() as patch:
            patch.setattr(os, "fsync", record_sync)
            assert reserve(ledger)
        assert synced == [tmp_path.stat().st_ino]
    finally:
        ledger.close()


def test_interrupted_reader_setup_closes_journal_directory_and_lock(tmp_path, monkeypatch):
    ledger = Ledger.open(tmp_path, run_id="pending")
    assert reserve(ledger)
    ledger.close()
    opening = os.open
    opened = []
    primary = KeyboardInterrupt("reader setup interrupted")

    def track(*args, **kwargs):
        fd = opening(*args, **kwargs)
        opened.append(fd)
        return fd

    def interrupt(*args, **kwargs):
        raise primary

    with monkeypatch.context() as patch:
        patch.setattr(os, "open", track)
        patch.setattr(os, "fdopen", interrupt)
        with pytest.raises(KeyboardInterrupt) as caught:
            Ledger.open(tmp_path, run_id="interrupted")
    assert caught.value is primary
    assert len(opened) == 3
    try:
        for fd in opened:
            with pytest.raises(OSError):
                os.fstat(fd)
    finally:
        # Also clean a descriptor leaked by a regressed implementation.
        for fd in opened:
            try:
                os.close(fd)
            except OSError:
                pass
    Ledger.open(tmp_path, run_id="reopen").close()


def test_append_rollback_uses_original_file_after_directory_replacement(tmp_path, monkeypatch):
    directory, held, replacement = (tmp_path / n for n in ("ledger", "held", "replacement"))
    ledger = Ledger.open(directory, run_id="first")
    unit = reserve(ledger)
    ledger.settle(unit, outcome="rejected", actual=None, buckets=None)
    before = snapshot(directory / "anthropic.jsonl")
    replacement.mkdir()
    sentinel = replacement / "anthropic.jsonl"
    sentinel.write_bytes(b"benign replacement\n")
    external = snapshot(sentinel)
    writing = os.write

    def swap_then_short_write(fd, body):
        directory.rename(held)
        directory.symlink_to(replacement, target_is_directory=True)
        return writing(fd, body[:7])

    try:
        with monkeypatch.context() as patch:
            patch.setattr(os, "write", swap_then_short_write)
            with pytest.raises(OSError, match="incomplete ledger append"):
                reserve(ledger)
        assert snapshot(sentinel) == external
        assert (held / "anthropic.jsonl").read_bytes() == before[0]
        assert len(ledger.invocation_events()) == 2
        assert reserve(ledger)
        assert snapshot(sentinel) == external
    finally:
        ledger.close()


def test_partial_append_with_failed_rollback_blocks_recovery_without_further_writes(
    tmp_path,
    monkeypatch,
):
    ledger = Ledger.open(tmp_path, run_id="partial")
    writing = os.write

    def short_write(fd, body):
        return writing(fd, body[:7])

    def failed_rollback(fd, size):
        raise OSError("rollback unavailable")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(os, "write", short_write)
            patch.setattr(os, "ftruncate", failed_rollback)
            with pytest.raises(OSError, match="incomplete ledger append") as caught:
                reserve(ledger)
        assert "rollback failed" in " ".join(caught.value.__notes__)
        assert ledger.invocation_events() == []
    finally:
        ledger.close()
    journal = tmp_path / "anthropic.jsonl"
    before = snapshot(journal)
    with pytest.raises(BenchmarkValidationError, match="corrupt"):
        Ledger.open(tmp_path, run_id="blocked")
    assert snapshot(journal) == before


def test_minimal_legacy_external_history_recovers_without_a_current_provider_cap(tmp_path):
    journal = tmp_path / "external-provider.jsonl"
    journal.write_text(
        '{"event":"reserve","run_id":"legacy","provider":"external-provider",'
        '"unit_id":"old-unit","bound":"3"}\n'
    )
    ledger = Ledger.open(tmp_path, run_id="recovery")
    try:
        assert ledger.spent("external-provider") == 6
        assert ledger.invocation_events()[0]["outcome"] == "unsettled_at_load"
        assert reserve(ledger)  # Current capped providers still work alongside this history.
    finally:
        ledger.close()
    history = [json.loads(line) for line in journal.read_text().splitlines()]
    assert [event["provider"] for event in history] == ["external-provider"] * 2
    assert history[-1]["booked"] == "6"


def test_journal_open_io_failure_preserves_original_error_and_leaves_no_reservation(
    tmp_path, monkeypatch,
):
    ledger = Ledger.open(tmp_path, run_id="io-failure")
    opening = os.open
    primary = OSError(errno.EIO, "journal storage unavailable")

    def fail_journal(name, flags, *args, **kwargs):
        if name == "anthropic.jsonl":
            raise primary
        return opening(name, flags, *args, **kwargs)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(os, "open", fail_journal)
            with pytest.raises(OSError) as caught:
                reserve(ledger)
        assert caught.value is primary
        assert ledger.invocation_events() == []
        assert not (tmp_path / "anthropic.jsonl").exists()
        assert reserve(ledger)
    finally:
        ledger.close()
