"""Command line entry point for the benchmark runner."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from benchmarks.corpus import validate_corpus
from benchmarks.report import generate_readme_summary, generate_report
from benchmarks.runner import BenchmarkConfig, BenchmarkValidationError, run_benchmark
from benchmarks.scoring import ingest_adjudication_verdicts, score_run

_DEFAULT_MODEL_MATRIX = Path("docs/benchmarks/model-matrix.yml")
_DEFAULT_METHODOLOGY = Path("docs/benchmarks/PUBLIC-BENCHMARK-METHODOLOGY.md")
_DEFAULT_CORPUS_SCHEMA = Path("docs/benchmarks/corpus.schema.json")


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
    # manual-adjudication ingest hook. Both read the answer-key corpus via
    # the same `benchmarks.corpus.validate_corpus` gate as `validate-corpus`
    # above, so a run can only be scored against a schema-valid corpus.
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
        required=True,
        type=Path,
        help="Path to the answer-key corpus file (validated against --schema).",
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
        required=True,
        type=Path,
        help="Path to the answer-key corpus file (validated against --schema).",
    )
    adjudicate_parser.add_argument(
        "--schema",
        type=Path,
        default=_DEFAULT_CORPUS_SCHEMA,
        help=f"Path to the corpus JSON schema file (default: {_DEFAULT_CORPUS_SCHEMA}).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

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


if __name__ == "__main__":
    sys.exit(main())
