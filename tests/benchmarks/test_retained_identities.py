"""Retained comparisons must bind declared arms to captured transcript keys."""

from copy import deepcopy

import pytest

from benchmarks.agentic_report import _RunMetadata
from benchmarks.grading.disclosure import retained_claims
from benchmarks.grading.mapping import Vote
from benchmarks.report import _load_run_bundle
from benchmarks.report_measurements import comparison_identities_match
from tests.benchmarks.accepted_report_fixture import read, write
from tests.benchmarks.test_agentic_report import _render
from tests.benchmarks.test_agentic_report import validated_bundle as validated_bundle
from tests.benchmarks.test_agentic_report import validated_template as validated_template
from tests.benchmarks.test_verified_disclosures import record_audit


@pytest.mark.parametrize("field", ["competitor_id", "cell"])
def test_retained_report_rejects_declared_no_docs_sibling(validated_bundle, field):
    run = validated_bundle
    record_audit(run)
    path = run / "planned-cells.json"
    plan = read(path)
    row = next(c for c in plan["cells"] if c["system_id"] == "docs-fetch"
               and c["model_family_id"] == "scripted-openai"
               and c["corpus_id"] == "SYN-EX-002")
    row[field] = "no-docs--scripted-openai--r1"
    write(path, plan)
    meta = _RunMetadata(_load_run_bundle(run))
    assert meta.audit["verified"] is True
    assert meta.tier == "Validated"
    assert meta.retained["C2"]["scripted-openai"]["passed"] is None
    assert meta.retained["C2"]["scripted-openai"]["interval"] is None
    assert meta.claim_evidence("C2", "scripted-openai") is False
    assert meta.claim_evidence("C2", "scripted-anthropic") is True
    text = _render(run)
    assert "Without flagged questions, C2/scripted-openai: not evaluable" in text
    assert "Without flagged questions, C2/scripted-anthropic: pass, 0.500" in text


def _identities():
    plan = [{"cell": f"{s}--gpt-6.1-sol--r1",
             "competitor_id": f"{s}--gpt-6.1-sol--r1", "corpus_id": q,
             "system_id": s, "model_family_id": "gpt-6.1-sol", "repeat": 1}
            for s in ("python-docs", "docs-fetch", "no-docs") for q in ("Q.1", "Q.2")]
    observed = {(p["competitor_id"], p["corpus_id"]):
                {k: v for k, v in p.items() if k != "cell"} for p in plan}
    return plan, observed


@pytest.mark.parametrize("mutation", [
    "cell", "competitor_id", "missing-cell", "missing-competitor", "system", "family",
    "corpus", "repeat-bool", "repeat-string", "repeat-zero", "repeat-negative",
    "duplicate", "missing", "swapped", "foreign", "observed-cell", "observed-competitor",
    "observed-corpus", "observed-system", "observed-family", "observed-repeat",
    "observed-key-cell", "observed-key-corpus", "observed-duplicate-coordinate",
    "malformed-cell", "noncanonical-cell",
])
def test_compared_identities_must_match_actual_transcript_keys(mutation):
    plan, observed = _identities()
    row = plan[2]  # docs-fetch Q.1
    key = row["competitor_id"], row["corpus_id"]
    transcript = observed[key]
    if mutation in ("cell", "competitor_id"):
        row[mutation] = "no-docs--gpt-6.1-sol--r1"
    elif mutation.startswith("missing-"):
        del row["cell" if mutation == "missing-cell" else "competitor_id"]
    elif mutation in ("system", "family", "corpus"):
        row[{"system": "system_id", "family": "model_family_id", "corpus": "corpus_id"}[
            mutation]] = "other"
    elif mutation.startswith("repeat-"):
        row["repeat"] = {"repeat-bool": True, "repeat-string": "1", "repeat-zero": 0,
                         "repeat-negative": -1}[mutation]
    elif mutation == "duplicate":
        plan.append(deepcopy(row))
    elif mutation == "missing":
        plan.remove(row)
    elif mutation == "swapped":
        row["cell"], plan[3]["cell"] = "no-docs--gpt-6.1-sol--r1", row["cell"]
    elif mutation == "foreign":
        observed[("docs-fetch--gpt-6.1-sol--r2", "Q.1")] = {
            **transcript, "competitor_id": "docs-fetch--gpt-6.1-sol--r2", "repeat": 2}
    elif mutation.startswith("observed-key-"):
        new_key = ("foreign", key[1]) if mutation.endswith("cell") else (key[0], "other")
        observed[new_key] = observed.pop(key)
    elif mutation == "observed-duplicate-coordinate":
        observed[("foreign", "Q.1")] = deepcopy(transcript)
    elif mutation.startswith("observed-"):
        field = {"cell": "cell", "competitor": "competitor_id", "corpus": "corpus_id",
                 "system": "system_id", "family": "model_family_id", "repeat": "repeat"}[
                     mutation.removeprefix("observed-")]
        transcript[field] = 2 if field == "repeat" else "other"
    else:
        row["cell"] = row["competitor_id"] = (
            "../escape" if mutation == "malformed-cell" else "docs-fetch--gpt-6.1-sol--r01")
        observed.pop(key)
        observed[(row["cell"], "Q.1")] = {**transcript, "competitor_id": row["cell"]}
    assert not comparison_identities_match(
        plan, observed, model="gpt-6.1-sol", systems=("python-docs", "docs-fetch"))


