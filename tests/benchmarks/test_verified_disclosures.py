"""Current-input disclosure controls. All receipts are unsigned local recordings."""

import json
import shutil
from dataclasses import asdict
from decimal import Decimal
from urllib.parse import urlsplit

import pytest

from benchmarks.agentic_report import _RunMetadata
from benchmarks.grading.aggregate import write_vote_record
from benchmarks.grading.batch import anthropic_result
from benchmarks.grading.disclosure import audit_request
from benchmarks.grading.inputs import (
    Criterion,
    GradingQuestion,
    JudgeInput,
    key_audit_schema,
    load_criteria,
)
from benchmarks.grading.panel import load_panel
from benchmarks.grading.score import _ledger_spend
from benchmarks.report import _load_run_bundle
from tests.benchmarks.accepted_report_fixture import digest, raw_result, read, write
from tests.benchmarks.test_agentic_report import (
    SCHEMA,
    _render,
)
from tests.benchmarks.test_agentic_report import (
    validated_bundle as validated_bundle,
)
from tests.benchmarks.test_agentic_report import (
    validated_template as validated_template,
)


def record_audit(run, flagged=("SYN-EX-001",)):
    """Real vote writer + normalizer, captured request and durable host receipt."""
    grading = run / "grading"
    questions = load_criteria(
        grading / "inputs/grading-criteria.yml",
        corpus_path=run / "snapshots/corpus.yml", schema_path=SCHEMA,
    )
    panel = load_panel(grading / "inputs/panel.yml")
    judge = panel.judges[panel.reference]
    prompt = (grading / "inputs/key-audit-prompt.md").read_text()
    inputs = read(grading / "inputs/inputs.json")
    invocation = {
        "grading_run_id": "audit", "target": "key-audit", "pass": 1,
        "composition": {judge.id: {"model_id": judge.model_id, "effort": judge.effort}},
        "status": "completed", "dry_run": False, "not_run": [], "units": ["audit-unit"],
        "records_written": len(questions), "execution_results": {},
        "spent_before": {judge.provider: "0"},
        "spent_after": {judge.provider: str(Decimal("0.01") * len(questions))},
    }
    bill = []
    for qid, question in questions.items():
        sections = []
        for url in question.citations:
            version = urlsplit(url).path.lstrip("/").split("/", 1)[0]
            if version not in question.version.split(" and "):
                version = question.version.split(" and ")[0]
            sections.append({"url": url, "version": version, "text": question.answer_key})
        user = json.dumps({
            "question_id": qid, "version": question.version, "question": question.prompt,
            "answer_key": question.answer_key, "cited_sections": sections,
        }, sort_keys=True, separators=(",", ":"))
        request = JudgeInput(prompt, user, key_audit_schema(), False)
        write(grading / "items/key-audit" / f"{qid}.json", request.item_evidence({
            "corpus_id": qid, "cited_sections": sections, "inputs": inputs,
        }))
        raw = raw_result(judge.provider, qid, "recorded-reference-2026-11-04", json.dumps({
            "explanation": "Synthetic cited-text audit", "key_supported": qid not in flagged,
        }))
        result = anthropic_result(raw)
        path = write_vote_record(
            grading, target="key-audit", judge=judge, effort=judge.effort, pass_number=1,
            grading_run_id="audit", attempt=1, result=result, terminal=False,
            unit_id="audit-unit", provider_batch_id="audit-batch", input_sha256=request.sha256(),
            schema=key_audit_schema(),
        )
        vote = read(path)
        history = {k: vote[k] for k in (
            "grading_run_id", "unit_id", "item_id", "judge_id", "target", "pass", "attempt",
            "provider_batch_id", "input_sha256", "model_id", "effort",
        )}
        history.update(vote_sha256=digest(path.read_bytes()), provider=judge.provider,
                       api=judge.api, result=asdict(result))
        relative = f"{judge.id}/{qid}--a1.json"
        body = write(grading / "executions/audit" / relative, history)
        invocation["execution_results"][relative] = digest(body)
        bill.append({"item_id": qid, "status": "ok", "booked": "0.01"})
    write(grading / "runs/audit.json", invocation)
    identity = {"unit_id": "audit-unit", "run_id": "audit", "provider": judge.provider}
    with (grading / "ledger-events.jsonl").open("a") as ledger:
        for event in [
            {**identity, "event": "reserve", "kind": "batch", "model_id": judge.model_id,
             "api": judge.api, "job_id": f"{judge.id}--grading", "bound": "100"},
            {**identity, "event": "settle", "outcome": "batch", "booked": "0.50",
             "actual": "0.50", "items": bill},
        ]:
            ledger.write(json.dumps(event) + "\n")


