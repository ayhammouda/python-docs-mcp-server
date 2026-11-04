"""Captured calibration attempts shared by workspace producers and portable replay.

The archive is unsigned. It proves internal continuity, never hosted execution.
Portable replay reads only the supplied mapping and never follows recorded paths.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, OrderedDict
from collections.abc import Iterator, Mapping, Sequence
from functools import cached_property
from pathlib import Path
from typing import Any

from benchmarks.agentic.inputs import CapturedInput, json_mapping
from benchmarks.agentic.protocol import MAX_FRAME_BYTES, assert_no_key_shaped, scanned_json_bytes
from benchmarks.grading.aggregate import recorded_result, select_vote_file, vote_from_record
from benchmarks.grading.calibration_scan import scan_calibration_source
from benchmarks.grading.calibration_storage import (
    MAX_SOURCE_BYTES,
    MAX_SOURCE_FILES,
    PACK_SOURCE_BYTES,
    pack_files,
    source_files,
)
from benchmarks.grading.gates import G1_PER_JUDGE, checked_composition, test_retest
from benchmarks.grading.inputs import (
    REPO_ROOT,
    SNAPSHOT_FILES,
    JudgeInput,
    criteria_from_mapping,
    frozen_judge_document,
    render_judge_input,
    safe_id,
    tuning_questions_from_mapping,
)
from benchmarks.grading.mapping import Vote, grade, panel_fields
from benchmarks.grading.panel import JudgeSpec, Panel
from benchmarks.grading.probes import Probe, reference_subset
from benchmarks.grading.vote_capture import VoteCapture, VoteInventoryUnavailable
from benchmarks.report_files import artifact_bytes, artifact_paths
from benchmarks.runner import BenchmarkValidationError


class MissingCalibrationInput(VoteInventoryUnavailable):
    """An issued vote's request is absent, rather than contradictory."""


class _Invocations(Mapping[str, dict[str, Any]]):
    """One frozen path listing, with bounded decoding shared by every calibration consumer."""

    def __init__(self, source: CalibrationSource):
        self.source = source
        self.paths = {p.stem: p for p in source.json_paths(source.root / "runs")}

    def __getitem__(self, run_id: str) -> dict[str, Any]:
        return self.source.invocation(self.paths[run_id])

    def __iter__(self) -> Iterator[str]:
        return iter(self.paths)

    def __len__(self) -> int:
        return len(self.paths)


