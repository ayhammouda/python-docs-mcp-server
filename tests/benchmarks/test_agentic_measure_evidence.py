"""Local consistency checks; synthetic receipts do not authenticate hosted execution."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from benchmarks.agentic.count import AnthropicCountClient, count_item, count_item_from_transcript
from benchmarks.agentic.schemas import schemas_sha256
from benchmarks.agentic_report import _live_count
from benchmarks.grading.aggregate import write_vote_record
from benchmarks.grading.batch import anthropic_result, gemini_result, openai_result
from benchmarks.grading.inputs import judge_schema, safe_id
from benchmarks.grading.panel import load_panel
from benchmarks.report_measurements import grading_receipt_matches
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks.accepted_report_fixture import raw_result, write


def _grading(tmp_path: Path, *, history: bool = False) -> None:
    panel = load_panel(Path("docs/benchmarks/grading/panel.yml"))
    members = ("haiku", "luna", "flash-lite")
    judges = {j: vars(panel.judges[j]) for j in (*members, "reference")}
    efforts = dict.fromkeys(members, "low")
    item = safe_id("python-docs--model--r1", "question")
    chosen, events, receipts = {}, [], {}
    members_with_reference = {**efforts, "reference": "high"}
    for j, effort in members_with_reference.items():
        judge = panel.judges[j]
        text = json.dumps(
            {
                "explanation": "local",
                "attempted": True,
                "p1_met": True,
                "p2_met": True,
                "contradicts_key": False,
                "wrong_version": False,
            }
        )
        raw = raw_result(judge.provider, item, judge.model_id, text)
        result = {"anthropic": anthropic_result, "openai": openai_result, "google": gemini_result}[
            judge.provider
        ](raw)
        target, run_id = ("reference", "ref") if j == "reference" else ("answers", "local")
        path = write_vote_record(
            tmp_path / "grading",
            target=target,
            judge=judge,
            effort=effort,
            pass_number=1,
            grading_run_id=run_id,
            attempt=1,
            result=result,
            terminal=False,
            unit_id=f"unit-{j}",
            provider_batch_id=f"batch-{j}",
            input_sha256="d" * 64,
            schema=judge_schema(),
        )
        chosen[j] = {
            "model_id": judge.model_id,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        if history:
            vote = json.loads(path.read_bytes())
            receipt = {
                k: vote[k]
                for k in (
                    "grading_run_id",
                    "unit_id",
                    "item_id",
                    "judge_id",
                    "target",
                    "pass",
                    "attempt",
                    "provider_batch_id",
                    "input_sha256",
                    "model_id",
                    "effort",
                )
            }
            receipt.update(
                vote_sha256=chosen[j]["sha256"],
                provider=judge.provider,
                api=judge.api,
                result=asdict(result),
            )
            relative = f"{j}/{item}--a1.json"
            body = write(tmp_path / "grading/executions" / run_id / relative, receipt)
            receipts[j] = (relative, hashlib.sha256(body).hexdigest())
        unit = {"unit_id": f"unit-{j}", "run_id": run_id, "provider": judge.provider}
        events.extend(
            [
                {
                    **unit,
                    "event": "reserve",
                    "kind": "batch",
                    "model_id": judge.model_id,
                    "job_id": f"{j}--grading",
                    "bound": "1",
                    "input_bound": 100,
                    "output_bound": 100,
                },
                {
                    **unit,
                    "event": "settle",
                    "outcome": "batch",
                    "actual": "0.01",
                    "booked": "0.01",
                    "items": [{"item_id": item, "status": "ok", "booked": "0.01"}],
                },
            ]
        )
    for run_id, target, members in (
        ("local", "answers", efforts),
        ("ref", "reference", {"reference": "high"}),
    ):
        providers = {judges[j]["provider"] for j in members}
        write(
            tmp_path / "grading/runs" / f"{run_id}.json",
            {
                "grading_run_id": run_id,
                "target": target,
                "pass": 1,
                "composition": {
                    j: {"model_id": judges[j]["model_id"], "effort": e} for j, e in members.items()
                },
                "status": "completed",
                "dry_run": False,
                "not_run": [],
                "units": [f"unit-{j}" for j in members],
                "records_written": len(members),
                "spent_before": dict.fromkeys(providers, "0"),
                "spent_after": dict.fromkeys(providers, "0.01"),
                "execution_results": dict(receipts[j] for j in members) if history else {},
            },
        )
    write(tmp_path / "events.json", {"events": events})


def _receipts_match(root: Path, events: list[dict[str, Any]] | None = None) -> bool:
    """Exercise the public receipt join alone, without inventing a grade or request."""
    panel = load_panel(Path("docs/benchmarks/grading/panel.yml"))
    if events is None:
        events = json.loads((root / "events.json").read_bytes())["events"]
    paths = list((root / "grading/votes").rglob("*.json"))
    if not paths:
        return False
    for path in paths:
        body = path.read_bytes()
        vote = json.loads(body)
        run = json.loads((root / "grading/runs" / f"{vote['grading_run_id']}.json").read_bytes())
        try:
            matched = grading_receipt_matches(
                root, vote, hashlib.sha256(body).hexdigest(),
                vars(panel.judges[vote["judge_id"]]), run, events,
            )
        except BenchmarkValidationError:
            raise  # Unsafe reads and scanner failures must retain their exception.
        except (FileNotFoundError, ValueError, KeyError, TypeError, AttributeError, IndexError):
            matched = False  # The ordinary renderer uses this same unavailable-evidence fallback.
        if not matched:
            return False
    return True


def test_real_vote_writer_without_execution_records_is_unknown(tmp_path: Path) -> None:
    import shutil

    _grading(tmp_path, history=True)
    assert _receipts_match(tmp_path)
    shutil.rmtree(tmp_path / "grading/executions")
    assert not _receipts_match(tmp_path)


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "no-reserve",
        "no-settle",
        "foreign-run",
        "wrong-job",
        "wrong-model",
        "wrong-provider",
        "duplicate-reserve",
        "duplicate-settle",
        "failed",
        "free",
        "wrong-item",
        "bad-total",
        "bad-spend",
        "no-history",
        "history-hash",
        "raw-hash",
        "raw-item",
        "raw-usage",
        "raw-model",
        "scripted",
        "aborted",
        "dry-run",
        "missing-unit",
        "vote-hash",
        "reference-no-reserve",
    ],
)
def test_grading_receipts_are_required_for_each_panel_and_reference_item(
    tmp_path: Path, bad: str | None
) -> None:
    _grading(tmp_path, history=True)
    assert _receipts_match(tmp_path)
    events = json.loads((tmp_path / "events.json").read_bytes())["events"]
    run_path = tmp_path / "grading/runs/local.json"
    run = json.loads(run_path.read_bytes())
    if bad == "no-reserve":
        events.pop(0)
    elif bad == "no-settle":
        events.pop(1)
    elif bad == "reference-no-reserve":
        events = [e for e in events if e["unit_id"] != "unit-reference"]
    elif bad == "foreign-run":
        events[0]["run_id"] = "foreign"
    elif bad in ("wrong-job", "wrong-model", "wrong-provider"):
        events[0][
            {"wrong-job": "job_id", "wrong-model": "model_id", "wrong-provider": "provider"}[bad]
        ] = "foreign"
    elif bad in ("duplicate-reserve", "duplicate-settle"):
        events.append(events[bad == "duplicate-settle"])
    elif bad in ("failed", "free", "wrong-item", "bad-total"):
        row = events[1]["items"][0]
        if bad == "failed":
            events[1]["outcome"] = "worker_lost"
        elif bad == "free":
            row.update(status="unbilled", booked="0")
        elif bad == "wrong-item":
            row["item_id"] = "foreign"
        else:
            events[1]["booked"] = "2"
    elif bad == "bad-spend":
        run["spent_after"]["anthropic"] = "10"
    elif bad in ("aborted", "dry-run", "missing-unit"):
        run[{"aborted": "status", "dry-run": "dry_run", "missing-unit": "units"}[bad]] = {
            "aborted": "aborted",
            "dry-run": True,
            "missing-unit": [],
        }[bad]
    elif bad in ("no-history", "history-hash"):
        if bad == "no-history":
            run["execution_results"] = {}
        else:
            run["execution_results"][next(iter(run["execution_results"]))] = "0" * 64
    elif bad in ("raw-hash", "raw-item", "raw-usage", "raw-model", "scripted", "vote-hash"):
        relative = next(r for r in run["execution_results"] if r.startswith("haiku/"))
        path = tmp_path / "grading/executions/local" / relative
        receipt = json.loads(path.read_bytes())
        raw = receipt["result"]["raw"]
        if bad == "raw-item":
            raw["custom_id"] = "foreign"
        elif bad == "raw-usage":
            raw["result"]["message"]["usage"]["input_tokens"] = 99
        elif bad == "raw-model":
            raw["result"]["message"]["model"] = "foreign"
        elif bad == "scripted":
            raw["scripted_rule"] = {}
        elif bad == "vote-hash":
            receipt["vote_sha256"] = "0" * 64
        else:
            raw["unexpected"] = True
        body = write(path, receipt)
        if bad != "raw-hash":
            run["execution_results"][relative] = hashlib.sha256(body).hexdigest()
    write(run_path, run)
    assert _receipts_match(tmp_path, events) is (bad is None)


def test_counter_labels_without_measurements_are_insufficient() -> None:
    token = {
        "status": "counted",
        "approximation": False,
        "count_model": "claude-sonnet-5-5",
        "execution": {
            "provider": "anthropic",
            "api": "anthropic-messages",
            "model_id": "claude-sonnet-5-5",
        },
        "client_wrapped_tokens": 500,
    }
    assert _live_count(token) is False


async def _count_bundle(
    root: Path, *, empty: bool = False, tool: bool = False, no_reply: bool = False
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Actual counter + recording-only SDK; archive in the production stage shape."""
    prompt = "synthetic prompt"
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": [] if empty else [{"type": "text", "text": "answer"}]},
    ]
    calls = [{"message_count": 1, "tool_calls_requested": 0, "raw_response": {"content": []}}]
    if no_reply:
        messages.pop()
        calls[0].update(tool_calls_requested=None, raw_response=None)
    if tool:
        messages[1]["content"] = [
            {"type": "tool_use", "name": "unoffered", "id": "raw-id", "input": {}}
        ]
        messages.extend(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "raw-id", "content": "payload"}
                    ],
                },
                {"role": "assistant", "content": [{"type": "text", "text": "answer"}]},
            ]
        )
        calls[0].update(
            tool_calls_requested=1,
            raw_response={"content": [{"type": "tool_use", "input": "bad JSON"}]},
        )
        calls.append(
            {"message_count": 3, "tool_calls_requested": 0, "raw_response": {"content": []}}
        )
    transcript = {
        "competitor_id": "python-docs--model--r1",
        "corpus_id": "question",
        "system_id": "python-docs",
        "model_family_id": "model",
        "provider": "anthropic",
        "tool_model_key": "python-docs:anthropic/model",
        "system_prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "tools_sha256": schemas_sha256([]),
        "canonical_messages": messages,
        "model_calls": calls,
    }
    transcript_body = write(root / "transcripts/python-docs--model--r1/question.json", transcript)
    system_body = write(root / "systems/model--python-docs.json", {"tools_raw": []})
    (root / "snapshots").mkdir()
    (root / "snapshots/system-prompt.txt").write_text(prompt)
    item = count_item_from_transcript(transcript, system_prompt=prompt, tools_raw=[])
    seen = []

    async def count_tokens(**request: Any) -> Any:
        seen.append(request)
        assert request["model"] == "claude-sonnet-5-5"
        # Deterministic recorded values, with nonzero output differences.
        values = [10] if empty else ([10, 20, 15, 10, 20, 15, 30, 5] if tool else [10, 20, 15])
        return SimpleNamespace(input_tokens=values[len(seen) - 1])

    sdk = SimpleNamespace(messages=SimpleNamespace(count_tokens=count_tokens))
    record = await count_item(AnthropicCountClient(sdk, "claude-sonnet-5-5"), item)
    frozen = {
        "item": asdict(item),
        "transcript_sha256": hashlib.sha256(transcript_body).hexdigest(),
        "system_sha256": hashlib.sha256(system_body).hexdigest(),
    }
    body = json.dumps(frozen, indent=2, sort_keys=True).encode() + b"\n"
    record.update(count_run_id="count-local", input_sha256=hashlib.sha256(body).hexdigest())
    write(
        root / "grading/count-inputs.json",
        {hashlib.sha256(f"{item.cell}/{item.corpus_id}".encode()).hexdigest(): frozen},
    )
    _save_count(root, record)
    assert len(seen) == record["count_calls"]
    return transcript, record


