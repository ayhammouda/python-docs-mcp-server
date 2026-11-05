"""One-off pre-pilot check of count endpoint envelopes; no inference or ledger units.

Run from the repository root with .venv/bin/python -m
ops.benchmarks.count_tokens_premise_check --scratch DIR --image sha256:... --keys-file PATH.
Uses the production container bootstrap and interactive live gate. Never run in CI.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from benchmarks.agentic import live
from benchmarks.agentic.count import Envelope, count_family
from benchmarks.agentic.host import (
    GRADING_CAPS,
    GRADING_SYSTEM,
    ItemSource,
    WorkerJob,
    refuse_host_keys,
    run_jobs,
)
from benchmarks.agentic.ledger import PlannedItem
from benchmarks.agentic.protocol import KeyLeakError, redact_key_shaped_text, write_json_evidence
from benchmarks.agentic.run import _until_signalled
from benchmarks.runner import BenchmarkValidationError


def premise_items() -> list[dict[str, Any]]:
    tool = {"name": "search_docs", "input_schema": {"type": "object"}}
    stub = {"name": "undefined", "input_schema": {"type": "object"}}
    user = {"role": "user", "content": "Look up Python documentation."}

    def turn(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "call_1", "name": name, "input": arguments}],
        }

    close = {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "."}],
    }
    text = {"role": "assistant", "content": [{"type": "text", "text": "."}]}
    real = turn("search_docs", {"query": "json.dumps"})
    undefined = turn("undefined", {"query": "json.dumps"})
    envelopes = [
        ("real-tools", Envelope("Count these messages.", [tool], [user, real, close])),
        ("undefined-tool-no-stub", Envelope("", [tool], [user, undefined, close])),
        ("undefined-tool-stub", Envelope("", [tool, stub], [user, undefined, close])),
        ("stand-in-tool-turn", Envelope("", [tool], [user, turn("search_docs", {}), close])),
        ("stand-in-text-turn", Envelope("", None, [user, text, {"role": "user", "content": "."}])),
        ("trailing-assistant", Envelope("", [tool], [user, real])),
        ("repeated-ids", Envelope("", [tool], [user, real, close, real, close])),
    ]
    return [{"kind": "envelope", "name": name, "envelope": asdict(e)} for name, e in envelopes]


def main(argv: list[str] | None = None) -> int:
    try:
        refuse_host_keys(os.environ)  # before arguments, authorization, or launcher construction
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--scratch", type=Path, required=True)
        parser.add_argument("--image", required=True)
        parser.add_argument("--keys-file", type=Path, required=True)
        parser.add_argument("--index", type=Path)
        args = parser.parse_args(argv)
        items = premise_items()
        live.authorize_live(
            "7 planned count calls; free Anthropic count-tokens endpoint; no ledger unit."
        )
        from benchmarks.__main__ import _container_launcher

        out = args.scratch / "premise"
        if args.scratch.is_symlink() or out.exists() or out.is_symlink():
            raise BenchmarkValidationError("premise evidence directory must be new")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.mkdir()  # claim it exclusively before the launcher can publish any evidence
        launcher = _container_launcher(args, uuid.uuid4().hex, out)
        family = count_family()
        source = ItemSource([(PlannedItem(i["name"], 0, 0, 0), i) for i in items])
        job = WorkerJob(
            "token-counter--grading",
            family,
            GRADING_SYSTEM,
            (),
            GRADING_CAPS,
            task="items",
            source=source,
        )
        results: dict[str, dict[str, Any]] = {}

        async def collect(job: WorkerJob, frame: dict[str, Any]) -> None:
            if frame["type"] == "counted":
                name = frame["item_id"]
                if frame.get("name") != name or name in results:
                    raise BenchmarkValidationError("invalid premise result coordinate")
                tokens, error = frame.get("tokens"), frame.get("error")
                if not (
                    (type(tokens) is int and tokens >= 0 and error is None)
                    or (tokens is None and isinstance(error, str))
                ):
                    raise BenchmarkValidationError("invalid premise count result")
                write_json_evidence(out / "results" / f"{name}.json", frame)
                results[name] = frame

        asyncio.run(
            _until_signalled(
                run_jobs(
                    [job],
                    launcher=launcher,
                    ledger=None,
                    on_message=collect,
                    log_dir=out / "logs",
                    cwd=Path(__file__).resolve().parents[2],
                )
            )
        )
        for item in items:
            name = item["name"]
            row = results.get(name, {})
            value = row.get("tokens")
            if type(value) is int:
                print(f"{name}: {value}")
            else:
                error = redact_key_shaped_text(str(row.get("error", "worker returned no result")))[
                    :80
                ]
                print(f"{name}: failed: HTTP {row.get('status')} ({error})")
        required = ("real-tools", "undefined-tool-stub", "stand-in-tool-turn", "stand-in-text-turn")
        return 0 if all(type(results.get(n, {}).get("tokens")) is int for n in required) else 2
    except (BenchmarkValidationError, OSError, ValueError, KeyError, TypeError) as exc:
        detail = str(exc) if isinstance(exc, BenchmarkValidationError) else type(exc).__name__
        print(f"premise check failed: {redact_key_shaped_text(detail)}", file=sys.stderr)
        return 3 if isinstance(exc, KeyLeakError) else 2


if __name__ == "__main__":
    raise SystemExit(main())
