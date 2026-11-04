"""The agentic REPORT.md and README summary (PR 5 Task 10; design §§2, 9, 11.3).

The bundles are synthetic run directories: answering artifacts, vote records written
with write_vote_record, then grading/summary.json from score_run_dir. Scenario
fixtures adjust summary.json or the evidence files.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

import benchmarks.grading.inputs as grading_inputs
from benchmarks.agentic.protocol import KeyLeakError
from benchmarks.agentic_report import (
    FORBIDDEN_PHRASES,
    OUTPUT_CAP_RULE,
    REQUIRED_WORDING,
    ROUND_1_SCOPE,
)
from benchmarks.grading.aggregate import write_vote_record
from benchmarks.grading.batch import BatchItemResult
from benchmarks.grading.mapping import MAPPING_TABLE
from benchmarks.grading.panel import load_panel
from benchmarks.grading.score import score_run_dir
from benchmarks.model_matrix import ModelFamily, load_model_matrix
from benchmarks.report import _load_run_bundle, _render_report, generate_readme_summary
from benchmarks.report import generate_report as _generate_report

REPO_ROOT = Path(__file__).resolve().parents[2]
FIX = Path(__file__).parent / "fixtures"
GFIX = FIX / "grading"
METHODOLOGY = REPO_ROOT / "docs" / "benchmarks" / "PUBLIC-BENCHMARK-METHODOLOGY.md"
SCHEMA = REPO_ROOT / "docs" / "benchmarks" / "corpus.schema.json"
MATRIX = FIX / "agentic" / "model-matrix.yml"
PANEL = load_panel(GFIX / "panel.yml")
JUDGES = ("haiku", "luna", "flash-lite")
COMPOSITION = {j: {"model_id": PANEL.judges[j].model_id, "effort": "low"} for j in JUDGES}
FAMILIES = ("scripted-anthropic", "scripted-openai")
SYSTEMS = ("python-docs", "docs-fetch", "no-docs")
QUESTIONS = ("SYN-EX-001", "SYN-EX-002")
TOKENS = {"python-docs": 500, "docs-fetch": 1000, "no-docs": 50}
SECTIONS = [
    "# Agentic benchmark report",
    "## Claims",
    "## Grading disclosure",
    "## Correctness",
    "## Normalized tokens",
    "## Cost per correct answer",
    "## Tool use",
    "## Latency",
    "## Pilot rules",
    "## Budget events",
    "## Failures",
    "## Reproducibility",
    "## Air-gapped arm",
]
_ALL_MET = {
    "attempted": True,
    "p1_met": True,
    "p2_met": True,
    "contradicts_key": False,
    "wrong_version": False,
}


def _json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _vote(
    root: Path,
    judge: str,
    item: str,
    fields: dict[str, bool],
    *,
    target: str = "answers",
    effort: str = "low",
) -> None:
    text = json.dumps({"explanation": "x", **_ALL_MET, **fields})
    write_vote_record(
        root,
        target=target,
        judge=PANEL.judges[judge],
        effort=effort,
        pass_number=1,
        grading_run_id="run-1",
        attempt=1,
        result=BatchItemResult(
            item,
            "succeeded",
            text,
            False,
            "end_turn",
            None,
            {"input_tokens": 10},
            None,
            f"{judge}-2026-11-01",
            False,
        ),
        terminal=False,
        unit_id="u1",
        provider_batch_id="b1",
        input_sha256="d" * 64,
        schema=grading_inputs.judge_schema(),
    )


def _transcript(cell: str, family: ModelFamily, system: str, qid: str) -> dict[str, Any]:
    calls = [
        {
            "index": 0,
            "message_count": 1,
            "tool_calls_requested": 0,
            "stop_reason": "end_turn",
            "buckets": {
                "uncached_input": 1000,
                "cache_write_input": 0,
                "cached_input": 100,
                "output": 100,
                "reasoning": 20,
            },
            "reported_model": family.model_id,
            "attempts": [{"outcome": "ok"}],
        }
    ]
    tool_calls = []
    if system != "no-docs":
        tool_calls = [
            {
                "index": 0,
                "model_call_index": 0,
                "tool": "search_docs",
                "arguments": {"query": qid},
                "raw_arguments": None,
                "warm": qid.endswith("2"),
                "result": "{}",
                "is_error": False,
                "wall_ms": 12.0,
            }
        ]
    return {
        "competitor_id": cell,
        "corpus_id": qid,
        "tool_model_key": f"{system}:p/m",
        "system_id": system,
        "model_family_id": family.id,
        "repeat": 1,
        "provider": family.provider,
        "status": "succeeded",
        "error": None,
        "answer": f"Answer {qid} from {system}.",
        "model_calls": calls,
        "tool_calls": tool_calls,
        "timing": {"model_ms": 900.0, "tool_ms": 12.0, "overhead_ms": 30.0},
    }


def _build(
    tmp_path: Path,
    *,
    families_run: tuple[str, ...] = FAMILIES,
    systems_run: tuple[str, ...] = SYSTEMS,
    matrix: Path = MATRIX,
) -> Path:
    run = tmp_path / "run"
    snapshots = run / "snapshots"
    snapshots.mkdir(parents=True)
    shutil.copy(FIX / "corpus.sample.yml", snapshots / "corpus.yml")
    shutil.copy(
        REPO_ROOT / "docs" / "benchmarks" / "agentic-manifest.yml",
        snapshots / "competitor-manifest.yml",
    )
    shutil.copy(matrix, snapshots / "model-matrix.yml")
    shutil.copy(FIX / "agentic" / "prices.yml", snapshots / "price-snapshot.yml")
    _json(
        run / "environment.json",
        {
            "run_id": "r1",
            "created_at": "2026-11-04T00:00:00Z",
            "repo_commit": "abc",
            "python_version": "3.13",
            "platform": "x",
            "system": "Darwin",
            "machine": "arm64",
            "mode": "agentic",
            "seed": 99,
            "client_versions": {"anthropic": "1.15.0"},
        },
    )
    grading = run / "grading"
    grading_inputs.snapshot_inputs(
        grading / "inputs",
        criteria_path=GFIX / "criteria.sample.yml",
        panel_path=GFIX / "panel.yml",
        probes_path=GFIX / "probes.sample.yml",
    )
    planned, cells = [], []
    models = {f.id: f for f in load_model_matrix(matrix).model_families}
    for family in families_run:
        for system in systems_run:
            cell = f"{system}--{family}--r1"
            cells.append(cell)
            for qid in QUESTIONS:
                planned.append(
                    {
                        "cell": cell,
                        "competitor_id": cell,
                        "corpus_id": qid,
                        "system_id": system,
                        "model_family_id": family,
                        "repeat": 1,
                    }
                )
                base = {"competitor_id": cell, "corpus_id": qid, "tool_model_key": f"{system}:p/m"}
                _json(
                    run / "transcripts" / cell / f"{qid}.json",
                    _transcript(cell, models[family], system, qid),
                )
                _json(
                    run / "scoring" / cell / f"{qid}.json",
                    {**base, "status": "succeeded", "score": None, "requires_manual_scoring": True},
                )
                _json(
                    run / "tokens" / cell / f"{qid}.json",
                    {
                        **base,
                        "status": "counted",
                        "client_wrapped_tokens": TOKENS[system],
                        "raw_payload_tokens": 40,
                        "count_model": "claude-sonnet-5-5",
                    },
                )
                _json(
                    run / "latency" / cell / f"{qid}.json",
                    {
                        **base,
                        "status": "succeeded",
                        "latency_ms": 950.0,
                        "timing": {"model_ms": 900.0, "tool_ms": 12.0, "overhead_ms": 30.0},
                    },
                )
                item = grading_inputs.safe_id(cell, qid)
                suspect = (family, system, qid) == ("scripted-openai", "python-docs", "SYN-EX-002")
                _json(
                    grading / "items" / "answers" / f"{item}.json",
                    {"meta": {"competitor_id": cell, "corpus_id": qid, "suspect": suspect}},
                )
                fields = {"attempted": False} if system == "no-docs" else {}
                for judge in JUDGES:
                    _vote(grading, judge, item, fields)
                if suspect:
                    _vote(
                        grading,
                        "reference",
                        item,
                        {"p2_met": False},
                        target="reference",
                        effort="high",
                    )
    _json(run / "planned-cells.json", {"cells": planned})
    _json(
        grading / "runs" / "run-1.json",
        {
            "grading_run_id": "run-1",
            "target": "answers",
            "pass": 1,
            "composition": COMPOSITION,
            "not_run": [],
        },
    )
    grading_events = [
        {
            "event": "settle",
            "provider": "anthropic",
            "booked": "0.02",
            "unit_id": "g1",
            "input_bound_exceeded": True,
            "actual": "0.02",
        },
        {
            "event": "settle",
            "provider": "openai",
            "booked": "0.01",
            "unit_id": "g2",
            "input_bound_exceeded": False,
            "actual": None,
        },
    ]
    (grading / "ledger-events.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in grading_events)
    )
    (run / "ledger-events.jsonl").write_text(
        json.dumps(
            {
                "event": "settle",
                "provider": "anthropic",
                "unit_id": "a1",
                "booked": "0.004",
                "actual": None,
                "outcome": "server",
                "input_bound_exceeded": False,
            }
        )
        + "\n"
    )
    families = {
        f: {
            "answer_turns": 100,
            "output_cap_stops": 2 if f == families_run[0] else 0,
            "wall_s": 3600.0,
            "retries": {},
            "timeouts": 0,
            "reruns": 0,
        }
        for f in families_run
    }
    _json(
        run / "run-summary.json",
        {
            "run_id": "r1",
            "repo_commit": "abc",
            "artifact_root": str(run),
            "mode": "agentic",
            "models": list(families_run),
            "competitors": cells,
            "corpus_ids": list(QUESTIONS),
            "planned_cells": len(planned),
            "scored_cells": len(planned),
            "succeeded_cells": len(planned),
            "failed_cells": 0,
            "families": families,
            "discarded": [],
            "not_run": [],
            "worker_errors": [],
            "ledger": {
                p: {
                    "spent_before": "0",
                    "spent_after": "1",
                    "cap": "180",
                    "stopped": None,
                    "input_bound_exceeded": 3 if p == "anthropic" else 0,
                }
                for p in ("anthropic", "openai", "google")
            },
            "price_snapshot": {"captured_on": "2026-10-31", "expires_on": None},
        },
    )
    _json(
        run / "systems" / "scripted-anthropic--python-docs.json",
        {"index_sha256": "e" * 64, "tools_raw": []},
    )
    return run


def _score(
    run: Path, tmp_path: Path, *, panel_path: Path = GFIX / "panel.yml", composition=COMPOSITION
) -> Path:
    inputs = json.loads((run / "grading" / "inputs" / "inputs.json").read_text())
    entry = {
        "binding": {"composition": composition, "inputs": inputs},
        "gate": {
            "passed": True,
            "details": {
                "overall": {"accuracy": 0.97, "n": 28, "hits": 27, "wilson": [0.8, 0.99]},
                "by_type": {"KEY": {"accuracy": 1.0, "n": 2, "hits": 2, "wilson": [0.3, 1.0]}},
                "by_judge": {
                    "haiku": {
                        "accuracy": 0.96,
                        "n": 28,
                        "hits": 27,
                        "wilson": [0.8, 0.99],
                        "inj_credit": 0,
                    }
                },
                "inj_credit": 0,
                "confusion": {},
                "misses_by_generator": {"sol": 1},
                "held_out_test_retest": {
                    "haiku": {"alpha": 0.75, "items": 24, "expected": 28, "pass2_records": 26}
                },
            },
        },
        "reference_check": {"passed": True, "details": {}},
        "dropped": {"PAD": 2},
        "tuning": [{"judges": {"haiku": {"tried": ["low"], "chosen": "low"}}}],
    }
    g1 = tmp_path / "g1.json"
    _json(g1, {"entries": {"+".join(composition): entry}})
    score_run_dir(
        run,
        criteria_path=GFIX / "criteria.sample.yml",
        panel_path=panel_path,
        schema_path=SCHEMA,
        g1_paths=[g1],
    )
    return run


def _edit_summary(run: Path, **changes: Any) -> None:
    path = run / "grading" / "summary.json"
    summary = json.loads(path.read_text())
    summary.update(changes)
    path.write_text(json.dumps(summary))


@pytest.fixture
def agentic_bundle(tmp_path: Path) -> Path:
    return _score(_build(tmp_path), tmp_path)


@pytest.fixture
def live_like_bundle(tmp_path: Path) -> Path:
    return _live_like_run(tmp_path)


def _live_like_run(tmp_path: Path, *, gemini_only: bool = False) -> Path:
    """Synthetic recorded calls and votes matching the active adapter snapshots."""

    docs = REPO_ROOT / "docs" / "benchmarks"
    matrix_source = docs / "model-matrix.yml" if gemini_only else MATRIX
    active = ("gemini-3.8-flash",) if gemini_only else FAMILIES
    agentic_bundle = _build(tmp_path, families_run=active, matrix=matrix_source)
    matrix = yaml.safe_load(matrix_source.read_text())
    hosted = {
        f["provider"]: f
        for f in yaml.safe_load((docs / "model-matrix.yml").read_text())["model_families"]
        if f["arm"] == "hosted"
    }
    for family in matrix["model_families"]:
        if family["id"] in FAMILIES:
            family.update(hosted[family["provider"]], id=family["id"])
            del family["script"]
    (agentic_bundle / "snapshots/model-matrix.yml").write_text(yaml.safe_dump(matrix))
    panel_path = agentic_bundle / "grading/inputs/panel.yml"
    panel = yaml.safe_load(panel_path.read_text())
    real = yaml.safe_load((docs / "grading/panel.yml").read_text())
    for judge in (*JUDGES, "reference"):
        panel["judges"][judge].update(real["judges"][judge], effort=PANEL.judges[judge].effort)
        del panel["judges"][judge]["script"]
    panel_path.write_text(yaml.safe_dump(panel))
    _refresh_manifest(agentic_bundle)
    families = {f["id"]: f for f in matrix["model_families"]}
    events = []
    summary_path = agentic_bundle / "run-summary.json"
    summary = json.loads(summary_path.read_bytes())
    for balance in summary["ledger"].values():
        balance["spent_after"] = 0
    for path in (agentic_bundle / "transcripts").glob("*/*.json"):
        record = json.loads(path.read_bytes())
        family = families[record["model_family_id"]]
        record["execution"] = {k: family[k] for k in ("provider", "model_id", "api")}
        record.update(provider=family["provider"], model_id=family["model_id"])
        call = record["model_calls"][0]
        call.update(
            reported_model=family["model_id"],
            request_params={"model": family["model_id"]},
            raw_response={"model": family["model_id"], "id": "saved-response"},
            attempts=[{"unit_id": f"{path.parent.name}/{path.stem}", "outcome": "ok"}],
        )
        _json(path, record)
        unit = {
            "run_id": summary["run_id"],
            "provider": family["provider"],
            "unit_id": call["attempts"][0]["unit_id"],
        }
        events.extend(
            [
                {
                    **unit,
                    "event": "reserve",
                    "job_id": f"{family['id']}--{record['system_id']}",
                    "model_id": family["model_id"],
                    "api": family["api"],
                    "kind": "model_call",
                    "bound": "1",
                    "input_bound": 1024,
                    "output_bound": 128,
                },
                {
                    **unit,
                    "event": "settle",
                    "actual": "1",
                    "booked": "1",
                    "buckets": call["buckets"],
                    "outcome": "ok",
                },
            ]
        )
        summary["ledger"][family["provider"]]["spent_after"] += 1
    _json(summary_path, summary)
    (agentic_bundle / "ledger-events.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events)
    )
    for path in (agentic_bundle / "tokens").glob("*/*.json"):
        record = json.loads(path.read_bytes())
        record.update(
            approximation=False,
            execution={
                "provider": "anthropic",
                "model_id": record["count_model"],
                "api": "anthropic-messages",
            },
        )
        _json(path, record)
    grading = agentic_bundle / "grading"
    composition = {j: {"model_id": panel["judges"][j]["model_id"], "effort": "low"} for j in JUDGES}
    for path in (grading / "votes").rglob("*.json"):
        vote = json.loads(path.read_bytes())
        vote["model_id"] = panel["judges"][vote["judge_id"]]["model_id"]
        if vote["target"] == "reference":
            vote["grading_run_id"] = "ref-1"
            path.unlink()
            path = path.with_name("ref-1--a1.json")
        _json(path, vote)
    for target, run_id, members in (
        ("answers", "run-1", composition),
        (
            "reference",
            "ref-1",
            {"reference": {"model_id": panel["judges"]["reference"]["model_id"], "effort": "high"}},
        ),
    ):
        _json(
            grading / "runs" / f"{run_id}.json",
            {"target": target, "pass": 1, "grading_run_id": run_id, "composition": members},
        )
    # The unused family, fallback and generators retain their scripted adapters.
    return _score(agentic_bundle, tmp_path, panel_path=panel_path, composition=composition)


def _refresh_manifest(run: Path) -> None:
    path = run / "grading/inputs/inputs.json"
    manifest = json.loads(path.read_bytes())
    for key, name in {"panel": "panel.yml", "mapping_table": "mapping.txt"}.items():
        manifest[key] = hashlib.sha256((path.parent / name).read_bytes()).hexdigest()
    _json(path, manifest)


def generate_report(run: Path, **kwargs: Any) -> Path:
    return _generate_report(run, model_matrix_path=MATRIX, methodology_path=METHODOLOGY, **kwargs)


def _render(run: Path) -> str:
    return generate_report(run).read_text()


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    following = [text.find(h, start + len(heading)) for h in SECTIONS if h != heading]
    ends = [e for e in following if e > start]
    return text[start : min(ends) if ends else len(text)]


def test_agentic_report_sections_in_order(live_like_bundle: Path) -> None:
    text = _render(live_like_bundle)
    positions = [text.index(heading + "\n") for heading in SECTIONS]
    assert positions == sorted(positions)
    tier = json.loads((live_like_bundle / "grading" / "summary.json").read_text())["tier"]
    assert f"Grading outcome: {tier} (G1–G4; {REQUIRED_WORDING})" in text
    assert "claude-sonnet-5-5" in text
    assert "providers' hosted batch APIs" in text
    assert "Only answering ran in the air-gapped arm" not in text


def test_report_never_makes_an_anti_claim(live_like_bundle: Path) -> None:
    text = _render(live_like_bundle)
    assert not any(p in text.lower() for p in FORBIDDEN_PHRASES)
    rest = text.replace(REQUIRED_WORDING, "").replace("Judge not validated", "")
    # what is left of "validated" is only the capitalized tier name
    assert re.findall(r"\bvalidated\b", rest, re.I) == re.findall(r"\bValidated\b", rest)


def test_report_renders_the_disclosure_from_the_exported_bundle_alone(
    agentic_bundle: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    export = tmp_path / "export"
    shutil.copytree(agentic_bundle, export)
    missing = tmp_path / "nowhere"
    for name in (
        "CRITERIA_PATH",
        "JUDGE_PROMPT_PATH",
        "JUDGE_SCHEMA_PATH",
        "KEY_AUDIT_PROMPT_PATH",
        "PROBE_SPEC_PATH",
        "TUNING_PATH",
        "PROBES_PATH",
        "GRADING_DOCS",
    ):
        monkeypatch.setattr(grading_inputs, name, missing / name)
    disclosure = _section(_render(export), "## Grading disclosure")
    inputs = json.loads((export / "grading" / "inputs" / "inputs.json").read_text())
    assert inputs["judge_prompt"] in disclosure and inputs["criteria"] in disclosure
    assert MAPPING_TABLE.strip().splitlines()[1] in disclosure
    assert grading_inputs.MASK_RULES.strip().splitlines()[1] in disclosure
    assert "grading/inputs/judge-prompt.md" in disclosure
    assert "grading/disclosure/" in disclosure
    assert "Held-out probe test–retest" in disclosure
    assert "haiku" in disclosure and "0.75" in disclosure and "24/28 paired" in disclosure
    assert "26 pass-2 records" in disclosure
    assert "Real-answer test–retest" in disclosure
    assert "per evaluated family" in disclosure and "per system" in disclosure
    assert "scripted-openai/python-docs" in disclosure  # suspects per (model, system)
    assert "Reference grade differs from the panel's" in disclosure


def test_no_docs_is_never_ranked_on_tokens(agentic_bundle: Path) -> None:
    text = _render(agentic_bundle)
    assert "no-docs" not in _section(text, "## Normalized tokens")
    assert "no-docs" not in _section(_section(text, "## Claims"), "### C2")


def test_round_1_scope_sentence_is_in_the_c2_section(agentic_bundle: Path) -> None:
    c2 = _section(_render(agentic_bundle), "## Claims")
    c2 = c2[c2.index("### C2") : c2.index("### C3")]
    assert f'round 1 supports only "{ROUND_1_SCOPE}"' in c2
    assert "scripted-anthropic" in c2 and "95% CI" in c2


def test_output_cap_stop_share_over_one_percent_is_flagged(agentic_bundle: Path) -> None:
    pilot = _section(_render(agentic_bundle), "## Pilot rules")
    assert OUTPUT_CAP_RULE == 0.01
    line = next(line for line in pilot.splitlines() if "scripted-anthropic" in line)
    assert "2.0%" in line and "over the 1% rule: raise the output cap before the full run" in line
    other = next(line for line in pilot.splitlines() if "scripted-openai" in line)
    assert "0.0%" in other and "over the 1% rule" not in other


def test_input_bound_exceeded_is_surfaced_for_answering_and_grading(agentic_bundle: Path) -> None:
    pilot = _section(_render(agentic_bundle), "## Pilot rules")
    assert "| anthropic | 3 | 1 |" in pilot and "| openai | 0 | 0 |" in pilot


@pytest.fixture
def agentic_bundle_with_fallback(tmp_path: Path) -> Path:
    run = _live_like_run(tmp_path, gemini_only=True)
    grading = run / "grading"
    panel_path = grading / "inputs/panel.yml"
    panel = yaml.safe_load(panel_path.read_bytes())
    docs = REPO_ROOT / "docs/benchmarks"
    panel["judges"]["flash"] = yaml.safe_load((docs / "grading/panel.yml").read_bytes())["judges"][
        "flash"
    ]
    panel["judges"]["flash"]["effort"] = "low"
    panel_path.write_text(yaml.safe_dump(panel))
    _refresh_manifest(run)
    source = grading / "votes/answers/flash-lite"
    dest = source.with_name("flash")
    source.rename(dest)
    model = panel["judges"]["flash"]["model_id"]
    for path in dest.rglob("*.json"):
        _json(
            path,
            json.loads(path.read_bytes())
            | {"judge_id": "flash", "model_id": model, "reported_model": "flash-2026-11-01"},
        )
    metadata = grading / "runs/run-1.json"
    invocation = json.loads(metadata.read_bytes())
    members = invocation["composition"]
    del members["flash-lite"]
    members["flash"] = {"model_id": model, "effort": "low"}
    _json(metadata, invocation)
    _score(run, tmp_path, panel_path=panel_path, composition=members)
    return run


def test_grading_disclosure_names_judges_versions_and_self_grading(
    agentic_bundle_with_fallback: Path,
) -> None:
    disclosure = _section(_render(agentic_bundle_with_fallback), "## Grading disclosure")
    assert "Correctness was graded by three LLMs" in disclosure
    assert "haiku (claude-haiku-5-5, reported haiku-2026-11-01, effort low)" in disclosure
    assert "flash (gemini-3.8-flash" in disclosure
    assert "flash (gemini-3.8-flash) is the evaluated model gemini-3.8-flash" in disclosure
    assert "grades its own answers" in disclosure
    assert "The run was graded by the composition haiku+luna+flash." in disclosure
    assert "No human graded or calibrated any grade." in disclosure
    assert "a valid alternative may earn less credit if the key omits it" in disclosure


@pytest.fixture
def agentic_bundle_pending(tmp_path: Path) -> Path:
    run = _build(tmp_path)
    shutil.rmtree(run / "grading" / "votes" / "reference")
    return _score(run, tmp_path)


def test_pending_grades_make_claims_not_evaluable(agentic_bundle_pending: Path) -> None:
    claims = _section(_render(agentic_bundle_pending), "## Claims")
    c2 = claims[claims.index("### C2") : claims.index("### C3")]
    line = next(line for line in c2.splitlines() if "scripted-openai" in line)
    assert "not evaluable" in line
    correctness = _section(_render(agentic_bundle_pending), "## Correctness")
    assert "pending" in correctness


def test_missing_egress_evidence_makes_c1_not_evaluable(agentic_bundle: Path) -> None:
    c1 = _section(_render(agentic_bundle), "## Claims")
    assert "not evaluable: no egress evidence in this run" in c1[: c1.index("### C2")]


@pytest.fixture
def agentic_bundle_airgap_denied(agentic_bundle: Path) -> Path:
    jobs = {
        f"{f}--{s}": {
            "hosts_allowed": ["api.anthropic.com:443"],
            "hosts_denied": [],
            "lookup_package_docs_attempts": 0,
        }
        for f in FAMILIES
        for s in SYSTEMS
    }
    jobs["gemma4-12b--python-docs"] = {
        "hosts_allowed": ["host.docker.internal:11434"],
        "hosts_denied": ["pypi.org:443"],
        "lookup_package_docs_attempts": 1,
    }
    _json(agentic_bundle / "egress" / "summary.json", {"jobs": jobs, "isolation_probe": "pass"})
    _json(
        agentic_bundle / "airgap" / "checks.json",
        {"all_passed": True, "checks": 9, "internet_hosts": []},
    )
    interval = {"point": 0.5, "low": 0.25, "high": 0.75}
    _edit_summary(
        agentic_bundle,
        claims={
            "C2": {},
            "C3": {
                "gemma4-12b": {
                    "id": "C3",
                    "model": "gemma4-12b",
                    "passed": True,
                    "interval": interval,
                    "reason": None,
                }
            },
        },
        offline={
            "ok": True,
            "expected_checks": 9,
            "airgap_jobs": ["gemma4-12b--python-docs"],
            "denied_attempts": [{"job_id": "gemma4-12b--python-docs", "host": "pypi.org:443"}],
        },
    )
    return agentic_bundle


def test_c3_disclosure_shows_a_denied_pypi_attempt_while_c3_passes(
    agentic_bundle_airgap_denied: Path,
) -> None:
    claims = _section(_render(agentic_bundle_airgap_denied), "## Claims")
    c3 = claims[claims.index("### C3") : claims.index("### S1")]
    assert "gemma4-12b" in c3 and "pass" in c3
    rendered = re.sub(r"\\([!-/:-@\[-`{-~])", r"\1", c3)  # GFM backslash escapes
    denied = [line for line in rendered.splitlines() if line.startswith("- gemma4-12b")]
    assert denied == ["- gemma4-12b--python-docs: pypi.org:443 (refused; nothing left the sandbox)"]
    c1 = claims[: claims.index("### C2")]
    assert "| python-docs | no |" in c1 and "lookup_package_docs" in c1


def test_readme_summary_has_no_claim_below_validated(live_like_bundle: Path) -> None:
    tier = json.loads((live_like_bundle / "grading" / "summary.json").read_text())["tier"]
    assert tier != "Validated"
    text = generate_readme_summary(live_like_bundle, methodology_path=METHODOLOGY).read_text()
    assert f"No public claim: grading outcome is {tier}." in text
    assert "C2" not in text and "fewer" not in text


def _validated(run: Path) -> Path:
    # Isolate the publication rule while retaining the scorer's actual paired claims.
    _edit_summary(run, tier="Validated", judge_sensitive=["C2/scripted-openai"])
    return run


def test_readme_summary_drops_a_judge_sensitive_claim(live_like_bundle: Path) -> None:
    text = generate_readme_summary(
        _validated(live_like_bundle), methodology_path=METHODOLOGY
    ).read_text()
    assert "scripted-anthropic" in text and "scripted-openai" not in text
    assert ROUND_1_SCOPE in text and REQUIRED_WORDING in text
    assert not any(p in text.lower() for p in FORBIDDEN_PHRASES)


def _readme(run: Path) -> str:
    return generate_readme_summary(run, methodology_path=METHODOLOGY).read_text()


def test_validated_scripted_bundle_has_only_demonstration_results(agentic_bundle: Path) -> None:
    readme = _readme(_validated(agentic_bundle))
    report = _render(agentic_bundle)
    assert "fewer normalized tokens" not in readme
    for text in (readme, report):
        assert "demonstration" in text.lower()
        assert REQUIRED_WORDING not in text
    assert "Correctness was graded by three LLMs" not in report
    assert "hosted batch APIs" not in report
    assert "Only answering ran in the air-gapped arm" not in report
    assert "scripted-1" in report
    claims = _section(report, "## Claims")
    assert "95% CI" in claims
    # All three systems were scored. Their diagnostics remain visible in a demonstration.
    assert "| scripted-anthropic | 0.500, 95% CI 0.500 to 0.500 |" in claims
    assert "Gate and claim bootstrap resamples: 10000" in report


@pytest.mark.parametrize("scripted", ["answer", "luna", "reference"])
def test_one_active_scripted_adapter_blocks_public_claim(
    live_like_bundle: Path, scripted: str
) -> None:
    run = _validated(live_like_bundle)
    if scripted == "answer":
        path = run / "snapshots/model-matrix.yml"
        data = yaml.safe_load(path.read_text())
        data["model_families"][1] = yaml.safe_load(MATRIX.read_text())["model_families"][1]
    else:
        path = run / "grading/inputs/panel.yml"
        data = yaml.safe_load(path.read_text())
        data["judges"][scripted] = yaml.safe_load((GFIX / "panel.yml").read_text())["judges"][
            scripted
        ]
    path.write_text(yaml.safe_dump(data))
    _refresh_manifest(run)
    readme = _readme(run)
    assert "fewer normalized tokens" not in readme
    for text in (readme, _render(run)):
        assert "demonstration" in text.lower()
        assert (REQUIRED_WORDING in text) == (scripted == "answer")


@pytest.mark.parametrize("missing", ["snapshots/model-matrix.yml", "grading/inputs/panel.yml"])
def test_missing_snapshot_withholds_public_claim_without_calling_it_scripted(
    live_like_bundle: Path, missing: str
) -> None:
    run = _validated(live_like_bundle)
    (run / missing).unlink()
    for text in (_readme(run), _render(run)):
        assert "unknown" in text.lower()
        assert "demonstration" not in text.lower()
    assert "fewer normalized tokens" not in _readme(run)


def test_unknown_active_api_withholds_public_claim(live_like_bundle: Path) -> None:
    run = _validated(live_like_bundle)
    path = run / "grading/inputs/panel.yml"
    data = yaml.safe_load(path.read_text())
    data["judges"]["reference"]["api"] = "unrecognized"
    path.write_text(yaml.safe_dump(data))
    _refresh_manifest(run)
    text = _readme(run)
    assert "unknown" in text.lower() and "demonstration" not in text.lower()
    assert "fewer normalized tokens" not in text and REQUIRED_WORDING not in text


def test_an_ungraded_agentic_run_still_renders(tmp_path: Path) -> None:
    run = _build(tmp_path)
    text = _render(run)
    assert REQUIRED_WORDING not in text and REQUIRED_WORDING not in _readme(run)
    assert "Grading outcome: not graded" in text
    assert "not evaluable: the run has not been graded" in text


def test_fixed_flow_reports_are_unchanged(agentic_bundle: Path, tmp_path: Path) -> None:
    fixed = tmp_path / "fixed"
    shutil.copytree(agentic_bundle, fixed)
    for name in ("environment.json", "run-summary.json"):
        data = json.loads((fixed / name).read_text())
        data.pop("mode")
        (fixed / name).write_text(json.dumps(data))
    report = generate_report(fixed).read_text()
    bundle = _load_run_bundle(fixed)
    expected = _render_report(bundle, yaml_matrix(), METHODOLOGY, fixed / "REPORT.md")
    assert report == expected and "## Grading disclosure" not in report


def yaml_matrix() -> Any:
    from benchmarks.model_matrix import load_model_matrix

    assert yaml.safe_load(MATRIX.read_text())
    return load_model_matrix(MATRIX)


# --- Security: run data never becomes Markdown (the class #173 closed for table cells) ---

PAYLOAD = (
    "<img src=x onerror=alert(1)> [text](https://evil.example) "
    "![i](https://evil.example/i.png) `code`\n# heading"
)
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_LINK = re.compile(r"(?<!\\)\[([^\]\n]*)\]\(([^)\s]*)")


def _split_fences(text: str) -> tuple[str, list[str]]:
    """CommonMark fences: the text outside every fenced block, and each block's content."""
    outside: list[str] = []
    blocks: list[str] = []
    fence: str | None = None
    content: list[str] = []
    for line in text.splitlines():
        match = _FENCE.match(line)
        if fence is None:
            if match:
                fence, content = match.group(1), []
            else:
                outside.append(line)
            continue
        closes = (
            match is not None
            and match.group(1)[0] == fence[0]
            and len(match.group(1)) >= len(fence)
            and not line.strip().strip(fence[0])
        )
        if closes:
            blocks.append("\n".join(content))
            fence = None
        else:
            content.append(line)
    assert fence is None, "a fence was left open"
    return "\n".join(outside), blocks