class CalibrationSource:
    """Capture mutable bytes once, including absent files and candidate listings."""

    def __init__(self, root: Path, files: Mapping[str, str] | None = None):
        self.root = root
        self.portable = files is not None
        self.files = dict(files) if isinstance(files, dict) else files if files is not None else {}
        self.absent: set[str] = set()
        self.listings: dict[Path, list[Path]] = {}
        self.invocations: OrderedDict[Path, tuple[int, dict[str, Any]]] = OrderedDict()
        self.invocation_bytes = 0
        self.size = 0
        if len(self.files) > MAX_SOURCE_FILES:
            raise BenchmarkValidationError("calibration source exceeds the file limit")
        for name, body in self.files.items():
            if (
                not isinstance(name, str)
                or not isinstance(body, str)
                or Path(name).is_absolute()
                or any(p.startswith(".") for p in Path(name).parts)
                or str(Path(name)) != name
                or len(body.encode()) > MAX_FRAME_BYTES
            ):
                raise BenchmarkValidationError("invalid portable calibration file")
            self.size += len(body.encode())
            assert_no_key_shaped(name, where="calibration source path")
            if name.endswith(".json"):
                json_mapping(body.encode(), "calibration source")
                path = root / name
                self.listings.setdefault(path.parent, []).append(path)
            else:
                assert_no_key_shaped(body, where="calibration source")
        for paths in self.listings.values():
            paths.sort()
        if self.size > MAX_SOURCE_BYTES:
            raise BenchmarkValidationError("calibration source exceeds the size limit")

    def read(self, root: Path, path: Path) -> bytes:
        if root != self.root or not path.is_relative_to(root):
            raise BenchmarkValidationError("foreign calibration source path")
        name = str(path.relative_to(root))
        if name not in self.files:
            if self.portable or name in self.absent:
                raise FileNotFoundError(name)
            try:
                body = artifact_bytes(root, path)
            except FileNotFoundError:
                self.absent.add(name)
                raise
            if (len(body) > MAX_FRAME_BYTES or self.size + len(body) > MAX_SOURCE_BYTES
                    or len(self.files) >= MAX_SOURCE_FILES):
                raise BenchmarkValidationError("calibration source exceeds the size limit")
            try:
                assert isinstance(self.files, dict)
                self.files[name] = body.decode("utf-8")
                self.size += len(body)
            except UnicodeDecodeError:
                raise BenchmarkValidationError("calibration source is not UTF-8") from None
        return self.files[name].encode("utf-8")

    def json_paths(self, base: Path) -> list[Path]:
        if not self.portable and base not in self.listings:
            self.listings[base] = artifact_paths(self.root, base, "*.json")
        return self.listings.get(base, [])

    def paths(self, root: Path, base: Path, pattern: str) -> list[Path]:
        """List each inventory scope once; portable lookup never opens a path."""
        if root != self.root or not base.is_relative_to(root):
            raise BenchmarkValidationError("foreign calibration inventory path")
        if pattern == "*.json":
            return self.json_paths(base)
        if not self.portable:
            return artifact_paths(root, base, pattern)
        depth = len(Path(pattern).parts)
        return [
            path for parent, paths in self.listings.items() if parent.is_relative_to(base)
            for path in paths
            if len(path.relative_to(base).parts) == depth
            and path.relative_to(base).match(pattern)
        ]

    @cached_property
    def inventory(self) -> VoteCapture:
        return VoteCapture(self.root, reader=self.read, paths=self.paths, runs=_Invocations(self))

    def composition(self, panel: Panel) -> dict[str, dict[str, str | None]]:
        compositions = []
        for run in self.inventory.runs.values():
            if run.get("target") == "probes" and run.get("pass") == 1:
                if type(run["pass"]) is not int or not isinstance(run.get("composition"), dict):
                    raise BenchmarkValidationError("invalid calibration composition")
                compositions.append(run["composition"])
        return checked_composition(compositions, panel, where="calibration pass-1 probes")

    def invocation(self, path: Path) -> dict[str, Any]:
        """Parse captured receipt inventories once, with a frame-bounded LRU.

        A manifest can name every result in an invocation. Reparsing it for each
        selected result makes a complete calibration quadratic in cohort size.
        Eviction only changes work: the underlying captured bytes stay fixed.
        """
        if path in self.invocations:
            self.invocations.move_to_end(path)
            return self.invocations[path][1]
        body = self.read(self.root, path)
        record = json_mapping(body, "calibration invocation")
        while self.invocations and self.invocation_bytes + len(body) > MAX_FRAME_BYTES:
            _, (size, _) = self.invocations.popitem(last=False)
            self.invocation_bytes -= size
        self.invocations[path] = len(body), record
        self.invocation_bytes += len(body)
        return record

    def export(self, inputs: Mapping[str, bytes]) -> dict[str, Any]:
        files = {k: v for k, v in self.files.items() if not k.startswith("inputs/")}
        stored = (
            pack_files(files) if self.size > PACK_SOURCE_BYTES else {"version": 1, "files": files}
        )
        proof = {
            **stored,
            "inputs": {k: v.decode("utf-8") for k, v in inputs.items()},
        }
        scan_calibration_source(proof)
        return proof

    @classmethod
    def restore(cls, proof: Any) -> tuple[CalibrationSource, dict[str, bytes]]:
        if (
            not isinstance(proof, dict)
            or type(proof.get("version")) is not int
            or proof["version"] not in (1, 2)
            or not isinstance(proof.get("inputs"), dict)
            or not isinstance(proof.get("files"), dict)
            or any(not isinstance(v, str) for v in proof["inputs"].values())
        ):
            raise BenchmarkValidationError("invalid calibration source proof")
        files = source_files(proof)
        if any(
            not isinstance(k, str)
            or k.split("/")[0] not in {"votes", "items", "runs", "executions"}
            for k in files
        ):
            raise BenchmarkValidationError("invalid calibration source file layout")
        for name, text in proof["inputs"].items():
            filename = SNAPSHOT_FILES.get(
                name, {"corpus": "corpus.yml", "tuning": "tuning.yml"}.get(name)
            )
            if filename is None:
                raise BenchmarkValidationError("unknown calibration source input")
            if Path(filename).suffix in (".yml", ".json"):
                CapturedInput.from_bytes(text.encode("utf-8"), name, "calibration source input")
            else:
                assert_no_key_shaped(text, where="calibration source input")
        scan_calibration_source(proof)
        return cls(Path("/calibration"), files), {
            k: v.encode("utf-8") for k, v in proof["inputs"].items()
        }


