---
phase: 5
slug: services-tool-polish-caching
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-16
---

# Phase 5 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio |
| **Config file** | pyproject.toml |
| **Quick run command** | `uv run pytest tests/test_services.py -x -q` |
| **Full suite command** | `uv run pytest tests/ -x -q` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/test_services.py -x -q`
- **After every plan wave:** Run `uv run pytest tests/ -x -q`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 05-01-01 | 01 | 1 | SRVR-03 | -- | N/A | unit | `uv run pytest tests/test_services.py -k test_get_docs -x` | ❌ W0 | ⬜ pending |
| 05-01-02 | 01 | 1 | SRVR-04 | -- | N/A | unit | `uv run pytest tests/test_services.py -k test_list_versions -x` | ❌ W0 | ⬜ pending |
| 05-01-03 | 01 | 1 | SRVR-07 | -- | N/A | unit | `uv run pytest tests/test_services.py -k test_meta -x` | ❌ W0 | ⬜ pending |
| 05-02-01 | 02 | 1 | OPS-01 | -- | N/A | unit | `uv run pytest tests/test_services.py -k test_logging -x` | ❌ W0 | ⬜ pending |
| 05-02-02 | 02 | 1 | OPS-02 | -- | N/A | unit | `uv run pytest tests/test_services.py -k test_logfmt -x` | ❌ W0 | ⬜ pending |
| 05-02-03 | 02 | 1 | OPS-03 | -- | N/A | unit | `uv run pytest tests/test_services.py -k test_decorator -x` | ❌ W0 | ⬜ pending |
| 05-03-01 | 03 | 2 | OPS-04 | -- | N/A | unit | `uv run pytest tests/test_services.py -k test_cache -x` | ❌ W0 | ⬜ pending |
| 05-03-02 | 03 | 2 | OPS-05 | -- | N/A | unit | `uv run pytest tests/test_services.py -k test_cache_lifetime -x` | ❌ W0 | ⬜ pending |
| 05-04-01 | 04 | 2 | PUBL-07 | -- | N/A | unit | `uv run pytest tests/test_services.py -k test_validate_corpus -x` | ❌ W0 | ⬜ pending |

---

## Wave 0 Requirements

- [ ] `tests/test_services.py` — stubs for all Phase 5 requirements
- [ ] Existing `tests/conftest.py` fixtures are sufficient (test_db, populated_db)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Cache hit on repeat calls visible in cache_info | OPS-04 | Requires runtime observation | Call service method twice, assert cache_info().hits >= 1 |

*Note: The above can be automated via cache_info() assertions.*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 10s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
