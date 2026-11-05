"""Retry totals must come from genuine counters in complete exported invocations."""

from __future__ import annotations

import asyncio
import copy
import json
import shutil
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from benchmarks.agentic.clients import ProviderCallError
from benchmarks.agentic.count import AnthropicCountClient, count_item, count_item_from_transcript
from benchmarks.agentic_report import _live_count
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks.accepted_report_fixture import digest, read, write
from tests.benchmarks.test_agentic_measure_evidence import _count_bundle


async def _record_count(item: Any, retries: int, total: int = 15) -> dict[str, Any]:
    """Only replace the external SDK endpoint; run production retries and accounting."""
    attempts = 0

    async def count_tokens(**request: Any) -> Any:
        nonlocal attempts
        assert request["model"] == "claude-sonnet-5-5"
        attempts += 1
        if attempts <= retries:
            raise ProviderCallError("rate_limited", "recorded benign limit", status=429)
        return SimpleNamespace(input_tokens=[total - 5, 20, 15][attempts - retries - 1])

    async def no_sleep(_: float) -> None:
        pass

    sdk = SimpleNamespace(messages=SimpleNamespace(count_tokens=count_tokens))
    result = await count_item(AnthropicCountClient(sdk, "claude-sonnet-5-5"), item, sleep=no_sleep)
    assert result["status"] == "counted"
    assert result["count_calls"] == attempts == 3 + retries
    assert result["client_wrapped_tokens"] == total
    return result


async def _invocation(
    root: Path, retries: tuple[int, ...]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Path]:
    template, _ = await _count_bundle(root)
    inputs, results, transcripts, tokens = {}, {}, [], []
    for index, retry in enumerate(retries):
        transcript = copy.deepcopy(template)
        transcript["corpus_id"] = "question" if index == 0 else f"question-{index}"
        cell, qid = transcript["competitor_id"], transcript["corpus_id"]
        relative = f"{cell}/{qid}.json"
        transcript_body = write(root / "transcripts" / relative, transcript)
        item = count_item_from_transcript(
            transcript, system_prompt="synthetic prompt", tools_raw=[]
        )
        frozen = {
            "item": asdict(item),
            "transcript_sha256": digest(transcript_body),
            "system_sha256": digest((root / "systems/model--python-docs.json").read_bytes()),
        }
        inputs[digest(f"{cell}/{qid}".encode())] = frozen
        token = await _record_count(item, retry)
        token.update(
            count_run_id="count-local",
            input_sha256=digest(json.dumps(frozen, indent=2, sort_keys=True).encode() + b"\n"),
        )
        body = write(root / "tokens" / relative, token)
        write(root / "grading/counts/count-local" / relative, token)
        results[relative] = digest(body)
        transcripts.append(transcript)
        tokens.append(token)
    write(root / "grading/count-inputs.json", inputs)
    receipt_path = root / "grading/count-runs/count-local.json"
    receipt = read(receipt_path)
    receipt.update(
        items=len(tokens), records_written=len(tokens), results=results,
        count_calls=sum(t["count_calls"] for t in tokens),
    )
    write(receipt_path, receipt)
    return transcripts, tokens, receipt_path


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "retries,total", [((0,), 3), ((0, 0), 6), ((1,), 4), ((1, 0), 7), ((1, 2), 9)],
)
async def test_invocation_reconciles_actual_retry_inclusive_counts(
    tmp_path: Path, retries: tuple[int, ...], total: int
) -> None:
    transcripts, tokens, receipt_path = await _invocation(tmp_path, retries)
    assert read(receipt_path)["count_calls"] == total
    for transcript, token in zip(transcripts, tokens, strict=True):
        assert _live_count(token, root=tmp_path, transcript=transcript)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "retries,fabricated",
    [((1,), 3), ((1, 0), 3), ((1, 0), 4), ((1, 0), 6), ((1, 0), 8), ((1, 0), True)],
)
async def test_invocation_rejects_minimum_or_single_item_or_fabricated_total(
    tmp_path: Path, retries: tuple[int, ...], fabricated: Any
) -> None:
    transcripts, tokens, receipt_path = await _invocation(tmp_path, retries)
    receipt = read(receipt_path)
    receipt["count_calls"] = fabricated
    write(receipt_path, receipt)
    assert not _live_count(tokens[0], root=tmp_path, transcript=transcripts[0])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    ["partial", "missing-receipt", "missing-result", "missing-manifest-item", "forged-hash",
     "stale-transcript", "stale-system", "stale-prompt", "stale-input", "identity",
     "transcript-identity", "wrong-run", "unbudgeted-unit", "not-run", "worker-error",
     "invalid-item-calls", "symlink", "secret"],
)
async def test_every_invocation_item_is_bound_to_current_safe_inputs(
    tmp_path: Path, bad: str
) -> None:
    # Validate the first item while corrupting its sibling in the same invocation.
    transcripts, tokens, receipt_path = await _invocation(tmp_path, (0, 0))
    receipt = read(receipt_path)
    relative = f"{transcripts[1]['competitor_id']}/question-1.json"
    archived = tmp_path / "grading/counts/count-local" / relative
    transcript_path = tmp_path / "transcripts" / relative
    if bad == "partial":
        receipt["status"] = "partial"
    elif bad == "missing-manifest-item":
        receipt["results"].pop(relative)
    elif bad == "missing-result":
        archived.unlink()
    elif bad == "forged-hash":
        receipt["results"][relative] = "f" * 64
    elif bad == "stale-transcript":
        changed = read(transcript_path)
        changed["canonical_messages"][0]["content"] = "changed question"
        write(transcript_path, changed)
    elif bad == "stale-system":
        write(tmp_path / "systems/model--python-docs.json", {"tools_raw": [], "edited": True})
    elif bad == "stale-prompt":
        (tmp_path / "snapshots/system-prompt.txt").write_text("changed prompt")
    elif bad == "stale-input":
        inputs = read(tmp_path / "grading/count-inputs.json")
        inputs.pop(digest(relative[:-5].encode()))
        write(tmp_path / "grading/count-inputs.json", inputs)
    elif bad == "identity":
        token = read(archived)
        token["corpus_id"] = "question"
        receipt["results"][relative] = digest(write(archived, token))
    elif bad == "transcript-identity":
        changed = read(transcript_path)
        changed["corpus_id"] = "question"
        write(transcript_path, changed)
    elif bad == "wrong-run":
        receipt["count_run_id"] = "foreign-run"
    elif bad == "unbudgeted-unit":
        receipt["units"] = ["invented-unit"]
    elif bad == "not-run":
        receipt["not_run"] = ["question-1"]
    elif bad == "worker-error":
        receipt["worker_errors"] = ["recorded error"]
    elif bad == "invalid-item-calls":
        token = read(archived)
        token["count_calls"] = 0
        receipt["count_calls"] = 3
        receipt["results"][relative] = digest(write(archived, token))
    elif bad == "symlink":
        archived.unlink()
        archived.symlink_to(tmp_path / "tokens" / relative)
    elif bad == "secret":
        # Bypass the safe test writer to exercise the production reader's scan.
        archived.write_text(json.dumps({"secret": "sk-" + "x" * 48}))
    write(receipt_path, receipt)
    if bad == "missing-receipt":
        receipt_path.unlink()
    if bad in ("symlink", "secret"):
        with pytest.raises(BenchmarkValidationError):
            _live_count(tokens[0], root=tmp_path, transcript=transcripts[0])
    else:
        assert not _live_count(tokens[0], root=tmp_path, transcript=transcripts[0])