@pytest.mark.parametrize("system", ["docs-fetch", "no-docs"])
def test_identity_join_excludes_flagged_questions_and_unrelated_arms(system):
    plan, observed = _identities()
    plan.append({**plan[-1], "corpus_id": "Q.1", "repeat": 2,
                 "cell": "no-docs--gpt-6.1-sol--r2",
                 "competitor_id": "no-docs--gpt-6.1-sol--r2"})
    # Both a malformed flagged row and a malformed unrelated family's row
    # must leave this comparison's unflagged observations available.
    plan[0]["cell"] = None
    plan.append({**plan[1], "model_family_id": "other", "repeat": False})
    assert comparison_identities_match(
        plan, observed, model="gpt-6.1-sol", systems=("python-docs", system),
        excluded=("Q.1",))


@pytest.mark.parametrize("family", ["gpt-6.1-sol", "gemini-3.8-flash", "-legacy", "_legacy"])
def test_identity_join_keeps_registered_and_legacy_coordinate_grammar(family):
    plan, _ = _identities()
    for row in plan:
        row["model_family_id"] = family
        row["cell"] = row["competitor_id"] = f"{row['system_id']}--{family}--r1"
    observed = {(p["competitor_id"], p["corpus_id"]): dict(p) for p in plan}
    assert comparison_identities_match(
        plan, observed, model=family, systems=("python-docs", "docs-fetch"))


@pytest.mark.parametrize("field", ["system_id", "model_family_id", "corpus_id"])
@pytest.mark.parametrize("value", ["", ".hidden", "../foreign", "a/b", "a b", "é"])
def test_identity_join_applies_existing_coordinate_grammar(field, value):
    plan, _ = _identities()
    plan[2][field] = value
    plan[2]["cell"] = plan[2]["competitor_id"] = (
        f"{plan[2]['system_id']}--{plan[2]['model_family_id']}--r1")
    observed = {(p["competitor_id"], p["corpus_id"]): dict(p) for p in plan}
    assert not comparison_identities_match(
        plan, observed, model=plan[2]["model_family_id"],
        systems=("python-docs", plan[2]["system_id"]))


def test_retained_sensitivity_empty_cohort_and_same_capture(validated_bundle):
    run = validated_bundle
    record_audit(run)
    meta = _RunMetadata(_load_run_bundle(run))
    original = retained_claims(meta, ["SYN-EX-001"])
    assert original["C2"]["scripted-openai"]["passed"] is True
    plan = deepcopy(meta.plan)
    row = next(c for c in meta.plan["cells"] if c["model_family_id"] == "scripted-openai"
               and c["system_id"] == "no-docs" and c["corpus_id"] == "SYN-EX-002")
    row["competitor_id"] = "docs-fetch--scripted-openai--r1"
    assert retained_claims(meta, ["SYN-EX-001"])["C2"] == original["C2"]
    assert meta.claim_evidence("C2", "scripted-openai") is True
    assert meta.claim_evidence("C2", "scripted-anthropic") is True
    meta.plan = plan
    path = run / "transcripts/docs-fetch--scripted-openai--r1/SYN-EX-002.json"
    path.write_text("{}")
    # Retained claims consume the admitted bytes, not a second filesystem view.
    assert retained_claims(meta, ["SYN-EX-001"]) == original
    empty = retained_claims(meta, list(meta.grade_capture.questions))
    assert empty["C2"]["scripted-openai"]["passed"] is None
    assert empty["C2"]["scripted-openai"]["interval"] is None
    judge = next(iter(meta.summary["efforts"]))
    for key, av in meta.grade_capture.answers.items():
        transcript = meta.transcripts[key]
        if (transcript["model_family_id"] == "scripted-openai"
                and transcript["system_id"] == "python-docs"):
            av.votes[judge] = Vote("controlled zero", True, False, False, False, False)
    sensitive = retained_claims(meta, ["SYN-EX-001"])
    assert sensitive["C2"]["scripted-openai"]["passed"] is None
    assert sensitive["C2"]["scripted-openai"]["reason"] == "judge-sensitive retained cohort"
    assert sensitive["C2"]["scripted-anthropic"]["passed"] is True
