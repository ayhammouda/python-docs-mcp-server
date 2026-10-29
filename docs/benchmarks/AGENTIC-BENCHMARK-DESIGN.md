# Agentic Multi-LLM Benchmark — Design

**Date:** 2026-10-29
**Status:** Draft for maintainer approval. Refs #63. Nothing in this document
authorizes a public claim before data exists (decision 5.17).
**Inputs:** `docs/research/2026-10-25-multi-llm-mcp-benchmarking.md`,
`docs/research/2026-10-25-mcp-python-sdk-v2-migration.md`,
`docs/benchmarks/PUBLIC-BENCHMARK-METHODOLOGY.md`, the audit of `benchmarks/`
done on 2026-10-25.

Labels used below:

- **Decided** — the maintainer decided this in the design conversation.
- **Call** — proposed by Claude and accepted in principle by the maintainer
  ("make the remaining calls and mark them for review"). Each one can be
  changed during review without reopening the rest of the design.

---

## 1. Intent

The maintainer wants public, reproducible evidence for the three things that
make `python-docs-mcp-server` worth installing (**Decided**):

1. **No third-party dependency.** At query time the agent needs only the
   official Python documentation, already indexed locally. No API key, no
   third-party service, no network path to a docs vendor.
2. **Token economy.** The docs are processed once, at index build time, into
   short, section-level, budget-trimmed results. Every question therefore costs
   fewer tokens than with peers that return larger or less targeted payloads.
3. **Security, compliance and air-gapped use.** The server is pull-only from
   official sources and answers from local storage. With a prebuilt index it
   works with the internet unreachable, so people running local models can get
   grounded Python docs without leaving their network.

Beating Context7 on correctness is a welcome secondary result, not the goal
(**Decided**). Context7 is skipped in round 1 (**Decided**, 2026-10-29): its
terms forbid benchmarking without Upstash's permission, which has not been
requested.

The existing `benchmarks/` package has good plumbing (frozen 50-question corpus,
artifact layout, scoring queue, report generator, gated Claude token counting)
but no LLM in the loop: its "answers" are retrieved text, and its provider
adapters are mocks. This design adds the missing part: real models choosing
tools in a loop, each in its own sandbox.

## 2. Claims under test

Each claim has a pass/fail rule fixed before any data is seen (**Call**).

| ID | Claim | Evidence | Passes when |
|---|---|---|---|
| C1 | At query time `python-docs-mcp-server` needs no API key and contacts no host other than the model provider's API. | Egress-proxy logs from every hosted run (section 7). | No allowed or attempted connection to any non-provider host, except calls the model made to `lookup_package_docs`, which are reported separately. |
| C2 (headline) | For each model, `python-docs-mcp-server` uses fewer normalized tokens per correct answer than each peer docs system (round 1: `docs-fetch` only). | Token and scoring records (sections 8, 9). | The upper bound of the 95% paired-bootstrap CI of the ratio (python-docs ÷ peer) is below 1.0, for that model and that peer. |
| C3 | With the internet unreachable, a local model plus `python-docs-mcp-server` answers the corpus better than the same model alone. | Air-gapped arm (section 7.4). | Proxy log shows zero internet hosts, and the 95% paired-bootstrap CI of the mean correctness difference (with docs − without) is entirely above 0. |
| S1 (secondary) | Correctness per system. Context7 and the other docs MCPs join in round 2. | Scoring records. | Reported as measured; no pass rule. |
| S2 (secondary) | The local cache makes repeat lookups faster. | Tool-call latency, cold vs warm (section 10). | Reported as measured; framed as speed, never as tokens. |

**Anti-claims** (must never appear in any report or copy):

- "Runs 2 and 3 are cheaper in tokens." The cache saves server work; the model
  receives the same payload every time (`docs/architecture/ADR-007-cache.md`).
- Any token ranking against the no-docs baseline. It injects no documentation,
  so it is the correctness floor, not a token competitor.
- "Human-graded", "human-calibrated", "human-validated", "graded against
  ground truth", or an unqualified "validated". The accurate wording is
  "LLM-graded, validated against synthetic probes and inter-model agreement"
  (section 9.3).
- "Fewer tokens than other docs MCPs" from round 1 data. Round 1's only C2
  peer is `docs-fetch`, so round 1 supports "fewer tokens than fetching the
  official docs pages" and nothing broader.

