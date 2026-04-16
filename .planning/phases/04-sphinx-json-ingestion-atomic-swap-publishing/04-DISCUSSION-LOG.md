# Phase 4: Sphinx JSON Ingestion & Atomic-Swap Publishing - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-16
**Phase:** 04-sphinx-json-ingestion-atomic-swap-publishing
**Areas discussed:** HTML-to-Markdown library, CPython source acquisition

---

## HTML-to-Markdown Library

| Option | Description | Selected |
|--------|-------------|----------|
| markdownify | Lightweight, actively maintained, 1.4M+ PyPI downloads/month, good at preserving docstring HTML structure | ✓ |
| html2text | Older, battle-tested (Aaron Swartz), strips more aggressively, less active maintenance | |
| You decide | Claude picks after researching both against CPython output | |

**User's choice:** markdownify (recommended)

---

## CPython Source Acquisition

| Option | Description | Selected |
|--------|-------------|----------|
| Shallow git clone | `git clone --depth 1 --branch v3.13.12` — ~50MB, exact tagged state, simple cleanup | ✓ |
| GitHub release tarball | `curl` tarball — ~30MB, no git dep, fastest, but different dir structure | |
| You decide | Claude picks | |

**User's choice:** Shallow git clone (recommended)

---

## Claude's Discretion

- Sphinx venv location strategy
- Smoke test queries for swap validation
- Per-document failure isolation granularity
- Build progress reporting to stderr
- ingestion_runs table population

## Deferred Ideas

None — discussion stayed within phase scope.
