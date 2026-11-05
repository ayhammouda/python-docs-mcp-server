"""Read-only financial evidence for design §12; no grading or authorization here.

The displayed average is rounded and can be synthetic. Reconcile execution,
answer coverage and observed dollars before deriving an unrounded pilot rate.
These local artifacts are consistency evidence, not signed provider receipts.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from decimal import Decimal, DecimalException
from pathlib import Path
from typing import Any

from benchmarks.agentic.clients import _LIVE_APIS
from benchmarks.agentic.host import _verified_buckets
from benchmarks.agentic.inputs import read_json, read_jsonl
from benchmarks.agentic.protocol import KeyLeakError
from benchmarks.grading.aggregate import final_grade, load_answer_votes
from benchmarks.grading.gates import pass1_composition
from benchmarks.grading.inputs import (
    SNAPSHOT_NAMES,
    inputs_manifest,
    load_criteria,
    render_judge_input,
    safe_id,
    scanned_input,
)
from benchmarks.grading.panel import load_panel
from benchmarks.runner import BenchmarkValidationError


def _read(path: Path) -> Any:
    return read_json(path, "reference grading evidence")


def _money(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("boolean cost")
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError("cost must be finite and nonnegative")
    return amount


def reference_grading_rates(run_dir: Path) -> dict[str, Decimal]:
    """Per-provider observed dollars / actual fully graded pilot answers, fail closed."""
    try:
        return _reference_grading_rates(run_dir)
    except KeyLeakError:
        raise
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        IndexError,
        DecimalException,
        OverflowError,
    ) as exc:
        raise BenchmarkValidationError(f"invalid reference grading {run_dir}: {exc}") from exc


def _reference_grading_rates(run_dir: Path) -> dict[str, Decimal]:
    root = run_dir / "grading"
    summary = _read(root / "summary.json")
    spend = summary["spend"]
    inputs = root / "inputs"
    manifest = _read(inputs / "inputs.json")
    names = {**SNAPSHOT_NAMES, "mapping_table": "mapping.txt", "mask_rules": "masking.txt"}
    for key, digest in manifest.items():
        path = inputs / names[key]
        if hashlib.sha256(scanned_input(path, suffix=path.suffix)).hexdigest() != digest:
            raise ValueError("frozen grading inputs differ")
    # Rendering uses the current masking/mapping/prompt rules, just as agentic-grade.
    if manifest != inputs_manifest(
        criteria_path=inputs / names["criteria"],
        panel_path=inputs / names["panel"],
        probes_path=inputs / names["probes"] if "probes" in manifest else None,
    ):
        raise ValueError("grading inputs differ from the current renderer")
    panel = load_panel(inputs / names["panel"])
    questions = load_criteria(
        inputs / names["criteria"],
        corpus_path=run_dir / "snapshots/corpus.yml",
        schema_path=Path(__file__).resolve().parents[2] / "docs/benchmarks/corpus.schema.json",
    )
    composition = pass1_composition(root, "answers", panel)
    efforts = {j: spec["effort"] for j, spec in composition.items()}

    # Bind item identities and exact rendered requests to this pilot's successful answers.
    planned = {
        (c["competitor_id"], c["corpus_id"]) for c in _read(run_dir / "planned-cells.json")["cells"]
    }
    answers = {}
    for path in (run_dir / "transcripts").glob("*/*.json"):
        row = _read(path)
        key = row["competitor_id"], row["corpus_id"]
        if key not in planned:
            raise ValueError("unplanned pilot answer")
        if row.get("status") == "succeeded" and (row.get("answer") or "").strip():
            answers[key] = render_judge_input(questions[row["corpus_id"]], row["answer"])
    votes = load_answer_votes(root, panel=panel, judges=efforts, requests=answers)
    if not answers or votes.keys() != answers.keys():
        raise ValueError("grading items do not cover this pilot's answers")
    hashes = {}
    for key, value in answers.items():
        item = safe_id(*key)
        hashes[item] = value.sha256()
        if not all(votes[key].records.get(j, {}).get("final") for j in efforts):
            raise ValueError("pilot grading is pending")
        if final_grade(votes[key], questions[key[1]], inputs=manifest) is None:
            raise ValueError("pilot grading awaits the reference")
    denominator = len(answers)
    if (
        type(spend["graded_answers"]) is not int
        or spend["graded_answers"] != denominator
        or type(summary["graded_cells"]) is not int
        or summary["graded_cells"] != denominator
        or summary["pending_cells"] != []
    ):
        raise ValueError("grading denominator disagrees with completed pilot grades")

    runs = {}
    for path in (root / "runs").glob("*.json"):
        invocation = _read(path)
        run_id = invocation["grading_run_id"]
        if not isinstance(run_id, str) or not run_id.strip() or run_id != path.stem:
            raise ValueError("grading invocation identity differs from its filename")
        if (
            run_id in runs
            or invocation["status"] != "completed"
            or invocation["dry_run"] is not False
        ):
            raise ValueError("grading invocation is incomplete or synthetic")
        if invocation["not_run"]:
            raise ValueError("grading invocation has unexecuted items")
        for judge, spec in invocation["composition"].items():
            frozen = panel.judges[judge]
            if _LIVE_APIS.get(frozen.api) != frozen.provider or spec != {
                "model_id": frozen.model_id,
                "effort": frozen.effort,
            }:
                raise ValueError("grading invocation lacks matching live judge provenance")
        runs[run_id] = invocation
    if not runs:
        raise ValueError("no actual grading invocations")

    reserved, settled = {}, {}
    booked: dict[str, Decimal] = defaultdict(Decimal)
    by_run: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for event in read_jsonl(root / "ledger-events.jsonl", "reference grading ledger"):
        if event.get("event") not in ("reserve", "settle"):
            continue
        unit, run_id, provider = event["unit_id"], event["run_id"], event["provider"]
        if not all(isinstance(v, str) and v.strip() for v in (unit, run_id, provider)):
            raise ValueError("invalid grading ledger identity")
        invocation = runs[run_id]
        if event["event"] == "reserve":
            judge = event["job_id"].removesuffix("--grading")
            spec = panel.judges[judge]
            if (
                unit in reserved
                or event["kind"] != "batch"
                or event["job_id"] != f"{judge}--grading"
                or judge not in invocation["composition"]
                or (provider, event["model_id"]) != (spec.provider, spec.model_id)
            ):
                raise ValueError("invalid grading reservation provenance")
            reserved[unit] = event
        else:
            reservation = reserved[unit]
            if unit in settled or (provider, run_id) != (
                reservation["provider"],
                reservation["run_id"],
            ):
                raise ValueError("unmatched grading settlement")
            amount = _money(event["booked"])
            if event["outcome"] == "batch":
                items = event["items"]
                if (
                    len({i["item_id"] for i in items}) != len(items)
                    or sum((_money(i["booked"]) for i in items), Decimal(0)) != amount
                    or any(i["status"] not in ("ok", "unbilled", "no_usage") for i in items)
                    or sum((_money(i["booked"]) for i in items if i["status"] == "ok"), Decimal(0))
                    != _money(event["actual"])
                ):
                    raise ValueError("grading batch dollars disagree")
            settled[unit] = event
            booked[provider] += amount
            by_run[run_id][provider] += amount
    if reserved.keys() != settled.keys():
        raise ValueError("grading has unsettled reservations")
    for run_id, invocation in runs.items():
        units = [u for u, e in reserved.items() if e["run_id"] == run_id]
        if sorted(units) != sorted(invocation["units"]):
            raise ValueError("grading invocation units disagree")
        before, after = invocation["spent_before"], invocation["spent_after"]
        if before.keys() != after.keys() or booked.keys() - before.keys():
            raise ValueError("grading invocation is missing provider balances")
        for provider in before:
            if _money(after[provider]) - _money(before[provider]) != by_run[run_id][provider]:
                raise ValueError("grading settlements disagree with observed spend")

    records: Counter[str] = Counter()
    paid_answers = set()
    for path in (root / "votes").rglob("*.json"):
        record = _read(path)
        run_id, judge, item = record["grading_run_id"], record["judge_id"], record["item_id"]
        invocation = runs[run_id]
        unit = record["unit_id"]
        reservation, settlement = reserved[unit], settled[unit]
        if (
            reservation["run_id"] != run_id
            or reservation["job_id"] != f"{judge}--grading"
            or record["model_id"] != panel.judges[judge].model_id
            or record["effort"] != invocation["composition"][judge]["effort"]
            or (record["target"], record["pass"]) != (invocation["target"], invocation["pass"])
            or not isinstance(record["provider_batch_id"], str)
            or not record["provider_batch_id"].strip()
            or record["provider_batch_id"].startswith("scripted-")
        ):
            raise ValueError("vote lacks matching live batch execution")
        if record["target"] in ("answers", "reference") and record["input_sha256"] != hashes[item]:
            raise ValueError("vote input differs from the pilot answer")
        if record["status"] == "succeeded":
            buckets = _verified_buckets(record, reservation["provider"])
            item_spend = next(
                (i for i in settlement.get("items", []) if i["item_id"] == item), None
            )
            if (
                settlement["outcome"] != "batch"
                or item_spend is None
                or item_spend["status"] != "ok"
                or _money(item_spend["booked"]) <= 0
                or _money(settlement["actual"]) <= 0
                or buckets is None
                or buckets.is_empty
                or not isinstance(record["reported_model"], str)
                or not record["reported_model"].strip()
                or record["reported_model"].lower().startswith("scripted")
            ):
                raise ValueError("vote lacks measured paid grading usage")
            if record["target"] == "answers" and record["pass"] == 1:
                paid_answers.add((judge, item))
        records[run_id] += 1
    if any(records[r] != invocation["records_written"] for r, invocation in runs.items()):
        raise ValueError("grading invocation vote count disagrees")
    if {(j, i) for j in efforts for i in hashes} - paid_answers:
        raise ValueError("pilot answers lack live paid panel measurements")
    totals = {p: _money(v) for p, v in spend["by_provider"].items()}
    averages = {p: _money(v) for p, v in spend["per_graded_answer"].items()}
    if totals != dict(booked) or averages.keys() != totals.keys() or not totals:
        raise ValueError("grading summary provider totals disagree with the ledger")
    rates = {p: v / denominator for p, v in totals.items()}
    if any(v <= 0 or averages[p] != v.quantize(Decimal("0.000001")) for p, v in rates.items()):
        raise ValueError("grading averages disagree with observed dollars and denominator")
    return rates