## 3. Goals, non-goals, anti-goals

**Goals**

- Prove or disprove C1–C3 with data a stranger can rerun from a clean clone.
- Cover the models the maintainer named: Claude, OpenAI (Codex models), Gemini
  Flash 3.8 and Gemma (**Decided**), each run in its own sandbox
  (**Decided**).
- Reuse the existing corpus, artifact layout, adjudication queue, report
  generator and live-phase guard.

**Non-goals (round 1)**

- Context7, GitMCP, DeepWiki and Ref.tools. They come in round 2
  (**Decided**), each behind the terms gate recorded in the competitor
  template.
- The fixed-retrieval "mode A" benchmark as a public result. The agentic run
  replaces it (**Call**). The CI retrieval regression in `benchmarks/regression.py`
  is unaffected because it calls the services directly.
- Provider-hosted MCP connectors (they cannot reach a local stdio server).
- CLI-agent comparisons (Claude Code vs Codex CLI vs Gemini CLI).
- A Python 3.15 post-cutoff slice, a contamination canary, new corpus questions.
- Any change to the server's tool names, parameters or return shapes.

**Anti-goals** (would make this a failure even if it ran)

- Tuning prompts, tool lists or caps per system.
- Dropping a failed system or cell silently.
- Publishing any comparative claim before the full run passes the judge gate
  (section 9.3).
- API keys reaching an image, a log, an artifact or a commit.
- Sending benchmark prompts through any tier whose inputs may be used for
  training (this rules out Gemma on the Gemini API free tier).
- Adding a dependency with a recent supply-chain incident to a project whose
  pitch is security (this rules out LiteLLM).

## 4. Prerequisite: MCP Python SDK v2 (separate PR, lands first)

**Decided:** move to the latest stable v2 SDK before the benchmark. Target:
`mcp` 2.3.0 (released 2026-10-02, verified on PyPI 2026-10-25).

Follow the checklist in section 5 of
`docs/research/2026-10-25-mcp-python-sdk-v2-migration.md`. In short:

- Pin `mcp>=2.3.0,<3` and `pydantic>=2.12`; refresh `uv.lock`.
- `server.py`: import from `mcp.server.mcpserver` instead of
  `mcp.server.fastmcp`; pass `version=__version__`.
- Tests: snake_case attribute access (`read_only_hint`, `input_schema`, …).
- **Call:** turn the six sync tools (`search_docs`, `get_docs`,
  `list_versions`, `detect_python_version`, `compare_versions`,
  `whatsnew_for_version`) into `async def`. v2 runs sync tools on worker
  threads, and these share one `sqlite3.Connection`. `async def` keeps v1's
  single-thread behavior exactly, which is the only option correct on every
  SQLite build, including the optional `pysqlite3-binary` extra nobody has
  checked. `lookup_package_docs` stays as is.
- `benchmarks/adapters/`: apply the minimal v2 fixes (snake_case result
  attributes, two-value `streamable_http_client` unpacking, explicit `httpx`
  dev dependency) so `main` stays correct until section 11 deletes these
  adapters. Their unit tests use camelCase fakes and would otherwise stay green
  while the live path is broken; update the fakes too.
- Reword ADR-008's "fd 1 carries only protocol frames" statement.

Done means: the canonical gate in `AGENTS.md` passes fresh, the stdio smoke
tests pass, and the server is checked in MCP Inspector and one real client per
`.github/INTEGRATION-TEST.md`. The PR description includes the
supervisor-review section required by `AGENT-EXECUTION-PIPELINE.md` §7 (major
dependency bump, `pyproject.toml` change, async in a sync path), citing this
document as the owner's approval.

## 5. Systems under test

| ID | What | Transport | Round | Notes |
|---|---|---|---|---|
| `python-docs` | This server at the run's commit | stdio subprocess | 1 | Full advertised tool list; index pinned by sha256. |
| `docs-fetch` | The MCP reference `fetch` server (`mcp-server-fetch`), limited to docs.python.org | stdio subprocess | 1 | What an agent does without a docs MCP (section 5.1). |
| `no-docs` | No tools | — | 1 | Correctness floor only. |
| `context7`, `gitmcp`, `deepwiki`, `ref-tools` | As in the competitor template | HTTP | 2 | Terms gates already recorded in the template. Context7 runs only with Upstash's written permission (ToS §C.8). |