def _assert_inert(text: str) -> None:
    outside, _ = _split_fences(text)
    assert re.search(r"(?<!\\)<[A-Za-z!/?]", outside) is None, "raw HTML"
    assert re.search(r"(?<!\\)!\[", outside) is None, "an image"
    for label, target in _LINK.findall(outside):
        assert "evil" not in target, f"a link from run data: [{label}]({target})"
    assert re.search(r"(?<!\\)`code", outside) is None, "a code span from run data"
    assert not any(line.lstrip().startswith("# heading") for line in outside.splitlines())
    assert "evil.example" in outside.replace("\\", "")  # the payload is shown, as text


@pytest.fixture
def agentic_bundle_hostile(live_like_bundle: Path) -> Path:
    run = live_like_bundle
    cell = "python-docs--scripted-anthropic--r1"
    path = run / "transcripts" / cell / "SYN-EX-001.json"
    transcript = json.loads(path.read_text())
    transcript |= {"status": "failed", "error": {"category": PAYLOAD, "message": PAYLOAD}}
    transcript["model_calls"][0]["reported_model"] = PAYLOAD
    path.write_text(json.dumps(transcript))
    token = run / "tokens" / cell / "SYN-EX-002.json"
    token.write_text(
        json.dumps({**json.loads(token.read_text()), "status": "failed", "error": PAYLOAD})
    )
    summary_path = run / "run-summary.json"
    summary = json.loads(summary_path.read_text())
    summary |= {
        "discarded": [{"cell": PAYLOAD, "corpus_id": PAYLOAD, "reason": PAYLOAD, "attempt": 1}],
        "not_run": [{"cell": PAYLOAD, "corpus_id": "q", "reason": PAYLOAD}],
        "worker_errors": [{"job_id": PAYLOAD, "reason": PAYLOAD}],
    }
    summary["ledger"]["anthropic"]["stopped"] = "overrun"
    summary_path.write_text(json.dumps(summary))
    panel = yaml.safe_load((run / "grading" / "inputs" / "panel.yml").read_text())
    panel["judges"]["haiku"]["model_id"] = PAYLOAD
    (run / "grading" / "inputs" / "panel.yml").write_text(yaml.safe_dump(panel))
    vote = next((run / "grading" / "votes" / "answers" / "luna").rglob("*.json"))
    vote.write_text(json.dumps({**json.loads(vote.read_text()), "reported_model": PAYLOAD}))
    (run / "grading" / "inputs" / "mapping.txt").write_text(
        "````\n# heading\n<img src=x onerror=alert(1)>\n``````` and more\n"
    )
    _refresh_manifest(run)
    graded = json.loads((run / "grading" / "summary.json").read_text())
    interval = {"point": 0.5, "low": 0.25, "high": 0.75}
    _edit_summary(
        run,
        gates={**graded["gates"], "G3": {"passed": None, "details": {"reason": PAYLOAD}}},
        claims={
            "C2": graded["claims"]["C2"],
            "C3": {
                PAYLOAD: {
                    "id": "C3",
                    "model": PAYLOAD,
                    "passed": True,
                    "interval": interval,
                    "reason": None,
                }
            },
        },
        offline={
            "ok": True,
            "expected_checks": 9,
            "airgap_jobs": [PAYLOAD],
            "denied_attempts": [{"job_id": PAYLOAD, "host": PAYLOAD}],
        },
        composition=PAYLOAD,
        judge_sensitive=[PAYLOAD],
        key_audit={"audited": 1, "flagged": [PAYLOAD]},
    )
    return run