def _save_count(root: Path, record: dict[str, Any]) -> None:
    from benchmarks.agentic.count import CountItem
    from benchmarks.agentic.count_evidence import planned_count_calls

    frozen = json.loads((root / "grading/count-inputs.json").read_bytes())
    item = CountItem(**next(iter(frozen.values()))["item"])
    relative = "python-docs--model--r1/question.json"
    body = write(root / "tokens" / relative, record)
    write(root / "grading/counts/count-local" / relative, record)
    write(
        root / "grading/count-runs/count-local.json",
        {
            "count_run_id": "count-local",
            "status": "completed",
            "dry_run": False,
            "units": [],
            "not_run": [],
            "worker_errors": [],
            "records_written": 1,
            "items": 1,
            "count_calls": planned_count_calls(item),
            "results": {relative: hashlib.sha256(body).hexdigest()},
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        None,
        "missing-per-call",
        "missing-prompt",
        "wrong-prompt",
        "wrong-tools",
        "wrong-total",
        "bool-total",
        "negative-input",
        "wrong-index",
        "missing-call",
        "extra-call",
        "raw-tool-count",
        "wrong-method",
        "wrong-model",
        "wrong-label",
        "failed",
        "scripted",
        "no-inputs",
        "input-hash",
        "transcript-hash",
        "system-hash",
        "no-archive",
        "archive-bytes",
        "receipt-hash",
        "receipt-run",
        "receipt-total",
        "receipt-partial",
        "receipt-free",
        "too-few-count-calls",
        "too-many-count-calls",
        "impossible-clamp",
        "payload-without-tools",
    ],
)
async def test_counts_join_real_per_call_measurements_and_export_receipts(
    tmp_path: Path, bad: str | None
) -> None:
    transcript, record = await _count_bundle(tmp_path)
    assert _live_count(record, root=tmp_path, transcript=transcript)
    if bad == "missing-per-call":
        record.pop("per_call")
    elif bad == "missing-prompt":
        record.pop("system_prompt_sha256")
    elif bad in (
        "wrong-prompt",
        "wrong-tools",
        "wrong-total",
        "bool-total",
        "wrong-method",
        "wrong-model",
        "wrong-label",
        "failed",
        "scripted",
        "input-hash",
    ):
        key, value = {
            "wrong-prompt": ("system_prompt_sha256", "f" * 64),
            "wrong-tools": ("tools_sha256", "f" * 64),
            "wrong-total": ("client_wrapped_tokens", 500),
            "bool-total": ("client_wrapped_tokens", True),
            "wrong-method": ("approximation", True),
            "wrong-model": ("count_model", "other"),
            "wrong-label": ("token_label", "invented"),
            "failed": ("status", "failed"),
            "scripted": (
                "execution",
                {"provider": "scripted", "api": "scripted", "model_id": "bytes/4"},
            ),
            "input-hash": ("input_sha256", "f" * 64),
        }[bad]
        record[key] = value
    elif bad == "negative-input":
        record["per_call"][0]["input_tokens"] = -1
    elif bad == "wrong-index":
        record["per_call"][0]["index"] = True
    elif bad == "missing-call":
        record["per_call"] = []
    elif bad == "extra-call":
        record["per_call"].append({"index": 1, "input_tokens": 0, "output_tokens": 0})
    elif bad == "raw-tool-count":
        transcript["model_calls"][0]["tool_calls_requested"] = 1
    elif bad in (
        "too-few-count-calls",
        "too-many-count-calls",
        "impossible-clamp",
        "payload-without-tools",
    ):
        key, value = {
            "too-few-count-calls": ("count_calls", 2),
            "too-many-count-calls": ("count_calls", 100),
            "impossible-clamp": ("negative_output_clamped", 1),
            "payload-without-tools": ("raw_payload_tokens", 1),
        }[bad]
        record[key] = value
    _save_count(tmp_path, record)
    if bad in ("no-inputs", "transcript-hash", "system-hash"):
        path = tmp_path / "grading/count-inputs.json"
        inputs = json.loads(path.read_bytes())
        if bad == "no-inputs":
            inputs = {}
        else:
            next(iter(inputs.values()))[
                {"transcript-hash": "transcript_sha256", "system-hash": "system_sha256"}[bad]
            ] = "f" * 64
        write(path, inputs)
    elif bad in ("no-archive", "archive-bytes"):
        path = tmp_path / "grading/counts/count-local/python-docs--model--r1/question.json"
        if bad == "no-archive":
            path.unlink()
        else:
            path.write_bytes(path.read_bytes() + b" ")
    elif bad in ("receipt-hash", "receipt-run", "receipt-total", "receipt-partial", "receipt-free"):
        path = tmp_path / "grading/count-runs/count-local.json"
        receipt = json.loads(path.read_bytes())
        if bad == "receipt-hash":
            receipt["results"][next(iter(receipt["results"]))] = "f" * 64
        else:
            key, value = {
                "receipt-run": ("count_run_id", "foreign"),
                "receipt-total": ("records_written", 2),
                "receipt-partial": ("status", "partial"),
                "receipt-free": ("dry_run", True),
            }[bad]
            receipt[key] = value
        write(path, receipt)
    assert _live_count(record, root=tmp_path, transcript=transcript) is (bad is None)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["empty", "tool", "no-reply"])
