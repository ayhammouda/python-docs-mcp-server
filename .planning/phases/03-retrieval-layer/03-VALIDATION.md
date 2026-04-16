---
phase: 3
slug: retrieval-layer
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-16
---

# Phase 3 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x + pytest-asyncio |
| **Config file** | pyproject.toml `[tool.pytest.ini_options]` |
| **Quick run command** | `uv run pytest tests/test_retrieval.py -x -q` |
| **Full suite command** | `uv run pytest tests/ -x -q` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/test_retrieval.py -x -q`
- **After every plan wave:** Run `uv run pytest tests/ -x -q`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 5 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 03-A-01 | A | 1 | RETR-01 | — | N/A | unit | `uv run pytest tests/test_retrieval.py::test_fts5_escape -x` | ❌ W0 | ⬜ pending |
| 03-A-02 | A | 1 | RETR-03 | — | N/A | fuzz | `uv run pytest tests/test_retrieval.py::test_fts5_escape_fuzz -x` | ❌ W0 | ⬜ pending |
| 03-A-03 | A | 1 | RETR-04 | — | N/A | unit | `uv run pytest tests/test_retrieval.py::test_classify_query -x` | ❌ W0 | ⬜ pending |
| 03-A-04 | A | 1 | RETR-05 | — | N/A | unit | `uv run pytest tests/test_retrieval.py::test_synonym_expansion -x` | ❌ W0 | ⬜ pending |
| 03-B-01 | B | 1 | RETR-06 | — | N/A | unit | `uv run pytest tests/test_retrieval.py::test_bm25_ranking -x` | ❌ W0 | ⬜ pending |
| 03-B-02 | B | 1 | RETR-07 | — | N/A | unit | `uv run pytest tests/test_retrieval.py::test_snippet_excerpts -x` | ❌ W0 | ⬜ pending |
| 03-B-03 | B | 1 | RETR-09 | — | N/A | unit | `uv run pytest tests/test_retrieval.py::test_hit_shape -x` | ❌ W0 | ⬜ pending |
| 03-C-01 | C | 1 | RETR-08 | — | N/A | unit | `uv run pytest tests/test_retrieval.py::test_apply_budget -x` | ❌ W0 | ⬜ pending |
| 03-C-02 | C | 1 | RETR-08 | — | N/A | unit | `uv run pytest tests/test_retrieval.py::test_budget_unicode -x` | ❌ W0 | ⬜ pending |
| 03-D-01 | D | 2 | SRVR-08 | — | N/A | integration | `uv run pytest tests/test_retrieval.py::test_error_routing -x` | ❌ W0 | ⬜ pending |
| 03-D-02 | D | 2 | RETR-02 | — | N/A | audit | `rg 'MATCH' src/ --type py` | N/A | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_retrieval.py` — test stubs for all RETR requirements
- [ ] Shared fixtures: in-memory SQLite with FTS5 tables, sample synonym dict

*Existing test infrastructure (pytest, conftest) covers framework needs.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| RETR-02 grep audit | RETR-02 | Code audit, not runtime test | Run `rg 'MATCH' src/ --type py` and verify all MATCH calls route through fts5_escape |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 5s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