def test_no_run_data_value_becomes_markdown_in_the_report(agentic_bundle_hostile: Path) -> None:
    text = _render(agentic_bundle_hostile)
    _assert_inert(text)
    for heading in (
        "## Failures",
        "## Budget events",
        "## Grading disclosure",
        "## Reproducibility",
    ):
        assert "evil.example" in _section(text, heading).replace("\\", "")
    claims = _section(text, "## Claims")
    assert "evil.example" in claims[claims.index("### C3") :].replace("\\", "")


def test_fenced_input_text_cannot_close_its_fence(agentic_bundle_hostile: Path) -> None:
    disclosure = _section(_render(agentic_bundle_hostile), "## Grading disclosure")
    _, blocks = _split_fences(disclosure)
    mapping = next(b for b in blocks if "# heading" in b)
    assert "<img src=x onerror=alert(1)>" in mapping and "``````` and more" in mapping


def test_no_run_data_value_becomes_markdown_in_the_readme_summary(live_like_bundle: Path) -> None:
    claim = {
        "id": "C2",
        "model": PAYLOAD,
        "passed": True,
        "interval": {"point": 0.5, "low": 0.4, "high": 0.6},
        "reason": None,
    }
    _edit_summary(
        live_like_bundle,
        tier="Validated",
        claims={"C2": {PAYLOAD: claim}, "C3": {}},
        judge_sensitive=[],
    )
    text = generate_readme_summary(live_like_bundle, methodology_path=METHODOLOGY).read_text()
    _assert_inert(text)


