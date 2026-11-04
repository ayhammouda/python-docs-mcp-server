# Agentic Benchmark: Sandbox, Egress Proxy, Run Script and Air-Gapped Arm Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land design §12 step (4): run every live worker in its own
hardened container behind its own logging egress proxy, with a run script,
live-call safety, the Ollama client and the air-gapped runbook, so the
maintainer can run the smoke run, G1 and the pilot with keys that never
touch the host process.

**Architecture:** One PR, `feat/agentic-sandbox`.
- `ContainerLauncher` implements the existing `Launcher` seam in
  `benchmarks/agentic/host.py`. The host stays keyless. Per
  (model, system) job it creates one `--internal` Docker network with an
  isolated gateway, one Squid proxy aliased `egress` on that network, and
  one hardened worker container that holds at most its own provider's key.
- A keyless isolation probe runs on every job's network before any worker
  starts. Teardown removes the worker container before its in-flight unit
  is settled.
- SDK clients take the proxy explicitly from `BENCHMARK_EGRESS_URL`; MCP
  servers get `HTTPS_PROXY`/`HTTP_PROXY` from the manifest's `{egress}`
  placeholder. No `*_PROXY` variable ever reaches a worker's own
  environment.
- `agentic-run --launcher container` is a dry run unless `--live` is given,
  and a live run needs an interactive confirmation of every provider's
  remaining cap and estimate.
- The air-gapped arm adds the Ollama client, an unmetered ledger path,
  host offline checks around every trajectory, and two runbooks.

**Tech Stack:**
- Python ≥ 3.12 on the host; the worker image runs CPython 3.14.8.
- Docker Desktop for Mac ≥ 4.96.1 (Engine ≥ 28.0 for the isolated gateway
  mode); Squid 6.6 (`ubuntu/squid`); uv 0.13.4; `mcp-server-fetch`
  2026.10.19 in its own hashed venv inside the image.
- `openai` 3.32.0 (already in the dev group) for Ollama's OpenAI-compatible
  endpoint. No new Python dependency.
- pytest (`asyncio_mode = "auto"`), Ruff (line length 100), Pyright.

**Spec:**
- `docs/benchmarks/AGENTIC-BENCHMARK-DESIGN.md` §§5.1, 6, 7.1–7.4, 11.4,
  12, 13, 14;
- the "Not in this plan → PR 4" list in
  `docs/plans/2026-10-31-agentic-benchmark-harness.md`;
- the owner rulings on #184 (part 1 items 6, 8 and 9) and the verification
  record on #197 (its "PR 4 requirements recorded in `shortcut:` comments"
  and "Deferred to #198");
