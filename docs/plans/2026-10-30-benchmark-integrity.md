# Benchmark Integrity Implementation Plan (PR 9)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the fixed-flow benchmark harness record what really happened:
real failure reasons, no leaked secrets, honest latency and token statistics,
valid token counts, clean URL parsing, a pinned scoring corpus and a wider
network tripwire.

**Architecture:** One PR on branch `fix/benchmark-integrity`, with eight
commits in audit order; each commit is one finding with its test and its fix.
Fixes land where the code survives the agentic redesign (`runner.py`,
`report.py`, `scoring.py`, `claude_tokens.py`, `regression.py`), plus two
shared pieces the new harness reuses: `cell_failure_from` in `runner.py` and
the new `benchmarks/urls.py`. Adapter-only problems get the smallest fix. The
five fixed-flow adapters are not deduplicated, because design §11.1 deletes
them. Nothing under `src/` changes.

**Left to the new harness:**
- everything in the roadmap's "Carried Into The New Benchmark Harness";
- the §10 latency split and cold and warm calls;
- failed and capped trajectory statistics;
- a real `no-docs` call;
- request hashes and 429 retries for counts;
- grading-input hashes;
- relative link destinations for grounding, with their own tests.

**Tech Stack:**
- Python ≥ 3.12 (local venv: CPython 3.13.10)
- `mcp` 2.3.0, `anyio` 4.14.2, `httpx2` 2.16.0, `platformdirs` 4.11.0
- stdlib `urllib` for count-tokens (no SDK: issue #89 rule)
- pytest 9.1.1 with pytest-asyncio 1.4.0 (`asyncio_mode = "auto"`)
- Ruff (E, F, I, W; line length 100), Pyright in standard mode

**Spec:** `docs/research/2026-10-30-audit-fix-best-practices.md`: section
"Benchmarks and engineering practice" items 1-6, section "Ops, security and
CI" item 2, and rows N6, N9, N10. Bound by
`docs/plans/2026-10-30-audit-fixes-roadmap.md` (D14, D15, the PR map, the
carried-into-harness list, Global Constraints) and by
`docs/benchmarks/AGENTIC-BENCHMARK-DESIGN.md` §5, §7, §8, §9.2, §10, §11. Read
the design with
`git show docs/agentic-benchmark-design:docs/benchmarks/AGENTIC-BENCHMARK-DESIGN.md`.

## Global Constraints

- **Precondition:** `docs/plans/2026-10-30-*.md` and `docs/research/2026-10-30-audit-fix-best-practices.md` are on `main` (roadmap Wave 0.5); stop if not.
- **Base:** `main` after #162 merges. Never merge `feat/mcp-sdk-v2` into this
  branch. In Wave 1 only `CHANGELOG.md` can conflict.
- **TDD** (superpowers:test-driven-development): write each failing test first
  and watch it fail. One finding is one commit, with its test and its fix.
- **Gate** (`AGENTS.md`), run fresh before the PR:
  - `uv run --locked ruff check src/ tests/ benchmarks/ ops/ .github/scripts/`
  - `uv run --locked pyright src/ benchmarks/`
  - `uv run --locked pytest --tb=short -q`
- **Existing tests:** delete none. Change only the items listed under
  "Existing tests and fixtures that change on purpose". The PR body repeats
  that list.
- **Supervisor review:** changed existing assertions are forbidden territory
  (`AGENT-EXECUTION-PIPELINE.md` §2, §7), and the diff will pass 500 lines.
  Label the PR `supervisor-review`, and cite: "The owner (Aymen Hammouda) asked
  on 2026-10-30 to implement all audit findings; decisions in
  `docs/plans/2026-10-30-audit-fixes-roadmap.md`."
- **No dependency or network change:** no new runtime or dev dependency and no
  new runtime network access. No test calls a live API; CI has no keys.
- **Never install an audit hook in the pytest process.** Hooks cannot be
  removed (PEP 578).
- **D14:** fix where the code survives the redesign; no adapter dedupe.
  **D15:** the count model is `claude-sonnet-5-5`.
- **No version bump:** `benchmarks/` is not in the wheel (`pyproject.toml:52-53`).
  The methodology amendment and the `## [Unreleased]` CHANGELOG entries come
  in Task 8.
- **Commits and PR:** conventional prefixes. Every commit ends with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. The PR body
  follows `.github/PULL_REQUEST_TEMPLATE/agent.md` and ends with
  `🤖 Generated with [Claude Code](https://claude.com/claude-code)`. Never
  self-merge.

## Checked on 2026-10-30 (worktree `c03e497`, offline)

- `uv run --locked pytest tests/benchmarks -q` gives `216 passed`.
- **Nesting.** A `BenchmarkCellFailure` raised inside a real `ClientSession`
  over `stdio_client` comes out as `ExceptionGroup > ExceptionGroup >
  BenchmarkCellFailure`. A `wait_for` timeout comes out as a bare, empty
  `TimeoutError`.
- **Live repro.** Against this machine's symbol-only 3.13 index, the
  first-party adapter reports `mcp_protocol_crash` ("unhandled errors in a
  TaskGroup (1 sub-exception)"), while the server logs `get_docs ...
  PageNotFoundError`. CI has no index, so the smoke test skips there.
- **Server env.** `mcp` 2.3.0 `stdio_client` spawns with
  `get_default_environment() | server.env`. On POSIX the defaults are `HOME
  LOGNAME PATH SHELL TERM USER`; on Windows they include `LOCALAPPDATA` and
  `USERPROFILE`.
- **Cache folder.** `platformdirs` 4.11.0 reads `XDG_CACHE_HOME` on Linux and
  macOS. Windows uses the shell API, then `LOCALAPPDATA`.

## Rulings (where the inputs were silent or conflicted)

1. **Tool stubs.** A real client that sends `tool_use`/`tool_result` blocks
   always sends tool definitions, and the count endpoint counts `tools`
   (token-counting docs). So the wrapped-envelope request carries one
   name-only stub per distinct tool, `{"name": <tool>, "input_schema":
   {"type": "object"}}`. This also keeps the request valid if the endpoint
   rejects tool blocks without `tools` (langchain-ai/langchain#29637,
   secondary). Real schemas, descriptions and the client's system prompt stay
   missing. The count does include the API's tool-use preamble, which is why
   every count stays `approximation: true`.
2. **Every fixed-flow count is `approximation: true`**, prompt-only cells
   included. `anthropic-version` stays `2023-06-01` and goes into every record.
3. **First-party env:** the auto-index switch, plus `XDG_CACHE_HOME` when it is
   set. `HOME` and `LOCALAPPDATA` arrive through the SDK defaults.
4. **Context7 ignores an explicit `api_key` in keyless mode.**
5. **Text parsing.** Context7 reads its id only from the labeled line; the old
   `/org/project` fallback is what returned `/context7.com/dashboard`. Ref.tools
   reads the `url:` line, then the first URL. GitMCP takes the first URL.
6. **Refusal order.** This moves two refusals. Today both entry points validate
   `--corpus` first (`scoring.py:140`, `:260`). Now `score_run` checks for
   scoring files first, and `ingest_adjudication_verdicts` validates the
   verdicts first; then both resolve the snapshot. Three tests stay unchanged:
   `test_score_run_refuses_when_no_scoring_files`,
   `test_ingest_rejects_malformed_verdicts_file` and
   `test_cli_score_exits_nonzero_with_clean_message_on_missing_run_dir`.
7. **The five-adapter tests live in `test_competitor_adapters.py`**, next to
   the guard helpers. The research had suggested the first-party test file.
8. **`cell_failure_from` text.** An empty `TimeoutError` reads
   `"{label} did not complete within {timeout_seconds}s"`. Any other empty leaf
   is named by its type.
9. **Exact counts on failed cells** leave the median and get their own visible
   bucket. After Ruling 2 no fixed-flow run produces them; the bucket guards
   hand-made and future records.
10. **Exclusions:** `null` means none. Any other value that is not a list, and
    any entry that is not a mapping, is refused.
11. **CHANGELOG:** consolidated bullets under Changed, Removed and Security in
    Task 8. Precedent: `CHANGELOG.md:69`.
12. **`_execute_cell` keeps its own catch-all.** Only the five adapter ladders
    change.
13. **One PR, not one per finding.** Research §6 asks for one PR per finding.
    The roadmap's PR map (`docs/plans/2026-10-30-audit-fixes-roadmap.md`,
    row 9) bundles these findings, and the roadmap binds. One commit per
    finding, each with its failing test, keeps §6's intent.
14. **Two methodology amendments.** Task 8 adds "Amendment 2026-10-30" now.
    The design's "Amendment 2026-10-29: agentic mode" (§11.2) lands later,
    under the earlier date the design gives it. Each names the runs it
    governs (fixed-flow or agentic), so neither overrides the other. The
    agentic one goes after this one in the file.

## Review Focus

1. **An MCP server dies mid-session.** anyio raises `BrokenResourceError()`
   with empty text. Expected: the message names the type. Pinned by Task 1,
   `test_cell_failure_from_names_a_leaf_that_has_no_text`.
2. **The count endpoint rejects tool blocks without `tools`.** Expected: stubs
   go only with an envelope that holds `tool_use` blocks. Pinned by Task 4,
   `test_count_cell_tokens_sends_tool_stubs_only_with_tool_use_blocks`.
3. **The count endpoint answers 200 with a non-JSON body.** Expected: a
   `failed` token record, and the run continues; today it aborts. Pinned by
   Task 4, `test_live_claude_token_counter_raises_benchmark_cell_failure_on_a_non_json_body`.
4. **`exclusions: null`, or an exclusion without `terms_check`.** Expected: the
   report renders, with empty cells for the missing fields. Pinned by Task 3,
   `test_report_tolerates_sparse_exclusions`.
5. **A run directory without `snapshots/corpus.yml`.** Expected: `score` and
   `adjudicate` both refuse, and no verdict is written. Pinned by Task 6,
   `test_score_and_adjudicate_refuse_a_run_without_a_corpus_snapshot`.

## Existing tests and fixtures that change on purpose

Paths are under `tests/benchmarks/`.

| Task | Label | Test or fixture | Change |
|---|---|---|---|
| 1 | stronger | `test_python_docs_mcp_adapter.py::test_real_server_round_trip_when_a_local_index_exists` (:305) | the accepted set drops `mcp_protocol_crash` (N10) |
| 1 | stronger | `test_competitor_adapters.py::_ADAPTER_MAPPING_CASES` (:513-535) | gains a `python-docs` case |
| 3 | neutral | `test_runner.py:46, :93, :267, :315, :321`; `test_report.py:62, :68, :111, :116, :122, :401`; `test_competitor_adapters.py:986` | `adapter: no-mcp-baseline` becomes `adapter: fake` (the alias stops dispatching) |
| 3 | contract change | `test_runner.py:173, :295, :451`; `test_competitor_adapters.py:1132` | `["external_provider_calls"] is False` becomes `"external_provider_calls" not in` |
| 4 | contract change | `test_claude_tokens.py::test_envelope_is_exact_for_a_plain_prompt_with_no_tool_calls` (:55-59) | renamed `test_envelope_for_a_plain_prompt_is_the_prompt_alone` (old name removed); `approximation` is `True` |
| 4 | contract change | `test_claude_tokens.py::test_envelope_wraps_tool_call_results_as_tool_result_blocks` (:62-76) | results move to `envelope[2]`; `approximation` is `True` |
| 4 | contract change | `test_claude_tokens.py::test_count_cell_tokens_fills_client_wrapped_and_raw_payload_tokens` (:94-104) | `approximation` is `True`; adds a note assertion |
| 4 | contract change | `test_runner.py::test_token_record_is_filled_when_live_guard_passes_with_a_fake_counter` (:571-575) | `approximation` is `True`; adds the two new fields |
| 4 | stronger | `test_claude_tokens.py::test_live_claude_token_counter_calls_count_tokens_endpoint_when_guard_passes` (:200-225) | adds model and no-`tools` assertions |
| 4 | stronger | `test_runner.py::test_token_record_is_the_honest_placeholder_when_live_guard_is_disabled` (:534-538) | adds the two new fields |
| 5 | contract change | `test_competitor_adapters.py::test_context7_flow_falls_back_to_text_regex_when_no_structured_candidates` (:176-186) | its text becomes Context7 3.2.3's real shape (label-only parsing) |
| 6 | neutral | `test_scoring.py::_build_run_dir` (:93-102) and the run dirs at :537-546, :572-578 | write `snapshots/corpus.yml`, as the runner does |

Unchanged on purpose, because they never dispatch:
- `test_competitor_adapters.py:859`, `:867`;
- `test_model_matrix.py:201-208`, `:254`;
- `test_report.py:267`, `:472`, `:596`;
- `test_scoring.py:84`.

---

### Task 1: Keep the real reason when a cell fails inside MCP task groups (finding 8 + N10)

**Files:**
- Modify: `benchmarks/runner.py:45-50` (add the helper after `BenchmarkCellFailure`)
- Modify the `run()` ladders: `benchmarks/adapters/python_docs_mcp_adapter.py:233-244`,
  `context7_adapter.py:303-314`, `gitmcp_adapter.py:253-263`,
  `deepwiki_adapter.py:186-196`, `ref_tools_adapter.py:258-269`
- Test: `tests/benchmarks/test_runner.py`, `test_competitor_adapters.py:493-592`,
  `test_python_docs_mcp_adapter.py`

**Interfaces:**
- Produces: `benchmarks.runner.cell_failure_from(exc: BaseException, *,
  timeout_seconds: float, label: str) -> BenchmarkCellFailure`.

Below, `BCF` is `BenchmarkCellFailure` and `EG(xs)` is `ExceptionGroup("tg", xs)`.

- [ ] **Step 1: Write the helper tests** in `tests/benchmarks/test_runner.py`
  (import `BenchmarkCellFailure` and `cell_failure_from`).
  `test_cell_failure_from_walks_nested_groups_to_their_leaves` is parametrized
  over the rows below. Each row asserts
  `(failure.category, str(failure)) == (category, message)`, where
  `failure = cell_failure_from(exc, timeout_seconds=2.5, label="flow")`.

| `exc` | `category` | `message` |
|---|---|---|
| `BCF("tool_failure", "boom")` | `tool_failure` | `boom` |
| `TimeoutError()` | `timeout` | `flow did not complete within 2.5s` |
| `RuntimeError("stdio pipe closed")` | `mcp_protocol_crash` | `stdio pipe closed` |
| `EG([EG([BCF("tool_failure", "get_docs: boom")])])` | `tool_failure` | `get_docs: boom` |
| `EG([OSError("pipe closed"), EG([BCF("timeout", "slow")])])` | `timeout` | `OSError: pipe closed; BenchmarkCellFailure: slow` |
| `EG([OSError("pipe closed"), TimeoutError()])` | `timeout` | `OSError: pipe closed; TimeoutError: flow did not complete within 2.5s` |

  Then add `test_cell_failure_from_names_a_leaf_that_has_no_text` (Review
  Focus 1), with the same arguments:
  - `EG([ConnectionResetError()])` gives `("mcp_protocol_crash", "ConnectionResetError")`;
  - `EG([ValueError("bad"), ConnectionResetError()])` gives the message
    `ValueError: bad; ConnectionResetError`.

- [ ] **Step 2: Write the five-adapter test** in `test_competitor_adapters.py`.
  - Import `benchmarks.adapters.python_docs_mcp_adapter as python_docs_mcp_adapter`.
  - Add `_enable_python_docs(monkeypatch)`. It patches
    `python_docs_mcp_adapter._default_index_path` to `lambda: Path(__file__)`,
    because `run()` only checks that the file exists.
  - Put `pytest.param(python_docs_mcp_adapter.PythonDocsMcpAdapter,
    python_docs_mcp_adapter.PythonDocsMcpResult, _enable_python_docs,
    id="python-docs")` first in `_ADAPTER_MAPPING_CASES`.
  - Add `test_adapter_maps_a_failure_group_to_its_real_reason`. It is
    parametrized over that table and over two module-level groups:
    - `_NESTED = EG([EG([BCF("tool_failure", "search_docs returned an error: boom")])])`,
      expecting `search_docs returned an error: boom`;
    - `_TWO_LEAVES = EG([BCF("tool_failure", "boom"), OSError("pipe closed")])`,
      expecting `BenchmarkCellFailure: boom; OSError: pipe closed`.

    After `enable_guard(monkeypatch)`, it patches `_run_async` with an async
    function that raises the group, then:

```python
    with pytest.raises(BenchmarkCellFailure) as exc_info:
        adapter_cls().run("anything")
    assert exc_info.value.category == "tool_failure"
    assert str(exc_info.value) == message
    assert exc_info.value.__cause__ is raised
```

- [ ] **Step 3: Write the offline real-session test**,
  `test_run_maps_a_failure_inside_a_real_stdio_session`, in
  `test_python_docs_mcp_adapter.py`.
  - Import `sys` and `ClientSession` from `mcp`, and name this test's silent
    child in the module docstring.
  - Write a dummy `index.db` in `tmp_path`.
  - Patch `ClientSession.initialize` with an async function that raises
    `BCF("tool_failure", "boom inside the session")`.
  - Under `pytest.raises(BenchmarkCellFailure)`, run
    `PythonDocsMcpAdapter(command=[sys.executable, "-c", "import sys; sys.stdin.read()"],
    index_path=..., timeout_seconds=20).run("anything")`.
  - Assert category `tool_failure`, message `boom inside the session`, and
    `isinstance(exc_info.value.__cause__, ExceptionGroup)`.

- [ ] **Step 4: Stop the smoke test from accepting crashes (N10).** At :305,
  assert `exc.category in {"tool_failure", "timeout"}, str(exc)`. Reword the
  comment above it: a symbol-only index makes `get_docs` fail, which must
  surface as `tool_failure`.

- [ ] **Step 5: Run the tests to verify they fail**

Run: `uv run --locked pytest tests/benchmarks/test_runner.py -q`
Expected: collection error, `ImportError: cannot import name 'cell_failure_from'`.

Run: `uv run --locked pytest tests/benchmarks/test_competitor_adapters.py tests/benchmarks/test_python_docs_mcp_adapter.py -q`
Expected: `12 failed` where a local index exists (11 elsewhere). Each failure
shows `'mcp_protocol_crash'`.

- [ ] **Step 6: Implement `cell_failure_from`** with that signature. Its
  docstring says mcp 2.3.0 nests a session failure two task groups deep. Rules:
  1. **Leaves:** walk `BaseExceptionGroup.exceptions` recursively, depth first.
  2. **Category:** the first leaf `BenchmarkCellFailure`'s category; else
     `timeout` if any leaf is a `TimeoutError`; else `mcp_protocol_crash`.
  3. **Text:** a leaf's text is `str(leaf)`. An empty `TimeoutError` reads
     `f"{label} did not complete within {timeout_seconds}s"`. Any other empty
     leaf has no text.
  4. **Message:** one leaf gives its text, or else its type name. Several give
     `"{Type}: {text}"` each (`{Type}` alone without text), joined by `"; "`
     in walk order.

- [ ] **Step 7: Replace the ladder in all five adapters.** Import
  `cell_failure_from` next to `BenchmarkCellFailure`. Keep the guard call and
  the `try` line; the three `except` clauses become one:

```python
        except Exception as exc:  # noqa: BLE001 - failures are benchmark artifacts
            raise cell_failure_from(
                exc, timeout_seconds=self._timeout_seconds, label=<label>
            ) from exc
```

| Adapter | `label` |
|---|---|
| `python_docs_mcp_adapter.py` | `"python-docs-mcp-server search_docs -> get_docs"` |
| `context7_adapter.py` | `"Context7 resolve-library-id -> query-docs"` |
| `gitmcp_adapter.py` | `"GitMCP retrieval flow"` |
| `deepwiki_adapter.py` | `"DeepWiki ask_question"` |
| `ref_tools_adapter.py` | `"Ref.tools ref_search_documentation -> ref_read_url"` |

  A `BaseExceptionGroup` holding `KeyboardInterrupt` or `CancelledError` still
  propagates, on purpose.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run --locked pytest tests/benchmarks -q`
Expected: all passed, including the smoke test where a local index exists.

- [ ] **Step 9: Commit**

```bash
git add benchmarks/runner.py benchmarks/adapters/ tests/benchmarks/test_runner.py tests/benchmarks/test_competitor_adapters.py tests/benchmarks/test_python_docs_mcp_adapter.py
git commit -m "fix: keep the real reason when a benchmark cell fails inside MCP task groups" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 2: Give MCP servers only the environment they need (finding 6 + N9)

**Files:**
- Modify: `benchmarks/adapters/context7_adapter.py:29-34` (docstring), `:282`, `:322-326`
- Modify: `benchmarks/adapters/python_docs_mcp_adapter.py:252`
- Test: `tests/benchmarks/test_competitor_adapters.py`, `test_python_docs_mcp_adapter.py`

**Interfaces:** none. Constructor signatures stay the same.

- [ ] **Step 1: Write the Context7 test** in `test_competitor_adapters.py`.
  - Define `class _TransportReached(Exception)`.
  - `test_context7_passes_only_the_key_and_only_in_key_mode` sets
    `CONTEXT7_API_KEY=k` and `UNRELATED_SECRET=s`.
  - It patches `context7_adapter._transport_factory` with a function that
    stores its keyword arguments in `recorded`, then raises `_TransportReached`.
  - It runs `asyncio.run(Context7Adapter(mode=..., key_mode=...,
    api_key=...)._run_async("q"))` under `pytest.raises(_TransportReached)`.
  - It asserts `recorded[field] == expected`:

| id | `mode` | `key_mode` | `api_key` | `field` | `expected` |
|---|---|---|---|---|---|
| key-stdio | `"stdio"` | `True` | `None` | `"env"` | `{"CONTEXT7_API_KEY": "k"}` |
| keyless-stdio | `"stdio"` | `False` | `None` | `"env"` | `None` |
| key-http | `"http"` | `True` | `None` | `"headers"` | `{"CONTEXT7_API_KEY": "k"}` |
| keyless-http | `"http"` | `False` | `None` | `"headers"` | `None` |
| keyless-ignores-explicit-key | `"stdio"` | `False` | `"explicit"` | `"env"` | `None` |

- [ ] **Step 2: Write the first-party test** in `test_python_docs_mcp_adapter.py`.
  - Define `class _TransportReached(Exception)` in this file too.
  - `test_server_env_holds_only_the_auto_index_switch_and_cache_location` sets
    `UNRELATED_SECRET=s`.
  - It patches `mcp.client.stdio.stdio_client`, which `_run_async` imports at
    call time, with a function that stores `params.env`, then raises.
  - It runs `asyncio.run(PythonDocsMcpAdapter()._run_async("q"))`.
  - With `XDG_CACHE_HOME=/tmp/bench-cache`, it asserts the env is
    `{"PYTHON_DOCS_MCP_DISABLE_AUTO_INDEX": "1", "XDG_CACHE_HOME": "/tmp/bench-cache"}`.
    With `XDG_CACHE_HOME` deleted, it asserts the env is
    `{"PYTHON_DOCS_MCP_DISABLE_AUTO_INDEX": "1"}`.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run --locked pytest tests/benchmarks/test_competitor_adapters.py tests/benchmarks/test_python_docs_mcp_adapter.py -q -k "only_the_key or only_the_auto_index"`
Expected: `6 failed, 1 passed`. Only `key-http` already sends just the key.

- [ ] **Step 4: Read the Context7 key only in key mode.**
  - `__init__` (:282): `self._api_key = (api_key if api_key is not None else
    os.environ.get(API_KEY_ENV)) if key_mode else None`.
  - `_run_async` (:326): `env = {API_KEY_ENV: self._api_key} if self._api_key
    else None`.
  - The comment at :322-325 says the SDK merges this dict over its safe
    defaults (`PATH`, `HOME`, ...), which `npx` needs.
  - The Auth docstring paragraph adds: "The key is read only in key mode.
    Neither mode forwards proxy or `npm_config_*` variables to `npx` any more;
    pass them explicitly if a run needs them."

- [ ] **Step 5: Narrow the first-party env** at :252 to
  `{DISABLE_AUTO_INDEX_ENV: "1"}`, plus `XDG_CACHE_HOME` when it is set and
  non-empty. Add a one-line comment: the SDK defaults carry `HOME` and
  `LOCALAPPDATA`, and platformdirs also reads `XDG_CACHE_HOME`, so the child
  opens the index this process checked.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run --locked pytest tests/benchmarks -q`
Expected: all passed, including the smoke test where a local index exists.

- [ ] **Step 7: Commit**

```bash
git add benchmarks/adapters/context7_adapter.py benchmarks/adapters/python_docs_mcp_adapter.py tests/benchmarks/test_competitor_adapters.py tests/benchmarks/test_python_docs_mcp_adapter.py
git commit -m "fix: give benchmark MCP servers only the environment they need" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 3: Report statistics, flag, alias and exclusions (finding 9)

**Files:**
- Modify: `benchmarks/report.py:9-16` (docstring), `:122-133`, `:185-256`, `:318-329`,
  `:372-409`, `:426-440`, `:597-657`, `:714-756`, `:761-828`
- Modify: `benchmarks/runner.py:1-20`, `:415`, `:423` (docstrings), `:153`, `:294`, `:599-616`, `:717`
- Modify comments: `benchmarks/adapters/eligibility.py:33-38`, `docs/benchmarks/competitor-manifest.template.yml:21-26`
- Test: `tests/benchmarks/test_report.py`, `test_runner.py`, `test_competitor_adapters.py`

**Interfaces:**
- Produces:
  - `_basic_stats(cells) -> dict[str, Any]` gains `success_latency_median_ms`,
    `success_latency_p95_ms`, `success_latency_min_ms`, `success_latency_max_ms`,
    `failed_latency_median_ms`, `failed_latency_cells` and `tokens_failed_cell`.
    It drops `latency_median_ms` and `latency_p95_ms`.
  - `_fmt_tokens(stats: dict[str, Any]) -> str`.
  - `_load_competitor_manifest(path: Path) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]`
    and `RunBundle.exclusions: list[dict[str, Any]]`.

- [ ] **Step 1: Make the Task 3 changes to existing tests** (see the table above).

- [ ] **Step 2: Write the report tests** in `test_report.py`. Import
  `_fmt_tokens`. Three helpers come first:
  - `_write_cell(run_dir, corpus_id, *, failed, latency_ms, tokens=None)`
    writes one `tool-a` scoring record. A failed cell has `status: "failed"`,
    `score: 0.0` and `error_category: "tool_failure"`; any other has
    `status: "placeholder"`. It also writes a latency record. With `tokens`, it
    writes a `counted`, non-approximate token record.
  - `_with_exclusions(tmp_path, exclusions)` starts from `_minimal_valid_run_dir`.
    It rewrites the manifest snapshot as
    `{"competitors": [{"id": "tool-a", "adapter": "fake"}], "exclusions": exclusions}`
    and returns the `REPORT.md` and `README-SUMMARY.md` texts.
  - `_token_stats(present=0, median=None, placeholder=0, approximated=0, failed_cell=0)`
    returns `tokens_present`, `median_client_wrapped_tokens`,
    `tokens_placeholder`, `tokens_approximated` and `tokens_failed_cell`.

  Then the tests:
  - **`test_latency_is_split_into_success_and_failed_cells`** writes cells
    `q001` to `q050`: the first 26 failed at 5.0 ms, the rest succeeded at
    1000.0 ms. The blended median is 5.0 today. It asserts:
    - REPORT contains `| tool-a | 1000.0 | 1000.0 | 1000.0 | 1000.0 | 5.0 (n=26) | 52.0% | 0.0% |`;
    - README contains `| 52.0% | 0.0% | 1000.0 | 1000.0 | 5.0 (n=26) |`.
  - **`test_token_median_excludes_counts_from_failed_cells`** writes `q001`
    succeeded with `tokens=100` and `q002` failed with `tokens=999999`.
    REPORT contains `| tool-a | 100, 1 failed-cell count(s) excluded |` and no
    `999999`.
  - **`test_report_renders_manifest_exclusions`** uses one entry:
    `{"id": "some-excluded-competitor", "reason": "vendor denied benchmarking
    permission", "terms_check": {"verdict": "forbidden-without-permission",
    "source_url": "https://example.test/terms"}, "decided_on": "2026-07-09"}`.
    It asserts:
    - REPORT has `### Excluded Competitors` and
      `| some-excluded-competitor | vendor denied benchmarking permission | forbidden-without-permission | 2026-07-09 | https://example.test/terms |`;
    - README has
      `- **Excluded competitors:** some-excluded-competitor (vendor denied benchmarking permission)`.
  - **`test_report_tolerates_sparse_exclusions`** (Review Focus 4) asserts both
    lines in each row:

| `exclusions` | REPORT contains | README contains |
|---|---|---|
| `None` | `No competitors were excluded from this run.` | `- **Excluded competitors:** none` |
| `[{"id": "x", "reason": "r"}]` | `\| x \| r \|  \|  \|  \|` | `- **Excluded competitors:** x (r)` |

  The table escapes the pipes in row 2. The string the test checks is
  `| x | r |  |  |  |`.
  - **`test_report_refuses_malformed_exclusions`** expects
    `pytest.raises(BenchmarkValidationError, match=...)`, with `"x"` matching
    `must be a list` and `["x"]` matching `entries must be mappings`.
  - **`test_fmt_tokens_renders_every_bucket_combination`** asserts
    `_fmt_tokens(stats) == expected` for each row. Row 3 is the form a live
    fixed-flow run shows.

| `stats` | `expected` |
|---|---|
| `_token_stats(present=1, median=100, approximated=1)` | `100, 1 approximation:true excluded` |
| `_token_stats(placeholder=2)` | `None (placeholder -- 2 cell(s) pending token-count integration)` |
| `_token_stats(placeholder=1, approximated=2)` | `None headline-eligible (approximation:true excludes 2 cell(s); 1 placeholder)` |
| `_token_stats(present=1, median=100, failed_cell=1)` | `100, 1 failed-cell count(s) excluded` |
| `_token_stats(placeholder=3, failed_cell=1)` | `None headline-eligible (1 failed-cell count(s) excluded; 3 placeholder)` |
| `_token_stats(present=1, median=100, placeholder=2, approximated=1, failed_cell=1)` | `100, 2 placeholder, 1 approximation:true excluded, 1 failed-cell count(s) excluded` |
| `_token_stats(approximated=1, failed_cell=1)` | `None headline-eligible (approximation:true excludes 1 cell(s); 1 failed-cell count(s) excluded; 0 placeholder)` |

- [ ] **Step 3: Write the alias test** in `test_runner.py`.
  `test_removed_baseline_alias_is_an_unknown_adapter` is parametrized over
  `"no-mcp-baseline"` and `"no_mcp_baseline"`. It runs
  `_manifest(tmp_path, [{"id": "baseline", "adapter": alias}])`, then asserts:
  - `summary["failed_cells"] == 1`;
  - `failures/baseline/q001.json` has `error.category == "tool_failure"`;
  - its message contains `not implemented in this runner`.

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run --locked pytest tests/benchmarks/test_runner.py tests/benchmarks/test_competitor_adapters.py tests/benchmarks/test_report.py -q`
Expected: `23 failed` (5 in `test_runner.py`, 4 in
`test_competitor_adapters.py`, 14 in `test_report.py`). The `_fmt_tokens`
rows fail with `TypeError`. The latency row today reads
`| tool-a | 5.0 | 1000.0 | 52.0% | 0.0% |`.

- [ ] **Step 5: Change `benchmarks/runner.py` and the stale comments.**
  - Delete the three `"external_provider_calls": False` entries and the two
    alias keys in `_ADAPTER_DISPATCH`.
  - Docstrings become: "the local fake adapter (issue #72; tests only)" at
    :4-5; "(the fake adapter)" at :415; "The mocked-plumbing fake adapter
    (issue #72), for tests only." at :423.
  - `eligibility.py:34` names only the `fake` and `python-docs-mcp-stdio` adapters.
  - The template comment at :21-26 names only the `python-docs-mcp-server`
    entry as not repeated. It says the fixed flow has no no-MCP baseline
    adapter, and that the agentic harness adds `no-docs` (design §5).

- [ ] **Step 6: Load the exclusions in `report.py`.**
  - `_load_competitor_manifest` also returns `exclusions`, with `None` read as
    `[]`.
  - Any other value that is not a list raises
    `BenchmarkValidationError("competitor manifest snapshot 'exclusions' must be a list")`.
  - An entry that is not a mapping raises
    `BenchmarkValidationError("competitor manifest snapshot 'exclusions' entries must be mappings")`.
  - `_load_run_bundle` stores the result in `RunBundle.exclusions`.

- [ ] **Step 7: Implement `_basic_stats` and `_fmt_tokens`** so that Step 2
  passes.
  - Succeeded means `status != "failed"`, and null latencies are skipped. The
    failed-cell median is over failed cells with non-null `latency_ms`.
  - The token buckets partition the cells:
    - approximated: a count marked as an approximation;
    - failed-cell: a failed cell with an exact count;
    - present: a succeeded cell with an exact count, the only cells that feed
      the median;
    - placeholder: the rest.

- [ ] **Step 8: Render `REPORT.md`.**
  - Token section: append "The median covers succeeded cells only; exact
    counts recorded for failed cells are excluded and counted alongside it."
  - Latency section: keep its title. Replace the paragraph at :629 with
    "Success latency (median, p95, min, max) covers succeeded cells only: the
    methodology measures latency from dispatch to the final answer, and a failed
    cell has none. Failed cells are shown separately, as a median with their
    count, next to the error rate, so a tool cannot look fast by failing fast,
    and a tool that fails slowly stays visible."
  - Latency headers: `Tool + Model`, `Success Median (ms)`, `Success p95 (ms)`,
    `Success Min (ms)`, `Success Max (ms)`, `Failed-Cell Median (ms)`,
    `Error Rate`, `Timeout Rate`. The failed-cell cell is
    `f"{_fmt_ms(median)} (n={count})"`.
  - Before `### Excluded From Correctness Denominator`, add
    `### Excluded Competitors`:
    - columns `Competitor`, `Reason`, `Terms Verdict`, `Decided On` and
      `Terms Source`, filled from `id`, `reason`, `terms_check.verdict`,
      `decided_on` and `terms_check.source_url`;
    - a missing value, or a `terms_check` that is not a mapping, gives an
      empty cell;
    - with no entries, write `No competitors were excluded from this run.`
  - Update the module docstring (:9-16).

- [ ] **Step 9: Render `README-SUMMARY.md`.**
  - Headers: `Tool + Model`, `Correctness`, `Error Rate`, `Timeout Rate`,
    `Success Median (ms)`, `Success p95 (ms)`, `Failed-Cell Median (ms)`, then
    tokens.
  - After `Repo commit`, add `- **Excluded competitors:** `, then the entries
    joined by `"; "`. Each entry is `id (reason)`, or `id` alone without a
    reason; with no entries, write `none`.

- [ ] **Step 10: Run the tests to verify they pass**

Run: `uv run --locked pytest tests/benchmarks -q`
Expected: all passed.

- [ ] **Step 11: Commit**

```bash
git add benchmarks/report.py benchmarks/runner.py benchmarks/adapters/eligibility.py docs/benchmarks/competitor-manifest.template.yml tests/benchmarks/test_report.py tests/benchmarks/test_runner.py tests/benchmarks/test_competitor_adapters.py
git commit -m "fix: report success and failed-cell latency apart and list exclusions" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 4: Count tokens with `claude-sonnet-5-5` and a valid envelope (finding 10 + N6)

**Files:**
- Modify: `benchmarks/adapters/claude_tokens.py:24-35` (docstring), `:63`, `:83-115`,
  `:138-166`, `:182-222`, `:241-277`
- Modify: `benchmarks/runner.py:353-405` (`_build_token_record`)
- Modify: `benchmarks/report.py` (`CellRecord`, `RunBundle`, `_load_run_bundle`,
  `_basic_stats`, `_fmt_tokens`, the token paragraph, the failures section)
- Test: `tests/benchmarks/test_claude_tokens.py`, `test_runner.py`, `test_report.py`

**Interfaces:**
- Consumes: Task 3's `_basic_stats` keys and `_fmt_tokens(stats)`.
- Produces:
  - `DEFAULT_COUNT_MODEL = "claude-sonnet-5-5"`. `ANTHROPIC_API_VERSION` stays
    `"2023-06-01"`.
  - `count(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> int`
    on `TokenCounter`, `FakeTokenCounter` (which ignores `tools`) and
    `LiveClaudeTokenCounter`.
  - `_tool_stubs(envelope: list[dict[str, Any]]) -> list[dict[str, Any]] | None`
    and `_error_body(raw: bytes, api_key: str) -> str`.
  - `build_client_wrapped_envelope(...)` keeps its `(envelope, approximation)`
    shape; `approximation` is always `True`.
  - Every token record gains `count_model` and `anthropic_version`.
  - `CellRecord.token_status: str | None`,
    `RunBundle.token_failure_records: list[dict[str, Any]]`, and the
    `_basic_stats` key `tokens_count_failed`.

- [ ] **Step 1: Make the Task 4 changes to existing tests** (see the table above).
  - The count test adds `assert "system prompt" in result.notes`.
  - The live-endpoint test adds `captured["body"]["model"] == "claude-sonnet-5-5"`
    and `"tools" not in captured["body"]`.

- [ ] **Step 2: Write the new counter tests** in `test_claude_tokens.py`.
  - Import `io` and `urllib.error`.
  - `_enable_live(monkeypatch)` sets `LIVE_PROVIDERS_ENABLED_ENV` to `"1"` and
    `ANTHROPIC_KEY_ENV` to `"fake-not-a-real-key"`.
  - `_call(tool, arguments)` returns `{"tool": tool, "arguments": arguments,
    "result": {"ok": True}, "is_error": False}`.

  - **`test_envelope_pairs_every_tool_result_with_a_preceding_tool_use`**
    builds the envelope for `_call("search_docs", {"query": "TaskGroup"})` and
    `_call("get_docs", {"slug": "x"})`. It asserts:
    - `approximation is True`, and the roles are `["user", "assistant", "user"]`;
    - `envelope[1]["content"]` holds the `(type, name, input)` triples
      `("tool_use", "search_docs", {"query": "TaskGroup"})` and
      `("tool_use", "get_docs", {"slug": "x"})`;
    - every block of `envelope[2]["content"]` is a `tool_result`, and their
      `tool_use_id`s equal the `tool_use` ids, in order.
  - **`test_count_cell_tokens_sends_tool_stubs_only_with_tool_use_blocks`**
    (Review Focus 2) uses a recording counter that appends each `tools`
    argument to `seen` and returns 7. It counts
    `[_call("search_docs", {}), _call("get_docs", {}), _call("search_docs", {})]`
    once plainly, then once with
    `provider_mock_payload={"provider": "openai", "mock": True}`. It asserts:
    - `seen == [[{"name": "search_docs", "input_schema": {"type": "object"}},
      {"name": "get_docs", "input_schema": {"type": "object"}}], None, None, None]`;
    - the first result's notes contain `name-only tool stubs`.
  - **`test_live_claude_token_counter_puts_a_safe_http_error_in_the_failure`**
    patches `urllib.request.urlopen` to raise
    `urllib.error.HTTPError(request.full_url, status, "error", None, io.BytesIO(body))`.
    It asserts that `str(exc_info.value)` ends with `expected` and does not
    contain `fake-not-a-real-key`:

| `status` | `body` | `expected` (end of the message) |
|---|---|---|
| 400 | `b'{"error":{"message":"bad   tool_use_id"}}'` | `HTTP 400: {"error":{"message":"bad tool_use_id"}}` |
| 429 | `b'{"error":{"message":"rate limited"}}'` | `HTTP 429: {"error":{"message":"rate limited"}}` |
| 401 | `b'{"error":{"message":"bad key fake-not-a-real-key"}}'` | `HTTP 401: {"error":{"message":"bad key [redacted]"}}` |
| 500 | `b"x" * 1000` | `"HTTP 500: " + "x" * 300` |

  - **`test_live_claude_token_counter_sends_tools_when_given`**: given
    `tools=[{"name": "search_docs", "input_schema": {"type": "object"}}]`, the
    captured request body's `tools` equals that list.
  - **`test_live_claude_token_counter_raises_benchmark_cell_failure_on_a_non_json_body`**
    (Review Focus 3): `urlopen` returns a `_FakeHttpResponse` whose `_body` is
    `b"<html>proxy login</html>"`. The test expects
    `pytest.raises(BenchmarkCellFailure, match="not JSON")`.

- [ ] **Step 3: Write the runner tests** in `test_runner.py` (import `http.client`).
  - Both existing token tests assert `(record["count_model"],
    record["anthropic_version"]) == ("claude-sonnet-5-5", "2023-06-01")`.
  - Add `test_token_record_is_failed_and_names_its_count_model_when_the_call_fails`.
    It sets the live env, as the fake-counter test does, and patches
    `LiveClaudeTokenCounter` with a counter whose `count` raises. It is
    parametrized over two exceptions:
    - `BenchmarkCellFailure("tool_failure", "HTTP 400: bad envelope")`;
    - `http.client.IncompleteRead(b"partial")`, which today aborts
      `run_benchmark`.
  - For each, `tokens/no-mcp/q001.json` must have `status == "failed"`, notes
    containing `f"{type(raised).__name__}: "`, and both new fields.

- [ ] **Step 4: Write the report tests** in `test_report.py`.
  - `_token_stats` gains `count_failed=0`, stored as `tokens_count_failed`, so
    every row carries it.
  - Add two rows to `test_fmt_tokens_renders_every_bucket_combination`:
    - `_token_stats(count_failed=1)` gives `None headline-eligible (1 count(s) failed; 0 placeholder)`;
    - `_token_stats(present=1, median=100, count_failed=1)` gives `100, 1 count(s) failed`.
  - Add `test_failed_token_count_renders_as_failed_and_is_listed`. It rewrites
    `tokens/tool-a/q001.json` in `_minimal_valid_run_dir` as a `failed` record
    with `tool_model_key: "tool-a"` and the note
    `Claude count-tokens call failed: BenchmarkCellFailure: HTTP 400: bad tool_use_id`.
    It asserts:
    - REPORT contains `| tool-a | None headline-eligible (1 count(s) failed; 0 placeholder) |`,
      `### Token-Count Failures` and `| tool-a | tool-a | q001 | <note> |`;
    - REPORT does not contain `pending token-count integration`;
    - README contains `1 count(s) failed`.

- [ ] **Step 5: Run the tests to verify they fail**

Run: `uv run --locked pytest tests/benchmarks/test_claude_tokens.py tests/benchmarks/test_runner.py tests/benchmarks/test_report.py -q`
Expected: `19 failed` (12 in `test_claude_tokens.py`, 4 in `test_runner.py`,
3 in `test_report.py`). The live test shows
`'claude-3-5-sonnet-20241022' == 'claude-sonnet-5-5'`.

- [ ] **Step 6: Change `benchmarks/adapters/claude_tokens.py`.**
  - `DEFAULT_COUNT_MODEL = "claude-sonnet-5-5"`. The live request body adds
    `"tools": tools` only when `tools` is truthy.
  - `_error_body(raw, api_key)` does four things, in order:
    - decode with `"utf-8", "replace"`;
    - replace `api_key` with `"[redacted]"`;
    - collapse whitespace runs to one space;
    - cut the result to 300 characters.
  - `HTTPError` is caught before the existing clause, with the message
    `f"Anthropic count-tokens call failed: HTTP {exc.code}: {_error_body(exc.read(), api_key)}"`.
  - A 200 response that will not decode or parse (`ValueError`) fails with
    `f"Anthropic count-tokens response is not JSON: {_error_body(raw, api_key)}"`.
  - With tool calls, the envelope holds three messages: the user prompt, one
    assistant message of `tool_use` blocks, and one user message of only the
    matching `tool_result` blocks.
    - Each `tool_use` block has `id`, `name` (the call's `tool`) and `input`
      (its `arguments`, or `{}` when they are not a dict).
    - The id is `f"benchmark-cell-{index}-{tool}"`, used for both `id` and
      `tool_use_id`.
    - The approximation flag is `True` in every branch.
  - `_tool_stubs(envelope)` returns one stub per distinct `tool_use` name, in
    first-seen order, or `None` when the envelope holds no `tool_use` block.
    `count_cell_tokens` sends it only with the wrapped envelope.
  - Notes with tool calls: "approximation: fixed-flow envelope with name-only
    tool stubs (the count includes the API's tool-use preamble) and no real tool
    definitions or system prompt (PUBLIC-BENCHMARK-METHODOLOGY.md 'Token
    Measurement')".
  - Notes without them: "approximation: fixed-flow envelope with no real tool
    definitions or system prompt (PUBLIC-BENCHMARK-METHODOLOGY.md 'Token
    Measurement')".
  - The "Envelope exactness" docstring says every count here is an
    approximation; exact post-run counting is the new harness's job (design §8).

- [ ] **Step 7: Change `_build_token_record`.** Add `"count_model":
  DEFAULT_COUNT_MODEL` and `"anthropic_version": ANTHROPIC_API_VERSION` to
  `base_fields`. Catch `Exception`, not only `BenchmarkCellFailure`, and write
  the note `f"Claude count-tokens call failed: {type(exc).__name__}: {exc}"`.

- [ ] **Step 8: Read the token status in `report.py`.**
  - `CellRecord.token_status` is `token_record.get("status")`.
  - `RunBundle.token_failure_records` holds the records whose status is
    `"failed"`. `tokens_count_failed` counts them, and they leave the
    placeholder bucket.
  - Token paragraph: append "A failed count is shown as failed, never as
    pending, and listed under 'Failures And Exclusions'."
  - After `### Excluded Competitors`, add `### Token-Count Failures`, with the
    columns `Competitor`, `Tool + Model`, `Corpus ID` and `Notes`. With none,
    write `No token-count failures in this run.`

- [ ] **Step 9: Run the tests to verify they pass**

Run: `uv run --locked pytest tests/benchmarks -q`
Expected: all passed.

- [ ] **Step 9b (optional; the owner runs it, never CI or an agent).** This
  checks the stub premise live. It is free, but needs `ANTHROPIC_API_KEY`
  exported. It counts the same envelope with stubs, then without.

```bash
BENCHMARK_LIVE_PROVIDERS_ENABLED=1 uv run --locked python - <<'EOF'
from benchmarks.adapters.claude_tokens import (
    LiveClaudeTokenCounter, _tool_stubs, build_client_wrapped_envelope)
from benchmarks.runner import BenchmarkCellFailure

call = {"tool": "search_docs", "arguments": {"query": "TaskGroup"},
        "result": {"hits": []}, "is_error": False}
envelope, _ = build_client_wrapped_envelope("What is asyncio.TaskGroup?", tool_calls=[call])
for tools in (_tool_stubs(envelope), None):
    try:
        print(LiveClaudeTokenCounter().count(envelope, tools=tools))
    except BenchmarkCellFailure as exc:
        text = str(exc)
        print(f"failed: {text.split(': ')[1]} (must define tools: {'must define tools' in text})")
EOF
```

Expected:
- Line 1 (with stubs) is an integer.
- Line 2 (without stubs) is either `failed: HTTP 400 (must define tools: True)`,
  which confirms the premise, or an integer, which refutes it. Keep the stubs
  either way, for fidelity.

Paste both lines into the PR, never the key or a response body. A failure on
line 1 blocks the first live count, not the merge.

- [ ] **Step 10: Commit**

```bash
git add benchmarks/adapters/claude_tokens.py benchmarks/runner.py benchmarks/report.py tests/benchmarks/test_claude_tokens.py tests/benchmarks/test_runner.py tests/benchmarks/test_report.py
git commit -m "fix: count benchmark tokens with claude-sonnet-5-5 in a valid tool envelope" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 5: One shared URL module (finding 11)

**Name:** `benchmarks/urls.py`. It names what it parses: URLs and labeled
values in tool or answer text. The new harness's grounding step (design §9.2)
imports it. A name like `grounding.py` would promise the (version, page,
anchor) matching that only the new harness builds.

**Files:**
- Create: `benchmarks/urls.py` (stdlib `re` only); `tests/benchmarks/test_urls.py`
- Modify: `benchmarks/adapters/gitmcp_adapter.py:40,58,112-134`,
  `ref_tools_adapter.py:45,65,119-141`, `context7_adapter.py:41,63-66,124-152`
- Test: `tests/benchmarks/test_competitor_adapters.py`

**Interfaces:**
- Produces: `extract_urls(text: str) -> list[str]`,
  `first_web_url(text: str) -> str | None`,
  `labeled_value(text: str, label: str) -> str | None`.

- [ ] **Step 1: Write the URL tests** in `tests/benchmarks/test_urls.py`.
  - Module constants:
    - `ITERTOOLS = "https://docs.python.org/3/library/itertools.html#itertools.pairwise"`;
    - `README = "https://github.com/python/cpython/blob/main/README.rst"`;
    - `OS` and `RE`, the `os.html` and `re.html` library pages under
      `https://docs.python.org/3/library/`;
    - `WIKIPEDIA = "https://en.wikipedia.org/wiki/Python_(programming_language)"`.
  - `CONTEXT7_WITH_FILTER_NOTE` is a team-filter note that mentions
    `https://context7.com/dashboard?tab=policies`, followed by
    `Available Libraries:`, `- Title: Python`,
    `- Context7-compatible library ID: /python/cpython` and `- Description: ...`
    lines.
  - `test_first_web_url_drops_delimiters_but_keeps_balanced_parentheses`
    asserts `first_web_url(text) == expected`:

| id | `text` | `expected` |
|---|---|---|
| markdown-link-then-period | `f"[pairwise]({ITERTOOLS})."` | `ITERTOOLS` |
| gitmcp-doc-search | `f"#### (README.rst)[{README}] (Score: 0.53)"` | `README` |
| angle-autolink-then-comma | `f"<{OS}>,"` | `OS` |
| bold-markers | `f"**{RE}**"` | `RE` |
| balanced-parenthesis-kept | `WIKIPEDIA` | `WIKIPEDIA` |

  - `test_labeled_value_finds_the_context7_id_behind_a_filter_note`:
    `labeled_value(CONTEXT7_WITH_FILTER_NOTE, "- Context7-compatible library ID") == "/python/cpython"`.
  - `test_labeled_value_reads_the_url_line_not_the_overview` uses
    `text = f"overview: see https://example.org/blog.\nurl: {OS}  \r\nmoduleId: py\n"`.
    It asserts `labeled_value(text, "url") == OS` and
    `labeled_value(text, "title") is None`.
  - `test_extract_urls_returns_bare_urls_in_text_order`:
    `extract_urls(f"see {OS}?q=walk! and <{RE}>") == [f"{OS}?q=walk", RE]`.

- [ ] **Step 2: Write the adapter wiring tests** in `test_competitor_adapters.py`.
  - In `test_context7_flow_falls_back_to_text_regex_when_no_structured_candidates`,
    the resolve text becomes a 3.2.3-shaped list behind the filter note. The
    assertion stays `libraryId == "/python/cpython"`.
  - `test_context7_flow_ignores_an_unlabeled_library_id` resolves to
    `_result(text="Best match: /python/cpython (Python stdlib)")`. It asserts
    `result.answer == ""` and `len(result.tool_calls) == 1`.
  - `test_gitmcp_flow_fetches_the_doc_search_url_without_its_bracket`: doc
    search returns `_result(text=f"#### (README.rst)[{readme}] (Score: 1)")`.
    It asserts `session.calls[1] == ("fetch_generic_url_content", {"url": readme})`.
  - `test_ref_tools_flow_reads_the_url_line_not_the_overview`: search returns
    `_result(text=f"overview: see https://example.org/blog.\nurl: {_PATHLIB_DOC_URL}\n")`.
    It asserts `session.calls[1] == ("ref_read_url", {"url": _PATHLIB_DOC_URL})`.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run --locked pytest tests/benchmarks/test_urls.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'benchmarks.urls'`.

Run: `uv run --locked pytest tests/benchmarks/test_competitor_adapters.py -q -k "regex or unlabeled or bracket or url_line"`
Expected: `4 failed`, on `/context7.com/dashboard`, two tool calls,
`...README.rst]` and `https://example.org/blog.`.

- [ ] **Step 4: Create `benchmarks/urls.py`.**
  - `extract_urls` returns, in text order, every match of
    ``https?://[^\s<>"'`{}|\\^\[\]]+``. From each match it keeps dropping a
    final `?!.,:*_~` (GFM), or a final `)` while the URL has more `)` than `(`.
  - `first_web_url` returns the first match, or `None`.
  - `labeled_value` returns group 1 of the first `text.splitlines()` line whose
    `rstrip()` fully matches `re.escape(label) + r": (\S+)"`, else `None`.

- [ ] **Step 5: Wire the three adapters.** The structured branches stay first.
  Delete `import re` and each module regex.
  - `gitmcp_adapter._extract_first_url` returns `first_web_url(text)`.
  - `ref_tools_adapter._extract_first_url` returns
    `labeled_value(text, "url") or first_web_url(text)`.
  - `context7_adapter._extract_library_id` returns
    `labeled_value(text, "- Context7-compatible library ID")`, with no
    fallback.
  - Its docstring says:
    - the labeled line is the pinned 3.2.3 format (`utils.ts#L24-L30`);
    - HTTP mode's hosted output is not pinned to 3.2.3;
    - if the format drifts, the answer is empty and `score` auto-scores it 0.0.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run --locked pytest tests/benchmarks -q`
Expected: all passed.

- [ ] **Step 7: Commit**

```bash
git add benchmarks/urls.py benchmarks/adapters/gitmcp_adapter.py benchmarks/adapters/ref_tools_adapter.py benchmarks/adapters/context7_adapter.py tests/benchmarks/test_urls.py tests/benchmarks/test_competitor_adapters.py
git commit -m "fix: parse competitor URLs and library ids with one shared module" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 6: Score against the run's own corpus snapshot

**Files:**
- Modify: `benchmarks/scoring.py` (new `_run_corpus`, `score_run` :110-229,
  `ingest_adjudication_verdicts` :232-339, docstrings)
- Modify: `benchmarks/__main__.py:93-96,107-112,137-142`
- Test: `tests/benchmarks/test_scoring.py`

**Interfaces:**
- Produces:
  - `score_run(run_dir: Path, *, corpus_path: Path | None = None, schema_path: Path) -> dict[str, Any]`
  - `ingest_adjudication_verdicts(run_dir: Path, verdicts_path: Path, *, corpus_path: Path | None = None, schema_path: Path) -> dict[str, Any]`
  - `_run_corpus(run_dir: Path, corpus_path: Path | None) -> tuple[Path, str]`,
    which returns the snapshot and its sha256.
  - `rollups` and `scoring-rollups.json` gain `"corpus_sha256"`.

- [ ] **Step 1: Make the Task 6 fixture changes.** Add `_write_snapshot(run_dir)`,
  which copies `REAL_FIXTURE_PATH` byte for byte to
  `run_dir / "snapshots" / "corpus.yml"`. Call it from `_build_run_dir` and in
  the two hand-built run dirs.

- [ ] **Step 2: Write the new tests** in `test_scoring.py`. Import `hashlib`.
  - Constants: `FIXTURE_SHA256` is the sha256 of `REAL_FIXTURE_PATH`'s bytes;
    `VERDICT = [{"competitor_id": "tool-a", "corpus_id": "SYN-EX-001", "score": 1.0}]`;
    `MISMATCH = "does not match this run's corpus snapshot"`.
  - Helpers:
    - `_queued_run(tmp_path)` is `_build_run_dir` with one placeholder
      `tool-a`/`SYN-EX-001` cell answering `"candidate answer"`;
    - `_changed_corpus(tmp_path)` writes the fixture with its first
      `"is a made-up placeholder API"` replaced by `"is a changed answer key"`.
  - The tests:
    - `test_score_run_defaults_to_the_run_snapshot_and_records_its_hash`:
      `score_run(run_dir, schema_path=REAL_SCHEMA_PATH)` returns
      `rollups["corpus_sha256"] == FIXTURE_SHA256`, and the same hash is in
      `scoring-rollups.json`.
    - `test_score_run_refuses_a_corpus_that_differs_from_the_snapshot`:
      `score_run(..., corpus_path=_changed_corpus(tmp_path), ...)` raises
      `BenchmarkValidationError` matching `MISMATCH`.
    - `test_adjudicate_refuses_a_changed_corpus_before_writing_any_verdict`:
      `ingest_adjudication_verdicts` with `VERDICT` and the changed corpus
      raises matching `MISMATCH`. The scoring record's `score` stays `None`.
    - `test_score_and_adjudicate_refuse_a_run_without_a_corpus_snapshot`
      (Review Focus 5): the scoring and transcript records are written with
      `_write_json` and no snapshot. `_score(run_dir)`, then
      `_adjudicate(run_dir, <VERDICT file>)`, each raise matching
      `no corpus snapshot`, and `score` stays `None`.
    - `test_cli_score_defaults_corpus_to_the_run_snapshot`: run
      `python -m benchmarks score --run-dir <run> --schema <schema>`, with no
      `--corpus`, in a subprocess. It exits 0, and its JSON's
      `rollups.corpus_sha256 == FIXTURE_SHA256`.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run --locked pytest tests/benchmarks/test_scoring.py -q`
Expected: `5 failed`: `TypeError` (missing `corpus_path`), three
`DID NOT RAISE`, and an exit code 2 that requires `--corpus`.

- [ ] **Step 4: Add `_run_corpus`.** The snapshot is
  `run_dir / "snapshots" / "corpus.yml"`.
  - A missing snapshot raises `BenchmarkValidationError(f"run directory has no
    corpus snapshot at {snapshot}; score only run directories written by
    'python -m benchmarks run'")`.
  - With `corpus_path=None`, return the snapshot and its sha256.
  - A path that is not a file raises
    `BenchmarkValidationError(f"corpus file does not exist: {corpus_path}")`.
  - Another sha256 raises
    `BenchmarkValidationError(f"--corpus {corpus_path} does not match this
    run's corpus snapshot {snapshot} (sha256 {given} != {expected}); score
    against the snapshot, or rerun the benchmark with the changed corpus")`.
  - Otherwise, return the snapshot and its hash.

- [ ] **Step 5: Use it in both entry points** (Ruling 6).
  - `score_run` keeps its "no scoring placeholder files" refusal first, then
    resolves the snapshot and loads answer keys from it.
  - `ingest_adjudication_verdicts` runs `_load_verdicts`, then `_run_corpus`,
    then loads answer keys, and only then applies verdicts.
  - Both set `rollups["corpus_sha256"]` before writing `scoring-rollups.json`.
  - Update the docstrings.

- [ ] **Step 6: Make `--corpus` optional** in `benchmarks/__main__.py` for
  `score` and `adjudicate`: `default=None`, with the help text "Answer-key
  corpus; defaults to the run's snapshots/corpus.yml. A different file is
  refused unless its sha256 matches that snapshot." Reword the comment at
  :93-96.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run --locked pytest tests/benchmarks -q`
Expected: all passed.

- [ ] **Step 8: Commit**

```bash
git add benchmarks/scoring.py benchmarks/__main__.py tests/benchmarks/test_scoring.py
git commit -m "fix: score benchmark runs against their own corpus snapshot" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 7: Widen the regression gate's tripwire

**Files:**
- Modify: `benchmarks/regression.py:1,109-137`
- Test: `tests/benchmarks/test_regression.py`

**Interfaces:**
- Produces: `FORBIDDEN_AUDIT_EVENTS: frozenset[str]`, the module-level
  `blocked_attempts: list[str]`, and `deny_network(event: str, args:
  tuple[object, ...]) -> None`, whose signature is unchanged.

- [ ] **Step 1: Write the tests** in `test_regression.py`. Import `sys`,
  `benchmarks.regression as regression`, and `FORBIDDEN_AUDIT_EVENTS`. The
  existing test stays as it is. Every test patches `regression.blocked_attempts`
  first.
  - **`test_forbidden_audit_events_cover_network_and_process_creation`**:
    `FORBIDDEN_AUDIT_EVENTS` equals exactly these 18 names:
    - `socket.bind`, `socket.connect`, `socket.getaddrinfo`,
      `socket.gethostbyaddr`, `socket.gethostbyname`, `socket.getnameinfo`,
      `socket.sendmsg`, `socket.sendto`;
    - `subprocess.Popen`, `os.exec`, `os.fork`, `os.forkpty`,
      `os.posix_spawn`, `os.spawn`, `os.startfile`, `os.system`, `pty.spawn`;
    - `webbrowser.open`.
  - **`test_deny_network_raises_and_records_the_attempt`**, over `socket.sendto`
    and `subprocess.Popen`: `deny_network(event, ())` raises `RuntimeError`
    matching `forbidden`, and `blocked_attempts == [event]`.
  - **`test_deny_network_lets_local_file_and_database_events_through`**:
    `deny_network("open", ("index.db", "r", 0))` and
    `deny_network("sqlite3.connect", ("index.db",))` return, and
    `blocked_attempts == []`.

```python
def test_main_fails_when_library_code_swallowed_a_blocked_attempt(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(regression, "blocked_attempts", ["left over from another test"])
    monkeypatch.setattr(sys, "addaudithook", lambda hook: None)  # never hook pytest itself

    def _swallowing_evaluate(index, corpus, schema):
        try:
            deny_network("socket.connect", (None, ("192.0.2.1", 443)))
        except RuntimeError:
            pass  # library code that hides the tripwire
        return {"corpus_sha256": "x", "index_versions": [], "index_documents": 0, "cases": {}}

    monkeypatch.setattr(regression, "evaluate", _swallowing_evaluate)
    output = tmp_path / "evaluation.json"
    monkeypatch.setattr(sys, "argv", ["regression", "--output", str(output), "--record"])
    assert regression.main() == 1
    err = capsys.readouterr().err
    assert "blocked network or process access: socket.connect" in err
    assert "left over" not in err
    assert not output.exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --locked pytest tests/benchmarks/test_regression.py -q`
Expected: collection error, `ImportError: cannot import name 'FORBIDDEN_AUDIT_EVENTS'`.

- [ ] **Step 3: Implement the tripwire.**
  - `FORBIDDEN_AUDIT_EVENTS` holds the 18 names. A one-line comment says a
    child process starts without this hook.
  - `blocked_attempts: list[str] = []` sits at module level.
  - `deny_network` appends `event`, then raises
    `RuntimeError(f"Network and process access is forbidden during offline
    evaluation: {event}")`. The existing `match="Network"` still holds.
  - `main()` calls `blocked_attempts.clear()` right after
    `sys.addaudithook(deny_network)`, so the result does not depend on test
    order.
  - Right after `evaluate(...)`, before anything is written: if the list is
    not empty, raise
    `RuntimeError(f"blocked network or process access: {', '.join(sorted(set(blocked_attempts)))}")`
    inside the existing `try`, which prints it and returns 1.
  - The module docstring says the hook is a tripwire, not a sandbox (PEP 578).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --locked pytest tests/benchmarks -q`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/regression.py tests/benchmarks/test_regression.py
git commit -m "fix: widen the offline regression gate's network and process tripwire" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 8: Methodology amendment and changelog

**Files:**
- Modify: `docs/benchmarks/PUBLIC-BENCHMARK-METHODOLOGY.md` (append at the end)
- Modify: `CHANGELOG.md:7-21`

**Interfaces:** none.

- [ ] **Step 1: Append the amendment** to `docs/benchmarks/PUBLIC-BENCHMARK-METHODOLOGY.md`.

```markdown
## Amendment 2026-10-30: How Fixed-Flow Results Are Computed

Applies to fixed-flow runs from this date
(`docs/plans/2026-10-30-benchmark-integrity.md`). Numbers made before it are
not comparable with numbers made after it.

- **Latency.** Median, p95, minimum and maximum cover succeeded cells only:
  latency runs from dispatch to the final answer, and a failed cell has none.
  Failed cells are reported separately, as a median with their count, next to
  the error rate.
- **Token medians.** A median covers succeeded cells with an exact count.
  Every fixed-flow count is an approximation, because its envelope has
  name-only tool stubs and no system prompt, so none enters a headline median.
  Counts use `claude-sonnet-5-5`, and each token record names its count model
  and API version.
- **Count failures.** A failed count is reported as failed, never as pending,
  and listed with its error under "Failures And Exclusions".
- **Failure reasons.** Inside nested task groups, the first recorded cell
  failure sets the category, and every reason is kept.
- **Corpus.** Scoring uses the run's corpus snapshot, refuses any other file
  unless its sha256 matches, and writes that hash to `scoring-rollups.json`.
- **Baseline.** The fixed flow has no no-MCP baseline. The removed alias only
  echoed the prompt, so earlier rows under that name were not model answers.
  The agentic harness adds `no-docs` (design §5).
- **Provider-call flag.** Result files no longer carry
  `external_provider_calls`; it was a constant, not a measurement.
```

- [ ] **Step 2: Add the changelog entries** under `## [Unreleased]`. Append to
  `### Changed`:

```markdown
- The benchmark harness (development tooling, not part of the package) now
  reports success latency and failed-cell latency separately, lists manifest
  exclusions, keeps the real reason when a cell fails inside MCP task groups,
  counts tokens with `claude-sonnet-5-5` in a valid tool envelope marked as an
  approximation, and scores runs against their own corpus snapshot. Its offline
  regression gate fails on any network or process attempt. Earlier benchmark
  numbers are not comparable.
```

  Insert between `### Changed` and `### Fixed`:

```markdown
### Removed

- Benchmark harness: the `no-mcp-baseline` adapter alias, which only echoed the
  prompt, and the `external_provider_calls` field, a constant rather than a
  measurement.
```

  Add after `### Fixed`:

```markdown
### Security

- The MCP servers that the benchmark harness starts now get the MCP SDK's
  default variables plus only: `CONTEXT7_API_KEY` for the Context7 server (npm
  code), and only in key mode; `PYTHON_DOCS_MCP_DISABLE_AUTO_INDEX` and, when
  set, `XDG_CACHE_HOME` for the first-party server. Proxy and `npm_config_*`
  variables are no longer forwarded.
```

- [ ] **Step 3: Verify**

Run: `grep -c "Amendment 2026-10-30" docs/benchmarks/PUBLIC-BENCHMARK-METHODOLOGY.md`
Expected: `1`

Run: `uv run --locked pytest tests/benchmarks/test_report.py -q`
Expected: all passed.

- [ ] **Step 4: Commit**

```bash
git add docs/benchmarks/PUBLIC-BENCHMARK-METHODOLOGY.md CHANGELOG.md
git commit -m "docs: amend the benchmark methodology for how results are now computed" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Final: gate and PR (no commit)

- [ ] **Step 1: Run the gate fresh.**

| Run | Expected |
|---|---|
| `uv run --locked ruff check src/ tests/ benchmarks/ ops/ .github/scripts/` | `All checks passed!` |
| `uv run --locked pyright src/ benchmarks/` | `0 errors` |
| `uv run --locked pytest --tb=short -q` | all passed |
| `uv run --locked python-docs-mcp-server doctor` | Every check passes except `Index database`, which may fail (exit 1) when no index exists; a symbol-only index passes. Paste the output as it is. Nothing under `src/` changed. |
| CI `Product regression` on the PR head | green. It builds the full 3.11-3.13 index and runs the tripwire for real. |

- [ ] **Step 2: Check for leftovers.**

Run: `grep -rn "no-mcp-baseline\|no_mcp_baseline\|external_provider_calls\|claude-3-5-sonnet\|fake/baseline" benchmarks/ docs/benchmarks/competitor-manifest.template.yml`
Expected: no output.

- [ ] **Step 3: Rebase if a Wave 1 PR landed first.** Only `CHANGELOG.md`
  should conflict. Keep every entry, then rerun Step 1.

- [ ] **Step 4: Push and open the PR** against `main`, titled
  `fix: benchmark harness integrity (audit PR 9)`, with the label
  `supervisor-review`. The body follows `.github/PULL_REQUEST_TEMPLATE/agent.md`:
  - `Refs #63`; one acceptance checkbox per task, with its test names as
    evidence; the gate output; `CodeRabbit review: Pending.`
  - `## Why this approach`: the rulings, in one paragraph.
  - `## Why this triggered supervisor review`:
    - the triggers: changed existing assertions and fixtures (§2), more than
      500 lines, and design choices beyond the research (§7);
    - the owner-approval sentence;
    - the labeled table of test changes. For the renamed test, write
      "renamed; old name removed".
  - **Probe evidence:**
    - `connect` and `connect_ex` both raise `socket.connect`;
    - `os.spawnv` on POSIX raises `os.fork`;
    - `webbrowser.open` is not observable without a browser;
    - `evaluate()` fired none of the 18 events;
    - the URL prototype passed all six rows;
    - `HTTPError` subclasses `URLError`, so its body is lost today;
    - a non-JSON 200 body escapes the counter today;
    - the narrowed env opened the index on macOS;
    - anyio's `BrokenResourceError()` has empty text.
  - **Product decision:** kind `bugfix`.
    - Baseline: lost failure reasons, the whole environment handed to npm
      code, a blended latency median, a retired count model with an invalid
      envelope, punctuation kept in URLs, any corpus scoreable, and a
      three-event tripwire.
    - Target: each of these fixed, with its tests as evidence.
    - Non-goals: "Left to the new harness"; no change under `src/`; no live
      runs.
    - Independent verifier check: rerun the gate on the PR head, confirm
      `Product regression` is green, and review each contract change.
    - Unresolved limitations: Windows index discovery is reasoned from source,
      not run; the stub premise is unverified until Task 4 Step 9b; there was
      no local full-index run, which CI covers.
    - Note that numbers are not comparable with earlier runs, and give the
      head and base SHAs.
  - The closing line `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

  Never self-merge.