@pytest.mark.parametrize("filename", ["REPORT.md", "README-SUMMARY.md"])
@pytest.mark.parametrize("field", ["reported_model", "legacy_extra"])
def test_report_rejects_sensitive_fields_in_old_votes_before_publication(
    agentic_bundle: Path, field: str, filename: str
) -> None:
    marker = "AIza" + "g" * 35
    path = next((agentic_bundle / "grading/votes/answers/haiku").rglob("*.json"))
    record = json.loads(path.read_bytes())
    record[field] = marker
    path.write_text(json.dumps(record).replace(marker, r"\u0041" + marker[1:]))
    out = agentic_bundle / filename
    out.write_text("previous report")
    with pytest.raises(KeyLeakError) as caught:
        (_render if filename == "REPORT.md" else _readme)(agentic_bundle)
    assert marker not in str(caught.value)
    assert out.read_text() == "previous report"


@pytest.mark.parametrize("filename", ["REPORT.md", "README-SUMMARY.md"])
def test_readme_refuses_sensitive_rendered_text_before_publication(
    agentic_bundle: Path, tmp_path: Path, filename: str
) -> None:
    marker = "AIza" + "g" * 35
    methodology = tmp_path / (marker + ".md")
    methodology.write_text("fixture methodology")
    out = agentic_bundle / filename
    out.write_text("previous output")
    with pytest.raises(KeyLeakError) as caught:
        if filename == "REPORT.md":
            _generate_report(agentic_bundle, model_matrix_path=MATRIX, methodology_path=methodology)
        else:
            generate_readme_summary(agentic_bundle, methodology_path=methodology)
    assert marker not in str(caught.value)
    assert out.read_text() == "previous output"


