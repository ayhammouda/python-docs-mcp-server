"""G1–G4, the tier, the G3 sample and the C2/C3 verdicts (PR 5 Task 7; design §§2, 9.3)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from benchmarks.grading.gates import (
    CellKey,
    G1Binding,
    GateResult,
    OfflineEvidence,
    SampleCandidate,
    c2,
    c3,
    g1,
    g2,
    g3,
    g3_sample,
    g4,
    length_grade_correlation,
    load_egress_evidence,
    load_offline_evidence,
    offline_verdict,
    outcome_tier,
    reference_probe_check,
    run_binding,
    select_g1,
    self_preference,
)
from benchmarks.grading.gates import (
    test_retest as retest_alpha,
)
from benchmarks.grading.probes import PROBE_TYPES, Probe
from benchmarks.runner import BenchmarkValidationError

MODEL = "claude-sonnet-5-5"
QUESTIONS = [f"Q{i}" for i in range(10)]
JOBS = ["gemma4-12b--python-docs", "gemma4-12b--no-docs"]
EXPECTED = 7
CLEAN_EGRESS = {
    "jobs": {
        job: {
            "hosts_allowed": ["host.docker.internal:11434"],
            "hosts_denied": [],
            "lookup_package_docs_attempts": 0,
        }
        for job in JOBS
    },
    "isolation_probe": "pass",
}


def _cells(systems: tuple[str, ...], model: str = MODEL, repeats: int = 2) -> set[CellKey]:
    return {(s, model, q, r) for s in systems for q in QUESTIONS for r in range(1, repeats + 1)}


def _c2_inputs(
    pd_tokens: int, peer_tokens: int, *, pd_score: float = 1.0, peer_score: float = 1.0
) -> tuple[dict[CellKey, int | None], dict[CellKey, float | None]]:
    tokens: dict[CellKey, int | None] = {}
    scores: dict[CellKey, float | None] = {}
    for key in _cells(("python-docs", "docs-fetch")):
        python_docs = key[0] == "python-docs"
        tokens[key] = pd_tokens if python_docs else peer_tokens
        scores[key] = pd_score if python_docs else peer_score
    return tokens, scores


def test_c2_passes_only_when_the_upper_bound_is_below_one() -> None:
    planned = _cells(("python-docs", "docs-fetch"))
    tokens, scores = _c2_inputs(500, 1000)
    result = c2(tokens, scores, planned, model=MODEL, seed="7")
    assert result.passed is True and result.interval is not None
    assert result.interval.point == 0.5 and result.interval.high < 1.0
    tokens, scores = _c2_inputs(1000, 1000)
    assert c2(tokens, scores, planned, model=MODEL, seed="7").passed is False


def test_c2_counts_failures_tokens_with_score_zero() -> None:
    planned = _cells(("python-docs", "docs-fetch"))
    tokens, scores = _c2_inputs(500, 1000)
    failed = ("python-docs", MODEL, "Q0", 1)
    tokens[failed], scores[failed] = 9_000, 0.0
    result = c2(tokens, scores, planned, model=MODEL, seed="7")
    assert result.interval is not None
    # (19 * 500 + 9000) tokens over 19 correct, vs 1000 per correct
    assert result.interval.point == pytest.approx((19 * 500 + 9000) / 19 / 1000)


def test_c2_upper_bound_is_infinite_when_a_resample_has_no_correct_answer() -> None:
    planned = _cells(("python-docs", "docs-fetch"))
    tokens, scores = _c2_inputs(100, 1000, pd_score=0.0)
    for r in (1, 2):
        scores[("python-docs", MODEL, "Q3", r)] = 1.0
    result = c2(tokens, scores, planned, model=MODEL, seed="7")
    assert result.interval is not None and result.interval.high == float("inf")
    assert result.passed is False


def test_c2_is_not_evaluable_with_an_ungraded_or_uncounted_cell() -> None:
    planned = _cells(("python-docs", "docs-fetch"))
    tokens, scores = _c2_inputs(500, 1000)
    scores[("docs-fetch", MODEL, "Q1", 1)] = None
    result = c2(tokens, scores, planned, model=MODEL, seed="7")
    assert result.passed is None and "docs-fetch" in (result.reason or "")
    tokens, scores = _c2_inputs(500, 1000)
    tokens[("python-docs", MODEL, "Q2", 2)] = None
    assert c2(tokens, scores, planned, model=MODEL, seed="7").passed is None


@pytest.mark.parametrize(
    "missing_system,c2_evaluable,c3_evaluable",
    [("docs-fetch", False, True), ("no-docs", True, False), ("python-docs", False, False)],
)
def test_a_missing_planned_cell_affects_only_the_claims_that_compare_its_system(
    missing_system: str, c2_evaluable: bool, c3_evaluable: bool
) -> None:
    systems = ("python-docs", "docs-fetch", "no-docs")
    planned = _cells(systems) | _cells(systems, model="other")
    tokens: dict[CellKey, int | None] = {key: 100 for key in planned}
    scores: dict[CellKey, float | None] = {
        key: 1.0 if key[0] == "python-docs" else 0.5 for key in planned
    }
    missing = (missing_system, MODEL, "Q7", 2)
    del tokens[missing], scores[missing]
    c2_result = c2(tokens, scores, planned, model=MODEL, seed="7")
    c3_result = c3(scores, planned, model=MODEL, seed="7", offline_ok=True)
    assert (c2_result.passed is not None) is c2_evaluable
    assert (c3_result.passed is not None) is c3_evaluable
    for result, evaluable in ((c2_result, c2_evaluable), (c3_result, c3_evaluable)):
        if not evaluable:
            assert f"{missing_system}/{MODEL}/Q7/r2" in (result.reason or "")
    assert c2(tokens, scores, planned, model="other", seed="7").passed is not None
    assert c3(scores, planned, model="other", seed="7", offline_ok=True).passed is not None


def test_c2_never_takes_no_docs_as_a_peer() -> None:
    with pytest.raises(ValueError, match="no-docs"):
        c2({}, {}, set(), model=MODEL, seed="7", peer="no-docs")


def _c3_scores(pd: float, nd: float, model: str = "gemma4-12b") -> dict[CellKey, float | None]:
    return {
        key: pd if key[0] == "python-docs" else nd
        for key in _cells(("python-docs", "no-docs"), model=model)
    }


SCORES_WITH_DOCS_BETTER = _c3_scores(1.0, 0.5)
PLANNED = _cells(("python-docs", "no-docs"), model="gemma4-12b")


def test_c3_passes_only_with_offline_evidence_and_a_positive_lower_bound() -> None:
    result = c3(SCORES_WITH_DOCS_BETTER, PLANNED, model="gemma4-12b", seed="7", offline_ok=True)
    assert result.passed is True and result.interval is not None and result.interval.low > 0
    assert (
        c3(SCORES_WITH_DOCS_BETTER, PLANNED, model="gemma4-12b", seed="7", offline_ok=False).passed
        is False
    )
    assert (
        c3(SCORES_WITH_DOCS_BETTER, PLANNED, model="gemma4-12b", seed="7", offline_ok=None).passed
        is None
    )
    assert (
        c3(_c3_scores(0.5, 0.5), PLANNED, model="gemma4-12b", seed="7", offline_ok=True).passed
        is False
    )


def _egress(jobs: list[str], *, allowed: list[str], denied: list[str]) -> dict[str, Any]:
    return {
        "jobs": {
            job: {
                "hosts_allowed": allowed,
                "hosts_denied": denied,
                "lookup_package_docs_attempts": 0,
            }
            for job in jobs
        },
        "isolation_probe": "pass",
    }


def test_c3_rejects_all_passed_with_internet_hosts() -> None:
    ev = OfflineEvidence(all_passed=True, checks=EXPECTED, internet_hosts=["pypi.org:443"])
    assert offline_verdict(ev, CLEAN_EGRESS, expected_checks=EXPECTED, airgap_jobs=JOBS).ok is False


def test_c3_needs_full_check_coverage_and_proxy_evidence() -> None:
    ok = OfflineEvidence(all_passed=True, checks=EXPECTED, internet_hosts=[])
    assert offline_verdict(ok, CLEAN_EGRESS, expected_checks=EXPECTED, airgap_jobs=JOBS).ok is True
    short = OfflineEvidence(all_passed=True, checks=EXPECTED - 1, internet_hosts=[])
    assert (
        offline_verdict(short, CLEAN_EGRESS, expected_checks=EXPECTED, airgap_jobs=JOBS).ok is None
    )
    assert offline_verdict(ok, None, expected_checks=EXPECTED, airgap_jobs=JOBS).ok is None
    assert (
        offline_verdict(None, CLEAN_EGRESS, expected_checks=EXPECTED, airgap_jobs=JOBS).ok is None
    )
    leaked = _egress(JOBS, allowed=["host.docker.internal:11434", "pypi.org:443"], denied=[])
    assert offline_verdict(ok, leaked, expected_checks=EXPECTED, airgap_jobs=JOBS).ok is False
    failed = OfflineEvidence(all_passed=False, checks=EXPECTED, internet_hosts=[])
    assert (
        offline_verdict(failed, CLEAN_EGRESS, expected_checks=EXPECTED, airgap_jobs=JOBS).ok
        is False
    )
    one_job = {"jobs": {JOBS[0]: CLEAN_EGRESS["jobs"][JOBS[0]]}, "isolation_probe": "pass"}
    assert offline_verdict(ok, one_job, expected_checks=EXPECTED, airgap_jobs=JOBS).ok is None


def test_a_denied_pypi_attempt_leaves_c3_evaluable_and_passing() -> None:
    ev = OfflineEvidence(all_passed=True, checks=EXPECTED, internet_hosts=[])
    egress = _egress(JOBS[:1], allowed=["host.docker.internal:11434"], denied=["pypi.org:443"])
    egress["jobs"][JOBS[1]] = CLEAN_EGRESS["jobs"][JOBS[1]]
    v = offline_verdict(ev, egress, expected_checks=EXPECTED, airgap_jobs=JOBS)
    assert v.ok is True and v.denied_attempts == [{"job_id": JOBS[0], "host": "pypi.org:443"}]
    assert (
        c3(SCORES_WITH_DOCS_BETTER, PLANNED, model="gemma4-12b", seed="7", offline_ok=v.ok).passed
        is True
    )


def _probes(n: int = 20) -> list[Probe]:
    probes = []
    for t in PROBE_TYPES:
        for i in range(n):
            score = 0.0 if t in ("FACT", "INJ", "IDK") else 1.0
            built = (
                {"code": True} if i % 2 else {"generator": "sol", "checker": "gemini", "round": 1}
            )
            probes.append(
                Probe(
                    f"{t}--Q{i:02d}",
                    t,
                    f"Q{i:02d}",
                    "held_out",
                    "x",
                    score,
                    "INCORRECT" if score == 0 else "CORRECT",
                    built,
                )
            )
    return probes


def _scores(probes: list[Probe], wrong: set[str] = frozenset()) -> dict[str, float | None]:
    return {p.id: (1.0 - p.expected_score if p.id in wrong else p.expected_score) for p in probes}


def test_g1_thresholds_and_injection_credit() -> None:
    probes = _probes()  # 280 probes, 20 per type
    judges = {"haiku": _scores(probes), "luna": _scores(probes), "flash-lite": _scores(probes)}
    wrong = {f"{t}--Q00" for t in PROBE_TYPES[:13]}  # 13 misses: 267/280 = 0.954
    result = g1(probes, _scores(probes, wrong), judges)
    assert result.passed is True and result.details["overall"]["accuracy"] > 0.95
    assert result.details["by_type"]["KEY"]["wilson"][0] < 0.95
    three = {f"KEY--Q{i:02d}" for i in range(3)}  # KEY at 17/20 = 0.85
    assert g1(probes, _scores(probes, three), judges).passed is False
    weak = dict(judges, luna=_scores(probes, {p.id for p in probes[:29]}))  # 251/280 = 0.896
    assert g1(probes, _scores(probes), weak).passed is False
    inj = {
        p.id: (0.5 if p.id == "INJ--Q05" else s)
        for p, s in zip(probes, _scores(probes).values(), strict=True)
    }
    assert g1(probes, inj, judges).passed is False
    inj_judge = dict(judges, haiku=inj)
    result = g1(probes, _scores(probes), inj_judge)
    assert result.passed is False and result.details["by_judge"]["haiku"]["inj_credit"] == 1


def test_g1_counts_an_unresolved_probe_as_a_miss() -> None:
    probes = _probes()
    panel = _scores(probes)
    for i in range(15):
        panel[f"PARA--Q{i:02d}"] = None
    judges = {"haiku": _scores(probes)}
    result = g1(probes, panel, judges)
    assert result.passed is False and result.details["by_type"]["PARA"]["accuracy"] == 0.25
    misses = result.details["misses_by_generator"]
    assert sum(misses.values()) == 15 and set(misses) <= {"sol", "code"}


def test_reference_probe_check() -> None:
    probes = _probes(2)
    assert reference_probe_check(probes, _scores(probes)).passed is True
    assert reference_probe_check(probes, _scores(probes, {"KEY--Q00", "PARA--Q00"})).passed is False
    inj = {**_scores(probes), "INJ--Q01": 1.0}
    assert reference_probe_check(probes, inj).passed is False


INPUTS = {
    "judge_prompt": "a" * 64,
    "judge_schema": "b" * 64,
    "criteria": "c" * 64,
    "mapping_table": "d" * 64,
    "mask_rules": "e" * 64,
    "probes": "f" * 64,
}
COMPOSITION = {
    "haiku": {"model_id": "claude-haiku-5-5", "effort": "low"},
    "luna": {"model_id": "gpt-6-luna", "effort": "none"},
    "flash-lite": {"model_id": "gemini-3.1-flash-lite", "effort": "minimal"},
}


def _entry(composition: dict[str, Any], passed: bool = True, **inputs: str) -> dict[str, Any]:
    return {
        "binding": {"composition": composition, "inputs": {**INPUTS, **inputs}},
        "gate": {"passed": passed, "details": {"overall": {"accuracy": 0.97}}},
        "reference_check": {"passed": True, "details": {}},
        "dropped": {},
        "tuning": [],
    }


def test_g1_evidence_must_match_the_runs_binding() -> None:
    binding = G1Binding(composition=COMPOSITION, inputs=INPUTS)
    key = "haiku+luna+flash-lite"
    assert select_g1({key: _entry(COMPOSITION)}, binding).passed is True
    effort = {**COMPOSITION, "luna": {"model_id": "gpt-6-luna", "effort": "low"}}
    with pytest.raises(BenchmarkValidationError, match="luna.effort"):
        select_g1({key: _entry(effort)}, binding)
    for field in INPUTS:
        with pytest.raises(BenchmarkValidationError, match=field):
            select_g1({key: _entry(COMPOSITION, **{field: "other"})}, binding)


def test_g1_holds_one_entry_per_composition() -> None:
    fallback = {
        **{k: v for k, v in COMPOSITION.items() if k != "flash-lite"},
        "flash": {"model_id": "gemini-3.8-flash", "effort": "low"},
    }
    entries = {
        "haiku+luna+flash-lite": _entry(COMPOSITION, passed=False),
        "haiku+luna+flash": _entry(fallback, passed=True),
    }
    result = select_g1(entries, G1Binding(composition=fallback, inputs=INPUTS))
    assert result.passed is True and result.details["composition"] == "haiku+luna+flash"
    assert select_g1(entries, G1Binding(composition=COMPOSITION, inputs=INPUTS)).passed is False
    alone = {"haiku+luna+flash-lite": entries["haiku+luna+flash-lite"]}
    assert select_g1(alone, G1Binding(composition=fallback, inputs=INPUTS)).passed is None


def test_run_binding_reads_inputs_and_the_answers_runs(tmp_path: Path) -> None:
    grading = tmp_path / "grading"
    (grading / "inputs").mkdir(parents=True)
    (grading / "inputs" / "inputs.json").write_text(json.dumps({**INPUTS, "panel": "x"}))
    (grading / "runs").mkdir()
    run = {"target": "answers", "pass": 1, "composition": COMPOSITION}
    (grading / "runs" / "g1.json").write_text(json.dumps(run))
    (grading / "runs" / "g2.json").write_text(
        json.dumps({**run, "pass": 2, "composition": {"x": {}}})
    )
    binding = run_binding(tmp_path)
    assert binding.composition == COMPOSITION and binding.inputs == INPUTS
    (grading / "runs" / "g3.json").write_text(json.dumps({**run, "composition": {"y": {}}}))
    with pytest.raises(BenchmarkValidationError, match="composition"):
        run_binding(tmp_path)


def _grades(
    n_questions: int, *, noise: dict[str, float] | None = None
) -> tuple[dict[str, dict[str, float | None]], dict[str, str]]:
    grades: dict[str, dict[str, float | None]] = {}
    question_of: dict[str, str] = {}
    for q in range(n_questions):
        for k in range(3):
            item = f"Q{q}#{k}"
            base = (0.0, 0.5, 1.0)[(q + k) % 3]
            grades[item] = {j: base for j in ("a", "b", "c")}
            for judge, flip in (noise or {}).items():
                if (q * 3 + k) % int(1 / flip) == 0:
                    grades[item][judge] = 1.0 - base
            question_of[item] = f"Q{q}"
    return grades, question_of


def test_g2_and_g3_thresholds() -> None:
    grades, question_of = _grades(50)
    assert g2(grades, question_of, seed="7").passed is True
    noisy, _ = _grades(50, noise={"a": 0.5, "b": 0.5})
    assert g2(noisy, question_of, seed="7").passed is False
    ids = sorted(grades)[:300] if len(grades) >= 300 else sorted(grades)
    pairs = {i: (grades[i]["a"] or 0.0, grades[i]["b"] or 0.0) for i in ids}
    ok = GateResult("reference_probe_check", True, {})
    assert g3(ids, pairs, question_of, seed="7", reference_check=ok, size=len(ids)).passed


def test_g3_is_not_evaluable_with_a_shortfall_or_a_withheld_reference_vote() -> None:
    grades, question_of = _grades(100)
    ids = sorted(grades)[:300]
    pairs = {i: (grades[i]["a"] or 0.0, grades[i]["a"] or 0.0) for i in ids}
    ok = GateResult("reference_probe_check", True, {})
    assert g3(ids[:299], pairs, question_of, seed="7", reference_check=ok).passed is None
    duplicate = g3([*ids[:-1], ids[0]], pairs, question_of, seed="7", reference_check=ok)
    assert duplicate.passed is None and "299 distinct" in duplicate.details["reason"]
    withheld = dict(pairs)
    del withheld[ids[5]]
    result = g3(ids, withheld, question_of, seed="7", reference_check=ok)
    assert result.passed is None and result.details["missing_pairs"] == [ids[5]]
    assert g3(ids, pairs, question_of, seed="7", reference_check=ok).passed is True


def test_g3_is_not_evaluable_when_the_reference_failed_its_probes() -> None:
    grades, question_of = _grades(100)
    ids = sorted(grades)[:300]
    pairs = {i: (1.0, 1.0) for i in ids}
    failed = GateResult("reference_probe_check", False, {})
    assert g3(ids, pairs, question_of, seed="7", reference_check=failed).passed is None
    missing = GateResult("reference_probe_check", None, {})
    assert g3(ids, pairs, question_of, seed="7", reference_check=missing).passed is None


def test_g4_uses_the_reference_grade_for_suspects() -> None:
    # Single-judge claims are computed from judge_grade, which returns the reference (final)
    # grade for a suspect answer; a judge that agrees with the panel elsewhere does not flip.
    verdicts = {"panel": {"C2/m": True}, "haiku": {"C2/m": True}, "luna": {"C2/m": True}}
    assert g4(verdicts).passed is True


def test_g4_flags_judge_sensitive_claims() -> None:
    verdicts = {
        "panel": {"C2/m1": True, "C2/m2": False, "C3/g": None},
        "haiku": {"C2/m1": True, "C2/m2": False, "C3/g": True},
        "luna": {"C2/m1": False, "C2/m2": False, "C3/g": False},
    }
    result = g4(verdicts)
    assert result.passed is False and result.details["judge_sensitive"] == ["C2/m1"]
    assert result.details["flips"] == {"luna": ["C2/m1"]}
    assert g4({"panel": {"C3/g": None}, "haiku": {"C3/g": True}}).passed is None


def _gate(passed: bool | None, alpha: float | None = None) -> GateResult:
    return GateResult("G", passed, {} if alpha is None else {"alpha": alpha})


@pytest.mark.parametrize(
    "gates,tier",
    [
        ((_gate(True), _gate(True, 0.9), _gate(True, 0.9), _gate(True)), "Validated"),
        ((_gate(True), _gate(False, 0.7), _gate(True, 0.85), _gate(True)), "Tentative"),
        ((_gate(True), _gate(False, 0.6), _gate(True, 0.85), _gate(True)), "Judge not validated"),
        ((_gate(True), _gate(True, 0.9), _gate(None), _gate(True)), "Judge not validated"),
        ((_gate(False), _gate(True, 0.9), _gate(True, 0.9), _gate(True)), "Judge not validated"),
        ((_gate(True), _gate(True, 0.9), _gate(True, 0.9), _gate(False)), "Judge not validated"),
    ],
)
def test_outcome_tiers(gates: tuple[GateResult, ...], tier: str) -> None:
    assert outcome_tier(*gates) == tier


def _candidates() -> list[SampleCandidate]:
    cands = []
    for model in ("m1", "m2"):
        for system in ("python-docs", "no-docs"):
            for category, n in (("exact_symbol", 90), ("concept", 60), ("applied", 30)):
                cands += [
                    SampleCandidate(f"{model}-{system}-{category}-{i}", model, system, category)
                    for i in range(n)
                ]
    return cands


def test_g3_sample_is_proportional_stratified_and_skips_settled() -> None:
    cands = _candidates()  # 720 answers in 12 strata
    ids, info = g3_sample(cands, excluded={"m1-python-docs-exact_symbol-0"}, seed="7", size=300)
    assert len(ids) == 300 and "m1-python-docs-exact_symbol-0" not in ids
    assert info["shortfall"] == {} and info["borrowed"] == {}
    assert (
        ids == g3_sample(cands, excluded={"m1-python-docs-exact_symbol-0"}, seed="7", size=300)[0]
    )
    assert sum(i.startswith("m1-python-docs-exact_symbol") for i in ids) in (37, 38)


def test_g3_sample_borrows_within_the_same_model_and_system_only() -> None:
    cands = _candidates()
    excluded = {f"m1-python-docs-applied-{i}" for i in range(1, 30)}  # 1 eligible of 30
    ids, info = g3_sample(cands, excluded=excluded, seed="7", size=300)
    assert len(ids) == 300 and info["shortfall"] == {}
    borrowed = info["borrowed"]["m1/python-docs"]
    assert sum(borrowed.values()) == 12 and set(borrowed) <= {"concept", "exact_symbol"}
    group = {
        f"m2-no-docs-{c}-{i}"
        for c, n in (("exact_symbol", 90), ("concept", 60), ("applied", 30))
        for i in range(n - 5)
    }
    ids, info = g3_sample(cands, excluded=group, seed="7", size=300)
    assert len(ids) < 300 and info["shortfall"] == {"m2/no-docs": 74 - 15}
    assert not any(i.startswith("m2-no-docs") and i in group for i in ids)
    result = g3(ids, {}, {}, seed="7", reference_check=GateResult("r", True, {}), sample_info=info)
    assert result.passed is None
    assert result.details["reason"] == (
        f"G3 was not evaluated: the reference sample holds {len(ids)} of 300 answers, "
        "because m2/no-docs (15 of 74) have too few eligible answers."
    )


def test_self_preference_cancels_general_leniency() -> None:
    grades: dict[str, dict[str, float | None]] = {}
    family_of: dict[str, str] = {}
    question_of: dict[str, str] = {}
    for q in range(20):
        for fam in ("anthropic", "openai", "google"):
            item = f"{fam}-{q}"
            base = (0.0, 0.5)[q % 2]
            grades[item] = {"haiku": base + 0.5, "luna": base, "flash-lite": base}
            family_of[item], question_of[item] = fam, f"Q{q}"
    judge_family = {"haiku": "anthropic", "luna": "openai", "flash-lite": "google"}
    spi = self_preference(grades, family_of, question_of, judge_family, seed="7")
    interval = spi[("haiku", "anthropic")]
    assert interval.low <= 0.0 <= interval.high and interval.point == 0.0


def test_test_retest_and_length_correlation() -> None:
    grades, question_of = _grades(20)
    retest = retest_alpha(grades, grades, question_of, seed="7")
    assert retest["a"]["alpha"] == 1.0 and retest["a"]["items"] == 60
    assert length_grade_correlation([1, 2, 3], [0.0, 0.5, 1.0]) == pytest.approx(1.0)
    assert length_grade_correlation([1, 2], [1.0, 1.0]) is None
    assert length_grade_correlation([1], [1.0]) is None


def test_evidence_loaders_fail_closed(tmp_path: Path) -> None:
    assert load_egress_evidence(tmp_path) is None and load_offline_evidence(tmp_path) is None
    (tmp_path / "egress").mkdir()
    (tmp_path / "airgap").mkdir()
    for bad in (
        "{",
        "[]",
        json.dumps({"jobs": []}),
        json.dumps(
            {"jobs": {"j": {"hosts_allowed": "x", "hosts_denied": []}}, "isolation_probe": "pass"}
        ),
    ):
        (tmp_path / "egress" / "summary.json").write_text(bad)
        assert load_egress_evidence(tmp_path) is None
    (tmp_path / "egress" / "summary.json").write_text(json.dumps(CLEAN_EGRESS))
    assert load_egress_evidence(tmp_path) == CLEAN_EGRESS
    for bad in (
        "{",
        json.dumps({"all_passed": "yes", "checks": 3, "internet_hosts": []}),
        json.dumps({"all_passed": True, "checks": True, "internet_hosts": []}),
        json.dumps({"all_passed": True, "checks": 3, "internet_hosts": [1]}),
    ):
        (tmp_path / "airgap" / "checks.json").write_text(bad)
        assert load_offline_evidence(tmp_path) is None
    (tmp_path / "airgap" / "checks.json").write_text(
        json.dumps({"all_passed": True, "checks": 3, "internet_hosts": []})
    )
    assert load_offline_evidence(tmp_path) == OfflineEvidence(True, 3, [])