**Call:** every MCP system exposes its full tool list exactly as the server
advertises it, unfiltered. Tool-definition tokens are part of the real cost,
and filtering our own list would bias C2 in our favor. If a model calls
`lookup_package_docs`, its PyPI request is denied by the proxy and the tool
returns its existing "Unable to retrieve PyPI metadata" note; C1 reports those
attempts separately.

### 5.1 `docs-fetch` baseline (**Call**)

Use the MCP project's own reference `fetch` server (`mcp-server-fetch` on
PyPI), not a tool we write. It is the generic fetch tool agents actually
install, so the baseline is not a strawman of our design, and it goes through
the same MCP bridge as every other system.

- Pin a release at least two weeks old at run time (2026.10.x releases require
  `mcp>=2.3`, so it shares the post-migration environment). Record the version.
- Its tool takes `url`, `max_length` (default 5,000 characters), `start_index`
  and `raw`, so the model can page through long pages. It checks
  `robots.txt` by default; keep that default.
- The egress proxy allows only `docs.python.org`, which is what limits it to
  the official docs. Its tool description is left unchanged.
- Live pages cannot be pinned: record fetch time and a sha256 of each tool
  result, and disclose this in the report. Record whether Readability.js
  (Node) or the pure-Python extraction path ran; the image does not install
  Node unless the pinned release needs it to work at all.

## 6. The tool loop (identical for every system and model)

- **One system prompt** for every system, committed with the harness and
  frozen before the pilot. It follows the methodology's prompting rules:
  concise answer, version-specific wording, no unsupported claims, and "ground
  your answer in the official Python documentation; if a documentation tool is
  available, use it before answering and cite the section URL you relied on".
  It never names any system. (**Call**)
- **User message:** the corpus `prompt`, verbatim.
- `tool_choice` = auto for every provider (Opus-class models reject forced tool
  use; auto is the only common setting).
- **Caps (Call):** at most 8 model calls and 12 tool calls per trajectory,
  30 s per tool call, and 300 s per trajectory (900 s in the air-gapped arm,
  for both of its systems). Hitting one of these ends the trajectory as a
  failure that stays in the denominator. Caps are identical across systems
  within a model; comparisons are only ever made within a model.
- **Result cap (Call):** each tool result is truncated at 100,000 characters
  with a marker, the hit is recorded, and the loop continues. It is a safety
  net, not a failure. In practice only `docs-fetch` can reach it, and its
  `start_index` lets the model read on.
- **Canonical tool results:** each MCP `CallToolResult` becomes one canonical
  text string (compact JSON of `structured_content` when present, else the
  concatenated text blocks), the same rule for every MCP system, hashed before
  being wrapped in each provider's tool-result shape.
- **Tool schemas:** store the raw `tools/list` result, each provider-converted
  schema, and their hashes. OpenAI `strict` is set explicitly to `false` (MCP
  schemas do not meet strict-mode rules). Gemini schemas are reduced to its
  supported OpenAPI subset; record what was dropped.
- **Recorded per model call:** canonical request messages, raw response, raw
  usage object, reported model version, wall time. **Per tool call:** name,
  arguments, canonical result text and hash, `is_error`, wall time, cap hits.
- **Repeats (Call):** k = 3 per (question, model, system). Execution order is
  randomized within each container, and all cells for one model run within the
  same 24-hour window.

## 7. Models and sandboxes

### 7.1 Models (**Call**, picking within the maintainer's named families)

