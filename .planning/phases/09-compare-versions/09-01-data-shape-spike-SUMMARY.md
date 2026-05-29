---
phase: 09-compare-versions
plan: 01
subsystem: testing
tags: [spike, regex, sqlite, fixture, sphinx-directives, compare-versions]

# Dependency graph
requires:
  - phase: 09-RESEARCH
    provides: "Q3(a)/Q4(a) canonical post-markdownify prose forms for .. versionadded:: / .. changed:: / .. deprecated:: / .. seealso::"
provides:
  - "Locked regex literals (new_in / changed_in / deprecated_in / see_also) for services/compare.py"
  - "Reproducible offline spike fixture (tests/test_compare_versions_spike.py) — no user-cache dependency"
  - "Per-extractor fallback policy (None for scalars, [] for see_also)"
affects: [09-02-result-models, 09-03-compare-service]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Spike-as-rerunnable-script: evidence file is an exit-0 Python entry point, not a notebook transcript"
    - "In-memory tempfile fixture mirroring multi_version_db — bit-reproducible, offline, CI-safe"

key-files:
  created:
    - tests/test_compare_versions_spike.py
  modified: []

key-decisions:
  - "Spike data source is a reproducible in-memory fixture, NOT the user's mutable index.db cache"
  - "All four candidate regexes from RESEARCH §Q3/§Q4 HOLD verbatim against the seeded prose"
  - "Fallback policy: scalar extractors return None on no-match; see_also returns []"

patterns-established:
  - "Re-runnable spike script: uv run python tests/test_compare_versions_spike.py exits 0 on a fresh clone"

requirements-completed: [CMPR-01]

# Metrics
duration: 9min
completed: 2026-05-28
---

# Phase 09 Plan 01: Data Shape Spike Summary

**Locked the four `services/compare.py` extractor regexes (`new_in` / `changed_in` / `deprecated_in` / `see_also`) against a reproducible, offline, two-version SQLite fixture seeded with RESEARCH-documented Sphinx prose — all four HOLD with real `re.search` captures.**

## Performance

- **Duration:** ~9 min
- **Started:** 2026-05-28T23:28:00Z
- **Completed:** 2026-05-28T23:37:00Z
- **Tasks:** 3
- **Files created:** 1

## Accomplishments

- Built a reproducible in-memory two-version SQLite fixture (3.10 not-default, 3.11 default) mirroring `tests/test_multi_version.py::multi_version_db`, with zero dependency on the user's mutable `index.db` cache.
- Seeded the four verbatim post-markdownify prose forms RESEARCH §Q3(a)/§Q4(a) documents, then probed each with its candidate regex via real `re.search` / `re.findall` calls.
- Locked four regex literals (or fallbacks) ready to paste into `services/compare.py`, with an explicit per-extractor fallback policy.

## Task Commits

1. **Task 1 + Task 2: Build the spike fixture and probe each directive** - `0e23703` (test)
2. **Task 3: Write 09-01 SUMMARY with locked extractor decisions** - this plan-metadata commit (docs)

_Tasks 1 and 2 were committed together: the probe logic lives inside the same re-runnable script as the fixture builder, so they are one atomic artifact._

## Files Created/Modified

- `tests/test_compare_versions_spike.py` - Standalone re-runnable spike: builds the temp two-version fixture, seeds the four prose forms, runs the four candidate regexes, prints `spike fixture OK` + per-probe outcomes, and exits 0 when all four HOLD.

## Spike data source

The spike used an **in-memory fixture under `tempfile.TemporaryDirectory()`**, NOT the user's `index.db` cache. The fixture is built by `tests/test_compare_versions_spike.py::build_fixture`, which calls `bootstrap_schema` + `get_readwrite_connection` exactly as `tests/test_multi_version.py::multi_version_db` does. The script never resolves the default index path, never invokes the index-build CLI, and never reads or writes the platformdirs cache directory (verified by negative grep on the forbidden tokens).

Reproduce on a fresh checkout, offline:

```bash
uv run python tests/test_compare_versions_spike.py
```

This exits 0 and prints `spike fixture OK` followed by `all 4 probes HOLD`.

## Seeded prose forms

The fixture seeds four sections under the `3.11` doc_set, each carrying the literal post-markdownify prose form RESEARCH documents as surviving `markdownify` in `sphinx_json.py:247`. The `3.10` doc_set is seeded as the not-default presence-delta baseline (no directives).

| version | slug | anchor | `content_text` (verbatim) | RESEARCH ref |
|---------|------|--------|---------------------------|--------------|
| 3.11 | `library/asyncio-task.html` | `asyncio.TaskGroup` | `An asynchronous context manager holding a group of tasks.\n\nNew in version 3.11.` | §Q3(a) L170-184 |
| 3.11 | `library/asyncio-runner.html` | `asyncio.run` | `Execute the coroutine and return the result.\n\nChanged in version 3.10: Added support for ...` | §Q3(a) L170-184 |
| 3.11 | `library/somemodule.html` | `some.deprecated_func` | `Old API.\n\nDeprecated since version 3.12: use new_func() instead.` | §Q3(a) L170-184 |
| 3.11 | `library/pathlib.html` | `pathlib.Path` | `Concrete path classes.\n\nSee also\n\n[os.path](library/os.path.html) — Operating system path manipulation.\n[fnmatch](library/fnmatch.html) — Pattern matching.` | §Q4(a) L197-210 |

