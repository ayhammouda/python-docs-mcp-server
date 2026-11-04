"""One read-only G1 replay for scoring imports and bounded report exports."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from benchmarks.agentic.inputs import CapturedInput
from benchmarks.agentic.protocol import MAX_FRAME_BYTES, KeyLeakError, assert_no_key_shaped_json
from benchmarks.grading.calibration_history import (
    CalibrationSource,
    MissingCalibrationInput,
    held_out_scores,
    repeat_problems,
    tuning_summary,
)
from benchmarks.grading.calibration_history import (
    judge_settings as judge_settings,
)
from benchmarks.grading.calibration_history import (
    probe_retest as probe_retest,
)
from benchmarks.grading.calibration_scan import scan_calibration_sources
from benchmarks.grading.calibration_storage import (
    MAX_CALIBRATION_BYTES,
    check_calibration_metadata,
    check_transport_structure,
)
from benchmarks.grading.gates import (
    BINDING_INPUTS,
    G1Binding,
    GateResult,
    g1,
    reference_probe_check,
    select_g1,
)
from benchmarks.grading.panel import composition_key, panel_from_mapping
from benchmarks.grading.probes import Probe, probe_file_from_inputs, reference_subset
from benchmarks.grading.vote_capture import VoteInventoryUnavailable
from benchmarks.runner import BenchmarkValidationError


def g1_entries(body: bytes, *, projection: bool = False) -> dict[str, Any]:
    """Parse original export bytes identically at import and report replay."""

    if len(body) > MAX_CALIBRATION_BYTES:
        raise BenchmarkValidationError("G1 evidence exceeds the artifact size limit")
    check_transport_structure(body)

    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        assert_no_key_shaped_json(values, where="G1 evidence")
        result = dict(values)
        if len(result) != len(values):
            raise BenchmarkValidationError("G1 evidence contains a duplicate JSON member")
        return result

    try:
        data = json.loads(body.decode("utf-8"), object_pairs_hook=pairs)
    except KeyLeakError:
        raise
    except (ValueError, RecursionError) as exc:
        raise BenchmarkValidationError("could not read G1 evidence as UTF-8 JSON") from exc
    if projection:
        data = {"entries": data}
    if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
        raise BenchmarkValidationError("G1 evidence needs an entries object")
    if len(body) > MAX_FRAME_BYTES:
        check_calibration_metadata(data["entries"])
    scan_calibration_sources(data)
    return data["entries"]


def _probe_scores(value: Any, probes: Sequence[Probe], where: str) -> dict[str, float | None]:
    """Require a pass-1 score (or unresolved None) for every retained frozen probe."""
    if not isinstance(value, dict) or value.keys() != {p.id for p in probes}:
        raise BenchmarkValidationError(
            f"{where} evidence must name every frozen probe exactly once"
        )
    if any(
        s is not None
        and (isinstance(s, bool) or not isinstance(s, (int, float)) or s not in (0, 0.5, 1))
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


def verified_tuning(
    evidence: Any,
    *,
    panel: CapturedInput,
    probes: CapturedInput,
    criteria: CapturedInput,
    judges: Sequence[str],
    inputs: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Recompute effort trials; an old summary alone cannot prove a trial occurred."""
    verified = []
    unknown = {}
    selected = panel_from_mapping(panel.mapping, Path("/calibration/panel.yml"))
    if not isinstance(evidence, list) or not evidence:
        return [], {"tuning": "original tuning attempts and inputs unavailable"}
    for record in evidence:
        restored = CalibrationSource.restore_optional(record.get("source")) if isinstance(
            record, dict
        ) else None
        if restored is None:
            unknown["tuning"] = "original tuning attempts and inputs unavailable (historic summary)"
            continue
        source, bodies = restored
        try:
            for name, frozen in (("probes", probes), ("criteria", criteria)):
                if bodies[name] != frozen.content:
                    raise BenchmarkValidationError("tuning source differs from frozen inputs")
            tuning = CapturedInput.from_bytes(bodies["tuning"], "tuning", "tuning")
            suite = probe_file_from_inputs(probes, criteria, tuning)
            before = CapturedInput.from_bytes(bodies["panel"], "panel", "panel")
            attempted_panel = panel_from_mapping(before.mapping, Path("/calibration/panel.yml"))
            manifest = record.get("inputs")
            if (
                not isinstance(manifest, dict)
                or any(
                    hashlib.sha256(bodies[name]).hexdigest() != digest
                    for name, digest in manifest.items()
                )
                or any(
                    hashlib.sha256(bodies[name]).hexdigest() != manifest.get(name)
                    or manifest.get(name) != inputs.get(name)
                    for name in BINDING_INPUTS
                )
                or record.get("prompt_sha256") != manifest.get("judge_prompt")
            ):
                raise BenchmarkValidationError("tuning source differs from its input manifest")
            actual = tuning_summary(
                source, attempted_panel, [p for p in suite.probes if p.set == "tuning"], bodies
            )
            pins = judge_settings(before, list(actual), before_effort_freeze=True)
            if not _same(record.get("judges"), actual) or record.get("judge_settings") != pins:
                raise BenchmarkValidationError("tuning trials differ from original attempts")
            trial_judges = [j for j, row in actual.items() if row["tried"]]
            expected = judge_settings(panel, trial_judges, before_effort_freeze=True)
            disclosed = {}
            for judge in trial_judges:
                row = actual[judge]
                if pins.get(judge) != expected[judge]:
                    raise BenchmarkValidationError("tuning trial differs from registered judge")
                if (judge in judges and row["chosen"] is not None
                    and row["chosen"] != selected.judges[judge].effort
                ):
                    raise BenchmarkValidationError(
                        "tuning chosen effort differs from frozen settings"
                    )
                disclosed[judge] = row
            verified.append({"judges": disclosed})
        except MissingCalibrationInput:
            # This tuning run cannot prove its trials. Its absent request has no
            # bearing on independently captured held-out or reference requests.
            unknown["tuning"] = "original tuning request document unavailable"
        except VoteInventoryUnavailable as exc:
            unknown["tuning"] = str(exc)
        except BenchmarkValidationError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise BenchmarkValidationError("incomplete tuning source proof") from exc
    covered = {j for record in verified for j, row in record["judges"].items()
               if j in judges and row["chosen"] == selected.judges[j].effort}
    if covered != set(judges):
        unknown["tuning"] = "original selected tuning attempts unavailable for " + ", ".join(
            sorted(set(judges) - covered)
        )
    return verified, unknown


