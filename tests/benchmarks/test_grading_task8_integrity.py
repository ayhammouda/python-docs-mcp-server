"""Task 8 integrity regressions through the public grading runner and CLI."""

import asyncio
import errno
import json
from decimal import Decimal
from pathlib import Path

import pytest

from benchmarks import __main__ as cli
from benchmarks.agentic.host import LocalLauncher
from benchmarks.agentic.ledger import Ledger
from benchmarks.agentic.protocol import KEY_SHAPED
from benchmarks.grading import run
from tests.benchmarks.test_grading_jobs import RESERVE, StubLauncher
from tests.benchmarks.test_grading_run import cli_args, config
from tests.benchmarks.test_grading_run import scripted_run as scripted_run


@pytest.mark.parametrize("artifact", ["journal", "summary", "both"])
def test_full_artifact_volume_preserves_key_leak_exit_and_durable_spend(
    scripted_run, monkeypatch, capsys, artifact
):
    cfg = config(scripted_run.parent, grading_run_id="full-volume")
    root = scripted_run / "grading"
    original_open, original_link = Path.open, run.os.link

    def full_open(path, mode="r", *args, **kwargs):
        if (
            artifact in ("journal", "both")
            and path == root / "ledger-events.jsonl"
            and mode == "ab"
        ):
            raise OSError(errno.ENOSPC, "artifact volume full")
        if artifact == "both" and root / "runs" in path.parents and "w" in mode:
            raise OSError(errno.ENOSPC, "artifact volume full")
        return original_open(path, mode, *args, **kwargs)

    def full_link(source, dest, *args, **kwargs):
        if artifact == "summary" and dest == root / "runs/full-volume.json":
            raise OSError(errno.ENOSPC, "artifact volume full")
        return original_link(source, dest, *args, **kwargs)

    monkeypatch.setattr(Path, "open", full_open)
    monkeypatch.setattr(run.os, "link", full_link)
    # A real framed worker reserves before its output guard detects a synthetic leak.
    launcher = StubLauncher(RESERVE + "send({'type':'heartbeat','text':'sk-ant-api03-'+'x'*90})")
    monkeypatch.setattr(run, "LocalLauncher", lambda: launcher)
    with pytest.raises(SystemExit) as caught:
        cli.main(cli_args(cfg, "--grading-run-id", "full-volume"))
    assert caught.value.code == 3
    output = capsys.readouterr()
    assert not output.out and not KEY_SHAPED.search(output.err)
    assert "full-volume" in output.err and "OSError" in output.err
    events = [
        json.loads(line)
        for path in cfg.ledger_dir.glob("*.jsonl")
        for line in path.read_text().splitlines()
    ]
    reserved = {e["unit_id"] for e in events if e["event"] == "reserve"}
    assert reserved and reserved == {e["unit_id"] for e in events if e["event"] == "settle"}
    assert all(unit in output.err for unit in reserved)
    assert sum(Decimal(e["booked"]) for e in events if e["event"] == "settle") > 0
    # The other artifact is still attempted; ENOSPC must never prevent ledger unlock.
    if artifact == "journal":
        assert json.loads((root / "runs/full-volume.json").read_text())["status"] == "aborted"
    elif artifact == "summary":
        assert (root / "ledger-events.jsonl").read_text()
    else:
        assert not (root / "runs/full-volume.json").exists()
    with_ledger = Ledger.open(cfg.ledger_dir, run_id="inspect")
    with_ledger.close()
    with pytest.raises(run.BenchmarkValidationError, match="already used"):
        run.run_grading(cfg)


def test_cancellation_survives_artifact_and_ledger_close_errors(scripted_run, monkeypatch):
    cfg = config(scripted_run.parent, grading_run_id="cancelled")
    primary = asyncio.CancelledError("cancelled during preparation")
    original_open, original_close = Path.open, Ledger.close

    class CancelledLauncher(LocalLauncher):
        async def prepare(self, jobs):
            raise primary

    def full_open(path, mode="r", *args, **kwargs):
        if path == scripted_run / "grading/ledger-events.jsonl" and mode == "ab":
            raise OSError(errno.ENOSPC, "artifact volume full")
        return original_open(path, mode, *args, **kwargs)

    def bad_close(ledger):
        original_close(ledger)
        raise OSError("synthetic close failure")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", full_open)
        patch.setattr(Ledger, "close", bad_close)
        with pytest.raises(asyncio.CancelledError) as caught:
            run.run_grading(cfg, launcher=CancelledLauncher())
    assert caught.value is primary
    assert "cancelled" in " ".join(caught.value.__notes__)
    assert "OSError" in " ".join(caught.value.__notes__)
    ledger = Ledger.open(cfg.ledger_dir, run_id="inspect")
    ledger.close()


def test_export_failure_after_success_is_reported_with_recovery_ids(
    scripted_run, monkeypatch, capsys
):
    cfg = config(scripted_run.parent)
    original_open = Path.open

    def full_open(path, mode="r", *args, **kwargs):
        if path == scripted_run / "grading/ledger-events.jsonl" and mode == "ab":
            raise OSError(errno.ENOSPC, "artifact volume full")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", full_open)
    with pytest.raises(SystemExit) as caught:
        cli.main(cli_args(cfg, "--grading-run-id", "export-failed"))
    assert caught.value.code == 2
    output = capsys.readouterr()
    assert not output.out and "export-failed" in output.err and "OSError" in output.err
    assert not KEY_SHAPED.search(output.err)
    summary = json.loads((scripted_run / "grading/runs/export-failed.json").read_text())
    assert summary["status"] == "completed" and summary["records_written"] == 6
    assert summary["units"] and all(unit in output.err for unit in summary["units"])
    ledger = Ledger.open(cfg.ledger_dir, run_id="inspect")
    ledger.close()
