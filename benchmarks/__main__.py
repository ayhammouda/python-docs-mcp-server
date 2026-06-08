"""Command line entry point for the benchmark runner."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from benchmarks.runner import BenchmarkConfig, BenchmarkValidationError, run_benchmark


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

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
