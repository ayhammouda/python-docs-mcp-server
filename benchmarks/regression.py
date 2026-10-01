"""Offline retrieval regression gate over the frozen public corpus (no LLM calls)."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from benchmarks.corpus import validate_corpus
from mcp_server_python_docs.errors import DocsServerError
from mcp_server_python_docs.server import _load_synonyms
from mcp_server_python_docs.services.content import ContentService
from mcp_server_python_docs.services.search import SearchService
from mcp_server_python_docs.storage.db import get_index_path, get_readonly_connection


def evaluate(index: Path, corpus: Path, schema: Path) -> dict[str, Any]:
    questions = validate_corpus(corpus, schema).questions
    db = get_readonly_connection(index)
    try:
        versions = {r[0] for r in db.execute("SELECT version FROM doc_sets")}
        search, content = SearchService(db, _load_synonyms()), ContentService(db)
        results: dict[str, Any] = {}
        for question in questions:
            requested = question.python_version
            for version in [requested] if isinstance(requested, str) else requested:
                if version not in versions:
                    raise ValueError(f"Missing documentation version: {version}")
                expected = []
                for citation in question.citations:
                    url = urlsplit(citation)
                    if url.hostname == "docs.python.org" and url.path.startswith(f"/{version}/"):
                        page = url.path[len(version) + 2 :]
                        row = db.execute(
                            "SELECT d.slug FROM documents d JOIN doc_sets ds "
                            "ON ds.id=d.doc_set_id WHERE ds.version=? AND d.uri=?",
                            (version, page),
                        ).fetchone()
                        expected.append((page, row[0] if row else page, url.fragment or None))
                if not expected:
                    raise ValueError(f"No versioned reference for {question.id}/{version}")
                started = time.perf_counter()
                hits = search.search(question.prompt, version=version, max_results=5).hits
                if any(hit.version != version for hit in hits):
                    raise ValueError(f"Wrong-version search result: {question.id}/{version}")
                matched = any(
                    hit.slug == slug and (anchor is None or hit.anchor == anchor)
                    for hit in hits
                    for page, slug, anchor in expected
                )
                citations = []
                for page, slug, anchor in expected:
                    try:
                        result = content.get_docs(slug, version, anchor, max_chars=8000)
                    except DocsServerError:
                        continue
                    if (result.version, result.slug, result.anchor) != (version, slug, anchor):
                        raise ValueError(f"Wrong citation/version: {question.id}/{version}")
                    if len(result.content) > 8000:
                        raise ValueError(f"Content budget exceeded: {question.id}/{version}")
                    if result.content.strip():
                        citations.append(
                            f"https://docs.python.org/{version}/{page}"
                            + (f"#{anchor}" if anchor else "")
                        )
                results[f"{question.id}/{version}"] = {
                    "retrieval_hit_at_5": matched,
                    "resolved_citations": sorted(citations),
                    "seconds": round(time.perf_counter() - started, 6),
                }
        return {
            "schema_version": 1,
            "corpus_sha256": hashlib.sha256(corpus.read_bytes()).hexdigest(),
            "index_versions": sorted(versions),
            "index_documents": db.execute("SELECT count(*) FROM documents").fetchone()[0],
            "cases": results,
        }
    finally:
        db.close()


def regressions(actual: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    errors = []
    if actual["corpus_sha256"] != baseline["corpus_sha256"]:
        errors.append("Corpus changed: independent baseline review required")
    if actual["cases"].keys() != baseline["cases"].keys():
        errors.append("Evaluation cases changed")
    if actual["index_documents"] < baseline["index_documents"]:
        errors.append("Documentation index lost pages")
    for key, expected in baseline["cases"].items():
        result = actual["cases"].get(key)
        if result is None:
            continue
        if expected["retrieval_hit_at_5"] and not result["retrieval_hit_at_5"]:
            errors.append(f"{key}: lost retrieval hit")
        if not set(expected["resolved_citations"]) <= set(result["resolved_citations"]):
            errors.append(f"{key}: lost canonical content")
        if result["seconds"] > baseline["max_case_seconds"]:
            errors.append(f"{key}: exceeded recorded latency budget")
    return errors


def deny_network(event: str, args: tuple[object, ...]) -> None:
    if event in {"socket.connect", "socket.connect_ex", "socket.getaddrinfo"}:
        raise RuntimeError("Network access is forbidden during offline evaluation")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=get_index_path())
    parser.add_argument("--corpus", type=Path, default=Path("docs/benchmarks/corpus.yml"))
    parser.add_argument("--schema", type=Path, default=Path("docs/benchmarks/corpus.schema.json"))
    parser.add_argument(
        "--baseline", type=Path, default=Path("docs/benchmarks/regression-baseline.json")
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--record", action="store_true", help="Record evidence; never approves it")
    args = parser.parse_args()
    sys.addaudithook(deny_network)
    try:
        result = evaluate(args.index, args.corpus, args.schema)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        if args.record:
            print("Recorded evaluation; baseline changes require independent review.")
            return 0
        errors = regressions(result, json.loads(args.baseline.read_text()))
        print(json.dumps({"cases": len(result["cases"]), "regressions": errors}))
        return int(bool(errors))
    except (ValueError, OSError, sqlite3.Error, RuntimeError) as exc:
        print(f"Regression gate failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
