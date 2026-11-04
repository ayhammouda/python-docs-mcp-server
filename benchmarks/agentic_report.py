"""The agentic ``REPORT.md`` and README summary (design §§2, 9, 11.3).

``benchmarks.report`` dispatches here when ``run-summary.json`` says ``mode:
agentic``. Every grading fact comes from the run directory (``grading/summary.json``,
``grading/inputs/``, ``grading/disclosure/``, vote records, evidence files), so the
report renders from an exported bundle alone. The wording rules of design §2 hold
everywhere: no anti-claim, and ``REQUIRED_WORDING`` for the grading.
"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict
from decimal import Decimal, DecimalException
from pathlib import Path
from typing import Any

from benchmarks.adapters.claude_tokens import DEFAULT_COUNT_MODEL
from benchmarks.agentic.egress import PROVIDER_HOSTS
from benchmarks.agentic.inputs import CapturedInput, json_mapping
from benchmarks.agentic.ledger import actual_cost, price_snapshot_from_mapping
from benchmarks.agentic.protocol import KeyLeakError, check_json_text
from benchmarks.agentic.reference import ReferenceTranscripts, ReferenceUnits, scripted_response
from benchmarks.agentic.types import Buckets
from benchmarks.grading.gates import (
    EGRESS_JOB_FIELDS,
    CellKey,
    c2,
    c3,
    egress_from_mapping,
    load_offline_evidence,
    model_lineage,
    offline_verdict,
    read_evidence_json,
)
from benchmarks.grading.inputs import SNAPSHOT_FILES, safe_id
from benchmarks.grading.stats import GATE_RESAMPLES, paired_bootstrap
from benchmarks.model_matrix import KNOWN_APIS, ModelMatrix, model_family_from_raw
from benchmarks.report import RunBundle, _cell, _percentile, _relative_link, _table
from benchmarks.report_files import artifact_bytes, artifact_paths, checked_path, optional_json
from benchmarks.runner import BenchmarkValidationError

REQUIRED_WORDING = "LLM-graded, validated against synthetic probes and inter-model agreement"
ROUND_1_SCOPE = "fewer tokens than fetching the official docs pages"
FORBIDDEN_PHRASES = (
    "human-graded",
    "human-calibrated",
    "human-validated",
    "graded against ground truth",
    "runs 2 and 3 are cheaper",
    "fewer tokens than other docs mcps",
)
OUTPUT_CAP_RULE = 0.01
NOT_GRADED = "not graded"
TOKEN_SYSTEMS = ("python-docs", "docs-fetch")  # the correctness floor never gets a token row
NOT_COMPARABLE = "not comparable with any earlier study that used another count model"
REPORT_RESAMPLES = 2_000  # descriptive intervals only (S1); the gates use 10,000
DISCLAIMER = (
    "This is a generated raw report. A comparative claim may enter the README or PyPI only "
    "for live answering and grading when the grading outcome is Validated and the claim is "
    "not judge-sensitive (design §9.3)."
)
DISCLOSURE = (
    "Correctness was graded by three LLMs ({judges}) against the corpus answer keys. "
    "No human graded or calibrated any grade. Validity rests on the published probe suite, "
    "agreement among the judges, and agreement with an LLM reference grader. These checks "
    "cannot show how often the graders are wrong on real answers."
)
RESIDUAL_RISK = (
    "The budget holds if no unit costs more than twice its bound. One token per byte is an "
    "assumption about tokenizers, not something every provider documents; an error larger "
    "than 2× is the accepted residual risk (design §12)."
)


def _read_yaml(root: Path, path: Path) -> Any:
    try:
        body = artifact_bytes(root, path)
    except FileNotFoundError:
        return None
    try:
        return CapturedInput.from_bytes(body, "report snapshot", "report snapshot").mapping
    except KeyLeakError:
        raise
    except BenchmarkValidationError:
        return None


def _events(root: Path, path: Path) -> list[dict[str, Any]]:
    try:
        content = artifact_bytes(root, path)
    except FileNotFoundError:
        return []
    try:
        return [
            json_mapping(line, "agentic report ledger")
            for line in content.splitlines()
            if line.strip()
        ]
    except KeyLeakError:
        raise
    except BenchmarkValidationError:
        # A malformed row must not hide escaped credentials later in the same ledger.
        check_json_text(content.decode("utf-8", errors="replace"), where="agentic report ledger")
        raise


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _interval(interval: Mapping[str, Any] | None) -> str:
    if not interval:
        return "n/a"
    low, high = _fmt(interval.get("low")), _fmt(interval.get("high"))
    return f"{_fmt(interval.get('point'))}, 95% CI {low} to {high}"


def _verdict(passed: Any) -> str:
    return {True: "pass", False: "fail"}.get(passed, "not evaluable")


def _median(values: Sequence[float]) -> str:
    return _fmt(statistics.median(values), 1) if values else "n/a"


def _esc(value: Any) -> str:
    """One run-data value as literal inline text, escaped exactly as a table cell.

    Run data (error bodies from providers, proxies and MCP servers, model ids, reasons)
    is attacker-influenced: unescaped in a bullet or a sentence it could add HTML, an
    image, a link, a code span or a heading, the class #173 closed for table cells.
    """
    return _cell(_fmt(value))


def _fence(text: str) -> list[str]:
    """A fenced block that its content cannot close: one backtick more than any run."""
    runs = [len(run) for run in re.findall(r"`+", text)]
    fence = "`" * max(3, max(runs, default=0) + 1)
    return [f"{fence}text", text.rstrip(), fence]


def _execution_kind(apis: Sequence[str | None], *, grading: bool = False) -> str:
    if "scripted" in apis:
        return "scripted"
    # The OpenAI-compatible Ollama endpoint supports answering only.
    allowed = KNOWN_APIS - {"openai-chat-completions"} if grading else KNOWN_APIS
    known = all(isinstance(api, str) and api in allowed for api in apis)
    return "live" if apis and known else "unknown"


def _answering_evidence(
    bundle: RunBundle,
    families: dict[str, dict[str, Any]],
    models: Sequence[str],
    events: list[dict[str, Any]],
) -> tuple[str, dict[tuple[str, str], dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    transcripts: dict[tuple[str, str], dict[str, Any]] = {}
    discarded: dict[tuple[str, str], dict[str, Any]] = {}
    root = bundle.run_dir
    summary = bundle.run_summary
    declared = _execution_kind([families.get(fid, {}).get("api") for fid in models])
    units = ReferenceUnits()
    inventory = None
    consistent = True
    seen: set[str] = set()
    try:
        if "transcript_inventory" in summary:
            inventory = ReferenceTranscripts(summary)
    except ValueError:
        consistent = False
    scripted = declared == "scripted"
    for kind in ("transcripts", "discarded"):
        for path in artifact_paths(root, root / kind, "*/*.json"):
            body = artifact_bytes(root, path)
            record = json_mapping(body, "answering transcript")
            if not isinstance(record.get("model_calls", []), list):
                raise BenchmarkValidationError(f"invalid transcript: {path}")
            calls = record.get("model_calls") or []
            execution = record.get("execution")
            execution = execution if isinstance(execution, dict) else {}
            scripted |= execution.get("api") == "scripted" or any(
                isinstance(call, dict) and scripted_response(call) for call in calls
            )
            key = (record.get("competitor_id"), record.get("corpus_id"))
            if kind == "transcripts":
                transcripts[path.parent.name, path.stem] = record
            else:
                discarded[path.parent.name, path.name] = record
            try:
                if inventory is not None:
                    inventory.verify(path.relative_to(root).as_posix(), body, record)
                family = model_family_from_raw(families[record["model_family_id"]])
                cell = f"{record['system_id']}--{family.id}--r{record['repeat']}"
                consistent &= key[0] == path.parent.name == cell
                units.add(record, family)
                if kind == "transcripts":
                    consistent &= path.stem == key[1]
                    if any(call.get("attempts") for call in calls):
                        seen.add(family.id)
                    if record.get("status") == "succeeded":
                        consistent &= any(call.get("raw_response") is not None for call in calls)
            except (ValueError, KeyError, TypeError, DecimalException):
                consistent = False
    if scripted:
        return "scripted", transcripts, discarded
    try:
        if inventory is not None:
            inventory.finish()
        units.reconcile(events, summary)
    except (ValueError, KeyError, TypeError, DecimalException):
        consistent = False
    expected = {(c.competitor_id, c.corpus_id) for c in bundle.cells}
    listed_discards = {
        (d.get("cell"), f"{d.get('corpus_id')}--attempt{d.get('attempt')}.json")
        for d in summary.get("discarded") or []
    }
    complete = (
        transcripts.keys() == expected
        and len(expected) == summary.get("scored_cells")
        and discarded.keys() == listed_discards
    )
    live = declared == "live" and consistent and complete and seen == set(models)
    return ("live" if live else "unknown"), transcripts, discarded


def _graded_execution(
    root: Path,
    judges: dict[str, Any],
    efforts: dict[str, Any],
    reference: str,
    inputs: dict[str, str],
    scoring: dict[tuple[str, str], dict[str, Any]],
    events: list[dict[str, Any]],
    reported_versions: dict[Path, str | None],
) -> str:
    # Join chosen votes by their digest and coordinates. Recorded path strings
    # may name the original machine or be relative to a different working directory.
    runs = {
        p.stem: optional_json(root, p) or {}
        for p in artifact_paths(root, root / "grading/runs", "*.json")
    }
    reservations = {e.get("unit_id"): e for e in events if e.get("event") == "reserve"}
    votes = {}
    for target, members in (
        ("answers", efforts),
        ("reference", {reference: judges[reference].get("effort")}),
    ):
        for judge, effort in members.items():
            parts = (judge, effort or "default")
            if not all(
                isinstance(p, str) and p and Path(p).name == p and not p.startswith(".")
                for p in parts
            ):
                return "unknown"
            base = root.joinpath("grading/votes", target, *parts, "pass1")
            for path in artifact_paths(root, base, "*/*.json"):
                body = artifact_bytes(root, path)
                vote = json_mapping(body, "grading vote")
                reported_versions[path] = vote.get("reported_model")
                if str(vote.get("provider_batch_id")).startswith("scripted-batch-"):
                    return "scripted"
                run_id = vote.get("grading_run_id")
                if not isinstance(run_id, str):
                    continue
                invocation = runs.get(run_id, {})
                model_id = judges[judge].get("model_id")
                spec = {"model_id": model_id, "effort": effort}
                if (
                    invocation.get("grading_run_id") != vote.get("grading_run_id")
                    or invocation.get("target") != target
                    or invocation.get("pass") != 1
                    or invocation.get("dry_run")
                    or (invocation.get("composition") or {}).get(judge) != spec
                ):
                    continue
                reserve = reservations.get(vote.get("unit_id"))
                if reserve and reserve.get("api") == "scripted":
                    return "scripted"
                if reserve and (
                    reserve.get("provider") != judges[judge].get("provider")
                    or reserve.get("model_id") != model_id
                    or reserve.get("api", judges[judge]["api"]) != judges[judge]["api"]
                    or reserve.get("job_id") != f"{judge}--grading"
                ):
                    continue
                identity = {
                    "model_id": model_id,
                    "effort": effort,
                    "judge_id": judge,
                    "target": target,
                    "pass": 1,
                    "item_id": path.parent.name,
                }
                if vote.get("provider_batch_id") and all(
                    vote.get(k) == v for k, v in identity.items()
                ):
                    votes[judge, path.parent.name, hashlib.sha256(body).hexdigest()] = model_id

    observed = set()
    for record in scoring.values():
        grade = record.get("grading")
        if not grade:
            # Automatic zeroes (e.g. a failed trajectory) have no LLM votes.
            if record.get("score") is not None and record.get("scoring_method") != "automatic":
                return "unknown"
            continue
        try:
            item = safe_id(record["competitor_id"], record["corpus_id"])
        except ValueError:
            return "unknown"
        if grade.get("inputs") != inputs or grade.get("item_id") != item:
            return "unknown"
        chosen = dict(grade.get("votes") or {})
        if grade.get("reference"):
            chosen[reference] = grade["reference"]
        if not chosen:
            return "unknown"
        for judge, binding in chosen.items():
            model_id = votes.get((judge, item, binding.get("sha256")))
            if not model_id or model_id != binding.get("model_id"):
                return "unknown"
            observed.add(judge)
    return "live" if efforts and set(efforts) <= observed else "unknown"


def _live_count(record: dict[str, Any]) -> bool:
    # ScriptedCountClient also writes DEFAULT_COUNT_MODEL and approximation=False.
    # The model label alone cannot establish which adapter measured the tokens.
    execution = record.get("execution")
    return (
        isinstance(execution, dict)
        and record.get("status") == "counted"
        and record.get("approximation") is False
        and record.get("count_model") == DEFAULT_COUNT_MODEL
        and execution.get("provider") == "anthropic"
        and execution.get("api") == "anthropic-messages"
        and execution.get("model_id") == record["count_model"]
        and type(record.get("client_wrapped_tokens")) is int
        and record["client_wrapped_tokens"] >= 0
    )


class _RunMetadata:
    """Execution provenance shared by both renderers, from exported inputs only."""

    def __init__(self, bundle: RunBundle) -> None:
        run = bundle.run_dir
        self.summary: dict[str, Any] = optional_json(run, run / "grading" / "summary.json") or {}
        snapshot = _read_yaml(run, run / "snapshots" / "model-matrix.yml") or {}
        self.families: dict[str, dict[str, Any]] = {
            str(f["id"]): f for f in snapshot.get("model_families") or [] if "id" in f
        }
        self.inputs = optional_json(run, run / "grading/inputs/inputs.json") or {}
        self.frozen: dict[str, bytes] = {}
        panel = {}
        for key, filename in SNAPSHOT_FILES.items():
            if key not in self.inputs:
                continue
            try:
                body = artifact_bytes(run, run / "grading/inputs" / filename)
            except FileNotFoundError:
                continue
            bound = hashlib.sha256(body).hexdigest() == self.inputs[key]
            try:
                if filename.endswith(".yml"):
                    mapping = CapturedInput.from_bytes(body, key, "frozen report input").mapping
                    if key == "panel" and bound:
                        panel = mapping
                elif filename.endswith(".json"):
                    json_mapping(body, "frozen report input")
            except KeyLeakError:
                raise
            except BenchmarkValidationError:
                continue  # A malformed snapshot cannot establish grading provenance.
            if bound:
                self.frozen[key] = body
        self.judges: dict[str, dict[str, Any]] = panel.get("judges") or {}

        models = bundle.run_summary.get("models") or bundle.run_summary.get("families") or []
        self.answer_events = _events(run, run / "ledger-events.jsonl")
        self.grading_events = _events(run, run / "grading/ledger-events.jsonl")
        self.egress_data = read_evidence_json(run, "egress/summary.json")
        self.offline = load_offline_evidence(run)
        self.plan = read_evidence_json(run, "planned-cells.json") or {}
        self.seed = str(bundle.environment.get("seed"))
        self.answering, self.transcripts, self.discarded = _answering_evidence(
            bundle, self.families, models, self.answer_events
        )
        efforts = self.summary.get("efforts") or {}
        active = [self.judges.get(j, {}).get("api") for j in efforts]
        references = [j.get("api") for j in self.judges.values() if j.get("role") == "reference"]
        if len(active) != 3 or len(references) != 1:
            active.append(None)  # an incomplete panel cannot establish live grading
        self.grading = _execution_kind([*active, *references], grading=True)
        self.scoring = bundle.scoring
        self.tokens = bundle.tokens
        checked_versions: dict[Path, str | None] = {}
        if self.grading == "live":
            reference = next(
                j for j, spec in self.judges.items() if spec.get("role") == "reference"
            )
            if (
                self.inputs.keys() != self.frozen.keys()
                or not (SNAPSHOT_FILES.keys() - {"probes"}) <= self.frozen.keys()
            ):
                self.grading = "unknown"
            else:
                self.grading = _graded_execution(
                    run,
                    self.judges,
                    efforts,
                    reference,
                    self.inputs,
                    self.scoring,
                    self.grading_events,
                    checked_versions,
                )
        self.versions = {
            judge: _reported_versions(run, judge, checked_versions) for judge in efforts
        }
        self.tier = str(self.summary.get("tier", NOT_GRADED))

    def claim_evidence(self, claim: str, model: str) -> bool:
        systems = TOKEN_SYSTEMS if claim == "C2" else ("python-docs", "no-docs")
        records = {
            k: t
            for k, t in self.transcripts.items()
            if t.get("model_family_id") == model and t.get("system_id") in systems
        }
        cells = self.plan.get("cells")
        if not isinstance(cells, list) or not all(isinstance(p, dict) for p in cells):
            return False
        planned = {
            (p.get("competitor_id"), p.get("corpus_id")): (
                p.get("system_id"),
                model,
                p.get("corpus_id"),
                p.get("repeat"),
            )
            for p in cells
            if p.get("model_family_id") == model and p.get("system_id") in systems
        }
        if not records or records.keys() != planned.keys():
            return False
        scores: dict[CellKey, float] = {}
        counts: dict[CellKey, int] = {}
        for key, transcript in records.items():
            coord = planned[key]
            score = self.scoring.get(key, {}).get("score")
            if (
                coord != (transcript["system_id"], model, key[1], transcript.get("repeat"))
                or type(coord[3]) is not int
                or type(score) not in (int, float)
                or score not in (0, 0.5, 1)
            ):
                return False
            scores[coord] = score
            if claim == "C2":
                token = self.tokens.get(key, {})
                if not _live_count(token):
                    return False
                counts[coord] = token["client_wrapped_tokens"]

        # A copied verdict or interval cannot certify a different set of paired observations.
        if self.summary.get("resamples") != GATE_RESAMPLES:
            return False
        if claim == "C2":
            try:
                result = c2(counts, scores, set(scores), model=model, seed=self.seed)
            except ZeroDivisionError:
                return False  # No defined ratio for a zero-token peer.
        elif claim == "C3" and self.families.get(model, {}).get("arm") == "air-gapped":
            result = c3(
                scores, set(scores), model=model, seed=self.seed, offline_ok=self.offline_ok()
            )
        else:
            return False
        return result.passed is True and asdict(result) == self.summary["claims"][claim][model]

    def offline_ok(self) -> bool:
        local = {f for f, spec in self.families.items() if spec.get("arm") == "air-gapped"}
        attempts = [
            t
            for t in (*self.transcripts.values(), *self.discarded.values())
            if t.get("model_family_id") in local
        ]
        cells = self.plan.get("cells")
        if not isinstance(cells, list) or any(
            not isinstance(cell, dict) or not isinstance(cell.get("system_id"), str)
            for cell in cells
        ):
            return False
        jobs = {
            f"{cell['model_family_id']}--{cell['system_id']}"
            for cell in cells
            if cell.get("model_family_id") in local
        }
        egress = egress_from_mapping(self.egress_data)
        return bool(
            egress
            and egress["isolation_probe"] == "pass"
            and offline_verdict(
                self.offline,
                egress,
                expected_checks=len(attempts) + 2,
                airgap_jobs=sorted(jobs),
            ).ok
        )

    @property
    def grading_label(self) -> str:
        if not self.summary:
            return NOT_GRADED
        if self.grading == "live":
            return REQUIRED_WORDING
        return "scripted grading" if self.grading == "scripted" else "grading provenance unknown"

    @property
    def claim_blocker(self) -> str:
        stages = {"answering": self.answering}
        if self.summary:
            stages["grading"] = self.grading
        scripted = " and ".join(stage for stage, kind in stages.items() if kind == "scripted")
        unknown = " and ".join(stage for stage, kind in stages.items() if kind == "unknown")
        messages = []
        if scripted:
            messages.append(f"Demonstration results include scripted {scripted}.")
        if unknown:
            messages.append(f"Execution provenance is unknown for {unknown}.")
        return " ".join(messages)


class _Run(_RunMetadata):
    """Everything the full report reads, from the run directory only."""

    def __init__(self, bundle: RunBundle, matrix: ModelMatrix, report_dir: Path) -> None:
        super().__init__(bundle)
        run = bundle.run_dir
        self.dir, self.bundle, self.report_dir = run, bundle, report_dir
        # The caller's matrix is a display fallback, never evidence for a claim.
        if not self.families:
            self.families = {
                f.id: {"id": f.id, "provider": f.provider, "model_id": f.model_id, "arm": f.arm}
                for f in matrix.model_families
            }
        self.latency = bundle.latency
        self.egress = egress_from_mapping(self.egress_data, partial=True)
        self.systems = bundle.competitors
        groups: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
        for key, t in self.transcripts.items():
            groups[(str(t.get("model_family_id")), str(t.get("system_id")))].append(key)
        self.groups = dict(sorted(groups.items()))

    def link(self, *parts: str) -> str:
        return _relative_link(checked_path(self.dir, self.dir.joinpath(*parts)), self.report_dir)

    def airgapped(self) -> set[str]:
        return {fid for fid, f in self.families.items() if f.get("arm") == "air-gapped"}


def render_agentic_report(
    bundle: RunBundle, matrix: ModelMatrix, methodology_path: Path, report_path: Path
) -> str:
    run = _Run(bundle, matrix, report_path.parent)
    sections = (
        _header(run, methodology_path),
        _claims(run),
        _disclosure(run),
        _correctness(run),
        _tokens(run),
        _cost(run),
        _tool_use(run),
        _latency(run),
        _pilot(run),
        _budget(run),
        _failures(run),
        _reproducibility(run),
        _airgap(run),
    )
    return "\n".join(line for section in sections for line in [*section, ""]).rstrip() + "\n"


def _header(run: _Run, methodology_path: Path) -> list[str]:
    summary = run.bundle.run_summary
    models = summary.get("models") or list(run.families)
    rows = [
        [fid, str(f.get("provider")), str(f.get("model_id")), str(f.get("arm") or "")]
        for fid, f in sorted(run.families.items())
        if fid in models
    ]
    link = _relative_link(methodology_path, run.report_dir)
    return [
        "# Agentic benchmark report",
        "",
        DISCLAIMER,
        "",
        *([run.claim_blocker, ""] if run.claim_blocker else []),
        f"Grading outcome: {_esc(run.tier)} (G1–G4; {run.grading_label})",
        "",
        f"- Run {_esc(summary.get('run_id'))} at commit {_esc(summary.get('repo_commit'))}",
        f"- Methodology: [{methodology_path.name}]({link})",
        f"- Corpus sha256: `{run.bundle.corpus_hash}`",
        f"- Planned cells: {_esc(summary.get('planned_cells'))}; "
        f"completed trajectories: {len(run.transcripts)}; scored cells: "
        f"{sum(run.scoring.get(k, {}).get('score') is not None for k in run.transcripts)}",
        "",
        *_table(["Model family", "Provider", "Model id", "Arm"], rows),
    ]


def _claim_rows(
    run: _Run, claims: Mapping[str, Any], sensitive: Sequence[str], claim_id: str
) -> list[list[str]]:
    rows = []
    for model, claim in sorted(claims.items()):
        passed = claim.get("passed")
        verdict = (
            _verdict(passed) if passed is not None else str(claim.get("reason") or "not evaluable")
        )
        if passed is True and not run.claim_blocker and not run.claim_evidence(claim_id, model):
            verdict = "not evaluable: missing or inconsistent execution evidence"
        flag = "judge-sensitive" if f"{claim_id}/{model}" in sensitive else ""
        rows.append([model, _interval(claim.get("interval")), verdict, flag])
    return rows


def _planned_job_families(run: _Run) -> dict[str, set[str]]:
    cells = run.plan.get("cells")
    if not isinstance(cells, list) or len(cells) != run.bundle.run_summary.get("planned_cells"):
        return {}
    systems: dict[str, set[str]] = defaultdict(set)
    for cell in cells:
        if not isinstance(cell, dict):
            return {}
        family, system = cell.get("model_family_id"), cell.get("system_id")
        if not isinstance(system, str) or not isinstance(family, str) or family not in run.families:
            return {}
        systems[system].add(family)
    return systems


def _hosted_job_complete(run: _Run, family: str, system: str, job: Mapping[str, Any]) -> bool:
    if job.keys() != EGRESS_JOB_FIELDS:
        return False
    model = run.families[family]
    if model.get("api") == "scripted":
        return True
    provider_hosts = {f"{h}:{p}" for h, p in PROVIDER_HOSTS.get(model.get("provider", ""), ())}
    if provider_hosts.intersection(job["hosts_allowed"]):
        return True
    # A successful hosted answer turn contradicts a log that never reached its provider.
    keys = run.groups.get((family, system), [])
    if any(run.transcripts[k].get("status") == "succeeded" for k in keys):
        return False
    return not any(
        attempt.get("outcome") == "ok"
        for key in keys
        for call in run.transcripts[key].get("model_calls") or []
        if isinstance(call, dict)
        for attempt in call.get("attempts") or []
        if isinstance(attempt, dict)
    )


def _c1_hosts(jobs: Iterable[Mapping[str, Any]], field: str, complete: bool) -> str:
    hosts = ", ".join(sorted({h for job in jobs for h in job.get(field, [])}))
    if complete:
        return hosts or "none"
    return f"unknown (observed: {hosts})" if hosts else "unknown"


def _c1(run: _Run) -> list[str]:
    lines = ["### C1: no API key and no host but the model provider's at query time", ""]
    if run.egress is None:
        lines += ["C1 is not evaluable: no egress evidence in this run.", ""]
    evidence = run.egress or {}
    jobs = evidence.get("jobs", {})
    probe = evidence.get("isolation_probe", "unknown")
    expected = _planned_job_families(run)
    airgap = run.airgapped()
    offline_ok = (run.summary.get("offline") or {}).get("ok") is True and run.offline_ok()
    rows = []
    for system_id, system in sorted(run.systems.items()):
        families = expected.get(system_id, set())
        mine = {f: jobs.get(f"{f}--{system_id}", {}) for f in families}
        hosted = {f: job for f, job in mine.items() if run.families[f].get("arm") == "hosted"}
        hosted_complete = (
            probe == "pass"
            and bool(hosted)
            and all(_hosted_job_complete(run, f, system_id, job) for f, job in hosted.items())
        )
        complete = (
            probe == "pass"
            and bool(mine)
            and all(j.keys() == EGRESS_JOB_FIELDS for j in mine.values())
            and (not hosted or hosted_complete)
        )
        attempts = sum(j.get("lookup_package_docs_attempts", 0) for j in mine.values())
        tested = bool(families & airgap)
        offline = (
            "yes (air-gapped arm)"
            if tested and complete and offline_ok
            else "not shown"
            if tested
            else "not tested"
        )
        names = set(system.get("env") or {}) | set(system.get("pass_env") or [])
        rows.append(
            [
                system_id,
                "yes" if any("KEY" in n for n in names) else "no",
                _c1_hosts(hosted.values(), "hosts_allowed", hosted_complete),
                _c1_hosts(hosted.values(), "hosts_denied", hosted_complete),
                offline,
                str(attempts)
                if complete
                else f"unknown (observed: {attempts})"
                if attempts
                else "unknown",
            ]
        )
    return [
        *lines,
        *_table(
            [
                "System",
                "API key required",
                "Hosts contacted at query time (hosted)",
                "Hosts denied at query time (hosted)",
                "Works offline",
                "lookup_package_docs attempts",
            ],
            rows,
        ),
        "",
        "Calls the model made to `lookup_package_docs` are reported separately. "
        f"The isolation probe was {_esc(probe)}.",
    ]


def _claims(run: _Run) -> list[str]:
    graded = bool(run.summary)
    sensitive = run.summary.get("judge_sensitive") or []
    claims = run.summary.get("claims") or {}
    ungraded = "not evaluable: the run has not been graded (no grading/summary.json)."
    lines = [
        "## Claims",
        "",
        *_c1(run),
        "",
        "### C2: tokens per correct answer, python-docs over docs-fetch",
        "",
        f'Round 1\'s only peer is docs-fetch, so round 1 supports only "{ROUND_1_SCOPE}".',
        "",
    ]
    if not graded:
        lines.append(f"C2 is {ungraded}")
    else:
        lines += _table(
            ["Model", "Ratio python-docs ÷ docs-fetch", "Verdict", "G4"],
            _claim_rows(run, claims.get("C2") or {}, sensitive, "C2"),
        )
    lines += ["", "### C3: offline, with docs better than without", ""]
    if not graded:
        lines.append(f"C3 is {ungraded}")
    elif not claims.get("C3"):
        lines.append("No air-gapped model ran in this run, so C3 has no verdict.")
    else:
        lines += _table(
            ["Model", "Score difference with docs minus without", "Verdict", "G4"],
            _claim_rows(run, claims["C3"], sensitive, "C3"),
        )
        offline = run.summary.get("offline") or {}
        lines += [
            "",
            f"Offline evidence: {_verdict(offline.get('ok'))} "
            f"({_esc(offline.get('expected_checks'))} check rounds expected).",
        ]
        denied = offline.get("denied_attempts") or []
        if denied:
            lines += [
                "",
                "Denied egress attempts, each refused by the proxy, so nothing left the sandbox:",
            ]
            lines += [
                f"- {_esc(d.get('job_id'))}: {_esc(d.get('host'))} "
                "(refused; nothing left the sandbox)"
                for d in denied
            ]
    lines += [
        "",
        "### S1: correctness per system",
        "",
        "Reported as measured, with no pass rule: see Correctness below.",
        "",
        "### S2: repeat lookups",
        "",
        (
            "Reported as speed, never as tokens: see the cold and warm python-docs tool "
            "calls under Latency."
        ),
    ]
    return lines


def _reported_versions(root: Path, judge_id: str, checked: dict[Path, str | None]) -> list[str]:
    seen: set[str] = set()
    if not judge_id or Path(judge_id).name != judge_id or judge_id.startswith("."):
        return []
    for path in artifact_paths(root, root / "grading/votes/answers" / judge_id, "*/*/*/*.json"):
        if path not in checked:
            record = optional_json(root, path) or {}
            checked[path] = record.get("reported_model")
        if version := checked[path]:
            seen.add(str(version))
    return sorted(seen)


def _alpha_line(name: str, gate: Mapping[str, Any]) -> str:
    details = gate.get("details") or {}
    if gate.get("passed") is None and details.get("reason"):
        return f"- {name}: not evaluated. {_esc(details['reason'])}"
    return (
        f"- {name}: {_verdict(gate.get('passed'))}; alpha {_esc(details.get('alpha'))}, "
        f"95% interval {_esc(details.get('low'))} to {_esc(details.get('high'))}, "
        f"P(alpha below 0.667) {_esc(details.get('q_below'))}, undefined draws "
        f"{_esc(details.get('undefined_draws', 'n/a'))}"
    )


def _alpha_table(title: str, groups: Mapping[str, Any]) -> list[str]:
    rows = [
        [g, _fmt(r.get("alpha")), _fmt(r.get("low")), _fmt(r.get("high")), _fmt(r.get("q_below"))]
        for g, r in sorted(groups.items())
    ]
    return [f"{title}:", "", *_table(["Group", "Alpha", "Low", "High", "q"], rows), ""]


def _disclosure(run: _Run) -> list[str]:
    lines = ["## Grading disclosure", ""]
    summary = run.summary
    if not summary:
        return [*lines, "The run has not been graded."]
    efforts: dict[str, Any] = summary.get("efforts") or {}
    described = []
    for judge_id, effort in efforts.items():
        model = (run.judges.get(judge_id) or {}).get("model_id", "?")
        versions = ", ".join(_esc(v) for v in run.versions[judge_id]) or "n/a"
        described.append(
            f"{_esc(judge_id)} ({_esc(model)}, reported {versions}, "
            f"effort {_esc(effort or 'default')})"
        )
    if run.grading == "live":
        lines += [
            DISCLOSURE.format(judges="; ".join(described)),
            "",
            f"{REQUIRED_WORDING}.",
            "",
            "The air-gapped arm applies only to answering. Grading used the providers' "
            "hosted batch APIs.",
        ]
    else:
        lines += [
            f"Grading: {run.grading_label}. No human graded or calibrated any grade.",
            "",
            f"Recorded judges: {'; '.join(described) or 'unknown'}.",
        ]
    lines += [
        "",
        f"The run was graded by the composition {_esc(summary.get('composition'))}.",
        "",
        "### Judges and evaluated models",
        "",
    ]
    reference = next((j for j, s in run.judges.items() if s.get("role") == "reference"), None)
    evaluated = {fid for fid, _ in run.groups}
    for judge_id in [*efforts, *([reference] if reference else [])]:
        judge = run.judges.get(judge_id) or {}
        for fid, family in sorted(run.families.items()):
            if fid not in evaluated:
                continue
            judge_text = f"{_esc(judge_id)} ({_esc(judge.get('model_id'))})"
            if judge.get("model_id") and judge["model_id"] == family.get("model_id"):
                lines.append(
                    f"- {judge_text} is the evaluated model {_esc(family.get('model_id'))}: "
                    "it grades its own answers."
                )
            elif judge.get("provider") and judge["provider"] == model_lineage(fid, family):
                lines.append(
                    f"- {judge_text} shares a provider family ({_esc(judge['provider'])}) "
                    f"with the evaluated model {_esc(fid)}."
                )
    lines += ["", "### Frozen inputs", ""]
    for name, sha in sorted(run.inputs.items()):
        target = SNAPSHOT_FILES.get(name)  # a fixed file name, never run data
        link = (
            f" ([{target}]({run.link('grading', 'inputs', target)}))"
            if name in run.frozen and target
            else " (missing or digest mismatch)"
        )
        lines.append(f"- {_esc(name)}: {_esc(sha)}{link}")
    for title, key in (("Score mapping", "mapping_table"), ("Link masking", "mask_rules")):
        text = run.frozen.get(key, b"(missing or digest mismatch)").decode("utf-8")
        lines += ["", f"{title}:", "", *_fence(text)]
    lines += _probe_disclosure(run)
    gates = summary.get("gates") or {}
    breakdowns = summary.get("breakdowns") or {}
    lines += [
        "",
        "### Reliability (G2, G3) and robustness (G4)",
        "",
        f"Gate and claim bootstrap resamples: {_esc(summary.get('resamples', 'not recorded'))}.",
        "",
        _alpha_line("G2 panel reliability", gates.get("G2") or {}),
        _alpha_line("G3 reference agreement", gates.get("G3") or {}),
        "",
    ]
    for gate in ("G2", "G3"):
        by = breakdowns.get(gate) or {}
        lines += _alpha_table(f"{gate} per evaluated family", by.get("by_family") or {})
        lines += _alpha_table(f"{gate} per system", by.get("by_system") or {})
    sample = optional_json(run.dir, run.dir / "grading" / "reference-sample.json") or {}
    groups = sample.get("groups") or {}
    borrowed = sample.get("borrowed") or {}
    shortfall = sample.get("shortfall") or {}
    if groups or borrowed or shortfall:
        allocation_rows = []
        for group in sorted(groups.keys() | borrowed.keys() | shortfall.keys()):
            counts = groups.get(group, {})
            donors = ", ".join(
                f"{n} from {category}" for category, n in sorted(borrowed.get(group, {}).items())
            )
            allocation_rows.append(
                [
                    group,
                    _fmt(counts.get("allocated")),
                    _fmt(counts.get("taken")),
                    donors or ("none" if "borrowed" in sample else "n/a"),
                    _fmt(shortfall.get(group, 0) if "shortfall" in sample else None),
                ]
            )
        lines += [
            "G3 reference sample allocation:",
            "",
            *_table(
                ["Model/system", "Allocated", "Taken", "Borrowed", "Shortfall"], allocation_rows
            ),
            "",
        ]
    confusion = (breakdowns.get("G3") or {}).get("confusion") or {}
    lines += [
        "G3 confusion (final grade by reference grade):",
        "",
        *_table(
            ["Final", "Reference", "Answers"],
            [
                [f, r, str(n)]
                for f, row in sorted(confusion.items())
                for r, n in sorted(row.items())
            ],
        ),
        "",
    ]
    g4 = gates.get("G4") or {}
    lines.append(
        f"- G4 claim robustness: {_verdict(g4.get('passed'))}; judge-sensitive claims: "
        f"{', '.join(_esc(c) for c in summary.get('judge_sensitive') or []) or 'none'}"
    )
    rows = [
        [judge, f"{cid}/{model}", _verdict(c.get("passed"))]
        for judge, claims in sorted((summary.get("claims_by_judge") or {}).items())
        for cid, by in sorted(claims.items())
        for model, c in sorted(by.items())
    ]
    lines += ["", *_table(["Single judge", "Claim", "Verdict"], rows), ""]
    lines += _diagnostics(run)
    return lines


def _probe_disclosure(run: _Run) -> list[str]:
    entries = optional_json(run.dir, run.dir / "grading" / "disclosure" / "g1-entries.json") or {}
    composition = str(run.summary.get("composition"))
    entry = entries.get(composition) or {}
    gate = run.summary.get("gates", {}).get("G1") or {}
    details = (entry.get("gate") or {}).get("details") or {}
    lines = [
        "",
        "### Probes (G1)",
        "",
        f"G1 for this run's composition: {_verdict(gate.get('passed'))}. Every G1 entry "
        f"is kept in [grading/disclosure/]({run.link('grading', 'disclosure')}):",
        "",
    ]
    lines += [
        f"- {_esc(key)}: {_verdict((e.get('gate') or {}).get('passed'))}"
        for key, e in sorted(entries.items())
    ] or ["- none"]
    dropped = entry.get("dropped") or {}
    dropped_text = ", ".join(f"{_esc(t)} {_esc(n)}" for t, n in sorted(dropped.items()))
    misses = json.dumps(details.get("misses_by_generator") or {}, sort_keys=True)
    lines += [
        "",
        f"Dropped probes: {dropped_text or 'none'}. Misses by generator: {_esc(misses)}.",
        "",
    ]
    rows = [
        [
            t,
            _fmt(r.get("accuracy")),
            f"{_fmt(r['wilson'][0])} to {_fmt(r['wilson'][1])}",
            str(r.get("n")),
        ]
        for t, r in sorted((details.get("by_type") or {}).items())
    ]
    lines += [
        "Accuracy per probe type (Wilson 95% interval):",
        "",
        *_table(["Type", "Accuracy", "Interval", "Probes"], rows),
        "",
    ]
    rows = [
        [
            j,
            _fmt(r.get("accuracy")),
            f"{_fmt(r['wilson'][0])} to {_fmt(r['wilson'][1])}",
            str(r.get("inj_credit")),
        ]
        for j, r in sorted((details.get("by_judge") or {}).items())
    ]
    lines += [
        "Accuracy per judge:",
        "",
        *_table(["Judge", "Accuracy", "Interval", "INJ credit"], rows),
        "",
    ]
    repeat = (gate.get("details") or {}).get("held_out_test_retest") or {}
    for judge, result in sorted(repeat.items()):
        lines.append(
            f"- Held-out probe test–retest, {_esc(judge)}: alpha {_esc(result.get('alpha'))}, "
            f"{_esc(result.get('items'))}/{_esc(result.get('expected'))} paired, "
            f"{_esc(result.get('pass2_records'))} pass-2 records"
        )
    if not repeat:
        lines.append("- Held-out probe test–retest: no coverage reported")
    for evidence in entry.get("tuning") or []:
        for judge, t in sorted((evidence.get("judges") or {}).items()):
            tried = ", ".join(_esc(e) for e in t.get("tried") or []) or "none"
            lines.append(
                f"- Tuning, {_esc(judge)}: efforts tried {tried}; chosen "
                f"{_esc(t.get('chosen') or 'none')}"
            )
    return lines


def _diagnostics(run: _Run) -> list[str]:
    summary = run.summary
    diag = summary.get("diagnostics") or {}
    lines = ["### Diagnostics", ""]
    retest = diag.get("test_retest") or {}
    lines += [
        f"- Real-answer test–retest alpha, {_esc(j)}: {_esc(r.get('alpha'))}"
        for j, r in sorted(retest.items())
    ] or ["- Real-answer test–retest: no paired scores reported"]
    for key, interval in sorted((diag.get("self_preference") or {}).items()):
        if interval is None:
            value = "not estimable"
        elif interval.get("low") is None or interval.get("high") is None:
            value = f"{_fmt(interval.get('point'))}, 95% CI unknown"
        else:
            value = _interval(interval)
        lines.append(f"- Self-preference index, {_esc(key)}: {_esc(value)}")
    lines.append(f"- Length–grade correlation: {_esc(diag.get('length_grade_correlation'))}")
    shares = diag.get("label_shares") or {}
    lines += [
        "",
        "Label shares per (model, system):",
        "",
        *_table(
            ["Model/system", "Labels"],
            [
                [g, ", ".join(f"{k} {v:.0%}" for k, v in sorted(s.items()))]
                for g, s in sorted(shares.items())
            ],
        ),
        "",
    ]
    suspects = diag.get("suspects") or {}
    lines += [
        "Injection suspects per (model, system):",
        "",
        *_table(
            ["Model/system", "Suspect answers", "Reference grade differs from the panel's"],
            [[g, str(s.get("suspect")), str(s.get("differs"))] for g, s in sorted(suspects.items())]
            or [["none", "0", "0"]],
        ),
        "",
    ]
    lines.append("Grader failures:")
    for judge, stats in sorted((diag.get("per_judge") or {}).items()):
        withheld = stats.get("withheld") or {}
        withheld_text = json.dumps(withheld, sort_keys=True) if withheld else "none"
        lines.append(
            f"- {_esc(judge)}: {_esc(stats.get('records'))} records; withheld {_esc(withheld_text)}"
        )
    for judge, reasons in sorted((diag.get("no_final_record") or {}).items()):
        for reason, items in sorted(reasons.items()):
            lines.append(
                f"- {_esc(judge)}: {len(items)} items with no final pass-1 record, last "
                f"not_run reason {_esc(reason)}: {', '.join(_esc(i) for i in items)}; "
                "they block the reference grader until fixed"
            )
    audit = summary.get("key_audit") or {}
    flagged = ", ".join(_esc(q) for q in audit.get("flagged") or [])
    lines += [
        "",
        f"Answer-key audit: {_esc(audit.get('audited', 0))} keys audited by the reference "
        f"grader; flagged: {flagged or 'none'}.",
        "The audit checks whether the cited docs support the frozen key. When a question has "
        "several correct answers, a valid alternative may earn less credit if the key omits it.",
    ]
    if summary.get("claims_without_flagged_keys"):
        for cid, by in sorted(summary["claims_without_flagged_keys"].items()):
            for model, c in sorted(by.items()):
                lines.append(
                    f"- Without flagged questions, {_esc(cid)}/{_esc(model)}: "
                    f"{_verdict(c.get('passed'))}, {_esc(_interval(c.get('interval')))}"
                )
    spend = summary.get("spend") or {}
    lines += [
        "",
        "Grading cost, kept apart from the benchmark's own token metrics:",
        "",
        *_table(
            ["Provider", "Booked (USD)", "Per graded answer"],
            [
                [p, v, (spend.get("per_graded_answer") or {}).get(p, "n/a")]
                for p, v in sorted((spend.get("by_provider") or {}).items())
            ],
        ),
    ]
    return lines


def _scores(run: _Run, keys: Sequence[tuple[str, str]]) -> tuple[list[float], int]:
    scores = [run.scoring[k].get("score") for k in keys if k in run.scoring]
    return [float(s) for s in scores if s is not None], sum(1 for s in scores if s is None)


def _correctness(run: _Run) -> list[str]:
    rows = []
    for (family, system), keys in run.groups.items():
        scored, pending = _scores(run, keys)
        if pending:
            rows.append([f"{family}/{system}", f"pending ({pending} cells)", "n/a", "n/a", "n/a"])
            continue
        per_q: dict[str, list[float]] = defaultdict(list)
        for k in keys:
            if run.scoring.get(k, {}).get("score") is not None:
                per_q[k[1]].append(float(run.scoring[k]["score"]))

        def mean(questions: Sequence[str], per_q: Mapping[str, list[float]] = per_q) -> float:
            values = [v for q in questions for v in per_q[q]]
            return sum(values) / len(values) if values else 0.0

        interval = (
            paired_bootstrap(
                sorted(per_q),
                mean,
                seed=f"{run.seed}/report/{family}/{system}",
                resamples=REPORT_RESAMPLES,
            )
            if per_q
            else None
        )
        grounded = [bool(run.scoring[k].get("grounded")) for k in keys if k in run.scoring]
        ungrounded = sum(1 for k in keys if run.scoring.get(k, {}).get("correct_but_ungrounded"))
        rows.append(
            [
                f"{family}/{system}",
                _interval(None if interval is None else vars(interval)),
                str(len(scored)),
                f"{sum(grounded) / len(grounded):.0%}" if grounded else "n/a",
                str(ungrounded),
            ]
        )
    by_category: dict[tuple[str, str], list[float]] = defaultdict(list)
    for cell in run.bundle.cells:
        if cell.score is not None:
            t = run.transcripts.get((cell.competitor_id, cell.corpus_id), {})
            group = f"{t.get('model_family_id')}/{t.get('system_id')}"
            by_category[(group, cell.category)].append(cell.score)
    category_rows = [
        [g, c, _fmt(sum(v) / len(v)), str(len(v))] for (g, c), v in sorted(by_category.items())
    ]
    return [
        "## Correctness",
        "",
        f"Mean score per (model, system) with a 95% bootstrap interval over questions "
        f"({REPORT_RESAMPLES} resamples); {run.grading_label}.",
        "",
        *_table(
            [
                "Model/system",
                "Mean score",
                "Scored cells",
                "Grounded rate",
                "correct_but_ungrounded",
            ],
            rows,
        ),
        "",
        "Per category:",
        "",
        *_table(["Model/system", "Category", "Mean score", "Cells"], category_rows),
    ]


def _tokens(run: _Run) -> list[str]:
    rows = []
    count_models: set[str] = set()
    for (family, system), keys in run.groups.items():
        if system not in TOKEN_SYSTEMS:
            continue
        records = [run.tokens[k] for k in keys if k in run.tokens]
        count_models |= {str(r["count_model"]) for r in records if r.get("count_model")}
        counted = [r for r in records if r.get("status") == "counted"]
        scored, pending = _scores(run, keys)
        if len(counted) < len(keys):
            rows.append(
                [family, system, f"n/a ({len(keys) - len(counted)} uncounted)", "n/a", "n/a"]
            )
            continue
        total = sum(int(r.get("client_wrapped_tokens") or 0) for r in counted)
        payload = sum(int(r.get("raw_payload_tokens") or 0) for r in counted)
        credit = sum(scored)
        per = "pending" if pending else (f"{total / credit:.1f}" if credit else "no correct answer")
        rows.append([family, system, str(total), per, str(payload)])
    return [
        "## Normalized tokens",
        "",
        "Claude Tokens (Normalized Payload), counted after the run with the count model "
        f"{', '.join(_esc(m) for m in sorted(count_models)) or 'n/a'}. The count-tokens "
        "endpoint is documented "
        f"as an estimate. These counts are {NOT_COMPARABLE}. Only python-docs and docs-fetch "
        "have token rows: the correctness floor injects no documentation.",
        "",
        *_table(
            ["Model", "System", "Normalized tokens", "Per correct answer", "Docs payload tokens"],
            rows,
        ),
    ]


def _cost(run: _Run) -> list[str]:
    prices = _read_yaml(run.dir, run.dir / "snapshots/price-snapshot.yml") or {}
    try:
        snapshot = price_snapshot_from_mapping(prices)
    except BenchmarkValidationError as exc:
        return ["## Cost per correct answer", "", f"Not computed: {_esc(str(exc))}"]
    totals: dict[str, Decimal] = defaultdict(Decimal)
    reasoning: Counter[str] = Counter()
    cached: Counter[str] = Counter()
    credit: dict[str, float] = defaultdict(float)
    pending: set[str] = set()
    for key, t in run.transcripts.items():
        family = str(t.get("model_family_id"))
        price = snapshot.models.get(str(run.families.get(family, {}).get("model_id")))
        for call in t.get("model_calls") or []:
            if call.get("buckets") and price is not None:
                buckets = Buckets(**call["buckets"])
                totals[family] += actual_cost(price, buckets)
                reasoning[family] += buckets.reasoning
                cached[family] += buckets.cached_input
        score = run.scoring.get(key, {}).get("score")
        if score is None:
            pending.add(family)
        else:
            credit[family] += float(score)
    rows = []
    for family, cost in sorted(totals.items()):
        if family in pending:
            per_correct = "pending"
        elif credit[family]:
            per_correct = f"{cost / Decimal(str(credit[family])):.4f}"
        else:
            per_correct = "no correct answer"
        rows.append(
            [
                family,
                f"{cost:.4f}",
                per_correct,
                str(reasoning[family]),
                str(cached[family]),
            ]
        )
    return [
        "## Cost per correct answer",
        "",
        (
            "Per model, from provider-reported usage and the run's price snapshot. "
            "Reasoning tokens are shown for information (already inside output); cached "
            "tokens are disclosed. Never compared across models."
        ),
        *(["Known spend is shown while a model has grades pending."] if pending else []),
        "",
        *_table(
            [
                "Model",
                "Cost (USD)",
                "Per correct answer",
                "Reasoning tokens",
                "Cached input tokens",
            ],
            rows,
        ),
    ]


def _tool_use(run: _Run) -> list[str]:
    rows = []
    for (family, system), keys in run.groups.items():
        ts = [run.transcripts[k] for k in keys]
        tool_counts = [len(t.get("tool_calls") or []) for t in ts]
        invalid = sum(
            1
            for t in ts
            for c in t.get("tool_calls") or []
            if c.get("is_error") or c.get("raw_arguments") is not None
        )
        caps = sum(
            1 for t in ts if str((t.get("error") or {}).get("category") or "").startswith("cap_")
        )
        rows.append(
            [
                f"{family}/{system}",
                f"{sum(1 for n in tool_counts if n) / len(ts):.0%}",
                _fmt(statistics.mean(tool_counts), 2),
                _fmt(statistics.mean(len(t.get("model_calls") or []) for t in ts), 2),
                str(invalid),
                str(caps),
            ]
        )
    return [
        "## Tool use",
        "",
        *_table(
            [
                "Model/system",
                "Tool-use rate",
                "Tool calls per trajectory",
                "Model calls per trajectory",
                "Invalid calls",
                "Cap hits",
            ],
            rows,
        ),
    ]


def _latency(run: _Run) -> list[str]:
    rows = []
    for (family, system), keys in run.groups.items():
        records = [run.latency[k] for k in keys if k in run.latency]
        values = [float(r["latency_ms"]) for r in records if r.get("latency_ms") is not None]
        timing = [r.get("timing") or {} for r in records]
        rows.append(
            [
                f"{family}/{system}",
                *(
                    _median([float(t.get(f"{part}_ms") or 0.0) for t in timing])
                    for part in ("model", "tool", "overhead")
                ),
                _median(values),
                _fmt(_percentile(values, 0.95), 1) if values else "n/a",
                _fmt(min(values), 1) if values else "n/a",
                _fmt(max(values), 1) if values else "n/a",
            ]
        )
    calls = [
        c
        for t in run.transcripts.values()
        if t.get("system_id") == "python-docs"
        for c in t.get("tool_calls") or []
        if c.get("wall_ms") is not None
    ]
    cold = [float(c["wall_ms"]) for c in calls if not c.get("warm")]
    warm = [float(c["wall_ms"]) for c in calls if c.get("warm")]
    env = run.bundle.environment
    lines = [
        "## Latency",
        "",
        *_table(
            [
                "Model/system",
                "Model ms",
                "Tool ms",
                "Overhead ms",
                "Median ms",
                "p95 ms",
                "Min ms",
                "Max ms",
            ],
            rows,
        ),
        "",
        f"python-docs tool calls: cold median {_median(cold)} ms ({len(cold)} calls), warm "
        f"median {_median(warm)} ms ({len(warm)} calls).",
    ]
    if env.get("index_size_bytes") or env.get("index_build_s"):
        lines.append(
            f"Index size {_esc(env.get('index_size_bytes'))} bytes; build time "
            f"{_esc(env.get('index_build_s'))} s."
        )
    return lines


def _pilot(run: _Run) -> list[str]:
    families = run.bundle.run_summary.get("families") or {}
    lines = ["## Pilot rules", "", "Output-cap stops per answer turn (the 1% rule):", ""]
    for family, stats in sorted(families.items()):
        turns, stops = int(stats.get("answer_turns") or 0), int(stats.get("output_cap_stops") or 0)
        share = stops / turns if turns else 0.0
        flag = (
            "; over the 1% rule: raise the output cap before the full run"
            if share > OUTPUT_CAP_RULE
            else ""
        )
        lines.append(f"- {_esc(family)}: {stops} of {turns} answer turns ({share:.1%}){flag}")
    ledger = run.bundle.run_summary.get("ledger") or {}
    grading = Counter(
        e.get("provider")
        for e in run.grading_events
        if e.get("event") == "settle" and e.get("input_bound_exceeded")
    )
    providers = sorted(set(ledger) | {str(p) for p in grading})
    rows = [
        [p, str((ledger.get(p) or {}).get("input_bound_exceeded", 0)), str(grading[p])]
        for p in providers
    ]
    return [
        *lines,
        "",
        "Reported input above its bound (one token per byte):",
        "",
        *_table(["Provider", "Answering", "Grading"], rows),
    ]


def _twice(events: Sequence[Mapping[str, Any]]) -> Counter[str]:
    return Counter(
        str(e.get("provider"))
        for e in events
        if e.get("event") == "settle"
        and e.get("actual") is None
        and Decimal(str(e.get("booked") or 0)) > 0
    )


def _counts(counter: Counter[str]) -> str:
    return _esc(json.dumps(dict(counter), sort_keys=True)) if counter else "none"


def _budget(run: _Run) -> list[str]:
    summary = run.bundle.run_summary
    ledger = summary.get("ledger") or {}
    overruns = sorted(p for p, s in ledger.items() if (s or {}).get("stopped") == "overrun")
    answering, grading = _twice(run.answer_events), _twice(run.grading_events)
    booked = {
        e.get("unit_id"): Decimal(str(e.get("booked") or 0))
        for e in run.answer_events
        if e.get("event") == "settle"
    }
    lines = [
        "## Budget events",
        "",
        f"- Overrun stops: {', '.join(_esc(p) for p in overruns) or 'none'}",
        f"- Units booked at twice their bound, answering: {_counts(answering)}; grading: "
        f"{_counts(grading)}",
    ]
    for d in summary.get("discarded") or []:
        name = f"{d.get('corpus_id')}--attempt{d.get('attempt')}.json"
        record = run.discarded.get((str(d.get("cell")), name), {})
        units = {
            a.get("unit_id")
            for c in record.get("model_calls") or []
            for a in c.get("attempts") or []
        }
        spend = (
            str(sum((booked.get(u, Decimal(0)) for u in units), Decimal(0)))
            if record
            else "unknown"
        )
        lines.append(
            f"- Discarded {_esc(d.get('cell'))}/{_esc(d.get('corpus_id'))} attempt "
            f"{_esc(d.get('attempt'))}: {_esc(d.get('reason'))}, spend {spend}"
        )
    for n in summary.get("not_run") or []:
        lines.append(
            f"- Not run {_esc(n.get('cell'))}/{_esc(n.get('corpus_id'))}: {_esc(n.get('reason'))}"
        )
    for w in summary.get("worker_errors") or []:
        lines.append(f"- Worker error in {_esc(w.get('job_id'))}: {_esc(w.get('reason'))}")
    for family, stats in sorted((summary.get("families") or {}).items()):
        if float(stats.get("wall_s") or 0) > 24 * 3600:
            lines.append(f"- {_esc(family)} ran longer than 24 hours from first to last trajectory")
    return [*lines, "", RESIDUAL_RISK]


def _failures(run: _Run) -> list[str]:
    lines = ["## Failures", ""]
    for (cell, qid), t in sorted(run.transcripts.items()):
        if t.get("status") == "failed":
            error = t.get("error") or {}
            lines.append(
                f"- {_esc(cell)}/{_esc(qid)}: {_esc(error.get('category'))}: "
                f"{_esc(error.get('message'))}"
            )
    for (cell, qid), r in sorted(run.tokens.items()):
        if r.get("status") == "failed":
            lines.append(f"- token count {_esc(cell)}/{_esc(qid)}: {_esc(r.get('error'))}")
    return lines if len(lines) > 2 else [*lines, "No failed cell or token count."]


def _reproducibility(run: _Run) -> list[str]:
    summary, env = run.bundle.run_summary, run.bundle.environment
    prices = summary.get("price_snapshot") or {}
    versions: dict[str, set[str]] = defaultdict(set)
    for t in run.transcripts.values():
        for c in t.get("model_calls") or []:
            if c.get("reported_model"):
                versions[str(t.get("model_family_id"))].add(str(c["reported_model"]))
    index = sorted(
        {
            str(i)
            for p in artifact_paths(run.dir, run.dir / "systems", "*.json")
            if (i := (optional_json(run.dir, p) or {}).get("index_sha256"))
        }
    )
    count_models = sorted(
        {str(r["count_model"]) for r in run.tokens.values() if r.get("count_model")}
    )
    sdk = json.dumps(env.get("client_versions") or {}, sort_keys=True)
    lines = [
        "## Reproducibility",
        "",
        f"- Price snapshot captured {_esc(prices.get('captured_on'))}, expires "
        f"{_esc(prices.get('expires_on'))}",
        *[
            f"- Reported model versions, {_esc(f)}: {', '.join(_esc(x) for x in sorted(v))}"
            for f, v in sorted(versions.items())
        ],
        f"- SDK versions: {_esc(sdk)}",
        f"- Count model: {', '.join(_esc(m) for m in count_models) or 'n/a'}",
        f"- Index sha256: {', '.join(_esc(i) for i in index) or 'n/a'}",
        f"- Seed: {_esc(env.get('seed'))}",
        f"- Judge prompt sha256: {_esc(run.inputs.get('judge_prompt', 'n/a'))}; judge schema "
        f"sha256: {_esc(run.inputs.get('judge_schema', 'n/a'))}",
    ]
    if env.get("image_digests"):
        digests = json.dumps(env["image_digests"], sort_keys=True)
        lines.append(f"- Image digests: {_esc(digests)}")
    return lines


def _airgap(run: _Run) -> list[str]:
    lines = ["## Air-gapped arm", ""]
    airgap = run.airgapped() & {f for f, _ in run.groups}
    if not airgap:
        return [*lines, "No air-gapped model ran in this run."]
    evidence = run.offline
    if evidence is None:
        lines.append("Host checks: no airgap/checks.json in this run.")
    else:
        hosts = ", ".join(_esc(h) for h in evidence.internet_hosts) or "none"
        lines.append(
            f"Host checks: all passed {evidence.all_passed}, {evidence.checks} rounds, "
            f"internet hosts reached: {hosts}."
        )
    for family in sorted(airgap):
        for (f, system), keys in run.groups.items():
            if f == family:
                scored, pending = _scores(run, keys)
                mean = _fmt(sum(scored) / len(scored)) if scored and not pending else "pending"
                lines.append(f"- {_esc(family)}/{_esc(system)}: mean score {mean}")
    return lines


def render_agentic_readme_summary(
    bundle: RunBundle, methodology_path: Path, summary_path: Path
) -> str:
    run = _RunMetadata(bundle)
    summary = run.summary
    tier = run.tier
    link = _relative_link(methodology_path, summary_path.parent)
    lines = [
        "# Agentic benchmark summary",
        "",
        f"- Methodology: [{methodology_path.name}]({link})",
        f"- Grading: {run.grading_label}",
        "",
    ]
    if run.claim_blocker:
        lines += [run.claim_blocker, ""]
    if tier != "Validated":
        return "\n".join([*lines, f"No public claim: grading outcome is {_esc(tier)}."]) + "\n"
    if run.claim_blocker:
        return "\n".join([*lines, "No public claim."]) + "\n"
    sensitive = set(summary.get("judge_sensitive") or [])
    claims = [
        (cid, model, c)
        for cid, by in sorted((summary.get("claims") or {}).items())
        for model, c in sorted(by.items())
        if c.get("passed") is True and f"{cid}/{model}" not in sensitive
    ]
    if not claims:
        return (
            "\n".join(
                [*lines, "No public claim: no claim passed outside the judge-sensitive ones."]
            )
            + "\n"
        )
    for cid, model, claim in claims:
        label = _esc(model)
        if not run.claim_evidence(cid, model):
            lines.append(
                f"No public claim for {_esc(cid)}/{label}: "
                "missing or inconsistent execution evidence."
            )
            continue
        interval = _esc(_interval(claim.get("interval")))
        if cid == "C2":
            lines.append(
                f"- {label}: python-docs-mcp-server uses fewer normalized tokens per "
                f"correct answer than docs-fetch (ratio {interval}). Round 1 supports only "
                f'"{ROUND_1_SCOPE}".'
            )
        elif cid == "C3":
            lines.append(
                f"- {label}: with the internet unreachable, the model answers the "
                "corpus better with python-docs-mcp-server than alone (score "
                f"difference {interval})."
            )
    return "\n".join(lines) + "\n"