@pytest.mark.parametrize(
    "relative",
    [
        "graded",
        "transcript",
        "ledger-events.jsonl",
        "grading/inputs/judge-schema.json",
        "grading/inputs/grading-criteria.yml",
        "snapshots/price-snapshot.yml",
        "egress/summary.json",
        "airgap/checks.json",
        "planned-cells.json",
    ],
)
def test_report_scans_captured_evidence_before_parsing_or_digest_fallback(
    tmp_path: Path, relative: str
) -> None:
    run = _build(tmp_path)
    if relative in {"graded", "transcript"}:
        folder = "scoring" if relative == "graded" else "transcripts"
        path = next((run / folder).glob("*/*.json"))
    else:
        path = run / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    marker = "AIza" + "g" * 35
    escaped = r"\u0041" + marker[1:]
    if path.suffix == ".yml":
        path.write_text(f'unused: "{escaped}"\nunused: ordinary\n')
    else:
        # Malformed evidence (including a broken ledger row) still takes the key-leak path.
        path.write_text('broken\n{"old":"' + escaped + '","old":"ordinary"}')
    with pytest.raises(KeyLeakError) as caught:
        _render(run)
    assert marker not in str(caught.value)
    assert not (run / "REPORT.md").exists()


def _c1_row(run: Path, system: str) -> list[str]:
    c1 = _render(run).split("### C1:", 1)[1].split("### C2:", 1)[0]
    row = next(line for line in c1.splitlines() if line.startswith(f"| {system} |"))
    return [
        re.sub(r"\\([!-/:-@\[-`{-~])", r"\1", cell.strip()) for cell in row.strip("|").split("|")
    ]