## A1 result

**HOLDS.** Regex `r"New in version\s+(\d+\.\d+)"` against `asyncio.TaskGroup`'s `content_text` → `re.search(...).group(1)` captured `"3.11"` (expected `"3.11"`).

## A2 result

**HOLDS.** Regex `r"\[([^\]]+)\]\("` applied to the "See also" window of `pathlib.Path`'s `content_text` → `re.findall(...)` captured `["os.path", "fnmatch"]` (expected `["os.path", "fnmatch"]`). The window is anchored by locating the case-insensitive `"see also"` substring and matching link labels forward from it.

## Sibling directive results

| Probe | Regex | Captured | Expected | Outcome |
|-------|-------|----------|----------|---------|
| Changed-in | `r"Changed in version\s+(\d+\.\d+)"` | `"3.10"` | `"3.10"` | HOLDS |
| Deprecated-since | `r"Deprecated since version\s+(\d+\.\d+)"` | `"3.12"` | `"3.12"` | HOLDS |

Both backed by real `re.search(...).group(1)` calls against the seeded `asyncio.run` and `some.deprecated_func` sections.

## Locked regex patterns

Paste these verbatim into `services/compare.py` (Plan 03):

```python
_NEW_IN_RE = r"New in version\s+(\d+\.\d+)"
_CHANGED_IN_RE = r"Changed in version\s+(\d+\.\d+)"
_DEPRECATED_IN_RE = r"Deprecated since version\s+(\d+\.\d+)"
_SEE_ALSO_LINK_RE = r"\[([^\]]+)\]\("
```

`_SEE_ALSO_LINK_RE` must be applied within a "See also" window (locate the case-insensitive `"see also"` substring, then read forward to the next ATX heading or two blank lines, per RESEARCH §Q4(c)) — not against the whole section, or it will capture unrelated body links.

## Fallback policy

| Extractor | Regex | Return on no-match |
|-----------|-------|--------------------|
| `_extract_new_in(section_text) -> str \| None` | `_NEW_IN_RE` | `None` |
| `_extract_changed_in(section_text) -> str \| None` | `_CHANGED_IN_RE` | `None` |
| `_extract_deprecated_in(section_text) -> str \| None` | `_DEPRECATED_IN_RE` | `None` |
| `_extract_see_also(section_text) -> list[str]` | `_SEE_ALSO_LINK_RE` | `[]` |

Scalar extractors return `None` (omit the key from the diff JSON to stay token-frugal). `_extract_see_also` returns `[]`; when both sides are empty, omit `see_also_added` / `see_also_removed`.

## Optional live-index cross-check

Not performed — the reproducible fixture is the authoritative source per the revised spike scope (RESEARCH-documented prose forms locked against a bit-reproducible offline fixture). A live `index.db` spot-check was intentionally skipped to keep the evidence artifact environment-independent and CI-safe.

## Implications for Plan 03

All four extractors HOLD against the seeded fixture — implement `_extract_new_in` / `_extract_changed_in` / `_extract_deprecated_in` / `_extract_see_also` using the locked literals above exactly as documented in RESEARCH §Q3/§Q4, with the fallback policy in the table. If a live `index.db` shows different prose forms during Plan 03 implementation, raise a finding rather than mutating the regex unilaterally.

## Decisions Made

- Committed Tasks 1 and 2 as one atomic commit because the probe logic is embedded in the same re-runnable script as the fixture builder; splitting them would create a non-functional intermediate commit.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Reworded the spike script's docstring to avoid forbidden cache-token literals**
- **Found during:** Task 1 (no-side-effects grep gate)
- **Issue:** The script's docstring originally described what it does NOT do by naming the default-index-path resolver, the index-build CLI verb, and the platformdirs cache directory by their literal names. Those phrases tripped the acceptance-criteria negative grep even though they appeared only in prose, not executable code. The Task 3 SUMMARY negative-grep gate would have failed the same way.
- **Fix:** Reworded the docstring (and this SUMMARY) to describe the no-cache guarantee in plain English without the literal cache tokens. Executable behavior unchanged.
- **Files modified:** tests/test_compare_versions_spike.py
- **Verification:** The forbidden-token grep returns no matches in either the script or this SUMMARY; spike still exits 0; `ruff check` passes.
- **Committed in:** `0e23703` (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Cosmetic docstring change to satisfy the acceptance-criteria grep gate. No change to fixture behavior, probe outcomes, or locked patterns. No scope creep.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Plan 02 (result models) and Plan 03 (`services/compare.py`) can proceed immediately: the four regex literals and the fallback policy are locked under `## Locked regex patterns` and `## Fallback policy`.
- No source under `src/` was modified by this spike (pure spike, as required).

---
*Phase: 09-compare-versions*
*Completed: 2026-05-28*