def judge_settings(
    panel_input: CapturedInput,
    judges: Sequence[str],
    *,
    reference: str | None = None,
    before_effort_freeze: bool = False,
) -> dict[str, str]:
    pins = {}
    for judge in [*judges, *([] if reference is None else [reference])]:
        settings = dict(panel_input.mapping["judges"][judge])
        if before_effort_freeze:
            settings.pop("effort", None)
        pins[judge] = hashlib.sha256(
            scanned_json_bytes(
                settings,
                where="grading judge settings",
                sort_keys=True,
            )
        ).hexdigest()
    return pins


def probe_retest(
    judge: str,
    observations: Mapping[str, dict[str, Any]],
    *,
    expected: int,
    seed: str,
) -> dict[str, Any]:
    paired = {k: r for k, r in observations.items() if None not in (r["pass1"], r["pass2"])}
    statistic = test_retest(
        {k: {judge: r["pass1"]} for k, r in paired.items()},
        {k: {judge: r["pass2"]} for k, r in paired.items()},
        {k: r["question_id"] for k, r in paired.items()},
        seed=seed,
    ).get(judge, {"alpha": None, "low": None, "high": None, "items": 0})
    return {
        **statistic,
        "expected": expected,
        "pass2_records": len(observations),
        "observations": observations,
    }


def probe_requests(
    probes: Sequence[Probe],
    inputs: Mapping[str, bytes],
    *,
    tuning: bool = False,
) -> tuple[dict[str, JudgeInput], dict[str, tuple[Any, Any]]]:
    system, schema = frozen_judge_document(inputs["judge_prompt"], inputs["judge_schema"])
    if tuning:
        questions = tuning_questions_from_mapping(
            CapturedInput.from_bytes(inputs["tuning"], "tuning", "tuning questions").mapping
        )
    else:
        questions = criteria_from_mapping(
            CapturedInput.from_bytes(inputs["criteria"], "criteria", "criteria").mapping,
            Path("/calibration/criteria.yml"),
            corpus_path=Path("/calibration/corpus.yml"),
            corpus_body=inputs["corpus"],
            schema_path=REPO_ROOT / "docs/benchmarks/corpus.schema.json",
        )
    requests = {}
    for probe in probes:
        if probe.question_id not in questions or probe.id != safe_id(probe.type, probe.question_id):
            raise BenchmarkValidationError("invalid grading probe")
        requests[probe.id] = render_judge_input(
            questions[probe.question_id],
            probe.text,
            system=system,
            schema=schema,
        )
    return requests, {qid: q.weights for qid, q in questions.items()}