def test_hosted_c1_reports_only_observed_jobs_and_separates_denials(tmp_path: Path) -> None:
    from benchmarks.agentic.egress import write_egress_summary
    from tests.benchmarks.test_agentic_egress import (
        ANTHROPIC_LINE,
        PROBE_LINES,
        PYPI_LINE,
        _evidence,
        _lines,
        _ok_settle,
    )
    from tests.benchmarks.test_agentic_sandbox import _job

    family = "claude-sonnet-5-5"
    run = _build(
        tmp_path,
        families_run=(family,),
        systems_run=("python-docs",),
        matrix=REPO_ROOT / "docs/benchmarks/model-matrix.yml",
    )
    job = _job(family, "python-docs")
    _evidence(run, job.job_id, log=_lines(*PROBE_LINES, ANTHROPIC_LINE, PYPI_LINE))
    record = next((run / "transcripts").glob("*/*.json"))
    transcript = json.loads(record.read_text())
    transcript["tool_calls"][0]["tool"] = "lookup_package_docs"
    _json(record, transcript)
    summary = write_egress_summary(run, [job], _ok_settle(job.job_id))
    assert summary["isolation_probe"] == "pass"
    row = _c1_row(run, "python-docs")
    assert row[2:4] == ["api.anthropic.com:443", "pypi.org:443"]
    assert row[-1] == "1"
    missing = _c1_row(run, "docs-fetch")
    assert missing[2] == missing[-1] == "unknown"

    # A successful hosted model call must have reached its provider through the proxy.
    summary["jobs"][job.job_id]["hosts_allowed"] = []
    _json(run / "egress/summary.json", summary)
    row = _c1_row(run, "python-docs")
    assert row[2] == "unknown" and "pypi.org:443" in row[3]


