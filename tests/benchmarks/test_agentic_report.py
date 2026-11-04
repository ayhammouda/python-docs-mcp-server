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
    grading_inputs.snapshot_inputs(
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
            "models": list(FAMILIES),
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
def live_like_bundle(agentic_bundle: Path) -> Path:
    """Report-only fixture with live adapter snapshots for the active aliases."""
    docs = REPO_ROOT / "docs" / "benchmarks"
    matrix = yaml.safe_load(MATRIX.read_text())
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
    # The unused family, fallback and generators retain their scripted adapters.
    return agentic_bundle


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
    def claim(model: str) -> dict[str, Any]:
        return {
            "id": "C2",
            "model": model,
            "passed": True,
            "interval": {"point": 0.5, "low": 0.4, "high": 0.6},
            "reason": None,
        }

    _edit_summary(
        run,
        tier="Validated",
        claims={"C2": {m: claim(m) for m in FAMILIES}, "C3": {}},
        judge_sensitive=["C2/scripted-openai"],
    )
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
    assert "95% CI" in _section(report, "## Claims")


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


@pytest.mark.parametrize("field", ["reported_model", "legacy_extra"])
def test_report_rejects_sensitive_fields_in_old_votes_before_publication(
    agentic_bundle: Path, field: str
) -> None:
    marker = "AIza" + "g" * 35
    path = next((agentic_bundle / "grading/votes/answers/haiku").rglob("*.json"))
    record = json.loads(path.read_bytes())
    record[field] = marker
    path.write_text(json.dumps(record).replace(marker, r"\u0041" + marker[1:]))
    out = agentic_bundle / "REPORT.md"
    out.write_text("previous report")
    with pytest.raises(KeyLeakError) as caught:
        _render(agentic_bundle)
    assert marker not in str(caught.value)
    assert out.read_text() == "previous report"


def test_readme_refuses_sensitive_rendered_text_before_publication(
    agentic_bundle: Path, tmp_path: Path
) -> None:
    marker = "AIza" + "g" * 35
    methodology = tmp_path / (marker + ".md")
    methodology.write_text("fixture methodology")
    out = agentic_bundle / "README-SUMMARY.md"
    out.write_text("previous summary")
    with pytest.raises(KeyLeakError) as caught:
        generate_readme_summary(agentic_bundle, methodology_path=methodology)
    assert marker not in str(caught.value)
    assert out.read_text() == "previous summary"