def selected_record(
    source: CalibrationSource,
    *,
    target: str,
    judge: JudgeSpec,
    effort: str | None,
    pass_number: int,
    probe: Probe,
    request: JudgeInput,
    allow_unavailable: bool = False,
) -> dict[str, Any] | None:
    root = source.root
    candidates = source.inventory.records(
        target=target, judge_id=judge.id, effort=effort,
        pass_number=pass_number, item_id=probe.id,
        allow_unavailable=allow_unavailable,
    )
    selected = select_vote_file(candidates)
    if selected is None:
        return None
    record = selected[1]
    identity = {
        "target": target,
        "judge_id": judge.id,
        "model_id": judge.model_id,
        "effort": effort,
        "pass": pass_number,
        "item_id": probe.id,
        "input_sha256": request.sha256(),
    }
    if any(type(record.get(k)) is not type(v) or record.get(k) != v for k, v in identity.items()):
        raise BenchmarkValidationError(
            f"foreign calibration vote record for {target}/{judge.id}/{probe.id}"
        )
    try:
        document = json_mapping(
            source.read(root, root / "items" / target / f"{probe.id}.json"), "calibration input"
        )
    except FileNotFoundError:
        raise MissingCalibrationInput(
            "missing calibration input document; legacy records without request proof "
            "cannot establish a verdict"
        ) from None
    expected = request.item_evidence({"question_id": probe.question_id, "suspect": request.suspect})
    if json.dumps(document, sort_keys=True) != json.dumps(expected, sort_keys=True):
        raise BenchmarkValidationError("calibration input document differs")
    invocation = source.inventory.runs.get(record["grading_run_id"])
    if invocation and invocation.get("target") == target and "composition" in invocation:
        composition = invocation["composition"]
        if not isinstance(composition, dict) or composition.get(judge.id) != {
            "model_id": judge.model_id, "effort": effort,
        }:
            raise BenchmarkValidationError("calibration invocation composition differs from vote")
    if record.get("parsed") is not None:
        raise BenchmarkValidationError("unexpected parsed calibration reply")
    vote_from_record(
        record,
        request=request,
        result=recorded_result(
            root,
            record,
            judge,
            vote_sha256=selected[2],
            read=source.read,
            invocation=invocation,
            history=source.inventory.history(record),
        ),
    )
    return record


def probe_score(record: Mapping[str, Any] | None, weights: tuple[Any, Any]) -> float | None:
    if record is None or record.get("withheld") is not None or record.get("vote") is None:
        return None
    return grade(record["vote"], weights)[0]


def tuning_summary(
    source: CalibrationSource,
    panel: Panel,
    probes: Sequence[Probe],
    inputs: Mapping[str, bytes],
) -> dict[str, Any]:
    requests, weights = probe_requests(probes, inputs, tuning=True)
    judges = {}
    for judge in [*panel.panel, *panel.fallback.values()]:
        efforts = {}
        for effort in panel.judges[judge].effort_levels:
            records = {
                p.id: selected_record(
                    source,
                    target="tuning",
                    judge=panel.judges[judge],
                    effort=effort,
                    pass_number=1,
                    probe=p,
                    request=requests[p.id],
                )
                for p in probes
            }
            if not any(r is not None for r in records.values()):
                continue
            scores = {p.id: probe_score(records[p.id], weights[p.question_id]) for p in probes}
            hits = sum(scores[p.id] == p.expected_score for p in probes)
            inj = sum(p.type == "INJ" and (scores[p.id] or 0) > 0 for p in probes)
            accuracy = hits / len(probes) if probes else 0.0
            efforts[effort] = {
                "accuracy": accuracy,
                "n": len(probes),
                "inj_credit": inj,
                "meets": bool(probes) and accuracy >= G1_PER_JUDGE and inj == 0,
            }
        judges[judge] = {
            "efforts": efforts,
            "tried": list(efforts),
            "chosen": next((e for e in efforts if efforts[e]["meets"]), None),
        }
    return judges


