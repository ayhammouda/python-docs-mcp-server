"""Narrow #63 admission gate; no open-ended grading exemption.

Registered inputs are repository files, never a user-supplied scoring policy.
Evidence checks establish consistency, not authenticity of unsigned local files.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal, DecimalException
from pathlib import Path
from typing import TYPE_CHECKING, Any

import benchmarks
from benchmarks.agentic.host import Launcher, WorkerJob
from benchmarks.agentic.inputs import (
    CapturedInput,
    CapturedJSON,
    json_mapping,
    read_input_bytes,
    read_json,
    read_jsonl,
)
from benchmarks.agentic.ledger import PROVIDER_CAPS
from benchmarks.agentic.live import ReferenceRate, reference_rates
from benchmarks.agentic.loop import HOSTED_CAPS
from benchmarks.agentic.prompt import SYSTEM_PROMPT
from benchmarks.agentic.protocol import KeyLeakError
from benchmarks.agentic.reference import ReferenceTranscripts, nonnegative_decimal
from benchmarks.agentic.sandbox import ContainerLauncher
from benchmarks.runner import BenchmarkValidationError

if TYPE_CHECKING:
    from benchmarks.agentic.run import AgenticConfig

REGISTERED = Path(benchmarks.__file__).resolve().parent.parent / "docs/benchmarks/structured"
SEED = 411122497
PILOT_IDS = (
    "AP-001",
    "AP-003",
    "CO-009",
    "CO-005",
    "XV-012",
    "XV-010",
    "EX-014",
    "EX-008",
    "PE-005",
    "PE-003",
)
MODELS = frozenset({"claude-sonnet-5-5", "gpt-6.1-sol", "gemini-3.8-flash"})
EXECUTION = {
    "claude-sonnet-5-5": ("anthropic", "anthropic-messages"),
    "gpt-6.1-sol": ("openai", "openai-responses"),
    "gemini-3.8-flash": ("google", "gemini-generate-content"),
}
SYSTEMS = frozenset({"python-docs", "docs-fetch", "no-docs"})


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BenchmarkValidationError(f"structured study: {message}")


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@dataclass(frozen=True)
class StructuredStudy:
    """Captured registered inputs and the deterministic scorer used for this run."""

    items: dict[str, str]
    corpus_ids: tuple[str, ...]
    prompts: dict[str, str]
    sources: dict[str, bytes]
    seed: int = SEED

    @classmethod
    def load(cls, key_path: Path, corpus: CapturedInput) -> StructuredStudy:
        # Import lazily: legacy runs must never depend on the structured package.
        from benchmarks import structured

        key = read_input_bytes(key_path, "structured-items.json", "structured key")
        # The items file is a JSON array; the owning reader scans decoded strings too.
        json_mapping(b'{"items":' + key + b"}", "structured key", inspect_strings=True)
        registered = read_input_bytes(REGISTERED / "items.json", "items.json", "registered key")
        source = CapturedInput.read(
            REGISTERED.parent / "corpus.yml", "source-corpus.yml", "registered source corpus"
        )
        _require(key == registered, "key must match the registered items exactly")
        try:
            rendered = structured.render_corpus(source.content, key)
        except structured.StructuredError as exc:
            raise BenchmarkValidationError("structured study: invalid registered items") from exc
        _require(corpus.content == rendered, "corpus must match the registered renderer exactly")
        prompts = {q["id"]: q["prompt"] for q in corpus.mapping["questions"]}
        items = {q["id"]: q["answer_key"] for q in corpus.mapping["questions"]}
        _require(
            len(prompts) == 50 and set(PILOT_IDS) <= prompts.keys(),
            "expected 50 frozen questions including the ten registered pilot IDs",
        )
        sources = {
            "corpus.yml": corpus.content,
            "structured-items.json": key,
            source.name: source.content,
            "structured-protocol.md": read_input_bytes(
                REGISTERED / "PROTOCOL.md", "structured protocol", "structured protocol"
            ),
            "structured-scorer.py": read_input_bytes(
                Path(structured.__file__), "structured scorer", "structured scorer"
            ),
            "system-prompt.txt": SYSTEM_PROMPT.encode(),
        }
        return cls(items, tuple(prompts), prompts, sources)

    def check_options(
        self, config: AgenticConfig, caps: Mapping[str, Decimal], shared_ledger: Path
    ) -> None:
        _require(config.repeats == 3, "exactly three repeats are required")
        _require(config.seed in (None, self.seed), "the registered seed cannot change")
        _require(caps == PROVIDER_CAPS, "the shared spending caps cannot change")
        _require(
            config.ledger_dir.resolve() == shared_ledger.resolve(),
            "use the default shared ledger; separate study ledgers are not supported",
        )
        _require(not config.acknowledge_overrun, "resolve overruns outside this study")

    def selection(self, requested: tuple[str, ...] | None, *, full: bool) -> tuple[str, ...]:
        expected = self.corpus_ids if full else PILOT_IDS
        _require(
            requested is None
            or (len(requested) == len(expected) and set(requested) == set(expected)),
            "run the exact ten-question pilot or the entire full corpus; no split runs",
        )
        return expected

    def binding(
        self,
        jobs: Sequence[WorkerJob],
        matrix: CapturedInput,
        manifest: CapturedInput,
        prices: CapturedInput,
        launcher: Launcher,
        seed: int,
    ) -> dict[str, Any]:
        _require(
            len(jobs) == 9
            and {(j.family.id, j.system.id) for j in jobs}
            == {(m, s) for m in MODELS for s in SYSTEMS},
            "all nine registered model/system pairs must run together",
        )
        _require(
            all(
                j.family.arm == "hosted"
                and j.family.model_id == j.family.id
                and (j.family.provider, j.family.api) == EXECUTION[j.family.id]
                for j in jobs
            ),
            "the structured study requires the registered hosted execution identities",
        )
        _require(isinstance(launcher, ContainerLauncher), "the container launcher is required")
        assert isinstance(launcher, ContainerLauncher)
        config = launcher.sandbox.config
        try:
            with config.index_path.open("rb") as stream:
                index_sha = hashlib.file_digest(stream, "sha256").hexdigest()
        except OSError as exc:
            raise BenchmarkValidationError("structured study: cannot hash the docs index") from exc
        return {
            "schema": "structured-reference-v1",
            "sha256": {
                **{name: _sha(content) for name, content in self.sources.items()},
                **{source.name: _sha(source.content) for source in (matrix, manifest, prices)},
            },
            "seed": seed,
            "repeats": 3,
            "models": sorted(MODELS),
            "systems": sorted(SYSTEMS),
            "caps": asdict(HOSTED_CAPS),
            "image": config.image,
            "index_sha256": index_sha,
        }

    def write_snapshots(self, directory: Path) -> None:
        for name, content in self.sources.items():
            (directory / name).write_bytes(content)

    def score(self, record: dict[str, Any], corpus_id: str) -> dict[str, Any]:
        from benchmarks import structured

        letter = structured.parse_answer(record["answer"])
        return {
            "method": "structured-local-v1",
            "status": "scored",
            "score": int(record["status"] == "succeeded" and letter == self.items[corpus_id]),
            "format_valid": letter is not None,
            "requires_manual_scoring": False,
            "notes": "Deterministic factual choice; citations do not affect credit.",
        }

    def verified_rates(self, run: Path, binding: dict[str, Any]) -> dict[str, ReferenceRate]:
        """Recompute readiness; cached pass flags and accuracy never authorize a run."""
        try:
            rates = reference_rates(run)
            self._verify_pilot(run, binding)
            _require(set(rates) == MODELS, "reference lacks measurements for all hosted models")
            return rates
        except (KeyLeakError, BenchmarkValidationError):
            raise
        except (OSError, ValueError, KeyError, TypeError, DecimalException, OverflowError) as exc:
            raise BenchmarkValidationError("structured study: invalid pilot evidence") from exc

    def _verify_pilot(self, run: Path, binding: dict[str, Any]) -> None:
        summary = read_json(run / "run-summary.json", "structured pilot summary")
        _require(summary["structured"]["stage"] == "pilot", "reference must be a pilot")
        _require(summary["structured"]["binding"] == binding, "pilot/full input or option mismatch")
        _require(summary["dry_run"] is False, "reference must be an executed pilot")
        _require(summary["mode"] == "agentic", "reference must be agentic")
        _require(summary["seed"] == self.seed and summary["repeats"] == 3, "pilot options changed")
        for field, values in (("models", MODELS), ("systems", SYSTEMS), ("corpus_ids", PILOT_IDS)):
            _require(
                len(summary[field]) == len(values) and set(summary[field]) == set(values),
                f"pilot {field} do not cover the registered selection",
            )
        for field in ("planned_cells", "scored_cells", "correctness_denominator_cells"):
            _require(type(summary[field]) is int and summary[field] == 270, f"pilot {field} != 270")
        _require(
            not summary["not_run"] and not summary["worker_errors"],
            "pilot has missing work or worker errors",
        )
        for name, digest in binding["sha256"].items():
            content = read_input_bytes(run / "snapshots" / name, name, "pilot snapshot")
            _require(_sha(content) == digest, f"pilot snapshot mismatch: {name}")
        expected = {
            (f"{system}--{model}--r{repeat}", qid): {
                "cell": f"{system}--{model}--r{repeat}",
                "competitor_id": f"{system}--{model}--r{repeat}",
                "corpus_id": qid,
                "system_id": system,
                "model_family_id": model,
                "repeat": repeat,
            }
            for system in SYSTEMS
            for model in MODELS
            for repeat in range(1, 4)
            for qid in PILOT_IDS
        }
        planned = read_json(run / "planned-cells.json", "structured pilot plan")["cells"]
        _require(len(planned) == 270, "pilot plan must contain exactly 270 cells")
        pending = dict(expected)
        for entry in planned:
            _require(
                type(entry["repeat"]) is int
                and pending.pop((entry["cell"], entry["corpus_id"]), None) == entry,
                "pilot plan has duplicate, changed or unexpected cells",
            )
        _require(not pending, "pilot plan is incomplete")
        pending = dict(expected)
        valid: Counter[tuple[str, str]] = Counter()
        statuses: Counter[str] = Counter()
        inventory = ReferenceTranscripts(summary)
        attempts: dict[tuple[str, str], set[int]] = {}
        terminal_attempts: dict[tuple[str, str], int] = {}
        for path in sorted((run / "transcripts").rglob("*.json")):
            captured = CapturedJSON.read(path, "structured pilot transcript")
            record = captured.mapping
            inventory.verify(path.relative_to(run).as_posix(), captured.content, record)
            qid = record["corpus_id"]
            coordinate = (record["competitor_id"], qid)
            _require(
                pending.pop(coordinate, None) is not None, "unexpected or duplicate pilot cell"
            )
            _require(path.name == f"{qid}.json", "pilot transcript filename mismatch")
            attempts[coordinate] = {record["attempt"]}
            terminal_attempts[coordinate] = record["attempt"]
            self._check_prompt(record, qid)
            status = record["status"]
            _require(status in {"succeeded", "failed"}, "unknown pilot status")
            _require(
                (record["error"] is None) == (status == "succeeded"), "pilot status/error mismatch"
            )
            statuses[status] += 1
            result = self.score(record, qid)
            saved = read_json(run / "scoring" / path.parent.name / path.name, "structured score")
            _require(
                all(saved.get(k) == v for k, v in result.items()), "local scoring evidence mismatch"
            )
            if status == "succeeded":
                _require(
                    record["canonical_messages"][-1]
                    == {
                        "role": "assistant",
                        "content": [{"type": "text", "text": record["answer"]}],
                    },
                    "final answer disagrees with retained messages",
                )
                if result["format_valid"]:
                    valid[record["model_family_id"], record["system_id"]] += 1
        _require(not pending, "pilot is missing planned transcripts")
        for path in (run / "discarded").rglob("*.json"):
            captured = CapturedJSON.read(path, "structured discarded transcript")
            record = captured.mapping
            inventory.verify(path.relative_to(run).as_posix(), captured.content, record)
            coordinate = (record["competitor_id"], record["corpus_id"])
            _require(
                coordinate in expected and record["attempt"] not in attempts[coordinate],
                "discarded attempt lies outside the pilot plan",
            )
            attempts[coordinate].add(record["attempt"])
            self._check_prompt(record, record["corpus_id"])
        inventory.finish()
        _require(
            all(
                len(seen) == terminal_attempts[cell] == max(seen) for cell, seen in attempts.items()
            ),
            "pilot is missing retained attempts",
        )
        _require(
            all(valid[m, s] >= 27 for m in MODELS for s in SYSTEMS),
            "pilot requires at least 27/30 successful strictly formatted responses in every pair",
        )
        _require(
            summary["succeeded_cells"] == statuses["succeeded"]
            and summary["failed_cells"] == statuses["failed"],
            "pilot summary disagrees with retained statuses",
        )
        self._check_ledger(run, summary)

    def _check_prompt(self, record: dict[str, Any], qid: str) -> None:
        _require(
            record["system_prompt_sha256"] == _sha(SYSTEM_PROMPT.encode())
            and record["canonical_messages"][0] == {"role": "user", "content": self.prompts[qid]},
            "transcript did not execute the frozen structured prompt",
        )

    @staticmethod
    def _check_ledger(run: Path, summary: dict[str, Any]) -> None:
        for provider, cap in PROVIDER_CAPS.items():
            balance = summary["ledger"][provider]
            _require(
                nonnegative_decimal(balance["cap"]) == cap
                and nonnegative_decimal(balance["spent_before"]) <= cap
                and nonnegative_decimal(balance["spent_after"]) <= cap
                and balance["stopped"] is None,
                "pilot has a provider stop, cap change or cap overrun",
            )
        stopped: set[str] = set()
        bounds: dict[str, Decimal] = {}
        for event in read_jsonl(run / "ledger-events.jsonl", "structured pilot ledger"):
            provider, kind = event["provider"], event["event"]
            if kind == "reserve":
                bounds[event["unit_id"]] = nonnegative_decimal(event["bound"])
            elif kind == "settle":
                actual = event["actual"]
                _require(
                    not event.get("overrun")
                    and (actual is None or nonnegative_decimal(actual) <= bounds[event["unit_id"]]),
                    "pilot contains a bound overrun",
                )
            elif kind == "stop":
                stopped.add(provider)
            elif kind == "resume":
                stopped.discard(provider)
        _require(not stopped, "pilot has an unresolved provider stop")

    @staticmethod
    def check_balances(run: Path, spent: Mapping[str, Decimal]) -> None:
        summary = read_json(run / "run-summary.json", "structured pilot balance")
        _require(
            all(
                spent[p] >= nonnegative_decimal(summary["ledger"][p]["spent_after"])
                for p in PROVIDER_CAPS
            ),
            "shared ledger spend is below the pilot's retained balance",
        )