- issue #198;
- the PR 5 plan (#199, `docs/plans/2026-11-04-agentic-grading-report.md`), whose owner-ruled question 9 binds the shapes of `egress/summary.json` and `airgap/checks.json`;
- `docs/research/2026-11-04-benchmark-sandbox-tooling.md` (the "sandbox
  note" below), the source of every pin and every Docker, Squid and Ollama
  fact in this plan, and `docs/research/2026-10-31-benchmark-provider-limits.md`.

Executors read the design §7, the sandbox note and `benchmarks/agentic/host.py`
in full before Task 1.

## Global Constraints

- **Branch:** `feat/agentic-sandbox` from `origin/main` at `9bb2fa0` (#197) or later. Refs #63; closes #198.
- **No live call during implementation or CI.** Tests use fakes: `FakeDocker` (a recorded `DockerRun`), `httpx2.MockTransport`/`httpx.MockTransport`, and scripted models. No test reaches a network host. The docker test (Task 15) is opt-in (`BENCHMARK_DOCKER_TESTS=1`), keyless (scripted models only) and never runs in CI. No implementer passes `--live` with a real key, sets a real key, or reads `.env.local`.
- **Keys:** the host process never holds a key in `os.environ`; `refuse_host_keys` is unchanged and still runs first. `ContainerLauncher.command` reads one key from the keys file at container-start time into the docker CLI child's environment and passes it as a bare `-e NAME`. A key is never in argv, a log, an artifact or the image. Each container holds at most its own provider's key; scripted and Ollama containers hold none.
- **Worker environment allowlist** (the only `-e` names a worker container gets): `HOME`, `XDG_CACHE_HOME`, `PYTHON_DOCS_MCP_DISABLE_AUTO_INDEX`, `BENCHMARK_EGRESS_URL`, `BENCHMARK_LIVE_PROVIDERS_ENABLED`, `OLLAMA_BASE_URL`, and the one provider key name. Never a `*_PROXY`, `SSL_CERT_*` or `REQUESTS_CA_BUNDLE` variable. The docker CLI child's own environment carries only `PATH`, `HOME`, `DOCKER_HOST`, `DOCKER_CONTEXT`, `DOCKER_CONFIG`, `DOCKER_CERT_PATH`, `DOCKER_TLS_VERIFY` (when set) and that key.
- **Container hardening (design §7.2):** `--read-only`, tmpfs for `/tmp` (which is `HOME`) and for `/cache/mcp-python-docs`, `--cap-drop ALL`, `--security-opt no-new-privileges`, `--user 10001:10001`, `--memory 1g --cpus 1 --pids-limit 256`, `--init`, `--rm`, `-i` without `-t`, `--pull never`, no Docker socket, image by ID (`sha256:<64 hex>`) never by tag. `XDG_CACHE_HOME=/cache`; `index.db` bind-mounted read-only at `/cache/mcp-python-docs/index.db` for `python-docs` jobs only; `PYTHON_DOCS_MCP_DISABLE_AUTO_INDEX=1` always.
- **Egress (design §7.3):** one Squid per job, on that job's `--internal` network (`-o com.docker.network.bridge.gateway_mode_ipv4=isolated`) under the alias `egress`, and on one shared outside network per run. Allowlist: the job's provider API host on 443 (none for scripted), plus `docs.python.org:443` for `docs-fetch`; the air-gapped arm allows only `host.docker.internal:11434`. No TLS interception. One log line per connection attempt: time, client, method, host, port, status, Squid result.
- **Pins** (sandbox note §A), unchanged without an owner ruling:
  - `python:3.14.8-slim-trixie@sha256:fbecd461fc72d21b2143c6eaba25143738079154a6d0ea3da3aec724f50eed4a`
  - `ghcr.io/astral-sh/uv:0.13.4@sha256:9f130421e930bfbdc563c00740466603f8723158e90297d07957c2294640262b`
  - `ubuntu/squid:6.6-24.04_edge@sha256:8a3baed477e2c282ab8aa5edad442f69873246964f225c5c2ae8364b6610963c`
  - `mcp-server-fetch==2026.10.19`, every transitive package hash-pinned and published on or before 2026-10-21.
  - Before Task 5, the implementer checks https://www.python.org/downloads/ for a CPython 3.14.9+ security fix. If one exists and is at least 14 days old, they raise it in the PR body instead of switching silently.
- **Evidence for PR 5** (#199, owner question 9): `<run>/egress/summary.json` is `{"jobs": {job_id: {"hosts_allowed": [...], "hosts_denied": [...], "lookup_package_docs_attempts": n}}, "isolation_probe": "pass"|"fail"}` and `<run>/airgap/checks.json` is `{"all_passed": bool, "checks": n, "internet_hosts": [...]}`. Exactly these keys. Tasks 10 and 13 write them, each checked by a shape validator before writing. A field this plan would want beyond them is an open question (11, 12), never an extra key.
- **Shared files with PR 5:** PR 4 merges first, and PR 5 also edits `benchmarks/agentic/host.py` and `worker.py`. PR 4's edits there stay additive at the launcher seam:
  - `host.py`: three new `Launcher` methods, `LocalLauncher` no-ops, one new `_teardown` argument, and the `prepare`/`close` calls around the gather in `run_jobs`. The rest is two rewritten comments and one message tail.
  - `worker.py`: one `_converted` branch and one `_system_info` key.
  - Nothing else in either file moves or is renamed.
- **Platform:** Docker Desktop for Mac only (context `desktop-linux`). Podman and Colima are out of scope for round 1.
- **Dependencies:** no change to `pyproject.toml` or `uv.lock`. `mcp-server-fetch` lives only in the image's `/opt/fetch` venv, from a hashed requirements file.
- **Forbidden territory** (`AGENT-EXECUTION-PIPELINE.md` §2): no change under `.github/workflows/`, `src/` (the tool surface is frozen), or the corpus (`c52c3f2`). The README change stays below the first install block.
- **Unchanged:** budget caps (Anthropic $180, OpenAI $65, Google $180), the loop caps (8 model calls, 12 tool calls, 30 s per tool call, 300 s / 900 s per trajectory, 100,000 result bytes, k = 3), one request per reserved unit, and the price snapshot expiry (2026-12-31).
- **Gate** (`AGENTS.md`), run fresh before the PR, every pytest run with a throwaway cache:
  - `uv run --locked ruff check src/ tests/ benchmarks/ ops/ .github/scripts/`
  - `uv run --locked pyright src/ benchmarks/`
  - `ISO=$(mktemp -d); uv run --locked env XDG_CACHE_HOME="$ISO" pytest --tb=short -q`
- **TDD** (superpowers:test-driven-development): every new behaviour has a failing test first.
- **Existing tests:** none is weakened. Only these change, and the PR body lists them:
  - Task 1: the two signal-warning tests and the HTTP-date test (#198).
  - Task 3: `test_make_client_refuses_the_ollama_api_until_pr_4` is replaced by `test_make_client_builds_the_ollama_client_through_the_guard`, because PR 4 delivers that client by design.
  - Task 4: the `docs-fetch` `pass_env` assertion in `tests/benchmarks/test_agentic_bridge.py` follows the manifest change.
- **Supervisor review:** the diff passes 500 lines and replaces a test. Label the PR `supervisor-review`.
- **Commits and PRs:** conventional prefixes; every commit ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. The PR body follows `.github/PULL_REQUEST_TEMPLATE/agent.md` and ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`. Implementers never self-verify and never merge.

## Review Focus

1. A worker container that outlives its killed docker CLI could keep its connection open after the unit is settled. Teardown must remove the container before settling, and nothing with the run's label may be left. Tests: Task 7 `test_teardown_stops_the_worker_before_its_unit_is_settled`; Task 8 `test_stop_worker_removes_only_its_own_container`; Task 15 docker test, "no container or network with the run label remains".
2. A `proxies` entry in the maintainer's `~/.docker/config.json` injects `HTTPS_PROXY` and `ALL_PROXY` into every new container, which a test that builds argv never sees. Tests: Task 9 `test_env_check_names_forbidden_variables_only`; Task 15 docker test, "a DOCKER_CONFIG with proxies fails the isolation probe".
3. `docker run -i` stdout is not documented as binary-safe, so a large frame could be corrupted on the way out. Test: Task 15 docker test, "a trajectory frame over 100,000 bytes round-trips through a container".
4. An air-gap check that passes while the Mac is still online: DNS is down but IP works, or an IPv6 `utun` default route remains. Tests: Task 12 `test_offline_checks_need_both_a_name_and_an_address_to_fail` and `test_default_routes_on_macos_and_linux`.
5. Keys-file shapes a parser can get wrong: `export NAME=`, quoted values, a key on a line after a comment, a name that is a prefix of another (`OPENAI_API_KEY_OLD`), or a group-readable file. Tests: Task 8 `test_read_key_returns_only_the_named_key` and `test_read_key_refuses_a_group_or_world_readable_file`.

## File map

| File | Task | Responsibility |
|---|---|---|
| `tests/benchmarks/test_agentic_run.py`, `test_agentic_clients.py` | 1 | #198 test fixes |
| `benchmarks/adapters/guard.py` | 2, 3 | `EGRESS_URL_ENV`; the Ollama latch |
| `benchmarks/agentic/clients.py` | 2, 3 | explicit egress proxy; `OllamaChatClient` |
| `benchmarks/agentic/schemas.py`, `usage.py`, `ledger.py`, `worker.py`, `run.py` | 3 | Ollama schema, buckets, unmetered units |
| `benchmarks/agentic/bridge.py`, `docs/benchmarks/agentic-manifest.yml` | 4 | `{egress}` for MCP servers |
| `benchmarks/sandbox/fetch-requirements.txt` | 4 | hashed `mcp-server-fetch` pin |
| `benchmarks/sandbox/Dockerfile` | 5 | the worker image |
| `benchmarks/agentic/egress.py` | 6, 10 | allowlists, Squid config, proxy log parser, forbidden env names; the C1 `egress/summary.json` |
| `benchmarks/agentic/host.py` | 7 | launcher lifecycle: prepare, stop worker, close |
| `benchmarks/agentic/sandbox.py` | 8, 10, 14 | docker runner, keys file, networks, proxies, `ContainerLauncher`, `run_once` |
| `benchmarks/agentic/probe.py` | 9 | isolation probe, in container and on the host |
| `benchmarks/agentic/live.py` | 11 | reference rates, full-run gate, live confirmation |
| `benchmarks/agentic/airgap.py` | 12, 13 | host offline checks, Ollama preflight; the C3 `airgap/checks.json` |
| `benchmarks/agentic/limits.py` | 14 | Sonnet 5.5 limits from the Models API |
| `benchmarks/__main__.py` | 9, 11, 14 | CLI flags and internal commands |
| `benchmarks/sandbox/run.sh`, `docs/benchmarks/SANDBOX-RUNBOOK.md`, `README.md`, `CHANGELOG.md` | 15 | operator flow and runbooks |

## `shortcut:` comments this PR must meet

`grep -rn "shortcut:" benchmarks/agentic` on `9bb2fa0` finds five. Three name a PR 4 requirement:

| Comment | Requirement | Met by |
|---|---|---|
| `host.py:95` | `cat` on `PATH` for the anchor; "PR 4's worker image must too" | Task 5 (the Dockerfile asserts `command -v cat`), Task 15 (`run.sh` checks the host's `cat`); Task 7 rewrites the comment |
| `host.py:110` | the whole-group kill guarantee, "which removing the container does, and signal only what it holds" | Task 7 (`stop_worker` before settle), Task 8 (`docker rm -f` of its own container name only); Task 7 rewrites the comment |
| `clients.py:117–119` | an explicit egress proxy (`proxy=...`), and a worker env allowlist with no `*_PROXY`, `SSL_CERT_*` or `REQUESTS_CA_BUNDLE` | Task 2 (explicit proxy), Task 8 (allowlist), Task 9 (in-container env check); Task 2 rewrites the comment |

The other two (`worker.py:113` fd redirection and `ledger.py:253` `fcntl`, both POSIX only) still hold: the worker runs on Linux in the container and the host runs on macOS. They stay as they are. The #197 record also lists "a fresh MCP session per cell for Ref.tools", which is round 2.

Each rewritten comment states the guarantee as now met. The prefix becomes a plain comment, so `grep -rn "shortcut:.*PR 4" benchmarks/` finds nothing.

---

### Task 1: The #198 test fixes

**Files:**
- Modify: `tests/benchmarks/test_agentic_run.py` (`test_run_agentic_completes_off_the_main_thread`, `test_a_loop_without_signal_support_runs_without_handlers`)
- Modify: `tests/benchmarks/test_agentic_clients.py` (`test_parse_retry_after_accepts_an_http_date`)

**Interfaces:** none; tests only. Production code is correct.

- [ ] **Step 1: Reproduce both failures**

Run: `ISO=$(mktemp -d); nohup uv run --locked env XDG_CACHE_HOME="$ISO" pytest tests/benchmarks/test_agentic_run.py -k "off_the_main_thread or without_signal_support" -q > "$ISO/out" 2>&1; tail -3 "$ISO/out"`
Expected: 2 failed, each on `"SIGTERM, SIGHUP" in ...`.

- [ ] **Step 2: Derive the expected names from the dispositions**

In both tests, before the run:

```python
expected = ", ".join(
    s.name for s in (signal.SIGTERM, signal.SIGHUP) if signal.getsignal(s) is not signal.SIG_IGN
)
```

Assert `expected in <line>`, replacing the literal. Add
`test_an_ignored_sighup_is_left_out_of_the_skipped_handlers_line`. It sets
`SIGHUP` to `SIG_IGN`, restores it in `finally`, runs
`_until_signalled` on a loop whose `add_signal_handler` raises
`NotImplementedError`, and asserts that the stderr line contains
`"SIGTERM"` and does not contain `"SIGHUP"`.

- [ ] **Step 3: Pin the clock in the HTTP-date test**

```python
def test_parse_retry_after_accepts_an_http_date(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 11, 4, 12, 0, 0, tzinfo=UTC)

    class Frozen(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            return now

    monkeypatch.setattr(clients, "datetime", Frozen)
    when = format_datetime(now + timedelta(seconds=30), usegmt=True)
    assert parse_retry_after({"retry-after": when}) == 30.0
```

- [ ] **Step 4: Run under `nohup` and normally**

Run Step 1's command again, then `ISO=$(mktemp -d); uv run --locked env XDG_CACHE_HOME="$ISO" pytest tests/benchmarks/test_agentic_run.py tests/benchmarks/test_agentic_clients.py -q`
Expected: all pass both ways.

- [ ] **Step 5: Commit**

```bash
git add tests/benchmarks/test_agentic_run.py tests/benchmarks/test_agentic_clients.py
git commit -m "test(benchmarks): derive ignored signals and pin the retry-after clock (#198)"
```

---

### Task 2: Explicit egress proxy in the SDK clients

**Files:**
- Modify: `benchmarks/adapters/guard.py` (add `EGRESS_URL_ENV`)
- Modify: `benchmarks/agentic/clients.py:104-165` (`_client_args`, the three `*_sdk` builders, `make_client`; rewrite the `shortcut:` comment)
- Test: `tests/benchmarks/test_agentic_clients.py`

**Interfaces:**
- Produces: `guard.EGRESS_URL_ENV = "BENCHMARK_EGRESS_URL"`.
- Produces: `clients.egress_proxy(environ: Mapping[str, str]) -> str | None`. It returns the value when set. It refuses with `BenchmarkValidationError` naming `BENCHMARK_EGRESS_URL` any value that is not `http://<host>:<port>` (no user info, path, query or fragment; scheme `http` only).
- Produces: `anthropic_sdk(api_key, timeout_s, *, transport=None, proxy: str | None = None)`, and the same `proxy` keyword on `openai_sdk` and `gemini_sdk`. With `proxy` set, every route (`http://`, `https://`, `all://`) goes through a transport built with that proxy and the same pinned trust store. An injected `transport` (tests only) still carries every request. Without `proxy`, behaviour is exactly as today.
- `make_client` passes `proxy=egress_proxy(os.environ)` to every live SDK.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("family_id,host", LIVE_HOSTS)  # the three hosted families, as in the existing proxy test
async def test_the_egress_proxy_carries_every_call(monkeypatch, family_id, host) -> None
    # BENCHMARK_EGRESS_URL=http://egress:3128, plus HTTPS_PROXY and ALL_PROXY=socks5://proxy.invalid:1080.
    # Patch AsyncHTTPTransport in kind._client and anthropic._base_client as the existing test does.
    assert [_proxy_url(p) for p in routes] == ["http://egress:3128"]
    assert [r.url.host for r in recorder.seen] == [host]

@pytest.mark.parametrize("value", ["socks5://egress:1080", "http://u:p@egress:3128",
                                   "http://egress:3128/x", "egress:3128", "https://egress:3128"])
def test_a_malformed_egress_url_is_refused(monkeypatch, value) -> None
    # make_client(...) raises BenchmarkValidationError matching "BENCHMARK_EGRESS_URL"

@pytest.mark.parametrize("build", [anthropic_sdk, openai_sdk, gemini_sdk])
def test_a_ca_variable_cannot_swap_the_proxy_routes_trust_store(monkeypatch, build) -> None
    # SSL_CERT_FILE=/nonexistent/ca.pem; build("test-key", 30, proxy="http://egress:3128") constructs
```

`_proxy_url(p)` normalises a string or a proxy object to its URL string.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --locked pytest tests/benchmarks/test_agentic_clients.py -k "egress or proxy_routes" -q`
Expected: FAIL, because `proxy` is an unexpected keyword and the routes record `None`.

- [ ] **Step 3: Implement `egress_proxy` and `_client_args(transport, proxy=None)`**

Use `urllib.parse.urlsplit` for the check. With a proxy, mount the
proxy transport on all three patterns, so the Anthropic SDK's own env
mounts cannot win. Replace the `shortcut:` comment with: "No proxy from
the environment. In a sandbox the egress proxy comes only from
`BENCHMARK_EGRESS_URL`, and the container launcher passes no `*_PROXY`,
`SSL_CERT_*` or `REQUESTS_CA_BUNDLE` (Task 8)."

- [ ] **Step 4: Run the client tests**

Run: `uv run --locked pytest tests/benchmarks/test_agentic_clients.py -q`
Expected: PASS, with `test_a_proxy_variable_cannot_steer_a_call` and `test_a_ca_variable_cannot_swap_a_clients_trust_store` unchanged and green.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/adapters/guard.py benchmarks/agentic/clients.py tests/benchmarks/test_agentic_clients.py
git commit -m "feat(benchmarks): route SDK clients through an explicit egress proxy"
```

---

### Task 3: The Ollama client and unmetered units

**Files:**
- Modify: `benchmarks/adapters/guard.py` (`LIVE_LATCH_ENV`, `require_live_environment`)
- Modify: `benchmarks/agentic/schemas.py` (`to_openai_chat`), `benchmarks/agentic/usage.py` (`ollama_buckets`)
- Modify: `benchmarks/agentic/clients.py` (`ollama_sdk`, `OllamaChatClient`, `_LIVE_APIS`, `make_client`, `_SCRIPT_BUCKETS`, `_SCRIPT_STOP`)
- Modify: `benchmarks/agentic/ledger.py` (`UNMETERED_PROVIDERS`, `LedgerService`)
- Modify: `benchmarks/agentic/worker.py` (`_converted`), `benchmarks/agentic/run.py` (snapshot expiry only for metered live families)
- Modify: `docs/benchmarks/model-matrix.yml` (`gemma4-12b`: `max_output_tokens: 32768`, `framing_allowance_tokens: 256`, with a comment that neither is sent to Ollama)
- Test: `tests/benchmarks/test_agentic_clients.py`, `test_agentic_schemas.py`, `test_agentic_ledger.py`, `test_agentic_run.py`

**Interfaces:**
- Consumes: `egress_proxy`, the `proxy` keyword (Task 2).
- Produces: `guard.LIVE_LATCH_ENV = {"ollama": "OLLAMA_BASE_URL"}`. `require_live_environment("ollama")` needs the live flag and a non-empty `OLLAMA_BASE_URL` (owner ruling 9). It never reads a key, and `PROVIDER_API_KEY_ENV` does not change.
- Produces: `schemas.to_openai_chat(tools) -> list[dict]`, each item `{"type": "function", "function": {"name", "description", "parameters", "strict": False}}`.
- Produces: `usage.ollama_buckets(usage) -> Buckets`, with `uncached = prompt_tokens − prompt_tokens_details.cached_tokens`, `cached = cached_tokens`, `output = completion_tokens`, `reasoning = 0`.
- Produces: `clients.ollama_sdk(base_url: str, timeout_s: float, *, transport=None, proxy=None) -> openai.AsyncOpenAI`, with `api_key="ollama"` (ignored by Ollama, sandbox note §D.1) and `max_retries=0`.
- Produces: `clients.OllamaChatClient(sdk, model_id)`, with `provider = "ollama"`.
  - It sends `model`, `messages` and, when tools exist, `tools`. It never sends `max_tokens`, `tool_choice` (unsupported, §D.1; auto is Ollama's only behaviour) or a sampling parameter (design §7.1, "Ollama defaults").
  - `request_params()` returns `{"model": model_id}`.
  - Messages: `system`, `user`, the assistant replay, and `{"role": "tool", "tool_call_id", "content"}`.
  - A tool call without an id gets `call_<n>`. That id is written into the replayed assistant message too, so each result pairs with its call.
  - `stop_reason` is `choices[0].finish_reason`; `reported_model` is `response.model`.
- Produces: `ledger.UNMETERED_PROVIDERS = frozenset({"ollama"})`. For these providers, `LedgerService.reserve` takes the provider lock (one unit in flight) and returns `f"{provider}-{uuid4().hex}"` without touching `Ledger`. `settle` releases the lock. `Ledger.reserve` still refuses any provider without a cap.
- `make_client`: `openai-chat-completions` maps to provider `ollama`, calls `require_live_environment("ollama")`, refuses when any of `PAID_KEY_ENVS` is set, and needs `OLLAMA_BASE_URL` to start with `http://` or `https://`.
- `worker._converted("ollama", tools)` returns `(to_openai_chat(tools), [])`. `_SCRIPT_BUCKETS["ollama"] = ollama_buckets` and `_SCRIPT_STOP["ollama"] = "stop"`, so a scripted air-gapped fixture works.
- `run_agentic` calls `refuse_stale_snapshot` only when a family is live and metered (`api != "scripted"` and `provider in caps`).

- [ ] **Step 1: Write the failing tests**

```python
def test_ollama_needs_the_live_flag_and_its_base_url(monkeypatch) -> None
    # flag only -> LiveProviderDisabledError matching "OLLAMA_BASE_URL"; flag + URL -> passes; no key read

def test_to_openai_chat_wraps_each_tool_with_strict_false() -> None

def test_ollama_buckets_split_cached_prompt_tokens() -> None
    assert ollama_buckets({"prompt_tokens": 120, "completion_tokens": 30,
                           "prompt_tokens_details": {"cached_tokens": 20}}) == Buckets(100, 0, 20, 30, 0)

async def test_ollama_client_round_trips_a_tool_call() -> None
    # MockTransport; first request JSON: messages[0] == {"role": "system", ...}, "tools" in chat shape,
    # and none of "max_tokens", "tool_choice", "temperature", "top_p" present.
    # Response with one tool call -> ToolCall; second call replays the assistant message and sends
    # {"role": "tool", "tool_call_id": <same id>, "content": ...}

async def test_ollama_tool_calls_without_ids_pair_by_position() -> None
    # two calls without ids -> call_1, call_2 in the ToolCalls, the replay and the tool messages

def test_make_client_builds_the_ollama_client_through_the_guard(monkeypatch) -> None
    # replaces test_make_client_refuses_the_ollama_api_until_pr_4:
    # no flag -> LiveProviderDisabledError; flag + OLLAMA_BASE_URL -> OllamaChatClient;
    # plus ANTHROPIC_API_KEY set -> BenchmarkValidationError matching "ANTHROPIC_API_KEY"

@pytest.mark.parametrize("status", [200, 500])
async def test_ollama_client_sends_one_request_per_call(status) -> None
    # max_retries == 0 on ollama_sdk; exactly one request recorded per call(), whatever the status

async def test_an_unmetered_provider_gets_one_unit_at_a_time_and_is_never_booked(tmp_path) -> None
    # two concurrent reserve("ollama") calls: the second is granted only after the first settles;
    # no ollama.jsonl in the ledger dir; every metered provider's spent() is unchanged

def test_an_air_gapped_dry_run_ignores_the_price_snapshot_expiry(tmp_path) -> None
    # dry run of gemma4-12b with a launcher that supports every family, today=date(2027, 1, 2): no raise;
    # the same for claude-sonnet-5-5 raises matching "expires"
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --locked pytest tests/benchmarks/test_agentic_clients.py tests/benchmarks/test_agentic_schemas.py tests/benchmarks/test_agentic_ledger.py tests/benchmarks/test_agentic_run.py -k "ollama or unmetered or air_gapped or openai_chat" -q`
Expected: FAIL, on import errors (`to_openai_chat`, `ollama_buckets`, `OllamaChatClient`), a `BenchmarkValidationError` from the price lookup, and the expiry.

- [ ] **Step 3: Implement the interfaces above**

Delete `test_make_client_refuses_the_ollama_api_until_pr_4` in the same
commit. Change `make_client`'s "arrives with the Ollama client in PR 4"
message to name the unknown api only.

- [ ] **Step 4: Run the agentic suite**

Run: `ISO=$(mktemp -d); uv run --locked env XDG_CACHE_HOME="$ISO" pytest tests/benchmarks -q`
Expected: PASS, with `test_provider_without_a_cap_is_refused[ollama]` still green (it tests `Ledger`, not `LedgerService`).

- [ ] **Step 5: Commit**

```bash
git add benchmarks/ docs/benchmarks/model-matrix.yml tests/benchmarks/
git commit -m "feat(benchmarks): Ollama chat client, unmetered ledger units and the base-URL latch"
```

---

### Task 4: MCP servers through the egress proxy, and the `docs-fetch` pin

**Files:**
- Modify: `benchmarks/agentic/bridge.py` (`EGRESS_PLACEHOLDER`, `server_env`, `open_bridge`)
- Modify: `docs/benchmarks/agentic-manifest.yml`
- Modify: `benchmarks/agentic/worker.py` (`_system_info` adds `fetch_extraction`)
- Create: `benchmarks/sandbox/fetch-requirements.txt`
- Test: `tests/benchmarks/test_agentic_bridge.py`, `tests/benchmarks/test_agentic_host.py`, `tests/benchmarks/test_agentic_sandbox_files.py` (new)

**Interfaces:**
- Consumes: `guard.EGRESS_URL_ENV` (Task 2).
- Produces: `bridge.EGRESS_PLACEHOLDER = "{egress}"`.
- Produces: `bridge.server_env(system: SystemSpec, environ: Mapping[str, str]) -> dict[str, str]`. It starts from `system.env`. A value equal to `"{egress}"` becomes `environ[EGRESS_URL_ENV]`, and the variable is dropped when that is unset. Then it copies `pass_env` names present in `environ`. `open_bridge` uses it with `os.environ`.
- Manifest:
  - `python-docs`: `env` adds `HTTPS_PROXY: "{egress}"` and `HTTP_PROXY: "{egress}"`, so a `lookup_package_docs` PyPI request reaches the proxy and is logged as denied (design §5, C1).
  - `docs-fetch`: `env` is the same two, `pass_env: [HOME]`. Its comment names `mcp-server-fetch` 2026.10.19 and `benchmarks/sandbox/fetch-requirements.txt`.
- `_system_info` adds `"fetch_extraction"`: for `docs-fetch`, `"readability-js"` when `shutil.which("node")` is set, else `"pure-python"`; `None` for other systems (design §5.1).
- `fetch-requirements.txt` is generated, never hand-written:

```bash
echo "mcp-server-fetch==2026.10.19" | uv pip compile - --universal --python-version 3.14 \
  --generate-hashes --exclude-newer 2026-10-22T00:00:00Z -o benchmarks/sandbox/fetch-requirements.txt
```

- [ ] **Step 1: Write the failing tests**

```python
def test_server_env_points_both_proxy_variables_at_the_egress() -> None
    # environ {BENCHMARK_EGRESS_URL: "http://egress:3128"}: HTTPS_PROXY == HTTP_PROXY == that URL
    # for python-docs and docs-fetch; PYTHON_DOCS_MCP_DISABLE_AUTO_INDEX == "1" kept

def test_server_env_drops_the_proxy_variables_without_an_egress() -> None

def test_no_worker_proxy_variable_reaches_an_mcp_server() -> None
    # environ {HTTPS_PROXY: "socks5://x:1", ALL_PROXY: "socks5://x:1"} and no egress:
    # server_env(docs-fetch) has no *_PROXY name

def test_system_info_records_the_fetch_extraction_path(monkeypatch) -> None
    # which("node") None -> "pure-python"; "/usr/bin/node" -> "readability-js"; python-docs -> None

def test_fetch_requirements_pin_the_researched_release_with_hashes() -> None
    # "mcp-server-fetch==2026.10.19" present; every requirement line carries "--hash=sha256:"
```

Update the existing `docs-fetch` assertion in `test_agentic_bridge.py`
to `pass_env == ("HOME",)` and the new `env`.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --locked pytest tests/benchmarks/test_agentic_bridge.py tests/benchmarks/test_agentic_host.py tests/benchmarks/test_agentic_sandbox_files.py -q`
Expected: FAIL (`server_env` missing, no requirements file).

- [ ] **Step 3: Implement, then generate and audit the requirements file**

Run the `uv pip compile` command above, then:
`uvx pip-audit --requirement benchmarks/sandbox/fetch-requirements.txt --no-deps --disable-pip --progress-spinner off`
Expected: `No known vulnerabilities found`. If one is found, lock the
fixed release, even when it is newer than 14 days, and record the
advisory ids in the PR body (the `pyasn1` precedent).

- [ ] **Step 4: Run the tests**

Run: `ISO=$(mktemp -d); uv run --locked env XDG_CACHE_HOME="$ISO" pytest tests/benchmarks -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/agentic/bridge.py benchmarks/agentic/worker.py docs/benchmarks/agentic-manifest.yml benchmarks/sandbox/fetch-requirements.txt tests/benchmarks/
git commit -m "feat(benchmarks): MCP servers reach the egress proxy; pin mcp-server-fetch 2026.10.19"
```

---

### Task 5: The worker image

**Files:**
- Create: `benchmarks/sandbox/Dockerfile`
- Test: `tests/benchmarks/test_agentic_sandbox_files.py`

**Interfaces:**
- Produces: an image whose `WORKDIR` is `/app` (the repo at the build commit), whose `PATH` starts with `/app/.venv/bin`, with user `10001:10001`, `mcp-server-fetch` on `PATH`, `cat` present, no `node`, and `/cache/mcp-python-docs` present.
- The build context is always `git archive HEAD` (Task 15), so the image holds committed files only. `.env.local` (gitignored by `.env*`) can never enter it.

The Dockerfile is fixed copy; there is deliberately no `# syntax=` line,
because that would pull an unpinned frontend image:

```dockerfile
FROM python:3.14.8-slim-trixie@sha256:fbecd461fc72d21b2143c6eaba25143738079154a6d0ea3da3aec724f50eed4a
COPY --from=ghcr.io/astral-sh/uv:0.13.4@sha256:9f130421e930bfbdc563c00740466603f8723158e90297d07957c2294640262b /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
# The build context is `git archive HEAD`: exactly the run commit, nothing untracked.
COPY . /app
RUN uv sync --locked --no-editable \
 && uv venv /opt/fetch --python /usr/local/bin/python3.14 \
 && uv pip install --python /opt/fetch/bin/python --require-hashes --no-deps \
      -r benchmarks/sandbox/fetch-requirements.txt \
 && ln -s /opt/fetch/bin/mcp-server-fetch /usr/local/bin/mcp-server-fetch \
 && mkdir -p /cache/mcp-python-docs \
 && command -v cat \
 && ! command -v node \
 && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin bench
ENV PATH=/app/.venv/bin:$PATH
USER 10001:10001
```

- [ ] **Step 1: Write the failing static test**

```python
def test_dockerfile_pins_every_base_by_digest_and_runs_unprivileged() -> None
    # first FROM == the pinned python ref; the uv COPY --from == the pinned uv ref;
    # no line starts with "# syntax"; the last USER is "10001:10001";
    # no ENV line names PROXY, SSL_CERT or REQUESTS_CA; "uv sync --locked" present;
    # "command -v cat" and "! command -v node" present
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run --locked pytest tests/benchmarks/test_agentic_sandbox_files.py -k dockerfile -q`
Expected: FAIL (no Dockerfile).

- [ ] **Step 3: Create the Dockerfile above**

- [ ] **Step 4: Run the test**

Run: `uv run --locked pytest tests/benchmarks/test_agentic_sandbox_files.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/sandbox/Dockerfile tests/benchmarks/test_agentic_sandbox_files.py
git commit -m "feat(benchmarks): sandbox worker image pinned by digest"
```

- [ ] **Step 6: Build once by hand from the commit (needs Docker and network, not CI)**

`git archive` reads the commit, so this step comes after Step 5.

Run: `IID=$(mktemp); git archive --format=tar HEAD | docker build --file benchmarks/sandbox/Dockerfile --label org.opencontainers.image.revision=$(git rev-parse HEAD) --iidfile "$IID" - && docker run --rm --pull never "$(cat "$IID")" sh -c 'id -u; command -v cat mcp-server-fetch; test ! -e /app/.env.local && echo no-env-file; python -c "import benchmarks.agentic.clients"'`
Expected: `10001`, `/usr/bin/cat`, `/usr/local/bin/mcp-server-fetch`, `no-env-file`, and exit 0. Keep the image ID for Task 15.

---

### Task 6: Egress policy: allowlists, Squid config and the proxy log

**Files:**
- Create: `benchmarks/agentic/egress.py`
- Test: `tests/benchmarks/test_agentic_egress.py`

**Interfaces:**
- Produces:
  - `EGRESS_ALIAS = "egress"`, `EGRESS_PORT = 3128`, `EGRESS_URL = "http://egress:3128"`.
  - `PROVIDER_HOSTS = {"anthropic": (("api.anthropic.com", 443),), "openai": (("api.openai.com", 443),), "google": (("generativelanguage.googleapis.com", 443),), "ollama": (("host.docker.internal", 11434),)}`; `SYSTEM_HOSTS = {"docs-fetch": (("docs.python.org", 443),)}`. These hosts are the pinned base URLs in `clients.py` (Anthropic, OpenAI, Gemini) and design §7.4.
  - `allowlist(family: ModelFamily, system_id: str | None) -> tuple[tuple[str, int], ...]`: the provider's hosts unless `family.api == "scripted"`, plus `SYSTEM_HOSTS.get(system_id, ())`, in that order.
  - `squid_conf(allow: Sequence[tuple[str, int]]) -> str` (template below).
  - `@dataclass(frozen=True) class ProxyEvent: at: str; client: str; method: str; host: str; port: int; status: int; result: str`, with property `allowed: bool` (`not result.startswith("TCP_DENIED")`).
  - `parse_proxy_log(text: str) -> list[ProxyEvent]`. It skips blank lines and raises `BenchmarkValidationError` on a malformed line, naming its line number.
  - `FORBIDDEN_ENV = re.compile(r"(?i)^(?:\w*_proxy|ssl_cert_\w*|requests_ca_bundle)$")` and `forbidden_env_names(environ: Mapping[str, str]) -> list[str]` (sorted names, never values).

`squid_conf([("api.anthropic.com", 443), ("host.docker.internal", 11434)])` returns exactly:

```
http_port 3128
visible_hostname egress
pid_filename none
cache deny all
shutdown_lifetime 1 seconds
httpd_suppress_version_string on
logformat bench %{%Y-%m-%dT%H:%M:%S}tg.%03tuZ %>a %rm %>rd %>rP %>Hs %Ss
access_log stdio:/dev/stdout logformat=bench
cache_log /dev/stderr
acl allow_1_host dstdomain api.anthropic.com
acl allow_1_port port 443
http_access allow CONNECT allow_1_host allow_1_port
acl allow_2_host dstdomain host.docker.internal
acl allow_2_port port 11434
http_access allow !CONNECT allow_2_host allow_2_port
http_access deny all
```

Port 443 entries allow only `CONNECT`; any other port allows only plain
HTTP. No `dst` ACL appears, so a denied name is never resolved (sandbox
note §C).

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("family_id,system_id,expected", [
    ("claude-sonnet-5-5", "python-docs", (("api.anthropic.com", 443),)),
    ("gpt-6.1-sol", "docs-fetch", (("api.openai.com", 443), ("docs.python.org", 443))),
    ("gemma4-12b", "python-docs", (("host.docker.internal", 11434),)),
    ("scripted-anthropic", "no-docs", ()),
])
def test_allowlist_per_job(family_id, system_id, expected) -> None

def test_squid_conf_matches_the_template() -> None          # the exact text above
def test_squid_conf_with_no_host_denies_everything() -> None  # no allow line; ends "http_access deny all"
def test_parse_proxy_log_reads_tunnels_and_denials() -> None
    # "2026-11-04T10:00:00.123Z 172.20.0.3 CONNECT api.anthropic.com 443 200 TCP_TUNNEL" -> allowed
    # "2026-11-04T10:00:01.004Z 172.20.0.3 CONNECT example.com 443 403 TCP_DENIED" -> not allowed
def test_parse_proxy_log_rejects_a_malformed_line() -> None
def test_forbidden_env_names_match_every_case() -> None
    # HTTPS_PROXY, http_proxy, ALL_PROXY, NO_PROXY, SSL_CERT_FILE, SSL_CERT_DIR, REQUESTS_CA_BUNDLE
    # are named; PATH and PROXY_NOTE are not
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --locked pytest tests/benchmarks/test_agentic_egress.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `benchmarks/agentic/egress.py`**

Import nothing heavier than `benchmarks.model_matrix` and `benchmarks.runner`: the probe imports this module inside the container.

- [ ] **Step 4: Run the tests**

Run: `uv run --locked pytest tests/benchmarks/test_agentic_egress.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/agentic/egress.py tests/benchmarks/test_agentic_egress.py
git commit -m "feat(benchmarks): egress allowlists, Squid config and proxy log parser"
```

---

### Task 7: Launcher lifecycle in the host

**Files:**
- Modify: `benchmarks/agentic/host.py` (`Launcher`, `LocalLauncher`, `_teardown`, `_supervise`, `run_jobs`; rewrite both `shortcut:` comments)
- Test: `tests/benchmarks/test_agentic_host.py`

**Interfaces:**
- Produces: three new `Launcher` methods:

```python
async def prepare(self, jobs: Sequence[WorkerJob]) -> None:
    """Before any worker starts; raise to stop the run with nothing spent."""
async def stop_worker(self, job: WorkerJob) -> None:
    """After the group kill and before the job's in-flight unit is settled."""
async def close(self) -> list[str]:
    """After every worker is gone: release everything; return problems, raise only KeyLeakError."""
```

- `LocalLauncher`: `prepare` and `stop_worker` do nothing; `close` returns `[]`.
- `_teardown(proc, anchor, log_path, ledger, in_flight, stop_worker)` runs in this order: kill the group, wait (bounded), reap the anchor, `await stop_worker()`, scrub the log. The settle stays in `finally`, so a failing `stop_worker` still books the unit at twice its bound.
- `run_jobs`: after the `unsupported` check, `await launcher.prepare(jobs)`, then the supervisors, all inside `try/finally: problems = await launcher.close()`. If the gather succeeded and `problems` is not empty, raise `BenchmarkValidationError` listing them. If an exception is already propagating, print them to stderr.
- The refusal text for unsupported families keeps "the local launcher runs only scripted models" and ends "use --launcher container".
- Comment at `host.py:95`: "The host's `cat`; `run.sh` checks for it, and the worker image has it too (benchmarks/sandbox/Dockerfile)." Comment at `host.py:110`: "Reaches only this host's process tree. A container launcher's `stop_worker` removes its own container, which ends everything in it, before the unit is settled."

- [ ] **Step 1: Write the failing tests** (a `RecordingLauncher` wraps `LocalLauncher` and logs calls to a shared list)

```python
async def test_run_jobs_prepares_every_job_before_any_worker_starts(tmp_path) -> None
    # events start with ("prepare", [all job ids]); no "command" before it
async def test_a_failed_prepare_starts_no_worker_and_still_closes(tmp_path) -> None
    # prepare raises -> no "command" event, "close" recorded, the error propagates
async def test_teardown_stops_the_worker_before_its_unit_is_settled(tmp_path) -> None
    # a worker lost with a unit in flight: order ["stop_worker:<job>", "settle:<unit>"],
    # booked at twice its bound (reuse the lost-worker fixture of
    # test_host_settles_a_lost_workers_unit_at_twice_its_bound_and_frees_the_provider)
async def test_a_failing_stop_worker_still_settles_the_unit(tmp_path) -> None
async def test_close_problems_fail_a_clean_run(tmp_path) -> None
    # close returns ["proxy log missing for j"] -> BenchmarkValidationError naming it
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --locked pytest tests/benchmarks/test_agentic_host.py -k "prepare or stop_worker or close_problems" -q`
Expected: FAIL (`AttributeError: prepare`).

- [ ] **Step 3: Implement the interfaces above**

- [ ] **Step 4: Run the host and run suites**

Run: `ISO=$(mktemp -d); uv run --locked env XDG_CACHE_HOME="$ISO" pytest tests/benchmarks/test_agentic_host.py tests/benchmarks/test_agentic_run.py -q`
Expected: PASS, every existing teardown and anchor test unchanged.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/agentic/host.py tests/benchmarks/test_agentic_host.py
git commit -m "feat(benchmarks): launcher prepare, stop-worker and close around every job"
```

---

### Task 8: The sandbox and `ContainerLauncher`

**Files:**
- Create: `benchmarks/agentic/sandbox.py`
- Test: `tests/benchmarks/test_agentic_sandbox.py` (with a `FakeDocker` in the module)

**Interfaces:**
- Consumes: `egress.*` (Task 6), the `Launcher` methods (Task 7), `EGRESS_URL_ENV`, `LIVE_PROVIDERS_ENABLED_ENV`, `PROVIDER_API_KEY_ENV`.
- Produces:
  - `SQUID_IMAGE` (the pinned ref), `WORKER_USER = "10001:10001"`, `PROXY_USER = "proxy"`, `RUN_LABEL = "org.python-docs-mcp.benchmark-run"`, `MIN_ENGINE = (28, 0)`.
  - Names: the worker container is `pdmcp-<run_id>-<job_id>`; its proxy is `<worker name>-proxy`; its internal network is `<worker name>-net`; the run's outside network is `pdmcp-<run_id>-out`. Every container and network carries `--label RUN_LABEL=<run_id>`.
  - `read_key(keys_file: Path, name: str) -> str`. It reads `NAME=VALUE` lines (optional `export `, matching single or double quotes stripped, `#` comment lines and blank lines skipped) and matches the exact name. It refuses a file whose mode has any group or other bit, a missing name, or an empty value. Every error names the file and the variable, never a value.
  - `DockerRun = Callable[[Sequence[str]], Awaitable[subprocess.CompletedProcess[bytes]]]`; `docker_runner(env: Mapping[str, str]) -> DockerRun` runs `docker` through `asyncio.to_thread(subprocess.run, ..., capture_output=True, env=env, timeout=120)`.
  - `cli_env(environ: Mapping[str, str]) -> dict[str, str]`: the docker CLI allowlist in Global Constraints.
  - `@dataclass(frozen=True) class SandboxConfig: image: str; run_id: str; out_dir: Path; index_path: Path; keys_file: Path | None; repo_commit: str`. `__post_init__` refuses an `image` that does not match `^sha256:[0-9a-f]{64}$`.
  - `@dataclass(frozen=True) class Egress: name: str; network: str; proxy: str; allow: tuple[tuple[str, int], ...]; conf_path: Path`.
  - `class Sandbox(config, docker: DockerRun | None = None)`:
    - `async verify_image() -> dict[str, str]`: `docker image inspect` gives `Id == config.image` and the label `org.opencontainers.image.revision == config.repo_commit`. `docker version` gives a server version ≥ `MIN_ENGINE`. `git status --porcelain` is empty. It writes `out_dir/sandbox.json` (image, revision, proxy image, engine version, docker context).
    - `async open_egress(name: str, allow) -> Egress`: creates the outside network once per run (`pdmcp-<run_id>-out`). Then the internal network (`--internal -o com.docker.network.bridge.gateway_mode_ipv4=isolated`), the conf at `out_dir/egress/<name>.squid.conf`, and the proxy container: `docker run -d --pull never --name <proxy> --label RUN_LABEL=<run_id> --network <outside> --read-only --tmpfs /run --tmpfs /var/spool/squid --cap-drop ALL --security-opt no-new-privileges --user proxy --memory 256m --cpus 0.5 --entrypoint /usr/sbin/squid --mount type=bind,src=<conf>,dst=/etc/squid/squid.conf,readonly SQUID_IMAGE -N -f /etc/squid/squid.conf`. Last, `docker network connect --alias egress <internal> <proxy>`.
    - `container_argv(*, name: str, network: str, env: Mapping[str, str], key_name: str | None, mounts: Sequence[str], argv: Sequence[str], interactive: bool = True) -> list[str]`: the full `docker run` argv with the Global Constraints hardening (`-i` only when `interactive`). `env` becomes `-e NAME=VALUE`, `key_name` a bare `-e NAME`, each mount a `--mount`, then the image ID, then `argv`.
    - `async close_egress(egress: Egress) -> list[str]`: `docker stop -t 5 <proxy>` (Squid then flushes within `shutdown_lifetime`). Then `docker logs <proxy>`: stdout goes to `out_dir/egress/<name>.access.log` and stderr to `.cache.log`, both passed through `redact_key_shaped`. Then `docker rm -f <proxy>` and `docker network rm <internal>`. Every step runs even after a failure; failures are returned as problems. A key-shaped string is recorded and raised as `KeyLeakError` by `ContainerLauncher.close` after all cleanup.
  - `ProbeFn = Callable[..., Awaitable[dict[str, Any]]]`, called as `probe(sandbox, egress, *, job_id, family, system_id)`.
  - `class ContainerLauncher(sandbox: Sandbox, probe: ProbeFn | None = None)` implements `Launcher` (`None` means Task 9's `isolation_probe`):
    - `supports(family)`: `True` for `scripted` and for every api `make_client` builds.
    - `command(job)`: argv `container_argv(name="pdmcp-<run_id>-<job_id>", ..., argv=["python", "-m", "benchmarks", "agentic-worker"])` and env `cli_env(os.environ)`. For a live metered family it adds `{key_name: read_key(...)}`; a missing `keys_file` refuses.
      - Worker env: `HOME=/tmp`, `XDG_CACHE_HOME=/cache`, `PYTHON_DOCS_MCP_DISABLE_AUTO_INDEX=1`, `BENCHMARK_EGRESS_URL=EGRESS_URL`. A live family adds `BENCHMARK_LIVE_PROVIDERS_ENABLED=1`; Ollama adds `OLLAMA_BASE_URL=http://host.docker.internal:11434/v1`.
      - Mounts: tmpfs `/cache/mcp-python-docs` (`tmpfs-mode=1777`); `python-docs` adds `index_path` read-only at `/cache/mcp-python-docs/index.db`; a scripted family adds its script read-only at its own absolute path.
    - `async prepare(jobs)`: `verify_image()`, then for every job `open_egress(job_id, allowlist(job.family, job.system.id))` and `probe(...)`. It raises on the first failure. Task 9 adds the run's control probe before the per-job probes.
    - `async stop_worker(job)`: `docker rm -f pdmcp-<run_id>-<job_id>`. It ignores "No such container" only.
    - `async close()`: `close_egress` for every egress it opened, then `docker network rm` for the outside network. Returns problems and raises `KeyLeakError` last. Task 10 adds the `egress/summary.json` write after the last `close_egress`.

- [ ] **Step 1: Write the failing tests**

```python
def test_read_key_returns_only_the_named_key(tmp_path) -> None
    # file (mode 0o600): "# c\nexport OPENAI_API_KEY_OLD=x\nOPENAI_API_KEY='sk-a'\nANTHROPIC_API_KEY=\"sk-b\"\n"
    assert read_key(f, "OPENAI_API_KEY") == "sk-a"
def test_read_key_refuses_a_group_or_world_readable_file(tmp_path) -> None   # 0o640 and 0o604
def test_read_key_errors_never_hold_a_value(tmp_path) -> None                # missing and empty names
def test_worker_argv_carries_the_design_hardening(tmp_path) -> None
    # contains --read-only, "--cap-drop ALL", "--security-opt no-new-privileges", "--user 10001:10001",
    # --init, --rm, -i, "--pull never", "--network <internal>", --memory, --cpus, --pids-limit;
    # no -t, --privileged, docker.sock or --network host; the image ID right before the command
def test_only_python_docs_jobs_mount_the_index_read_only(tmp_path) -> None
def test_worker_env_is_the_allowlist(tmp_path, monkeypatch) -> None
    # host env polluted with HTTPS_PROXY, ALL_PROXY, SSL_CERT_FILE, REQUESTS_CA_BUNDLE, LANG:
    # -e names in argv are within the allowlist; cli env has no FORBIDDEN_ENV name
def test_each_container_gets_only_its_own_providers_key(tmp_path, monkeypatch) -> None
    # anthropic job: cli env holds ANTHROPIC_API_KEY only, argv holds bare "-e ANTHROPIC_API_KEY";
    # openai job: OPENAI_API_KEY only; gemma4-12b and scripted: no key name anywhere;
    # refuse_host_keys(os.environ) still passes after every command()
def test_a_key_value_never_reaches_argv(tmp_path) -> None
def test_a_live_family_without_a_keys_file_is_refused(tmp_path) -> None
async def test_prepare_builds_one_isolated_network_and_proxy_per_job(tmp_path) -> None
    # FakeDocker log: one outside network; per job "network create --internal -o
    # com.docker.network.bridge.gateway_mode_ipv4=isolated", one squid run, one "network connect
    # --alias egress"; out/egress/<job>.squid.conf == squid_conf(allowlist(...)); probe called per job
async def test_prepare_refuses_an_image_from_another_commit(tmp_path) -> None
async def test_prepare_refuses_an_engine_older_than_28(tmp_path) -> None
def test_sandbox_config_refuses_a_tag(tmp_path) -> None                       # "python-docs:latest"
async def test_stop_worker_removes_only_its_own_container(tmp_path) -> None
    # exactly ["docker", "rm", "-f", "pdmcp-<run>-<job>"]; FakeDocker never sees ps, prune or --filter
async def test_close_saves_each_proxy_log_and_removes_what_it_created(tmp_path) -> None
    # per egress in order: stop, logs, rm -f, network rm; then the outside network;
    # out/egress/<job>.access.log holds the fake stdout
async def test_close_redacts_a_key_shaped_proxy_log_and_raises_after_cleanup(tmp_path) -> None
async def test_close_keeps_going_after_a_failed_step(tmp_path) -> None
def test_container_launcher_supports_scripted_and_every_live_api() -> None
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --locked pytest tests/benchmarks/test_agentic_sandbox.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `benchmarks/agentic/sandbox.py`**

It must not import a provider SDK, because `run.sh` imports `SQUID_IMAGE`
from it. Tests pass a stub `probe`; Task 9 supplies the real default.

- [ ] **Step 4: Run the tests**

Run: `ISO=$(mktemp -d); uv run --locked env XDG_CACHE_HOME="$ISO" pytest tests/benchmarks/test_agentic_sandbox.py tests/benchmarks/test_agentic_host.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/agentic/sandbox.py tests/benchmarks/test_agentic_sandbox.py
git commit -m "feat(benchmarks): container launcher with per-job egress proxy and one key per container"
```

---

### Task 9: The isolation probe

**Files:**
- Create: `benchmarks/agentic/probe.py`
- Modify: `benchmarks/__main__.py` (internal `agentic-probe --spec JSON`)
- Modify: `benchmarks/agentic/sandbox.py` (`ContainerLauncher.prepare` uses it)
- Test: `tests/benchmarks/test_agentic_probe.py`

**Interfaces:**
- Consumes: `forbidden_env_names`, `parse_proxy_log`, `EGRESS_URL` (Task 6); `Sandbox`, `Egress` (Task 8).
- Produces, in the container:
  - `run_probe_checks(spec: Mapping[str, Any]) -> list[dict[str, Any]]`. Each result is `{"check", "target", "passed", "detail"}`. `spec` keys: `direct` (list of `[host, port]`), `token`, `dns_name`, `denied` (`[host, port]`), `canary_via_proxy` (`[host, port]`), `allowed_http` (list of URLs). Checks:
    - `env`: `forbidden_env_names(os.environ)` is empty (detail: names only).
    - `direct`: for each target, a TCP connect with a 2 s timeout must fail. On a connect it sends `GET / HTTP/1.0\r\nAuthorization: Bearer <token>\r\n\r\n` and fails the check.
    - `dns`: `socket.getaddrinfo(dns_name, 443)` must raise.
    - `proxy_denied`: `CONNECT host:port` through `EGRESS_URL` must answer 403.
    - `proxy_canary`: a plain `GET` to the canary through the proxy, carrying the token, must answer 403.
    - `allowed_http`: each URL through the proxy must answer 200. Only the Ollama `/api/version` URL is ever listed.
  - `run_control(canary: tuple[str, int], token: str) -> dict`: the connect must succeed and send the token.
  - `probe_main(argv: list[str]) -> int`: `agentic-probe --spec <json>`. It prints one JSON object `{"checks": [...]}` and exits 0 whatever the checks found.
- Produces, on the host:
  - `class Canary`, an async context manager. It is a TCP listener on `0.0.0.0:0`, with `port` and `received: list[bytes]`.
  - `host_addresses() -> list[str]`: non-loopback IPv4 addresses from `socket.gethostbyname_ex(socket.gethostname())`, plus the source address of a UDP socket connected to `192.0.2.1:9` (no packet is sent). An empty list is allowed.
  - `class IsolationProbeFailed(BenchmarkValidationError)`.
  - `probe_verdict(checks: Sequence[Mapping[str, Any]], *, canary_received: Sequence[bytes], token: str, proxy_events: Sequence[ProxyEvent], denied: tuple[str, int], canary: tuple[str, int]) -> list[str]`: the names of what failed. That covers each failed check, `canary_saw_token`, and `denial_not_logged` unless both the `CONNECT` to `denied` and the plain request to `canary` appear as not-allowed events. An empty list means pass.
  - `isolation_probe(sandbox, egress, *, job_id, family, system_id) -> dict`, the default `ProbeFn`:
    - It opens its own `Canary` and runs `python -m benchmarks agentic-probe` in a container on `egress.network`, built by `container_argv(..., interactive=False)` with the worker's non-secret env and no key.
    - `direct` targets: `host.docker.internal`, `gateway.docker.internal` and every `host_addresses()` entry, each on the canary port and on 11434.
    - It also sets `dns_name="pypi.org"`, `denied=["example.com", 443]`, the canary through the proxy, and the Ollama `/api/version` URL for an Ollama family.
    - Verdict: `probe_verdict(...)` over the checks, the canary's bytes and `parse_proxy_log(docker logs <proxy>)`.
    - The record holds `checks`, `failed`, `passed` and `proxy_lines`: the number of access-log lines the proxy had written when the verdict was taken. Every line up to there is probe traffic, so Task 10 leaves it out of the query-time evidence.
    - It writes `out_dir/probe/<job_id>.json`, then returns it or raises `IsolationProbeFailed` naming each failed check.
  - `ContainerLauncher` keeps each job's probe record, and whether the control probe and every job probe passed, for Task 10.
  - `control_probe(sandbox) -> dict`: with its own `Canary`, `run_control` in a container on the outside network must reach `host.docker.internal:<canary port>`. Otherwise it raises `IsolationProbeFailed("inconclusive: ...")`. `ContainerLauncher.prepare` runs it once, before the per-job probes, and writes `out_dir/probe/control.json`.

- [ ] **Step 1: Write the failing tests**

```python
def test_direct_check_fails_and_sends_the_token_when_something_listens() -> None
    # a listener on 127.0.0.1: passed False; the listener received b"Bearer <token>"
def test_direct_check_passes_when_nothing_listens() -> None
def test_dns_check(monkeypatch) -> None                       # getaddrinfo raises -> pass; returns -> fail
def test_proxy_checks_read_the_status_line() -> None          # a local fake proxy answering 403, then 200
def test_env_check_names_forbidden_variables_only(monkeypatch) -> None
    # HTTPS_PROXY=http://user:secret@x:1 -> passed False, "HTTPS_PROXY" in detail, "secret" not in detail
def test_verdict_fails_when_the_canary_saw_the_token() -> None             # probe_verdict -> ["canary_saw_token"]
def test_verdict_fails_when_the_denial_is_missing_from_the_proxy_log() -> None  # -> ["denial_not_logged"]
def test_verdict_needs_the_canary_denial_logged_too() -> None              # only example.com logged -> ["denial_not_logged"]
async def test_the_probe_record_counts_its_own_proxy_lines(tmp_path) -> None
    # FakeDocker "docker logs" returns 2 probe lines -> record["proxy_lines"] == 2
def test_verdict_passes_with_every_check_and_a_logged_denial() -> None     # -> []
async def test_a_failed_control_makes_the_probe_inconclusive(tmp_path) -> None
async def test_no_worker_starts_when_a_probe_fails(tmp_path) -> None
    # run_jobs with ContainerLauncher(FakeDocker, probe raising) -> IsolationProbeFailed;
    # no "agentic-worker" argv was ever run; close() ran
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --locked pytest tests/benchmarks/test_agentic_probe.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `probe.py`, the `agentic-probe` command and the `prepare` wiring**

The in-container part uses only the standard library and `egress.py`.

- [ ] **Step 4: Run the tests**

Run: `ISO=$(mktemp -d); uv run --locked env XDG_CACHE_HOME="$ISO" pytest tests/benchmarks/test_agentic_probe.py tests/benchmarks/test_agentic_sandbox.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/agentic/probe.py benchmarks/agentic/sandbox.py benchmarks/__main__.py tests/benchmarks/test_agentic_probe.py
git commit -m "feat(benchmarks): isolation probe gates every sandboxed job"
```

---

### Task 10: C1 evidence: `egress/summary.json`

PR 5 (#199, owner question 9, ruled by the owner) reads C1's evidence
through `load_egress_evidence(run_dir)`, and only in this shape. Anything
else makes C1 "not evaluable". This task writes exactly that shape and
nothing more.

**Files:**
- Modify: `benchmarks/agentic/egress.py` (`lookup_attempts`, `egress_summary`, `check_egress_summary`)
- Modify: `benchmarks/agentic/sandbox.py` (`ContainerLauncher.close` writes the summary)
- Test: `tests/benchmarks/test_agentic_egress.py`, `tests/benchmarks/test_agentic_sandbox.py`

**Interfaces:**
- Consumes: `ProxyEvent`, `parse_proxy_log` (Task 6); the probe records and `proxy_lines` (Task 9); `close_egress` and the `egress/<job>.access.log` files (Task 8); the run's `transcripts/` and `discarded/` records.
- The shape, bound by #199:

```json
{
  "jobs": {
    "claude-sonnet-5-5--python-docs": {
      "hosts_allowed": ["api.anthropic.com:443"],
      "hosts_denied": ["pypi.org:443"],
      "lookup_package_docs_attempts": 2
    }
  },
  "isolation_probe": "pass"
}
```

  - The top-level keys are exactly `jobs` and `isolation_probe`.
  - `jobs` is keyed by `job_id` (`<family id>--<system id>`), one entry per job that `prepare` opened an egress for. Each entry has exactly the three keys shown.
  - `hosts_allowed` and `hosts_denied` are sorted, unique `"host:port"` strings. They come from the job's query-time proxy events: every access-log line after the probe's `proxy_lines`, split by `ProxyEvent.allowed`. Probe traffic never appears.
  - `lookup_package_docs_attempts` is the number of `lookup_package_docs` tool calls the model made in that job. It counts every record under `transcripts/` and `discarded/` whose cell is `<system>--<family>--r<k>`.
  - `isolation_probe` is `"pass"` only when the control probe and every job's probe passed; otherwise `"fail"`, including when `prepare` stopped early.
- Produces in `egress.py`:
  - `lookup_attempts(out_dir: Path, family_id: str, system_id: str) -> int`.
  - `egress_summary(events: Mapping[str, Sequence[ProxyEvent]], attempts: Mapping[str, int], *, probe_passed: bool) -> dict[str, Any]`, keyed by job id.
  - `check_egress_summary(data: Any) -> None`: it raises `BenchmarkValidationError` when the value differs from the shape above in keys, types or ordering.
- `ContainerLauncher.close()` builds the summary after every `close_egress`, checks it with `check_egress_summary`, and writes `out_dir/egress/summary.json` with `_write_json` after a key scan. It does this on every exit once `prepare` has started. A run with the local launcher writes no summary, so PR 5 reports C1 as not evaluable.

- [ ] **Step 1: Write the failing tests**

```python
def test_egress_summary_has_exactly_the_bound_shape() -> None
    # two jobs with events -> check_egress_summary passes; json round-trip equals the input
@pytest.mark.parametrize("broken", [EXTRA_TOP_KEY, MISSING_JOB_KEY, UNSORTED_HOSTS, HOST_WITHOUT_PORT,
                                    ATTEMPTS_AS_STRING, PROBE_AS_BOOL])
def test_check_egress_summary_refuses_any_other_shape(broken) -> None
def test_probe_lines_never_reach_the_summary() -> None
    # access log: 2 probe lines (example.com denied, canary denied), then api.anthropic.com allowed
    # and pypi.org denied; proxy_lines=2 -> hosts_allowed ["api.anthropic.com:443"],
    # hosts_denied ["pypi.org:443"]
def test_lookup_attempts_count_scored_and_discarded_records(tmp_path) -> None
    # transcripts/python-docs--m--r1/EX-001.json with 2 lookup_package_docs calls, discarded/.../ with 1
    # -> 3; a docs-fetch cell of the same family is not counted
async def test_close_writes_the_summary_even_after_a_failed_probe(tmp_path) -> None
    # FakeDocker + a probe that fails on the second job: egress/summary.json exists,
    # isolation_probe == "fail", check_egress_summary passes
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --locked pytest tests/benchmarks/test_agentic_egress.py tests/benchmarks/test_agentic_sandbox.py -k "summary or lookup or probe_lines" -q`
Expected: FAIL (`egress_summary` missing).

- [ ] **Step 3: Implement the interfaces above**

- [ ] **Step 4: Run the tests**

Run: `ISO=$(mktemp -d); uv run --locked env XDG_CACHE_HOME="$ISO" pytest tests/benchmarks/test_agentic_egress.py tests/benchmarks/test_agentic_sandbox.py tests/benchmarks/test_agentic_probe.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/agentic/egress.py benchmarks/agentic/sandbox.py tests/benchmarks/
git commit -m "feat(benchmarks): write the C1 egress summary in the shape PR 5 reads"
```

---

### Task 11: Live-run safety, the full-run gate and the CLI

**Files:**
- Create: `benchmarks/agentic/live.py`
- Modify: `benchmarks/agentic/run.py` (`AgenticConfig.reference_run`, `run_agentic(..., confirm=...)`, the mixed-arm refusal)
- Modify: `benchmarks/__main__.py` (`agentic-run` flags)
- Test: `tests/benchmarks/test_agentic_live.py`, `tests/benchmarks/test_agentic_run.py`

**Interfaces:**
- Consumes: `ContainerLauncher`, `Sandbox`, `SandboxConfig` (Task 8); `PROVIDER_CAPS`, `PROVIDER_MARGINS`, `UNMETERED_PROVIDERS`.
- Produces in `live.py`:
  - `PILOT_TRAJECTORIES_PER_PROVIDER = 90` (design §12: 10 questions × 3 systems × k = 3), `SAFETY = Decimal("1.25")`, `WINDOW_S = 86_400`.
  - `@dataclass(frozen=True) class ReferenceRate: family_id: str; provider: str; trajectories: int; usd_per_trajectory: Decimal; seconds_per_trajectory: float`.
  - `reference_rates(run_dir: Path) -> dict[str, ReferenceRate]`, keyed by family id. Cost: the provider's booked spend in that run's `ledger-events.jsonl` (`settle` events) divided by the trajectories run for it, counting `transcripts/` and `discarded/` records. Time: mean of `timing.wall_ms − timing.grant_wait_ms` over its transcripts.
  - `@dataclass(frozen=True) class ProviderPlan: provider: str; trajectories: int; spent: Decimal | None; cap: Decimal | None; estimate: Decimal | None` (`None` money fields for an unmetered provider).
  - `provider_plans(jobs, spent: Mapping[str, Decimal], caps, rates: Mapping[str, ReferenceRate] | None) -> list[ProviderPlan]`.
  - `full_run_gate(plans, jobs, rates) -> list[str]`. A problem is returned when a metered provider plans more than `PILOT_TRAJECTORIES_PER_PROVIDER` trajectories without `rates`, when `estimate × SAFETY` is over `cap − spent`, or when a family's `trajectories × seconds_per_trajectory × SAFETY` is over `WINDOW_S`. The per-graded-answer term of design §12 arrives with grading in PR 5.
  - `confirmation_text(plans) -> str`. One line per provider: planned trajectories, spent, cap, remaining, the estimate or "no reference run", and "the ledger stops at the cap; one overrun is at most the $<margin> margin". An unmetered provider shows "local, unmetered".
  - `interactive_confirm(text: str, *, stdin: TextIO = sys.stdin, stderr: TextIO = sys.stderr, ask: Callable[[str], str] = input) -> bool`. It prints `text` to `stderr`, returns `False` when `stdin` is not a TTY, and returns `True` only for the exact answer `yes`.
- `run_agentic(config, *, launcher=None, caps=PROVIDER_CAPS, today=None, confirm: Callable[[str], bool] | None = None)`:
  - It refuses a plan that mixes the `air-gapped` arm with any other arm ("the air-gapped arm runs alone, with the host offline").
  - After the ledger opens and the overrun check passes, when any family has `api != "scripted"`: build `provider_plans`, run `full_run_gate` (problems raise), then require `confirm is not None and confirm(text)`. Otherwise raise `BenchmarkValidationError("live run not confirmed")`.
  - The summary records `live_confirmation: text`. A refusal ends as `status: "aborted"` through the existing `finally`.
- CLI `agentic-run` adds `--launcher {local,container}` (default `local`), `--image ID`, `--keys-file PATH`, `--index PATH` (default `get_index_path()`), `--live`, and `--reference-run DIR`.
  - `--live` and `--dry-run` are mutually exclusive, and `--live` with `--launcher local` is a parser error.
  - `--launcher container` without `--live` is a dry run.
  - The CLI computes `run_id` once and passes it to both `AgenticConfig` and `SandboxConfig`. It passes `confirm=interactive_confirm`.

- [ ] **Step 1: Write the failing tests**

```python
def test_reference_rates_from_a_finished_scripted_run(tmp_path) -> None
    # run the existing scripted e2e config, then reference_rates(out): one rate per family,
    # usd_per_trajectory == booked spend / trajectories, seconds_per_trajectory > 0
def test_the_gate_refuses_a_provider_whose_scaled_cost_exceeds_its_remaining_cap() -> None
def test_the_gate_refuses_a_model_whose_scaled_time_exceeds_24_hours() -> None
def test_a_live_run_larger_than_the_pilot_needs_a_reference_run() -> None   # 91 openai trajectories
def test_confirmation_lists_every_provider_with_its_remaining_cap_and_estimate() -> None
def test_interactive_confirm_refuses_without_a_terminal() -> None
def test_interactive_confirm_accepts_only_yes() -> None                    # "y", "YES ", "" -> False
def test_run_agentic_refuses_a_live_family_without_a_confirmation(tmp_path) -> None
    # launcher supporting live families, confirm=None: raises "not confirmed"; no worker command built;
    # run-summary.json status "aborted"
def test_a_declined_confirmation_starts_nothing(tmp_path) -> None
def test_run_agentic_refuses_mixing_the_air_gapped_and_hosted_arms(tmp_path) -> None
def test_cli_container_launcher_defaults_to_a_dry_run(tmp_path, monkeypatch) -> None
    # main([... "--launcher", "container", "--image", "sha256:" + "0" * 64]) prints dry_run true;
    # Sandbox is patched to fail on any docker call
def test_cli_refuses_live_with_the_local_launcher() -> None
```

The existing scripted end-to-end tests stay unchanged: a run of scripted
families never asks for confirmation.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --locked pytest tests/benchmarks/test_agentic_live.py tests/benchmarks/test_agentic_run.py -q`
Expected: FAIL (module missing; unknown arguments).

- [ ] **Step 3: Implement `live.py`, the `run_agentic` changes and the CLI flags**

- [ ] **Step 4: Run the agentic suite and the dry-run CLI**

Run: `ISO=$(mktemp -d); uv run --locked env XDG_CACHE_HOME="$ISO" pytest tests/benchmarks -q`
Expected: PASS.

Run: `ISO=$(mktemp -d); uv run --locked env XDG_CACHE_HOME="$ISO" python -m benchmarks agentic-run --corpus docs/benchmarks/corpus.yml --out "$ISO/run" --launcher container --image "sha256:$(printf '0%.0s' {1..64})" --models claude-sonnet-5-5 --questions EX-001 --repeats 1`
Expected: exit 0 and a summary with `"dry_run": true` and `"planned_cells": 3`, with no docker command run.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/agentic/live.py benchmarks/agentic/run.py benchmarks/__main__.py tests/benchmarks/
git commit -m "feat(benchmarks): dry run by default, confirmed live runs and the full-run gate"
```

---

### Task 12: The air-gapped arm's host checks

**Files:**
- Create: `benchmarks/agentic/airgap.py`
- Modify: `benchmarks/agentic/run.py` (preflight, per-trajectory checks, final check)
- Create: `tests/benchmarks/fixtures/agentic/model-matrix-local.yml` (one family, `scripted-local`: provider `ollama`, arm `air-gapped`, api `scripted`, `max_output_tokens: 1024`, `framing_allowance_tokens: 256`) and `script-local.yml`
- Test: `tests/benchmarks/test_agentic_airgap.py`

**Interfaces:**
- Produces:
  - `OUTSIDE_TARGETS = (("pypi.org", 443), ("1.1.1.1", 443))`, `REQUIRED_CONTEXT = 32_768`, `OLLAMA_LOCAL = "http://127.0.0.1:11434"`.
  - `default_routes(*, system: str | None = None, read: Callable[[], str] | None = None) -> list[str]`. On macOS it reads the lines of `netstat -rn` (IPv4 and IPv6) whose first column is `default`. On Linux it reads `/proc/net/route` lines with destination `00000000` and `/proc/net/ipv6_route` default entries.
  - `host_offline_checks(*, connect=socket.create_connection, routes=default_routes) -> list[dict]`. It returns `outside_connect:<host>` (each `OUTSIDE_TARGETS` connect, 5 s timeout, must raise) and `no_default_route` (detail: the offending lines). Each record carries `at` (UTC ISO) and `passed`.
  - `ollama_preflight(model_id: str, *, base: str = OLLAMA_LOCAL, fetch: Callable[[str, bytes | None], dict] | None = None) -> dict`. The default `fetch` uses a urllib opener built with `ProxyHandler({})`, so host proxy variables are ignored. It reads `/api/version`, then the model's `digest` and `details` from `/api/tags`, then loads the model (`POST /api/generate` `{"model", "prompt": "", "keep_alive": "60m"}`), then reads `/api/ps` `context_length` and `POST /api/show` `parameters`. It raises `BenchmarkValidationError` when the model is missing or `context_length != REQUIRED_CONTEXT`.
  - `class AirGapBroken(BenchmarkValidationError)`.
  - `class AirGapWatch(out_dir: Path, checks: Callable[[], list[dict]] = host_offline_checks)` with `async check(when: str) -> None`. It appends each record with `when` to `out_dir/airgap/records.jsonl`, and raises `AirGapBroken` naming the first failed check. It exposes `rounds: int` (the number of `check` calls made) and `broken: bool` for Task 13.
- `run_agentic(..., offline_checks=host_offline_checks, preflight=ollama_preflight)`. When the families are air-gapped and the run is not a dry run:
  - Before `run_jobs`, it writes `preflight(...)` for each air-gapped family to `out/ollama.json` (skipped for `api == "scripted"`) and runs `watch.check("before_first")`.
  - Each `trajectory` or `discarded` message from an air-gapped job runs `watch.check(f"after {cell}/{corpus_id}")` before the host acknowledges it, so before the worker starts its next trajectory.
  - After `run_jobs` it runs `watch.check("after_last")`.
  - The summary records `airgap: {"checks": <count>, "broken": <bool>}`. A broken arm aborts the run with the failed check's name (design §7.4: "The first failed check stops the arm").

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("system,text,expected", [
    ("Darwin", "Routing tables\n\nInternet:\nDestination Gateway Flags Netif\n127.0.0.1 127.0.0.1 UH lo0\n", []),
    ("Darwin", "Internet6:\ndefault fe80::%utun0 UGcIg utun0\n", ["default fe80::%utun0 UGcIg utun0"]),
    ("Linux", "Iface\tDestination\tGateway\neth0\t00000000\t0100A8C0\n", ["eth0\t00000000\t0100A8C0"]),
])
def test_default_routes_on_macos_and_linux(system, text, expected) -> None
def test_offline_checks_need_both_a_name_and_an_address_to_fail() -> None
    # connect raises for "pypi.org" but succeeds for "1.1.1.1" -> the 1.1.1.1 check fails
def test_ollama_preflight_requires_the_32768_context() -> None             # 4096 -> raises; 32768 -> record
def test_ollama_preflight_records_the_reported_digest() -> None
async def test_the_arm_checks_the_host_before_each_trajectory_and_after_the_last(tmp_path) -> None
    # scripted-local x no-docs, 2 questions x 1 repeat, offline_checks counting calls:
    # 1 + 2 + 1 = 4 checks; airgap/records.jsonl has 4 x (records per check) lines
async def test_the_first_failed_check_stops_the_arm(tmp_path) -> None
    # the 2nd check fails: status "aborted", abort_reason names "no_default_route", the last line failed
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --locked pytest tests/benchmarks/test_agentic_airgap.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `airgap.py` and the `run_agentic` hooks**

- [ ] **Step 4: Run the tests**

Run: `ISO=$(mktemp -d); uv run --locked env XDG_CACHE_HOME="$ISO" pytest tests/benchmarks -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/agentic/airgap.py benchmarks/agentic/run.py tests/benchmarks/
git commit -m "feat(benchmarks): air-gapped arm host checks and Ollama preflight"
```

---

### Task 13: C3 evidence: `airgap/checks.json`

PR 5 reads C3's offline evidence through `load_offline_evidence(run_dir)`.
It returns `all_passed` from this file, and it fails closed on any other
shape (#199, owner question 9). PR 5 reads `all_passed` alone, so that
one field must carry every condition design §2 sets for C3's offline
part.

**Files:**
- Modify: `benchmarks/agentic/airgap.py` (`airgap_evidence`, `check_airgap_evidence`)
- Modify: `benchmarks/agentic/run.py` (write the file at the end of an air-gapped live run)
- Test: `tests/benchmarks/test_agentic_airgap.py`

**Interfaces:**
- Consumes: `AirGapWatch.rounds` and `.broken` (Task 12); `out/egress/summary.json` (Task 10).
- The shape, bound by #199:

```json
{"all_passed": true, "checks": 302, "internet_hosts": []}
```

  - The keys are exactly these three.
  - `checks` is the number of check rounds run: before the first trajectory, after each reported trajectory or discard, and after the last.
  - `internet_hosts` lists sorted, unique `"host:port"` strings: every host in the air-gapped jobs' `hosts_allowed` and `hosts_denied` in `egress/summary.json`, except `host.docker.internal:11434`.
  - `all_passed` is `true` only when all of these hold: every round passed, the arm was not stopped, `egress/summary.json` exists with `isolation_probe` `"pass"`, and `internet_hosts` is empty. Design §2: "Proxy log shows zero internet hosts, every host-offline check (7.4) passes".
- Produces in `airgap.py`:
  - `airgap_evidence(*, rounds: int, broken: bool, egress: Mapping[str, Any] | None, job_ids: Sequence[str]) -> dict[str, Any]`.
  - `check_airgap_evidence(data: Any) -> None`: it raises `BenchmarkValidationError` on any other keys or types.
- `run_agentic` writes `out/airgap/checks.json` in its `finally`, after `run_jobs` and after `launcher.close` wrote the egress summary, for every air-gapped run that is not a dry run. An aborted arm writes `all_passed: false`.

- [ ] **Step 1: Write the failing tests**

```python
def test_airgap_evidence_has_exactly_the_bound_shape() -> None
@pytest.mark.parametrize("broken", [EXTRA_KEY, CHECKS_AS_STRING, HOSTS_UNSORTED, ALL_PASSED_AS_STRING])
def test_check_airgap_evidence_refuses_any_other_shape(broken) -> None
def test_an_internet_host_in_the_proxy_log_fails_the_evidence() -> None
    # egress jobs gemma4-12b--python-docs: hosts_denied ["pypi.org:443"] -> internet_hosts ["pypi.org:443"],
    # all_passed False
def test_the_ollama_host_is_not_an_internet_host() -> None
def test_missing_egress_evidence_fails_the_evidence() -> None              # egress None -> all_passed False
def test_a_failed_isolation_probe_fails_the_evidence() -> None
async def test_an_air_gapped_run_writes_checks_json_even_when_aborted(tmp_path) -> None
    # the Task 12 scripted-local run with a failing second round: airgap/checks.json exists,
    # all_passed False, checks == 2, check_airgap_evidence passes
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --locked pytest tests/benchmarks/test_agentic_airgap.py -k "evidence or checks_json or internet or ollama_host" -q`
Expected: FAIL (`airgap_evidence` missing).

- [ ] **Step 3: Implement the interfaces above**

- [ ] **Step 4: Run the tests**

Run: `ISO=$(mktemp -d); uv run --locked env XDG_CACHE_HOME="$ISO" pytest tests/benchmarks -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/agentic/airgap.py benchmarks/agentic/run.py tests/benchmarks/test_agentic_airgap.py
git commit -m "feat(benchmarks): write the C3 air-gap evidence in the shape PR 5 reads"
```

---

### Task 14: Keyed one-shot containers and the Sonnet 5.5 limits read (G1 prerequisite)

Design §7.3 says every paid call, judging and token counting included,
runs in a sandbox. PR 5's judges and counter need a way to run one keyed
command in a probed sandbox. That is `Sandbox.run_once`, and it lands with
its first caller: the harness plan's PR 4 item "reading Sonnet 5.5's exact
token limits from the Models API".

**Files:**
- Modify: `benchmarks/agentic/sandbox.py` (`run_once`)
- Create: `benchmarks/agentic/limits.py`
- Modify: `benchmarks/__main__.py` (`agentic-limits` on the host, internal `agentic-limits-worker`)
- Modify: `docs/benchmarks/model-matrix.yml` (the comment at line 65 names the command)
- Test: `tests/benchmarks/test_agentic_limits.py`

**Interfaces:**
- Consumes: `open_egress`, `close_egress`, `container_argv`, `read_key`, `isolation_probe`, `control_probe`, `allowlist`; `protect_output` and `read_frame`; `anthropic_sdk(..., proxy=...)`; `egress_proxy`.
- Produces: `Sandbox.run_once(family: ModelFamily, argv: Sequence[str], *, name: str, timeout_s: float) -> dict[str, Any]`.
  - It opens an egress with `allowlist(family, None)` and runs the control probe and the isolation probe.
  - It runs `docker run` without `-i`, with the family's one key as a bare `-e NAME` and the worker's non-secret env, and reads exactly one frame from stdout.
  - `assert_no_key_shaped` runs on it. It then closes the egress and the outside network, and returns the frame.
- Produces: `limits.limits_worker() -> int`. It runs `protect_output()` first, then `require_live_environment("anthropic")`, then `anthropic_sdk(key, 30, proxy=egress_proxy(os.environ)).models.retrieve(model_id)` (model from `argv`). It sends one frame `{"type": "model_limits", "id", "max_input_tokens", "max_tokens"}`. It makes exactly one HTTP request and no billable call.
- Produces: `limits.check_limits(reported: Mapping[str, Any], family: ModelFamily) -> list[str]`. It returns one line per difference: `max_input_tokens != family.max_input_tokens`, or `max_tokens < family.max_output_tokens`.
- CLI: `agentic-limits --image ID --keys-file PATH --out DIR --model claude-sonnet-5-5 --live`. Without `--live` it prints what it would run and exits 0. With `--live` it prints the frame and the differences, and exits 1 when there is any. It never edits the matrix: the maintainer corrects `model-matrix.yml` by hand in a docs PR.

- [ ] **Step 1: Write the failing tests**

```python
async def test_limits_worker_reads_the_models_api_once_through_the_egress(monkeypatch) -> None
    # MockTransport records exactly one GET .../v1/models/claude-sonnet-5-5; the frame carries the
    # two integers; with BENCHMARK_EGRESS_URL set the route is the proxy (Task 2 helper)
def test_check_limits_reports_each_difference() -> None
async def test_run_once_probes_its_egress_passes_one_key_and_reads_one_frame(tmp_path) -> None
    # FakeDocker: network/proxy/probe calls, then one run with bare "-e ANTHROPIC_API_KEY" and no "-i";
    # stdout = encode_frame({...}) -> returned; proxy closed afterwards
async def test_run_once_refuses_a_key_shaped_frame(tmp_path) -> None
def test_agentic_limits_without_live_runs_nothing(tmp_path, monkeypatch) -> None
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --locked pytest tests/benchmarks/test_agentic_limits.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement**

- [ ] **Step 4: Run the tests**

Run: `ISO=$(mktemp -d); uv run --locked env XDG_CACHE_HOME="$ISO" pytest tests/benchmarks -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/agentic/sandbox.py benchmarks/agentic/limits.py benchmarks/__main__.py docs/benchmarks/model-matrix.yml tests/benchmarks/test_agentic_limits.py
git commit -m "feat(benchmarks): keyed one-shot sandbox runs; read Sonnet 5.5 limits from the Models API"
```

---

### Task 15: Run script, runbooks, opt-in docker test, then the PR

**Files:**
- Create: `benchmarks/sandbox/run.sh` (executable)
- Create: `docs/benchmarks/SANDBOX-RUNBOOK.md`
- Modify: `README.md` (new `## Air-gapped install` section after `## Retrieved docs cache`)
- Create: `tests/benchmarks/test_agentic_sandbox_docker.py`
- Modify: `CHANGELOG.md` (`## [Unreleased]`)
- Modify: `tests/benchmarks/test_agentic_sandbox_files.py` (run.sh checks)

**Interfaces:**
- Consumes: everything above.
- `run.sh` is fixed copy:

```bash
#!/usr/bin/env bash
# Build the sandbox image from HEAD, pull the pinned proxy, then run agentic-run in containers.
# A dry run unless --live is passed. IMAGE=sha256:... skips the build (the air-gapped arm).
# KEYS_FILE (default .env.local) is read only by the container launcher, one key per container.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
for tool in git docker uv cat; do
  command -v "$tool" >/dev/null || { echo "run.sh: $tool not found" >&2; exit 2; }
done
[ -z "$(git status --porcelain)" ] || { echo "run.sh: commit first; the image is built from HEAD" >&2; exit 2; }
if [ -z "${IMAGE:-}" ]; then
  iid=$(mktemp)
  git archive --format=tar HEAD | docker build --file benchmarks/sandbox/Dockerfile \
    --label "org.opencontainers.image.revision=$(git rev-parse HEAD)" --iidfile "$iid" -
  IMAGE=$(cat "$iid"); rm -f "$iid"
  docker pull "$(uv run --locked python -c 'from benchmarks.agentic.sandbox import SQUID_IMAGE; print(SQUID_IMAGE)')"
fi
echo "run.sh: image $IMAGE" >&2
exec uv run --locked python -m benchmarks agentic-run --launcher container --image "$IMAGE" \
  --keys-file "${KEYS_FILE:-.env.local}" "$@"
```

- `SANDBOX-RUNBOOK.md` has these sections, each with its exact commands:
  1. **Prerequisites:** Docker Desktop ≥ 4.96.1 (Engine ≥ 28.0), context `desktop-linux`, no `proxies` key in `~/.docker/config.json`; the index built for 3.10–3.14 (CONTRIBUTING §3); a keys file with mode 600 outside any commit.
  2. **Dry run:** `benchmarks/sandbox/run.sh --corpus docs/benchmarks/corpus.yml --out <dir>`.
  3. **Keyless sandbox check:** `BENCHMARK_DOCKER_TESTS=1 BENCHMARK_SANDBOX_IMAGE=<id> uv run --locked pytest tests/benchmarks/test_agentic_sandbox_docker.py -q`.
  4. **Sonnet 5.5 limits:** `uv run --locked python -m benchmarks agentic-limits --image <id> --keys-file <file> --out <dir> --model claude-sonnet-5-5 --live`.
  5. **Smoke run** (design §14): one question, k = 1, every hosted model and system, `--live`.
  6. **Pilot:** the 10 fixed questions, k = 3, `--live`.
  7. **Full run:** `--reference-run <pilot dir> --live`, plus what to do when the gate refuses (fewer repeats or questions, chosen before running and disclosed).
  8. **Artifacts:** `egress/summary.json` (C1 evidence, read by PR 5), `egress/<job>.access.log` and `egress/<job>.squid.conf` (the raw logs and allowlists behind it), `probe/<job>.json`, `sandbox.json`, `airgap/checks.json` (C3 evidence, read by PR 5), `airgap/records.jsonl`, `ollama.json`.
  9. **Air-gapped arm**, in this order:
     - While online: install Ollama (the newest release ≥ 14 days old, and at least the version the owner rules on; see open question 6). Quit the menu-bar app, so no updater runs, and use the CLI only. `ollama pull gemma4:12b`. Build the image with `run.sh` and note its ID. Pull the Squid image.
     - Install the index by following README "Air-gapped install" literally.
     - Set Docker Desktop's memory limit to 4 GB.
     - Start `OLLAMA_CONTEXT_LENGTH=32768 OLLAMA_NO_CLOUD=1 ollama serve` in its own terminal.
     - Go offline: turn off Wi-Fi, unplug Ethernet, turn off VPNs and iCloud Private Relay (each can leave a `utun` default route).
     - First the 5-question local smoke run (design §14): `IMAGE=<id> benchmarks/sandbox/run.sh --models gemma4-12b --systems python-docs,no-docs --corpus docs/benchmarks/corpus.yml --questions <5 ids> --repeats 1 --out <dir> --live`. If tool calls fail often, stop and report (design §14 names `gemma4:e4b` or a stronger machine).
     - Then the arm: the same command with all 50 questions, k = 3, and a new `--out`.
     - If the probe's Ollama check fails, the fallback is `OLLAMA_HOST=0.0.0.0:11434` for this arm only, while the host is offline (open question 7).
  10. **Cleanup:** `docker ps -a --filter label=org.python-docs-mcp.benchmark-run=<run_id>` lists anything left; nothing should be.
- README `## Air-gapped install`, per design §11.4:
  - On a connected machine with the same OS, CPU architecture and Python minor version (or pip's `--platform`, `--python-version`, `--only-binary=:all:`), run `pip download python-docs-mcp-server==<version> -d wheels/` and `python-docs-mcp-server build-index --versions 3.10,3.11,3.12,3.13,3.14`.
  - Copy `wheels/` and `index.db` across.
  - On the target, run `pip install --no-index --find-links wheels/ python-docs-mcp-server==<version>`.
  - Place `index.db` at `~/Library/Caches/mcp-python-docs/index.db` (macOS) or `~/.cache/mcp-python-docs/index.db` (Linux, or `$XDG_CACHE_HOME/mcp-python-docs/`).
  - Set `PYTHON_DOCS_MCP_DISABLE_AUTO_INDEX=1` and run `python-docs-mcp-server doctor`.
  - `lookup_package_docs` needs PyPI and returns a clear note when offline. There is no new CLI command.
- The docker test module skips unless `BENCHMARK_DOCKER_TESTS=1` and `BENCHMARK_SANDBOX_IMAGE` is set. It uses scripted families only and no key.

- [ ] **Step 1: Write the failing tests**

`test_agentic_sandbox_files.py`:

```python
def test_run_sh_builds_from_git_archive_and_defaults_to_a_dry_run() -> None
    # mode has the executable bit; "git archive --format=tar HEAD" present; "--live" never appears in it;
    # "set -euo pipefail" present; no "printenv", "env |", "set -x" or "cat .env" in it
```

`test_agentic_sandbox_docker.py` (opt-in):

```python
def test_image_runs_unprivileged_with_cat_and_without_node(image) -> None
async def test_a_scripted_run_completes_inside_containers(tmp_path, image) -> None
    # run_agentic with ContainerLauncher, scripted-anthropic x {no-docs, python-docs}
    # (python-docs uses an index built with tests/conftest.py's stability builder in tmp_path):
    # status completed; every probe/<job>.json passed; egress/<job>.access.log holds a denied
    # "CONNECT example.com 443"; egress/summary.json passes check_egress_summary with
    # isolation_probe "pass" and empty host lists (probe lines excluded); the persistent docs
    # cache file was created on the tmpfs
async def test_a_trajectory_frame_over_100000_bytes_round_trips_through_a_container(tmp_path, image) -> None
async def test_a_docker_config_with_proxies_fails_the_isolation_probe(tmp_path, image, monkeypatch) -> None
    # DOCKER_CONFIG=<tmp dir> whose config.json sets proxies.default.httpsProxy (and a copy of the
    # current context): IsolationProbeFailed naming HTTPS_PROXY
async def test_no_container_or_network_with_the_run_label_remains(tmp_path, image) -> None
```

- [ ] **Step 2: Run the static tests to see them fail**

Run: `uv run --locked pytest tests/benchmarks/test_agentic_sandbox_files.py -q`
Expected: FAIL (no `run.sh`).

- [ ] **Step 3: Write `run.sh`, the runbook, the README section, the docker test and the CHANGELOG**

CHANGELOG under `## [Unreleased]`:
- **Added:** the benchmark sandbox (`benchmarks/sandbox/`), with one hardened container and one logging Squid egress proxy per (model, system); `--launcher container`, dry run by default, `--live` with confirmation, `--reference-run`; the Ollama client for the air-gapped arm; the "Air-gapped install" README section.
- **Changed:** `docs-fetch` and `python-docs` reach the network only through the run's egress proxy.

- [ ] **Step 4: Run the full gate, then the opt-in docker test once**

Run the three gate commands.
Expected: `All checks passed!`, `0 errors`, every test passing, and the docker module skipped.

Then run the docker test (needs Docker Desktop and the image from Task 5; no key):
`BENCHMARK_DOCKER_TESTS=1 BENCHMARK_SANDBOX_IMAGE=<id> uv run --locked pytest tests/benchmarks/test_agentic_sandbox_docker.py -q`
Expected: 5 passed. If the DNS check fails (sandbox note §B.3), stop and report to the owner (open question 5). Do not weaken the check.

Paste both outputs into the PR body.

- [ ] **Step 5: Commit, push, open the PR**

```bash
git add benchmarks/sandbox/run.sh docs/benchmarks/SANDBOX-RUNBOOK.md README.md CHANGELOG.md tests/benchmarks/
git commit -m "docs(benchmarks): sandbox run script, runbooks and the opt-in docker test"
git push -u origin feat/agentic-sandbox
```

Open the PR with `gh pr create` against `main`:
- **Title:** `feat: agentic benchmark sandbox, egress proxy, run script and air-gapped arm`.
- **Label:** `supervisor-review`.
- **Body:**
  - `Closes #198`, `Refs #63`;
  - each Global Constraint, checked;
  - the `shortcut:` table above;
  - the pins with their dates;
  - the intended test changes;
  - the gate output and the docker-test output;
  - the owner rulings applied.

---

## Not in this plan

- **PR 5 (#199):** the criteria file, the probes, the judge panel and the reference grader, run through `Sandbox.run_once`; batch units and `split_batch`; `scoring.py` provenance; post-hoc token counting in an Anthropic-key container; the C1 and C3 report sections, which read only `egress/summary.json` and `airgap/checks.json`; the per-graded-answer term of the full-run gate (from PR 5's `grading/summary.json`); grounding; the report sections.
- **Maintainer-run, after this PR merges, never by an implementer:** the Sonnet 5.5 limits read, the smoke run, G1, the pilot, the full run and the air-gapped arm. Their order is open question 4.
- **Round 2:** the Streamable HTTP transport, the competitor systems, and a fresh MCP session per cell for Ref.tools.
- **Not built:** Podman or Colima support; TLS inspection; a CI workflow for the docker test (workflows are forbidden territory); an `OUTPUT_CAP_STOPS` entry for Ollama's `length` (its stop reason is recorded per call; the 1% rule covers the hosted pilot only).

## Open questions (owner rulings needed)

1. **One PR or two?** Recommended: one PR (`feat/agentic-sandbox`, `supervisor-review`). The Ollama client and the egress plumbing can only be exercised end to end with the sandbox. The alternative is Tasks 1–4 first, then Tasks 5–15.
2. **Where `mcp-server-fetch` lives.** Recommended: a hashed requirements file used only in the image's `/opt/fetch` venv, with no change to `pyproject.toml` or `uv.lock`. Also recommended: add that file to the security workflow's `pip-audit` step. That is a workflow change the owner makes; until then, Task 4 audits it by hand.
3. **The proxy image.** Recommended: `ubuntu/squid:6.6-24.04_edge` by digest (Canonical, Docker Hub Verified Publisher, Ubuntu-patched Squid 6.6, pushed 2026-05-07). No Docker Official Squid image exists. Squid is chosen over tinyproxy because only Squid gives a per-connection log line with host, port and decision. The alternative is building Squid from Debian packages on the pinned Python base, which a version pin cannot keep reproducible.
4. **Order of the first live calls.** Ruling 8 says the first live call is G1. Design §§12 and 14 put a ~$1 smoke run, with the isolation probe, before the pilot. Recommended order:
   1. the keyless docker test (no key);
   2. the free Sonnet 5.5 limits read;
   3. the smoke run (~$1);
   4. G1 (PR 5);
   5. the pilot.
   G1's judges run in the same sandbox, so proving it with the least spend first protects G1's ~$4.
5. **External DNS inside workers.** Docker Desktop is "not affected" by the engine's internal-network DNS fix (sandbox note §B.3), so a worker may still resolve `pypi.org` through Desktop's resolver. Recommended: keep the probe strict, so a resolved name stops the run. The fallback order is (a) `--dns` pointing at an address with no resolver, re-probed, then (b) workers in a Linux VM, per design §7.3. A DNS query is an unlogged contact the proxy log cannot show.
6. **Ollama version.** v0.40.8 is the newest release at least 14 days old today, but its OpenAI endpoint overrides the model's packaged sampler defaults with 1.0. v0.40.14 (2026-11-03) stops that. Recommended: the air-gapped arm runs after the pilot, so require the newest release at least 14 days old on the run day, and no older than v0.40.14 (which qualifies from 2026-11-17). The preflight records `/api/version` and `/api/show` parameters. No sampling parameter is ever sent.
7. **Reaching Ollama from the proxy.** Whether `host.docker.internal` reaches a service bound to the Mac's 127.0.0.1 is not documented. Recommended: keep Ollama's default bind. If the probe's Ollama check fails, set `OLLAMA_HOST=0.0.0.0:11434` for the air-gapped arm only, while the host is offline.
8. **Live confirmation without a reference run.** Recommended: without `--reference-run`, the prompt shows each provider's remaining cap as the worst case and no estimate. A live run planning more than 90 metered trajectories for one provider (more than the pilot) is refused without `--reference-run`, which also applies the design §12 gate.
9. **The keys file.** Recommended: `--keys-file`, default `.env.local` (gitignored, never in the image, because the build context is `git archive HEAD`). The launcher reads only the one name it needs, at container start, and refuses a group- or world-readable file. The value reaches the docker CLI child's environment and the container through a bare `-e NAME`, never argv. Design §7.3 says `--env-file`; a per-container file would have to be written to disk, so this is a deliberate change. `docker inspect` shows a container's environment while it runs; every container runs with `--rm`.
10. **Container runtime.** Recommended: Docker Desktop only for round 1 (Engine ≥ 28.0, for the isolated gateway mode). Colima is installed on the machine but is not the active context, and its host access is documented only in an issue thread.
11. **What the bound `egress/summary.json` fields mean.** The #199 shape fixes the keys but not three meanings. Task 10 reads them as follows, with no extra key:
    - each host is a `"host:port"` string;
    - the lists cover query time only, with the isolation probe's own lines left out;
    - `pypi.org:443` stays in `hosts_denied` when a `lookup_package_docs` call reached the proxy, and `lookup_package_docs_attempts` counts the model's calls of that tool from the transcripts.
    Recommended: PR 5 treats `pypi.org:443` as design C1's `lookup_package_docs` exception only when the attempts count is above 0. If the owner wants the PyPI denials kept out of `hosts_denied`, or a separate count of proxy-level PyPI attempts, that is a new field in both plans.
12. **What `airgap/checks.json`'s `all_passed` covers.** PR 5 reads `all_passed` alone, so Task 13 sets it only when four things hold: every host-offline round passed, the arm was not stopped, the egress summary exists with a passing probe, and `internet_hosts` is empty. `checks` counts rounds, not individual records. Recommended: accept. The alternative is a PR 5 loader that also checks `internet_hosts`, which changes #199.