def test_cached_audit_subset_and_cost_cannot_fabricate_public_disclosures(validated_bundle):
    run = validated_bundle
    path = run / "grading/summary.json"
    summary = read(path)
    summary["key_audit"] = {"audited": 50, "flagged": ["SYN-EX-001"]}
    summary["claims_without_flagged_keys"] = {"C2": {"scripted-openai": {
        "passed": True, "interval": {"point": .03, "low": .02, "high": .04},
    }}}
    summary["spend"]["by_provider"]["google"] = "9999.99"
    write(path, summary)
    text = _render(run)
    assert "9999.99" not in text
    assert "Without flagged questions, C2/scripted-openai: pass" not in text
    assert "Answer-key audit: unverified" in text
    assert "| google | 3.00 | 0.010000 |" in text


def test_positive_audit_and_ledger_survive_portable_export(validated_bundle, tmp_path):
    record_audit(validated_bundle)
    moved = shutil.copytree(validated_bundle, tmp_path / "export")
    shutil.rmtree(validated_bundle)
    before = (moved / "grading/summary.json").read_bytes()
    text = _render(moved)
    assert "Answer-key audit: 50 keys audited" in text
    assert "flagged: SYN-EX-001." in text
    assert "Without flagged questions, C2/scripted-openai: pass, 0.500" in text
    # 6.00 grading + .50 audit, divided by the same 300 LLM finals as the scorer.
    assert "| anthropic | 6.50 | 0.021667 |" in text
    assert (moved / "grading/summary.json").read_bytes() == before


@pytest.mark.parametrize("scope", ["none", "all"])
def test_real_zero_flags_and_empty_retained_cohort_are_distinct(validated_bundle, scope):
    from benchmarks.grading.inputs import load_criteria

    root = validated_bundle / "grading"
    questions = load_criteria(
        root / "inputs/grading-criteria.yml",
        corpus_path=validated_bundle / "snapshots/corpus.yml", schema_path=SCHEMA,
    )
    record_audit(validated_bundle, flagged=tuple(questions) if scope == "all" else ())
    text = _render(validated_bundle)
    assert "Answer-key audit: 50 keys audited" in text
    assert "Without flagged questions, C2/scripted-openai: pass" not in text
    if scope == "none":
        assert "flagged: none." in text
        assert "Without flagged questions," not in text
    else:
        assert "Without flagged questions, C2/scripted-openai: not evaluable" in text


@pytest.mark.parametrize("mutation", ["missing", "digest", "parsed", "receipt", "section",
                                      "identity", "withheld", "newer", "cohort", "count"])