async def test_counter_empty_output_and_raw_tool_arguments_are_reconciled(
    tmp_path: Path, kind: str
) -> None:
    transcript, record = await _count_bundle(
        tmp_path, empty=kind == "empty", tool=kind == "tool", no_reply=kind == "no-reply"
    )
    assert _live_count(record, root=tmp_path, transcript=transcript)
    if kind == "empty":
        assert record["per_call"][0]["output_tokens"] == 0
        record["per_call"][0]["output_tokens"] = 1
        record["client_wrapped_tokens"] += 1
    elif kind == "tool":
        assert record["raw_argument_calls"] == 1
        assert record["stubbed_tools"] == ["unoffered"]
        assert record["raw_payload_tokens"] == 25
        record["raw_argument_calls"] = 0
    else:
        assert record["per_call"] == []
        assert record["client_wrapped_tokens"] == record["count_calls"] == 0
        assert record["uncounted_calls"] == 1
        record["client_wrapped_tokens"] = 1
    _save_count(tmp_path, record)
    assert not _live_count(record, root=tmp_path, transcript=transcript)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "relative",
    [
        "snapshots/system-prompt.txt",
        "systems/model--python-docs.json",
        "grading/count-inputs.json",
        "grading/count-runs/count-local.json",
        "grading/counts/count-local/python-docs--model--r1/question.json",
    ],
)
async def test_count_evidence_reads_preserve_export_confinement(
    tmp_path: Path, relative: str
) -> None:
    from benchmarks.runner import BenchmarkValidationError

    transcript, record = await _count_bundle(tmp_path)
    assert _live_count(record, root=tmp_path, transcript=transcript)
    path = tmp_path / relative
    target = tmp_path / "unrelated.json"
    path.rename(target)
    path.symlink_to(target)
    with pytest.raises(BenchmarkValidationError, match="symlink"):
        _live_count(record, root=tmp_path, transcript=transcript)


