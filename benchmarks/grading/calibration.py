"""One read-only G1 replay for scoring imports and bounded report exports."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from benchmarks.agentic.inputs import CapturedInput
from benchmarks.agentic.protocol import scanned_json_bytes
from benchmarks.grading.gates import G1Binding, GateResult, g1, reference_probe_check, select_g1
from benchmarks.grading.panel import composition_key
from benchmarks.grading.probes import Probe, probe_file_from_inputs, reference_subset
from benchmarks.runner import BenchmarkValidationError


def judge_settings(
    panel_input: CapturedInput,
    judges: Sequence[str],
    *,
    reference: str | None = None,
    before_effort_freeze: bool = False,
) -> dict[str, str]:
    """Pin each participating judge's portable runtime settings independently."""
    pins = {}
    participants = [*judges, reference] if reference is not None else judges
    for judge in participants:
        settings = dict(panel_input.mapping["judges"][judge])
        if before_effort_freeze:
            settings.pop("effort", None)
        body = scanned_json_bytes(settings, where="grading judge settings", sort_keys=True)
        pins[judge] = hashlib.sha256(body).hexdigest()
    return pins


def _probe_scores(value: Any, probes: Sequence[Probe], where: str) -> dict[str, float | None]:
    """Require a pass-1 score (or unresolved None) for every retained frozen probe."""
    if not isinstance(value, dict) or value.keys() != {p.id for p in probes}:
        raise BenchmarkValidationError(
            f"{where} evidence must name every frozen probe exactly once"
        )
    if any(
        s is not None
        and (isinstance(s, bool) or not isinstance(s, (int, float)) or not 0 <= s <= 1)
        for s in value.values()
    ):
        raise BenchmarkValidationError(f"{where} evidence contains an invalid probe score")
    return value


def _same(saved: Any, actual: Any) -> bool:
    # JSON booleans must never impersonate a numeric accuracy, count or score.
    if isinstance(saved, bool) or isinstance(actual, bool):
        return type(saved) is type(actual) and saved == actual
    if isinstance(actual, dict):
        return (
            isinstance(saved, dict)
            and saved.keys() == actual.keys()
            and all(_same(saved[k], v) for k, v in actual.items())
        )
    if isinstance(actual, list):
        return (
            isinstance(saved, list)
            and len(saved) == len(actual)
            and all(_same(a, b) for a, b in zip(saved, actual, strict=True))
        )
    return saved == actual


def _recomputed_gate(saved: GateResult, actual: GateResult) -> GateResult:
    """Refuse contradictory annotations, retaining independent retest disclosure."""
    if (
        saved.passed is not actual.passed
        or any(not _same(saved.details.get(k), v) for k, v in actual.details.items())
        or (saved.details.get("reason") and not actual.details.get("reason"))
    ):
        raise BenchmarkValidationError(
            f"{saved.id} verdict or aggregate evidence differs from scores"
        )
    return GateResult(actual.id, actual.passed, {**saved.details, **actual.details})


def replay_g1(
    entries: Mapping[str, Any],
    binding: G1Binding,
    *,
    panel: CapturedInput,
    probes: CapturedInput,
    criteria: CapturedInput,
    tuning: CapturedInput | None = None,
) -> tuple[GateResult, GateResult]:
    """Replay the selected raw score arrays; never open a path or trust aggregates.

    Reports supply only their current captured bytes. Scoring may also validate
    the tuning input, which is not needed to establish the held-out verdict.
    Missing legacy evidence remains readable but cannot establish PASS.
    """
    key = composition_key(list(binding.composition))
    entry = entries.get(key)
    if entry is not None and not isinstance(entry, dict):
        raise BenchmarkValidationError("G1 entry must be an object")
    selected = select_g1(entries, binding)
    reference = GateResult("reference_probe_check", None, {})
    if entry is None:
        return selected, reference
    annotated_composition = entry["gate"]["details"].get("composition", key)
    if annotated_composition != key:
        raise BenchmarkValidationError("G1 annotated composition differs from the run")
    bound = entry["binding"]
    judges = list(binding.composition)
    check = entry.get("reference_check")
    if check is not None:
        if (
            not isinstance(check, dict)
            or not isinstance(check.get("details"), dict)
            or "passed" not in check
            or (check["passed"] is not None and type(check["passed"]) is not bool)
        ):
            raise BenchmarkValidationError("G1 reference check must contain gate details")
        reference = GateResult("reference_probe_check", check["passed"], dict(check["details"]))
    frozen_judges = panel.mapping.get("judges")
    if not isinstance(frozen_judges, dict) or any(
        not isinstance(spec, dict) for spec in frozen_judges.values()
    ):
        raise BenchmarkValidationError("G1 requires frozen judge settings")
    for judge, spec in binding.composition.items():
        frozen_spec = frozen_judges.get(judge, {})
        if any(spec.get(f) != frozen_spec.get(f) for f in ("model_id", "effort")):
            raise BenchmarkValidationError("G1 composition differs from the frozen run panel")
    references = [j for j, s in frozen_judges.items() if s.get("role") == "reference"]
    if check is not None and len(references) != 1:
        raise BenchmarkValidationError("G1 requires one frozen reference judge")
    expected_settings = judge_settings(
        panel, judges, reference=references[0] if check is not None else None
    )
    if "judge_settings" in bound and bound["judge_settings"] != expected_settings:
        raise BenchmarkValidationError("G1 judge settings differ from the frozen run panel")
    # The caller's binding is also checked against the exact bytes being replayed.
    for name, captured in (("probes", probes), ("criteria", criteria)):
        if binding.inputs.get(name) != hashlib.sha256(captured.content).hexdigest():
            raise BenchmarkValidationError(f"G1 captured {name} differs from the run binding")
    suite = probe_file_from_inputs(probes, criteria, tuning)
    if suite.header.get("corpus_sha256") != criteria.mapping.get("corpus_sha256"):
        raise BenchmarkValidationError("G1 probe corpus differs from the frozen criteria")
    held = [p for p in suite.probes if p.set == "held_out"]
    if "scores" in entry:
        scores = entry["scores"]
        if (
            not isinstance(scores, dict)
            or not isinstance(scores.get("judges"), dict)
            or scores["judges"].keys() != binding.composition.keys()
        ):
            raise BenchmarkValidationError("G1 score evidence needs every registered judge")
        actual = g1(
            held,
            _probe_scores(scores.get("panel"), held, "G1 panel"),
            {j: _probe_scores(s, held, f"G1 judge {j}") for j, s in scores["judges"].items()},
            dropped=[d for d in suite.dropped if d["set"] == "held_out"],
        )
        selected = _recomputed_gate(selected, actual)
    else:
        selected = GateResult(
            "G1", None,
            {**selected.details, "reason": "legacy G1 lacks underlying pass-1 score evidence"},
        )
    if "scores" in reference.details:
        subset = reference_subset(held, seed=suite.header["corpus_sha256"])
        actual = reference_probe_check(
            subset, _probe_scores(reference.details["scores"], subset, "G1 reference")
        )
        reference = _recomputed_gate(reference, actual)
    else:
        reference = GateResult(
            "reference_probe_check", None,
            {**reference.details, "reason": "reference lacks underlying score evidence"},
        )
    if "judge_settings" not in bound:
        reason = "legacy G1 lacks frozen judge settings evidence"
        selected = GateResult("G1", None, {**selected.details, "reason": reason})
        reference = GateResult(
            "reference_probe_check", None, {**reference.details, "reason": reason}
        )
    return selected, reference