@pytest.mark.parametrize(
    "bad",
    [
        "empty",
        "missing-job",
        "wrong-job",
        "no-counter",
        "boolean",
        "negative",
        "string",
        "failed",
        "no-probe",
    ],
)
def test_inadequate_c1_never_reports_zero_or_no_hosts(agentic_bundle: Path, bad: str) -> None:
    jobs = {
        f"{f}--{s}": {"hosts_allowed": [], "hosts_denied": [], "lookup_package_docs_attempts": 0}
        for f in FAMILIES
        for s in SYSTEMS
    }
    evidence: dict[str, Any] = {"jobs": jobs, "isolation_probe": "pass"}
    target = f"{FAMILIES[0]}--python-docs"
    if bad == "empty":
        jobs.clear()
    elif bad in {"missing-job", "wrong-job"}:
        entry = jobs.pop(target)
        if bad == "wrong-job":
            jobs["unplanned--python-docs"] = entry
    elif bad == "failed":
        evidence["isolation_probe"] = "fail"
    elif bad == "no-probe":
        del evidence["isolation_probe"]
    elif bad == "no-counter":
        del jobs[target]["lookup_package_docs_attempts"]
    else:
        jobs[target]["lookup_package_docs_attempts"] = {
            "boolean": True,
            "negative": -1,
            "string": "0",
        }[bad]
    _json(agentic_bundle / "egress/summary.json", evidence)
    row = _c1_row(agentic_bundle, "python-docs")
    assert row[2] == row[-1] == "unknown"
    if bad not in {"empty", "failed", "no-probe"}:
        other = _c1_row(agentic_bundle, "docs-fetch")
        assert other[2] == "none" and other[-1] == "0"


def test_gemma_only_run_discloses_google_and_unestimable_spi(tmp_path: Path) -> None:
    run = _build(
        tmp_path,
        families_run=("gemma4-12b",),
        systems_run=("python-docs", "no-docs"),
        matrix=REPO_ROOT / "docs/benchmarks/model-matrix.yml",
    )
    jobs = {
        f"gemma4-12b--{system}": {
            "hosts_allowed": ["host.docker.internal:11434"],
            "hosts_denied": ["pypi.org:443"] if system == "python-docs" else [],
            "lookup_package_docs_attempts": int(system == "python-docs"),
        }
        for system in ("python-docs", "no-docs")
    }
    _json(run / "egress/summary.json", {"jobs": jobs, "isolation_probe": "pass"})
    _json(run / "airgap/checks.json", {"all_passed": True, "internet_hosts": [], "checks": 6})
    _score(run, tmp_path)
    summary = json.loads((run / "grading/summary.json").read_text())
    assert summary["diagnostics"]["self_preference"] == {f"{j}/google": None for j in JUDGES}
    report = _render(run)
    assert "shares a provider family (google) with the evaluated model gemma4-12b" in report
    assert "Self-preference index, flash-lite/google: not estimable" in report
    assert "with the evaluated model gemini-3.8-flash" not in report
    assert summary["offline"]["ok"] is True
    assert summary["offline"]["denied_attempts"] == [
        {"job_id": "gemma4-12b--python-docs", "host": "pypi.org:443"}
    ]
    row = _c1_row(run, "python-docs")
    assert row[2:4] == ["unknown", "unknown"]  # no hosted job ran
    assert row[-2:] == ["yes (air-gapped arm)", "1"]
    checks = run / "airgap/checks.json"
    checks.unlink()
    assert _c1_row(run, "python-docs")[-2] == "not shown"
    _json(checks, {"all_passed": True, "internet_hosts": [], "checks": 6})

    _json(run / "egress/summary.json", {"jobs": jobs, "isolation_probe": "fail"})
    _score(run, tmp_path)
    offline = json.loads((run / "grading/summary.json").read_text())["offline"]
    assert offline["ok"] is False
    assert offline["denied_attempts"] == summary["offline"]["denied_attempts"]


def test_partial_c1_keeps_observed_values_without_claiming_totals(agentic_bundle: Path) -> None:
    observed = {
        "hosts_allowed": ["api.openai.com:443"],
        "hosts_denied": ["pypi.org:443"],
        "lookup_package_docs_attempts": 2,
    }
    _json(
        agentic_bundle / "egress/summary.json",
        {"jobs": {"scripted-openai--python-docs": observed}, "isolation_probe": "pass"},
    )
    row = _c1_row(agentic_bundle, "python-docs")
    assert row[2:4] == [
        "unknown (observed: api.openai.com:443)",
        "unknown (observed: pypi.org:443)",
    ]
    assert row[-1] == "unknown (observed: 2)"


@pytest.mark.parametrize("keep_execution", [False, True])
def test_saved_relabelled_pilot_cannot_publish(
    live_like_bundle: Path, keep_execution: bool
) -> None:
    run = _validated(live_like_bundle)
    saved = FIX / "agentic/r13-scripted-live"
    transcript = next((saved / "transcripts").glob("*/*.json"))
    original = json.loads(transcript.read_bytes())
    raw = original["model_calls"][0]["raw_response"]
    assert "scripted_step" in raw
    target = run / transcript.relative_to(saved)
    if keep_execution:
        # Even when the inventory fields, request, usage and ledger agree, the
        # actual scripted response contradicts the purported live adapter.
        record = json.loads(target.read_bytes())
        record["model_calls"][0]["raw_response"] = raw
        _json(target, record)
    else:
        shutil.copyfile(transcript, target)
    for text in (_readme(run), _render(run)):
        assert "scripted answering" in text
    assert "fewer normalized tokens" not in _readme(run)


@pytest.mark.parametrize(
    "relative",
    [
        "grading/inputs/mapping.txt",
        "grading/inputs",
        "egress/summary.json",
        "airgap/checks.json",
        "discarded/c/q--attempt1.json",
        "scoring/python-docs--scripted-anthropic--r1/SYN-EX-001.json",
    ],
)
def test_export_cannot_follow_evidence_links(
    agentic_bundle: Path, tmp_path: Path, relative: str
) -> None:
    from benchmarks.runner import BenchmarkValidationError

    export = shutil.copytree(agentic_bundle, tmp_path / "export")
    dest = export / relative
    outside = tmp_path / "outside"
    if dest.is_dir():
        dest.rename(outside)
        (outside / "mapping.txt").write_text("ordinary outside marker")
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest.rename(outside)
        else:
            outside.write_text("{}")
        if relative.endswith("mapping.txt"):
            outside.write_text("ordinary outside marker")
    dest.symlink_to(outside)
    path = export / "run-summary.json"
    _json(
        path,
        json.loads(path.read_bytes())
        | {"discarded": [{"cell": "c", "corpus_id": "q", "attempt": 1}]},
    )
    with pytest.raises(BenchmarkValidationError):
        _render(export)
    assert not (export / "REPORT.md").exists()


def test_report_output_links_do_not_overwrite_targets(agentic_bundle: Path, tmp_path: Path) -> None:
    outside = tmp_path / "ordinary.txt"
    outside.write_text("ordinary outside marker")
    for filename, render in (("REPORT.md", _render), ("README-SUMMARY.md", _readme)):
        (agentic_bundle / filename).symlink_to(outside)
        render(agentic_bundle)
        assert outside.read_text() == "ordinary outside marker"