def test_audit_or_retained_cohort_must_be_current(validated_bundle, mutation):
    run = validated_bundle
    record_audit(run)
    root = run / "grading"
    vote_path = next((root / "votes/key-audit/reference/high/pass1/SYN-EX-001").glob("*.json"))
    vote = read(vote_path)
    if mutation == "missing":
        vote_path.unlink()
    elif mutation == "receipt":
        (root / "executions/audit/reference/SYN-EX-001--a1.json").unlink()
    elif mutation == "section":
        path = root / "items/key-audit/SYN-EX-001.json"
        item = read(path)
        item["meta"]["cited_sections"][0]["text"] += " changed"
        write(path, item)
    elif mutation == "cohort":
        path = run / "scoring/python-docs--scripted-openai--r1/SYN-EX-002.json"
        row = read(path)
        row["score"] = 0
        write(path, row)
    elif mutation == "count":
        path = run / "tokens/python-docs--scripted-openai--r1/SYN-EX-002.json"
        row = read(path)
        row["client_wrapped_tokens"] = 1
        write(path, row)
    else:
        if mutation == "digest":
            vote["input_sha256"] = "d" * 64
        elif mutation == "parsed":
            vote["parsed"]["key_supported"] = True
        elif mutation == "identity":
            vote["target"] = "answers"
        elif mutation == "withheld":
            vote["withheld"] = "parse_failure"
        elif mutation == "newer":
            vote.update(attempt=2, graded_at="2099-01-01T00:00:00Z")
            vote_path = vote_path.with_name("audit--a2.json")
        write(vote_path, vote)
    text = _render(run)
    assert "Without flagged questions, C2/scripted-openai: pass" not in text
    if mutation == "count":
        assert "Without flagged questions, C2/scripted-openai: not evaluable" in text
    else:
        assert "unverified" in text


@pytest.mark.parametrize("stage", ["pending", "zero", "missing-ledger"])
def test_cost_uses_current_settlements_and_genuine_graded_denominator(validated_bundle, stage):
    run = validated_bundle
    if stage == "missing-ledger":
        (run / "grading/ledger-events.jsonl").unlink()
    else:
        keys = sorted((run / "scoring").glob("*/*.json"))
        for path in keys if stage == "zero" else keys[:1]:
            row = read(path)
            row.update(score=None, grading=None, scoring_method="pending")
            write(path, row)
    metadata = _RunMetadata(_load_run_bundle(run))
    text = _render(run)
    if stage == "missing-ledger":
        assert "Grading cost: unverified" in text
        assert "| google | 3.00 |" not in text
    elif stage == "pending":
        assert len(metadata.grade_capture.finals) == 299
        assert "| google | 3.00 | 0.010033 |" in text
    else:
        assert "| google | 3.00 | n/a |" in text


@pytest.mark.parametrize("graded,ratio", [(2, "1.250000"), (1, "2.500000"), (0, None)])
def test_scorer_books_settled_charges_including_conservative_costs(tmp_path, graded, ratio):
    # A reservation is not spend; a conservatively booked charge is not actual usage.
    events = [
        {"event": "reserve", "provider": "google", "bound": "100"},
        {"event": "settle", "provider": "google", "booked": "0.50", "actual": "0.50"},
        {"event": "settle", "provider": "google", "booked": "2.00", "actual": None},
        {"event": "settle", "provider": "openai", "booked": "0", "actual": "0"},
    ]
    (tmp_path / "ledger-events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    spend = _ledger_spend(tmp_path, graded)
    assert spend["by_provider"] == {"google": "2.50", "openai": "0"}
    assert spend["per_graded_answer"].get("google") == ratio
    assert spend["graded_answers"] == graded


def test_audit_preserves_real_cited_section_fields_and_version_pairs():
    question = GradingQuestion(
        "versions", "cross-version", "3.12 and 3.13", "Compare a feature.", "The key.", None,
        ("https://docs.python.org/3.12/library/os.html", "https://docs.python.org/3.13/library/os.html"),
        (Criterion("p1", "First.", "core"), Criterion("p2", "Second.", "nuance")),
    )
    sections = [
        {"url": url, "version": version, "page": "library/os.html", "anchor": None,
         "heading": "Synthetic recorded heading", "text": "Synthetic captured docs."}
        for url, version in zip(question.citations, ("3.12", "3.13"), strict=True)
    ]
    request = audit_request(question, sections, prompt=b"Frozen audit prompt")
    assert json.loads(request.user)["cited_sections"] == sections
    assert request.system == "Frozen audit prompt"
