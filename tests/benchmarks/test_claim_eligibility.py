"""Claim identity must be admitted before panel/single-judge statistics and G4."""

import json
from pathlib import Path

import pytest
import yaml

from benchmarks.grading.gates import run_offline_evidence
from benchmarks.grading.score import _model_families
from tests.benchmarks import test_grading_score as runs
from tests.benchmarks.test_agentic_report import validated_bundle as validated_bundle
from tests.benchmarks.test_agentic_report import validated_template as validated_template


@pytest.mark.parametrize("mutation", ["competitor_id", "cell", "missing", "malformed-repeat"])
def test_scorer_withholds_ineligible_comparison_before_g4(tmp_path: Path, mutation: str):
    run = runs._build_run(tmp_path)
    path = run / "planned-cells.json"
    plan = json.loads(path.read_bytes())
    row = next(c for c in plan["cells"] if c["system_id"] == "docs-fetch"
               and c["corpus_id"] == "SYN-EX-002")
    if mutation == "missing":
        plan["cells"].remove(row)
    elif mutation == "malformed-repeat":
        row["repeat"] = True
    else:
        row[mutation] = f"no-docs--{runs.FAMILY}--r1"
    runs._json(path, plan)
    summary = runs._score(run, tmp_path)
    for claims in (summary["claims"], *summary["claims_by_judge"].values()):
        claim = claims["C2"][runs.FAMILY]
        assert claim["passed"] is None and claim["interval"] is None
    assert summary["gates"]["G4"]["passed"] is None
    assert summary["tier"] == "Judge not validated"


@pytest.mark.parametrize("field", ["competitor_id", "cell", "repeat", "missing", "local-sibling"])
def test_hosted_plan_conflict_preserves_local_offline_evidence(tmp_path: Path, field: str):
    run = runs._airgap_run(tmp_path)
    runs._json(run / "airgap/checks.json",
               {"all_passed": True, "checks": 5, "internet_hosts": []})
    path = run / "planned-cells.json"
    plan = json.loads(path.read_bytes())
    row = next(c for c in plan["cells"] if c["system_id"] == "docs-fetch")
    if field == "missing":
        plan["cells"].remove(row)
    elif field == "local-sibling":
        row["competitor_id"] = "no-docs--scripted-local--r1"
    else:
        row[field] = False if field == "repeat" else f"no-docs--{runs.FAMILY}--r1"
    runs._json(path, plan)
    evidence = run_offline_evidence(run, _model_families(run))
    assert evidence.verdict.ok is True
    assert evidence.expected_checks == 5
    assert evidence.verdict.denied_attempts == [
        {"job_id": "scripted-local--no-docs", "host": "pypi.org:443"},
        {"job_id": "scripted-local--python-docs", "host": "pypi.org:443"},
    ]
    summary = runs._score(run, tmp_path)
    assert summary["claims"]["C3"]["scripted-local"]["passed"] is True
    assert summary["claims"]["C2"][runs.FAMILY]["passed"] is None


@pytest.mark.parametrize("state", ["active", "missing", "malformed", "different"])
def test_offline_family_declaration_does_not_change_hosted_c2(tmp_path: Path, state: str):
    run = runs._airgap_run(tmp_path)
    runs._json(run / "airgap/checks.json",
               {"all_passed": True, "checks": 5, "internet_hosts": []})
    families = _model_families(run)
    if state == "missing":
        del families["scripted-local"]
    elif state == "malformed":
        families["scripted-local"] = {"arm": ["air-gapped"]}
    elif state == "different":
        families["other-local"] = families.pop("scripted-local")
    evidence = run_offline_evidence(run, families)
    assert evidence.verdict.ok is (True if state == "active" else None)
    # The scorer reads the actual exported declaration, independently of C2.
    if state != "active":
        runs_path = run / "snapshots/model-matrix.yml"
        matrix = yaml.safe_load(runs_path.read_text())
        matrix["model_families"] = [{**value, "id": key} for key, value in families.items()]
        runs_path.write_text(yaml.safe_dump(matrix))
    summary = runs._score(run, tmp_path)
    assert summary["claims"]["C2"][runs.FAMILY]["passed"] is True
    if state == "active":
        assert summary["claims"]["C3"]["scripted-local"]["passed"] is True
    else:
        assert summary["claims"]["C3"] == {}


@pytest.mark.parametrize("mutation", [
    "competitor", "family", "missing-family", "malformed-family", "orphan", "missing-plan",
    "unowned-plan",
])
def test_local_inventory_conflict_still_withholds_c3_only(tmp_path: Path, mutation: str):
    run = runs._airgap_run(tmp_path)
    runs._json(run / "airgap/checks.json",
               {"all_passed": True, "checks": 5, "internet_hosts": []})
    path = run / "planned-cells.json"
    plan = json.loads(path.read_bytes())
    row = next(c for c in plan["cells"] if c["model_family_id"] == "scripted-local")
    if mutation == "competitor":
        row["competitor_id"] = "python-docs--foreign--r1"
    elif mutation == "family":
        row["model_family_id"] = "foreign-local"
    elif mutation == "missing-family":
        del row["model_family_id"]
    elif mutation == "malformed-family":
        row["model_family_id"] = ["scripted-local"]
    elif mutation == "missing-plan":
        plan["cells"].remove(row)
    elif mutation == "unowned-plan":
        plan["cells"].append({})
    else:
        runs._json(run / "discarded/python-docs--scripted-local--r2/attempt.json",
                   {"reason": "budget_stop"})
    runs._json(path, plan)
    evidence = run_offline_evidence(run, _model_families(run))
    assert evidence.verdict.ok is None
    assert evidence.expected_checks is None
    summary = runs._score(run, tmp_path)
    assert summary["claims"]["C2"][runs.FAMILY]["passed"] is True
    assert summary["claims"]["C3"]["scripted-local"]["passed"] is None


def test_report_has_no_validated_outcome_when_all_claim_identities_are_ineligible(validated_bundle):
    # Short scripted fixtures cannot establish this public Validated outcome.
    from benchmarks.agentic_report import _RunMetadata
    from benchmarks.report import _load_run_bundle
    from tests.benchmarks.test_agentic_report import _readme, _render

    run = validated_bundle
    path = run / "planned-cells.json"
    plan = json.loads(path.read_bytes())
    for row in plan["cells"]:
        if row["system_id"] == "docs-fetch" and row["corpus_id"] == "SYN-EX-002":
            row["competitor_id"] = f"no-docs--{row['model_family_id']}--r1"
    runs._json(path, plan)
    meta = _RunMetadata(_load_run_bundle(run))
    assert meta.summary["gates"]["G4"]["passed"] is None
    assert meta.tier == "Judge not validated"
    for claims in (meta.summary["claims"], *meta.summary["claims_by_judge"].values()):
        assert all(c["passed"] is None and c["interval"] is None for c in claims["C2"].values())
    assert "Grading outcome: Judge not validated" in _render(run)
    assert "fewer normalized tokens" not in _readme(run)