| Arm | Model | API | Settings |
|---|---|---|---|
| Hosted | `claude-sonnet-5-5` | Anthropic Messages | Provider default effort; no sampling parameters (Claude 5.5 rejects them). |
| Hosted | `gpt-6.1-sol` (Codex's recommended balanced model) | OpenAI Responses | Provider default reasoning effort. |
| Hosted | `gemini-3.8-flash` | `google-genai` `generate_content`, automatic function calling disabled, paid tier | Provider default thinking level. Run before 2027-01-01, when its price doubles, or refresh the price snapshot. |
| Air-gapped | `gemma4:12b` via Ollama | Ollama's OpenAI-compatible Chat Completions | Ollama defaults except `OLLAMA_CONTEXT_LENGTH=32768` for both local systems (the default on a 16 GB machine is 4,096, which cannot hold seven tool schemas plus a docs result, and the OpenAI-compatible endpoint cannot change it per request). 12B fits the maintainer's 16 GB M1 Pro; 26B and 31B do not. |

Record the settings each call actually used, and the model version each
response reports (Google silently redirects deprecated IDs).
`docs/benchmarks/model-matrix.yml` is rewritten to this table.

### 7.2 Container per (model, system) (**Call**)

One image, `benchmarks/sandbox/Dockerfile`: a Python 3.14 slim base pinned by
digest, pinned `uv`, `uv sync --locked`, the repo at the run commit, a non-root
user. One container per (model, system) pair, so each proxy log describes
exactly one system.

The index holds the versions the README tells users to build (3.10–3.14),
and the interpreter is 3.14, so `detect_python_version` reports a current
stable install. Neither is tuned to the corpus (which is mostly 3.13); a model
that omits a version gets 3.14 docs, as a real user would. Container settings:

- `--read-only`, tmpfs for `HOME` and `/tmp`, `--cap-drop ALL`,
  `--security-opt no-new-privileges`, non-root `--user`, CPU and memory
  limits. No Docker socket.
- Fixed working directory and interpreter, so `detect_python_version` returns
  the same value every run.
- The docs index is bind-mounted read-only at the path `platformdirs` resolves
  inside the container (set `XDG_CACHE_HOME`), with its sha256 recorded. The
  server's own value-cache file lives on tmpfs, so every container starts
  cold.
- `PYTHON_DOCS_MCP_DISABLE_AUTO_INDEX=1` always.

### 7.3 Egress control and evidence (**Call**)

- Each container sits on a Docker `internal` network with no route out. Its
  only exit is a small egress-proxy container (builder picks Squid, tinyproxy
  or similar, pinned by digest) on both networks, configured through
  `HTTPS_PROXY`/`HTTP_PROXY`.
- Per-run allowlist: the model provider's API host, plus `docs.python.org`
  for `docs-fetch` runs. Nothing else.
- The proxy writes one line per connection attempt (time, host, port,
  allowed/denied) into the run's artifacts. That log is C1's evidence.
- Secrets: only that container's provider key, via `--env-file` from a file
  outside the repo. Never baked
  into the image, never logged. At the end of every run the harness scans all
  artifacts for each key value and fails the run if one is found.

### 7.4 Air-gapped arm (**Call**)

- Systems: `python-docs` and `no-docs`, model `gemma4:12b`, all 50 questions,
  k = 3 (about 300 local trajectories; expect an overnight run).
- Ollama runs natively on the Mac (Metal acceleration; Docker on macOS has no
  GPU access). The model is pulled once beforehand.
- The proxy allowlist contains only `host.docker.internal:11434`. No provider
  key exists in the container.
- The index is installed by following the air-gapped runbook (section 11.4)
  literally, so the run also proves the runbook.
- Only answering is air-gapped. Grading these answers afterwards uses the
  hosted judge APIs (section 9); the report says so.

## 8. Token measurement (**Call**)

This amends the methodology's "Token Measurement" for multi-turn loops.

- **Normalized tokens (headline, "Claude Tokens (Normalized Payload)").** Per
  trajectory: for every model call, count its full canonical input (system
  prompt + tool definitions + conversation so far, rendered in Anthropic
  Messages format) with the Anthropic count-tokens endpoint, plus the visible
  output of that call (text and tool-call arguments, counted by envelope
  difference). Sum over calls. Because every call's input is counted, extra
  round trips show up as extra tokens, which is how "easy to search" turns
  into token savings.
- **Count model:** `claude-sonnet-5-5`, pinned and written into every token
  record. `claude_tokens.py`'s retired `claude-3-5-sonnet-20241022` default is
  replaced. Newer Claude tokenizers count about 30% more tokens than older
  ones, so these numbers are not comparable to any earlier internal study;
  say so in the report.
- **Computed after the run** from the stored canonical transcripts, so counts
  can be redone and audited without rerunning models. Expect roughly 13,000
  count calls; the endpoint is free but rate-limited.
- **Per correct answer** = Σ normalized tokens over all trajectories of
  (model, system) ÷ Σ correctness scores (a 0.5 counts half; failures count
  their tokens and score 0).
- **CI:** paired bootstrap over question IDs (resample questions, keep all
  repeats of each), 10,000 resamples, 95% interval on the ratio
  python-docs ÷ peer.
- **Provider-reported usage (secondary, cost).** Stored verbatim per call;
  summed into input, cached input, output and reasoning tokens; multiplied by
  a price snapshot stored with the run. Reported as cost per correct answer
  per model. Never mixed with normalized tokens, never compared across models.
- **Docs payload tokens (diagnostic):** normalized count of tool results only.
- **Prompt caching:** no explicit caching requests; record whatever cached
  tokens providers report, and disclose it.

## 9. Correctness

Sections 9.1–9.4 follow
`docs/research/2026-10-29-llm-grading-without-human-calibration.md` §4; read it
for the evidence behind each choice.

### 9.1 Judge panel (**Call**)

- **Judges:** `claude-haiku-5-5`, `gpt-6-luna`, and a Gemini model that is not
  under test if one passes the probe gate G1 (first candidate:
  `gemini-3.1-flash-lite`, release status unverified). Otherwise
  `gemini-3.8-flash`, which then grades its own answers; disclose that.
  Effort per judge: the lowest level that passes G1, then frozen. Use the
  providers' batch APIs.
- **The grade covers content only.** Citations do not affect it: a blinded
  judge cannot apply the methodology's "citation when the system provides
  one" condition, and requiring one would cost the no-docs baseline half
  credit by construction. Citations are measured separately as grounding
  (9.2). This amends the methodology's rubric, whose 0.5 level mentions a
  missing citation.
- **Criteria file:** `docs/benchmarks/grading-criteria.yml`, one sentence per
  expected property, each marked `core` or `nuance`. An agent drafts it from
  `answer_key`, `expected_properties` and `ambiguity_notes`; it is committed
  before anything is graded. It is a derived file, so the frozen corpus does
  not change. Judges and probe builders read the same file.
- **Judge inputs:** the question with its version, the answer key, the two
  criteria, the ambiguity note if any, and the answer with every URL and doc
  path replaced by `[link]`. No citation list, no doc text, no system
  identity, no tool transcript.
- **Judge prompt:** frozen, committed and hashed before grading. Grade one
  answer against the key; do not answer the question; ignore style, length,
  confidence wording and `[link]` markers; extra statements count only if
  they contradict the key; a hedge that commits to one answer is graded on
  that answer; uncommitted alternatives, refusals and "I don't know" are not
  attempted; a criterion is met in any wording. Four worked examples (correct,
  partial, contradiction, not attempted) on one stdlib question that is not
  in the corpus.
- **Output:** strict structured JSON, all fields required, in this order:
  `reasoning` (at most three sentences, differences from the key only),
  `attempted`, `p1_met`, `p2_met`, `contradicts_key`, `wrong_version`. A parse
  failure or refusal is a withheld vote, never a default grade.
- **Aggregation:** majority vote per boolean field across the three judges,
  then the grade is computed in code:

  | Condition (checked in order) | Score | Label |
  |---|---|---|
  | not `attempted` | 0.0 | `NOT_ATTEMPTED` |
  | `contradicts_key` or `wrong_version` | 0.0 | `INCORRECT` |
  | a `core` criterion not met | 0.0 | `INCORRECT` |
  | all criteria met | 1.0 | `CORRECT` |
  | only a `nuance` criterion not met | 0.5 | `PARTIAL` |

  This matches the methodology's rubric: missing a nuance is 0.5, missing a
  core fact is "materially incomplete". Three binary votes always give a
  majority, so there are no ties. A field left with a withheld vote and a
  1–1 split goes to the reference grader (9.3). Keep every judge's raw output
  and usage.

### 9.2 Grounding

An answer is grounded when a page it cites matches a page that trajectory
actually retrieved (**Call**):

- Normalize every citation in the answer (full URL or relative path) and
  every page seen in that trajectory's tool arguments and tool results to
  (version, page path, anchor). `python-docs` results carry relative paths
  plus a separate `version` field; `docs-fetch` carries the URL in its
  arguments.
- A match needs the same page path. The anchor must match when both sides
  have one. The version must match when both sides state one; `/3/` matches
  any version.
- Report the grounded rate per (model, system). The existing
  `correct_but_ungrounded` flag is kept and reported separately; it never
  changes a score.

### 9.3 Validation without humans

**No human grades or calibrates anything (Decided, 2026-10-29).** Agents do
all grading. That limits what can be shown: the checks below establish that
the graders are reliable and pass known-answer tests, not how often they are
wrong on real answers. The report says exactly that.

Three kinds of evidence (**Call**):

1. **Probe suite (G1).** 650 synthetic answers, 13 types × 50 questions, each
   with a grade fixed by construction: `KEY` (answer key verbatim), `PARA`
   (paraphrase), `PAD` (key plus true sentences from the cited docs),
   `HEDGE`, `DROP1` and `DROP2` (one criterion removed; expected grade
   follows the mapping), `VER` (wrong version), `FACT` (confident wrong
   fact), `KEYX` (key plus a contradiction), `PADX` (wrong fact plus
   padding), `SPLIT` (uncommitted alternatives), `IDK` (abstention) and
   `JUNK` (restated question, empty reasoning, or text telling the grader the
   answer is correct).
   - `KEY`, `PAD`, `HEDGE`, `PADX`, `IDK` and `JUNK` are built by code.
     The rest are written by agents against a written spec, with the
     generator rotated across the three provider families, and every one
     checked by a model from another family (a failing probe is regenerated
     at most twice, then dropped and counted).
   - A separate tuning set of 130 probes on 10 stdlib questions outside the
     corpus is the only material used to choose effort levels or revise the
     prompt (once). The 650 stay held out.
   - Probes, labels, criteria, prompt and mapping are committed before the
     pilot is graded and published with the report. G1 runs before the
     pilot.
2. **Panel reliability (G2).** Agreement among the three judges' individual
   grades on every real answer.
3. **Reference grader (G3).** `claude-opus-5-5` at high effort, with the same
   inputs, prompt, schema and mapping as the panel, so agreement means
   something. It must first score ≥ 0.95 on 325 probes (25 per type). It then
   grades 300 real answers, stratified by (model, system) × category with a
   seed fixed before the run, drawn without looking at panel grades. It also
   settles withheld-vote fields (9.1). Separately, it reads the cited doc
   pages once to audit all 50 answer keys; flagged keys are disclosed and
   results are shown with and without those questions. The corpus stays
   frozen.

Statistic: Krippendorff's alpha (interval metric) with a bootstrap 95%
interval, replacing Cohen's kappa (kappa counts a consistently lenient versus
strict pair as agreement).