def held_out_scores(
    source: CalibrationSource,
    panel: Panel,
    probes: Sequence[Probe],
    inputs: Mapping[str, bytes],
    *,
    seed: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    requests, weights = probe_requests(probes, inputs)
    composition = source.composition(panel)
    scores: dict[str, Any] = {"panel": {}, "judges": {j: {} for j in composition}}
    repeats: dict[str, Any] = {j: {} for j in composition}
    repeat_unavailable = {}
    try:
        source.inventory.check("probes", 2)
    except VoteInventoryUnavailable as exc:
        # Optional repeat loss cannot erase independently replayable pass-1 or
        # reference records. It also cannot prove that nothing was issued.
        repeat_unavailable = {
            j: str(exc) for j, spec in composition.items()
            if any(j == seat and effort in (None, spec["effort"]) and item in requests
                   for seat, effort, item in exc.missing)
        }
    repeat_counts = Counter(
        path.name.rpartition("--a")[0] for path in source.inventory.versions
        if path.is_relative_to(source.root / "votes/probes") and path.parts[-3] == "pass2"
    )
    for run, invocation in source.inventory.runs.items():
        if (invocation.get("target") == "probes" and invocation.get("pass") == 2
            and "composition" in invocation and invocation["composition"] != composition
        ):
            raise BenchmarkValidationError("calibration repeat invocation composition differs")
        count = invocation.get("records_written")
        if (invocation.get("target") == "probes" and invocation.get("pass") == 2
            and invocation.get("execution_results") is None
            and type(count) is int and count > repeat_counts[run]
        ):
            # A legacy count proves that something is missing, but cannot assign
            # it to a judge/probe without the original receipt allocation.
            for judge in composition:
                repeat_unavailable[judge] = "legacy repeat issuance count exceeds captured votes"
    for p in probes:
        votes = {}
        for j, spec in composition.items():
            first = selected_record(
                source,
                target="probes",
                judge=panel.judges[j],
                effort=spec["effort"],
                pass_number=1,
                probe=p,
                request=requests[p.id],
            )
            scores["judges"][j][p.id] = probe_score(first, weights[p.question_id])
            votes[j] = (
                Vote(**first["vote"])
                if first is not None
                and first.get("withheld") is None
                and first.get("vote") is not None
                else None
            )
            try:
                second = None if j in repeat_unavailable else selected_record(
                    source,
                    target="probes",
                    judge=panel.judges[j],
                    effort=spec["effort"],
                    pass_number=2,
                    probe=p,
                    request=requests[p.id],
                    allow_unavailable=True,
                )
            except VoteInventoryUnavailable as exc:
                repeat_unavailable[j], second = str(exc), None
            if second is not None:
                repeats[j][p.id] = {
                    "question_id": p.question_id,
                    "pass1": scores["judges"][j][p.id],
                    "pass2": probe_score(second, weights[p.question_id]),
                    "withheld": second["withheld"],
                    "input_sha256": requests[p.id].sha256(),
                    "pass1_record": None
                    if first is None
                    else {k: first[k] for k in ("grading_run_id", "attempt")},
                    "pass2_record": {k: second[k] for k in ("grading_run_id", "attempt")},
                }
        fields = panel_fields(votes)
        scores["panel"][p.id] = (
            None
            if fields.needs_reference
            else grade({f: bool(v) for f, v in fields.fields.items()}, weights[p.question_id])[0]
        )
    reference = panel.judges[panel.reference]
    ref_records = {
        p.id: selected_record(
            source,
            target="reference-probes",
            judge=reference,
            effort=reference.effort,
            pass_number=1,
            probe=p,
            request=requests[p.id],
        )
        for p in reference_subset(probes, seed=seed)
    }
    ref_scores = {
        p.id: probe_score(ref_records[p.id], weights[p.question_id])
        for p in probes
        if p.id in ref_records
    }
    return (
        scores,
        {
            j: (
                unavailable_retest(len(probes), repeat_unavailable[j]) if j in repeat_unavailable
                else probe_retest(j, rows, expected=len(probes), seed=seed)
            )
            for j, rows in repeats.items()
        },
        (ref_scores if any(r is not None for r in ref_records.values()) else {}),
    )


def unavailable_retest(expected: int, reason: str) -> dict[str, Any]:
    return {
        "alpha": "unknown", "low": "unknown", "high": "unknown", "items": "unknown",
        "expected": expected, "pass2_records": "unknown", "reason": reason,
    }


def repeat_problems(repeats: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    reasons = sorted({r["reason"] for r in repeats.values() if "reason" in r})
    return {"held_out_test_retest": "; ".join(reasons)} if reasons else {}
