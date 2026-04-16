---
phase: 4
slug: sphinx-json-ingestion-atomic-swap-publishing
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-15
---

# Phase 4 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio |
| **Config file** | `pyproject.toml` `[tool.pytest.ini_options]` |
| **Quick run command** | `uv run pytest tests/test_ingestion.py tests/test_publish.py -x -q` |
| **Full suite command** | `uv run pytest tests/ -x -q` |
| **Estimated runtime** | ~10 seconds (fixture-based, no network) |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/test_ingestion.py tests/test_publish.py -x -q`
- **After every plan wave:** Run `uv run pytest tests/ -x -q`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | Status |
|---------|------|------|-------------|-----------|--------------------|--------|
| 04-01-01 | 01 | 1 | INGR-C-04 | unit | `uv run pytest tests/test_ingestion.py::test_fjson_parsing -x` | pending |
| 04-01-02 | 01 | 1 | INGR-C-05 | unit | `uv run pytest tests/test_ingestion.py::test_html_to_markdown -x` | pending |
| 04-01-03 | 01 | 1 | INGR-C-06 | unit | `uv run pytest tests/test_ingestion.py::test_broken_fjson_isolation -x` | pending |
| 04-01-04 | 01 | 1 | INGR-C-07 | unit | `uv run pytest tests/test_ingestion.py::test_code_block_extraction -x` | pending |
| 04-02-01 | 02 | 1 | PUBL-01, PUBL-02 | unit | `uv run pytest tests/test_publish.py::test_build_artifact -x` | pending |
| 04-02-02 | 02 | 1 | PUBL-03 | unit | `uv run pytest tests/test_publish.py::test_smoke_tests -x` | pending |
| 04-02-03 | 02 | 1 | PUBL-04, PUBL-05 | unit | `uv run pytest tests/test_publish.py::test_atomic_swap -x` | pending |
| 04-02-04 | 02 | 1 | PUBL-06 | integration | `uv run pytest tests/test_publish.py::test_ingestion_while_serving -x` | pending |
| 04-03-01 | 03 | 2 | INGR-C-01, INGR-C-02, INGR-C-03 | integration | `uv run pytest tests/test_ingestion.py::test_build_index_cli -x` | pending |
| 04-03-02 | 03 | 2 | INGR-C-08 | unit | `uv run pytest tests/test_ingestion.py::test_fts_population -x` | pending |
| 04-03-03 | 03 | 2 | INGR-C-09 | unit | `uv run pytest tests/test_ingestion.py::test_synonym_population -x` | pending |

---

## Wave 0 Requirements

- [ ] `tests/test_ingestion.py` — stubs for INGR-C-* requirements
- [ ] `tests/test_publish.py` — stubs for PUBL-* requirements
- [ ] `tests/fixtures/` — sample fjson fixtures for testing
- [ ] `tests/conftest.py` — shared fixtures (tmp_path DB, sample fjson data)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Full CPython clone + sphinx-build | INGR-C-01, INGR-C-02, INGR-C-03 | Requires network + 3-8 min build time | Run `build-index --versions 3.13` on dev machine, verify .fjson output |

*All other behaviors have automated verification via fixtures.*

---

## Validation Sign-Off

- [ ] All tasks have automated verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