def replay_g1(
    entries: Mapping[str, Any],
    binding: G1Binding,
    *,
    panel: CapturedInput,
    probes: CapturedInput,
    criteria: CapturedInput,
    tuning: CapturedInput | None = None,
    history_problems: Mapping[str, str] | None = None,
) -> tuple[GateResult, GateResult]:
    """Replay the selected raw score arrays; never open a path or trust aggregates.

    Reports supply only their current captured bytes. Scoring may also validate
    the tuning input, which is not needed to establish the held-out verdict.
    Missing legacy evidence remains readable but cannot establish PASS.
    """
    key = composition_key(list(binding.composition))
    history_problems = history_problems or {}
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
    if "judge_settings" not in bound:
        # Historical arrays still require valid scores, but cannot bind auxiliary trials
        # to the current runtime. Both headline verdicts stay unknown below.
        reason = "legacy G1 lacks frozen judge settings evidence"
        history_problems = {**history_problems, "tuning": reason, "held_out_test_retest": reason}
    elif bound["judge_settings"] != expected_settings:
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
        supported_tuning, unknown = (
            verified_tuning(
                entry.get("tuning"),
                panel=panel,
                probes=probes,
                criteria=criteria,
                judges=judges,
                inputs=binding.inputs,
            )
            if "tuning" not in history_problems
            else ([], {"tuning": history_problems["tuning"]})
        )
        repeats = {
            j: {
                "alpha": "unknown",
                "items": "unknown",
                "expected": len(held),
                "pass2_records": "unknown",
            }
            for j in judges
        }
        restored = (
            CalibrationSource.restore_optional(entry.get("source"))
            if "held_out_test_retest" not in history_problems else None
        )
        if restored is not None:
            source, bodies = restored
            try:
                for name in BINDING_INPUTS:
                    if hashlib.sha256(bodies[name]).hexdigest() != binding.inputs.get(name):
                        raise BenchmarkValidationError(
                            "calibration source differs from frozen inputs"
                        )
                source_panel_input = CapturedInput.from_bytes(bodies["panel"], "panel", "panel")
                if (
                    judge_settings(
                        source_panel_input,
                        judges,
                        reference=references[0] if check is not None else None,
                    )
                    != expected_settings
                ):
                    raise BenchmarkValidationError("calibration source differs from frozen judges")
                exported_tuning = CapturedInput.from_bytes(bodies["tuning"], "tuning", "tuning")
                probe_file_from_inputs(probes, criteria, exported_tuning)
                source_panel = panel_from_mapping(
                    source_panel_input.mapping, Path("/calibration/panel.yml")
                )
                if source.composition(source_panel) != binding.composition:
                    raise BenchmarkValidationError("calibration source composition differs")
                actual_scores, repeats, ref_scores = held_out_scores(
                    source,
                    source_panel,
                    held,
                    bodies,
                    seed=suite.header["corpus_sha256"],
                )
                unknown.update(repeat_problems(repeats))
                if not _same(scores, actual_scores):
                    raise BenchmarkValidationError(
                        "calibration scores differ from original attempts"
                    )
                if check is not None and not _same(reference.details.get("scores"), ref_scores):
                    raise BenchmarkValidationError("reference scores differ from original attempts")
                saved_repeats = selected.details.get("held_out_test_retest") or {}
                if not isinstance(saved_repeats, dict):
                    raise BenchmarkValidationError("invalid held-out repeat evidence")
                for judge, saved in saved_repeats.items():
                    if judge in repeats and repeats[judge].get("reason"):
                        continue  # Missing issued detail supplies no observation to compare.
                    if (
                        judge not in repeats
                        or not isinstance(saved, dict)
                        or not _same(saved.get("observations"), repeats[judge]["observations"])
                    ):
                        raise BenchmarkValidationError(
                            "held-out repeat differs from original attempts"
                        )
            except BenchmarkValidationError:
                raise
            except (KeyError, TypeError, ValueError, FileNotFoundError) as exc:
                raise BenchmarkValidationError("incomplete held-out source proof") from exc
        else:
            unknown["held_out_test_retest"] = history_problems.get("held_out_test_retest") or (
                "original held-out pass-1/pass-2 attempts and inputs unavailable (historic export)"
            )
        selected = GateResult(
            selected.id,
            selected.passed,
            {
                **actual.details,
                "composition": key,
                "held_out_test_retest": repeats,
                "calibration_unknown": unknown,
                **({"tuning": supported_tuning} if supported_tuning else {}),
            },
        )
    else:
        selected = GateResult(
            "G1",
            None,
            {"reason": "legacy G1 lacks underlying pass-1 score evidence"},
        )
    if "scores" in reference.details:
        subset = reference_subset(held, seed=suite.header["corpus_sha256"])
        actual = reference_probe_check(
            subset, _probe_scores(reference.details["scores"], subset, "G1 reference")
        )
        reference = _recomputed_gate(reference, actual)
    else:
        reference = GateResult(
            "reference_probe_check",
            None,
            {**reference.details, "reason": "reference lacks underlying score evidence"},
        )
    if "judge_settings" not in bound:
        reason = history_problems["held_out_test_retest"]
        selected = GateResult("G1", None, {**selected.details, "reason": reason})
        reference = GateResult(
            "reference_probe_check", None, {**reference.details, "reason": reason}
        )
    return selected, reference
