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
  `benchmarks/agentic/` package. A host orchestrator holds the ledger and
  never a key. It spawns one worker process per (model, system) and talks
  to it over JSON lines on stdin/stdout. Each worker runs the tool loop
  with one model client and one MCP bridge. PR 4 only swaps the
  local-process launcher for a container launcher.
- The run writes the existing artifact layout, so `score` and `report`
  read it unchanged. The five fixed-flow adapters and the provider stubs
  are deleted at the end.

**Tech Stack:**
- Python ≥ 3.12 (`asyncio.timeout`, `Timeout.reschedule`, `Timeout.expired`)
- `mcp` 2.3.0 (`MCPServer`, `ClientSession`, `stdio_client`, `MCPError`)
- Dev group, new: `anthropic` 1.15.0, `openai` 3.32.0, `google-genai` 2.31.0.
  The first two use `httpx2`; `google-genai` uses `httpx`.
- `decimal.Decimal` for money; PyYAML for config files
- pytest with `asyncio_mode = "auto"`; Ruff (line length 100); Pyright (standard)

**Spec:** `docs/benchmarks/AGENTIC-BENCHMARK-DESIGN.md` (merged in #161),
§§2, 5–8, 10–15; issue #177 (retry settings and policy); the "Carried
Into The New Benchmark Harness" list in
`docs/plans/2026-10-30-audit-fixes-roadmap.md`. Executors read the design
in full before Task 1.

## Global Constraints

- **Order:** PR A merges first. PR B starts only after #173 (benchmark integrity) and PR A are on `main`; it builds on #173's `cell_failure_from`, `guard.redact_api_keys`, `MalformedApiKeyError`, `benchmarks/urls.py` and count model `claude-sonnet-5-5`. Both #173 and PR A append to `PUBLIC-BENCHMARK-METHODOLOGY.md`; whichever lands second rebases.
- **No paid call in CI or tests.** Production code builds clients only through `make_client` (Task 7), which calls `require_live_environment` (`BENCHMARK_LIVE_PROVIDERS_ENABLED` plus the provider's key) for every live API. Tests use `httpx2.MockTransport` / `httpx.MockTransport` or the scripted client. No test reaches a network host.
- **Keys:** `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`. The host orchestrator refuses to start when any of them is set in its environment (design §7.3). A worker holds at most one paid key. PR B's local launcher passes none, so no paid call is possible before PR 4's container launcher exists.
- **Dependencies:** `anthropic==1.15.0` (uploaded 2026-10-15), `openai==3.32.0` (2026-10-16), `google-genai==2.31.0` (2026-10-14), exact pins in the `dev` group. All are at least 14 days old on 2026-10-31 (checked against PyPI). New transitive packages younger than 14 days are locked one release back: `jiter==0.17.0` (2026-09-12) instead of 0.18.0, and `pyasn1==0.6.4` (2026-07-09) instead of 0.6.5. No LiteLLM, no Inspect AI, nothing else.
- **Owner approval:** `pyproject.toml`, `uv.lock` and dependency changes are forbidden territory for autonomous agents (`AGENT-EXECUTION-PIPELINE.md` §2). Design §13 is the owner's approval for exactly the three SDKs above in the `dev` group, and nothing beyond them. No workflow change: CI's `uv sync --locked --dev` already installs the group.
- **Budget (§12):** caps Anthropic $180, OpenAI $65, Google $180; margins $20, $10, $20; budget $200, $75, $200.
- **Loop caps (§6):** 8 model calls, 12 tool calls, 30 s per tool call, 300 s per trajectory (900 s air-gapped), 100,000 UTF-8 bytes per tool result, k = 3, `tool_choice` auto, OpenAI `strict: false`.
- **Frozen inputs:** the corpus (`c52c3f2`, PR #130) does not change. Nothing under `src/` changes; the tool surface stays as is.
- **Gate** (`AGENTS.md`), run fresh before each PR, and every pytest run with a throwaway cache so nothing touches `~/Library/Caches/mcp-python-docs`:
  - `uv run --locked ruff check src/ tests/ benchmarks/ ops/ .github/scripts/`
  - `uv run --locked pyright src/ benchmarks/`
  - `ISO=$(mktemp -d); uv run --locked env XDG_CACHE_HOME="$ISO" pytest --tb=short -q`
- **TDD** (superpowers:test-driven-development): every new behaviour has a failing test first.
- **Existing tests:** none is weakened. The only deletions are the ones design §11.1 orders, listed in Task 11; the PR B body repeats that list.
- **Supervisor review:** PR B changes `pyproject.toml`, deletes tests and passes 500 lines. Label it `supervisor-review`.
- **Commits and PRs:** conventional prefixes; every commit ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. PR bodies follow `.github/PULL_REQUEST_TEMPLATE/agent.md` and end with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`. Implementers never self-verify and never merge.

## Review Focus

1. **The model calls a tool that does not exist, or passes arguments that fail the schema or are not valid JSON.** The model gets an `is_error` tool result and the loop continues. It must not end as a transport crash. Tests: Task 4 `test_bridge_returns_an_unknown_tool_as_an_error_result`, `test_bridge_returns_invalid_arguments_as_an_error_result`; Task 8 `test_unparseable_tool_arguments_are_returned_to_the_model`.
2. **A tool result straddles the 100,000-byte cap with a multi-byte character, or has no text block.** The cut lands on a character boundary and carries the marker. A non-text block becomes a visible placeholder, never an empty string. Tests: Task 4 `test_truncate_utf8_cuts_on_a_character_boundary_and_marks_it`, `test_canonical_text_joins_text_blocks_and_marks_other_blocks`.
3. **A worker dies while its provider unit is in flight** (OOM, killed container, crash). The host books that unit at twice its bound and frees the provider's slot, so the other workers of that provider continue. Test: Task 9 `test_host_settles_a_lost_workers_unit_at_twice_its_bound_and_frees_the_provider`.
4. **The ledger directory is reused after a crash between reserve and settle, or by two runs at once.** Reopening books the unsettled unit at twice its bound, and a second concurrent open is refused. Tests: Task 3 `test_reopening_books_an_unsettled_unit_at_twice_its_bound`, `test_a_second_open_on_the_same_directory_is_refused`.
5. **Gemini returns function calls without ids, or two calls to the same function in one turn.** Harness-assigned ids keep each result paired with its call, by position. Test: Task 7 `test_gemini_pairs_two_calls_of_the_same_function_by_position`.

---

# PR A — Methodology amendment and model matrix (design §12 step 2)

Branch `docs/agentic-methodology-matrix` from `origin/main`. Refs #63, #177.

### Task 1: Model matrix per design §7.1

**Files:**
- Modify: `benchmarks/model_matrix.py` (`ModelFamily`, `_load_model_families`)
- Modify: `docs/benchmarks/model-matrix.yml` (rewrite)
- Test: `tests/benchmarks/test_model_matrix.py` (add tests only; reuse its `_family` and file-writing helpers)

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
    for family in matrix.model_families:
        assert "client_wrapper" not in family.raw
        if family.arm == "hosted":
            assert family.max_output_tokens == 16384
            assert family.max_input_tokens and family.max_input_tokens > 0
            assert family.output_cap_includes_reasoning is True
            assert family.framing_allowance_tokens == 256

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
```

- [ ] **Step 2: Run them and watch them fail**

Run: `ISO=$(mktemp -d); uv run --locked env XDG_CACHE_HOME="$ISO" pytest tests/benchmarks/test_model_matrix.py -q`
Expected: the new tests FAIL (unknown attributes, old matrix rows).

- [ ] **Step 3: Extend `_load_model_families`**

Validate each new field only when present: `api` in `KNOWN_APIS`, `arm` in
`KNOWN_ARMS`, token fields positive `int` (not `bool`),
`output_cap_includes_reasoning` a `bool`, `framing_allowance_tokens ≥ 256`.
An entry with `arm: hosted` must set `max_input_tokens`,
`max_output_tokens`, `output_cap_includes_reasoning` and
`framing_allowance_tokens`. The family `id` must match
`benchmarks.runner._SAFE_ID`. Leave `REQUIRED_PROVIDERS` unchanged.

- [ ] **Step 4: Rewrite `docs/benchmarks/model-matrix.yml`**

Keep `schema_version: 1` and `methodology_token_label`. Rewrite the header
comment to cite design §7.1, and drop the issue #73 stub and mock-adapter
text. Four entries, ids as in the test. Each entry has:
- `client`: `anthropic` (Anthropic) or `openai` (OpenAI and Ollama), or
  `google-genai` (Google);
- `token_count_method: claude_normalized_payload`;
- `provider_native_token_field`: `usage` (Anthropic, OpenAI, Ollama) or
  `usage_metadata` (Google);
- `latency_scope: request_dispatch_to_final_answer`;
- `headline_eligible: false`, with the existing decision-5.17 reason;
- `settings`, a string with the §7.1 Settings text verbatim.

Hosted entries also set:
- `max_output_tokens: 16384` and `framing_allowance_tokens: 256`;
- `max_input_tokens` from each provider's model card: 1,000,000 for
  Sonnet 5.5, 1,048,576 for Gemini 3.8 Flash, and the published limit for
  GPT-6.1 Sol;
- `output_cap_includes_reasoning: true`;
- a `limits_source_url` for each value.

Check each provider's docs that its output cap includes thinking or
reasoning tokens. If Gemini's `max_output_tokens` does not, stop and
escalate (Open question 2). The `gemma4-12b` entry has
`arm: air-gapped`, no token limits, and its settings name
`OLLAMA_CONTEXT_LENGTH=32768`.

- [ ] **Step 5: Run the whole benchmark suite**

Run: `ISO=$(mktemp -d); uv run --locked env XDG_CACHE_HOME="$ISO" pytest tests/benchmarks -q`
Expected: PASS. `test_real_model_matrix_file_loads_and_covers_required_providers` and the report tests still pass on the new file.

- [ ] **Step 6: Commit**

```bash
git add benchmarks/model_matrix.py docs/benchmarks/model-matrix.yml tests/benchmarks/test_model_matrix.py
git commit -m "feat(benchmarks): rewrite the model matrix for the agentic design"
```

### Task 2: Methodology amendment, smoke-run cost row, PR A

**Files:**
- Modify: `docs/benchmarks/PUBLIC-BENCHMARK-METHODOLOGY.md` (append one section)
- Modify: `docs/benchmarks/AGENTIC-BENCHMARK-DESIGN.md` (§12 cost table only)

**Interfaces:** none (docs).

- [ ] **Step 1: Append `## Amendment 2026-10-29: Agentic Mode`**

The title is the one design §11.2 gives; place the section after any
amendment already in the file. In the file's style (short sentences, one
idea each), it states:
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
  - the price snapshot;
  - the system prompt and tool-schema hashes;
  - the index sha256 and the order seed.

- [ ] **Step 2: Add the smoke run to the §12 table (#177 item 3)**

Insert as the first data row:
`| Smoke run: every (provider, system) pair, before the pilot (section 14) | — | ~$1 |`

- [ ] **Step 3: Verify**

Run: `grep -c "^## Amendment 2026-10-29: Agentic Mode" docs/benchmarks/PUBLIC-BENCHMARK-METHODOLOGY.md; grep -c "Smoke run" docs/benchmarks/AGENTIC-BENCHMARK-DESIGN.md`
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
- **Body:** Refs #63 and #177 (item 3), plus the gate output.

---

# PR B — Agentic harness with mocked end-to-end CI (design §12 step 3)

Branch `feat/agentic-harness` from `origin/main` after #173 and PR A merge.
Closes #177 (items 1 and 2; item 3 landed in PR A). Refs #63.

Package layout (design §15 leaves it to the builder):

| File | Responsibility |
|---|---|
| `benchmarks/agentic/types.py` | Neutral value types shared by every module |
| `benchmarks/agentic/usage.py` | §8 usage buckets per provider |
| `benchmarks/agentic/ledger.py` | Price snapshot, bound pricing, persistent ledger, one-in-flight service |
| `benchmarks/agentic/bridge.py` | Systems manifest, MCP stdio bridge, canonical results, truncation |
| `benchmarks/agentic/schemas.py` | MCP tool → provider tool conversion |
| `benchmarks/agentic/clients.py` | SDK construction, provider calls, error classification, scripted client, factory |
| `benchmarks/agentic/prompt.py` | The frozen system prompt |
| `benchmarks/agentic/loop.py` | One trajectory: caps, retries, bounds, records |
| `benchmarks/agentic/protocol.py` | JSON-lines messages, key scans |
| `benchmarks/agentic/worker.py` | Worker process: one (model, system) job |
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
def ollama_buckets(usage: Mapping[str, Any]) -> Buckets        # Chat Completions usage

# ledger.py
PROVIDER_CAPS: dict[str, Decimal]      # anthropic 180, openai 65, google 180
PROVIDER_MARGINS: dict[str, Decimal]   # anthropic 20, openai 10, google 20
UNMETERED_PROVIDERS = frozenset({"ollama"})
Outcome = Literal["ok", "rejected", "rate_limited", "server", "timeout", "connection",
                  "cut", "no_usage", "worker_lost", "unsettled_at_load"]

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

def load_price_snapshot(path: Path) -> dict[str, ModelPrice]          # keyed by model_id
def actual_cost(price: ModelPrice, buckets: Buckets) -> Decimal
def bound_cost(price: ModelPrice, *, input_bound: int, output_bound: int) -> Decimal
def clamp_input_bound(bound: int, max_input_tokens: int) -> int
def booked_cost(outcome: Outcome, *, bound: Decimal, actual: Decimal | None) -> Decimal
def split_batch(item_bounds: Sequence[Decimal], *, margin: Decimal, remaining: Decimal) -> list[list[int]]

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
                 families: Mapping[str, ModelFamily]) -> None    # families keyed by model_id
    async def reserve(self, *, provider: str, model_id: str, input_bound: int,
                      output_bound: int, kind: str = "model_call") -> str | None
    async def settle(self, unit_id: str, *, outcome: Outcome, buckets: Buckets | None) -> None
```

Rules the tests pin (design §8, §12):
- **Buckets.** Anthropic: `input_tokens`, `cache_creation_input_tokens`,
  `cache_read_input_tokens`, `output_tokens`. OpenAI: uncached is
  `input_tokens − cached_tokens − cache_write_tokens`. Gemini: uncached is
  `prompt − cached_content + tool_use_prompt`; output is
  `candidates + thoughts`. A `None` count is 0. A negative uncached count
  raises `BenchmarkValidationError`.
- **Actual cost.** Pick the tier from `buckets.total_input`: the first tier
  whose `up_to_input_tokens` is at least that, or the last tier. Each
  bucket is priced at that tier's own rate, and output at the tier's
  output rate.
- **Bound cost.** Input is priced at `cache_write` for `openai` and
  `input` for every other provider. The tier comes from `input_bound`, for
  input and output both.
- **Booked cost.** `ok` books the actual cost. `rejected` and
  `rate_limited` book 0. Every other outcome books 2 × bound.
- **Reserve.**
  - Refused, and the provider stopped with `"cap"`, when `spent + bound`
    is over the cap. A stop is sticky.
  - Raises `RuntimeError` when a unit is already in flight for that
    provider.
  - Raises `BenchmarkValidationError` for a provider in neither the caps
    nor `UNMETERED_PROVIDERS`.
  - An unmetered provider is never refused and books 0.
- **Settle.** An `ok` whose actual cost is above its bound books the
  actual cost and stops the provider with `"overrun"`. Only
  `acknowledge_overrun` lifts that stop.
- **Files.** Each provider has `<provider>.jsonl` with `reserve`,
  `settle`, `stop` and `resume` events. Money is written as decimal
  strings.
- **Open.**
  - `open` books every reserve that has no settle with
    `outcome="unsettled_at_load"`, at 2 × bound, and appends that event.
  - `open` creates `<ledger_dir>/.lock` with `O_CREAT | O_EXCL` and
    writes its pid into it. If the file exists, `open` raises
    `BenchmarkValidationError` naming the lock file. `close()` removes it.
- **Split.** `split_batch` greedily groups contiguous items while each
  group's sum stays at or under `min(margin, remaining)`. An item above
  that limit raises `BenchmarkValidationError`.
- **LedgerService.**
  - Holds one `asyncio.Lock` per provider, from `reserve` until that
    unit's `settle`.
  - Clamps `input_bound` to the family's `max_input_tokens`, prices the
    bound, and passes `input_bound_exceeded = buckets.total_input >
    input_bound` to `settle`.

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

def test_ollama_buckets() -> None:
    assert ollama_buckets({"prompt_tokens": 70, "completion_tokens": 9}) == Buckets(70, 0, 0, 9, 0)

def test_buckets_refuse_a_negative_uncached_input() -> None   # openai cached 200 > input 100

def test_committed_snapshot_has_the_design_prices() -> None:
    prices = load_price_snapshot(PRICE_SNAPSHOT_PATH)
    def base(m): return prices[m].tiers[0].rates
    assert (base("claude-sonnet-5-5").input, base("claude-sonnet-5-5").output) == (Decimal("2"), Decimal("10"))
    assert (base("gpt-6.1-sol").input, base("gpt-6.1-sol").output) == (Decimal("2"), Decimal("10"))
    assert base("gpt-6.1-sol").cache_write == Decimal("2.5")
    assert (base("gemini-3.8-flash").input, base("gemini-3.8-flash").output) == (Decimal("0.75"), Decimal("3.75"))
    assert base("gemma4:12b") == Rates(Decimal(0), Decimal(0), Decimal(0), Decimal(0))

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
    ("server", "4"), ("timeout", "4"), ("connection", "4"), ("cut", "4"), ("no_usage", "4"),
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
def test_unmetered_provider_needs_no_cap(tmp_path) -> None                   # ollama, spent 0
def test_unknown_paid_provider_is_refused(tmp_path) -> None                  # "mistral"

def test_split_batch_keeps_every_part_under_margin_and_remaining_cap() -> None:
    assert split_batch([Decimal(4)] * 7, margin=Decimal(10), remaining=Decimal(9)) == [[0, 1], [2, 3], [4, 5], [6]]
def test_split_batch_refuses_an_item_bigger_than_the_limit() -> None

async def test_service_allows_one_unit_in_flight_per_provider_and_runs_providers_in_parallel(tmp_path) -> None:
    # second anthropic reserve is still pending until the first settles; an openai reserve
    # completes meanwhile (asyncio.wait_for with a short timeout on each)

def test_committed_matrix_largest_single_call_bound_is_below_every_margin() -> None:
    matrix, prices = load_model_matrix(MODEL_MATRIX_PATH), load_price_snapshot(PRICE_SNAPSHOT_PATH)
    for f in (f for f in matrix.model_families if f.arm == "hosted"):
        cost = bound_cost(prices[f.model_id], input_bound=f.max_input_tokens, output_bound=f.max_output_tokens)
        assert cost < PROVIDER_MARGINS[f.provider]

def test_caps_and_margins_add_up_to_the_budget() -> None:
    assert {p: PROVIDER_CAPS[p] + PROVIDER_MARGINS[p] for p in PROVIDER_CAPS} == \
        {"anthropic": Decimal(200), "openai": Decimal(75), "google": Decimal(200)}
```

`_flat` and `_tiered` are test-local `ModelPrice` builders.

- [ ] **Step 2: Run them and watch them fail**

Run: `ISO=$(mktemp -d); uv run --locked env XDG_CACHE_HOME="$ISO" pytest tests/benchmarks/test_agentic_ledger.py -q`
Expected: FAIL with `ModuleNotFoundError: benchmarks.agentic`.

- [ ] **Step 3: Write `docs/benchmarks/price-snapshot.yml`**

Top-level keys: `schema_version: 1`, `captured_on`, `currency: USD`,
`unit: per_million_tokens`, and `models`. Each model entry has
`model_id`, `provider`, `source_url` and `tiers`. Each tier has
`up_to_input_tokens` and rates `input`, `cache_write`, `cached_input` and
`output`, written as strings.

Copy the rates for the three hosted models from each provider's pricing
page on the day, including any long-context tier and the cached and
cache-write rates. Record the page URL and the date. `gemma4:12b` gets one
tier of zeros. If a page's base rate differs from the design's ($2/$10,
$2/$10, $0.75/$3.75), stop and report: the §12 estimates assumed those
rates.

- [ ] **Step 4: Implement `types.py`, `usage.py`, `ledger.py` to the rules above**

`load_price_snapshot` refuses:
- tiers that are not ascending;
- a last tier with a limit;
- a negative rate;
- an unknown provider;
- a duplicate `model_id`.

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
  else (the SDK adds its default set).
- **`tools/list`.** Follow `next_cursor` until it is empty.
- **`call`.**
  - An `MCPError` becomes `ToolOutcome(is_error=True)` with the error
    text.
  - A result with `is_error` keeps it.
  - A dead transport raises `BridgeCrash`, mapped from #173's
    `cell_failure_from` leaves.

- [ ] **Step 1: Write the fake server**

The fixture is an `MCPServer("fake-fetch")` script over stdio. Its tool is
`fetch(url: str, max_length: int = 5000, start_index: int = 0, raw: bool = False) -> str`,
which returns `f"Contents of {url}: the json module encodes and decodes JSON."`.
With `--edge` it also registers:
- `env_names() -> list[str]`, the sorted names in `os.environ`;
- `die() -> str`, which calls `os._exit(1)`.

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
def to_openai_chat(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]      # type function, function{name, description, parameters, strict False}
def to_gemini(tools: Sequence[ToolSpec]) -> tuple[list[dict[str, Any]], list[str]]  # declarations, dropped paths
def schemas_sha256(converted: Any) -> str   # sha256 of json.dumps(converted, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
```

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
uv lock --upgrade-package "jiter==0.17.0" --upgrade-package "pyasn1==0.6.4"
uv lock --check && uv sync --locked --dev
```

Then compare `uv.lock` with the base commit. The only added packages may
be the three SDKs and their transitive dependencies, and no existing
package may change version. Check that no added package was uploaded
after 2026-10-17; pyasn1 and jiter are already handled above. Keep the
list of added packages and their dates for the PR body. If a new package
is younger than 14 days, lock its previous release the same way and
record it.

- [ ] **Step 2: Write the failing tests**

```python
TOOLS = [ToolSpec(t["name"], t["description"], t["inputSchema"])
         for t in json.loads((REPO_ROOT / "tests/fixtures/tools-list.json").read_text())]

def test_anthropic_tools_keep_name_description_and_input_schema_verbatim() -> None:
    assert to_anthropic(TOOLS)[0] == {"name": TOOLS[0].name, "description": TOOLS[0].description,
                                      "input_schema": TOOLS[0].input_schema}

def test_openai_tools_set_strict_false_explicitly() -> None:
    assert all(t["type"] == "function" and t["strict"] is False for t in to_openai_responses(TOOLS))
    assert all(t["function"]["strict"] is False for t in to_openai_chat(TOOLS))

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

### Task 6: Anthropic, OpenAI Responses and Ollama clients

**Files:**
- Create: `benchmarks/agentic/clients.py`
- Modify: `benchmarks/agentic/types.py` (turn types)
- Modify: `benchmarks/adapters/guard.py` (add the `ollama` latch)
- Test: `tests/benchmarks/test_agentic_clients.py`

**Interfaces:**
- Consumes: `Buckets`, `anthropic_buckets`, `openai_buckets`, `ollama_buckets` (Task 3); `ToolSpec` (Task 4); `to_anthropic`, `to_openai_responses`, `to_openai_chat` (Task 5).
- Produces:

```python
# types.py
@dataclass(frozen=True)
class ToolCall:
    id: str                      # provider id, or "call_<n>" (1-based within the turn) when missing
    name: str
    arguments: dict[str, Any]
    raw_arguments: str | None = None   # set only when the arguments were not valid JSON
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
    stop_reason: str | None
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
    async def call(self, conv: Conversation) -> ModelTurn   # raises ProviderCallError

# clients.py
def parse_retry_after(headers: Mapping[str, str]) -> float | None   # retry-after-ms, retry-after seconds or HTTP date
def anthropic_sdk(api_key: str, timeout_s: float, *, http_client: httpx2.AsyncClient | None = None) -> AsyncAnthropic
def openai_sdk(api_key: str, timeout_s: float, *, base_url: str | None = None,
               http_client: httpx2.AsyncClient | None = None) -> AsyncOpenAI
class AnthropicClient:      # ModelClient; __init__(self, sdk, model_id, max_output_tokens)
class OpenAIResponsesClient:  # ModelClient; __init__(self, sdk, model_id, max_output_tokens)
class OllamaChatClient:     # ModelClient; __init__(self, sdk, model_id)
```

Request rules:
- **Anthropic.** Send `model`, `max_tokens`, `system`, `tools`
  (`to_anthropic`), `tool_choice={"type": "auto"}` and `messages`. No
  sampling parameters. Replay the response `content` blocks unchanged,
  thinking blocks and their signatures included. Each tool result is a
  `tool_result` block with `is_error`.
- **OpenAI Responses.**
  - Send `model`, `instructions` (the system prompt), `input`, `tools`,
    `tool_choice="auto"`, `max_output_tokens`, `store=False` and
    `include=["reasoning.encrypted_content"]`.
  - Replay every output item unchanged. A tool result is
    `{"type": "function_call_output", "call_id": ..., "output": text}`.
  - `arguments` is parsed with `json.loads`. On failure the call gets
    `arguments={}` and `raw_arguments` set.
- **Ollama (Chat Completions).** Send `model`, `messages` (system, user,
  assistant with `tool_calls`, and `tool` messages), `tools`
  (`to_openai_chat`) and `tool_choice="auto"`. Send no `max_tokens` and
  no sampling parameters (§7.1: Ollama defaults).
- **Errors.**
  - 429 is `rate_limited`, with `parse_retry_after` applied to the
    response headers.
  - Status 500 and above is `server`. Any other 4xx is `rejected`.
  - `APITimeoutError` is `timeout`; `APIConnectionError` is `connection`.
- **SDK construction.** `max_retries=0` and `timeout=timeout_s` (#177
  item 1).
- **Guard.** Add `"ollama": "OLLAMA_BASE_URL"` to `PROVIDER_API_KEY_ENV`,
  with a one-line comment: it is not a secret; it is the per-target
  opt-in, like `LIVE_COMPETITORS_ENV`.

- [ ] **Step 1: Write the failing tests**

All HTTP goes through `httpx2.AsyncClient(transport=httpx2.MockTransport(handler))`.
The handler records each request and returns a canned JSON body written
inline in the test.

```python
def test_sdk_clients_have_their_retries_off() -> None:
    assert anthropic_sdk("test-key", 30).max_retries == 0
    assert openai_sdk("test-key", 30).max_retries == 0

async def test_anthropic_request_has_auto_tool_choice_and_no_sampling_parameters() -> None:
    body = json.loads(seen[0].content)
    assert body["tool_choice"] == {"type": "auto"} and body["max_tokens"] == 16384
    assert not {"temperature", "top_p", "top_k"} & set(body)
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
    assert body["include"] == ["reasoning.encrypted_content"] and body["max_output_tokens"] == 16384
    assert all(t["strict"] is False for t in body["tools"]) and "temperature" not in body
    assert turn.buckets == Buckets(500, 300, 200, 50, 40)

async def test_openai_replays_output_items_and_sends_function_call_output() -> None
async def test_openai_unparseable_arguments_are_kept_raw() -> None:
    assert turn.tool_calls[0].arguments == {} and turn.tool_calls[0].raw_arguments == "{not json"

async def test_ollama_chat_request_sends_auto_tool_choice_and_no_max_tokens() -> None:
    assert body["tool_choice"] == "auto" and "max_tokens" not in body and "temperature" not in body
    assert turn.buckets == Buckets(70, 0, 0, 9, 0)

def test_parse_retry_after_accepts_an_http_date() -> None   # a date 30 s ahead -> 29 < value <= 30
def test_rendered_bytes_grows_with_each_entry() -> None
```

- [ ] **Step 2: Run them and watch them fail**

Run: `ISO=$(mktemp -d); uv run --locked env XDG_CACHE_HOME="$ISO" pytest tests/benchmarks/test_agentic_clients.py -q`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement the turn types, the three clients and the guard entry**

- [ ] **Step 4: Run the tests**

Expected: PASS. The existing guard tests in `tests/benchmarks/test_adapters.py` still pass.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/agentic/ benchmarks/adapters/guard.py tests/benchmarks/test_agentic_clients.py
git commit -m "feat(benchmarks): add Anthropic, OpenAI and Ollama agentic clients"
```

### Task 7: Gemini client, scripted client and the client factory

**Files:**
- Modify: `benchmarks/agentic/clients.py`
- Create: `tests/benchmarks/fixtures/agentic/script.yml`
- Test: `tests/benchmarks/test_agentic_clients.py` (append)

**Interfaces:**
- Consumes: Task 6 types; `gemini_buckets`, the bucket functions (Task 3); `to_gemini` (Task 5); `require_live_environment`, `PROVIDER_API_KEY_ENV` (guard); `ModelFamily` (Task 1).
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
```

Gemini:
- Call `sdk.aio.models.generate_content` with
  `GenerateContentConfig(system_instruction, tools=[Tool(function_declarations=...)],
  tool_config=ToolConfig(function_calling_config=FunctionCallingConfig(mode="AUTO")),
  automatic_function_calling=AutomaticFunctionCallingConfig(disable=True),
  max_output_tokens)`.
- Replay `candidate.content` unchanged, thought signatures included.
- A function call without an `id` gets `call_<n>` by position in the
  turn. A result is `Part.from_function_response(name=..., response=...)`,
  where `response` is `{"output": text}` or `{"error": text}`. It carries
  the provider id when there was one.
- `reported_model` is `response.model_version`.
- Errors: `errors.ClientError` with code 429 is `rate_limited`, any other
  `ClientError` is `rejected`, and `errors.ServerError` is `server`.
  `httpx.TimeoutException` is `timeout`; `httpx.TransportError` is
  `connection`.

Scripted script (`family.raw["script"]`, a YAML path relative to the
matrix file):
- The file is `steps:`, a list. Each step is one of three kinds:
  - `{tools: [{name, arguments}], usage}`: a turn that calls those
    tools;
  - `{text, usage}`: a final answer;
  - `{error: {kind, status, retry_after_s}}`.
- `usage` is the provider-native snake_case dict, and buckets come from
  the matching Task 3 function. `"{prompt}"` in a string argument becomes
  the first user message.
- The client counts its calls per `Conversation` object (by `id(conv)`).
  The n-th call plays the n-th step that applies.
- A `tools` step applies only when every tool it names is in
  `conv.tools`, so `no-docs` skips straight to the answer. An optional
  `delay_s` on any step sleeps first.

Factory:
- `api == "scripted"`: return a `ScriptedClient`. No guard and no key.
- Otherwise:
  1. Call `require_live_environment(family.provider)`.
  2. If any `PAID_KEY_ENVS` variable other than this provider's is set,
     raise `BenchmarkValidationError` naming it.
  3. Build the SDK for `family.api` with the key from
     `PROVIDER_API_KEY_ENV`. Ollama uses `OLLAMA_BASE_URL` and the
     placeholder key `"ollama"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_gemini_client_makes_one_attempt_and_aiohttp_is_absent() -> None:
    sdk = gemini_sdk("test-key", 30)
    assert sdk._api_client._http_options.retry_options.attempts == 1
    # google-genai 2.31.0's aiohttp transport retries a connection error once whatever the
    # options say (_api_client.py); keep that transport out of the environment
    assert importlib.util.find_spec("aiohttp") is None

async def test_gemini_request_uses_auto_mode_and_disables_automatic_calling() -> None:
    assert body["toolConfig"]["functionCallingConfig"]["mode"] == "AUTO"
    assert body["generationConfig"]["maxOutputTokens"] == 16384
    assert turn.buckets == Buckets(950, 0, 100, 50, 20) and turn.reported_model == "gemini-3.8-flash-001"

async def test_gemini_pairs_two_calls_of_the_same_function_by_position() -> None:
    # response parts: two functionCall "search_docs" without ids
    assert [c.id for c in turn.tool_calls] == ["call_1", "call_2"]
    # the next request ends with two functionResponse parts, in that order, each named
    # "search_docs" with response {"output": <its text>}

@pytest.mark.parametrize("status,kind", [(429, "rate_limited"), (400, "rejected"), (500, "server")])
async def test_gemini_status_errors_are_classified(status, kind) -> None

def test_make_client_refuses_a_live_api_without_the_guard(monkeypatch) -> None   # LiveProviderDisabledError
def test_make_client_refuses_a_second_paid_key(monkeypatch) -> None:
    # flag on, ANTHROPIC_API_KEY and OPENAI_API_KEY set -> make_client(anthropic family) raises,
    # message names OPENAI_API_KEY and holds neither value
def test_make_client_builds_a_scripted_client_without_any_key(monkeypatch) -> None
async def test_scripted_client_skips_tool_steps_for_tools_it_was_not_given() -> None
async def test_scripted_client_raises_a_scripted_provider_error() -> None:
    # step {error: {kind: rate_limited, status: 429, retry_after_s: 0}} -> ProviderCallError, then the next step
```

- [ ] **Step 2: Run them and watch them fail**

Expected: FAIL (`gemini_sdk`, `ScriptedClient`, `make_client` undefined).

- [ ] **Step 3: Implement, and write `fixtures/agentic/script.yml`**

The script has three steps:
1. `tools: [{name: search_docs, arguments: {query: json}}]`;
2. `tools: [{name: fetch, arguments: {url: "https://docs.python.org/3/library/json.html"}}]`;
3. `text: "Scripted answer."`.

Each step has `usage` in the shape of the provider that uses the script.
Write one script per provider (`script-anthropic.yml`,
`script-openai.yml`, `script-google.yml`) if one shape cannot serve all
three.

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
- Consumes: `ModelClient`, `Conversation`, turn types, `ProviderCallError` (Task 6); `ScriptedClient` (Task 7, used in tests); `ToolBridge`, `ToolOutcome`, `BridgeCrash`, `truncate_utf8` (Task 4); `Buckets`, `Outcome`, `LedgerService` (Task 3, satisfies `LedgerPort`); `ModelFamily` (Task 1).
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
    model_call_timeout_s: float = 120.0
HOSTED_CAPS = Caps()
AIR_GAPPED_CAPS = Caps(trajectory_s=900.0, model_call_timeout_s=600.0)
MAX_RETRIES = 4
MAX_RETRY_AFTER_S = 120.0
RETRYABLE = frozenset({"rate_limited", "server", "timeout", "connection"})

class LedgerPort(Protocol):
    async def reserve(self, *, provider: str, model_id: str, input_bound: int,
                      output_bound: int, kind: str = "model_call") -> str | None: ...
    async def settle(self, unit_id: str, *, outcome: Outcome, buckets: Buckets | None) -> None: ...
    # Outcome is imported from ledger.py, so LedgerService satisfies this protocol exactly

class TrajectoryDiscarded(Exception):
    def __init__(self, reason: Literal["budget_stop", "retries_exhausted"],
                 record: dict[str, Any]) -> None: ...   # record: the partial record, for audit only

def first_input_bound(client: ModelClient, conv: Conversation, framing: int) -> int
    # client.rendered_bytes(conv, 0) + framing * 2   (system/tools block + user message)
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
- **Clock.** Wrap the whole trajectory in `asyncio.timeout(caps.trajectory_s)`.
  Classify a fire with `cm.expired()`. After a failed attempt, call
  `cm.reschedule(cm.when() + attempt_s + wait_s)`, so failed attempts and
  retry waits do not use the trajectory clock.
- **Each model call.**
  1. Compute the input bound: `first_input_bound` for call 1, else
     `next_input_bound` over the entries added since the last call.
  2. The output bound is `family.max_output_tokens or 0`, and framing is
     `family.framing_allowance_tokens or 256`.
  3. For each attempt: `reserve`, where `None` raises
     `TrajectoryDiscarded("budget_stop")`; then `client.call`; then
     `settle`. A success settles `"ok"` with its buckets, or
     `"no_usage"` when buckets are `None`. A `ProviderCallError` settles
     with its `kind`.
  4. A retryable kind waits `backoff_s` with the injected `sleep`. After
     `MAX_RETRIES` retries, or when `backoff_s` returns `None`, raise
     `TrajectoryDiscarded("retries_exhausted")`.
  5. `rejected` ends the trajectory as failed, with category
     `provider_rejected`.
  6. Retries never count toward `caps.model_calls`.
- **Tool calls.**
  - Run them in turn order.
  - A call with `raw_arguments` set is not sent. It returns `is_error`
    text `"invalid JSON arguments: <raw>"`.
  - Each call runs inside `asyncio.timeout(caps.tool_call_s)`.
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
  - A trajectory cut while a model call is in flight settles that unit
    `"cut"`.
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
  - `timing`: `{wall_ms, model_ms, tool_ms, retry_wait_ms,
    failed_attempt_ms, overhead_ms}`;
  - `started_at`, `completed_at`.

- [ ] **Step 1: Write the failing tests**

They run in-process with `ScriptedClient`, a test-local `FakeBridge` with
canned outcomes and delays, a `LedgerService` over a `tmp_path` ledger,
and a `sleep` that records its argument and returns at once.

```python
async def test_trajectory_calls_a_tool_then_answers_and_records_every_call() -> None:
    assert record["status"] == "succeeded" and record["answer"] == "Scripted answer."
    assert [c["tool"] for c in record["tool_calls"]] == ["search_docs"]
    assert record["model_calls"][1]["message_count"] == 3
    assert record["canonical_messages"][1]["content"][0]["type"] == "tool_use"
    assert record["canonical_messages"][2]["content"][0] == {"type": "tool_result",
        "tool_use_id": record["canonical_messages"][1]["content"][0]["id"],
        "content": FAKE_TEXT, "is_error": False}

async def test_ninth_model_call_is_a_cap_failure() -> None:
    assert record["error"]["category"] == "cap_model_calls" and len(record["model_calls"]) == 8
async def test_thirteenth_tool_call_is_a_cap_failure() -> None:
    assert record["error"]["category"] == "cap_tool_calls" and len(record["tool_calls"]) == 12
async def test_slow_tool_call_ends_the_trajectory() -> None          # Caps(tool_call_s=0.05)
async def test_trajectory_time_cap_books_the_cut_call_at_twice_its_bound() -> None:
    # Caps(trajectory_s=0.05), scripted step delay_s 1 -> "cap_trajectory_time"; ledger settle event
    # outcome "cut", booked == 2 * bound
async def test_result_over_the_cap_is_truncated_and_the_loop_continues() -> None:
    assert record["tool_calls"][0]["truncated"] is True and record["status"] == "succeeded"
async def test_unknown_tool_call_is_returned_to_the_model_and_the_loop_continues() -> None
async def test_unparseable_tool_arguments_are_returned_to_the_model() -> None:
    assert record["tool_calls"][0]["is_error"] is True and bridge.calls == []
async def test_rate_limit_is_retried_honouring_retry_after_and_not_counted_as_a_model_call() -> None:
    assert sleeps == [7.0] and len(record["model_calls"]) == 1
    assert [a["outcome"] for a in record["model_calls"][0]["attempts"]] == ["rate_limited", "ok"]
    assert record["timing"]["retry_wait_ms"] == 7000
async def test_server_error_is_booked_at_twice_the_bound_and_retried() -> None
async def test_retries_exhausted_discards_the_trajectory() -> None:
    # five 500s -> TrajectoryDiscarded("retries_exhausted"); sleeps == [2, 4, 8, 16]
async def test_rejected_request_is_a_scored_failure_booked_at_zero() -> None
async def test_budget_stop_discards_the_trajectory() -> None          # caps {"anthropic": Decimal("0")}
async def test_failed_attempts_do_not_consume_the_trajectory_clock() -> None:
    # Caps(trajectory_s=1.0): a step with delay_s 0.8 then error server, then delay_s 0.4 + text
    # -> succeeded (1.2 s of wall time, 0.4 s on the clock)
async def test_repeated_tool_arguments_are_marked_warm() -> None     # shared set, two trajectories
async def test_empty_final_text_is_an_empty_answer() -> None
async def test_bridge_crash_fails_the_trajectory_as_a_protocol_crash() -> None
async def test_no_docs_sends_no_tools() -> None
def test_input_bounds() -> None:
    assert next_input_bound(Buckets(100, 0, 20, 30), added_bytes=500, added_entries=2, framing=256) == 1162
def test_backoff() -> None:
    assert [backoff_s(r, None) for r in (1, 2, 3, 4)] == [2, 4, 8, 16]
    assert backoff_s(1, 7.0) == 7.0 and backoff_s(1, 121.0) is None
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
clock tests use wide margins, so a flaky result means a bug, not noise.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/agentic/prompt.py benchmarks/agentic/loop.py tests/benchmarks/test_agentic_loop.py
git commit -m "feat(benchmarks): add the agentic trajectory loop with caps and retries"
```

### Task 9: Worker and host over JSON lines

**Files:**
- Create: `benchmarks/agentic/protocol.py`, `benchmarks/agentic/worker.py`, `benchmarks/agentic/host.py`
- Test: `tests/benchmarks/test_agentic_host.py`

**Interfaces:**
- Consumes: `run_trajectory`, `TrajectoryDiscarded`, `Caps`, `LedgerPort` (Task 8); `make_client`, `PAID_KEY_ENVS` (Task 7); `open_bridge`, `SystemSpec` (Task 4); `to_*`, `schemas_sha256` (Task 5); `LedgerService` (Task 3).
- Produces:

```python
# protocol.py
KEY_SHAPED = re.compile(r"(?<![A-Za-z0-9])(?:sk-ant-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9_-]{20,}"
                        r"|AIza[0-9A-Za-z_-]{30,})")
class KeyLeakError(BenchmarkValidationError): ...
def encode(message: dict[str, Any]) -> bytes            # compact JSON + b"\n"
def decode(line: bytes) -> dict[str, Any]
def assert_no_key_shaped(text: str, *, where: str) -> None   # message names `where`, never the match
def contains_own_key(text: str, provider: str) -> bool

# worker.py
MAX_RERUNS = 2
async def run_worker(reader: asyncio.StreamReader,
                     write: Callable[[bytes], Awaitable[None]]) -> int
def main_worker() -> int     # `python -m benchmarks agentic-worker`

# host.py
@dataclass(frozen=True)
class WorkerJob:
    job_id: str                          # f"{family.id}--{system.id}"
    family: ModelFamily
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
                   log_dir: Path, cwd: Path) -> None
```

Protocol (one JSON object per line):
- **Host to worker.** The first line is
  `{"type": "job", "job_id", "family": <raw>, "system": <raw>,
  "trajectories", "caps"}`. Replies are `{"type": "grant", "unit_id"}`,
  `{"type": "deny"}` and `{"type": "ack"}`.
- **Worker to host:**
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

Worker rules:
- **Protocol channel.** Before anything else, keep `os.dup(1)` for
  protocol writes and point fd 1 at stderr, so a stray print cannot
  corrupt the channel.
- **Session.** Build the client once per job with
  `make_client(family, timeout_s=job.caps.model_call_timeout_s)`. Open one
  bridge per job (none for `no-docs`), and reopen it after a
  `BridgeCrash` or a tool timeout. Keep one `seen_tool_args` set per job.
- **Budget stop.** On `budget_stop`, send `discarded`, then `not_run` for
  every remaining trajectory.
- **Exhausted retries.** On `retries_exhausted`, send `discarded` and
  requeue the trajectory at the end, at most `MAX_RERUNS` times. After
  that, send `not_run` with that reason.
- **Own key.** Before each write, if `contains_own_key(line, provider)`,
  send `error` with reason `"own key found in output"` instead and exit
  with status 3.

Host rules:
- **Startup.** `refuse_host_keys(os.environ)` before anything else. Any
  family the launcher does not support is refused before any process
  starts.
- **Workers.** Spawn each with `asyncio.create_subprocess_exec(*argv,
  env=env, cwd=cwd, stdin=PIPE, stdout=PIPE, stderr=<log file>)`.
- **Key scans.** Run `assert_no_key_shaped` on every received line, and
  on every log file when its worker exits. On `KeyLeakError`, kill every
  worker and re-raise.
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

def test_worker_detects_its_own_key(monkeypatch) -> None    # contains_own_key, with redact-safe message

async def test_worker_runs_its_job_over_the_pipe_and_reports_done(tmp_path) -> None:
    # real subprocess via LocalLauncher; scripted family + no-docs system, two trajectories
    assert [m["type"] for m in received] == ["system_info", "trajectory", "trajectory", "done"]

async def test_host_settles_a_lost_workers_unit_at_twice_its_bound_and_frees_the_provider(tmp_path) -> None:
    # a test launcher runs a `python -c` fake worker that reads the job, sends reserve, reads
    # the grant and exits 1; a second job of the same provider (normal scripted worker) completes
    assert ledger.spent("anthropic") == 2 * first_bound + second_job_actual
    assert {(m["type"], m["reason"]) for m in lost_job_messages} == {("not_run", "worker_lost")}

async def test_host_aborts_the_run_when_a_worker_line_holds_a_key_shaped_string(tmp_path) -> None
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

- [ ] **Step 3: Implement `protocol.py`, `worker.py`, `host.py`, and add the `agentic-worker` subcommand to `benchmarks/__main__.py`**

The subcommand has no options and returns `main_worker()`.

- [ ] **Step 4: Run the tests**

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/agentic/ benchmarks/__main__.py tests/benchmarks/test_agentic_host.py
git commit -m "feat(benchmarks): run agentic jobs in keyless worker processes"
```

### Task 10: Run orchestration, artifacts, CLI and the mocked end-to-end test

**Files:**
- Create: `benchmarks/agentic/run.py`
- Modify: `benchmarks/__main__.py` (`agentic-run`)
- Create: `tests/benchmarks/fixtures/agentic/model-matrix.yml`, `tests/benchmarks/fixtures/agentic/prices.yml`
- Test: `tests/benchmarks/test_agentic_run.py`

**Interfaces:**
- Consumes: everything above; from `benchmarks.runner`: `_load_yaml_mapping`, `_load_questions`, `_write_json`, `_write_snapshot`, `_environment_metadata`, `_default_run_id`, `_utc_now`, `_scoring_record`, `BenchmarkCell`, `Competitor`, `Question`; `METHODOLOGY_TOKEN_LABEL`; `DEFAULT_COUNT_MODEL`.
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
def plan_jobs(questions: Sequence[Question], families: Sequence[ModelFamily],
              systems: Sequence[SystemSpec], *, repeats: int, seed: int) -> list[WorkerJob]
    # per job: [(q, r) for q in questions for r in 1..repeats], shuffled with
    # random.Random(f"{seed}/{family.id}/{system.id}"); caps AIR_GAPPED_CAPS when arm == "air-gapped"
def run_agentic(config: AgenticConfig, *, launcher: Launcher | None = None,
                caps: Mapping[str, Decimal] = PROVIDER_CAPS) -> dict[str, Any]
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
  - `output_cap_includes_reasoning: false` is refused with "set a
    reasoning budget first (design §12)".
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
  - `discarded` and `not_run`: lists of `{cell, corpus_id, reason}`.
  - `ledger`: per provider, `{spent_before, spent_after, cap, stopped}`.
  - `model_windows`: per family, `{first_started_at, last_completed_at}`.
- **CLI.**

```
python -m benchmarks agentic-run --corpus PATH --out DIR --ledger-dir DIR
    [--matrix docs/benchmarks/model-matrix.yml] [--manifest docs/benchmarks/agentic-manifest.yml]
    [--prices docs/benchmarks/price-snapshot.yml] [--models IDS] [--systems IDS] [--questions IDS]
    [--repeats 3] [--seed N] [--run-id ID] [--dry-run] [--acknowledge-overrun PROVIDER]...
```

IDS are comma-separated. It prints the summary JSON. Exit codes: 0 on
success, 2 on `BenchmarkValidationError`, 3 on `KeyLeakError`.

- [ ] **Step 1: Write the fixtures**

`model-matrix.yml` holds three scripted families: `scripted-anthropic`,
`scripted-openai` and `scripted-google`, with providers `anthropic`,
`openai` and `google`. Each is `arm: hosted` with model ids
`scripted-1/2/3`, the Task 7 script for its provider, `max_input_tokens:
200000`, `max_output_tokens: 1024`, `output_cap_includes_reasoning: true`
and `framing_allowance_tokens: 256`.

`prices.yml` prices `scripted-1/2/3` with flat round rates, so the test
can compute the expected spend by hand.

- [ ] **Step 2: Write the failing tests**

```python
def test_agentic_run_end_to_end_with_scripted_models(tmp_path, monkeypatch) -> None:
    for name in PAID_KEY_ENVS:
        monkeypatch.delenv(name, raising=False)
    env, cache_dir = _isolated_cache_env(tmp_path)          # tests.test_stdio_smoke
    _create_contentful_json_index(cache_dir)                # tests.test_mcp_get_docs_cache_smoke
    monkeypatch.setenv("XDG_CACHE_HOME", env["XDG_CACHE_HOME"]); monkeypatch.setenv("HOME", env["HOME"])
    manifest = _write_manifest(tmp_path)   # python-docs (committed entry), docs-fetch -> fake_fetch_server.py, no-docs
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

def test_plan_jobs_order_is_seeded_per_job() -> None:
    # equals random.Random(f"7/{family.id}/{system.id}").shuffle of the canonical order,
    # and two calls with seed 7 give identical jobs

def test_agentic_run_budget_stop_discards_and_lists_cells_not_run(tmp_path, monkeypatch) -> None:
    # same setup, caps={"anthropic": Decimal("0"), "openai": Decimal(65), "google": Decimal(180)}
    assert len(summary["discarded"]) + len(summary["not_run"]) == 12
    assert summary["succeeded_cells"] == 24
    assert not any((run / "scoring").glob("*--scripted-anthropic--*"))

def test_agentic_run_refuses_a_non_empty_out_dir(tmp_path) -> None
def test_agentic_run_dry_run_writes_the_plan_without_a_ledger(tmp_path) -> None
def test_cli_agentic_run_exits_2_with_a_provider_key_set(tmp_path, monkeypatch) -> None
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
  - `benchmarks/runner.py`: the dispatch keeps only `"fake"`; delete the
    five dispatch helpers and `_tool_call_records_to_dicts`; update the
    module docstring.
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
  - the added-packages table with upload dates (Task 5);
  - the intended-deletions table (Step 1);
  - the gate output;
  - a "Why this triggered supervisor review" section citing design §13
    for the dependencies and design §11.1 for the deletions.

---

## Not in this plan

- **PR 4:**
  - the Dockerfile and the container launcher (`--env-file`, one key per
    container, `--read-only`, tmpfs, `--cap-drop ALL`);
  - the per-worker egress proxy and its logs;
  - the isolation probe and the run script;
  - the `mcp-server-fetch` pin (2026.10.10 is the newest that is at least
    14 days old on 2026-10-31; re-check at run time) and its extraction
    path record;
  - the air-gapped runbook and host checks;
  - the full-run cost and time gate from the pilot.
- **PR 5:**
  - the criteria file, the probes, the judge panel and its batch units
    (using `split_batch`), and the reference grader;
  - `scoring.py` provenance;
  - post-hoc token counting from `canonical_messages` with `system` and
    `tools`;
  - grounding through `benchmarks/urls.py`;
  - report sections, including regrouping the per-repeat cell ids by
    `tool_model_key`.
- **Round 2:** the Streamable HTTP transport in the bridge (with
  `httpx2.AsyncClient` opened through `async with`) and the competitor
  systems.

## Open questions

1. **Price and limit facts the design does not give.** These are GPT-6.1
   Sol's `max_input_tokens`, any long-context tiers, and the cached and
   cache-write rates for Sonnet 5.5 and Gemini 3.8 Flash. Tasks 1 and 3
   read them from provider pages on the day.
2. **Gemini's output cap.** Does `max_output_tokens` include thinking
   tokens on `gemini-3.8-flash`? If not, §12's output bound needs a
   non-default thinking setting, which conflicts with "provider default
   thinking level". That is an owner decision.
3. **The output bound value.** This plan picks 16,384 for every hosted
   model. A lower value risks truncated answers; a higher one, larger
   bounds. Confirm it before the pilot.
4. **Young transitive packages.** The plan locks `pyasn1` 0.6.4 rather
   than 0.6.5 (2026-10-27). If 0.6.5 is a security fix, the owner may
   prefer it.
5. **Retry numbers.** #177 leaves N open. This plan picks 4 retries,
   backoff of 2, 4, 8 and 16 s, `retry-after` honoured up to 120 s, and 2
   reruns of a discarded trajectory.
6. **Model-call timeouts and the trajectory clock.** This plan sets the
   per-call SDK timeout to 120 s hosted and 600 s local, and stops failed
   attempts from using the trajectory clock. The design does not say
   either.
7. **Per-repeat cell ids.** Cells are named `<system>--<model>--r<k>` so
   that `score` and `report` read agentic runs without change until
   PR 5. The owner may prefer a layout change now.
8. **No live call before PR 4.** The host refuses keys, and the local
   launcher runs only scripted models, so PR B cannot make a live call at
   all. The design's "every paid call runs in a sandbox" then holds by
   construction. Confirm that no PR 3 live check is wanted.
9. **The Ollama latch.** This plan uses `OLLAMA_BASE_URL` as Ollama's
   second guard latch, in place of a key.
10. **The amendment date.** The design names the amendment "2026-10-29",
    but it lands after #173's "2026-10-30" amendment in the same file.
