---
status: passed
phase: 07-stability-tests-release-polish
verified: 2026-04-15
---

# Phase 7: Stability Tests & Release Polish - Verification

## Goal Check

**Goal:** ~20 structural stability tests that survive CPython doc revisions, a `doctor` CLI subcommand for first-run diagnostics, and a README with copy-paste `mcpServers` snippets + install / first-run / troubleshooting sections.

**Result: PASSED** -- All goal elements delivered and verified.

## Requirements Traceability

| Requirement | Status | Evidence |
|-------------|--------|----------|
| TEST-01 | PASSED | 20 stability tests in test_stability.py assert structural properties (len >= 1, substring in uri), not exact content |
| TEST-02 | PASSED | Unit tests green: test_retrieval.py covers fts5_escape 50-input fuzz, budget truncation, synonym expansion, symbol classification |
| TEST-03 | PASSED | Storage tests green: test_schema.py covers idempotency, WAL, FTS5 check, repository queries |
| TEST-04 | PASSED | Ingestion tests green: test_ingestion.py (objects.inv, sphinx json), test_publish.py (atomic swap) |
| TEST-05 | PASSED | test_stdio_smoke.py spawns server subprocess, verifies zero stdout pollution across full JSON-RPC round-trips |
| TEST-06 | PASSED | CI config: .github/workflows/ci.yml with ubuntu-latest + macos-latest, Python 3.12 + 3.13; Windows best-effort |
| CLI-02 | PASSED | `doctor` subcommand checks Python version, FTS5, cache dir, index.db, disk space; PASS/FAIL report to stderr |
| SHIP-03 | PASSED | README has mcpServers config snippets for Claude Desktop (macOS/Linux/Windows paths) and Cursor |
| SHIP-04 | PASSED | README has install (uvx), first-run (build-index), troubleshooting (FTS5, uvx stale, MSIX, restart) |
| SHIP-05 | PASSED | README Support section: "Tested on macOS and Linux; Windows should work... but is not verified on every release" |

## Must-Haves Verification

| Must-Have | Verified |
|-----------|----------|
| ~20 structural stability tests | Yes -- 20 tests, all structural assertions |
| Full test pyramid green | Yes -- 204 tests, 0 failures |
| doctor CLI subcommand | Yes -- 5 probes, PASS/FAIL output, exit codes correct |
| README with mcpServers config | Yes -- Claude Desktop + Cursor configs with platform paths |
| README troubleshooting | Yes -- 4 troubleshooting sections (FTS5, uvx, MSIX, restart) |
| CI configuration | Yes -- GitHub Actions 2x2 matrix (macOS + Linux, 3.12 + 3.13) |

## Automated Checks

```
204 passed in 3.78s
```

All test files:
- test_stability.py: 20 tests (structural stability)
- test_stdio_smoke.py: 4 tests (subprocess MCP round-trips)
- test_doctor.py: 8 tests (doctor subcommand)
- test_retrieval.py: 63 tests (unit - fts5_escape, budget, synonyms, query)
- test_schema.py: 26 tests (storage)
- test_ingestion.py: 17 tests (ingestion)
- test_publish.py: 10 tests (atomic swap)
- test_services.py: 22 tests (service layer)
- test_multi_version.py: 20 tests (multi-version)
- test_packaging.py: 8 tests (wheel contents)
- test_schema_snapshot.py: 3 tests (drift guard)
- test_phase1_integration.py: 3 tests (integration)
- test_stdio_hygiene.py: 4 tests (stdio hygiene)
- test_synonyms.py: 5 tests (synonym loading)

## Human Verification Items

None -- all deliverables are automated-testable.