def test_grading_history_reads_preserve_export_confinement(tmp_path: Path) -> None:
    from benchmarks.runner import BenchmarkValidationError

    _grading(tmp_path, history=True)
    assert _receipts_match(tmp_path)
    events = json.loads((tmp_path / "events.json").read_bytes())["events"]
    path = next((tmp_path / "grading/executions/local").glob("haiku/*.json"))
    target = tmp_path / "unrelated.json"
    path.rename(target)
    path.symlink_to(target)
    with pytest.raises(BenchmarkValidationError, match="symlink"):
        _receipts_match(tmp_path, events)


@pytest.mark.asyncio
async def test_positive_counter_bundle_verifies_after_export_without_network_or_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil
    import socket
    import subprocess

    source = tmp_path / "source"
    source.mkdir()
    transcript, record = await _count_bundle(source, tool=True)
    exported = tmp_path / "export"
    shutil.copytree(source, exported)
    shutil.rmtree(source)

    def unavailable(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("export verifier cannot use network or subprocess state")

    monkeypatch.setattr(socket.socket, "connect", unavailable)
    monkeypatch.setattr(socket, "create_connection", unavailable)
    monkeypatch.setattr(subprocess, "run", unavailable)
    monkeypatch.chdir(exported)
    assert _live_count(record, root=exported, transcript=transcript)


def test_synthetic_export_keeps_technical_results_and_unknown_grading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil
    import socket
    import subprocess

    from benchmarks.agentic_report import _RunMetadata
    from benchmarks.report import _load_run_bundle, generate_readme_summary, generate_report
    from tests.benchmarks.test_agentic_report import METHODOLOGY, _live_like_run, _validated

    source = _validated(_live_like_run(tmp_path, evidence=False))
    exported = tmp_path / "export"
    shutil.copytree(source, exported)
    methodology = exported / "methodology.md"
    shutil.copyfile(METHODOLOGY, methodology)
    shutil.rmtree(source)

    def unavailable(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("report must not use network or git/local subprocess state")

    monkeypatch.setattr(socket, "create_connection", unavailable)
    monkeypatch.setattr(socket.socket, "connect", unavailable)
    monkeypatch.setattr(subprocess, "run", unavailable)
    monkeypatch.chdir(exported)
    metadata = _RunMetadata(_load_run_bundle(exported))
    assert metadata.grading == "unknown"
    report = generate_report(
        exported,
        methodology_path=methodology,
        model_matrix_path=exported / "snapshots/model-matrix.yml",
    ).read_text()
    readme = generate_readme_summary(exported, methodology_path=methodology).read_text()
    assert "0.500" in report
    assert "fewer normalized tokens" not in readme
    assert "unknown" in readme.lower() or "unverified" in readme.lower()
