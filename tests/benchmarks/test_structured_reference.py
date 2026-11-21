"""Synthetic retained evidence only: no providers, subprocesses, or grading data."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from benchmarks import structured
from benchmarks.agentic import run as runner
from benchmarks.agentic.inputs import CapturedInput
from benchmarks.agentic.ledger import PROVIDER_CAPS, Ledger
from benchmarks.agentic.live import full_run_gate, provider_plans
from benchmarks.agentic.prompt import SYSTEM_PROMPT
from benchmarks.agentic.structured_reference import (
    MODELS,
    PILOT_IDS,
    REGISTERED,
    SEED,
    SYSTEMS,
    StructuredStudy,
)
from benchmarks.model_matrix import load_model_matrix
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks.test_agentic_live import (
    _bind_reference_transcripts,
    _settled_unit,
    _write_record,
)
from tests.benchmarks.test_agentic_run import (
    COMMITTED_MANIFEST,
    COMMITTED_MATRIX,
    COMMITTED_PRICES,
    _container,
)


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def _read(path: Path) -> Any:
    return json.loads(path.read_text())


@pytest.fixture
def pilot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    # All runner metadata is local; even the normal git metadata call is replaced.
    monkeypatch.setattr(runner, "_environment_metadata", lambda **_: {"repo_commit": "fixture"})
    monkeypatch.setattr(runner, "default_ledger_dir", lambda: tmp_path / "ledger")
    monkeypatch.setattr(
        "benchmarks.grading.preflight.reference_grading_rates",
        lambda *_: pytest.fail("structured mode requested hosted grading"),
    )
    monkeypatch.delenv("CI", raising=False)
    corpus = tmp_path / "frozen.yml"
    corpus.write_bytes(
        structured.render_corpus(
            (REGISTERED.parent / "corpus.yml").read_bytes(),
            (REGISTERED / "items.json").read_bytes(),
        )
    )
    study = StructuredStudy.load(
        REGISTERED / "items.json",
        CapturedInput.read(corpus, "corpus.yml", "corpus"),
    )
    (tmp_path / "index.db").write_bytes(b"synthetic index identity")
    launcher, docker = _container(tmp_path)
    config = runner.AgenticConfig(
        corpus_path=corpus,
        matrix_path=COMMITTED_MATRIX,
        manifest_path=COMMITTED_MANIFEST,
        prices_path=COMMITTED_PRICES,
        ledger_dir=tmp_path / "ledger",
        out_dir=tmp_path / "pilot",
        structured_key=REGISTERED / "items.json",
        dry_run=True,
    )
    summary = runner.run_agentic(config, launcher=launcher, today=date(2026, 11, 4))
    assert docker.calls == []
    root = config.out_dir
    families = {f.id: f for f in load_model_matrix(COMMITTED_MATRIX).model_families}
    events = []
    for n, entry in enumerate(_read(root / "planned-cells.json")["cells"]):
        family = families[entry["model_family_id"]]
        path = root / "transcripts" / entry["cell"] / f"{entry['corpus_id']}.json"
        unit = f"unit-{n}"
        _write_record(
            path,
            family.id,
            family.provider,
            1000,
            0,
            model_id=family.model_id,
            api=family.api,
            unit_id=unit,
        )
        record = _read(path)
        # Deliberately wrong but valid; performance must not select advancement.
        wrong = next(c for c in "ABCD" if c != study.items[entry["corpus_id"]])
        answer = json.dumps({"answer": wrong})
        record.update(
            system_id=entry["system_id"],
            status="succeeded",
            error=None,
            answer=answer,
            system_prompt_sha256=summary["structured"]["binding"]["sha256"]["system-prompt.txt"],
            canonical_messages=[
                {"role": "user", "content": study.prompts[entry["corpus_id"]]},
                {"role": "assistant", "content": [{"type": "text", "text": answer}]},
            ],
        )
        _write(path, record)
        _write(
            root / "scoring" / entry["cell"] / path.name, study.score(record, entry["corpus_id"])
        )
        for event in _settled_unit(unit, family.id, "0.001", "ok", model_id=family.model_id):
            event["provider"] = family.provider
            if event["event"] == "reserve":
                event["job_id"] = f"{family.id}--{entry['system_id']}"
            events.append(event)
    (root / "ledger-events.jsonl").write_text("\n".join(json.dumps(e) for e in events))
    summary.update(
        run_id="pilot",
        dry_run=False,
        status="completed",
        scored_cells=270,
        correctness_denominator_cells=270,
        succeeded_cells=270,
        failed_cells=0,
        discarded=[],
        not_run=[],
        worker_errors=[],
        ledger={
            p: {"spent_before": "0", "spent_after": "0.090", "cap": str(cap), "stopped": None}
            for p, cap in PROVIDER_CAPS.items()
        },
    )
    _write(root / "run-summary.json", summary)
    _bind_reference_transcripts(root)
    return {
        "study": study,
        "root": root,
        "binding": summary["structured"]["binding"],
        "config": config,
        "launcher": launcher,
        "docker": docker,
    }


def _edit_answers(pilot: dict[str, Any], count: int, *, failed: bool = False) -> None:
    root, study = pilot["root"], pilot["study"]
    # One pair, spread over repeats: threshold is per pair, not global.
    paths = sorted((root / "transcripts").glob("no-docs--gpt-6.1-sol--*/*.json"))[:count]
    for path in paths:
        record = _read(path)
        if failed:
            record.update(status="failed", error={"category": "timeout"})
        else:
            record["answer"] = '{"answer":"A","answer":"B"}'
            record["canonical_messages"][-1]["content"][0]["text"] = record["answer"]
        _write(path, record)
        _write(
            root / "scoring" / path.parent.name / path.name,
            study.score(record, record["corpus_id"]),
        )
    if failed:
        summary = _read(root / "run-summary.json")
        summary.update(succeeded_cells=270 - count, failed_cells=count)
        _write(root / "run-summary.json", summary)
    _bind_reference_transcripts(root)


def test_zero_accuracy_pilot_passes_and_projects_answering_only(pilot: dict[str, Any]) -> None:
    rates = pilot["study"].verified_rates(pilot["root"], pilot["binding"])
    assert set(rates) == MODELS
    assert all(r.cells == 90 and r.usd_per_cell == Decimal(".001") for r in rates.values())
    report = structured.score_run(pilot["root"], pilot["config"].corpus_path)
    assert report["correct"] == 0 and report["valid"] == report["planned"] == 270
    full = runner.run_agentic(
        replace(
            pilot["config"], reference_run=pilot["root"], out_dir=pilot["root"].parent / "full"
        ),
        launcher=pilot["launcher"],
        today=date(2026, 11, 4),
    )
    assert full["planned_cells"] == 1350
    assert full["structured"]["binding"] == pilot["binding"]
    assert full["seed"] == SEED and pilot["docker"].calls == []


@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize("count,passes", [(3, True), (4, False)])
def test_per_pair_success_and_format_threshold(
    pilot: dict[str, Any],
    failed: bool,
    count: int,
    passes: bool,
) -> None:
    _edit_answers(pilot, count, failed=failed)
    if passes:
        pilot["study"].verified_rates(pilot["root"], pilot["binding"])
    else:
        with pytest.raises(BenchmarkValidationError, match="27/30"):
            pilot["study"].verified_rates(pilot["root"], pilot["binding"])


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "hash",
        "duplicate-plan",
        "changed-plan",
        "summary",
        "prompt",
        "answer",
        "scoring",
        "snapshot",
        "execution",
        "ledger",
        "stop",
        "cap",
        "cap-overrun",
        "overrun",
        "not-run",
        "worker-error",
        "full-reference",
        "seed",
        "wrong-question",
        "missing-plan",
    ],
)
def test_any_invalid_reference_stops_before_authorization(
    pilot: dict[str, Any],
    mutation: str,
) -> None:
    root = pilot["root"]
    path = next((root / "transcripts").glob("*/*.json"))
    summary = _read(root / "run-summary.json")
    if mutation in {"missing", "hash"}:
        path.unlink() if mutation == "missing" else path.write_text(path.read_text() + " ")
    elif mutation in {"duplicate-plan", "changed-plan", "missing-plan"}:
        plan = _read(root / "planned-cells.json")
        if mutation == "duplicate-plan":
            plan["cells"][-1] = plan["cells"][0]
        elif mutation == "changed-plan":
            plan["cells"][0]["repeat"] = 4
        else:
            plan["cells"].pop()
        _write(root / "planned-cells.json", plan)
    elif mutation in {"prompt", "answer", "execution", "wrong-question"}:
        record = _read(path)
        if mutation == "prompt":
            record["canonical_messages"][0]["content"] = "an open-ended question"
        elif mutation == "answer":
            record["answer"] = '{"answer":"D"}'
            record["canonical_messages"][-1]["content"][0]["text"] = "other answer"
            _write(
                root / "scoring" / path.parent.name / path.name,
                pilot["study"].score(record, record["corpus_id"]),
            )
        elif mutation == "execution":
            record["execution"]["api"] = "scripted"
        else:
            record["corpus_id"] = "EXTRA"
        _write(path, record)
    elif mutation == "scoring":
        _write(root / "scoring" / path.parent.name / path.name, {"score": 1, "format_valid": True})
    elif mutation == "snapshot":
        (root / "snapshots/structured-items.json").write_text("[]")
    elif mutation in {"ledger", "stop", "overrun"}:
        events = [
            json.loads(line) for line in (root / "ledger-events.jsonl").read_text().splitlines()
        ]
        if mutation == "ledger":
            events.pop()
        elif mutation == "overrun":
            events[1]["overrun"] = True
        else:
            events.append(
                {"provider": "openai", "run_id": "pilot", "event": "stop", "reason": "cap"}
            )
        (root / "ledger-events.jsonl").write_text("\n".join(json.dumps(e) for e in events))
    else:
        if mutation == "summary":
            summary["succeeded_cells"] = 269
        elif mutation == "cap":
            summary["ledger"]["openai"]["cap"] = "1000"
        elif mutation == "cap-overrun":
            summary["ledger"]["openai"].update(spent_before="65", spent_after="65.090")
        elif mutation == "not-run":
            summary["not_run"] = [{"reason": "budget"}]
        elif mutation == "worker-error":
            summary["worker_errors"] = [{"reason": "worker stopped"}]
        elif mutation == "full-reference":
            summary["structured"]["stage"] = "full"
        elif mutation == "seed":
            summary["seed"] = 2
        _write(root / "run-summary.json", summary)
    if mutation not in {"hash", "missing"}:
        _bind_reference_transcripts(root)  # self-consistent hashes cannot excuse bad evidence
    with pytest.raises(BenchmarkValidationError):
        runner.run_agentic(
            replace(
                pilot["config"], dry_run=False, reference_run=root, out_dir=root.parent / "full"
            ),
            launcher=pilot["launcher"],
            today=date(2026, 11, 4),
            confirm=lambda _: pytest.fail("invalid evidence reached confirmation"),
        )
    assert pilot["docker"].calls == []


@pytest.mark.parametrize(
    "option,value",
    [
        ("questions", ("AP-001",)),
        ("models", ("gpt-6.1-sol",)),
        ("systems", ("no-docs",)),
        ("repeats", 1),
        ("seed", 0),
        ("acknowledge_overrun", ("openai",)),
    ],
)
def test_no_splits_or_changed_options(pilot: dict[str, Any], option: str, value: Any) -> None:
    with pytest.raises(BenchmarkValidationError):
        runner.run_agentic(
            replace(pilot["config"], out_dir=pilot["root"].parent / "other", **{option: value}),
            launcher=pilot["launcher"],
            today=date(2026, 11, 4),
        )


def test_arbitrary_corpus_or_key_cannot_enable_zero_fee(pilot: dict[str, Any]) -> None:
    corpus = CapturedInput.read(pilot["config"].corpus_path, "corpus.yml", "corpus")
    with pytest.raises(BenchmarkValidationError, match="renderer"):
        StructuredStudy.load(
            REGISTERED / "items.json", replace(corpus, content=corpus.content + b"\n")
        )
    key = pilot["root"].parent / "custom-key.json"
    key.write_text("[]")
    with pytest.raises(BenchmarkValidationError, match="registered items"):
        StructuredStudy.load(key, corpus)


def test_shared_caps_ledger_and_pilot_balance_are_required(pilot: dict[str, Any]) -> None:
    config, study = pilot["config"], pilot["study"]
    with pytest.raises(BenchmarkValidationError, match="shared ledger"):
        study.check_options(
            replace(config, ledger_dir=config.out_dir), PROVIDER_CAPS, config.ledger_dir
        )
    with pytest.raises(BenchmarkValidationError, match="caps"):
        study.check_options(config, {**PROVIDER_CAPS, "openai": Decimal(100)}, config.ledger_dir)
    with pytest.raises(BenchmarkValidationError, match="retained balance"):
        study.check_balances(pilot["root"], dict.fromkeys(PROVIDER_CAPS, Decimal(0)))


def _seed_shared_ledger(path: Path, amount: Decimal) -> None:
    ledger = Ledger.open(path, run_id="synthetic-history")
    try:
        for provider in PROVIDER_CAPS:
            unit = ledger.reserve(
                provider,
                model_id="fixture",
                job_id="fixture",
                kind="model_call",
                bound=amount,
                input_bound=1,
                output_bound=1,
            )
            assert unit is not None
            ledger.settle(unit, outcome="ok", actual=amount, buckets=None)
    finally:
        ledger.close()


def test_full_gate_keeps_confirmation_and_shared_spend(pilot: dict[str, Any]) -> None:
    config = pilot["config"]
    _seed_shared_ledger(config.ledger_dir, Decimal(".09"))
    seen = []
    with pytest.raises(BenchmarkValidationError, match="not confirmed"):
        runner.run_agentic(
            replace(
                config,
                dry_run=False,
                reference_run=pilot["root"],
                out_dir=config.out_dir.parent / "full",
            ),
            launcher=pilot["launcher"],
            today=date(2026, 11, 4),
            confirm=lambda text: seen.append(text) or False,
        )
    assert len(seen) == 1
    assert seen[0].count("grading $0.00 for 1350 answers") == 3
    assert "x 1.25" in seen[0] and "spent $0.09" in seen[0]
    assert pilot["docker"].calls == []


def test_original_cost_and_time_limits_still_apply(pilot: dict[str, Any]) -> None:
    from benchmarks.agentic.bridge import load_systems
    from benchmarks.runner import _load_questions

    config, study = pilot["config"], pilot["study"]
    corpus = CapturedInput.read(config.corpus_path, "corpus.yml", "corpus")
    jobs = runner.plan_jobs(
        _load_questions(corpus.mapping),
        [f for f in load_model_matrix(COMMITTED_MATRIX).model_families if f.id in MODELS],
        load_systems(COMMITTED_MANIFEST),
        repeats=3,
        seed=SEED,
        matrix_dir=COMMITTED_MATRIX.parent,
    )
    rates = study.verified_rates(pilot["root"], pilot["binding"])
    spent = {p: cap - Decimal(".5") for p, cap in PROVIDER_CAPS.items()}
    plans = provider_plans(jobs, spent, PROVIDER_CAPS, rates, {})
    assert len(full_run_gate(plans, jobs, rates)) == 3  # .45 x 1.25 > .50 per provider
    slow = {key: replace(value, seconds_per_cell=200) for key, value in rates.items()}
    plans = provider_plans(jobs, {}, PROVIDER_CAPS, slow, {})
    assert all("24-hour" in reason for reason in full_run_gate(plans, jobs, slow))
    assert len(full_run_gate(plans, jobs, slow)) == 3


def test_legacy_config_has_no_structured_default() -> None:
    from benchmarks.__main__ import _build_parser

    args = _build_parser().parse_args(["agentic-run", "--corpus", "c.yml", "--out", "run"])
    assert args.structured_key is None and args.repeats == 3 and args.seed is None
    assert set(PILOT_IDS) and len(MODELS) * len(SYSTEMS) == 9
    assert SYSTEM_PROMPT.endswith("cite the section URL you relied on.")


def test_open_ended_reference_still_requires_hosted_grading(
    pilot: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def grading_required(_: Path) -> None:
        raise BenchmarkValidationError("legacy hosted grading still required")

    monkeypatch.setattr("benchmarks.grading.preflight.reference_grading_rates", grading_required)
    with pytest.raises(BenchmarkValidationError, match="legacy hosted grading"):
        runner.run_agentic(
            replace(
                pilot["config"],
                structured_key=None,
                reference_run=pilot["root"],
                out_dir=pilot["root"].parent / "legacy",
            ),
            launcher=pilot["launcher"],
            today=date(2026, 11, 4),
        )


@pytest.mark.parametrize("part", ["matrix", "manifest", "prices", "image", "index", "scorer"])
def test_full_must_match_all_pilot_execution_inputs(pilot: dict[str, Any], part: str) -> None:
    binding = json.loads(json.dumps(pilot["binding"]))
    if part in {"image", "index"}:
        binding["image" if part == "image" else "index_sha256"] = "changed"
    else:
        name = {
            "matrix": "model-matrix.yml",
            "manifest": "competitor-manifest.yml",
            "prices": "price-snapshot.yml",
            "scorer": "structured-scorer.py",
        }[part]
        binding["sha256"][name] = "0" * 64
    with pytest.raises(BenchmarkValidationError, match="option mismatch"):
        pilot["study"].verified_rates(pilot["root"], binding)


def test_missing_discarded_attempt_is_unaccounted_work(pilot: dict[str, Any]) -> None:
    path = next((pilot["root"] / "transcripts").glob("*/*.json"))
    record = _read(path)
    record["attempt"] = 2
    _write(path, record)
    _bind_reference_transcripts(pilot["root"])
    with pytest.raises(BenchmarkValidationError, match="retained attempts"):
        pilot["study"].verified_rates(pilot["root"], pilot["binding"])


def test_collector_scores_locally_with_shared_parser(pilot: dict[str, Any]) -> None:
    from benchmarks.runner import Question

    path = next((pilot["root"] / "transcripts").glob("*/*.json"))
    record = _read(path)
    qid, study = record["corpus_id"], pilot["study"]
    record.update(
        answer=json.dumps({"answer": study.items[qid]}),
        started_at="2026-11-04T00:00:00Z",
        completed_at="2026-11-04T00:00:01Z",
    )
    record["timing"].update(used_ms=1000, overhead_ms=0)
    question = Question(qid, study.prompts[qid], {})
    out = pilot["root"].parent / "collected"
    collector = runner._Collector(out, {qid: question}, [], study)
    ident = {**record, "tool_model_key": "fixture"}
    collector._write_cell(ident, record)
    score = _read(out / "scoring" / path.parent.name / path.name)
    assert score["score"] == 1 and score["format_valid"] is True
    assert score["requires_manual_scoring"] is False
    assert collector.succeeded == 1
