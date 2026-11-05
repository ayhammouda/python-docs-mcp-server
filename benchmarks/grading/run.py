"""Plan and run immutable, version-bound judge batches through the agentic host."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import sys
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal, get_args

from benchmarks.agentic import live
from benchmarks.agentic.cleanup import until_signalled as _until_signalled
from benchmarks.agentic.host import (
    GRADING_CAPS,
    GRADING_SYSTEM,
    ItemSource,
    Launcher,
    LocalLauncher,
    WorkerJob,
    refuse_host_keys,
    run_jobs,
)
from benchmarks.agentic.inputs import CapturedInput, CapturedJSON
from benchmarks.agentic.ledger import (
    BATCH_LIMITS,
    PROVIDER_CAPS,
    PROVIDER_MARGINS,
    Ledger,
    LedgerService,
    PlannedItem,
    PriceSnapshot,
    bound_cost,
    load_price_snapshot,
    refuse_stale_snapshot,
    split_batch,
)
from benchmarks.agentic.protocol import (
    assert_no_key_shaped,
    assert_no_key_shaped_json,
    scanned_json_bytes,
)
from benchmarks.agentic.types import Buckets
from benchmarks.grading import inputs
from benchmarks.grading.aggregate import (
    has_final_record,
    load_answer_votes,
    reference_needed,
    withheld_reason,
    write_vote_record,
)
from benchmarks.grading.batch import BatchItemResult, BatchRequest, missing
from benchmarks.grading.calibration import verified_tuning
from benchmarks.grading.calibration_scan import scan_calibration_sources
from benchmarks.grading.calibration_storage import calibration_bytes, check_calibration_metadata
from benchmarks.grading.disclosure import audit_request
from benchmarks.grading.gates import BINDING_INPUTS, pass1_composition, registered_g3_sample
from benchmarks.grading.inputs import (
    PROBES_PATH,
    REPO_ROOT,
    GradingQuestion,
    JudgeInput,
    inputs_manifest,
    load_criteria,
    load_tuning_questions,
    render_judge_input,
    safe_id,
    snapshot_inputs,
)
from benchmarks.grading.panel import JudgeSpec, Panel, as_model_family, load_panel
from benchmarks.grading.probes import (
    MAX_ROUNDS,
    assignments,
    check_request,
    cited_sections,
    generation_request,
    load_probe_file,
    reference_subset,
)
from benchmarks.grading.score import CORPUS_PATH, CORPUS_SCHEMA_PATH, probe_build_round
from benchmarks.report_files import artifact_bytes
from benchmarks.runner import BenchmarkValidationError, _utc_now

Target = Literal[
    "probe-gen",
    "probe-check",
    "tuning",
    "probes",
    "reference-probes",
    "answers",
    "reference",
    "key-audit",
]
WorkspaceKind = Literal["probe-build", "tuning", "held-out"]
KINDS = {
    "probe-gen": "probe-build",
    "probe-check": "probe-build",
    "tuning": "tuning",
    "probes": "held-out",
    "reference-probes": "held-out",
}
RUN_TARGETS = {"answers", "reference", "key-audit"}


@dataclass(frozen=True)
class GradingConfig:
    target: Target
    out_dir: Path
    ledger_dir: Path
    panel_path: Path
    prices_path: Path
    criteria_path: Path
    tuning_path: Path
    probes_path: Path | None = None
    index_path: Path | None = None
    judges: tuple[str, ...] | None = None
    effort: str | None = None
    pass_number: int = 1
    round_number: int = 1
    live: bool = False
    confirm_max_spend: Decimal | None = None
    grading_run_id: str | None = None
    acknowledge_overrun: tuple[str, ...] = ()
    dry_run: bool = False


@dataclass(frozen=True)
class GradingItem:
    item_id: str
    judge_id: str
    request: BatchRequest
    planned: PlannedItem
    meta: dict[str, Any]


def grading_dir(config: GradingConfig) -> Path:
    return config.out_dir / "grading" if config.target in RUN_TARGETS else config.out_dir


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", value):
        raise BenchmarkValidationError("invalid grading identifier")
    assert_no_key_shaped(value, where="grading identifier")
    return value


def _read(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(path.read_bytes())
        if not isinstance(obj, dict):
            raise ValueError
        return obj
    except (OSError, ValueError) as exc:
        raise BenchmarkValidationError(f"cannot read grading evidence {path}") from exc


def _digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _tuning_handoff(path: Path, *, root: Path | None = None) -> CapturedJSON:
    """Capture explicit inputs or confined workspace copies exactly once."""
    try:
        if root is None:
            path = path.resolve()
            root = path.parent
        body = calibration_bytes(root, path)
    except FileNotFoundError:
        raise BenchmarkValidationError("missing tuning handoff") from None
    except (OSError, RuntimeError):
        raise BenchmarkValidationError("unavailable tuning handoff") from None

    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        assert_no_key_shaped_json(values, where="tuning handoff")
        record = dict(values)
        if len(record) != len(values):
            raise BenchmarkValidationError("tuning handoff contains a duplicate JSON member")
        return record

    try:
        record = json.loads(body.decode("utf-8"), object_pairs_hook=pairs)
    except BenchmarkValidationError:
        raise
    except (ValueError, RecursionError):
        raise BenchmarkValidationError("tuning handoff is not valid UTF-8 JSON") from None
    if not isinstance(record, dict):
        raise BenchmarkValidationError("invalid tuning evidence")
    entries = {"tuning": record}
    check_calibration_metadata(entries)
    scan_calibration_sources({"entries": entries})
    return CapturedJSON(body, record)


def _immutable(path: Path, value: Mapping[str, Any]) -> None:
    body = scanned_json_bytes(value, where="grading evidence", indent=2) + b"\n"
    if path.exists():
        if not path.is_file() or path.is_symlink() or path.read_bytes() != body:
            raise BenchmarkValidationError(f"immutable grading evidence differs: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    # Expose a complete file or nothing, even on an interrupted first write.
    with TemporaryDirectory(dir=path.parent) as directory:
        staged = Path(directory) / "record"
        staged.write_bytes(body)
        os.link(staged, path)


def _panel_ids(panel: Panel, requested: Sequence[str] | None) -> tuple[str, ...]:
    ids = panel.panel if requested is None else tuple(requested)
    if len(ids) != len(panel.panel) or any(
        len(set(ids) & {j, panel.fallback.get(j)}) != 1 for j in panel.panel
    ):
        raise BenchmarkValidationError(
            "select one panel judge per provider, including any fallback"
        )
    return tuple(j if j in ids else panel.fallback[j] for j in panel.panel)


def _validate_efforts(
    panel: Panel,
    ids: Sequence[str],
    manifest: Mapping[str, str],
    evidence: Sequence[dict[str, Any]],
    *,
    frozen: Path,
    require_source: bool = False,
) -> None:
    selected: dict[str, str] = {}
    for record in evidence:
        record_inputs = record.get("inputs")
        judges = record.get("judges")
        if not isinstance(record_inputs, dict) or not isinstance(judges, dict):
            raise BenchmarkValidationError("invalid tuning evidence inputs or judges")
        if record.get("prompt_sha256") != manifest["judge_prompt"] or any(
            record_inputs.get(k) != manifest[k] for k in BINDING_INPUTS
        ):
            raise BenchmarkValidationError(
                "tuning evidence inputs differ from the held-out workspace"
            )
        for j in ids:
            entry = judges.get(j)
            if entry is not None and not isinstance(entry, dict):
                raise BenchmarkValidationError(f"invalid tuning evidence for {j}")
            if not entry or not entry.get("chosen"):
                continue
            efforts = entry.get("efforts")
            if not isinstance(efforts, dict) or any(
                not isinstance(result, dict) for result in efforts.values()
            ):
                raise BenchmarkValidationError(f"invalid tuning effort evidence for {j}")
            passing = next(
                (
                    level
                    for level in panel.judges[j].effort_levels
                    if efforts.get(level, {}).get("meets") is True
                ),
                None,
            )
            if (
                passing is None
                or passing != entry["chosen"]
                or selected.setdefault(j, passing) != passing
            ):
                raise BenchmarkValidationError(f"inconsistent lowest passing effort for {j}")
    if any(not panel.judges[j].effort or selected.get(j) != panel.judges[j].effort for j in ids):
        raise BenchmarkValidationError(
            "frozen efforts must match the lowest passing tuning efforts"
        )
    _, unknown = verified_tuning(
        list(evidence),
        panel=CapturedInput.read(frozen / "panel.yml", "panel", "frozen panel"),
        probes=CapturedInput.read(frozen / "probes.yml", "probes", "frozen probes"),
        criteria=CapturedInput.read(frozen / "grading-criteria.yml", "criteria", "frozen criteria"),
        judges=ids,
        inputs=manifest,
    )
    if unknown and (require_source or any("source" in record for record in evidence)):
        raise BenchmarkValidationError("selected tuning attempts require original source evidence")


def init_workspace(
    path: Path,
    kind: WorkspaceKind,
    *,
    criteria_path: Path,
    panel_path: Path,
    probes_path: Path | None,
    tuning_evidence: Sequence[Path] = (),
    judges: Sequence[str] | None = None,
    tuning_path: Path = inputs.TUNING_PATH,
) -> dict[str, Any]:
    if kind not in get_args(WorkspaceKind):
        raise BenchmarkValidationError("invalid workspace kind")
    if path.is_symlink() or (path.exists() and (not path.is_dir() or any(path.iterdir()))):
        raise BenchmarkValidationError(f"workspace already exists: {path}")
    if kind != "probe-build" and probes_path is None:
        raise BenchmarkValidationError(f"{kind} workspace needs a probes file")
    if kind == "held-out" and not tuning_evidence:
        raise BenchmarkValidationError("held-out workspace needs tuning evidence")
    if kind != "held-out" and tuning_evidence:
        raise BenchmarkValidationError("tuning evidence requires a held-out workspace")
    path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path.parent) as temp:
        stage = Path(temp) / "workspace"
        manifest = snapshot_inputs(
            stage / "inputs",
            criteria_path=criteria_path,
            panel_path=panel_path,
            probes_path=probes_path,
        )
        handoff: dict[str, str] = {}
        records = []
        tuning_body = inputs.scanned_input(tuning_path, suffix=".yml")
        tuning_hash = _digest(tuning_body)
        (stage / "inputs/tuning-questions.yml").write_bytes(tuning_body)
        if probes_path:
            probe_file = load_probe_file(
                stage / "inputs/probes.yml",
                criteria_path=stage / "inputs/grading-criteria.yml",
                tuning_path=stage / "inputs/tuning-questions.yml",
            )
            if not probe_file.probes or len({p.id for p in probe_file.probes}) != len(
                probe_file.probes
            ):
                raise BenchmarkValidationError("empty or duplicate probe suite")
            if any(p.id != safe_id(p.type, p.question_id) for p in probe_file.probes):
                raise BenchmarkValidationError("invalid probe id")
            body = (stage / "inputs/probes.yml").read_bytes()
            dest = stage / "handoff"
            dest.mkdir()
            (dest / "probes.yml").write_bytes(body)
            handoff["probes.yml"] = _digest(body)
        for n, source in enumerate(tuning_evidence, 1):
            captured = _tuning_handoff(source)
            name = f"tuning-{n}.json"
            (stage / "handoff" / name).write_bytes(captured.content)
            handoff[name] = _digest(captured.content)
            records.append(captured.mapping)
        meta = {
            "kind": kind,
            "created_at": _utc_now(),
            "inputs": manifest,
            "handoff": handoff,
            "tuning_sha256": tuning_hash,
            "frozen_tuning": True,
        }
        if kind == "held-out":
            # The criteria digest pins the exact corpus used to render probes.
            corpus = inputs.scanned_input(CORPUS_PATH, suffix=".yml")
            criteria = CapturedInput.read(
                stage / "inputs/grading-criteria.yml", "criteria", "grading criteria"
            )
            if _digest(corpus) != criteria.mapping.get("corpus_sha256"):
                raise BenchmarkValidationError("held-out corpus differs from frozen criteria")
            (stage / "inputs/corpus.yml").write_bytes(corpus)
            panel = load_panel(stage / "inputs/panel.yml")
            ids = _panel_ids(panel, judges)
            _validate_efforts(
                panel, ids, manifest, records, frozen=stage / "inputs", require_source=True
            )
            meta["judges"] = list(ids)
        _immutable(stage / "workspace.json", meta)
        stage.rename(path)
    return meta


def _probes(config: GradingConfig) -> Path | None:
    if config.target in RUN_TARGETS:
        path = config.probes_path or PROBES_PATH
    elif config.target in ("probe-gen", "probe-check"):
        return config.probes_path
    else:
        path = config.probes_path or config.out_dir / "handoff/probes.yml"
    if not path.is_file():
        raise BenchmarkValidationError(f"missing probes file {path}")
    return path


def _frozen(config: GradingConfig) -> dict[str, str]:
    root = grading_dir(config)
    probes_path = _probes(config)
    manifest = inputs_manifest(
        criteria_path=config.criteria_path, panel_path=config.panel_path, probes_path=probes_path
    )
    if config.target not in RUN_TARGETS:
        meta = _read(root / "workspace.json")
        if meta.get("kind") != KINDS.get(config.target):
            raise BenchmarkValidationError(f"wrong workspace kind for {config.target}")
        if meta.get("tuning_sha256") != _digest(config.tuning_path.read_bytes()):
            raise BenchmarkValidationError("workspace tuning questions differ")
        if meta.get("frozen_tuning") is True and (
            _digest((root / "inputs/tuning-questions.yml").read_bytes()) != meta["tuning_sha256"]
        ):
            raise BenchmarkValidationError("frozen tuning questions differ")
        if meta.get("inputs") != manifest:
            raise BenchmarkValidationError("workspace inputs differ")
        records = []
        for name, digest in meta["handoff"].items():
            path = root / "handoff" / name
            if name == "probes.yml":
                body = artifact_bytes(root, path)
            elif meta["kind"] == "held-out" and re.fullmatch(r"tuning-[1-9][0-9]*\.json", name):
                captured = _tuning_handoff(path, root=root)
                body = captured.content
                records.append(captured.mapping)
            else:
                raise BenchmarkValidationError("invalid workspace handoff entry")
            if _digest(body) != digest:
                raise BenchmarkValidationError("workspace handoff differs")
        if "probes" in manifest and meta["handoff"].get("probes.yml") != manifest["probes"]:
            raise BenchmarkValidationError("workspace probes handoff differs")
        if meta["kind"] == "held-out":
            _validate_efforts(
                load_panel(root / "inputs/panel.yml"),
                meta["judges"],
                manifest,
                records,
                frozen=root / "inputs",
                require_source=meta.get("frozen_tuning") is True,
            )
    dest = root / "inputs"
    if not dest.exists() and config.target in RUN_TARGETS:
        with TemporaryDirectory(dir=root) as temp:
            staged = Path(temp) / "inputs"
            if (
                snapshot_inputs(
                    staged,
                    criteria_path=config.criteria_path,
                    panel_path=config.panel_path,
                    probes_path=probes_path,
                )
                != manifest
            ):
                raise BenchmarkValidationError("grading inputs changed during snapshot")
            staged.rename(dest)
    if _read(dest / "inputs.json") != manifest:
        raise BenchmarkValidationError("frozen grading inputs differ")
    names = {**inputs.SNAPSHOT_NAMES, "mapping_table": "mapping.txt", "mask_rules": "masking.txt"}
    for key, digest in manifest.items():
        if _digest((dest / names[key]).read_bytes()) != digest:
            raise BenchmarkValidationError("corrupt frozen grading inputs")
    return manifest


def _judges(config: GradingConfig, panel: Panel) -> tuple[str, ...]:
    if config.target not in get_args(Target):
        raise BenchmarkValidationError("unknown grading target")
    if config.pass_number not in (1, 2) or (
        config.pass_number == 2 and config.target not in ("answers", "probes")
    ):
        raise BenchmarkValidationError("invalid grading pass for this target")
    if not 1 <= config.round_number <= MAX_ROUNDS or (
        config.round_number != 1 and config.target not in ("probe-gen", "probe-check")
    ):
        raise BenchmarkValidationError("invalid grading round for this target")
    if config.effort is not None and config.target != "tuning":
        raise BenchmarkValidationError("--effort is for tuning only")
    ids = config.judges
    if config.target == "tuning":
        if (
            not ids
            or len(ids) != 1
            or ids[0] not in (*panel.panel, *panel.fallback.values())
            or config.effort not in panel.judges[ids[0]].effort_levels
        ):
            raise BenchmarkValidationError("tuning needs one panel judge and a supported effort")
    elif config.target in ("probes", "answers"):
        if config.target == "probes":
            frozen = _read(config.out_dir / "workspace.json").get("judges")
            ids = _panel_ids(panel, ids or frozen)
            if list(ids) != frozen:
                raise BenchmarkValidationError("held-out workspace composition differs")
        else:
            ids = _panel_ids(panel, ids)
        if any(panel.judges[j].effort is None for j in ids):
            raise BenchmarkValidationError("panel efforts must be frozen")
    else:
        allowed = (
            panel.generators
            if config.target in ("probe-gen", "probe-check")
            else (panel.reference,)
        )
        ids = allowed if ids is None else ids
        if not ids or len(set(ids)) != len(ids) or not set(ids) <= set(allowed):
            raise BenchmarkValidationError(f"invalid judges for {config.target}")
    assert ids is not None
    for j in ids:
        _identifier(j)
        if e := (config.effort or panel.judges[j].effort):
            _identifier(e)
    return tuple(ids)


def _criteria(config: GradingConfig) -> dict[str, GradingQuestion]:
    corpus = (
        config.out_dir / "snapshots/corpus.yml" if config.target in RUN_TARGETS else CORPUS_PATH
    )
    if config.target in ("probes", "reference-probes"):
        captured = config.out_dir / "inputs/corpus.yml"
        if captured.exists():
            corpus = captured
    return load_criteria(config.criteria_path, corpus_path=corpus, schema_path=CORPUS_SCHEMA_PATH)


def _item(judge: JudgeSpec, req: BatchRequest, meta: dict[str, Any]) -> GradingItem:
    value = JudgeInput(req.system, req.user, req.schema, bool(meta.get("suspect")))
    length = len((req.system + req.user + json.dumps(req.schema)).encode())
    size = len(scanned_json_bytes(asdict(req), where="judge request", ensure_ascii=False))
    planned = PlannedItem(
        req.item_id, length + 2 * judge.framing_allowance_tokens, judge.max_output_tokens, size
    )
    return GradingItem(
        req.item_id, judge.id, req, planned, {**meta, "input_sha256": value.sha256()}
    )


def _judge_items(
    panel: Panel,
    ids: Sequence[str],
    question: GradingQuestion,
    answer: str,
    item_id: str,
    meta: dict[str, Any],
) -> Iterable[GradingItem]:
    value = render_judge_input(question, answer)
    req = BatchRequest(item_id, value.system, value.user, value.schema, "grade")
    for j in ids:
        yield _item(panel.judges[j], req, {**meta, "suspect": value.suspect})


def _probe_build(config: GradingConfig, panel: Panel, ids: Sequence[str]) -> Iterable[GradingItem]:
    spec = (config.out_dir / "inputs/probe-spec.md").read_text(encoding="utf-8")

    r = config.round_number
    for questions in (_criteria(config), load_tuning_questions(config.tuning_path)):
        for (qid, kind), (gen, checker) in assignments(sorted(questions), panel.generators).items():
            def pair(n: int) -> tuple[dict[str, Any], dict[str, Any]]:
                return probe_build_round(
                    config.out_dir, question=questions[qid], probe_type=kind, round_number=n,
                    generator=panel.judges[gen], checker=panel.judges[checker], spec_text=spec,
                )

            if config.target == "probe-gen":
                if gen not in ids or any(pair(n)[1].get("meets_spec") for n in range(1, r)):
                    continue
                j, req = gen, generation_request(kind, questions[qid], r, spec_text=spec)
            else:
                text = pair(r)[0].get("probe_text")
                if checker not in ids or not isinstance(text, str) or not text.strip():
                    continue
                j, req = checker, check_request(kind, questions[qid], text, r, spec_text=spec)
            yield _item(panel.judges[j], req, {"question_id": qid, "probe_type": kind, "round": r})


def _reference_ids(
    config: GradingConfig,
    panel: Panel,
    answers: dict[str, dict[str, Any]],
    questions: Mapping[str, GradingQuestion],
) -> set[str]:
    root = grading_dir(config)
    composition = pass1_composition(root, "answers")
    panel_ids = _panel_ids(panel, tuple(composition))
    efforts = {j: panel.judges[j].effort for j in panel_ids}
    for j in panel_ids:
        if composition[j] != {"model_id": panel.judges[j].model_id, "effort": efforts[j]}:
            raise BenchmarkValidationError("pass-1 answer composition differs")
    requests = {
        (row["competitor_id"], row["corpus_id"]): render_judge_input(
            questions[row["corpus_id"]], row["answer"]
        )
        for row in answers.values()
    }
    votes = load_answer_votes(root, panel=panel, judges=efforts, requests=requests)
    for av in votes.values():
        if not all(av.records.get(j, {}).get("final") for j in efforts):
            raise BenchmarkValidationError(f"no final pass-1 panel record for {av.item_id}")
    if {v.item_id for v in votes.values()} != set(answers):
        raise BenchmarkValidationError("pass-1 inputs differ from the run answers")
    excluded = set(reference_needed(votes))
    transcripts = {
        (row["competitor_id"], row["corpus_id"]): row for row in answers.values()
    }
    seed = str(_read(config.out_dir / "environment.json")["seed"])
    ids, info = registered_g3_sample(
        votes, transcripts, questions, panel_ids, seed=seed
    )
    _immutable(root / "reference-sample.json", {**info, "ids": ids})
    return excluded.union(ids)


def _build_items(config: GradingConfig, panel: Panel, ids: Sequence[str]) -> Iterable[GradingItem]:
    if config.target in ("probe-gen", "probe-check"):
        yield from _probe_build(config, panel, ids)
        return
    if config.target in ("probes", "reference-probes", "tuning"):
        tuning = config.target == "tuning"
        questions = load_tuning_questions(config.tuning_path) if tuning else _criteria(config)
        suite = load_probe_file(
            config.out_dir / "handoff/probes.yml",
            criteria_path=config.criteria_path,
            tuning_path=config.tuning_path,
        )
        probes = [p for p in suite.probes if p.set == ("tuning" if tuning else "held_out")]
        if config.target == "reference-probes":
            probes = reference_subset(probes, seed=suite.header["corpus_sha256"])
        for p in probes:
            if p.question_id not in questions or p.id != safe_id(p.type, p.question_id):
                raise BenchmarkValidationError("invalid grading probe")
            yield from _judge_items(
                panel, ids, questions[p.question_id], p.text, p.id, {"question_id": p.question_id}
            )
        return
    questions = _criteria(config)
    if config.target == "key-audit":
        if config.index_path is None or not config.index_path.is_file():
            raise BenchmarkValidationError("key-audit needs a readable docs index")
        frozen = grading_dir(config) / "inputs"
        manifest = _read(frozen / "inputs.json")
        prompt = inputs.scanned_input(frozen / "key-audit-prompt.md", suffix=".md")
        if _digest(prompt) != manifest.get("key_audit_prompt"):
            raise BenchmarkValidationError("corrupt frozen key-audit prompt")
        for q in questions.values():
            sections = [asdict(s) for s in cited_sections(q, config.index_path)]
            # Inspect the full source before clipping; omitted text cannot hide a key.
            scanned_json_bytes(sections, where="key-audit cited sections")
            remaining = 20_000
            # Keep short citations whole, then share the space among longer ones.
            # Mutate their text in place to preserve citation order in the request.
            by_length = sorted(sections, key=lambda section: len(section["text"]))
            for pos, section in enumerate(by_length):
                share = remaining // (len(by_length) - pos)
                section["text"] = section["text"][:share]
                remaining -= len(section["text"])
            # Shared with scorer/report replay: every required citation must retain
            # nonempty text, in order and at its own version, within the document cap.
            value = audit_request(q, sections, prompt=prompt)
            req = BatchRequest(
                safe_id(q.id),
                value.system,
                value.user,
                value.schema,
                "key_audit",
            )
            yield _item(panel.judges[ids[0]], req, {
                "corpus_id": q.id, "cited_sections": sections, "inputs": manifest,
            })
        return
    answers = {}
    for path in sorted((config.out_dir / "transcripts").glob("*/*.json")):
        row = _read(path)
        if row.get("status") != "succeeded" or not (row.get("answer") or "").strip():
            continue
        item_id = safe_id(row["competitor_id"], row["corpus_id"])
        if item_id in answers or row["corpus_id"] not in questions:
            raise BenchmarkValidationError("duplicate or unknown answer")
        answers[item_id] = row
    wanted = (
        _reference_ids(config, panel, answers, questions)
        if config.target == "reference"
        else set(answers)
    )
    for item_id, row in answers.items():
        if item_id in wanted:
            meta = {key: row[key] for key in ("competitor_id", "corpus_id")}
            yield from _judge_items(
                panel, ids, questions[row["corpus_id"]], row["answer"], item_id, meta
            )


def build_items(config: GradingConfig, panel: Panel) -> list[GradingItem]:
    items = list(_build_items(config, panel, _judges(config, panel)))
    if len({(i.judge_id, i.item_id) for i in items}) != len(items):
        raise BenchmarkValidationError("duplicate grading item id")
    return items


def _item_evidence(item: GradingItem) -> dict[str, Any]:
    req = item.request
    value = JudgeInput(req.system, req.user, req.schema, bool(item.meta.get("suspect")))
    return value.item_evidence(item.meta)


@dataclass
class _Prepared:
    plan: dict[str, Any]
    jobs: list[WorkerJob]
    items: dict[tuple[str, str], GradingItem]
    snapshot: PriceSnapshot
    total: Decimal


def _prepare(config: GradingConfig, panel: Panel, manifest: dict[str, str]) -> _Prepared:
    root = grading_dir(config)
    if _frozen(config) != manifest:
        raise BenchmarkValidationError("grading inputs changed while loading the panel")
    ids = _judges(config, panel)
    snapshot = load_price_snapshot(config.prices_path)
    items = build_items(config, panel)
    # A prompt or schema edited between hashing and rendering invalidates this plan too.
    if _frozen(config) != manifest:
        raise BenchmarkValidationError("grading inputs changed during preparation")
    pending: dict[str, list[GradingItem]] = defaultdict(list)
    skipped: Counter[str] = Counter()
    for item in items:
        judge = panel.judges[item.judge_id]
        evidence = _item_evidence(item)
        path = root / "items" / config.target / f"{item.item_id}.json"
        _immutable(path, evidence)
        if has_final_record(
            root,
            target=config.target,
            judge_id=judge.id,
            effort=config.effort or judge.effort,
            pass_number=config.pass_number,
            item_id=item.item_id,
        ):
            skipped[judge.id] += 1
        else:
            pending[judge.id].append(item)
    if config.target == "answers":
        old = {p.stem for p in (root / "items/answers").glob("*.json")}
        if old != {i.item_id for i in items}:
            raise BenchmarkValidationError("run answers differ from frozen answer items")
    jobs = []
    by_provider: dict[str, Decimal] = defaultdict(Decimal)
    estimates: dict[str, Decimal] = defaultdict(Decimal)
    by_judge = {}
    composition = {}
    for j in ids:
        judge = panel.judges[j]
        price = snapshot.models.get(judge.model_id)
        if price is None or price.provider != judge.provider:
            raise BenchmarkValidationError(f"missing price for {judge.model_id}")
        effort = config.effort or judge.effort
        composition[j] = {"model_id": judge.model_id, "effort": effort}
        todo = pending[j]
        costs = [
            bound_cost(
                price,
                input_bound=min(i.planned.input_bound, judge.max_input_tokens),
                output_bound=i.planned.output_bound,
            )
            for i in todo
        ]
        max_items, max_bytes = BATCH_LIMITS[judge.provider]
        groups = split_batch(
            costs,
            [i.planned.size_bytes for i in todo],
            limit=PROVIDER_MARGINS[judge.provider],
            max_items=max_items,
            max_bytes=max_bytes,
        )
        bound = sum(costs, Decimal(0))
        by_provider[judge.provider] += bound
        estimates[judge.provider] += bound * price.batch_multiplier
        by_judge[j] = {
            "items": len(todo),
            "skipped": skipped[j],
            "batches": [[todo[i].item_id for i in group] for group in groups],
            "bound": str(bound),
        }
        if todo:
            source = ItemSource([(i.planned, asdict(i.request)) for i in todo])
            jobs.append(
                WorkerJob(
                    f"{j}--grading",
                    as_model_family(judge),
                    GRADING_SYSTEM,
                    (),
                    GRADING_CAPS,
                    task="batches",
                    payload={"effort": effort, "target": config.target},
                    source=source,
                )
            )
    if config.target in ("probes", "answers"):
        for path in (root / "runs").glob("*.json"):
            prior = _read(path)
            if prior.get("target") == config.target and prior.get("composition") != composition:
                raise BenchmarkValidationError("grading composition differs in the same directory")
    total = sum(by_provider.values(), Decimal(0))
    plan = {
        "target": config.target,
        "pass": config.pass_number,
        "round": config.round_number,
        "composition": composition,
        "judges": by_judge,
        "items": len({i.item_id for todo in pending.values() for i in todo}),
        "provider_bounds": {p: str(v) for p, v in by_provider.items()},
        "batch_estimates": {p: str(v) for p, v in estimates.items()},
        "total_bound": float(total),
    }
    return _Prepared(plan, jobs, {(i.judge_id, i.item_id): i for i in items}, snapshot, total)


def plan_grading(config: GradingConfig) -> dict[str, Any]:
    return run_grading(config, plan_only=True)


class _Collector:
    def __init__(self, config: GradingConfig, panel: Panel, prepared: _Prepared, run_id: str):
        self.config = config
        self.panel = panel
        self.items = prepared.items
        self.run_id = run_id
        self.records = 0
        self.execution_results: dict[str, str] = {}
        self.withheld: Counter[str] = Counter()
        self.not_run: list[dict[str, Any]] = []

    async def collect(self, job: WorkerJob, frame: dict[str, Any]) -> None:
        ctx = {
            "target": self.config.target,
            "judge_id": job.family.id,
            "effort": job.payload["effort"],
            "pass": self.config.pass_number,
        }
        kind = frame["type"]
        if kind == "item_not_run":
            self.not_run.append({**ctx, **{k: frame[k] for k in ("item_id", "reason", "attempt")}})
        elif kind == "item_terminal":
            self.record(job, frame, missing(frame["item_id"]), frame["attempt"], True)
        elif kind == "batch_results":
            for row in frame["results"]:
                result = dict(row)
                attempt, terminal = result.pop("attempt"), result.pop("terminal")
                result.pop("raw_dropped", None)  # host annotation on an oversized raw reply
                result.pop("billing_evidence", None)  # host-validated compact free-error receipt
                if result["buckets"] is not None:
                    result["buckets"] = Buckets(**result["buckets"])
                self.record(job, frame, BatchItemResult(**result), attempt, terminal)

    def record(
        self,
        job: WorkerJob,
        frame: dict[str, Any],
        result: BatchItemResult,
        attempt: int,
        terminal: bool,
    ) -> None:
        item = self.items[job.family.id, result.item_id]
        vote_path = write_vote_record(
            grading_dir(self.config),
            target=self.config.target,
            judge=self.panel.judges[job.family.id],
            effort=job.payload["effort"],
            pass_number=self.config.pass_number,
            grading_run_id=self.run_id,
            attempt=attempt,
            result=result,
            terminal=terminal,
            unit_id=frame["unit_id"],
            provider_batch_id=frame.get("provider_batch_id"),
            input_sha256=item.meta["input_sha256"],
            schema=item.request.schema,
        )
        # The host publishes this callback only after durable batch settlement.
        # Keep its normalized raw result: vote records intentionally omit raw.
        relative = f"{job.family.id}/{result.item_id}--a{attempt}.json"
        history_path = grading_dir(self.config) / "executions" / self.run_id / relative
        history = {
            "grading_run_id": self.run_id,
            "unit_id": frame["unit_id"],
            "item_id": result.item_id,
            "judge_id": job.family.id,
            "target": self.config.target,
            "pass": self.config.pass_number,
            "attempt": attempt,
            "provider_batch_id": frame.get("provider_batch_id"),
            "input_sha256": item.meta["input_sha256"],
            "vote_sha256": _digest(vote_path.read_bytes()),
            "provider": job.family.provider,
            "api": job.family.api,
            "model_id": job.family.model_id,
            "effort": job.payload["effort"],
            "result": asdict(result),
        }
        _immutable(history_path, history)
        self.execution_results[relative] = _digest(history_path.read_bytes())
        self.records += 1
        if reason := withheld_reason(result, item.request.schema):
            self.withheld[reason] += 1


def _execute(
    config: GradingConfig,
    run_id: str,
    launcher: Launcher,
    caps: Mapping[str, Decimal],
    today: date | None,
    confirm: Callable[[str], bool],
    plan_only: bool,
) -> dict[str, Any]:
    root = grading_dir(config)
    run_file = root / "runs" / f"{run_id}.json"
    claim = root / "invocations" / run_id
    if run_file.exists() or claim.exists():
        raise BenchmarkValidationError(f"grading run id already used: {run_id}")
    manifest = _frozen(config)
    panel = load_panel(config.panel_path)
    prepared = _prepare(config, panel, manifest)
    plan = prepared.plan
    live_judges = any(panel.judges[j].api != "scripted" for j in plan["judges"])
    dry = plan_only or config.dry_run or (live_judges and not config.live)
    if config.live:
        amount = config.confirm_max_spend
        if amount is None or not amount.is_finite() or amount < prepared.total:
            raise BenchmarkValidationError(f"--confirm-max-spend must be at least {prepared.total}")
        if live_judges:
            refuse_stale_snapshot(prepared.snapshot, today=today or datetime.now(UTC).date())
    if plan_only:
        return {**plan, "dry_run": True}
    # Claim even dry runs and interrupted invocations before opening the ledger.
    claim.parent.mkdir(exist_ok=True)
    claim.mkdir()
    if dry:
        summary = {**plan, "dry_run": True}
        _immutable(claim / "plan.json", summary)
        fixed = root / "plans" / f"{config.target}-pass{config.pass_number}.json"
        if not fixed.exists():
            _immutable(fixed, summary)
        return summary
    collector = _Collector(config, panel, prepared, run_id)
    summary = {
        **plan,
        "grading_run_id": run_id,
        "dry_run": False,
        "started_at": _utc_now(),
        "status": "aborted",
    }
    ledger = Ledger.open(
        config.ledger_dir,
        run_id=run_id,
        caps=caps,
        invocation_budget=config.confirm_max_spend if config.live else None,
    )
    try:
        summary["spent_before"] = {p: str(ledger.spent(p)) for p in caps}
        for provider in config.acknowledge_overrun:
            ledger.acknowledge_overrun(provider)
        for p in plan["provider_bounds"]:
            if p not in caps:
                raise BenchmarkValidationError(f"missing provider cap for {p}")
            if ledger.stopped(p) == "overrun":
                raise BenchmarkValidationError(
                    f"{p} stopped on an overrun; use --acknowledge-overrun"
                )
        if live_judges:
            text = "\n".join(
                f"{p}: planned standard-rate bound {b}, spent {ledger.spent(p)}, "
                f"cap {caps[p]}, remaining {caps[p] - ledger.spent(p)}, "
                f"batch-rate estimate {plan['batch_estimates'][p]}"
                for p, b in plan["provider_bounds"].items()
            )
            live.authorize_live(text, confirm=confirm)
        if prepared.jobs:
            families = {job.family.model_id: job.family for job in prepared.jobs}
            service = LedgerService(ledger, prepared.snapshot.models, families)
            asyncio.run(
                _until_signalled(
                    run_jobs(
                        prepared.jobs,
                        launcher=launcher,
                        ledger=service,
                        on_message=collector.collect,
                        log_dir=root / "logs" / run_id,
                        cwd=REPO_ROOT,
                    )
                )
            )
        summary["status"] = "completed"
    finally:
        primary = sys.exception()
        failures: list[tuple[str, BaseException]] = []
        events: list[dict[str, Any]] = []
        try:
            events = ledger.invocation_events()
            summary.update(
                completed_at=_utc_now(),
                spent_after={p: str(ledger.spent(p)) for p in caps},
                units=[e["unit_id"] for e in events if e["event"] == "reserve"],
                not_run=collector.not_run,
                records_written=collector.records,
                execution_results=dict(collector.execution_results),
                withheld=dict(collector.withheld),
            )
        except BaseException as exc:
            failures.append(("outcome", exc))
        try:
            body = b"".join(
                scanned_json_bytes(e, where="grading ledger events", sort_keys=True) + b"\n"
                for e in events
            )
            with (root / "ledger-events.jsonl").open("ab") as file:
                file.write(body)
        except BaseException as exc:
            failures.append(("journal", exc))
        try:
            _immutable(run_file, summary)
        except BaseException as exc:
            failures.append(("run record", exc))
        try:
            ledger.close()
        except BaseException as exc:
            failures.append(("ledger close", exc))
        if failures:
            # Artifact storage can fill while the separate durable ledger still works.
            # Keep the original cancellation/credential error, try every finalizer, and
            # disclose only validated IDs and error types (never exception messages).
            units = []
            for unit in summary.get("units", []):
                try:
                    units.append(_identifier(unit))
                except BenchmarkValidationError:
                    continue
            failure = primary if primary is not None else failures[0][1]
            failure.add_note(
                f"Grading recovery: grading_run_id={run_id}; units={','.join(units) or 'none'}; "
                + "; ".join(f"{step}: {type(exc).__name__}" for step, exc in failures)
            )
            if primary is None:
                raise failure
    return summary


def run_grading(
    config: GradingConfig,
    *,
    launcher: Launcher | None = None,
    caps: Mapping[str, Decimal] = PROVIDER_CAPS,
    today: date | None = None,
    confirm: Callable[[str], bool] = live.interactive_confirm,
    plan_only: bool = False,
) -> dict[str, Any]:
    refuse_host_keys(os.environ)
    run_id = _identifier(
        config.grading_run_id if config.grading_run_id is not None else uuid.uuid4().hex
    )
    root = grading_dir(config)
    if config.target in RUN_TARGETS and not (config.out_dir / "snapshots/corpus.yml").is_file():
        raise BenchmarkValidationError("grading requires an answering run with a corpus snapshot")
    if config.target in KINDS and not (root / "workspace.json").is_file():
        raise BenchmarkValidationError(f"grading requires a {KINDS[config.target]} workspace")
    try:
        root.mkdir(parents=True, exist_ok=True)
        fd = os.open(root, os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # A workspace owns its evidence. Following an existing link could publish outside it.
            for path in root.rglob("*"):
                if path.is_symlink() or not (path.is_dir() or path.is_file()):
                    raise BenchmarkValidationError(
                        "grading evidence must be regular files and directories"
                    )
            return _execute(
                config, run_id, launcher or LocalLauncher(), caps, today, confirm, plan_only
            )
        finally:
            os.close(fd)
    except BenchmarkValidationError:
        raise
    except (OSError, KeyError, TypeError, ValueError, sqlite3.Error) as exc:
        error = BenchmarkValidationError(
            f"invalid or unavailable grading input ({type(exc).__name__})"
        )
        for note in getattr(exc, "__notes__", ()):
            error.add_note(note)
        raise error from exc


def check_tuning_handoff(workspace: Path, paths: Sequence[Path]) -> None:
    handoff = _read(workspace / "workspace.json")["handoff"]
    prior = {k: v for k, v in handoff.items() if k.startswith("tuning-")}
    requested = {
        f"tuning-{n}.json": _digest(_tuning_handoff(path).content)
        for n, path in enumerate(paths, 1)
    }
    if prior != requested:
        raise BenchmarkValidationError("immutable tuning handoff differs")
