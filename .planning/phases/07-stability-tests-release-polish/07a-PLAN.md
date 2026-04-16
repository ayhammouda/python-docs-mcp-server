---
phase: 7
plan: a
title: "Structural Stability Tests"
wave: 1
depends_on: []
files_modified:
  - tests/test_stability.py
  - tests/conftest.py
requirements:
  - TEST-01
  - TEST-06
autonomous: true
---

# Plan 07a: Structural Stability Tests

<objective>
Create ~20 structural stability tests that assert properties of real search results (len(hits) >= 1, "asyncio" in hit.uri) rather than exact content, so tests survive CPython doc revisions. These tests validate the full retrieval stack end-to-end against a freshly populated in-memory index.
</objective>

## Tasks

<task id="1">
<title>Create stability test fixtures and helpers</title>

<read_first>
- tests/conftest.py
- src/mcp_server_python_docs/storage/db.py
- src/mcp_server_python_docs/retrieval/query.py
- src/mcp_server_python_docs/retrieval/ranker.py
- src/mcp_server_python_docs/services/search.py
- src/mcp_server_python_docs/models.py
</read_first>

<action>
Add a `stability_db` fixture to `tests/conftest.py` that:
1. Creates a fresh SQLite database in tmp_path
2. Bootstraps the schema
3. Inserts a `doc_sets` row for version 3.13 (is_default=True)
4. Populates ~30 representative symbol rows covering the stdlib surface area. Include these exact symbols:
   - `asyncio.TaskGroup` (class, uri=`library/asyncio-task.html#asyncio.TaskGroup`)
   - `asyncio.run` (function, uri=`library/asyncio-runner.html#asyncio.run`)
   - `json.dumps` (function, uri=`library/json.html#json.dumps`)
   - `json.loads` (function, uri=`library/json.html#json.loads`)
   - `os.path.join` (function, uri=`library/os.path.html#os.path.join`)
   - `pathlib.Path` (class, uri=`library/pathlib.html#pathlib.Path`)
   - `collections.OrderedDict` (class, uri=`library/collections.html#collections.OrderedDict`)
   - `typing.Optional` (data, uri=`library/typing.html#typing.Optional`)
   - `re.compile` (function, uri=`library/re.html#re.compile`)
   - `subprocess.run` (function, uri=`library/subprocess.html#subprocess.run`)
   - `logging.getLogger` (function, uri=`library/logging.html#logging.getLogger`)
   - `sqlite3.connect` (function, uri=`library/sqlite3.html#sqlite3.connect`)
   - `http.server.HTTPServer` (class, uri=`library/http.server.html#http.server.HTTPServer`)
   - `urllib.parse.urlparse` (function, uri=`library/urllib.parse.html#urllib.parse.urlparse`)
   - `dataclasses.dataclass` (function, uri=`library/dataclasses.html#dataclasses.dataclass`)
   - `functools.lru_cache` (function, uri=`library/functools.html#functools.lru_cache`)
   - `itertools.chain` (function, uri=`library/itertools.html#itertools.chain`)
   - `contextlib.contextmanager` (function, uri=`library/contextlib.html#contextlib.contextmanager`)
   - `abc.ABC` (class, uri=`library/abc.html#abc.ABC`)
   - `enum.Enum` (class, uri=`library/enum.html#enum.Enum`)
   - `datetime.datetime` (class, uri=`library/datetime.html#datetime.datetime`)
   - `hashlib.sha256` (function, uri=`library/hashlib.html#hashlib.sha256`)
   - `socket.socket` (class, uri=`library/socket.html#socket.socket`)
   - `threading.Thread` (class, uri=`library/threading.html#threading.Thread`)
   - `multiprocessing.Process` (class, uri=`library/multiprocessing.html#multiprocessing.Process`)
   - `sys.argv` (data, uri=`library/sys.html#sys.argv`)
   - `os.environ` (data, uri=`library/os.html#os.environ`)
   - `io.StringIO` (class, uri=`library/io.html#io.StringIO`)
   - `csv.reader` (function, uri=`library/csv.html#csv.reader`)
   - `argparse.ArgumentParser` (class, uri=`library/argparse.html#argparse.ArgumentParser`)

5. Also insert a few section rows for modules (asyncio-task, json, os.path) with heading text and minimal content_text so FTS5 searches can return section hits.
6. Rebuild FTS indexes after insertion (`INSERT INTO symbols_fts(symbols_fts) VALUES('rebuild')` and same for sections_fts).
7. Return the connection.

Also add a `search_service` fixture that creates a SearchService from the stability_db connection.
</action>

<acceptance_criteria>
- `tests/conftest.py` contains a fixture named `stability_db`
- `tests/conftest.py` contains a fixture named `search_service`
- The `stability_db` fixture inserts at least 25 symbol rows
- The `stability_db` fixture rebuilds FTS indexes
- Running `pytest tests/conftest.py --co` does not error
</acceptance_criteria>
</task>