@pytest.mark.asyncio
async def test_retry_invocation_is_portable_without_source_or_external_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket
    import subprocess

    source = tmp_path / "source"
    source.mkdir()
    transcripts, tokens, _ = await _invocation(source, (1, 2))
    exported = tmp_path / "export"
    shutil.copytree(source, exported)
    shutil.rmtree(source)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("no external state available")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.chdir(exported)
    for transcript, token in zip(transcripts, tokens, strict=True):
        assert _live_count(token, root=exported, transcript=transcript)


def test_public_renderers_reconcile_a_complete_retry_invocation_after_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket

    import benchmarks.report_measurements as measurements
    from benchmarks.report import generate_readme_summary, generate_report
    from tests.benchmarks.test_agentic_report import FAMILIES, METHODOLOGY, _live_like_run, _readme

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("no network in a recording-only report")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    source = _live_like_run(tmp_path, full=True)
    claim = f"- {FAMILIES[0]}: python-docs-mcp-server uses"
    assert claim in _readme(source)  # Genuine ordinary no-retry control.
    inputs = read(source / "grading/count-inputs.json")
    cell = f"python-docs--{FAMILIES[0]}--r1"
    paths = sorted((source / "transcripts" / cell).glob("*.json"))[:2]
    assert len(paths) == 2
    results = {}

    async def recount() -> None:
        for path, retries in zip(paths, (1, 2), strict=True):
            transcript = read(path)
            relative = f"{cell}/{path.name}"
            item = count_item_from_transcript(
                transcript,
                system_prompt=(source / "snapshots/system-prompt.txt").read_text(),
                tools_raw=read(
                    source / "systems" / f"{FAMILIES[0]}--python-docs.json"
                )["tools_raw"],
            )
            result = await _record_count(item, retries, total=500)
            frozen = inputs[digest(f"{cell}/{path.stem}".encode())]
            result.update(
                count_run_id="retry-joined",
                input_sha256=digest(json.dumps(frozen, indent=2, sort_keys=True).encode() + b"\n"),
            )
            body = write(source / "tokens" / relative, result)
            write(source / "grading/counts/retry-joined" / relative, result)
            results[relative] = digest(body)

    asyncio.run(recount())
    write(source / "grading/count-runs/retry-joined.json", {
        "count_run_id": "retry-joined", "status": "completed", "dry_run": False,
        "units": [], "not_run": [], "worker_errors": [], "items": 2,
        "records_written": 2, "count_calls": 9, "results": results,
    })
    exported = tmp_path / "export"
    shutil.copytree(source, exported)
    methodology = exported / "methodology.md"
    shutil.copyfile(METHODOLOGY, methodology)
    shutil.rmtree(source)
    reads: dict[Path, int] = {}
    reader = measurements.artifact_bytes

    def counted_reader(root: Path, path: Path) -> bytes:
        reads[path] = reads.get(path, 0) + 1
        return reader(root, path)

    monkeypatch.setattr(measurements, "artifact_bytes", counted_reader)
    assert claim in generate_readme_summary(exported, methodology_path=methodology).read_text()
    assert reads and max(reads.values()) == 1
    reads.clear()
    report = generate_report(
        exported, methodology_path=methodology,
        model_matrix_path=exported / "snapshots/model-matrix.yml",
    ).read_text()
    assert "0.500" in report
    assert reads and max(reads.values()) == 1
    receipt_path = exported / "grading/count-runs/retry-joined.json"
    receipt = read(receipt_path)
    receipt["count_calls"] = 6
    write(receipt_path, receipt)
    assert claim not in generate_readme_summary(exported, methodology_path=methodology).read_text()