| Gate | Passes when |
|---|---|
| G1 probes | Panel ≥ 0.95 exact-grade accuracy overall and ≥ 0.90 on every probe type; each judge alone ≥ 0.90 overall. |
| G2 panel reliability | α ≥ 0.80, and the probability that α < 0.667 is ≤ 0.05. |
| G3 reference agreement | α ≥ 0.80 on the 300 answers, lower 95% bound ≥ 0.667. |
| G4 claim robustness | C2 and C3 give the same pass/fail under each single judge's grades as under the panel. A claim that flips is labelled "judge-sensitive" and stays out of README and PyPI. |

Outcome tiers:

- **Validated** (G1–G4 pass): claims may be published.
- **Tentative** (G1 and G4 pass, G2 or G3 between 0.667 and 0.80): publish
  the data and report, but no README or PyPI claim.
- **Judge not validated** (anything else): data only, no public claim.

Reported but not gated: test–retest alpha per judge, a self-preference index
per (judge, evaluated family), length–grade correlation, label shares per
(model, system), parse failures, refusals and withheld votes.

## 10. Latency

Wall clock per trajectory (dispatch to final answer), split into model time,
tool time and harness overhead; median, p95, min, max. For `python-docs` tool
calls, the harness marks each call cold (first time those exact arguments are
seen in that container) or warm (seen before) and reports both. Index build
time is reported once as setup cost, with the index file size.

