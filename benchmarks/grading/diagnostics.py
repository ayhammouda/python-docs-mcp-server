"""Descriptive grading statistics for the scorer and the portable report."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from typing import Any

from benchmarks.grading.aggregate import LLM_PANEL, AnswerVotes, own_grade
from benchmarks.grading.gates import (
    alpha_by_group,
    length_grade_correlation,
    model_lineage,
    self_preference,
    test_retest,
)
from benchmarks.grading.mapping import grade, panel_fields
from benchmarks.grading.stats import GATE_RESAMPLES


def missing_records(
    runs: Sequence[Mapping[str, Any]],
    efforts: Mapping[str, str | None],
    items: Mapping[str, AnswerVotes],
) -> dict[str, dict[str, list[str]]]:
    """Group missing final records using the already captured invocation history."""
    # Invocation timestamps are written in UTC. Their one-second precision allows ties.
    history: dict[tuple[str, str], dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for run in runs:
        when = run.get("completed_at") or run.get("started_at") or ""
        for entry in run.get("not_run") or []:
            if entry.get("target") == "answers" and entry.get("pass") == 1:
                history[entry["judge_id"], entry["item_id"]][when].add(entry["reason"])
    result: dict[str, dict[str, list[str]]] = {}
    for judge_id in efforts:
        for item in sorted(items):
            if not items[item].records.get(judge_id, {}).get("final"):
                reasons = history.get((judge_id, item), {})
                latest = reasons.get(max(reasons, default=""), set()) | reasons.get("", set())
                reason = next(iter(latest), "no_record")
                if len(latest) > 1:
                    reason = f"chronology unknown ({', '.join(sorted(latest))})"
                result.setdefault(judge_id, {}).setdefault(reason, []).append(item)
    return result


def diagnostic_tables(
    answer_votes: Mapping[tuple[str, str], AnswerVotes],
    pass2: Mapping[tuple[str, str], AnswerVotes],
    finals: Mapping[tuple[str, str], dict[str, Any]],
    transcripts: Mapping[tuple[str, str], dict[str, Any]],
    scoring: Mapping[tuple[str, str], dict[str, Any]],
    questions: Mapping[str, Any],
    efforts: Mapping[str, str | None],
    providers: Mapping[str, str],
    families: Mapping[str, dict[str, Any]],
    sample_ids: Sequence[str],
    no_final_record: Mapping[str, Any],
    *,
    seed: str,
    resamples: int = GATE_RESAMPLES,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Canonical descriptive statistics over the admitted, captured scoring cohort."""
    by_item = {av.item_id: av for av in answer_votes.values()}
    question_of = {av.item_id: av.corpus_id for av in answer_votes.values()}
    family_of = {
        av.item_id: transcripts[k]["model_family_id"]
        for k, av in answer_votes.items()
        if k in transcripts
    }
    system_of = {
        av.item_id: transcripts[k]["system_id"]
        for k, av in answer_votes.items()
        if k in transcripts
    }
    weights = {qid: q.weights for qid, q in questions.items()}
    panel_votes = {
        av.item_id: {j: own_grade(av, j, weights[av.corpus_id]) for j in efforts}
        for k, av in answer_votes.items()
        if k in transcripts and not av.suspect
    }
    agreement = {
        i: vs for i, vs in panel_votes.items() if sum(v is not None for v in vs.values()) >= 2
    }
    own = {
        av.item_id: panel_votes[av.item_id]
        for k, av in answer_votes.items()
        if k in finals and av.item_id in panel_votes
    }
    pairs = {
        av.item_id: (finals[k]["score"], grade(av.reference.fields(), weights[av.corpus_id])[0])
        for k, av in answer_votes.items()
        if k in finals
        and finals[k]["method"] == LLM_PANEL
        and not av.suspect
        and av.reference is not None
    }
    in_sample = {i: pairs[i] for i in sample_ids if i in pairs}
    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    for final_score, reference_score in in_sample.values():
        confusion[str(final_score)][str(reference_score)] += 1
    group_key = {i: f"{family_of.get(i)}/{system_of.get(i)}" for i in by_item}
    own2 = {
        av.item_id: {j: own_grade(av, j, weights[av.corpus_id]) for j in efforts}
        for av in pass2.values()
        if any(v is not None for v in av.votes.values())
    }
    lineages = {fid: model_lineage(fid, f) for fid, f in families.items()}
    spi = self_preference(
        own,
        {i: lineage for i, fid in family_of.items() if (lineage := lineages.get(fid))},
        question_of,
        {j: providers[j] for j in efforts},
        seed=seed,
        resamples=resamples,
    )
    graded = [(k, f) for k, f in finals.items() if k in transcripts]
    labels: dict[str, Counter[str]] = defaultdict(Counter)
    grounded_cells: dict[str, list[bool]] = defaultdict(list)
    for k, record in scoring.items():
        if k not in transcripts:
            continue
        group = f"{transcripts[k]['model_family_id']}/{transcripts[k]['system_id']}"
        if record.get("label"):
            labels[group][record["label"]] += 1
        if record.get("score") is not None:
            grounded_cells[group].append(bool(record.get("grounded")))
    suspects: dict[str, dict[str, int]] = defaultdict(lambda: {"suspect": 0, "differs": 0})
    for k, final in finals.items():
        av = answer_votes[k]
        if av.suspect:
            group = group_key[av.item_id]
            suspects[group]["suspect"] += 1
            pf = panel_fields(av.votes)
            if (
                pf.needs_reference
                or grade({f: bool(v) for f, v in pf.fields.items()}, weights[av.corpus_id])[0]
                != final["score"]
            ):
                suspects[group]["differs"] += 1
    per_judge = {
        j: {
            "records": sum(1 for av in answer_votes.values() if j in av.records),
            "withheld": dict(
                Counter(
                    av.records[j]["withheld"]
                    for av in answer_votes.values()
                    if j in av.records and av.records[j]["withheld"] is not None
                )
            ),
        }
        for j in efforts
    }
    diagnostics = {
        "test_retest": test_retest(
            {i: own[i] for i in own if i in own2},
            own2,
            question_of,
            seed=seed,
            resamples=resamples,
        )
        if own2
        else None,
        "self_preference": {f"{j}/{f}": asdict(v) if v else None for (j, f), v in spi.items()},
        "length_grade_correlation": length_grade_correlation(
            [len(transcripts[k].get("answer") or "") for k, _ in graded],
            [f["score"] for _, f in graded],
        ),
        "label_shares": {
            g: {label: n / sum(c.values()) for label, n in sorted(c.items())}
            for g, c in sorted(labels.items())
        },
        "grounded_rate": {g: sum(v) / len(v) for g, v in sorted(grounded_cells.items()) if v},
        "suspects": dict(sorted(suspects.items())),
        "per_judge": per_judge,
        # shortcut: an item listed here blocks the reference target until it is fixed and
        # graded again (Task 8 refuses `reference` while any panel item lacks a final record).
        "no_final_record": no_final_record,
    }

    def breakdown(grades: Mapping[str, Any], of: Mapping[str, str]) -> dict[str, Any]:
        groups = alpha_by_group(
            grades, {i: of.get(i, "?") for i in grades}, question_of, seed=seed, resamples=resamples
        )
        return {g: asdict(r) for g, r in groups.items()}

    g3_grades = {i: {"final": p[0], "reference": p[1]} for i, p in in_sample.items()}
    breakdowns = {
        "G2": {
            "by_family": breakdown(agreement, family_of),
            "by_system": breakdown(agreement, system_of),
        },
        "G3": {
            "by_family": breakdown(g3_grades, family_of),
            "by_system": breakdown(g3_grades, system_of),
            "confusion": {k: dict(v) for k, v in sorted(confusion.items())},
        },
    }
    return diagnostics, breakdowns
