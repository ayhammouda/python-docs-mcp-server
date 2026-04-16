# Plan 07a: Structural Stability Tests - Summary

**Status:** Complete
**Tests added:** 20

## What Was Built

20 structural stability tests in `tests/test_stability.py` that assert properties of search results rather than exact content. Tests exercise the full SearchService stack (classifier -> synonym expansion -> FTS5/symbol fast-path -> ranker).

### Fixtures Added to conftest.py
- `stability_db` -- 30 representative stdlib symbols + 3 documents with sections, FTS indexes rebuilt
- `search_service` -- SearchService backed by stability_db

### Test Categories
- **Symbol Resolution (6):** asyncio.TaskGroup, json.dumps, os.path.join, pathlib.Path, collections.OrderedDict, typing.Optional
- **Module-Level Search (4):** asyncio, json, subprocess, logging
- **Result Shape (3):** required fields non-None, score numeric, max_results respected
- **Stdlib Breadth (3):** io.StringIO, csv.reader, threading.Thread
- **Edge Cases (4):** nonexistent symbol, empty query, special chars, version filter

## Files Modified

- `tests/conftest.py` -- Added stability_db and search_service fixtures
- `tests/test_stability.py` -- 20 new tests (new file)

## Requirements Coverage

| Requirement | Status |
|-------------|--------|
| TEST-01 | Done |
| TEST-06 | Partial (CI config in 07d) |

## Self-Check: PASSED