## 11. What changes in the repo

### 11.1 Harness (**Call**: thin loop on official SDKs, inside `benchmarks/`)

No Inspect AI and no LiteLLM. The loop is a few hundred lines on `anthropic`,
`openai` and `google-genai`, with the repo's own `mcp` client for stdio and
Streamable HTTP. Every byte sent and received stays visible, the output feeds
the existing artifact schema, scorer and report directly, and the dependency
footprint stays small, which matters for a project selling security. Ollama is
reached through the `openai` SDK, so it adds no dependency. The three SDKs go
in the `dev` dependency group, pinned in `uv.lock`, so CI's existing
`uv sync --locked --dev` can lint and type-check them without a workflow
change.

- Replace the live-provider stubs with real implementations behind the existing
  `require_live_environment` guard. Keep deterministic mocks so CI runs the
  whole agentic pipeline end to end with no network and no keys.
- One generic MCP bridge (stdio or Streamable HTTP) serves every system,
  including `docs-fetch`, and replaces the five fixed-flow adapters. Once it
  covers `python-docs` and `docs-fetch`, delete
  `python_docs_mcp_adapter.py`, the four competitor adapters and their tests
  (an intended test deletion; record it in the PR). Keep `eligibility.py` and
  the manifest terms gates.
- Extend the CLI with an agentic run, the probe gate, judge and reference
  grading, post-hoc token counting and the new report sections. Exact flags
  are the builder's.
