"""Command line entry point for the benchmark runner."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from benchmarks.corpus import validate_corpus
from benchmarks.grading.stats import GATE_RESAMPLES
from benchmarks.report import generate_readme_summary, generate_report
from benchmarks.runner import BenchmarkConfig, BenchmarkValidationError, run_benchmark
from benchmarks.scoring import ingest_adjudication_verdicts, score_run

_DEFAULT_MODEL_MATRIX = Path("docs/benchmarks/model-matrix.yml")
_DEFAULT_METHODOLOGY = Path("docs/benchmarks/PUBLIC-BENCHMARK-METHODOLOGY.md")
_DEFAULT_CORPUS_SCHEMA = Path("docs/benchmarks/corpus.schema.json")
_DEFAULT_AGENTIC_MANIFEST = Path("docs/benchmarks/agentic-manifest.yml")
_DEFAULT_PRICES = Path("docs/benchmarks/price-snapshot.yml")
_DEFAULT_GRADING_CRITERIA = Path("docs/benchmarks/grading-criteria.yml")
_DEFAULT_GRADING_PANEL = Path("docs/benchmarks/grading/panel.yml")
_DEFAULT_TUNING_QUESTIONS = Path("docs/benchmarks/grading/tuning-questions.yml")


def _ids(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks",
        description="Run the reproducible Python docs benchmark harness.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="Run benchmark cells and write raw artifacts.")
    run.add_argument("--corpus", required=True, type=Path, help="Path to the corpus YAML file.")
    run.add_argument(
        "--manifest",
        required=True,
        type=Path,
        help="Path to the competitor manifest YAML file.",
    )
    run.add_argument("--out", required=True, type=Path, help="Output directory for run artifacts.")
    run.add_argument("--run-id", help="Stable run identifier. Defaults to a UTC timestamp.")
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and emit planned benchmark cells without execution.",
    )

    report = subparsers.add_parser(
        "report", help="Generate REPORT.md and a README-safe summary from run artifacts."
    )
    report.add_argument(
        "--run-dir", required=True, type=Path, help="Path to a run's artifact directory."
    )
    report.add_argument(
        "--model-matrix",
        type=Path,
        default=_DEFAULT_MODEL_MATRIX,
        help=f"Path to the model/client matrix YAML file (default: {_DEFAULT_MODEL_MATRIX}).",
    )
    report.add_argument(
        "--methodology",
        type=Path,
        default=_DEFAULT_METHODOLOGY,
        help=f"Path to the public benchmark methodology document (default: {_DEFAULT_METHODOLOGY})",
    )
    report.add_argument("--report-out", type=Path, help="Defaults to <run-dir>/REPORT.md.")
    report.add_argument(
        "--readme-summary-out", type=Path, help="Defaults to <run-dir>/README-SUMMARY.md."
    )

    # Note on naming: this is `python -m benchmarks validate-corpus`, a distinct
    # program (`prog="python -m benchmarks"`, set above) from the server's own
    # `python-docs-mcp-server validate-corpus` (src/mcp_server_python_docs/__main__.py),
    # which smoke-tests a built docs *index* database and is unrelated to the
    # benchmark evaluation corpus validated here. The two commands cannot be
    # invoked interchangeably and do not share any code path.
    validate_corpus_parser = subparsers.add_parser(
        "validate-corpus",
        help=(
            "Validate a benchmark evaluation corpus file against "
            "docs/benchmarks/corpus.schema.json (distinct from "
            "`python-docs-mcp-server validate-corpus`, which validates a built docs index)."
        ),
    )
    validate_corpus_parser.add_argument(
        "--corpus",
        required=True,
        type=Path,
        help="Path to the corpus YAML or JSON file to validate.",
    )
    validate_corpus_parser.add_argument(
        "--schema",
        type=Path,
        default=_DEFAULT_CORPUS_SCHEMA,
        help=f"Path to the corpus JSON schema file (default: {_DEFAULT_CORPUS_SCHEMA}).",
    )

    # `score` and `adjudicate` (issue #88): the correctness scorer and its
    # manual-adjudication ingest hook. Both read the run's own corpus
    # snapshot (refusing a --corpus whose sha256 differs) through the same
    # `benchmarks.corpus.validate_corpus` gate as `validate-corpus` above.
    score_parser = subparsers.add_parser(
        "score",
        help=(
            "Apply the automatic correctness-scoring pass to a run's scoring "
            "placeholders and write the adjudication queue + rollups."
        ),
    )
    score_parser.add_argument(
        "--run-dir", required=True, type=Path, help="Path to a run's artifact directory."
    )
    score_parser.add_argument(
        "--corpus",
        default=None,
        type=Path,
        help=(
            "Answer-key corpus; defaults to the run's snapshots/corpus.yml. A different "
            "file is refused unless its sha256 matches that snapshot."
        ),
    )
    score_parser.add_argument(
        "--schema",
        type=Path,
        default=_DEFAULT_CORPUS_SCHEMA,
        help=f"Path to the corpus JSON schema file (default: {_DEFAULT_CORPUS_SCHEMA}).",
    )

    adjudicate_parser = subparsers.add_parser(
        "adjudicate",
        help=(
            "Ingest human adjudication verdicts for queued cells and re-emit "
            "final scoring records. A human verdict is never overwritten by a "
            "later `score` run."
        ),
    )
    adjudicate_parser.add_argument(
        "--run-dir", required=True, type=Path, help="Path to a run's artifact directory."
    )
    adjudicate_parser.add_argument(
        "--verdicts",
        required=True,
        type=Path,
        help="Path to a JSON or YAML file of human verdicts (see benchmarks.scoring).",
    )
    adjudicate_parser.add_argument(
        "--corpus",
        default=None,
        type=Path,
        help=(
            "Answer-key corpus; defaults to the run's snapshots/corpus.yml. A different "
            "file is refused unless its sha256 matches that snapshot."
        ),
    )
    adjudicate_parser.add_argument(
        "--schema",
        type=Path,
        default=_DEFAULT_CORPUS_SCHEMA,
        help=f"Path to the corpus JSON schema file (default: {_DEFAULT_CORPUS_SCHEMA}).",
    )

    agentic = subparsers.add_parser(
        "agentic-run",
        help="Run the agentic benchmark (docs/benchmarks/AGENTIC-BENCHMARK-DESIGN.md).",
    )
    agentic.add_argument("--corpus", required=True, type=Path, help="Corpus YAML file.")
    agentic.add_argument("--out", required=True, type=Path, help="Empty artifact directory.")
    agentic.add_argument(
        "--ledger-dir",
        type=Path,
        help="Spend ledger directory (default: one durable per-user path, shared by every run).",
    )
    agentic.add_argument("--matrix", type=Path, default=_DEFAULT_MODEL_MATRIX)
    agentic.add_argument("--manifest", type=Path, default=_DEFAULT_AGENTIC_MANIFEST)
    agentic.add_argument("--prices", type=Path, default=_DEFAULT_PRICES)
    for name in ("models", "systems", "questions"):
        agentic.add_argument(f"--{name}", type=_ids, metavar="IDS", help="Comma-separated ids.")
    agentic.add_argument("--repeats", type=int, default=3)
    agentic.add_argument("--seed", type=int)
    agentic.add_argument("--run-id")
    mode = agentic.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Write the plan; no ledger.")
    mode.add_argument(
        "--live",
        action="store_true",
        help="Container launcher only: run for real, after the CI refusal and a typed 'yes'.",
    )
    agentic.add_argument(
        "--launcher",
        choices=("local", "container"),
        default="local",
        help="local: scripted models only. container: the sandbox, a dry run unless --live.",
    )
    agentic.add_argument("--image", help="The sandbox worker image ID (sha256:...).")
    agentic.add_argument(
        "--keys-file",
        type=Path,
        help="Mode-600 NAME=VALUE file; only the container launcher reads it, one key per worker.",
    )
    agentic.add_argument(
        "--index", type=Path, help="The docs index for python-docs jobs (default: the cache's)."
    )
    agentic.add_argument(
        "--reference-run",
        type=Path,
        help=(
            "A finished pilot run whose answering cost, grading cost and per-model time "
            "the full-run gate scales; hosted runs require complete live grading evidence."
        ),
    )
    agentic.add_argument(
        "--acknowledge-overrun",
        action="append",
        default=[],
        metavar="PROVIDER",
        help="Lift a provider's overrun stop after its bound was corrected.",
    )

    grade = subparsers.add_parser("agentic-grade", help="Plan or run version-bound judge batches.")
    grade.add_argument(
        "--target",
        required=True,
        choices=(
            "answers",
            "reference",
            "key-audit",
            "probe-gen",
            "probe-check",
            "tuning",
            "probes",
            "reference-probes",
        ),
    )
    dest = grade.add_mutually_exclusive_group(required=True)
    dest.add_argument("--run-dir", type=Path)
    dest.add_argument("--workspace", type=Path)
    grade.add_argument("--workspace-kind", choices=("probe-build", "tuning", "held-out"))
    grade.add_argument("--handoff-tuning", type=Path, action="append", default=[])
    grade.add_argument("--panel", type=Path, default=_DEFAULT_GRADING_PANEL)
    grade.add_argument("--prices", type=Path, default=_DEFAULT_PRICES)
    grade.add_argument("--criteria", type=Path, default=_DEFAULT_GRADING_CRITERIA)
    grade.add_argument("--tuning", type=Path, default=_DEFAULT_TUNING_QUESTIONS)
    grade.add_argument("--probes", type=Path)
    grade.add_argument("--index", type=Path)
    grade.add_argument("--judges", type=_ids)
    grade.add_argument("--effort")
    grade.add_argument("--pass", type=int, default=1, dest="pass_number")
    grade.add_argument("--round", type=int, default=1, dest="round_number")
    grade.add_argument("--ledger-dir", type=Path)
    grade.add_argument("--launcher", choices=("local", "container"), default="local")
    grade.add_argument("--image")
    grade.add_argument("--keys-file", type=Path)
    grade.add_argument("--live", action="store_true")
    grade.add_argument("--confirm-max-spend")
    grade.add_argument("--grading-run-id")
    grade.add_argument("--acknowledge-overrun", action="append", default=[])

    counter = subparsers.add_parser(
        "agentic-count-tokens", help="Plan or count normalized tokens of scored trajectories."
    )
    counter.add_argument("--run-dir", type=Path, required=True)
    counter.add_argument("--launcher", choices=("local", "container"), default="local")
    counter.add_argument("--image")
    counter.add_argument("--keys-file", type=Path)
    counter.add_argument("--index", type=Path)
    count_mode = counter.add_mutually_exclusive_group()
    count_mode.add_argument("--live", action="store_true")
    count_mode.add_argument("--dry-run", action="store_true")
    counter.add_argument(
        "--all", action="store_true", help="Recount, retaining every prior result."
    )

    # PR 5: grades, gates and claims of an agentically graded run, or one workspace step.
    # It reads files only and makes no network call.
    agentic_score = subparsers.add_parser(
        "agentic-score",
        help="Grade, gate and claim a graded agentic run, or run one grading workspace step.",
    )
    where = agentic_score.add_mutually_exclusive_group(required=True)
    where.add_argument("--run-dir", type=Path, help="A graded agentic run directory.")
    where.add_argument("--workspace", type=Path, help="A grading workspace directory.")
    agentic_score.add_argument(
        "--g1",
        type=Path,
        action="append",
        default=[],
        metavar="PATH",
        help="A g1.json from a held-out workspace (repeatable).",
    )
    agentic_score.add_argument("--step", choices=("assemble-probes", "tuning", "g1"))
    agentic_score.add_argument(
        "--criteria",
        type=Path,
        help="Criteria file to check against the frozen grading inputs.",
    )
    agentic_score.add_argument(
        "--panel",
        type=Path,
        help="Panel file to check against the frozen grading inputs.",
    )
    agentic_score.add_argument("--schema", type=Path, default=_DEFAULT_CORPUS_SCHEMA)
    agentic_score.add_argument("--tuning", type=Path, default=_DEFAULT_TUNING_QUESTIONS)
    agentic_score.add_argument("--index", type=Path, help="Docs index (default: the server's).")
    agentic_score.add_argument(
        "--resamples", type=int, choices=[GATE_RESAMPLES], default=GATE_RESAMPLES
    )

    # Spawned by the agentic host, never by hand: it reads its job as frames on stdin.
    subparsers.add_parser(
        "agentic-worker", help="Internal: run one agentic (model, system) job for the host."
    )
    # Run by the container launcher in a keyless probe container, never by hand.
    probe = subparsers.add_parser(
        "agentic-probe", help="Internal: check a sandbox network's isolation from inside it."
    )
    probe.add_argument("--spec", required=True, help="The probe's checks, as JSON.")
    return parser


def _container_launcher(
    args: argparse.Namespace,
    run_id: str,
    out_dir: Path,
    *,
    docker: Any = None,
    verify: Any = None,
    probe: Any = None,
) -> Any:
    """The production sandbox: the real image check, the real probes, docker on this host."""
    from benchmarks.agentic.sandbox import ContainerLauncher, Sandbox, SandboxConfig, verify_image
    from benchmarks.runner import _repo_commit_sha
    from mcp_server_python_docs.storage.db import get_index_path

    config = SandboxConfig(
        image=args.image,
        run_id=run_id,
        out_dir=out_dir,
        index_path=args.index or get_index_path(),
        keys_file=args.keys_file,
        repo_commit=_repo_commit_sha(),
    )
    return ContainerLauncher(
        Sandbox(config, docker=docker, verify=verify or verify_image), probe=probe
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "agentic-run":
        # argparse itself quotes invalid values (e.g. --repeats) in its diagnostics.
        from benchmarks.agentic.protocol import KeyLeakError, assert_no_key_shaped_json

        try:
            assert_no_key_shaped_json(argv[1:], where="agentic-run arguments")
        except KeyLeakError as exc:
            parser.exit(3, f"agentic run stopped on a key leak: {exc}\n")
    args = parser.parse_args(argv)

    if args.command == "agentic-worker":
        # Lazy: the provider SDKs load only in a worker. main_worker protects output first.
        from benchmarks.agentic.worker import main_worker

        return main_worker()

    if args.command == "agentic-probe":
        from benchmarks.agentic.probe import probe_main

        return probe_main(["--spec", args.spec])

    if args.command == "agentic-run":
        # Lazy, like agentic-worker: only agentic commands load the provider SDKs.
        from benchmarks.agentic import run as agentic_run
        from benchmarks.agentic.protocol import KeyLeakError, scanned_json_bytes
        from benchmarks.runner import _default_run_id

        if args.live and args.launcher != "container":
            parser.error("--live needs --launcher container: the local launcher is scripted only")
        if args.launcher == "container" and not args.image:
            parser.error("--launcher container needs --image sha256:...")
        run_id = args.run_id or _default_run_id()  # one id for the run and its sandbox
        config = agentic_run.AgenticConfig(
            corpus_path=args.corpus,
            matrix_path=args.matrix,
            manifest_path=args.manifest,
            prices_path=args.prices,
            ledger_dir=args.ledger_dir or agentic_run.default_ledger_dir(),
            out_dir=args.out,
            models=args.models,
            systems=args.systems,
            questions=args.questions,
            repeats=args.repeats,
            seed=args.seed,
            run_id=run_id,
            # The container launcher is a dry run unless --live is given.
            dry_run=args.dry_run or (args.launcher == "container" and not args.live),
            acknowledge_overrun=tuple(args.acknowledge_overrun),
            reference_run=args.reference_run,
        )
        try:
            launcher = (
                _container_launcher(args, run_id, args.out)
                if args.launcher == "container"
                else None
            )
            summary = agentic_run.run_agentic(config, launcher=launcher)
            output = scanned_json_bytes(summary, where="run summary", indent=2, sort_keys=True)
        except KeyLeakError as exc:  # before its base class
            parser.exit(3, f"agentic run stopped on a key leak: {exc}\n")
        except BenchmarkValidationError as exc:
            parser.exit(2, f"agentic run failed: {exc}\n")
        print(output.decode())
        return 0

    if args.command == "agentic-grade":
        return _agentic_grade(parser, args)

    if args.command == "agentic-count-tokens":
        return _agentic_count_tokens(parser, args)

    if args.command == "agentic-score":
        return _agentic_score(parser, args)

    if args.command == "run":
        config = BenchmarkConfig(
            corpus_path=args.corpus,
            manifest_path=args.manifest,
            out_dir=args.out,
            run_id=args.run_id,
            dry_run=args.dry_run,
        )
        try:
            summary = run_benchmark(config)
        except BenchmarkValidationError as exc:
            parser.exit(2, f"benchmark validation failed: {exc}\n")

        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    if args.command == "report":
        try:
            report_path = generate_report(
                args.run_dir,
                model_matrix_path=args.model_matrix,
                methodology_path=args.methodology,
                out_path=args.report_out,
            )
            summary_path = generate_readme_summary(
                args.run_dir,
                methodology_path=args.methodology,
                out_path=args.readme_summary_out,
            )
        except BenchmarkValidationError as exc:
            parser.exit(2, f"benchmark report generation failed: {exc}\n")

        print(
            json.dumps(
                {"report_path": str(report_path), "readme_summary_path": str(summary_path)},
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if args.command == "validate-corpus":
        try:
            result = validate_corpus(args.corpus, args.schema)
        except BenchmarkValidationError as exc:
            parser.exit(2, f"benchmark corpus validation failed: {exc}\n")

        print(
            json.dumps(
                {
                    "corpus_path": str(args.corpus),
                    "schema_path": str(args.schema),
                    "question_count": result.question_count,
                    "category_counts": result.category_counts,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if args.command == "score":
        try:
            result = score_run(args.run_dir, corpus_path=args.corpus, schema_path=args.schema)
        except BenchmarkValidationError as exc:
            parser.exit(2, f"benchmark scoring failed: {exc}\n")

        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if args.command == "adjudicate":
        try:
            result = ingest_adjudication_verdicts(
                args.run_dir,
                args.verdicts,
                corpus_path=args.corpus,
                schema_path=args.schema,
            )
        except BenchmarkValidationError as exc:
            parser.exit(2, f"benchmark adjudication ingest failed: {exc}\n")

        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    parser.print_help()
    return 2


def _agentic_count_tokens(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    import os

    from benchmarks.agentic import count
    from benchmarks.agentic.host import refuse_host_keys
    from benchmarks.agentic.protocol import KeyLeakError, scanned_json_bytes

    if args.live and args.launcher != "container":
        parser.error("--live needs --launcher container")
    if args.launcher == "container" and not args.image:
        parser.error("--launcher container needs --image")
    try:
        refuse_host_keys(os.environ)
        run_id = uuid.uuid4().hex
        launcher = (
            _container_launcher(args, run_id, args.run_dir / "grading/sandbox" / run_id)
            if args.launcher == "container"
            else None
        )
        summary = count.run_counting(
            args.run_dir,
            live=args.live,
            launcher=launcher,
            only_missing=not args.all,
            dry_run=args.dry_run or (args.launcher == "container" and not args.live),
            count_run_id=run_id,
        )
        output = scanned_json_bytes(summary, where="count summary", indent=2, sort_keys=True)
    except KeyLeakError as exc:
        parser.exit(3, f"counting stopped on a key leak: {exc}\n")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        detail = str(exc) if isinstance(exc, BenchmarkValidationError) else type(exc).__name__
        parser.exit(2, f"counting failed: {detail}\n")
    print(output.decode())
    return 0


def _agentic_grade(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    import os

    from benchmarks.agentic.host import refuse_host_keys
    from benchmarks.agentic.protocol import (
        KeyLeakError,
        redact_key_shaped_text,
        scanned_json_bytes,
    )
    from benchmarks.agentic.run import default_ledger_dir
    from benchmarks.grading import run
    from mcp_server_python_docs.storage.db import get_index_path

    if args.live and args.launcher != "container":
        parser.error("--live needs --launcher container")
    if args.launcher == "container" and not args.image:
        parser.error("--launcher container needs --image")
    if (args.target in run.RUN_TARGETS) != (args.run_dir is not None):
        parser.error("choose --run-dir for run targets or --workspace for workspace targets")
    if args.run_dir and (args.workspace_kind or args.handoff_tuning):
        parser.error("workspace options require --workspace")
    try:
        refuse_host_keys(os.environ)
        run_id = args.grading_run_id if args.grading_run_id is not None else uuid.uuid4().hex
        cfg = run.GradingConfig(
            target=args.target,
            out_dir=args.run_dir or args.workspace,
            ledger_dir=args.ledger_dir or default_ledger_dir(),
            panel_path=args.panel,
            prices_path=args.prices,
            criteria_path=args.criteria,
            tuning_path=args.tuning,
            probes_path=args.probes,
            index_path=args.index or get_index_path(),
            judges=args.judges,
            effort=args.effort,
            pass_number=args.pass_number,
            round_number=args.round_number,
            live=args.live,
            grading_run_id=run_id,
            confirm_max_spend=Decimal(args.confirm_max_spend)
            if args.confirm_max_spend is not None
            else None,
            acknowledge_overrun=tuple(args.acknowledge_overrun),
            dry_run=args.launcher == "container" and not args.live,
        )
        if args.workspace:
            if args.workspace_kind and args.workspace_kind != run.KINDS[args.target]:
                parser.error("target requires its matching workspace kind")
            if not (args.workspace / "workspace.json").is_file():
                if not args.workspace_kind:
                    parser.error("a new workspace needs --workspace-kind")
                run.init_workspace(
                    args.workspace,
                    args.workspace_kind,
                    criteria_path=args.criteria,
                    panel_path=args.panel,
                    probes_path=args.probes,
                    tuning_evidence=args.handoff_tuning,
                    tuning_path=args.tuning,
                    judges=args.judges if args.target == "probes" else None,
                )
            elif args.handoff_tuning:
                run.check_tuning_handoff(args.workspace, args.handoff_tuning)
        launcher = (
            _container_launcher(args, run_id, run.grading_dir(cfg) / "sandbox" / run_id)
            if args.launcher == "container"
            else None
        )
        summary = run.run_grading(cfg, launcher=launcher)
        output = scanned_json_bytes(summary, where="grading summary", indent=2, sort_keys=True)
    except KeyLeakError as exc:
        detail = redact_key_shaped_text("\n".join([str(exc), *getattr(exc, "__notes__", ())]))
        parser.exit(3, f"grading stopped on a key leak: {detail}\n")
    except (InvalidOperation, OSError, ValueError, KeyError, TypeError) as exc:
        detail = str(exc) if isinstance(exc, BenchmarkValidationError) else type(exc).__name__
        detail = redact_key_shaped_text("\n".join([detail, *getattr(exc, "__notes__", ())]))
        parser.exit(2, f"grading failed: {detail}\n")
    print(output.decode())
    return 0


def _agentic_score(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    # Lazy, like the other agentic commands.
    from benchmarks.grading import score

    try:
        if args.run_dir is not None:
            if args.step is not None:
                parser.error("--step goes with --workspace, not --run-dir")
            result: object = score.score_run_dir(
                args.run_dir,
                criteria_path=args.criteria,
                panel_path=args.panel,
                schema_path=args.schema,
                g1_paths=args.g1,
                tuning_path=args.tuning,
                resamples=args.resamples,
            )
        elif args.step == "assemble-probes":
            from mcp_server_python_docs.storage.db import get_index_path

            path = score.assemble_workspace_probes(
                args.workspace,
                criteria_path=args.criteria,
                tuning_path=args.tuning,
                panel_path=args.panel,
                index_path=args.index or get_index_path(),
            )
            result = {"probes": str(path)}
        elif args.step == "tuning":
            path = score.write_tuning(
                args.workspace,
                panel_path=args.panel,
                criteria_path=args.criteria,
                tuning_path=args.tuning,
            )
            result = {"tuning": str(path)}
        elif args.step == "g1":
            path = score.write_g1(
                args.workspace,
                panel_path=args.panel,
                criteria_path=args.criteria,
                tuning_path=args.tuning,
            )
            result = {"g1": str(path)}
        else:
            parser.error("--workspace needs --step")
    except BenchmarkValidationError as exc:
        parser.exit(2, f"agentic scoring failed: {exc}\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