@pytest.mark.parametrize(
    "change",
    [
        "missing_execution",
        "wrong_model",
        "scripted_execution",
        "missing_calls",
        "scripted_vote",
        "missing_vote",
        "wrong_composition",
        "scripted_reservation",
        "count",
        "scripted_count",
        "legacy_count",
        "copied_claim",
        "offline",
        "digest",
    ],
)
def test_saved_claim_needs_matching_evidence(live_like_bundle: Path, change: str) -> None:
    run = _validated(live_like_bundle)
    if change in {"missing_execution", "wrong_model", "scripted_execution", "missing_calls"}:
        path = next((run / "transcripts").glob("*/*.json"))
        record = json.loads(path.read_bytes())
        if change == "missing_execution":
            record.pop("execution", None)
        elif change == "missing_calls":
            record["model_calls"] = []
        else:
            record["execution"] = (record.get("execution") or {}) | (
                {"model_id": "other"} if change == "wrong_model" else {"api": "scripted"}
            )
        _json(path, record)
    elif change in {"scripted_vote", "missing_vote"}:
        path = next((run / "grading/votes/answers/luna").rglob("*.json"))
        if change == "scripted_vote":
            _json(path, json.loads(path.read_bytes()) | {"provider_batch_id": "scripted-batch-1"})
        else:
            path.unlink()
    elif change == "wrong_composition":
        path = run / "grading/runs/run-1.json"
        invocation = json.loads(path.read_bytes())
        invocation["composition"]["luna"]["model_id"] = "another-model"
        _json(path, invocation)
    elif change == "scripted_reservation":
        path = run / "grading/ledger-events.jsonl"
        with path.open("a") as ledger:
            ledger.write(
                json.dumps({"event": "reserve", "unit_id": "u1", "api": "scripted"}) + "\n"
            )
    elif change == "count":
        next((run / "tokens/python-docs--scripted-anthropic--r1").glob("*.json")).unlink()
    elif change in {"scripted_count", "legacy_count"}:
        path = next((run / "tokens/python-docs--scripted-anthropic--r1").glob("*.json"))
        token = json.loads(path.read_bytes())
        if change == "legacy_count":
            token.pop("execution")
        else:
            token["execution"]["api"] = "scripted"
        _json(path, token)
    elif change == "copied_claim":
        # Genuine live counter metadata cannot turn an old interval into current evidence.
        for path in (run / "tokens/python-docs--scripted-anthropic--r1").glob("*.json"):
            _json(path, json.loads(path.read_bytes()) | {"client_wrapped_tokens": 2000})
    elif change == "offline":
        _edit_summary(run, claims={"C3": {FAMILIES[0]: {"passed": True, "interval": {}}}})
    else:
        (run / "grading/inputs/mapping.txt").write_text("unbound ordinary text")
        assert "unbound ordinary text" not in _render(run)
    readme = _readme(run)
    assert "fewer normalized tokens" not in readme and "answers the corpus better" not in readme
    if change in {"count", "scripted_count", "legacy_count", "copied_claim", "offline"}:
        assert r"not evaluable\: missing or inconsistent execution evidence" in _render(run)
    if change in {"missing_execution", "missing_calls", "wrong_model"}:
        assert "unknown for answering" in readme and "scripted answering" not in readme


def test_real_worker_hosted_only_ungraded_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.benchmarks.test_agentic_reference_evidence import _hosted_mock_pilot

    run = _hosted_mock_pilot(tmp_path / "hosted", monkeypatch)
    for text in (_readme(run), _render(run)):
        assert (
            "not graded" in text
            and "unknown for answering" not in text
            and "scripted answering" not in text
        )


def test_export_cli_rechecks_offline_evidence_for_c3(
    live_like_bundle: Path, tmp_path: Path
) -> None:
    from benchmarks.__main__ import main

    run = _validated(live_like_bundle).rename(tmp_path / "export")
    path = run / "snapshots/model-matrix.yml"
    matrix = yaml.safe_load(path.read_bytes())
    real = yaml.safe_load((REPO_ROOT / "docs/benchmarks/model-matrix.yml").read_bytes())
    local = next(f for f in real["model_families"] if f["arm"] == "air-gapped")
    model = FAMILIES[0]
    family = next(f for f in matrix["model_families"] if f["id"] == model)
    family.update(local, id=model)
    path.write_text(yaml.safe_dump(matrix))
    identity = {k: family[k] for k in ("provider", "model_id", "api")}
    jobs = {}
    attempts = 0
    for path in (run / "transcripts").glob("*/*.json"):
        record = json.loads(path.read_bytes())
        if record["model_family_id"] == model:
            record.update(execution=identity, model_id=identity["model_id"], provider="ollama")
            call = record["model_calls"][0]
            call["reported_model"] = identity["model_id"]
            call["request_params"]["model"] = identity["model_id"]
            call["raw_response"] = {
                "model": identity["model_id"],
                "choices": [{"message": {"content": record["answer"]}}],
            }
            _json(path, record)
            attempts += 1
            jobs[f"{model}--{record['system_id']}"] = {
                "hosts_allowed": ["host.docker.internal:11434"],
                "hosts_denied": [],
                "lookup_package_docs_attempts": 0,
            }
    path = run / "ledger-events.jsonl"
    path.write_text(
        "".join(
            line + "\n"
            for line in path.read_text().splitlines()
            if json.loads(line)["provider"] != "anthropic"
        )
    )
    path = run / "run-summary.json"
    summary = json.loads(path.read_bytes())
    summary["ledger"]["anthropic"]["spent_after"] = 0
    _json(path, summary)
    _json(run / "egress/summary.json", {"jobs": jobs, "isolation_probe": "pass"})
    checks = run / "airgap/checks.json"
    _json(checks, {"checks": attempts + 2, "all_passed": True, "internet_hosts": []})
    composition = json.loads((run / "grading/runs/run-1.json").read_bytes())["composition"]
    _score(run, tmp_path, panel_path=run / "grading/inputs/panel.yml", composition=composition)
    _validated(run)
    args = [
        "report",
        "--run-dir",
        str(run),
        "--methodology",
        str(METHODOLOGY),
        "--model-matrix",
        str(MATRIX),
    ]
    assert main(args) == 0
    assert "answers the corpus better" in (run / "README-SUMMARY.md").read_text()
    checks.unlink()
    assert main(args) == 0
    assert "answers the corpus better" not in (run / "README-SUMMARY.md").read_text()
    assert "missing or inconsistent execution evidence" in (run / "REPORT.md").read_text()


@pytest.mark.parametrize("artifact", ["fifo", "oversized"])
def test_report_rejects_unreadable_evidence_without_open_ended_reads(
    agentic_bundle: Path, artifact: str
) -> None:
    from benchmarks.agentic.protocol import MAX_FRAME_BYTES
    from benchmarks.runner import BenchmarkValidationError

    path = agentic_bundle / "grading/inputs/mapping.txt"
    path.unlink()
    if artifact == "fifo":
        os.mkfifo(path)
    else:
        with path.open("wb") as file:
            file.truncate(MAX_FRAME_BYTES + 1)
    with pytest.raises(BenchmarkValidationError):
        _render(agentic_bundle)


def test_hidden_export_files_are_not_evidence(live_like_bundle: Path) -> None:
    run = _validated(live_like_bundle)
    path = run / "transcripts/.notes/old.json"
    _json(path, {"error": "ordinary hidden marker"})
    assert "fewer normalized tokens" in _readme(run)
    assert "ordinary hidden marker" not in _render(run)