- `benchmarks/scoring.py`: `ingest_adjudication_verdicts` tags every verdict
  `scoring_method: "human"` and counts it in `human_scored_cells`. Add LLM
  provenance values (for example `llm_panel`, `llm_reference`), record grader
  model IDs as the adjudicator, and update the queue text and module
  docstring that say a human decides. No LLM grade may ever be stored as
  `human`.
- New committed inputs, all before the pilot is graded: the criteria file,
  the probe file, the judge prompt with its examples, and the mapping.

### 11.2 Methodology and matrix

- Add "Amendment 2026-10-29: agentic mode" to
  `PUBLIC-BENCHMARK-METHODOLOGY.md`, covering: parity now means "same prompt,
  same loop and caps, only the tool set differs"; the systems in section 5;
  sections 8–10 of this document, including the content-only grade (9.1);
  the claim rules in section 2; no-docs is never ranked on tokens.
- Rewrite `model-matrix.yml` per section 7.1.

### 11.3 Report additions

Correctness per (model, system) with CIs and per-category breakdown; C2 table
(normalized tokens per correct answer, ratio and CI, pass/fail); cost table;
tool-use metrics (tool-use rate, calls and turns per trajectory, invalid
calls, cap hits); latency including cold vs warm; C1 dependency table (API key
required, hosts contacted at query time from proxy logs, works offline); the
air-gapped arm; every failure with its reason; price snapshot, model versions
reported by providers, count model, index sha256, image digests.

Grading disclosure, next to the correctness numbers (**Call**):

- A plain statement: correctness was graded by three LLMs (IDs, reported
  versions, effort) against the corpus answer keys; no human graded or
  calibrated any grade; validity rests on the published probe suite,
  agreement among the judges, and agreement with an LLM reference grader;
  these checks cannot show how often the graders are wrong on real answers.
- Which judge shares a family with, or is, which evaluated model.
- Full texts and hashes of the judge prompt, examples, schema, mapping,
  link-masking rule and criteria file.
- All probes and labels, how each was built, generator and checker models,
  dropped-probe counts, per-judge and per-type accuracy with Wilson intervals,
  effort levels tried and chosen.
- G2 and G3 alpha with intervals (overall, per evaluated family, per system),
  the G3 confusion matrix, G4 verdicts under each single judge, the outcome
  tier, test–retest and self-preference indices, label shares, grader
  failures, the answer-key audit, and grading cost kept apart from the
  benchmark's own token metrics.

Publication (**Call**): `docs/benchmarks/results/<run-id>/REPORT.md` plus the
existing README-safe summary, with the raw artifact tarball attached to a
GitHub release and its sha256 in the report. No README or PyPI claim unless
the grading outcome is "Validated" (section 9.3).

### 11.4 Air-gapped runbook (part of this project)

A short README section: on a connected machine, `pip download` the package
and its dependencies and run `build-index`; copy the wheels and `index.db`
across; on the target, install with `--no-index --find-links`, place
`index.db` at the cache path, set `PYTHON_DOCS_MCP_DISABLE_AUTO_INDEX=1`, and
run `doctor`. Note that `lookup_package_docs` needs PyPI and returns a clear
note when offline. No new CLI command (**Call**); copying the prebuilt index
is the import.

