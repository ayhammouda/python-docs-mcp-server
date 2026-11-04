# Agentic Benchmark: Methodology, Model Matrix and Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land design §12 steps (2) and (3): the methodology amendment and
model matrix, then a thin agentic tool loop on the official SDKs with a
spend ledger, a generic MCP bridge and a mocked end-to-end CI test.

**Architecture:** Two PRs, in order.
- **PR A** (`docs/agentic-methodology-matrix`, Tasks 1–2) is docs plus a
  small loader change: the methodology amendment, the §7.1 model matrix
  and the §12 smoke-run row.
- **PR B** (`feat/agentic-harness`, Tasks 3–11) adds the
  `benchmarks/agentic/` package.
  - A host orchestrator holds the ledger and never a key. It spawns one
    worker process per (model, system).
  - Host and worker exchange length-prefixed JSON frames on a dedicated
    channel. Everything else the worker writes is redacted for its own
    key.
  - Each worker runs the tool loop with one model client and one MCP
    bridge.
  - PR 4 only swaps the local-process launcher for a container launcher,
    and adds the Ollama client.
- The run writes the existing artifact layout, so `score` and `report`
  read it unchanged. The five fixed-flow adapters and the provider stubs
  are deleted at the end.

**Tech Stack:**
- Python ≥ 3.12 (`asyncio.timeout`, `Timeout.expired`), POSIX only (`fcntl`, `os.dup2`)
- `mcp` 2.3.0 (`MCPServer`, `ClientSession`, `stdio_client`, `MCPError`)
- Dev group, new: `anthropic` 1.15.0, `openai` 3.32.0, `google-genai` 2.31.0.
  The first two use `httpx2`; `google-genai` uses `httpx`.
- `decimal.Decimal` for money; PyYAML for config files
- pytest with `asyncio_mode = "auto"`; Ruff (line length 100); Pyright (standard)

**Spec:**
- `docs/benchmarks/AGENTIC-BENCHMARK-DESIGN.md` (merged in #161),
  §§2, 5–8, 10–15;
- issue #177 (retry settings and policy);
- the "Carried Into The New Benchmark Harness" list in
  `docs/plans/2026-10-30-audit-fixes-roadmap.md`;
- the owner's rulings comments on #184: part 1 (the ten first-round
  questions), part 2 (the research results), and the round-2 and round-3
  review rulings relayed on the PR;
- `docs/research/2026-10-31-benchmark-provider-limits.md` (the
  "research note" below), the source of every model limit, price and
  package date in this plan.

Executors read the design and the research note in full before Task 1.

## Global Constraints

