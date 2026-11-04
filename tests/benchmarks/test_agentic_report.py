"""The agentic REPORT.md and README summary (PR 5 Task 10; design §§2, 9, 11.3).

The bundles are synthetic run directories: answering artifacts, vote records written
with write_vote_record, then grading/summary.json from score_run_dir. Scenario
fixtures adjust summary.json or the evidence files.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

import benchmarks.grading.inputs as grading_inputs
from benchmarks.agentic_report import (
    FORBIDDEN_PHRASES,
    OUTPUT_CAP_RULE,
    REQUIRED_WORDING,
    ROUND_1_SCOPE,
)
from benchmarks.grading.aggregate import write_vote_record
from benchmarks.grading.batch import BatchItemResult
from benchmarks.grading.inputs import MASK_RULES, judge_schema, safe_id, snapshot_inputs
from benchmarks.grading.mapping import MAPPING_TABLE
from benchmarks.grading.panel import load_panel
from benchmarks.grading.score import score_run_dir
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
        schema=judge_schema(),
    )


def _transcript(cell: str, family: str, system: str, qid: str) -> dict[str, Any]:
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
            "reported_model": f"{family}-v1",
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
        "model_family_id": family,
        "repeat": 1,
        "provider": "anthropic",
        "status": "succeeded",
        "error": None,
        "answer": f"Answer {qid} from {system}.",
        "model_calls": calls,
        "tool_calls": tool_calls,
        "timing": {"model_ms": 900.0, "tool_ms": 12.0, "overhead_ms": 30.0},
    }


def _build(tmp_path: Path) -> Path:
    run = tmp_path / "run"
    snapshots = run / "snapshots"
    snapshots.mkdir(parents=True)
    shutil.copy(FIX / "corpus.sample.yml", snapshots / "corpus.yml")
    shutil.copy(
        REPO_ROOT / "docs" / "benchmarks" / "agentic-manifest.yml",
        snapshots / "competitor-manifest.yml",
    )
    shutil.copy(MATRIX, snapshots / "model-matrix.yml")
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
    snapshot_inputs(
        grading / "inputs",
        criteria_path=GFIX / "criteria.sample.yml",
        panel_path=GFIX / "panel.yml",
        probes_path=GFIX / "probes.sample.yml",
    )
    planned, cells = [], []
    for family in FAMILIES:
        for system in SYSTEMS:
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
                    _transcript(cell, family, system, qid),
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
                item = safe_id(cell, qid)
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
            "output_cap_stops": 2 if f == FAMILIES[0] else 0,
            "wall_s": 3600.0,
            "retries": {},
            "timeouts": 0,
            "reruns": 0,
        }
        for f in FAMILIES
    }
    _json(
        run / "run-summary.json",
        {
            "run_id": "r1",
            "repo_commit": "abc",
            "artifact_root": str(run),
            "mode": "agentic",
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


def _score(run: Path, tmp_path: Path) -> Path:
    inputs = json.loads((run / "grading" / "inputs" / "inputs.json").read_text())
    entry = {
        "binding": {"composition": COMPOSITION, "inputs": inputs},
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
            },
        },
        "reference_check": {"passed": True, "details": {}},
        "dropped": {"PAD": 2},
        "tuning": [{"judges": {"haiku": {"tried": ["low"], "chosen": "low"}}}],
    }
    g1 = tmp_path / "g1.json"
    _json(g1, {"entries": {"haiku+luna+flash-lite": entry}})
    score_run_dir(
        run,
        criteria_path=GFIX / "criteria.sample.yml",
        panel_path=GFIX / "panel.yml",
        schema_path=SCHEMA,
        g1_paths=[g1],
        resamples=200,
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


def generate_report(run: Path, **kwargs: Any) -> Path:
    return _generate_report(run, model_matrix_path=MATRIX, methodology_path=METHODOLOGY, **kwargs)


def _render(run: Path) -> str:
    return generate_report(run).read_text()


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    following = [text.find(h, start + len(heading)) for h in SECTIONS if h != heading]
    ends = [e for e in following if e > start]
    return text[start : min(ends) if ends else len(text)]


def test_agentic_report_sections_in_order(agentic_bundle: Path) -> None:
    text = _render(agentic_bundle)
    positions = [text.index(heading + "\n") for heading in SECTIONS]
    assert positions == sorted(positions)
    tier = json.loads((agentic_bundle / "grading" / "summary.json").read_text())["tier"]
    assert f"Grading outcome: {tier} (G1–G4; {REQUIRED_WORDING})" in text
    assert "scripted-1" in text


def test_report_never_makes_an_anti_claim(agentic_bundle: Path) -> None:
    text = _render(agentic_bundle)
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
    assert MASK_RULES.strip().splitlines()[1] in disclosure
    assert "grading/inputs/judge-prompt.md" in disclosure
    assert "grading/disclosure/" in disclosure
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
def agentic_bundle_with_fallback(agentic_bundle: Path) -> Path:
    inputs = agentic_bundle / "grading" / "inputs"
    shutil.copy(REPO_ROOT / "docs" / "benchmarks" / "grading" / "panel.yml", inputs / "panel.yml")
    shutil.copy(
        REPO_ROOT / "docs" / "benchmarks" / "model-matrix.yml",
        agentic_bundle / "snapshots" / "model-matrix.yml",
    )
    _edit_summary(
        agentic_bundle,
        composition="haiku+luna+flash",
        efforts={"haiku": "low", "luna": "none", "flash": "low"},
    )
    return agentic_bundle


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
    assert "pypi.org:443" in c3 and "refused" in c3 and "nothing left the sandbox" in c3
    c1 = claims[: claims.index("### C2")]
    assert "| python-docs | no |" in c1 and "lookup_package_docs" in c1


def test_readme_summary_has_no_claim_below_validated(agentic_bundle: Path) -> None:
    tier = json.loads((agentic_bundle / "grading" / "summary.json").read_text())["tier"]
    assert tier != "Validated"
    text = generate_readme_summary(agentic_bundle, methodology_path=METHODOLOGY).read_text()
    assert f"No public claim: grading outcome is {tier}." in text
    assert "C2" not in text and "fewer" not in text


@pytest.fixture
def agentic_bundle_validated(agentic_bundle: Path) -> Path:
    def claim(model: str) -> dict[str, Any]:
        return {
            "id": "C2",
            "model": model,
            "passed": True,
            "interval": {"point": 0.5, "low": 0.4, "high": 0.6},
            "reason": None,
        }

    _edit_summary(
        agentic_bundle,
        tier="Validated",
        claims={"C2": {m: claim(m) for m in FAMILIES}, "C3": {}},
        judge_sensitive=["C2/scripted-openai"],
    )
    return agentic_bundle


def test_readme_summary_drops_a_judge_sensitive_claim(agentic_bundle_validated: Path) -> None:
    text = generate_readme_summary(
        agentic_bundle_validated, methodology_path=METHODOLOGY
    ).read_text()
    assert "scripted-anthropic" in text and "scripted-openai" not in text
    assert ROUND_1_SCOPE in text and REQUIRED_WORDING in text
    assert not any(p in text.lower() for p in FORBIDDEN_PHRASES)


def test_an_ungraded_agentic_run_still_renders(tmp_path: Path) -> None:
    run = _build(tmp_path)
    text = _render(run)
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