## 12. Budget and run sequence (**Call**)

Prices used for the estimate: Sonnet 5.5 and GPT-6.1 Sol $2 / $10 per million
input/output tokens, Gemini 3.8 Flash $0.75 / $3.75.

| Step | Trajectories | Estimated cost | Hard cap |
|---|---|---|---|
| Probe gate G1 (tuning and held-out probes, all judges, before the pilot) | — | ~$4 batch | in the pilot cap |
| Pilot: 10 fixed questions (2 per category, chosen before running) × 3 hosted models × 3 systems × k=3 | 270 | ~$15 + ~$1 grading | $30 |
| Full hosted run: 50 × 3 × 3 × 3 | 1,350 | ~$75–95 | $175 total, grading included |
| Air-gapped arm (local answering) | ~300 | $0 | — |
| Grading the full run: panel on 1,650 answers, reference grader on 325 probes + 300 answers + 50 key audits | — | ~$25 batch (~$50 list; Opus is ~70%) | in the $175 cap |

Grading figures are estimates from the research note (§4.8) and get replaced
by pilot measurements.

The harness keeps a running cost from provider usage × the price snapshot and
refuses to start a new trajectory once the cap is reached. The pilot decides
whether k = 3 is enough (if the C2 interval cannot exclude 1.0 for reasons of
variance alone, raise k within the cap). Run before 2027-01-01 if possible
(Gemini price change).

Suggested PR order (the builder may re-slice): (1) SDK v2; (2) methodology
amendment, model matrix, this document; (3) harness with mocked end-to-end CI
test; (4) sandbox, proxy, run script, air-gapped runbook; (5) criteria file,
probe suite, judge panel, reference grader, `scoring.py` provenance, post-hoc
token counting, report; then G1, the maintainer-run pilot, review, full run,
grading, and the report PR.

## 13. Constraints

- Canonical gate from `AGENTS.md` (ruff, pyright on `src/` and `benchmarks/`,
  pytest) passes on every PR.
- No paid call can happen in CI or tests; the live-phase guard stays.
- Live runs need maintainer-supplied keys: `ANTHROPIC_API_KEY`,
  `OPENAI_API_KEY` and `GEMINI_API_KEY`.
- The corpus is frozen (`c52c3f2`, PR #130); questions and answer keys do not
  change.
- `pyproject.toml`, dependency and workflow changes are forbidden territory for
  autonomous agents (`AGENT-EXECUTION-PIPELINE.md` §2); this document is the
  owner's approval for the ones listed here, and nothing beyond them.

## 14. Risks

- **Retrieval quality may cap correctness.** The CI retrieval baseline shows
  8 of 65 exact canonical hits in the top five. Agentic models can recover by
  calling `get_docs` with better arguments, at a token cost. The report
  publishes whatever happens.
- **`docs-fetch` may be competitive on C2.** With 5,000-character windows and
  `start_index`, a careful model can read only the part of a page it needs.
  That outcome gets published too.
- **`gemma4:12b` tool calling** may be unreliable with seven tools. The pilot
  includes a 5-question local smoke run; if tool calls fail often, report
  that and consider `gemma4:e4b` or a stronger machine.
- **Memory on the 16 GB Mac.** The 12B model plus a 32K context plus Docker
  Desktop may not fit together. The smoke run checks this; keep Docker
  Desktop's memory allocation small during the local arm.
- **Provider drift:** model redirects, rate limits and price changes during a
  run. Recorded per call; the 24-hour window limits it.
- **Three cheap judges may not reach α ≥ 0.80.** Then the honest outcome is
  the "tentative" tier, not a lower threshold. If most answers score 1.0,
  alpha can fall despite high raw agreement; the pilot checks the grade mix.

## 15. Left to the builder

Module layout inside `benchmarks/`; provider adapter internals; the canonical
transcript schema (it must hold everything listed in sections 6 and 8); the
proxy software; Dockerfile details; the judge prompt wording (within 9.1);
the probe-writing spec (within 9.3); the bootstrap and alpha
implementations; CLI flag names; report layout within 11.3.