<task id="2">
<title>Create structural stability test file with ~20 tests</title>

<read_first>
- tests/conftest.py (after task 1 modifications)
- src/mcp_server_python_docs/services/search.py
- src/mcp_server_python_docs/retrieval/query.py
- src/mcp_server_python_docs/models.py
- python-docs-mcp-server-build-guide.md (lines 536-546, stability test example)
</read_first>

<action>
Create `tests/test_stability.py` with approximately 20 structural stability tests. Each test asserts structural properties, NOT exact content. Use the `stability_db` and `search_service` fixtures.

The tests should cover these categories:

**Symbol Resolution (6 tests):**
1. `test_resolve_asyncio_taskgroup` -- `search_docs("asyncio.TaskGroup", kind="symbol")` returns `len(hits) >= 1`, `hits[0].kind == "symbol"`, `"asyncio" in hits[0].uri`, `"TaskGroup" in hits[0].title`
2. `test_resolve_json_dumps` -- `search_docs("json.dumps")` returns hits, first hit uri contains `"json"`, title contains `"dumps"`
3. `test_resolve_dotted_symbol` -- `search_docs("os.path.join")` returns hits with `"os.path" in hit.uri`
4. `test_resolve_class_symbol` -- `search_docs("pathlib.Path")` returns hits where kind is `"class"` or `"symbol"`
5. `test_resolve_collections_orderdict` -- `search_docs("collections.OrderedDict")` returns hits with `"collections" in hit.uri`
6. `test_resolve_typing_optional` -- `search_docs("typing.Optional")` returns hits with `"typing" in hit.uri`

**Module-Level Search (4 tests):**
7. `test_search_asyncio_module` -- `search_docs("asyncio")` returns `len(hits) >= 1`
8. `test_search_json_module` -- `search_docs("json")` returns hits
9. `test_search_subprocess_module` -- `search_docs("subprocess")` returns hits
10. `test_search_logging_module` -- `search_docs("logging")` returns hits

**Result Shape (3 tests):**
11. `test_hit_has_required_fields` -- Every hit from `search_docs("asyncio.TaskGroup")` has non-None `uri`, `title`, `kind`, `version`
12. `test_hit_score_is_numeric` -- Every hit has a numeric `score >= 0`
13. `test_max_results_respected` -- `search_docs("asyncio", max_results=2)` returns `len(hits) <= 2`

**Cross-Domain (3 tests):**
14. `test_search_stdlib_breadth_io` -- `search_docs("io.StringIO")` returns hits
15. `test_search_stdlib_breadth_csv` -- `search_docs("csv.reader")` returns hits  
16. `test_search_stdlib_breadth_threading` -- `search_docs("threading.Thread")` returns hits

**Negative/Edge Cases (4 tests):**
17. `test_nonexistent_symbol_returns_empty_or_few` -- `search_docs("nonexistent.FakeSymbol12345")` returns `len(hits) == 0` or hits are low-relevance
18. `test_empty_query_does_not_crash` -- `search_docs("")` does not raise
19. `test_special_chars_do_not_crash` -- `search_docs("c++")`, `search_docs("*")`, `search_docs("(")` all do not raise
20. `test_version_filter` -- If `search_docs("asyncio.TaskGroup", version="3.13")` returns hits, all hits have `version == "3.13"`

Each test should call through the SearchService (which exercises the full retrieval + query + ranking stack), NOT raw SQL. This ensures the stability tests validate the actual code path users hit.
</action>

<acceptance_criteria>
- `tests/test_stability.py` exists with at least 18 test functions
- Every test name starts with `test_`
- No test asserts exact string content (no `== "some exact title"` assertions)
- All tests use `>=`, `in`, `<=`, `is not None`, or `isinstance` assertions (structural checks)
- `pytest tests/test_stability.py --co` collects at least 18 tests
- `pytest tests/test_stability.py` passes (all tests green)
</acceptance_criteria>
</task>

## Verification

```bash
# Collect stability tests
uv run pytest tests/test_stability.py --co -q 2>&1 | tail -5

# Run stability tests
uv run pytest tests/test_stability.py -v 2>&1

# Verify no exact-content assertions (structural only)
grep -n '== "' tests/test_stability.py | grep -v 'version ==' | grep -v 'kind ==' || echo "OK: no exact content assertions"

# Full test suite still passes
uv run pytest --tb=short 2>&1 | tail -10
```

<must_haves>
- At least 18 structural stability tests in tests/test_stability.py
- Every test asserts structural properties (len >= 1, substring in field), NOT exact content
- Tests exercise the full SearchService stack, not raw SQL
- All stability tests pass
- Existing 172 tests remain passing
</must_haves>