- **Order:** #173 is on `main` (`64d586d`). PR A builds on it and merges first. PR B starts after PR A is on `main`. It builds on #173's `cell_failure_from`, `guard.redact_api_keys`, `MalformedApiKeyError`, `benchmarks/urls.py` and count model `claude-sonnet-5-5`.
- **No paid call in CI or tests.** Production code builds clients only through `make_client` (Task 7), which calls `require_live_environment` (`BENCHMARK_LIVE_PROVIDERS_ENABLED` plus the provider's key) for every live API. Tests use `httpx2.MockTransport` / `httpx.MockTransport` or the scripted client. No test reaches a network host.
- **Keys:** `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`. The host orchestrator refuses to start when any of them is set in its environment (design §7.3). A worker holds at most one paid key. PR B's local launcher passes none, so no paid call is possible before PR 4's container launcher exists (owner ruling 8).
- **One request per unit.** Every provider client sends exactly one HTTP request per reserved ledger unit: no SDK retries and no SDK continuation (#177 item 1, round-2 ruling B1).
- **Dependencies** (research note §F):
  - Exact pins in the `dev` group: `anthropic==1.15.0` (uploaded 2026-10-15), `openai==3.32.0` (2026-10-16), `google-genai==2.31.0` (2026-10-14). All are at least 14 days old on 2026-10-31.
  - New transitive packages younger than 14 days on the day of locking are locked one release back: `jiter==0.17.0` (2026-09-12), not 0.18.0.
  - **Security exception:** `pyasn1==0.6.5` (2026-10-27) is locked although it is younger than 14 days. It fixes four high-severity DoS advisories against 0.6.4: GHSA-fm9c-5rcf-2q5p, GHSA-9m75-vc3g-4x39, GHSA-2qm8-x737-gmw5 and GHSA-mm57-4v3p-rchx. OSV flags 0.6.4, so the required Dependency audit would fail on it (research note §E; owner ruling part 2, item 4).
  - No LiteLLM, no Inspect AI, nothing else.
- **Owner approval:** `pyproject.toml`, `uv.lock` and dependency changes are forbidden territory for autonomous agents (`AGENT-EXECUTION-PIPELINE.md` §2). Design §13 is the owner's approval for exactly the three SDKs above in the `dev` group, and nothing beyond them. No workflow change: CI's `uv sync --locked --dev` already installs the group.
- **Budget (§12):** caps Anthropic $180, OpenAI $65, Google $180; margins $20, $10, $20; budget $200, $75, $200.
- **Model limits** (research note §§A–D): output cap 32,768 tokens for all three hosted models. Thinking or reasoning counts inside that cap on all three. Max input: Sonnet 5.5 1,000,000; GPT-6.1 Sol 1,017,232 (its 1,050,000 context window minus the output cap); Gemini 3.8 Flash 1,048,576.
- **Price snapshot** (research note §§A–C): it expires 2026-12-31, when Gemini 3.8 Flash's prices double. A live run refuses to start on or after 2026-12-31 UTC. The harness never requests extended prompt-cache retention.
- **Loop caps (§6):** 8 model calls, 12 tool calls, 30 s per tool call, 300 s per trajectory (900 s air-gapped), 100,000 UTF-8 bytes per tool result, k = 3, `tool_choice` auto, OpenAI `strict: false`.
- **Frozen inputs:** the corpus (`c52c3f2`, PR #130) does not change. Nothing under `src/` changes; the tool surface stays as is.
- **Gate** (`AGENTS.md`), run fresh before each PR, and every pytest run with a throwaway cache so nothing touches `~/Library/Caches/mcp-python-docs`:
  - `uv run --locked ruff check src/ tests/ benchmarks/ ops/ .github/scripts/`
  - `uv run --locked pyright src/ benchmarks/`
  - `ISO=$(mktemp -d); uv run --locked env XDG_CACHE_HOME="$ISO" pytest --tb=short -q`
- **TDD** (superpowers:test-driven-development): every new behaviour has a failing test first.
- **Existing tests:** none is weakened. Two kinds of change are allowed, and the PR bodies repeat both lists. Task 1 changes two report assertions to the new matrix ids. Task 11 makes the deletions that design §11.1 orders.
- **Supervisor review:** PR B changes `pyproject.toml`, deletes tests and passes 500 lines. Label it `supervisor-review`.
- **Commits and PRs:** conventional prefixes; every commit ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. PR bodies follow `.github/PULL_REQUEST_TEMPLATE/agent.md` and end with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`. Implementers never self-verify and never merge.

## Review Focus

1. An SDK retry or Gemini continuation must not add a request: one HTTP request per reserved unit. Tests: Task 6 `test_each_client_sends_one_request_per_call`; Task 7 `test_gemini_sends_one_request_when_the_response_asks_to_continue`.
2. A worker key written by `print()`, `sys.stderr` or `os.write(2, ...)` is redacted before it leaves the worker. Tests: Task 9 `test_protect_output_redacts_the_own_key_on_every_stream`, `test_host_logs_and_artifacts_never_hold_a_workers_key`.
3. A trajectory frame over 100,000 bytes round-trips; one over the limit is a harness error. Tests: Task 9 `test_a_trajectory_frame_over_100000_bytes_round_trips_through_a_worker`, `test_a_frame_over_the_limit_is_a_harness_error`.
4. Grant waits, failed attempts and backoff never use the trajectory clock. Tests: Task 8 `test_grant_wait_longer_than_the_trajectory_allowance_still_succeeds`, `test_failed_attempt_longer_than_the_remaining_allowance_is_retried`.
5. A ledger holder killed mid-reserve releases the lock, and its unit is booked at 2× its bound. Tests: Task 3 `test_a_killed_ledger_holder_releases_the_lock_and_its_unit_is_booked_at_twice_its_bound`, `test_a_second_open_on_the_same_directory_is_refused`.
6. What reaches the ledger is the right input bound, output bound and dollar bound for each call, retry and post-no-usage call. Test: Task 8 `test_reservations_carry_independently_computed_bounds`.
7. Tool arguments that are not a JSON object become an `is_error` result and never reach the bridge. Tests: Task 6 `test_openai_non_object_arguments_are_kept_raw`; Task 8 `test_non_object_tool_arguments_are_returned_to_the_model`.
8. A tool result straddling the 100,000-byte cap is cut on a character boundary with the marker. Tests: Task 4 `test_truncate_utf8_cuts_on_a_character_boundary_and_marks_it`, `test_canonical_text_joins_text_blocks_and_marks_other_blocks`.
9. A worker that dies with a unit in flight has that unit booked at 2×, and the provider slot is freed. Test: Task 9 `test_host_settles_a_lost_workers_unit_at_twice_its_bound_and_frees_the_provider`.
10. Gemini calls without ids, including two calls to the same function, pair with their results by position. Test: Task 7 `test_gemini_pairs_two_calls_of_the_same_function_by_position`.

---

# PR A — Methodology amendment and model matrix (design §12 step 2)

Branch `docs/agentic-methodology-matrix` from `origin/main` (which has #173). Refs #63, #177.

### Task 1: Model matrix per design §7.1

**Files:**
- Modify: `benchmarks/model_matrix.py` (`ModelFamily`, `_load_model_families`, new `model_family_from_raw`)
- Modify: `docs/benchmarks/model-matrix.yml` (rewrite)
- Test: `tests/benchmarks/test_model_matrix.py` (add tests only; reuse its `_family` and file-writing helpers)
- Modify: `tests/benchmarks/test_report.py:148-149` (the two family-id assertions; see Step 5)

**Interfaces:**
- Produces (Tasks 3, 6–10 rely on these):

```python
KNOWN_APIS = frozenset({"anthropic-messages", "openai-responses",
                        "gemini-generate-content", "openai-chat-completions", "scripted"})
KNOWN_ARMS = frozenset({"hosted", "air-gapped"})
MIN_FRAMING_ALLOWANCE_TOKENS = 256

@dataclass(frozen=True)
class ModelFamily:
    ...                                   # existing fields unchanged, `raw` stays last required
    api: str | None = None
    arm: str | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    output_cap_includes_reasoning: bool | None = None
    framing_allowance_tokens: int | None = None

def model_family_from_raw(item: Mapping[str, Any], *, label: str = "model family") -> ModelFamily
    # validates and builds one entry; _load_model_families calls it, then checks duplicate ids.
    # The PR B worker rebuilds its family from the job's raw dict with this function.
```

- [ ] **Step 1: Write the failing tests**

```python
def test_real_model_matrix_matches_design_section_7_1() -> None:
    matrix = load_model_matrix(MODEL_MATRIX_PATH)
    rows = {(f.id, f.provider, f.model_id, f.api, f.arm) for f in matrix.model_families}
    assert rows == {
        ("claude-sonnet-5-5", "anthropic", "claude-sonnet-5-5", "anthropic-messages", "hosted"),
        ("gpt-6.1-sol", "openai", "gpt-6.1-sol", "openai-responses", "hosted"),
        ("gemini-3.8-flash", "google", "gemini-3.8-flash", "gemini-generate-content", "hosted"),
        ("gemma4-12b", "ollama", "gemma4:12b", "openai-chat-completions", "air-gapped"),
    }
    hosted = {f.id: f for f in matrix.model_families if f.arm == "hosted"}
    assert {i: f.max_input_tokens for i, f in hosted.items()} == {
        "claude-sonnet-5-5": 1_000_000, "gpt-6.1-sol": 1_017_232, "gemini-3.8-flash": 1_048_576}
    for family in hosted.values():
        assert "client_wrapper" not in family.raw
        assert family.max_output_tokens == 32_768
        assert family.output_cap_includes_reasoning is True
        assert family.framing_allowance_tokens == 256
        assert family.raw["limits_source"].startswith("docs/research/2026-10-31-benchmark-provider-limits.md#")

def test_every_limits_source_anchor_is_a_research_note_heading() -> None:
    note = (REPO_ROOT / "docs/research/2026-10-31-benchmark-provider-limits.md").read_text()
    # GitHub slug: lowercase, drop every character except letters, digits, spaces, "-" and "_",
    # then spaces -> "-"
    slugs = {re.sub(r"[^\w\- ]", "", line.lstrip("#").strip().lower()).replace(" ", "-")
             for line in note.splitlines() if line.startswith("#")}
    for family in load_model_matrix(MODEL_MATRIX_PATH).model_families:
        if family.arm == "hosted":
            assert family.raw["limits_source"].split("#", 1)[1] in slugs

def test_hosted_family_must_set_its_budget_fields(tmp_path) -> None:
    # a hosted entry without max_input_tokens -> BenchmarkValidationError naming "max_input_tokens"

@pytest.mark.parametrize("field,value", [
    ("api", "bedrock"), ("arm", "cloud"), ("max_output_tokens", 0),
    ("max_input_tokens", -1), ("framing_allowance_tokens", 255),
    ("output_cap_includes_reasoning", "yes")])
def test_load_model_matrix_rejects_a_bad_agentic_field(tmp_path, field, value) -> None:
    # pytest.raises(BenchmarkValidationError, match=field)

def test_model_family_id_must_be_path_safe(tmp_path) -> None:
    # id "gemma4:12b" -> BenchmarkValidationError (ids name run directories in Task 10)

def test_entries_without_agentic_fields_still_load(tmp_path) -> None:
    # the existing minimal fixture shape loads; api/arm/... are None

def test_model_family_from_raw_round_trips_every_committed_entry() -> None:
    for family in load_model_matrix(MODEL_MATRIX_PATH).model_families:
        assert model_family_from_raw(family.raw) == family
```

- [ ] **Step 2: Run them and watch them fail**

Run: `ISO=$(mktemp -d); uv run --locked env XDG_CACHE_HOME="$ISO" pytest tests/benchmarks/test_model_matrix.py -q`
Expected: the new tests FAIL (unknown attributes, old matrix rows).

- [ ] **Step 3: Add `model_family_from_raw` and the field checks**

Move the per-entry body of `_load_model_families` into
`model_family_from_raw`, with the same messages. Validate each new field
only when present:
- `api` in `KNOWN_APIS` and `arm` in `KNOWN_ARMS`;
- token fields positive `int` (not `bool`);
- `output_cap_includes_reasoning` a `bool`;
- `framing_allowance_tokens ≥ 256`.

An entry with `arm: hosted` must set `max_input_tokens`,
`max_output_tokens`, `output_cap_includes_reasoning` and
`framing_allowance_tokens`. The family `id` must match
`benchmarks.runner._SAFE_ID`. Leave `REQUIRED_PROVIDERS` unchanged.

- [ ] **Step 4: Rewrite `docs/benchmarks/model-matrix.yml`**

Keep `schema_version: 1` and `methodology_token_label`. Rewrite the header
comment to cite design §7.1 and the research note, and drop the issue #73
stub and mock-adapter text. Four entries, ids as in the test.

Each entry has:
- `client`: `anthropic` (Anthropic), `openai` (OpenAI and Ollama), or
  `google-genai` (Google);
- `token_count_method: claude_normalized_payload`;
- `provider_native_token_field`: `usage` (Anthropic, OpenAI, Ollama) or
  `usage_metadata` (Google);
- `latency_scope: request_dispatch_to_final_answer`;
- `headline_eligible: false`, with the existing decision-5.17 reason;
- `settings`, a string holding the §7.1 Settings text verbatim plus the
  documented default:
  - Sonnet 5.5: adaptive thinking on, cannot be disabled, default effort
    `high`;
  - GPT-6.1 Sol: default effort `medium`;
  - Gemini 3.8 Flash: default thinking level `medium`.

Hosted entries also set these values (research note §§A–D):

| id | `max_input_tokens` | why | `limits_source` |
|---|---|---|---|
| `claude-sonnet-5-5` | 1000000 | documented "1M tokens"; the exact integer needs the Models API, which PR 4's smoke run reads | `docs/research/2026-10-31-benchmark-provider-limits.md#a-claude-sonnet-5-5-anthropic-messages-api` |
| `gpt-6.1-sol` | 1017232 | no separate input limit is published: the 1,050,000 context window minus the 32,768 output cap | `...#b-gpt-61-sol-openai-responses-api` |
| `gemini-3.8-flash` | 1048576 | documented input token limit | `...#c-gemini-38-flash-gemini-api-google-genai-paid-tier` |

All three also set:
- `max_output_tokens: 32768`, below the documented maximums of 128,000,
  128,000 and 65,536;
- `output_cap_includes_reasoning: true`, cited to the research note
  sections quoted above;
- `framing_allowance_tokens: 256`.

The `gemma4-12b` entry has `arm: air-gapped`, no token limits, and its
settings name `OLLAMA_CONTEXT_LENGTH=32768`.

- [ ] **Step 5: Update the report test and run the whole benchmark suite**

`tests/benchmarks/test_report.py:148-149` asserts the old ids
`openai-gpt-4o-mini` and `google-gemini-1.5-flash`, which the real matrix
no longer has. Change them to `claude-sonnet-5-5` and `gemini-3.8-flash`.
This is a contract change, not a weakening: the test still checks that the
report renders the real matrix's families. List it in the PR A body.

Run: `ISO=$(mktemp -d); uv run --locked env XDG_CACHE_HOME="$ISO" pytest tests/benchmarks -q`
Expected: PASS after the two-line update. Before it,
`test_generate_report_includes_all_required_sections` fails on
`openai-gpt-4o-mini`; `test_real_model_matrix_file_loads_and_covers_required_providers`
passes either way.

- [ ] **Step 6: Commit**

```bash
git add benchmarks/model_matrix.py docs/benchmarks/model-matrix.yml tests/benchmarks/test_model_matrix.py tests/benchmarks/test_report.py
git commit -m "feat(benchmarks): rewrite the model matrix for the agentic design"
```

### Task 2: Methodology amendment, smoke-run cost row, PR A

**Files:**
- Modify: `docs/benchmarks/PUBLIC-BENCHMARK-METHODOLOGY.md` (append one section)
- Modify: `docs/benchmarks/AGENTIC-BENCHMARK-DESIGN.md` (§12 cost table only)

**Interfaces:** none (docs).

- [ ] **Step 1: Append `## Amendment <YYYY-MM-DD>: Agentic Mode`**

The date is the day PR A merges (owner ruling 10). Amendments stay in
chronological order, so the section goes after #173's
`## Amendment 2026-10-30: How Fixed-Flow Results Are Computed`. Its first
line cites design #161, approved 2026-10-29. In the file's style (short
sentences, one idea each), it states:
- **Scope.** It covers `python -m benchmarks agentic-run` runs. The
  fixed-flow `run` is no longer a public result (design §3).
- **Parity.** Every system gets the same system prompt, the corpus prompt
  verbatim, the same loop, the same caps and `tool_choice` auto. Only the
  tool set differs. This replaces the "only allowed difference is the
  documentation context" rule for agentic runs.
- **Systems** (round 1, §5):
  - `python-docs`;
  - `docs-fetch` (`mcp-server-fetch`, limited to docs.python.org by the
    egress proxy);
  - `no-docs`.

  Every MCP system exposes its full tool list, unfiltered. Context7,
  GitMCP, DeepWiki and Ref.tools come in round 2, behind their terms
  gates.
- **Correctness** (§9):
  - The grade covers content only and comes from a three-judge LLM panel.
  - Citations are measured separately, as grounding. This replaces the
    0.5 level's missing-citation clause.
  - No human grades or calibrates anything. Validity rests on gates
    G1–G4 and the outcome tiers.
  - The required wording is "LLM-graded, validated against synthetic
    probes and inter-model agreement".
- **Tokens** (§8): normalized tokens per trajectory, counted after the run
  with `claude-sonnet-5-5`, per correct answer, with a paired-bootstrap CI.
  Provider usage serves only as cost. Neither is compared across models.
- **Latency** (§10): a model, tool and overhead split, with cold and warm
  calls.
- **Output cap:** 32,768 tokens for every hosted model, with thinking and
  reasoning inside it. If more than 1% of pilot answers stop at the cap,
  the cap is raised before the full run (owner rulings 3 and part 2,
  item 3). A Gemini `CONTINUATION` stop is not followed, and it counts as
  an output-cap stop.
- **Claims** (§2): the C1–C3 pass rules and every anti-claim. `no-docs` is
  never ranked on tokens. Round 1 supports only "fewer tokens than
  fetching the official docs pages".
- **Denominators:**
  - A cap hit is a failure scored 0.0.
  - A trajectory cut off by a budget stop, or by exhausted retries after
    429, 5xx, a timeout or a dropped connection, is discarded, not scored,
    and listed in the report (§12, #177).
- **Reproducibility:** each run records:
  - SDK versions and the model version each provider reports;
  - the price snapshot and its expiry;
  - the system prompt and tool-schema hashes;
  - the index sha256 and the order seed.

- [ ] **Step 2: Add the smoke run to the §12 table (#177 item 3)**

Insert as the first data row:
`| Smoke run: every (provider, system) pair, before the pilot (section 14) | — | ~$1 |`

- [ ] **Step 3: Verify**

Run: `grep -c "^## Amendment .*: Agentic Mode" docs/benchmarks/PUBLIC-BENCHMARK-METHODOLOGY.md; grep -c "Smoke run" docs/benchmarks/AGENTIC-BENCHMARK-DESIGN.md`
Expected: `1` and `1`.

- [ ] **Step 4: Commit**

```bash
git add docs/benchmarks/PUBLIC-BENCHMARK-METHODOLOGY.md docs/benchmarks/AGENTIC-BENCHMARK-DESIGN.md
git commit -m "docs(benchmarks): amend the methodology for agentic mode"
```

- [ ] **Step 5: Gate, push, open PR A**

Run the three gate commands; expect `All checks passed!`, `0 errors`, all
tests passing. Push and open PR A with `gh pr create` against `main`:
- **Title:** `docs: agentic methodology amendment and model matrix`.
- **Body:** Refs #63 and #177 (item 3), the Task 1 report-assertion
  change, and the gate output.

---

# PR B — Agentic harness with mocked end-to-end CI (design §12 step 3)

Branch `feat/agentic-harness` from `origin/main` after PR A merges.
Closes #177 (items 1 and 2; item 3 landed in PR A). Refs #63.

Package layout (design §15 leaves it to the builder):

| File | Responsibility |
|---|---|
| `benchmarks/agentic/types.py` | Neutral value types shared by every module |
| `benchmarks/agentic/usage.py` | §8 usage buckets per hosted provider |
| `benchmarks/agentic/ledger.py` | Price snapshot, bound pricing, persistent ledger, one-in-flight service |
| `benchmarks/agentic/bridge.py` | Systems manifest, MCP stdio bridge, canonical results, truncation |
| `benchmarks/agentic/schemas.py` | MCP tool → provider tool conversion |
| `benchmarks/agentic/clients.py` | SDK construction, provider calls, error classification, scripted client, factory |
| `benchmarks/agentic/prompt.py` | The frozen system prompt |
| `benchmarks/agentic/loop.py` | One trajectory: caps, clock, retries, bounds, records |
| `benchmarks/agentic/protocol.py` | Length-prefixed frames, key scans |
| `benchmarks/agentic/worker.py` | Worker process: output protection, one (model, system) job |
| `benchmarks/agentic/host.py` | Launchers, worker supervision, ledger arbitration |
| `benchmarks/agentic/run.py` | Plan, artifacts, run summary, `run_agentic` |

### Task 3: Usage buckets, price snapshot and the spend ledger

**Files:**
- Create: `benchmarks/agentic/__init__.py` (docstring only), `benchmarks/agentic/types.py`, `benchmarks/agentic/usage.py`, `benchmarks/agentic/ledger.py`
- Create: `docs/benchmarks/price-snapshot.yml`
- Test: `tests/benchmarks/test_agentic_ledger.py`

**Interfaces:**
- Consumes: `ModelFamily`, `load_model_matrix` (Task 1); `BenchmarkValidationError`.
- Produces:

```python
# types.py
@dataclass(frozen=True)
class Buckets:
    uncached_input: int
    cache_write_input: int
    cached_input: int
    output: int          # includes thinking/reasoning
    reasoning: int = 0   # information only, already inside `output`
    @property
    def total_input(self) -> int: ...

# usage.py — each takes the SDK usage object's model_dump() (snake_case keys)
def anthropic_buckets(usage: Mapping[str, Any]) -> Buckets
def openai_buckets(usage: Mapping[str, Any]) -> Buckets        # Responses API usage
def gemini_buckets(usage: Mapping[str, Any]) -> Buckets

# ledger.py
PROVIDER_CAPS: dict[str, Decimal]      # anthropic 180, openai 65, google 180
PROVIDER_MARGINS: dict[str, Decimal]   # anthropic 20, openai 10, google 20
Outcome = Literal["ok", "rejected", "rate_limited", "server", "timeout", "connection",
                  "no_usage", "worker_lost", "unsettled_at_load"]

@dataclass(frozen=True)
class Rates:          # USD per million tokens
    input: Decimal; cache_write: Decimal; cached_input: Decimal; output: Decimal
@dataclass(frozen=True)
class Tier:
    up_to_input_tokens: int | None   # None = no upper limit (last tier)
    rates: Rates
@dataclass(frozen=True)
class ModelPrice:
    model_id: str; provider: str; tiers: tuple[Tier, ...]; source_url: str
@dataclass(frozen=True)
class PriceSnapshot:
    captured_on: date
    expires_on: date | None
    models: dict[str, ModelPrice]      # keyed by model_id

def load_price_snapshot(path: Path) -> PriceSnapshot
def refuse_stale_snapshot(snapshot: PriceSnapshot, *, today: date) -> None
    # raises BenchmarkValidationError when expires_on is set and today >= expires_on
def actual_cost(price: ModelPrice, buckets: Buckets) -> Decimal
def bound_cost(price: ModelPrice, *, input_bound: int, output_bound: int) -> Decimal
def clamp_input_bound(bound: int, max_input_tokens: int) -> int
def booked_cost(outcome: Outcome, *, bound: Decimal, actual: Decimal | None) -> Decimal

class Ledger:                                   # sync; one JSONL file per provider
    @classmethod
    def open(cls, ledger_dir: Path, *, run_id: str,
             caps: Mapping[str, Decimal] = PROVIDER_CAPS) -> "Ledger"
    def close(self) -> None
    def spent(self, provider: str) -> Decimal
    def stopped(self, provider: str) -> str | None          # "cap" | "overrun" | None
    def reserve(self, provider: str, *, model_id: str, kind: str, bound: Decimal,
                input_bound: int, output_bound: int) -> str | None   # unit id; None = refused
    def settle(self, unit_id: str, *, outcome: Outcome, actual: Decimal | None,
               buckets: Buckets | None, input_bound_exceeded: bool = False) -> Decimal
    def acknowledge_overrun(self, provider: str) -> None
    def events(self, run_id: str) -> list[dict[str, Any]]

class LedgerService:                            # async; the host's arbiter
    def __init__(self, ledger: Ledger, prices: Mapping[str, ModelPrice],
                 families: Mapping[str, ModelFamily]) -> None    # both keyed by model_id
    async def reserve(self, *, provider: str, model_id: str, input_bound: int,
                      output_bound: int, kind: str = "model_call") -> str | None
    async def settle(self, unit_id: str, *, outcome: Outcome, buckets: Buckets | None) -> None
```

Rules the tests pin (design §8, §12):
- **Buckets.**
  - Anthropic: `input_tokens`, `cache_creation_input_tokens`,
    `cache_read_input_tokens`, `output_tokens`.
  - OpenAI: uncached is `input_tokens − cached_tokens − cache_write_tokens`.
  - Gemini: uncached is `prompt − cached_content + tool_use_prompt`;
    output is `candidates + thoughts`.
  - A `None` count is 0. A negative uncached count raises
    `BenchmarkValidationError`.
- **Actual cost.** Pick the tier from `buckets.total_input`: the first tier
  whose `up_to_input_tokens` is at least that, or the last tier. Each
  bucket is priced at that tier's own rate, and output at the tier's
  output rate.
- **Bound cost.** Input is priced at `cache_write` for `openai` and
  `input` for every other provider. The tier comes from `input_bound`, for
  input and output both: GPT-6.1 Sol is priced at its above-272K tier
  whenever its input bound exceeds 272,000.
- **Booked cost.** `ok` books the actual cost. `rejected` and
  `rate_limited` book 0. Every other outcome books 2 × bound. The loop
  never cuts a call in flight (Task 8), so no outcome means "cut".
- **Reserve.**
  - Refused, and the provider stopped with `"cap"`, when `spent + bound`
    is over the cap. A stop is sticky.
  - Raises `RuntimeError` when a unit is already in flight for that
    provider.
  - Raises `BenchmarkValidationError` for a provider without a cap.
  - The Ollama provider is unmetered and arrives in PR 4.
- **Settle.** An `ok` whose actual cost is above its bound books the
  actual cost and stops the provider with `"overrun"`. Only
  `acknowledge_overrun` lifts that stop.
- **Files.** Each provider has `<provider>.jsonl` with `reserve`,
  `settle`, `stop` and `resume` events. Money is written as decimal
  strings. A `settle` event carries `input_bound_exceeded`.
- **Open.**
  - `open` books every reserve that has no settle with
    `outcome="unsettled_at_load"`, at 2 × bound, and appends that event.
  - `open` takes the lock first. It opens `<ledger_dir>/.lock`, keeps the
    descriptor for the ledger's lifetime, and calls
    `fcntl.flock(fd, LOCK_EX | LOCK_NB)`. The OS releases the lock when
    the process dies. If the lock is held, `open` raises
    `BenchmarkValidationError` naming the lock file. `close()` unlocks
    and closes.
  - Mark the lock with
    `# shortcut: fcntl is POSIX only; the harness runs on macOS and Linux, add msvcrt.locking if Windows runs are ever needed`.
- **LedgerService.**
  - Holds one `asyncio.Lock` per provider, from `reserve` until that
    unit's `settle`.
  - Clamps `input_bound` to the family's `max_input_tokens`, prices the
    bound, and passes `input_bound_exceeded = buckets.total_input >
    input_bound` to `settle`.
- **Snapshot.** `load_price_snapshot` refuses:
  - tiers that are not ascending;
  - a last tier with a limit;
  - a negative rate;
  - a provider other than `anthropic`, `openai` and `google`;
  - a duplicate `model_id`.

- [ ] **Step 1: Write the failing tests**

```python
def test_anthropic_buckets() -> None:
    assert anthropic_buckets({"input_tokens": 100, "cache_creation_input_tokens": None,
        "cache_read_input_tokens": 20, "output_tokens": 30}) == Buckets(100, 0, 20, 30, 0)

def test_openai_buckets_subtract_cached_and_cache_write_from_input() -> None:
    assert openai_buckets({"input_tokens": 1000, "input_tokens_details": {"cached_tokens": 200,
        "cache_write_tokens": 300}, "output_tokens": 50,
        "output_tokens_details": {"reasoning_tokens": 40}}) == Buckets(500, 300, 200, 50, 40)

def test_gemini_buckets_add_tool_use_prompt_and_thoughts() -> None:
    assert gemini_buckets({"prompt_token_count": 1000, "cached_content_token_count": 100,
        "tool_use_prompt_token_count": 50, "candidates_token_count": 30,
        "thoughts_token_count": 20}) == Buckets(950, 0, 100, 50, 20)

def test_buckets_refuse_a_negative_uncached_input() -> None   # openai cached 200 > input 100

def test_committed_snapshot_has_the_researched_prices() -> None:
    snap = load_price_snapshot(PRICE_SNAPSHOT_PATH)    # values: research note §§A-C
    def tiers(m): return [(t.up_to_input_tokens, t.rates) for t in snap.models[m].tiers]
    D = Decimal
    assert tiers("claude-sonnet-5-5") == [(None, Rates(D("2"), D("4"), D("0.10"), D("10")))]
    assert tiers("gpt-6.1-sol") == [(272_000, Rates(D("2"), D("2.50"), D("0.10"), D("10"))),
                                    (None, Rates(D("4"), D("5"), D("0.20"), D("15")))]
    assert tiers("gemini-3.8-flash") == [(None, Rates(D("0.75"), D("0"), D("0.075"), D("3.75")))]
    assert snap.expires_on == date(2026, 12, 31)
    assert all(p.source_url.startswith("https://") for p in snap.models.values())

def test_stale_snapshot_is_refused_from_its_expiry_day() -> None:
    snap = load_price_snapshot(PRICE_SNAPSHOT_PATH)
    refuse_stale_snapshot(snap, today=date(2026, 12, 30))           # no error
    with pytest.raises(BenchmarkValidationError, match="2026-12-31"):
        refuse_stale_snapshot(snap, today=date(2026, 12, 31))

def test_actual_cost_prices_each_bucket_at_its_own_rate() -> None:
    price = _flat("2", "2.5", "0.2", "10")            # input, cache_write, cached, output
    assert actual_cost(price, Buckets(1_000_000, 1_000_000, 1_000_000, 1_000_000, 500_000)) == Decimal("14.7")

def test_tier_is_chosen_by_total_input_and_applies_to_output_too() -> None:
    price = _tiered(up_to=200_000, low=("2", "10"), high=("4", "15"))
    assert actual_cost(price, Buckets(200_000, 0, 0, 1_000_000)) == Decimal("10.4")
    assert actual_cost(price, Buckets(200_001, 0, 0, 1_000_000)) == Decimal("15.800004")

def test_bound_cost_uses_openai_cache_write_rate_and_uncached_rate_elsewhere() -> None:
    assert bound_cost(_flat("2", "2.5", "0.2", "10", provider="openai"), input_bound=1_000_000, output_bound=0) == Decimal("2.5")
    assert bound_cost(_flat("2", "2.5", "0.2", "10", provider="anthropic"), input_bound=1_000_000, output_bound=0) == Decimal("2")

def test_input_bound_is_clamped_to_the_model_maximum() -> None:
    assert clamp_input_bound(2_000_000, 1_048_576) == 1_048_576

@pytest.mark.parametrize("outcome,expected", [("ok", "1.2"), ("rejected", "0"), ("rate_limited", "0"),
    ("server", "4"), ("timeout", "4"), ("connection", "4"), ("no_usage", "4"),
    ("worker_lost", "4"), ("unsettled_at_load", "4")])
def test_booked_cost_by_outcome(outcome, expected) -> None:
    actual = Decimal("1.2") if outcome == "ok" else None
    assert booked_cost(outcome, bound=Decimal("2"), actual=actual) == Decimal(expected)

def test_reserve_refuses_when_spend_plus_bound_exceeds_cap_and_stops_provider(tmp_path) -> None:
    # caps {"anthropic": 10}: reserve 6 -> settle ok 5; reserve 5 -> granted (10 <= 10), settle ok 5;
    # reserve 0.01 -> None; stopped("anthropic") == "cap"; reserve 0 -> None (sticky)

def test_reserve_raises_when_a_unit_is_already_in_flight(tmp_path) -> None
def test_actual_above_bound_stops_the_provider_until_acknowledged(tmp_path) -> None:
    # settle ok actual 3 > bound 2 -> booked 3, stopped "overrun"; reopen -> still "overrun";
    # acknowledge_overrun -> stopped None, a "resume" event is written
def test_reopening_books_an_unsettled_unit_at_twice_its_bound(tmp_path) -> None:
    # reserve bound 3, close without settle; reopen -> spent("anthropic") == Decimal("6")
def test_a_second_open_on_the_same_directory_is_refused(tmp_path) -> None   # match=".lock"
def test_a_killed_ledger_holder_releases_the_lock_and_its_unit_is_booked_at_twice_its_bound(tmp_path) -> None:
    # subprocess: `python -c` opens the ledger, reserves bound 3, print("reserved", flush=True), sleeps;
    # parent: after "reserved", Ledger.open(tmp_path) raises (lock held);
    # proc.kill() (SIGKILL), proc.wait(); Ledger.open succeeds and spent("anthropic") == Decimal("6")
def test_provider_without_a_cap_is_refused(tmp_path) -> None                  # "ollama", "mistral"

async def test_service_allows_one_unit_in_flight_per_provider_and_runs_providers_in_parallel(tmp_path) -> None:
    # second anthropic reserve is still pending until the first settles; an openai reserve
    # completes meanwhile (asyncio.wait_for with a short timeout on each)

async def test_service_prices_a_bound_above_the_tier_threshold_at_the_high_tier(tmp_path) -> None:
    # openai family max_input 1_017_232; tiers up_to 272_000 (2, 2.5, 0.1, 10) / above (4, 5, 0.2, 15)
    unit = await service.reserve(provider="openai", model_id="m", input_bound=300_000, output_bound=1_000)
    assert _event(ledger, "reserve", unit)["bound"] == "1.515"          # 300_000*5 + 1_000*15, per 1M
    await service.settle(unit, outcome="ok", buckets=Buckets(310_000, 0, 0, 10))
    assert _event(ledger, "settle", unit)["input_bound_exceeded"] is True
    assert ledger.spent("openai") == Decimal("1.24015")                 # actual, at the high tier

def test_committed_matrix_largest_single_call_bound_is_below_every_margin() -> None:
    matrix, snap = load_model_matrix(MODEL_MATRIX_PATH), load_price_snapshot(PRICE_SNAPSHOT_PATH)
    for f in (f for f in matrix.model_families if f.arm == "hosted"):
        cost = bound_cost(snap.models[f.model_id], input_bound=f.max_input_tokens, output_bound=f.max_output_tokens)
        assert cost < PROVIDER_MARGINS[f.provider]       # about $2.33, $5.58 and $0.91

def test_caps_and_margins_add_up_to_the_budget() -> None:
    assert {p: PROVIDER_CAPS[p] + PROVIDER_MARGINS[p] for p in PROVIDER_CAPS} == \
        {"anthropic": Decimal(200), "openai": Decimal(75), "google": Decimal(200)}
```

`_flat`, `_tiered` and `_event` are test-local helpers.

- [ ] **Step 2: Run them and watch them fail**

Run: `ISO=$(mktemp -d); uv run --locked env XDG_CACHE_HOME="$ISO" pytest tests/benchmarks/test_agentic_ledger.py -q`
Expected: FAIL with `ModuleNotFoundError: benchmarks.agentic`.

- [ ] **Step 3: Write `docs/benchmarks/price-snapshot.yml`**

Top-level keys:
- `schema_version: 1`, `currency: USD`, `unit: per_million_tokens`;
- `captured_on: "2026-10-31"` and `expires_on: "2026-12-31"`;
- `source`: the research note path;
- `models`.

A comment above `expires_on` explains it: Gemini 3.8 Flash's prices
double on 2027-01-01, and a live run refuses the snapshot from 2026-12-31
UTC, a one-day margin across time zones.

Each model entry has `model_id`, `provider`, `source_url` (the vendor
page the research note quotes) and `tiers`. Each tier has
`up_to_input_tokens` and rates `input`, `cache_write`, `cached_input` and
`output`, written as strings, with exactly the values the test pins. A
one-line comment beside each `cache_write` says why:
- **Sonnet:** $4, the higher of the 5-minute and 1-hour write rates. The
  harness sends no `cache_control`, so a write is unexpected and priced
  high.
- **GPT-6.1 Sol:** $2.50 and $5.00, the 30-minute rates. The harness never
  requests 12-hour retention (Task 6 test).
- **Gemini:** 0. Gemini has no cache-write bucket, and the harness creates
  no cached content.

- [ ] **Step 4: Implement `types.py`, `usage.py`, `ledger.py` to the rules above**

- [ ] **Step 5: Run the tests**

Run: `ISO=$(mktemp -d); uv run --locked env XDG_CACHE_HOME="$ISO" pytest tests/benchmarks/test_agentic_ledger.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add benchmarks/agentic/ docs/benchmarks/price-snapshot.yml tests/benchmarks/test_agentic_ledger.py
git commit -m "feat(benchmarks): add the agentic spend ledger and price snapshot"
```

### Task 4: Systems manifest and MCP stdio bridge

**Files:**
- Create: `benchmarks/agentic/bridge.py`, `docs/benchmarks/agentic-manifest.yml`
- Create: `tests/benchmarks/fixtures/agentic/fake_fetch_server.py`
- Modify: `benchmarks/agentic/types.py` (add `ToolSpec`)
- Test: `tests/benchmarks/test_agentic_bridge.py`

**Interfaces:**
- Consumes: `benchmarks.runner._load_yaml_mapping`, `_load_competitors`, `_validate_manifest_eligibility`; `mcp.ClientSession`, `StdioServerParameters`, `stdio_client`, `MCPError`.
- Produces:

```python
# types.py
@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]

# bridge.py
RESULT_CAP_BYTES = 100_000
TRUNCATION_MARKER = "\n[benchmark harness: tool result truncated at 100000 UTF-8 bytes]"

@dataclass(frozen=True)
class SystemSpec:
    id: str
    transport: Literal["stdio", "none"]      # manifest `adapter`: "mcp-stdio" | "none"
    command: tuple[str, ...]                 # "{python}" is replaced by sys.executable at spawn
    env: dict[str, str]
    pass_env: tuple[str, ...]
    raw: dict[str, Any]

@dataclass(frozen=True)
class ToolOutcome:
    text: str            # full canonical text, before truncation
    sha256: str          # sha256 of `text` as UTF-8
    is_error: bool
    wall_ms: float

class BridgeCrash(Exception): ...            # transport or session failure

class ToolBridge(Protocol):
    tools: list[ToolSpec]
    async def call(self, name: str, arguments: dict[str, Any]) -> ToolOutcome: ...

class Bridge:                                # implements ToolBridge
    server_info: dict[str, Any]              # initialize result serverInfo, by_alias dump
    tools_raw: list[dict[str, Any]]          # every tools/list page, by_alias dump, unfiltered
    tools: list[ToolSpec]

def system_spec_from_raw(raw: Mapping[str, Any]) -> SystemSpec
    # one manifest entry -> SystemSpec; load_systems uses it, and the worker rebuilds its system with it
def load_systems(manifest_path: Path) -> list[SystemSpec]
@asynccontextmanager
async def open_bridge(system: SystemSpec) -> AsyncIterator[Bridge]
def canonical_text(result: CallToolResult) -> str
def truncate_utf8(text: str, limit: int = RESULT_CAP_BYTES) -> tuple[str, bool]
```

Rules:
- **`canonical_text`.** When `structured_content` is not `None`, return
  `json.dumps(structured_content, ensure_ascii=False, separators=(",", ":"))`.
  Otherwise join, with `"\n"`, the text of every `TextContent` and
  `[<type> content omitted]` for any other block.
- **`truncate_utf8`.** Text at or under the limit is returned unchanged.
  Longer text is cut to the longest prefix whose UTF-8 encoding fits the
  limit, plus `TRUNCATION_MARKER`.
- **Server environment.** The server gets `system.env` plus each
  `pass_env` variable present in the worker's environment, and nothing
  else (the SDK adds its default set). The server's stderr is the worker's
  `sys.stderr`, which Task 9 redacts.
- **`tools/list`.** Follow `next_cursor` until it is empty.
- **`call`.**
  - An `MCPError` becomes `ToolOutcome(is_error=True)` with the error
    text.
  - A result with `is_error` keeps it.
  - A dead transport raises `BridgeCrash`, mapped from #173's
    `cell_failure_from` leaves.

- [ ] **Step 1: Write the fake server**

The fixture is an MCP stdio server built with
`from mcp.server import MCPServer` and `MCPServer("fake-fetch")`. Its tool
is `fetch(url: str, max_length: int = 5000, start_index: int = 0, raw: bool = False) -> str`,
which returns `f"Contents of {url}: the json module encodes and decodes JSON."`.
With `--edge` it also registers:
- `env_names() -> list[str]`, the sorted names in `os.environ`;
- `die() -> str`, which calls `os._exit(1)`;
- `pid() -> int`, which returns `os.getpid()` (Task 9's shutdown test).

- [ ] **Step 2: Write the failing tests**

```python
def test_canonical_text_prefers_compact_structured_content() -> None:
    r = CallToolResult(content=[TextContent(type="text", text="ignored")],
                       structured_content={"a": "é", "b": [1, 2]}, is_error=False)
    assert canonical_text(r) == '{"a":"é","b":[1,2]}'

def test_canonical_text_joins_text_blocks_and_marks_other_blocks() -> None:
    # content: text "one", an ImageContent, text "two"; structured_content None
    assert canonical_text(r) == "one\n[image content omitted]\ntwo"

def test_truncate_utf8_cuts_on_a_character_boundary_and_marks_it() -> None:
    text = "a" * 99_999 + "é" + "tail"          # "é" is 2 bytes and straddles byte 100,000
    assert truncate_utf8(text, 100_000) == ("a" * 99_999 + TRUNCATION_MARKER, True)

def test_truncate_utf8_leaves_a_result_at_the_limit_untouched() -> None:
    assert truncate_utf8("a" * 100_000, 100_000) == ("a" * 100_000, False)

def test_load_systems_reads_the_committed_manifest() -> None:
    systems = {s.id: s for s in load_systems(AGENTIC_MANIFEST_PATH)}
    assert set(systems) == {"python-docs", "docs-fetch", "no-docs"}
    assert systems["python-docs"].command == ("{python}", "-m", "mcp_server_python_docs", "serve")
    assert systems["python-docs"].env == {"PYTHON_DOCS_MCP_DISABLE_AUTO_INDEX": "1"}
    assert systems["docs-fetch"].command == ("mcp-server-fetch",)
    assert systems["no-docs"].transport == "none"
    assert all(system_spec_from_raw(s.raw) == s for s in systems.values())

async def test_bridge_lists_the_real_server_tools_unfiltered(tmp_path, monkeypatch) -> None:
    # tests.test_stdio_smoke._isolated_cache_env + tests.test_mcp_get_docs_cache_smoke
    # ._create_contentful_json_index; monkeypatch XDG_CACHE_HOME/HOME to the isolated values
    async with open_bridge(python_docs) as bridge:
        assert {t.name for t in bridge.tools} == {"search_docs", "get_docs", "lookup_package_docs",
            "list_versions", "detect_python_version", "compare_versions", "whatsnew_for_version"}
        assert len(bridge.tools_raw) == 7 and "name" in bridge.server_info
        outcome = await bridge.call("get_docs", {"slug": "library/json.html", "version": "3.13"})
        assert outcome.is_error is False and "json module" in outcome.text
        assert outcome.sha256 == hashlib.sha256(outcome.text.encode()).hexdigest()

async def test_bridge_returns_an_unknown_tool_as_an_error_result(...) -> None:
    outcome = await bridge.call("no_such_tool", {})
    assert outcome.is_error is True and "no_such_tool" in outcome.text

async def test_bridge_returns_invalid_arguments_as_an_error_result(...) -> None:
    assert (await bridge.call("search_docs", {"query": 5})).is_error is True

async def test_bridge_raises_bridge_crash_when_the_server_dies(...) -> None   # fake --edge, die()

async def test_server_env_holds_only_the_listed_variables(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    # fake --edge with env {"MARKER": "1"}, pass_env ("HOME",): env_names() includes MARKER and
    # HOME, and excludes ANTHROPIC_API_KEY
```

- [ ] **Step 3: Run them and watch them fail**

Run: `ISO=$(mktemp -d); uv run --locked env XDG_CACHE_HOME="$ISO" pytest tests/benchmarks/test_agentic_bridge.py -q`
Expected: FAIL with `ImportError`.

- [ ] **Step 4: Write `docs/benchmarks/agentic-manifest.yml` and implement `bridge.py`**

The manifest uses the existing `competitors:` format, so the runner's
loaders and the report read it.
- `python-docs`: `adapter: mcp-stdio`, the command from the test, `env`
  as in the test, and `pass_env: [HOME, XDG_CACHE_HOME]`.
- `docs-fetch`: `adapter: mcp-stdio`, `command: [mcp-server-fetch]` with
  no flags (keep the robots.txt default), and
  `pass_env: [HOME, HTTPS_PROXY, HTTP_PROXY, NO_PROXY]`. Its version is
  pinned in PR 4's image.
- `no-docs`: `adapter: none`.

A header comment cites design §5.

- [ ] **Step 5: Run the tests**

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add benchmarks/agentic/ docs/benchmarks/agentic-manifest.yml tests/benchmarks/fixtures/agentic/ tests/benchmarks/test_agentic_bridge.py
git commit -m "feat(benchmarks): add the agentic MCP bridge and systems manifest"
```

### Task 5: SDK dependencies and tool schema conversion

**Files:**
- Modify: `pyproject.toml` (`[dependency-groups].dev`), `uv.lock`
- Create: `benchmarks/agentic/schemas.py`
- Test: `tests/benchmarks/test_agentic_schemas.py`

**Interfaces:**
- Consumes: `ToolSpec` (Task 4); `google.genai.types.Schema`.
- Produces:

```python
GEMINI_SCHEMA_KEYS: frozenset[str]     # JSON names of google.genai.types.Schema fields (pydantic aliases)
def to_anthropic(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]        # name, description, input_schema
def to_openai_responses(tools: Sequence[ToolSpec]) -> list[dict[str, Any]] # type function, name, description, parameters, strict False
def to_gemini(tools: Sequence[ToolSpec]) -> tuple[list[dict[str, Any]], list[str]]  # declarations, dropped paths
def schemas_sha256(converted: Any) -> str   # sha256 of json.dumps(converted, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
```

`to_openai_chat`, used only by the Ollama client, arrives in PR 4.

Gemini reduction:
- Walk the schema recursively, keeping only keys in `GEMINI_SCHEMA_KEYS`.
- The values under `properties` are property names mapped to schemas.
  Every name is kept, and the walk recurses into each schema.
- `items` and each `anyOf` member are walked too.
- A dropped key is recorded as a dotted path starting with the tool name,
  for example `fetch.properties.max_length.exclusiveMaximum`.

- [ ] **Step 1: Add the dependencies**

```bash
uv add --dev "anthropic==1.15.0" "openai==3.32.0" "google-genai==2.31.0"
uv lock --upgrade-package "jiter==0.17.0" --upgrade-package "pyasn1==0.6.5"
uv lock --check && uv sync --locked --dev
```

Then compare `uv.lock` with the base commit. The only added packages may
be the three SDKs and their transitive dependencies, and no existing
package may change version.

Check every added package's PyPI upload date. Only `pyasn1` 0.6.5 may be
younger than 14 days on the day of locking; it is the security exception
in Global Constraints. Lock any other young package one release back the
same way, and record it.

Keep the table of added packages and their dates for the PR body. Copy
the `pyasn1` reason into it: the four GHSA ids and the OSV finding against
0.6.4, from research note §E.

- [ ] **Step 2: Write the failing tests**

```python
TOOLS = [ToolSpec(t["name"], t["description"], t["inputSchema"])
         for t in json.loads((REPO_ROOT / "tests/fixtures/tools-list.json").read_text())]

def test_anthropic_tools_keep_name_description_and_input_schema_verbatim() -> None:
    assert to_anthropic(TOOLS)[0] == {"name": TOOLS[0].name, "description": TOOLS[0].description,
                                      "input_schema": TOOLS[0].input_schema}

def test_openai_tools_set_strict_false_explicitly() -> None:
    assert all(t["type"] == "function" and t["strict"] is False for t in to_openai_responses(TOOLS))

def test_gemini_reduction_drops_unsupported_keywords_and_records_them() -> None:
    spec = ToolSpec("fetch", "d", {"type": "object", "properties": {"max_length": {"type": "integer",
        "exclusiveMaximum": 1000000, "exclusiveMinimum": 0, "default": 5000}}, "required": []})
    decls, dropped = to_gemini([spec])
    assert decls[0]["parameters"]["properties"]["max_length"] == {"type": "integer", "default": 5000}
    assert dropped == ["fetch.properties.max_length.exclusiveMaximum",
                       "fetch.properties.max_length.exclusiveMinimum"]

def test_gemini_reduction_keeps_a_property_named_like_a_keyword() -> None:
    # properties {"format": {"type": "string"}, "pattern": {"type": "string"}} survive unchanged

def test_gemini_reduction_of_the_real_tool_list_drops_nothing() -> None:
    assert to_gemini(TOOLS)[1] == []

def test_gemini_schema_keys_come_from_the_pinned_sdk() -> None:
    assert {"anyOf", "enum", "minimum", "maxLength", "description", "default"} <= GEMINI_SCHEMA_KEYS
    assert "exclusiveMinimum" not in GEMINI_SCHEMA_KEYS

def test_schemas_sha256_ignores_key_order() -> None
```

- [ ] **Step 3: Run them and watch them fail**

Run: `ISO=$(mktemp -d); uv run --locked env XDG_CACHE_HOME="$ISO" pytest tests/benchmarks/test_agentic_schemas.py -q`
Expected: FAIL with `ImportError`.

- [ ] **Step 4: Implement `schemas.py`**

Derive `GEMINI_SCHEMA_KEYS` from `types.Schema.model_fields`, using each
field's `alias` (the SDK sets a camelCase alias generator).

- [ ] **Step 5: Run the tests, then pyright**

Run: the test command, then `uv run --locked pyright src/ benchmarks/`.
Expected: PASS, `0 errors`.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock benchmarks/agentic/schemas.py tests/benchmarks/test_agentic_schemas.py
git commit -m "feat(benchmarks): add the provider SDKs and tool schema conversion"
```

### Task 6: Anthropic and OpenAI Responses clients

**Files:**
- Create: `benchmarks/agentic/clients.py`
- Modify: `benchmarks/agentic/types.py` (turn types)
- Test: `tests/benchmarks/test_agentic_clients.py`

**Interfaces:**
- Consumes: `Buckets`, `anthropic_buckets`, `openai_buckets` (Task 3); `ToolSpec` (Task 4); `to_anthropic`, `to_openai_responses` (Task 5).
- Produces:

```python
# types.py
@dataclass(frozen=True)
class ToolCall:
    id: str                      # provider id, or "call_<n>" (1-based within the turn) when missing
    name: str
    arguments: dict[str, Any]
    raw_arguments: str | None = None   # set only when the arguments were not a JSON object
@dataclass(frozen=True)
class ToolResult:
    call_id: str; name: str; text: str; is_error: bool   # text as sent, after truncation
@dataclass(frozen=True)
class UserText:
    text: str
@dataclass(frozen=True)
class ToolResults:
    results: tuple[ToolResult, ...]
@dataclass(frozen=True)
class ModelTurn:
    text: str                    # visible text blocks joined with ""
    tool_calls: tuple[ToolCall, ...]
    replay: Any                  # provider-native assistant content, appended back unchanged
    raw_response: dict[str, Any] # SDK response model_dump(mode="json")
    raw_usage: dict[str, Any] | None
    buckets: Buckets | None
    reported_model: str | None
    stop_reason: str | None      # normalized per provider; see the request rules below
@dataclass
class Conversation:
    system: str
    tools: list[ToolSpec]
    entries: list[UserText | ModelTurn | ToolResults]
class ProviderCallError(Exception):
    def __init__(self, kind: Literal["rejected", "rate_limited", "server", "timeout", "connection"],
                 message: str, *, status: int | None = None,
                 retry_after_s: float | None = None) -> None: ...   # stores kind, status, retry_after_s
class ModelClient(Protocol):
    provider: str
    model_id: str
    def request_params(self) -> dict[str, Any]          # every setting sent except system/tools/messages
    def rendered_bytes(self, conv: Conversation, start: int) -> int
        # UTF-8 bytes of the provider-rendered request: system + tools + entries[start:] when
        # start == 0, else entries[start:] only
    async def call(self, conv: Conversation) -> ModelTurn   # one HTTP request; raises ProviderCallError

# clients.py
def tool_arguments(value: Any) -> tuple[dict[str, Any], str | None]
    # a str is parsed with json.loads; a dict result passes; anything else (array, scalar, null,
    # invalid JSON) returns ({}, raw), where raw is the original string or json.dumps(value)
def parse_retry_after(headers: Mapping[str, str]) -> float | None
    # retry-after-ms (ms); retry-after as seconds, or as an HTTP date via
    # email.utils.parsedate_to_datetime (seconds until that time, at least 0)
def anthropic_sdk(api_key: str, timeout_s: float, *, http_client: httpx2.AsyncClient | None = None) -> AsyncAnthropic
def openai_sdk(api_key: str, timeout_s: float, *, base_url: str | None = None,
               http_client: httpx2.AsyncClient | None = None) -> AsyncOpenAI
class AnthropicClient:      # ModelClient; __init__(self, sdk, model_id, max_output_tokens)
class OpenAIResponsesClient:  # ModelClient; __init__(self, sdk, model_id, max_output_tokens)
```

The Ollama client (`OllamaChatClient`), `ollama_buckets` and the Ollama
guard entry arrive in PR 4, where they can be smoke-run.

Request rules:
- **Anthropic.** Send `model`, `max_tokens`, `system`, `tools`
  (`to_anthropic`), `tool_choice={"type": "auto"}` and `messages`. No
  sampling parameters and no `cache_control`. Replay the response
  `content` blocks unchanged, thinking blocks and their signatures
  included. Each tool result is a `tool_result` block with `is_error`.
  `tool_use.input` goes through `tool_arguments`.
- **OpenAI Responses.**
  - Send `model`, `instructions` (the system prompt), `input`, `tools`,
    `tool_choice="auto"`, `max_output_tokens`, `store=False` and
    `include=["reasoning.encrypted_content"]`.
  - Send no prompt-cache option (`prompt_cache_options`,
    `prompt_cache_retention`). The default 30-minute retention is the one
    the snapshot prices.
  - Replay every output item unchanged. A tool result is
    `{"type": "function_call_output", "call_id": ..., "output": text}`.
  - `arguments` goes through `tool_arguments`.
- **Stop reason.** Anthropic: `response.stop_reason`. OpenAI:
  `incomplete_details.reason if status == "incomplete" else status`.
- **Errors.**
  - 429 is `rate_limited`, with `parse_retry_after` applied to the
    response headers.
  - Status 500 and above is `server`. Any other 4xx is `rejected`.
  - `APITimeoutError` is `timeout`; `APIConnectionError` is `connection`.
- **SDK construction.** `max_retries=0` and `timeout=timeout_s`, so each
  `call` is exactly one HTTP request (#177 item 1, ruling B1).

- [ ] **Step 1: Write the failing tests**

All HTTP goes through
`httpx2.AsyncClient(transport=httpx2.MockTransport(handler))`. The handler
records each request and returns a canned JSON body written inline in the
test.

```python
def test_sdk_clients_have_their_retries_off() -> None:
    assert anthropic_sdk("test-key", 30).max_retries == 0
    assert openai_sdk("test-key", 30).max_retries == 0

@pytest.mark.parametrize("provider", ["anthropic", "openai"])
@pytest.mark.parametrize("status", [200, 500, 529])
async def test_each_client_sends_one_request_per_call(provider, status) -> None:
    # counting MockTransport; a 200 returns a valid body, 5xx an error body
    # call once (catch ProviderCallError on 5xx) -> len(seen) == 1

async def test_anthropic_request_has_auto_tool_choice_and_no_sampling_parameters() -> None:
    body = json.loads(seen[0].content)
    assert body["tool_choice"] == {"type": "auto"} and body["max_tokens"] == 32768
    assert not {"temperature", "top_p", "top_k", "cache_control"} & set(body)
    assert turn.tool_calls == (ToolCall("toolu_1", "search_docs", {"query": "json"}),)
    assert turn.buckets == Buckets(100, 0, 20, 30, 0) and turn.reported_model == "claude-sonnet-5-5"

async def test_anthropic_replays_the_assistant_content_unchanged() -> None:
    # first response holds {"type": "thinking", "thinking": "t", "signature": "sig"} + tool_use;
    # second request's messages[1]["content"] equals the first response's content list

@pytest.mark.parametrize("status,headers,kind,retry_after", [
    (429, {"retry-after": "7"}, "rate_limited", 7.0), (429, {"retry-after-ms": "1500"}, "rate_limited", 1.5),
    (400, {}, "rejected", None), (500, {}, "server", None), (529, {}, "server", None)])
async def test_anthropic_status_errors_are_classified(status, headers, kind, retry_after) -> None

async def test_openai_timeout_and_connection_errors_are_classified() -> None:
    # handler raises httpx2.ReadTimeout -> kind "timeout"; httpx2.ConnectError -> "connection"

async def test_openai_responses_request_and_usage() -> None:
    assert body["tool_choice"] == "auto" and body["store"] is False
    assert body["include"] == ["reasoning.encrypted_content"] and body["max_output_tokens"] == 32768
    assert all(t["strict"] is False for t in body["tools"]) and "temperature" not in body
    assert turn.buckets == Buckets(500, 300, 200, 50, 40)

@pytest.mark.parametrize("status,details,expected", [
    ("incomplete", {"reason": "max_output_tokens"}, "max_output_tokens"), ("completed", None, "completed")])
async def test_openai_stop_reason_is_normalized(status, details, expected) -> None:
    assert turn.stop_reason == expected

async def test_openai_request_never_asks_for_extended_cache_retention() -> None:
    # a tool turn, then a follow-up call; for both captured request bodies and request_params():
    assert not {"prompt_cache_options", "prompt_cache_retention"} & set(body)

async def test_openai_replays_output_items_and_sends_function_call_output() -> None

@pytest.mark.parametrize("raw", ["{not json", "[1, 2]", "3", "\"x\"", "null", "true"])
async def test_openai_non_object_arguments_are_kept_raw(raw) -> None:
    assert turn.tool_calls[0].arguments == {} and turn.tool_calls[0].raw_arguments == raw

def test_parse_retry_after_accepts_an_http_date() -> None
    # format a date 30 s ahead with email.utils.format_datetime(usegmt=True); 29 < value <= 30
def test_rendered_bytes_grows_with_each_entry() -> None
```

- [ ] **Step 2: Run them and watch them fail**

Run: `ISO=$(mktemp -d); uv run --locked env XDG_CACHE_HOME="$ISO" pytest tests/benchmarks/test_agentic_clients.py -q`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement the turn types and the two clients**

- [ ] **Step 4: Run the tests**

Expected: PASS. The existing guard tests in `tests/benchmarks/test_adapters.py` still pass.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/agentic/ tests/benchmarks/test_agentic_clients.py
git commit -m "feat(benchmarks): add Anthropic and OpenAI agentic clients"
```

### Task 7: Gemini client, scripted client and the client factory

**Files:**
- Modify: `benchmarks/agentic/clients.py`
- Create: `tests/benchmarks/fixtures/agentic/script-anthropic.yml`, `script-openai.yml`, `script-google.yml`
- Test: `tests/benchmarks/test_agentic_clients.py` (append)

**Interfaces:**
- Consumes:
  - Task 6 types and `tool_arguments`;
  - the bucket functions (Task 3);
  - `to_gemini` (Task 5);
  - `require_live_environment` and `PROVIDER_API_KEY_ENV` (guard);
  - `ModelFamily` (Task 1).
- Produces:

```python
PAID_KEY_ENVS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY")
def gemini_sdk(api_key: str, timeout_s: float, *, http_client: httpx.AsyncClient | None = None) -> genai.Client
    # HttpOptions(timeout=int(timeout_s * 1000), retry_options=HttpRetryOptions(attempts=1),
    #             httpx_async_client=http_client)
class GeminiClient:      # ModelClient; __init__(self, sdk, model_id, max_output_tokens)
class ScriptedClient:    # ModelClient; __init__(self, provider, model_id, steps: list[dict[str, Any]])
def make_client(family: ModelFamily, *, timeout_s: float,
                http_client: Any | None = None) -> ModelClient
    # for api "scripted", family.raw["script"] is an absolute path: Task 10's plan_jobs resolves it
```

Gemini:
- **Request.** Call `sdk.aio.models.generate_content` with
  `GenerateContentConfig(system_instruction, tools=[Tool(function_declarations=...)],
  tool_config=ToolConfig(function_calling_config=FunctionCallingConfig(mode="AUTO")),
  automatic_function_calling=AutomaticFunctionCallingConfig(disable=True),
  automatic_continuation=False, max_output_tokens)`.
- **No continuation.** google-genai 2.31.0 follows `finish_reason ==
  CONTINUATION` with extra requests by default (`models.py`, async
  `generate_content` docstring near line 8388 and
  `_generate_content_with_continuation`). `automatic_continuation=False`
  stops that, so each `call` is one request (ruling B1).
- **Stop reason** is `candidates[0].finish_reason` (`STOP`, `MAX_TOKENS`,
  `CONTINUATION`, ...).
- **A `CONTINUATION` stop** comes back as a turn with
  `stop_reason="CONTINUATION"` and is not followed. When it has no tool
  calls, its text is the answer. The owner ruled (round 3) that it counts
  as an output-cap stop (Task 10).
- **Replay** `candidate.content` unchanged, thought signatures included.
- **Function calls.** A call without an `id` gets `call_<n>` by position
  in the turn. `args` goes through `tool_arguments`.
- **Function results** are built as
  `Part(function_response=FunctionResponse(id=<provider id or None>, name=..., response=...))`,
  where `response` is `{"output": text}` or `{"error": text}`.
- `reported_model` is `response.model_version`.
- **Errors.**
  - `errors.ClientError` with code 429 is `rate_limited`; any other
    `ClientError` is `rejected`; `errors.ServerError` is `server`.
  - `httpx.TimeoutException` is `timeout`; `httpx.TransportError` is
    `connection`.

Scripted script (`family.raw["script"]`, a YAML path):
- The file is `steps:`, a list. Each step is one of three kinds:
  - `{tools: [{name, arguments}], usage}`: a turn that calls those
    tools;
  - `{text, usage}`: a final answer;
  - `{error: {kind, status, retry_after_s}}`.
- A `text` step may set `stop_reason` (default: `end_turn`, `completed`
  or `STOP`, by provider). A step with `prompt_contains: <text>` applies
  only when the first user message contains that text.
- `usage` is the provider-native snake_case dict, or `null` for no usage.
  Buckets come from the matching Task 3 function. `arguments` may be any
  YAML value and goes through `tool_arguments`. `"{prompt}"` in a string
  argument becomes the first user message.
- The client counts its calls per `Conversation` object, held strongly
  (a dict keyed by the object itself), never by a bare `id(conv)`, which
  CPython reuses once a conversation is freed. The n-th call plays the
  n-th step that applies.
- A `tools` step applies only when every tool it names is in
  `conv.tools`, so `no-docs` skips straight to the answer. An optional
  `delay_s` on any step sleeps first.

Factory:
- `api == "scripted"`: return a `ScriptedClient` that reads the absolute
  `family.raw["script"]`. No guard and no key.
- `api` in `anthropic-messages`, `openai-responses` or
  `gemini-generate-content`:
  1. Call `require_live_environment(family.provider)`.
  2. If any `PAID_KEY_ENVS` variable other than this provider's is set,
     raise `BenchmarkValidationError` naming it.
  3. Build the SDK with the key from `PROVIDER_API_KEY_ENV`.
- Any other `api` raises `BenchmarkValidationError`. That includes
  `openai-chat-completions`, which arrives in PR 4.

- [ ] **Step 1: Write the failing tests**

```python
def test_gemini_client_makes_one_attempt_and_aiohttp_is_absent() -> None:
    sdk = gemini_sdk("test-key", 30)
    assert sdk._api_client._http_options.retry_options.attempts == 1
    # google-genai 2.31.0's aiohttp transport retries a connection error once whatever the
    # options say (_api_client.py); keep that transport out of the environment
    assert importlib.util.find_spec("aiohttp") is None

async def test_gemini_sends_one_request_when_the_response_asks_to_continue() -> None:
    # counting httpx.MockTransport returns candidates[0] with finishReason "CONTINUATION",
    # continuationToken "dG9r" and partial text
    assert len(seen) == 1 and turn.stop_reason == "CONTINUATION"

async def test_gemini_sends_one_request_on_a_server_error() -> None    # 500 -> ProviderCallError, 1 request

async def test_gemini_request_uses_auto_mode_and_disables_automatic_calling() -> None:
    assert body["toolConfig"]["functionCallingConfig"]["mode"] == "AUTO"
    assert body["generationConfig"]["maxOutputTokens"] == 32768
    assert turn.buckets == Buckets(950, 0, 100, 50, 20) and turn.reported_model == "gemini-3.8-flash-001"

async def test_gemini_pairs_two_calls_of_the_same_function_by_position() -> None:
    # response parts: two functionCall "search_docs" without ids
    assert [c.id for c in turn.tool_calls] == ["call_1", "call_2"]
    # the next request ends with two functionResponse parts, in that order, each named
    # "search_docs" with response {"output": <its text>} and no id

@pytest.mark.parametrize("status,kind", [(429, "rate_limited"), (400, "rejected"), (500, "server")])
async def test_gemini_status_errors_are_classified(status, kind) -> None

def test_make_client_refuses_a_live_api_without_the_guard(monkeypatch) -> None   # LiveProviderDisabledError
def test_make_client_refuses_a_second_paid_key(monkeypatch) -> None:
    # flag on, ANTHROPIC_API_KEY and OPENAI_API_KEY set -> make_client(anthropic family) raises,
    # message names OPENAI_API_KEY and holds neither value
def test_make_client_refuses_the_ollama_api_until_pr_4(monkeypatch) -> None
def test_make_client_builds_a_scripted_client_without_any_key(monkeypatch) -> None
async def test_scripted_client_skips_tool_steps_for_tools_it_was_not_given() -> None
async def test_scripted_client_raises_a_scripted_provider_error() -> None:
    # step {error: {kind: rate_limited, status: 429, retry_after_s: 0}} -> ProviderCallError, then the next step
```

- [ ] **Step 2: Run them and watch them fail**

Expected: FAIL (`gemini_sdk`, `ScriptedClient`, `make_client` undefined).

- [ ] **Step 3: Implement, and write the three scripts**

Each script has three steps:
1. `tools: [{name: search_docs, arguments: {query: json}}]`;
2. `tools: [{name: fetch, arguments: {url: "https://docs.python.org/3/library/json.html"}}]`;
3. `text: "Scripted answer."`.

Each step carries `usage` in its provider's shape.

- [ ] **Step 4: Run the tests**

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/agentic/clients.py tests/benchmarks/fixtures/agentic/ tests/benchmarks/test_agentic_clients.py
git commit -m "feat(benchmarks): add the Gemini and scripted clients and the client factory"
```

### Task 8: The trajectory loop

**Files:**
- Create: `benchmarks/agentic/prompt.py`, `benchmarks/agentic/loop.py`
- Test: `tests/benchmarks/test_agentic_loop.py`

**Interfaces:**
- Consumes:
  - `ModelClient`, `Conversation`, the turn types and `ProviderCallError` (Task 6);
  - `ScriptedClient` (Task 7, used in tests);
  - `ToolBridge`, `ToolOutcome`, `BridgeCrash` and `truncate_utf8` (Task 4);
  - `Buckets`, `Outcome` and `LedgerService` (Task 3; `LedgerService`
    satisfies `LedgerPort`);
  - `ModelFamily` (Task 1).
- Produces:

```python
# prompt.py
SYSTEM_PROMPT = (
    "You answer questions about the Python standard library.\n"
    "Give a concise answer. When the question names a Python version, "
    "state the behavior for that version.\n"
    "Do not make claims you cannot support.\n"
    "Ground your answer in the official Python documentation; if a documentation "
    "tool is available, use it before answering and cite the section URL you relied on."
)

# loop.py
@dataclass(frozen=True)
class Caps:
    model_calls: int = 8
    tool_calls: int = 12
    tool_call_s: float = 30.0
    trajectory_s: float = 300.0
    result_bytes: int = 100_000
    model_call_timeout_s: float = 300.0   # = trajectory_s (owner ruling, #197 round 2)
HOSTED_CAPS = Caps()
AIR_GAPPED_CAPS = Caps(trajectory_s=900.0, model_call_timeout_s=600.0)
MAX_RETRIES = 4
MAX_RETRY_AFTER_S = 120.0
RETRYABLE = frozenset({"rate_limited", "server", "timeout", "connection"})

class LedgerPort(Protocol):
    async def reserve(self, *, provider: str, model_id: str, input_bound: int,
                      output_bound: int, kind: str = "model_call") -> str | None: ...
    async def settle(self, unit_id: str, *, outcome: Outcome, buckets: Buckets | None) -> None: ...

class TrajectoryDiscarded(Exception):
    def __init__(self, reason: Literal["budget_stop", "retries_exhausted"],
                 record: dict[str, Any]) -> None: ...   # record: the partial record, for audit only

def full_input_bound(client: ModelClient, conv: Conversation, framing: int) -> int
    # client.rendered_bytes(conv, 0) + framing * (len(conv.entries) + 1)   (+1: system/tools block)
def next_input_bound(previous: Buckets, *, added_bytes: int, added_entries: int, framing: int) -> int
    # previous.total_input + previous.output + added_bytes + framing * added_entries
def backoff_s(retry: int, retry_after_s: float | None) -> float | None
    # retry_after_s when given (None above MAX_RETRY_AFTER_S), else min(60, 2 ** retry); retry is 1-based
def canonical_messages(conv: Conversation) -> list[dict[str, Any]]
    # Anthropic-Messages shape, visible content only: user text; assistant text + tool_use
    # {id, name, input}; user tool_result {tool_use_id, content, is_error}. No thinking/reasoning.
async def run_trajectory(*, client: ModelClient, bridge: ToolBridge | None, family: ModelFamily,
                         prompt: str, ledger: LedgerPort, caps: Caps, seen_tool_args: set[str],
                         sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> dict[str, Any]
```

Loop rules:
- **Conversation.** Start with `SYSTEM_PROMPT`, the bridge's tools (none
  without a bridge) and the prompt verbatim.
- **Clock** (owner ruling 6, round-2 ruling B4). The trajectory clock is
  a counter of successful work.
  - After each successful model attempt and each completed tool call,
    add that unit's duration to `used_s`. When `used_s >
    caps.trajectory_s`, the trajectory ends as failed with
    `cap_trajectory_time`, before the next unit starts.
  - `trajectory_s` bounds successful work only. Time in `ledger.reserve`
    (`grant_wait_ms`), failed attempts (`failed_attempt_ms`) and retry
    backoff (`retry_wait_ms`) is excluded from it, recorded per
    trajectory, and reported in the run summary (Task 10). No call is cut
    in flight.
- **Per-attempt timeout**, enforced apart from the clock: each attempt
  runs inside `asyncio.timeout(caps.model_call_timeout_s)`. A fire, read
  with `cm.expired()`, is outcome `"timeout"`: retryable, booked at 2×
  bound. The SDK's own timeout is set to the same value.
- **Bounds** (ruling B10). The output bound is `family.max_output_tokens`,
  and framing is `family.framing_allowance_tokens`.
  - Call 0 uses `full_input_bound`.
  - A call after one with buckets uses `next_input_bound` with those
    buckets, over the entries added since. Its `added_bytes` is
    `client.rendered_bytes(conv, previous_entry_count)`.
  - A call after one that reported no usage uses `full_input_bound`
    again (ruling B6).
  - A retry reuses the failed attempt's bound.
- **Each attempt.**
  1. `reserve`. `None` raises `TrajectoryDiscarded("budget_stop", ...)`.
  2. `client.call` under the per-attempt timeout.
  3. `settle`. A success settles `"ok"` with its buckets, or `"no_usage"`
     when buckets are `None`. A `ProviderCallError` settles with its
     `kind`.
  4. A retryable kind waits `backoff_s` with the injected `sleep`. After
     `MAX_RETRIES` retries, or when `backoff_s` returns `None`, raise
     `TrajectoryDiscarded("retries_exhausted", ...)`.
  5. `rejected` ends the trajectory as failed, with category
     `provider_rejected`.
  6. Retries never count toward `caps.model_calls`.
- **Tool calls.**
  - Run them in turn order.
  - A call with `raw_arguments` set is not sent to the bridge. Its
    `is_error` text is `"tool arguments must be a JSON object: <raw>"`
    (ruling B7).
  - Each sent call runs inside `asyncio.timeout(caps.tool_call_s)`; a
    fire, read with `expired()`, fails the trajectory with
    `cap_tool_timeout`.
  - The result sent is `truncate_utf8(outcome.text, caps.result_bytes)`.
  - A call is `warm` when its key
    `f"{name}:{json.dumps(arguments, sort_keys=True, ensure_ascii=False)}"`
    is already in `seen_tool_args`. The key is added afterwards.
  - A `BridgeCrash` ends the trajectory as failed, with category
    `mcp_protocol_crash`.
- **Caps.** A failure category is `cap_model_calls`, `cap_tool_calls`,
  `cap_tool_timeout` or `cap_trajectory_time`.
  - A turn with tool calls after the 8th call fails `cap_model_calls`.
  - The 13th tool call fails `cap_tool_calls` before it runs.
- **Answer.** The first turn without tool calls is the answer: its
  `text`, which may be empty.
- **Record keys:**
  - `status`, `error` (`{category, type, message}` or `None`), `answer`;
  - `system_prompt_sha256`, `tools_sha256` and `canonical_messages`;
  - `model_calls`, a list of `{index, message_count, request_params,
    input_bound, output_bound, attempts: [{outcome, status, wall_ms,
    wait_ms, unit_id}], raw_response, raw_usage, buckets, reported_model,
    stop_reason, wall_ms}`;
  - `tool_calls`, a list of `{index, model_call_index, tool, arguments,
    raw_arguments, result, result_sha256, is_error, truncated, warm,
    wall_ms}`. `result` is the full canonical text, a name kept so the
    existing scorer's grounding check reads it;
  - `timing`: `{wall_ms, used_ms, model_ms, tool_ms, grant_wait_ms,
    failed_attempt_ms, retry_wait_ms, overhead_ms}`;
  - `started_at`, `completed_at`.

- [ ] **Step 1: Write the failing tests**

They run in-process with:
- `ScriptedClient`, or `FixedBytesClient`, a test-local subclass whose
  `rendered_bytes(conv, start)` returns fixed numbers keyed by
  `(start, len(conv.entries))`;
- `RecordingLedger`, a test-local `LedgerPort` that records every
  `reserve` call's keyword arguments, then forwards it to the real
  `LedgerService`;
- `FakeBridge`, a test-local bridge with canned outcomes and delays;
- a `LedgerService` over a `tmp_path` ledger;
- a `sleep` that records its argument and returns at once.

```python
async def test_trajectory_calls_a_tool_then_answers_and_records_every_call() -> None:
    assert record["status"] == "succeeded" and record["answer"] == "Scripted answer."
    assert [c["tool"] for c in record["tool_calls"]] == ["search_docs"]
    assert record["model_calls"][1]["message_count"] == 3
    assert record["canonical_messages"][1]["content"][0]["type"] == "tool_use"
    assert record["canonical_messages"][2]["content"][0] == {"type": "tool_result",
        "tool_use_id": record["canonical_messages"][1]["content"][0]["id"],
        "content": FAKE_TEXT, "is_error": False}

async def test_reservations_carry_independently_computed_bounds() -> None:
    # OpenAI family: max_output 32_768, max_input 1_017_232, the committed gpt-6.1-sol rates.
    # FixedBytesClient: (0, 1) -> 500, (1, 3) -> 300, (0, 5) -> 2_000.
    # Script: tool step, usage {input_tokens: 1000, output_tokens: 50, no cache} -> call 0;
    #         500 error -> call 1, attempt 1; tool step with usage null -> call 1, retry;
    #         text -> call 2, after the call without usage.
    # Every expected value is plain arithmetic here, not a call to the loop's helpers:
    #   call 0:  500 + 256 * 2                       = 1_012
    #   call 1:  1_000 + 50 + 300 + 256 * 2          = 1_862   (the retry reserves the same)
    #   call 2:  2_000 + 256 * (5 + 1)               = 3_536   (full request after no usage)
    #   dollars: input * $2.50 / 1M (cache-write rate, short tier) + 32_768 * $10 / 1M
    assert [r["input_bound"] for r in recording.reserves] == [1_012, 1_862, 1_862, 3_536]
    assert [r["output_bound"] for r in recording.reserves] == [32_768] * 4
    bounds = [Decimal(e["bound"]) for e in ledger.events(RUN_ID) if e["event"] == "reserve"]
    assert bounds == [Decimal("0.33021"), Decimal("0.332335"), Decimal("0.332335"), Decimal("0.33652")]
    # a fake that reserved a fixed 200_000 input tokens would book 0.82768 for call 0 and fail here

async def test_ninth_model_call_is_a_cap_failure() -> None:
    assert record["error"]["category"] == "cap_model_calls" and len(record["model_calls"]) == 8
async def test_thirteenth_tool_call_is_a_cap_failure() -> None:
    assert record["error"]["category"] == "cap_tool_calls" and len(record["tool_calls"]) == 12
async def test_slow_tool_call_ends_the_trajectory() -> None          # Caps(tool_call_s=0.05)
async def test_successful_work_over_the_allowance_is_a_cap_failure() -> None:
    # Caps(trajectory_s=0.1), tool step delay_s 0.25 -> "cap_trajectory_time", one model call, one reserve
async def test_grant_wait_longer_than_the_trajectory_allowance_still_succeeds() -> None:
    # Caps(trajectory_s=0.2); a LedgerPort wrapper whose reserve sleeps 0.3 s first
    assert record["status"] == "succeeded" and record["timing"]["grant_wait_ms"] >= 300
async def test_failed_attempt_longer_than_the_remaining_allowance_is_retried() -> None:
    # Caps(trajectory_s=0.3): step delay_s 0.5 + error server, then text
    assert record["status"] == "succeeded"
    assert [a["outcome"] for a in record["model_calls"][0]["attempts"]] == ["server", "ok"]
    assert record["timing"]["failed_attempt_ms"] >= 500
async def test_attempt_over_its_own_timeout_is_a_retried_timeout() -> None:
    # Caps(model_call_timeout_s=0.1): step delay_s 1, then text -> attempts ["timeout", "ok"];
    # the timed-out unit is booked at 2x its bound
async def test_result_over_the_cap_is_truncated_and_the_loop_continues() -> None:
    assert record["tool_calls"][0]["truncated"] is True and record["status"] == "succeeded"
async def test_unknown_tool_call_is_returned_to_the_model_and_the_loop_continues() -> None
@pytest.mark.parametrize("arguments", [[1, 2], "x", 3, None, True])
async def test_non_object_tool_arguments_are_returned_to_the_model(arguments) -> None:
    assert record["tool_calls"][0]["is_error"] is True and bridge.calls == []
    assert record["tool_calls"][0]["result"].startswith("tool arguments must be a JSON object")
async def test_rate_limit_is_retried_honouring_retry_after_and_not_counted_as_a_model_call() -> None:
    assert sleeps == [7.0] and len(record["model_calls"]) == 1
    assert [a["outcome"] for a in record["model_calls"][0]["attempts"]] == ["rate_limited", "ok"]
    assert record["timing"]["retry_wait_ms"] == 7000
async def test_server_error_is_booked_at_twice_the_bound_and_retried() -> None
async def test_retries_exhausted_discards_the_trajectory() -> None:
    # five 500s -> TrajectoryDiscarded("retries_exhausted"); sleeps == [2, 4, 8, 16]
async def test_rejected_request_is_a_scored_failure_booked_at_zero() -> None
async def test_budget_stop_discards_the_trajectory() -> None          # caps {"anthropic": Decimal("0")}
async def test_repeated_tool_arguments_are_marked_warm() -> None     # shared set, two trajectories
async def test_empty_final_text_is_an_empty_answer() -> None
async def test_bridge_crash_fails_the_trajectory_as_a_protocol_crash() -> None
async def test_no_docs_sends_no_tools() -> None
def test_input_bounds() -> None:
    assert next_input_bound(Buckets(100, 0, 20, 30), added_bytes=500, added_entries=2, framing=256) == 1162
def test_backoff() -> None:
    assert [backoff_s(r, None) for r in (1, 2, 3, 4)] == [2, 4, 8, 16]
    assert backoff_s(1, 7.0) == 7.0 and backoff_s(1, 121.0) is None
    assert backoff_s(1, 0.0) == 0.0
def test_system_prompt_follows_the_design() -> None:
    assert ("ground your answer in the official python documentation; if a documentation tool is "
            "available, use it before answering and cite the section url you relied on") in SYSTEM_PROMPT.lower()
    for name in ("python-docs", "docs-fetch", "context7", "mcp", "fetch"):
        assert name not in SYSTEM_PROMPT.lower()
```

- [ ] **Step 2: Run them and watch them fail**

Run: `ISO=$(mktemp -d); uv run --locked env XDG_CACHE_HOME="$ISO" pytest tests/benchmarks/test_agentic_loop.py -q`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `prompt.py` and `loop.py` to the rules above**

- [ ] **Step 4: Run the tests three times**

Run the test command three times in a row. Expected: PASS each time. The
timing tests keep at least 0.1 s between the threshold and the delay, so
a flaky result means a bug, not noise.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/agentic/prompt.py benchmarks/agentic/loop.py tests/benchmarks/test_agentic_loop.py
git commit -m "feat(benchmarks): add the agentic trajectory loop with caps and retries"
```

### Task 9: Worker and host over length-prefixed frames

**Files:**
- Create: `benchmarks/agentic/protocol.py`, `benchmarks/agentic/worker.py`, `benchmarks/agentic/host.py`
- Modify: `benchmarks/__main__.py` (`agentic-worker`)
- Test: `tests/benchmarks/test_agentic_host.py`

**Interfaces:**
- Consumes:
  - `run_trajectory`, `TrajectoryDiscarded`, `Caps` and `LedgerPort` (Task 8);
  - `make_client` and `PAID_KEY_ENVS` (Task 7);
  - `open_bridge`, `SystemSpec` and `system_spec_from_raw` (Task 4);
  - the `to_*` converters and `schemas_sha256` (Task 5);
  - `LedgerService` (Task 3);
  - `model_family_from_raw` (Task 1).
- Produces:

```python
# protocol.py
MAX_FRAME_BYTES = 64 * 1024 * 1024
KEY_SHAPED = re.compile(r"(?<![A-Za-z0-9])(?:sk-ant-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9_-]{20,}"
                        r"|AIza[0-9A-Za-z_-]{30,})")
class KeyLeakError(BenchmarkValidationError): ...
class FrameTooLarge(BenchmarkValidationError): ...
def encode_frame(message: dict[str, Any], *, limit: int = MAX_FRAME_BYTES) -> bytes
    # 8-byte big-endian length + compact UTF-8 JSON; raises FrameTooLarge above `limit`
async def read_frame(reader: asyncio.StreamReader, *, limit: int = MAX_FRAME_BYTES) -> dict[str, Any] | None
    # None at a clean EOF or a partial frame (the writer died); FrameTooLarge when the declared
    # length is above `limit`, before reading the body
def assert_no_key_shaped(text: str, *, where: str) -> None   # message names `where`, never the match
def contains_own_key(text: str, own_key: str | None) -> bool   # False when own_key is None

# worker.py
MAX_RERUNS = 2
class ProtocolChannel:
    def send(self, message: dict[str, Any]) -> None   # refuses a frame holding the own key
    def close(self) -> None                           # drains the redactor, restores nothing
def protect_output(own_key: str | None) -> ProtocolChannel
async def run_worker(channel: ProtocolChannel, reader: asyncio.StreamReader) -> int
def main_worker() -> int     # `python -m benchmarks agentic-worker`

# host.py
@dataclass(frozen=True)
class WorkerJob:
    job_id: str                          # f"{family.id}--{system.id}"
    family: ModelFamily                  # family.raw["script"] is absolute for scripted families
    system: SystemSpec
    trajectories: tuple[dict[str, Any], ...]   # {"corpus_id", "prompt", "repeat"}, in run order
    caps: Caps
class Launcher(Protocol):
    def supports(self, family: ModelFamily) -> bool: ...
    def command(self, job: WorkerJob) -> tuple[list[str], dict[str, str]]: ...
class LocalLauncher:     # supports only api == "scripted"; PR 4 adds a container launcher
    ...
def refuse_host_keys(environ: Mapping[str, str]) -> None
async def run_jobs(jobs: Sequence[WorkerJob], *, launcher: Launcher, ledger: LedgerService,
                   on_message: Callable[[WorkerJob, dict[str, Any]], Awaitable[None]],
                   log_dir: Path, cwd: Path, max_frame_bytes: int = MAX_FRAME_BYTES) -> None
```

Protocol (length-prefixed JSON frames, ruling B3):
- **Host to worker,** on the worker's stdin. The first frame is
  `{"type": "job", "job_id", "family": <raw>, "system": <raw>,
  "trajectories", "caps"}`. Replies are `{"type": "grant", "unit_id"}`,
  `{"type": "deny"}` and `{"type": "ack"}`.
- **Worker to host,** on the dedicated protocol channel:
  - `system_info`: `{server_info, tools_raw, tools_raw_sha256,
    tools_converted, tools_converted_sha256, gemini_dropped,
    index_sha256, client_versions}`. `index_sha256` is set for the
    `python-docs` system only, from `get_index_path()`.
    `client_versions` comes from `importlib.metadata` for `anthropic`,
    `openai`, `google-genai` and `mcp`.
  - `reserve` and `settle`, mirroring `LedgerPort`, with buckets as a
    dict.
  - `trajectory {record, corpus_id, repeat}`.
  - `discarded {record, corpus_id, repeat, reason, attempt}`.
  - `not_run {corpus_id, repeat, reason}`.
  - `error {reason}` and `done`.

  `reserve`, `settle`, `trajectory` and `discarded` wait for a reply.
- **Size.** A frame of any size up to `MAX_FRAME_BYTES` round-trips. A
  larger frame raises `FrameTooLarge` on the side that meets it.
  - On the worker, it sends `error` and exits with status 2.
  - On the host, it kills every worker and raises.
  - Either way the run stops as a harness error (CLI exit 2). It is never
    scored as a model failure.

Worker rules:
- **`protect_output(own_key)` runs before anything else** (ruling B2).
  1. Keep `os.dup(1)` as the protocol channel.
  2. Keep `os.dup(2)` as the diagnostics sink.
  3. Create a pipe, then `os.dup2` its write end onto fd 1 and fd 2.
  4. Start a daemon thread that reads the pipe, replaces every occurrence
     of `own_key` with `[redacted]`, and writes to the diagnostics sink.
     It holds back the last `len(own_key) - 1` bytes until more data or
     EOF, so a key split across two writes is still caught.
  5. Reset `sys.stdout` and `sys.stderr` to fresh text wrappers over fd 1
     and fd 2.

  So `print()`, `sys.stderr.write`, `os.write(2, ...)`, tracebacks and the
  MCP servers' inherited stderr all pass the redactor. Only
  `ProtocolChannel.send` writes the protocol fd.
- **Own key.** `own_key` is the worker's provider key from
  `PROVIDER_API_KEY_ENV`, or `None`.
- **Sending.** `send` raises when `contains_own_key(frame_text, own_key)`
  is true. The worker then sends `error` with reason
  `"own key found in output"`, and exits with status 3.
- **Shutdown order.**
  1. Close the bridge, so the MCP server child exits and drops its copy
     of fd 2.
  2. Send `done`.
  3. `ProtocolChannel.close()`: flush `sys.stdout` and `sys.stderr`,
     point fds 1 and 2 at `/dev/null`, close the pipe's write end, and
     join the redactor thread with a 5 s timeout, so every byte is
     drained before exit.

  The thread is a daemon, so a grandchild that still holds the pipe
  cannot keep the worker alive. Its bytes are dropped, never written
  unredacted.
- **Mark** `protect_output` with
  `# shortcut: fd redirection is POSIX only; the harness runs on macOS and Linux`.
- **Rebuild.** The worker rebuilds its family with
  `model_family_from_raw(job["family"])` and its system with
  `system_spec_from_raw(job["system"])`.
- **Session.** Build the client once per job with
  `make_client(family, timeout_s=job.caps.model_call_timeout_s)`. Open one
  bridge per job (none for `no-docs`), and reopen it after a
  `BridgeCrash` or a tool timeout. Keep one `seen_tool_args` set per job.
- **Budget stop.** On `budget_stop`, send `discarded`, then `not_run` for
  every remaining trajectory.
- **Exhausted retries.** On `retries_exhausted`, send `discarded` and
  requeue the trajectory at the end, at most `MAX_RERUNS` times. After
  that, send `not_run` with that reason.

Host rules:
- **Startup.** `refuse_host_keys(os.environ)` before anything else. Any
  family the launcher does not support is refused before any process
  starts.
- **Workers.** Spawn each with `asyncio.create_subprocess_exec(*argv,
  env=env, cwd=cwd, stdin=PIPE, stdout=PIPE, stderr=<log file>)`. The
  worker's stdout is the protocol channel, because it is the fd the
  worker kept as `os.dup(1)`.
- **Key scans.** Run `assert_no_key_shaped` on every received frame's
  text, and on every log file when its worker exits. On `KeyLeakError`,
  kill every worker and re-raise.
- **Lost worker.** On EOF without `done`, settle the worker's in-flight
  unit as `"worker_lost"`. Report each of its unreported trajectories to
  `on_message` as `not_run` with reason `"worker_lost"`.
- **LocalLauncher.** Its argv is `[sys.executable, "-m", "benchmarks",
  "agentic-worker"]`. Its env holds only `PATH`, `HOME`, `LANG`, `TMPDIR`,
  `XDG_CACHE_HOME` and `VIRTUAL_ENV`, where set. It never passes a
  `PAID_KEY_ENVS` name or `BENCHMARK_LIVE_PROVIDERS_ENABLED`.

- [ ] **Step 1: Write the failing tests**

```python
def test_host_refuses_to_start_with_a_provider_key_in_its_environment() -> None:
    with pytest.raises(BenchmarkValidationError, match="OPENAI_API_KEY"):
        refuse_host_keys({"OPENAI_API_KEY": "x"})

def test_local_launcher_passes_no_provider_key_and_runs_only_scripted_models(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    argv, env = LocalLauncher().command(job)
    assert argv == [sys.executable, "-m", "benchmarks", "agentic-worker"]
    assert not (set(PAID_KEY_ENVS) | {"BENCHMARK_LIVE_PROVIDERS_ENABLED"}) & set(env)
    assert LocalLauncher().supports(anthropic_live_family) is False

@pytest.mark.parametrize("text,leaks", [("sk-ant-api03-" + "a" * 40, True), ("AIza" + "b" * 35, True),
    ("sk-proj-" + "c" * 40, True), ("a disk-based cache", False), ("task-runner-" + "d" * 40, False)])
def test_key_shaped_scan(text, leaks) -> None

def test_protect_output_redacts_the_own_key_on_every_stream() -> None:
    # subprocess `python -c`: ch = protect_output(KEY); print(KEY); sys.stderr.write(KEY + "\n");
    # os.write(2, KEY[:10].encode()); os.write(2, KEY[10:].encode() + b"\n"); ch.send({"type": "done"});
    # ch.close()  with KEY = "sk-ant-synthetic-" + "k" * 40
    assert KEY.encode() not in proc_stderr and proc_stderr.count(b"[redacted]") == 3
    assert _frames(proc_stdout) == [{"type": "done"}]    # test helper; the channel holds only the frame

def test_protocol_channel_refuses_a_frame_holding_the_own_key() -> None

async def test_host_logs_and_artifacts_never_hold_a_workers_key(tmp_path) -> None:
    # a test launcher passes ANTHROPIC_API_KEY=KEY to a worker started as
    # `python -c` that wraps worker.run_worker so it print()s, sys.stderr.writes and
    # os.write(2, ...)s KEY before running a scripted job normally
    logs = "".join(p.read_text() for p in (tmp_path / "logs").iterdir())
    assert KEY not in logs and logs.count("[redacted]") == 3
    assert all(KEY not in json.dumps(m) for m in received)

async def test_worker_runs_its_job_over_the_channel_and_reports_done(tmp_path) -> None:
    # real subprocess via LocalLauncher; scripted family + no-docs system, two trajectories
    assert [m["type"] for m in received] == ["system_info", "trajectory", "trajectory", "done"]

async def test_worker_exits_with_its_mcp_server_child_still_alive_at_shutdown(tmp_path) -> None:
    # scripted job against the fake server with --edge; the script calls its pid() tool, so the
    # server child is alive and serving when the job ends
    # the worker exits 0 within 10 s, and os.kill(server_pid, 0) then raises ProcessLookupError

async def test_a_trajectory_frame_over_100000_bytes_round_trips_through_a_worker(tmp_path) -> None:
    # script whose text step is "x" * 150_000
    assert received_trajectory["record"]["answer"] == "x" * 150_000

async def test_a_frame_over_the_limit_is_a_harness_error(tmp_path) -> None:
    with pytest.raises(FrameTooLarge):
        await run_jobs([job], ..., max_frame_bytes=1_000)
    # nothing for that trajectory reached on_message as a trajectory or a failure

async def test_host_settles_a_lost_workers_unit_at_twice_its_bound_and_frees_the_provider(tmp_path) -> None:
    # a test launcher runs a `python -c` fake worker that reads the job, sends reserve, reads
    # the grant and exits 1; a second job of the same provider (normal scripted worker) completes
    assert ledger.spent("anthropic") == 2 * first_bound + second_job_actual
    assert {(m["type"], m["reason"]) for m in lost_job_messages} == {("not_run", "worker_lost")}

async def test_host_aborts_the_run_when_a_worker_frame_holds_a_key_shaped_string(tmp_path) -> None
async def test_budget_stop_marks_the_rest_of_the_job_not_run(tmp_path) -> None:
    # LedgerService over Ledger(caps={"anthropic": Decimal("0")}): one discarded(budget_stop),
    # then not_run(budget_stop) for the other trajectories, then done
async def test_retries_exhausted_trajectory_is_rerun_at_most_twice(tmp_path) -> None:
    # script: five 429s with retry_after_s 0 on every call -> three discarded (attempts 1-3),
    # then not_run(retries_exhausted)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `ISO=$(mktemp -d); uv run --locked env XDG_CACHE_HOME="$ISO" pytest tests/benchmarks/test_agentic_host.py -q`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `protocol.py`, `worker.py`, `host.py`, and add the `agentic-worker` subcommand**

The subcommand has no options. It calls `main_worker()`, which calls
`protect_output` first.

- [ ] **Step 4: Run the tests**

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/agentic/ benchmarks/__main__.py tests/benchmarks/test_agentic_host.py
git commit -m "feat(benchmarks): run agentic jobs in keyless, redacted worker processes"
```

### Task 10: Run orchestration, artifacts, CLI and the mocked end-to-end test

**Files:**
- Create: `benchmarks/agentic/run.py`
- Modify: `benchmarks/__main__.py` (`agentic-run`)
- Create: `tests/benchmarks/fixtures/agentic/model-matrix.yml`, `tests/benchmarks/fixtures/agentic/prices.yml`
- Test: `tests/benchmarks/test_agentic_run.py`

**Interfaces:**
- Consumes:
  - everything above;
  - from `benchmarks.runner`: `_load_yaml_mapping`, `_load_questions`,
    `_write_json`, `_write_snapshot`, `_environment_metadata`,
    `_default_run_id`, `_utc_now`, `_scoring_record`, `BenchmarkCell`,
    `Competitor` and `Question`;
  - `METHODOLOGY_TOKEN_LABEL` and `DEFAULT_COUNT_MODEL`.
- Produces:

```python
@dataclass(frozen=True)
class AgenticConfig:
    corpus_path: Path
    matrix_path: Path
    manifest_path: Path
    prices_path: Path
    ledger_dir: Path
    out_dir: Path
    models: tuple[str, ...] | None = None        # family ids; None = every family the launcher supports
    systems: tuple[str, ...] | None = None
    questions: tuple[str, ...] | None = None
    repeats: int = 3
    seed: int | None = None                      # None = int(sha256(run_id)[:8], 16)
    run_id: str | None = None
    dry_run: bool = False
    acknowledge_overrun: tuple[str, ...] = ()

def cell_id(system_id: str, family_id: str, repeat: int) -> str          # f"{system_id}--{family_id}--r{repeat}"
def tool_model_key(system_id: str, provider: str, model_id: str) -> str  # f"{system_id}:{provider}/{model_id}"
OUTPUT_CAP_STOPS = frozenset({"max_tokens", "max_output_tokens", "MAX_TOKENS", "CONTINUATION"})
def plan_jobs(questions: Sequence[Question], families: Sequence[ModelFamily],
              systems: Sequence[SystemSpec], *, repeats: int, seed: int,
              matrix_dir: Path) -> list[WorkerJob]
    # Before building a job, a scripted family's raw["script"] is made absolute
    # ((matrix_dir / script).resolve()) with dataclasses.replace, so make_client in the worker
    # sees an absolute path (ruling B9).
    # Per job: [(q, r) for q in questions for r in 1..repeats], shuffled with
    # random.Random(f"{seed}/{family.id}/{system.id}"); caps AIR_GAPPED_CAPS when arm == "air-gapped".
def run_agentic(config: AgenticConfig, *, launcher: Launcher | None = None,
                caps: Mapping[str, Decimal] = PROVIDER_CAPS,
                today: date | None = None) -> dict[str, Any]     # today defaults to the UTC date
```

Run rules:
- **Validation before anything starts.** Each failure raises
  `BenchmarkValidationError` and maps to CLI exit 2:
  - `refuse_host_keys`;
  - `--out` must be empty;
  - the corpus must parse;
  - every selected family and system must exist and be supported by the
    launcher;
  - every metered family needs a price entry and its hosted budget
    fields;
  - `output_cap_includes_reasoning: false` is refused, because the
    research note shows thinking inside the cap on every hosted model;
  - when any selected family is not `scripted`, `refuse_stale_snapshot`
    runs with `today` (owner ruling part 2, item 2).
- **Dry run.** Write `environment.json`, `planned-cells.json` and the
  snapshots, then stop. A dry run never opens the ledger.
- **Ledger.** `Ledger.open(ledger_dir, run_id, caps)`, then
  `acknowledge_overrun` for each listed provider. A provider still
  stopped for `overrun` is refused. Close the ledger in a `finally`.
- **Artifacts** under `--out`, every JSON write through
  `assert_no_key_shaped`. Each `<cell>/<qid>.json` uses
  `cell_id(system, family, repeat)`.
  - `environment.json`: the runner's fields plus `mode: "agentic"`,
    `seed` and `client_versions`.
  - `snapshots/`: `corpus.yml`, `competitor-manifest.yml` (the systems
    manifest, byte copy), `model-matrix.yml`, `price-snapshot.yml` and
    `system-prompt.txt`.
  - `systems/<family>--<system>.json`, from `system_info`.
  - `transcripts/`: the loop record plus `competitor_id`, `corpus_id`,
    `system_id`, `model_family_id`, `provider`, `model_id`, `repeat` and
    `tool_model_key`.
  - `scoring/`: `_scoring_record` over a synthetic `BenchmarkCell`
    (adapter `"agentic"`), with the `tool_model_key` above.
  - `tokens/`: the runner's placeholder fields, with `status:
    "placeholder"`, `count_model: DEFAULT_COUNT_MODEL`, and notes "counted
    after the run (design §8)".
  - `latency/`: the runner's latency fields plus `timing`.
  - `failures/`: failed trajectories only, with the runner's failure
    fields.
  - `discarded/<cell>/<qid>--attempt<n>.json`.
  - `logs/<job_id>.stderr.log`.
  - `ledger-events.jsonl`: this run's events.
- **`run-summary.json`.**
  - Every field `report.py` requires.
  - `planned_cells`: planned trajectories. `scored_cells`: succeeded plus
    failed. `correctness_denominator_cells`: `scored_cells`.
    `competitors`: the cell ids that were written.
  - `mode`, `seed`, `repeats`, `models` and `systems`.
  - `discarded` and `not_run`: lists of `{cell, corpus_id, reason,
    attempt}`. Every rerun appears with its attempt number (owner ruling
    5).
  - `families`, per family id:
    - `retries`: the count of retried attempts, by outcome;
    - `reruns`: the count of discarded trajectories that were requeued;
    - `answer_turns`: the count of answer turns, one per trajectory that
      reached an answer;
    - `output_cap_stops`: the answer turns whose `stop_reason` is in
      `OUTPUT_CAP_STOPS` (the code block above). This is the count the
      pilot's 1% rule reads.
    - `wall_s`: from the first start to the last completion, with retries
      included (owner ruling 6);
    - `grant_wait_s`, `failed_attempt_s` and `retry_wait_s`: the sums of
      the trajectory timings;
    - `first_started_at` and `last_completed_at`.
  - `ledger`: per provider, `{spent_before, spent_after, cap, stopped}`.
  - `price_snapshot`: `{captured_on, expires_on}`.
- **CLI.**

```
python -m benchmarks agentic-run --corpus PATH --out DIR --ledger-dir DIR
    [--matrix docs/benchmarks/model-matrix.yml] [--manifest docs/benchmarks/agentic-manifest.yml]
    [--prices docs/benchmarks/price-snapshot.yml] [--models IDS] [--systems IDS] [--questions IDS]
    [--repeats 3] [--seed N] [--run-id ID] [--dry-run] [--acknowledge-overrun PROVIDER]...
```

IDS are comma-separated. It prints the summary JSON. Exit codes:
- 0 on success;
- 3 on `KeyLeakError`. Catch it before `BenchmarkValidationError`, its
  base class;
- 2 on any other `BenchmarkValidationError`, `FrameTooLarge` included.

- [ ] **Step 1: Write the fixtures**

`model-matrix.yml` holds three scripted families: `scripted-anthropic`,
`scripted-openai` and `scripted-google`, with providers `anthropic`,
`openai` and `google`. Each is `arm: hosted` with model ids
`scripted-1/2/3` and these fields:
- `script`, the matching Task 7 script, relative to the fixture
  directory;
- `max_input_tokens: 200000` and `max_output_tokens: 1024`;
- `output_cap_includes_reasoning: true` and
  `framing_allowance_tokens: 256`.

`prices.yml` prices `scripted-1/2/3` with flat round rates and no
`expires_on`, so the test can compute the expected spend by hand.

- [ ] **Step 2: Write the failing tests**

```python
def test_agentic_run_end_to_end_with_scripted_models(tmp_path, monkeypatch) -> None:
    for name in PAID_KEY_ENVS:
        monkeypatch.delenv(name, raising=False)
    env, cache_dir = _isolated_cache_env(tmp_path)          # tests.test_stdio_smoke
    _create_contentful_json_index(cache_dir)                # tests.test_mcp_get_docs_cache_smoke
    monkeypatch.setenv("XDG_CACHE_HOME", env["XDG_CACHE_HOME"]); monkeypatch.setenv("HOME", env["HOME"])
    manifest = _write_manifest(tmp_path)   # python-docs (committed entry), docs-fetch -> fake_fetch_server.py, no-docs
    monkeypatch.chdir(tmp_path)            # the relative script paths must not depend on the cwd
    summary = run_agentic(AgenticConfig(corpus_path=SAMPLE_CORPUS, matrix_path=FIX / "model-matrix.yml",
        manifest_path=manifest, prices_path=FIX / "prices.yml", ledger_dir=tmp_path / "ledger",
        out_dir=tmp_path / "run", questions=("SYN-EX-001", "SYN-EX-002"), repeats=2, seed=7))
    assert summary["planned_cells"] == 36 and summary["succeeded_cells"] == 36
    assert summary["failed_cells"] == 0 and summary["discarded"] == [] and summary["not_run"] == []
    run = tmp_path / "run"
    def tools(system): return [c["tool"] for c in json.loads((run / "transcripts" /
        f"{system}--scripted-anthropic--r1" / "SYN-EX-001.json").read_text())["tool_calls"]]
    assert (tools("python-docs"), tools("docs-fetch"), tools("no-docs")) == (["search_docs"], ["fetch"], [])
    info = json.loads((run / "systems" / "scripted-anthropic--python-docs.json").read_text())
    assert len(info["tools_raw"]) == 7
    assert info["index_sha256"] == hashlib.sha256((cache_dir / "index.db").read_bytes()).hexdigest()
    assert summary["ledger"]["anthropic"]["spent_after"] == EXPECTED_ANTHROPIC_SPEND   # from fixture rates
    assert score_run(run, corpus_path=SAMPLE_CORPUS, schema_path=CORPUS_SCHEMA)["total_cells"] == 36
    report = generate_report(run, model_matrix_path=FIX / "model-matrix.yml",
                             methodology_path=METHODOLOGY_PATH).read_text()
    assert "scripted-1" in report
    for path in run.rglob("*"):
        if path.is_file():
            assert KEY_SHAPED.search(path.read_text(errors="replace")) is None

def test_plan_jobs_makes_script_paths_absolute_and_orders_each_job_by_seed(tmp_path) -> None:
    jobs = plan_jobs(questions, families, systems, repeats=2, seed=7, matrix_dir=FIX)
    assert all(Path(j.family.raw["script"]).is_absolute() for j in jobs)
    # order equals random.Random(f"7/{family.id}/{system.id}").shuffle of the canonical order,
    # and a second call with seed 7 gives identical jobs

def test_agentic_run_budget_stop_discards_and_lists_cells_not_run(tmp_path, monkeypatch) -> None:
    # same setup, caps={"anthropic": Decimal("0"), "openai": Decimal(65), "google": Decimal(180)}
    assert len(summary["discarded"]) + len(summary["not_run"]) == 12
    assert summary["succeeded_cells"] == 24
    assert not any((run / "scoring").glob("*--scripted-anthropic--*"))

def test_a_live_run_refuses_an_expired_price_snapshot(tmp_path, monkeypatch) -> None:
    # a launcher stub that supports every family; committed prices; today=date(2026, 12, 31)
    # -> BenchmarkValidationError naming 2026-12-31, before any process starts

def test_run_summary_counts_retries_and_reruns(tmp_path, monkeypatch) -> None:
    # one scripted family, no-docs, two questions, repeats 1. The script for the first question:
    # 429 (retry_after_s 0) then text. For the second: 429 (retry_after_s 0) on every call, so
    # its trajectory is discarded three times, then not run.
    fam = summary["families"]["scripted-anthropic"]
    assert fam["retries"]["rate_limited"] == 1 + 3 * MAX_RETRIES and fam["reruns"] == 2
    assert [d["attempt"] for d in summary["discarded"]] == [1, 2, 3] and len(summary["not_run"]) == 1

def test_run_summary_wall_time_includes_failed_attempts_backoff_and_grant_waits(tmp_path, monkeypatch) -> None:
    # a tmp matrix with two scripted families of provider anthropic, one no-docs job each, so
    # their units contend for the grant. Each script: 429 with retry_after_s 0.3 and delay_s 1.0
    # (a 1.0 s failed attempt that holds the provider's grant, so the other job's grant wait does
    # not depend on how fast two processes spawn; then 0.3 s of backoff), then text with
    # delay_s 0.1.
    for fam in summary["families"].values():
        assert fam["failed_attempt_s"] >= 0.2 and fam["retry_wait_s"] >= 0.3
        assert fam["wall_s"] >= 0.6
    assert sum(f["grant_wait_s"] for f in summary["families"].values()) > 0

def test_continuation_answers_count_as_output_cap_stops(tmp_path, monkeypatch) -> None:
    # one scripted google family, no-docs, all 50 sample questions, repeats 2 = 100 answers.
    # The script's first text step has stop_reason CONTINUATION and prompt_contains holding a
    # phrase from SYN-EX-001's prompt; the second text step answers everything else.
    fam = summary["families"]["scripted-google"]
    assert (fam["output_cap_stops"], fam["answer_turns"]) == (2, 100)        # 2%

def test_agentic_run_refuses_a_non_empty_out_dir(tmp_path) -> None
def test_agentic_run_dry_run_writes_the_plan_without_a_ledger(tmp_path) -> None
def test_cli_agentic_run_exits_2_with_a_provider_key_set(tmp_path, monkeypatch) -> None
def test_cli_agentic_run_exits_3_on_a_key_leak(tmp_path, monkeypatch) -> None
    # monkeypatch run_agentic to raise KeyLeakError -> main([...]) exits 3
```

- [ ] **Step 3: Run them and watch them fail**

Run: `ISO=$(mktemp -d); uv run --locked env XDG_CACHE_HOME="$ISO" pytest tests/benchmarks/test_agentic_run.py -q`
Expected: FAIL with `ImportError`.

- [ ] **Step 4: Implement `run.py` and the `agentic-run` subcommand**

- [ ] **Step 5: Run the tests, then the whole suite**

Expected: PASS, and the end-to-end test takes well under a minute.

- [ ] **Step 6: Commit**

```bash
git add benchmarks/agentic/run.py benchmarks/__main__.py tests/benchmarks/fixtures/agentic/ tests/benchmarks/test_agentic_run.py
git commit -m "feat(benchmarks): add agentic-run with a mocked end-to-end test"
```

### Task 11: Remove the fixed-flow adapters and provider stubs, then PR B

**Files:**
- Delete:
  - `benchmarks/adapters/python_docs_mcp_adapter.py`, `context7_adapter.py`,
    `gitmcp_adapter.py`, `deepwiki_adapter.py`, `ref_tools_adapter.py`;
  - `benchmarks/adapters/base.py`, `openai_adapter.py`, `google_adapter.py`;
  - `tests/benchmarks/test_python_docs_mcp_adapter.py` and
    `tests/benchmarks/test_competitor_adapters.py`.
- Modify:
  - `benchmarks/runner.py`: remove only the five fixed-flow dispatch
    entries, their helpers and `_tool_call_records_to_dicts`.
    - Keep `"fake"`, and leave the baseline-alias behaviour exactly as on
      `main`. #173 removed the `no-mcp-baseline` dispatch aliases, the
      owner ruled (round 3) that they stay removed, and
      `test_removed_baseline_alias_is_an_unknown_adapter` stays unchanged.
    - Update the module docstring.
  - `benchmarks/adapters/__init__.py`: exports.
  - `benchmarks/adapters/guard.py`: remove `LiveExecutionNotImplementedError`
    and its docstring text.
  - Docstrings only in `benchmarks/adapters/claude_tokens.py` and
    `eligibility.py`.
  - The header comment of `docs/benchmarks/competitor-manifest.template.yml`:
    the adapters are gone, and round 2 runs through the agentic bridge.
  - `CHANGELOG.md` `[Unreleased]`.
- Modify tests:
  - `tests/benchmarks/test_adapters.py`: delete only the mock- and
    stub-adapter tests; keep every guard test.
  - `tests/benchmarks/test_runner.py`: delete only the
    `python-docs-mcp-stdio` dispatch tests.
  - The comment at `tests/benchmarks/test_claude_tokens.py` that names
    the mock adapters.

**Interfaces:**
- Consumes: Tasks 4 and 10. The bridge now covers `python-docs` and
  `docs-fetch`, the condition design §11.1 sets for this deletion.
- Produces: nothing new. `eligibility.py` and the manifest terms gates
  stay.

- [ ] **Step 1: Record the intended test deletions**

Before deleting anything, list every test function in the deleted files.
Do the same for each test removed from `test_adapters.py` and
`test_runner.py`, with the line numbers on the base commit. This table
goes into the PR body under "Intended test deletions (design §11.1)".

- [ ] **Step 2: Delete and edit as listed**

- [ ] **Step 3: Check for leftovers**

Run: `grep -rn "python_docs_mcp_adapter\|context7_adapter\|gitmcp_adapter\|deepwiki_adapter\|ref_tools_adapter\|MockOpenAIAdapter\|MockGoogleAdapter\|LiveOpenAIAdapter\|LiveGoogleAdapter\|LiveExecutionNotImplementedError\|ProviderAdapter\|AdapterRequest" benchmarks/ tests/ docs/benchmarks/competitor-manifest.template.yml docs/benchmarks/model-matrix.yml`
Expected: no output.

- [ ] **Step 4: Add the CHANGELOG entries**

Under `## [Unreleased]`:
- **Added:** the agentic benchmark harness (`python -m benchmarks
  agentic-run`) and the three SDKs in the dev group, with versions.
- **Security:** `pyasn1` locked at 0.6.5, with the four GHSA ids.
- **Removed:** the five fixed-flow adapters and the OpenAI and Google
  provider stubs, replaced by the agentic bridge and clients.

- [ ] **Step 5: Run the full gate**

Run the three gate commands, then the PR 4 seam check below.
Expected: `All checks passed!`, `0 errors`, and every test passing. The
only skips are the ones already on `main`.

```bash
ISO=$(mktemp -d); uv run --locked env XDG_CACHE_HOME="$ISO" python -m benchmarks agentic-run \
  --corpus docs/benchmarks/corpus.yml --out "$ISO/run" --ledger-dir "$ISO/ledger" --dry-run \
  --models claude-sonnet-5-5
```

Expected: exit 2, with a message saying the local launcher runs only
scripted models.

- [ ] **Step 6: Commit, push, open PR B**

```bash
git add -A benchmarks/ tests/benchmarks/ docs/benchmarks/competitor-manifest.template.yml CHANGELOG.md
git commit -m "refactor(benchmarks): remove the fixed-flow adapters replaced by the agentic bridge"
git push -u origin feat/agentic-harness
```

Open PR B with `gh pr create` against `main`:
- **Title:** `feat: agentic benchmark harness with mocked end-to-end CI`.
- **Label:** `supervisor-review`.
- **Body:**
  - `Closes #177` and `Refs #63`;
  - each Global Constraint, checked;
  - the added-packages table with upload dates and the `pyasn1` security
    reason (Task 5);
  - the intended-deletions table (Step 1);
  - the gate output;
  - a "Why this triggered supervisor review" section citing design §13
    for the dependencies and design §11.1 for the deletions.

---

## Not in this plan

- **PR 4:**
  - the Dockerfile and the container launcher (`--env-file`, one key per
    container, `--read-only`, tmpfs, `--cap-drop ALL`);
  - the per-worker egress proxy and its logs, passed to each SDK client
    explicitly (the clients ignore proxy and CA variables);
  - the container launcher's worker environment as an allowlist that
    excludes every `*_PROXY`, `SSL_CERT_*` and `REQUESTS_CA_BUNDLE`
    variable (a SOCKS `ALL_PROXY` crashes the Anthropic SDK at
    construction; #197 round 4);
  - the isolation probe and the run script;
  - the Ollama client (`OllamaChatClient`), `to_openai_chat`,
    `ollama_buckets`, the unmetered `ollama` ledger provider and the
    `OLLAMA_BASE_URL` guard latch (owner ruling 9), all smoke-run there;
  - reading Sonnet 5.5's exact token limits from the Models API in the
    smoke run, then correcting the matrix if they differ;
  - the `mcp-server-fetch` pin (2026.10.10 is the newest that is at least
    14 days old on 2026-10-31; re-check at run time) and its extraction
    path record;
  - the air-gapped runbook and host checks;
  - the full-run cost and time gate from the pilot.
- **PR 5:**
  - the criteria file, the probes, the judge panel and the reference
    grader;
  - batch units, with `split_batch` (groups contiguous items while each
    group's sum stays at or under `min(margin, remaining)`), landing with
    its caller;
  - `scoring.py` provenance;
  - post-hoc token counting from `canonical_messages` with `system` and
    `tools`;
  - grounding through `benchmarks/urls.py`;
  - report sections: per-repeat cell ids regrouped by `tool_model_key`,
    and the `output_cap_stops` share per family (the 1% pilot rule).
- **Round 2:** the Streamable HTTP transport in the bridge (with
  `httpx2.AsyncClient` opened through `async with`) and the competitor
  systems.
