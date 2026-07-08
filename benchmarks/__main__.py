"""Command line entry point for the benchmark runner."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from benchmarks.report import generate_readme_summary, generate_report
from benchmarks.runner import BenchmarkConfig, BenchmarkValidationError, run_benchmark

_DEFAULT_MODEL_MATRIX = Path("docs/benchmarks/model-matrix.yml")
_DEFAULT_METHODOLOGY = Path("docs/benchmarks/PUBLIC-BENCHMARK-METHODOLOGY.md")


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

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
