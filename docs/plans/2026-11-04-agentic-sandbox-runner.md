# Agentic Benchmark: Sandbox, Egress Proxy, Run Script and Air-Gapped Arm Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land design §12 step (4). Every live worker runs in its own
hardened container behind its own logging egress proxy, with a run
script, live-call safety, the Ollama client and the air-gapped runbook.
The maintainer can then run the smoke run, G1 and the pilot with keys
that live only in host memory and in the one worker process that needs
them.

**Architecture:** One PR, `feat/agentic-sandbox`.
- `ContainerLauncher` implements the existing `Launcher` seam in
  `benchmarks/agentic/host.py`. The host stays keyless in its
  environment.
- Per (model, system) job, the launcher creates:
  - one `--internal`, IPv4-only Docker network with an isolated gateway;
  - one Squid proxy aliased `egress` on it, which logs to a bind-mounted
    file;
  - one hardened worker container, started with no key in its
    environment.
- The key reaches the worker only as a bootstrap frame on its stdin. The
  worker reads that frame before it protects its output.
- A keyless isolation probe runs on every job's network before any
  worker starts. Teardown removes the worker container before its
  in-flight unit is settled.
- SDK clients take the proxy explicitly from `BENCHMARK_EGRESS_URL`; MCP
  servers get `HTTPS_PROXY`/`HTTP_PROXY` from the manifest's `{egress}`
  placeholder. No `*_PROXY` variable ever reaches a worker's own
  environment.
- Live safety:
  - `agentic-run --launcher container` is a dry run unless `--live` is
    given.
  - Every live entry point passes one authorization gate before any key
    is read or any keyed container starts: it refuses under `CI`, then
    requires an interactive confirmation of every provider's remaining
    cap and estimate.
- The air-gapped arm adds the Ollama client, an unmetered ledger path,
  host offline checks around every trajectory, and two runbooks.
- The run writes the C1 and C3 evidence files in the shapes PR 5 (#199)
  reads.

**Tech Stack:**
- Python ≥ 3.12 on the host; the worker image runs CPython 3.14.8.
- Docker Desktop for Mac ≥ 4.96.1 (Engine ≥ 28.0, for the isolated
  gateway mode); Squid 6.6 (`ubuntu/squid`); uv 0.13.4; `mcp-server-fetch`
  2026.10.19 in its own hashed venv inside the image.
- `openai` 3.32.0 (already in the dev group) for Ollama's
  OpenAI-compatible endpoint. No new Python dependency.
- pytest (`asyncio_mode = "auto"`), Ruff (line length 100), Pyright.

**Spec:**
- `docs/benchmarks/AGENTIC-BENCHMARK-DESIGN.md` §§2, 5.1, 6, 7.1–7.4,
  11.4, 12, 13, 14;
- the "Not in this plan → PR 4" list in
  `docs/plans/2026-10-31-agentic-benchmark-harness.md`;
- the owner rulings on #184 (part 1 items 6, 8 and 9) and the
  verification record on #197 (its "PR 4 requirements recorded in
  `shortcut:` comments" and "Deferred to #198");
- the owner rulings on #200 (round 1) and the round-2 amendments (A1–E19)
  relayed on #200; where the two differ, round 2 wins. "Owner rulings
  (applied)" at the end lists the result;
- issue #198;
- the PR 5 plan (#199, `docs/plans/2026-11-04-agentic-grading-report.md`),
  whose owner-ruled question 9 binds the shapes of `egress/summary.json`
  and `airgap/checks.json`;
- `docs/research/2026-11-04-benchmark-sandbox-tooling.md` (the "sandbox
  note"), the source of every pin and every Docker, Squid and Ollama fact
  here, and `docs/research/2026-10-31-benchmark-provider-limits.md`.

Executors read the design §7, the sandbox note and
`benchmarks/agentic/host.py` in full before Task 1.

## Global Constraints

- **Branch:** `feat/agentic-sandbox` from `origin/main` at `9bb2fa0` (#197) or later. Refs #63; closes #198. PR 4 merges before PR 5 (#199), which rebases on it.
- **Commit order:** one commit per task, in task order. Tasks 1–6 need no Docker, so CI alone proves that half before the sandbox tasks.
- **No live call during implementation or CI.**
  - Tests use fakes: `FakeDocker` (a recorded `DockerRun`), `httpx2.MockTransport`/`httpx.MockTransport`, and scripted models. No test reaches a network host.
  - The docker test (Task 16) is opt-in (`BENCHMARK_DOCKER_TESTS=1`), keyless (scripted models only) and never runs in CI.
  - No implementer passes `--live` with a real key, sets a real key, reads `.env.local`, or runs a provider call.
- **Keys** (round-2 ruling A1, replacing #200 ruling 9):
  - The host process never holds a key in `os.environ`; `refuse_host_keys` is unchanged and still runs first.
  - A worker container starts with no key in its environment. A key is never in argv, the docker CLI child's environment, the container config (`docker inspect`), a log, an artifact or the image.
  - After the authorization gate and after the probes, `ContainerLauncher.bootstrap_frame(job)` reads the one needed key from the keys file. The host writes it as a `{"type": "bootstrap", "env": {...}}` frame on the worker's stdin, before the job frame.
  - The worker reads that frame before `protect_output` and before importing any SDK, puts the key in its own process environment in memory, and never writes it anywhere.
  - Each live worker holds at most its own provider's key; Ollama and scripted workers hold none.
- **Worker environment allowlist.** These are the only `-e` names a worker container gets: `HOME`, `XDG_CACHE_HOME`, `PYTHON_DOCS_MCP_DISABLE_AUTO_INDEX`, `BENCHMARK_EGRESS_URL`, `BENCHMARK_LIVE_PROVIDERS_ENABLED`, `OLLAMA_BASE_URL`. Never a key, and never a `*_PROXY`, `SSL_CERT_*` or `REQUESTS_CA_BUNDLE` variable. The docker CLI child's environment carries only `PATH`, `HOME`, `DOCKER_HOST`, `DOCKER_CONTEXT`, `DOCKER_CONFIG`, `DOCKER_CERT_PATH` and `DOCKER_TLS_VERIFY`, each when set.
- **Container hardening (design §7.2):**
  - `--read-only`, with tmpfs for `/tmp` (which is `HOME`) and for `/cache/mcp-python-docs`;
  - `--cap-drop ALL`, `--security-opt no-new-privileges`, `--user 10001:10001`;
  - `--memory 1g --cpus 1 --pids-limit 256`;
  - `--init`, `--rm`, `-i` without `-t`, `--pull never`, `--log-driver none` (the daemon keeps no second copy of worker output; round-3 item 7);
  - no Docker socket, and the image by ID (`sha256:<64 hex>`), never by tag;
  - `XDG_CACHE_HOME=/cache`; `index.db` bind-mounted read-only at `/cache/mcp-python-docs/index.db` for `python-docs` jobs only; `PYTHON_DOCS_MCP_DISABLE_AUTO_INDEX=1` always.
- **Egress (design §7.3):**
  - One Squid per job, on that job's internal network (`--internal --ipv6=false -o com.docker.network.bridge.gateway_mode_ipv4=isolated`) under the alias `egress`, and on one shared outside network per run.
  - Allowlist: the job's provider API host on 443 (none for scripted), plus `docs.python.org:443` for `docs-fetch`. The air-gapped arm allows only `host.docker.internal:11434`. Every allowlist ACL is `dstdomain -n`. No TLS interception.
  - Squid writes its access log to a bind-mounted file with no rotation: one line per connection attempt (time, client, method, host, port, status, Squid result).
- **Evidence for PR 5** (#199, owner question 9):
  - `<run>/egress/summary.json` is `{"jobs": {job_id: {"hosts_allowed": [...], "hosts_denied": [...], "lookup_package_docs_attempts": n}}, "isolation_probe": "pass"|"fail"}`.
  - `<run>/airgap/checks.json` is `{"all_passed": bool, "checks": n, "internet_hosts": [...]}`.
  - Exactly these keys. Tasks 12 and 15 write them, each guarded by one literal key-set assertion. A field this plan would want beyond them is an open question, never an extra key.
- **Authorization gate** (round-2 ruling D9): `live.authorize_live` is the one host-side check for every live entry point. It runs before any key is read and before any keyed container starts. It refuses when `CI` is set to a truthy value, and it requires an interactive `yes`.
- **One free request outside the ledger:** at startup, a live Anthropic or Gemini worker makes one models-retrieve GET (free, not billable) and reports `model_limits` (round-2 ruling D10). Every billable call keeps exactly one request per reserved unit.
- **Pins** (sandbox note §A), unchanged without an owner ruling:
  - `python:3.14.8-slim-trixie@sha256:fbecd461fc72d21b2143c6eaba25143738079154a6d0ea3da3aec724f50eed4a`
  - `ghcr.io/astral-sh/uv:0.13.4@sha256:9f130421e930bfbdc563c00740466603f8723158e90297d07957c2294640262b`
  - `ubuntu/squid:6.6-24.04_edge@sha256:8a3baed477e2c282ab8aa5edad442f69873246964f225c5c2ae8364b6610963c`
  - `mcp-server-fetch==2026.10.19`, every transitive package hash-pinned and published on or before 2026-10-21.
  - The two young pins (the Python image and `mcp-server-fetch`) are re-checked at implementation time (#200 ruling). Before Task 7, the implementer also checks https://www.python.org/downloads/ for a CPython 3.14.9+ security fix. If one exists and is at least 14 days old, they raise it in the PR body instead of switching silently.
- **Platform:** Docker Desktop for Mac only (context `desktop-linux`), Engine ≥ 28.0. Podman and Colima are out of scope for round 1.
- **Local daemon only** (round-3 item 4): `verify_image` refuses unless the active context's endpoint and any `DOCKER_HOST` are `unix://`. A bootstrap frame therefore never crosses a network to a remote daemon.
- **Dependencies:** no change to `pyproject.toml` or `uv.lock`. `mcp-server-fetch` lives only in the image's `/opt/fetch` venv, from a hashed requirements file.
- **Forbidden territory** (`AGENT-EXECUTION-PIPELINE.md` §2):
  - No change under `src/` (the tool surface is frozen) or to the corpus (`c52c3f2`).
  - The one workflow change is owner-approved (#200 ruling 2): `.github/workflows/security.yml`'s "Audit dependencies" step also audits `benchmarks/sandbox/fetch-requirements.txt` (Task 4).
  - The README change stays below the first install block.
- **Shared files with PR 5:** PR 4 merges first, and PR 5 also edits `host.py` and `worker.py`. PR 4's edits there stay additive at the launcher seam:
  - `host.py`: four new `Launcher` methods; `LocalLauncher` no-ops; in `_supervise`, writing the bootstrap frame, passing `stop_worker`, and passing `job_id=job.job_id` to `ledger.reserve`; one defaulted `_teardown` argument; the `prepare`/`close` calls around the gather in `run_jobs`. The rest is two rewritten comments and one message tail.
  - `ledger.py`: `Ledger.reserve` and `LedgerService.reserve` gain a defaulted `job_id: str | None = None`, written as the `job_id` field of every `reserve` event (round-3 item 2).
  - `worker.py`: `main_worker` reads the bootstrap frame first. `_serve`, `run_worker` and `_run_job` gain defaulted parameters. `_converted` gains one branch, and `_system_info` gains two keys.
  - `protocol.py`: one new function, `read_frame_fd`.
  - Nothing else in these files moves or is renamed.
- **Unchanged:** budget caps (Anthropic $180, OpenAI $65, Google $180); the loop caps (8 model calls, 12 tool calls, 30 s per tool call, 300 s / 900 s per trajectory, 100,000 result bytes, k = 3); and the price snapshot expiry (2026-12-31).
- **Gate** (`AGENTS.md`), run fresh before the PR, every pytest run with a throwaway cache:
  - `uv run --locked ruff check src/ tests/ benchmarks/ ops/ .github/scripts/`
  - `uv run --locked pyright src/ benchmarks/`
  - `ISO=$(mktemp -d); uv run --locked env XDG_CACHE_HOME="$ISO" pytest --tb=short -q`
  - Tests of non-CI behaviour `monkeypatch.delenv("CI", raising=False)`, because GitHub's runners set `CI=true`.
- **TDD** (superpowers:test-driven-development): every new behaviour has a failing test first.
- **Existing tests:** none is weakened. Only these change, and the PR body lists them:
  - Task 1: the two signal-warning tests and the HTTP-date test (#198).
  - Task 3: `test_make_client_refuses_the_ollama_api_until_pr_4` is replaced by `test_make_client_builds_the_ollama_client_through_the_guard`, because PR 4 delivers that client by design.
  - Task 4: the `docs-fetch` `pass_env` assertion in `tests/benchmarks/test_agentic_bridge.py` follows the manifest change.
- **Supervisor review:** the diff passes 500 lines, changes a workflow and replaces a test. Label the PR `supervisor-review`.
- **Commits and PRs:** conventional prefixes; every commit ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. The PR body follows `.github/PULL_REQUEST_TEMPLATE/agent.md` and ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`. Implementers never self-verify and never merge.

## Review Focus

Items 1–9 map to the round-2 amendments A1–D9. Item 10 is a failure mode
the spec implies and no other task pins.

1. **The key leaks through Docker.** A key passed through `-e` is visible in `docker inspect` and in the docker CLI's `ps -E`. Tests:
   - Task 10 `test_no_key_in_argv_cli_env_or_container_config`;
   - Task 5 `test_host_environ_is_unchanged_after_a_launch`, `test_bootstrap_key_is_redacted_from_worker_output` and `test_worker_refuses_a_live_job_without_its_bootstrap`.
2. **A failing container removal skips the log scrub.** Test: Task 5 `test_a_failing_stop_worker_still_scrubs_the_log_and_settles_the_unit`.
3. **Squid reverse DNS lets an IP literal through.** A CONNECT to an IP whose PTR names an allowlisted host would match a plain `dstdomain`. Tests:
   - Task 9 `test_every_allowlist_acl_disables_lookups`;
   - Task 16 docker test `test_an_ip_literal_connect_whose_ptr_names_an_allowlisted_host_is_denied`.
4. **An IPv6 or IPAM gateway stays reachable.** Tests:
   - Task 10 `test_internal_networks_are_ipv4_only_with_an_isolated_gateway`;
   - Task 11 `test_the_probe_targets_every_ipam_gateway_and_records_the_inspect`.
5. **The proxy log loses lines or gains a stale prefix.** Tests:
   - Task 9 `test_squid_conf_matches_the_template` (file log, no rotation);
   - Task 12 `test_a_truncated_or_reprefixed_log_invalidates_the_evidence` and `test_a_missing_log_invalidates_the_evidence`.
6. **A false clean summary.** Log collection failed, the proxy ended abnormally (exit 137, OOM), or a hosted job billed `ok` calls but its log shows no provider host, including a job whose worker died before reporting the trajectory. Tests:
   - Task 10 `test_an_abnormal_proxy_exit_fails_the_collection`;
   - Task 12 `test_no_false_clean_when_collection_failed_or_the_provider_never_appears`, `test_an_ok_settle_from_a_worker_that_died_early_fails_the_evidence` and `test_an_unattributed_ok_settle_fails_the_evidence`.
7. **A denied attempt counted as an internet host.** Test: Task 15 `test_a_denied_pypi_attempt_keeps_all_passed_true`.
8. **A retry-heavy pilot halves the estimate.** Test: Task 13 `test_reference_rates_aggregate_every_attempt_of_a_cell` (90 completed, 90 discarded, $9 booked gives $0.10 per cell).
9. **A live run starts under CI or without a terminal.** Tests: Task 13 `test_ci_refuses_a_live_run_even_with_a_terminal` and `test_a_non_tty_stdin_refuses_a_live_run`.
10. **An air-gap check that passes while the Mac is still online.** DNS is down but IP works, or an IPv6 `utun` default route remains. Tests: Task 14 `test_offline_checks_need_both_a_name_and_an_address_to_fail` and `test_default_routes_on_macos_and_linux`.

## File map

| File | Task | Responsibility |
|---|---|---|
| `tests/benchmarks/test_agentic_run.py`, `test_agentic_clients.py` | 1 | #198 test fixes |
| `benchmarks/adapters/guard.py` | 2, 3 | `EGRESS_URL_ENV`; the Ollama latch |
| `benchmarks/agentic/clients.py` | 2, 3, 6 | explicit egress proxy; `OllamaChatClient`; `fetch_model_limits` |
| `benchmarks/agentic/schemas.py`, `usage.py`, `ledger.py` | 3 | Ollama schema, buckets, unmetered units |
| `benchmarks/agentic/bridge.py`, `docs/benchmarks/agentic-manifest.yml`, `benchmarks/sandbox/fetch-requirements.txt`, `.github/workflows/security.yml` | 4 | `{egress}` for MCP servers; the `docs-fetch` pin and its audit |
| `benchmarks/agentic/protocol.py`, `host.py`, `worker.py`, `ledger.py` | 5 | the launcher seam: key bootstrap, prepare, stop worker, close; `job_id` on `reserve` events |
| `benchmarks/agentic/worker.py`, `run.py` | 6 | `model_limits` at startup and the host's comparison |
| `benchmarks/sandbox/Dockerfile` | 7 | the worker image |
| `benchmarks/agentic/egress.py` | 8, 9, 12 | allowlists, Squid config, log parser, forbidden env names; `egress/summary.json` |
| `benchmarks/agentic/sandbox.py` | 10 | docker runner, keys file, networks, proxies, `ContainerLauncher` |
| `benchmarks/agentic/probe.py` | 11 | isolation probe, in container and on the host |
| `benchmarks/agentic/live.py`, `run.py`, `benchmarks/__main__.py` | 13 | authorization gate, reference rates, full-run gate, CLI |
| `benchmarks/agentic/airgap.py`, `run.py` | 14, 15 | host offline checks, Ollama preflight; `airgap/checks.json` |
| `benchmarks/sandbox/run.sh`, `docs/benchmarks/SANDBOX-RUNBOOK.md`, `README.md`, `CHANGELOG.md` | 16 | operator flow and runbooks |

The tasks, in order:
1. #198 fixes
2. egress proxy in the clients
3. Ollama client
4. MCP servers, the fetch pin and its audit
5. launcher seam and key bootstrap
6. model limits
7. image
8. egress policy
9. Squid config
10. `ContainerLauncher`
11. isolation probe
12. C1 evidence
13. authorization, gate and CLI
14. air-gap checks
15. C3 evidence
16. operator flow and PR

## `shortcut:` comments this PR must meet

`grep -rn "shortcut:" benchmarks/agentic` on `9bb2fa0` finds five. Three name a PR 4 requirement:

| Comment | Requirement | Met by |
|---|---|---|
| `host.py:95` | `cat` on `PATH` for the anchor; "PR 4's worker image must too" | Task 7 (the Dockerfile asserts `command -v cat`), Task 16 (`run.sh` checks the host's `cat`); Task 5 rewrites the comment |
| `host.py:110` | the whole-group kill guarantee, "which removing the container does, and signal only what it holds" | Task 5 (`stop_worker` before settle), Task 10 (`docker rm -f` of its own container name only); Task 5 rewrites the comment |
| `clients.py:117–119` | an explicit egress proxy (`proxy=...`), and a worker env allowlist with no `*_PROXY`, `SSL_CERT_*` or `REQUESTS_CA_BUNDLE` | Task 2 (explicit proxy), Task 10 (allowlist), Task 11 (in-container env check); Task 2 rewrites the comment |

The other two still hold and stay as they are: `worker.py:113` (fd redirection) and `ledger.py:253` (`fcntl`) are POSIX only, and the worker runs on Linux in the container while the host runs on macOS. The #197 record also lists "a fresh MCP session per cell for Ref.tools", which is round 2.

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
- Produces: `clients.egress_proxy(environ: Mapping[str, str]) -> str | None`. It returns the value when set. It refuses with `BenchmarkValidationError` naming `BENCHMARK_EGRESS_URL` any value that is not `http://<host>:<port>`: no user info, path, query or fragment, and scheme `http` only.
- Produces: `anthropic_sdk(api_key, timeout_s, *, transport=None, proxy: str | None = None)`, and the same `proxy` keyword on `openai_sdk` and `gemini_sdk`.
  - With `proxy` set, every route (`http://`, `https://`, `all://`) goes through a transport built with that proxy and the same pinned trust store.
  - An injected `transport` (tests only) still carries every request.
  - Without `proxy`, behaviour is exactly as today.
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
`SSL_CERT_*` or `REQUESTS_CA_BUNDLE` (Task 10)."

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
- Produces: `guard.LIVE_LATCH_ENV = {"ollama": "OLLAMA_BASE_URL"}`. `require_live_environment("ollama")` needs the live flag and a non-empty `OLLAMA_BASE_URL` (#184 ruling 9). It never reads a key, and `PROVIDER_API_KEY_ENV` does not change.
- Produces: `schemas.to_openai_chat(tools) -> list[dict]`, each item `{"type": "function", "function": {"name", "description", "parameters", "strict": False}}`.
- Produces: `usage.ollama_buckets(usage) -> Buckets`, with `uncached = prompt_tokens − cached`, `cached = prompt_tokens_details.cached_tokens` (0 when the details or the count are absent), `output = completion_tokens`, and `reasoning = 0`.
- Produces: `clients.ollama_sdk(base_url: str, timeout_s: float, *, transport=None, proxy=None) -> openai.AsyncOpenAI`, with `api_key="ollama"` (ignored by Ollama, sandbox note §D.1) and `max_retries=0`.
- Produces: `clients.OllamaChatClient(sdk, model_id)`, with `provider = "ollama"`.
  - It sends `model`, `messages` and, when tools exist, `tools`. It never sends `max_tokens`, `tool_choice` (unsupported, §D.1; auto is Ollama's only behaviour) or a sampling parameter (design §7.1, "Ollama defaults").
  - `request_params()` returns `{"model": model_id}`.
  - Messages: `system`, `user`, the assistant replay, and `{"role": "tool", "tool_call_id", "content"}`.
  - A tool call without an id gets `call_<n>`. That id is written into the replayed assistant message too, so each result pairs with its call.
  - `stop_reason` is `choices[0].finish_reason`; `reported_model` is `response.model`.
- Produces: `ledger.UNMETERED_PROVIDERS = frozenset({"ollama"})`.
  - For these providers, `LedgerService.reserve` takes the provider lock (one unit in flight) and returns `f"{provider}-{uuid4().hex}"` without touching `Ledger`. `settle` releases the lock.
  - `Ledger.reserve` still refuses any provider without a cap.
- `make_client`: `openai-chat-completions` maps to provider `ollama`. It calls `require_live_environment("ollama")`, refuses when any of `PAID_KEY_ENVS` is set, and needs `OLLAMA_BASE_URL` to start with `http://` or `https://`.
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

def test_ollama_buckets_without_cache_details() -> None
    assert ollama_buckets({"prompt_tokens": 120, "completion_tokens": 30}) == Buckets(120, 0, 0, 30, 0)

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
Expected: FAIL, with import errors (`to_openai_chat`, `ollama_buckets`, `OllamaChatClient`), a `BenchmarkValidationError` from the price lookup, and the expiry error.

- [ ] **Step 3: Implement the interfaces above**

Delete `test_make_client_refuses_the_ollama_api_until_pr_4` in the same
commit. Change `make_client`'s "arrives with the Ollama client in PR 4"
message so it names the unknown api only.

- [ ] **Step 4: Run the agentic suite**

Run: `ISO=$(mktemp -d); uv run --locked env XDG_CACHE_HOME="$ISO" pytest tests/benchmarks -q`
Expected: PASS, with `test_provider_without_a_cap_is_refused[ollama]` still green (it tests `Ledger`, not `LedgerService`).

- [ ] **Step 5: Commit**

```bash
git add benchmarks/ docs/benchmarks/model-matrix.yml tests/benchmarks/
git commit -m "feat(benchmarks): Ollama chat client, unmetered ledger units and the base-URL latch"
```

---

### Task 4: MCP servers through the egress proxy, the `docs-fetch` pin and its audit

**Files:**
- Modify: `benchmarks/agentic/bridge.py` (`EGRESS_PLACEHOLDER`, `server_env`, `open_bridge`)
- Modify: `docs/benchmarks/agentic-manifest.yml`
- Modify: `benchmarks/agentic/worker.py` (`_system_info` adds `fetch_extraction`)
- Create: `benchmarks/sandbox/fetch-requirements.txt`
- Modify: `.github/workflows/security.yml` ("Audit dependencies" step; owner-approved, #200 ruling 2)
- Test: `tests/benchmarks/test_agentic_bridge.py`, `tests/benchmarks/test_agentic_host.py`, `tests/benchmarks/test_agentic_sandbox_files.py` (new), `tests/test_ci_workflows.py`

**Interfaces:**
- Consumes: `guard.EGRESS_URL_ENV` (Task 2).
- Produces: `bridge.EGRESS_PLACEHOLDER = "{egress}"`.
- Produces: `bridge.server_env(system: SystemSpec, environ: Mapping[str, str]) -> dict[str, str]`. It starts from `system.env`. A value equal to `"{egress}"` becomes `environ[EGRESS_URL_ENV]`, and the variable is dropped when that is unset. Then it copies the `pass_env` names present in `environ`. `open_bridge` calls it with `os.environ`.
- Manifest:
  - `python-docs`: `env` adds `HTTPS_PROXY: "{egress}"` and `HTTP_PROXY: "{egress}"`, so a `lookup_package_docs` PyPI request reaches the proxy and is logged as denied (design §5, C1).
  - `docs-fetch`: `env` is the same two, and `pass_env: [HOME]`. Its comment names `mcp-server-fetch` 2026.10.19 and `benchmarks/sandbox/fetch-requirements.txt`.
- `_system_info` adds `"fetch_extraction"`: for `docs-fetch`, `"readability-js"` when `shutil.which("node")` is set, else `"pure-python"`; `None` for other systems (design §5.1).
- `fetch-requirements.txt` is generated, never hand-written:

```bash
echo "mcp-server-fetch==2026.10.19" | uv pip compile - --universal --python-version 3.14 \
  --generate-hashes --exclude-newer 2026-10-22T00:00:00Z -o benchmarks/sandbox/fetch-requirements.txt
```

- `security.yml`: the "Audit dependencies" step's `uvx pip-audit` call gains `--requirement benchmarks/sandbox/fetch-requirements.txt` beside `--requirement requirements-audit.txt`. Its other flags are unchanged.

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

# tests/test_ci_workflows.py
def test_security_audit_covers_the_sandbox_fetch_requirements() -> None
    # security.yml, job dependency-audit, step "Audit dependencies": its run text contains
    # "--requirement benchmarks/sandbox/fetch-requirements.txt" and "--requirement requirements-audit.txt"
```

Update the existing `docs-fetch` assertion in `test_agentic_bridge.py`
to `pass_env == ("HOME",)` and the new `env`.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --locked pytest tests/benchmarks/test_agentic_bridge.py tests/benchmarks/test_agentic_host.py tests/benchmarks/test_agentic_sandbox_files.py tests/test_ci_workflows.py -q`
Expected: FAIL (`server_env` missing, no requirements file, no audit entry).

- [ ] **Step 3: Implement, generate the file, edit the workflow, and audit by hand**

Run the `uv pip compile` command above, then:
`uvx pip-audit --requirement benchmarks/sandbox/fetch-requirements.txt --no-deps --disable-pip --progress-spinner off`
Expected: `No known vulnerabilities found`. If one is found, lock the
fixed release, even when it is newer than 14 days, and record the
advisory ids in the PR body (the `pyasn1` precedent).

- [ ] **Step 4: Run the tests**

Run: `ISO=$(mktemp -d); uv run --locked env XDG_CACHE_HOME="$ISO" pytest tests/benchmarks tests/test_ci_workflows.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/agentic/bridge.py benchmarks/agentic/worker.py docs/benchmarks/agentic-manifest.yml benchmarks/sandbox/fetch-requirements.txt .github/workflows/security.yml tests/
git commit -m "feat(benchmarks): MCP servers reach the egress proxy; pin and audit mcp-server-fetch 2026.10.19"
```

---

### Task 5: The launcher seam: key bootstrap, prepare, stop worker, close

All `host.py` and `worker.py` changes for the sandbox land here, additive
at the seam (Global Constraints, "Shared files with PR 5").

**Files:**
- Modify: `benchmarks/agentic/protocol.py` (`read_frame_fd`)
- Modify: `benchmarks/agentic/host.py` (`Launcher`, `LocalLauncher`, `_supervise`, `_teardown`, `run_jobs`; rewrite both `shortcut:` comments)
- Modify: `benchmarks/agentic/worker.py` (`BOOTSTRAP`, `apply_bootstrap`, `main_worker`, `_serve`, `run_worker`, `_run_job`)
- Modify: `benchmarks/agentic/ledger.py` (`Ledger.reserve`, `LedgerService.reserve`: a defaulted `job_id`)
- Test: `tests/benchmarks/test_agentic_host.py`, `tests/benchmarks/test_agentic_ledger.py`

**Interfaces:**
- Produces: `protocol.read_frame_fd(fd: int, *, limit: int = MAX_FRAME_BYTES) -> dict[str, Any] | None`. It reads exactly one frame with unbuffered `os.read`, so no byte of the next frame is consumed. It returns `None` at EOF.
- Produces: `Ledger.reserve(..., job_id: str | None = None)` and `LedgerService.reserve(..., job_id: str | None = None)`. The value is written as the `job_id` field of the `reserve` event, in `<provider>.jsonl` and therefore in the run's `ledger-events.jsonl` (round-3 item 2). `_supervise` passes `job_id=job.job_id`, because it already holds `in_flight` per job. Every other caller keeps working unchanged.
- Produces: four new `Launcher` methods:

```python
def bootstrap_frame(self, job: WorkerJob) -> dict[str, Any] | None:
    """The private first frame for the worker's stdin, or None. Called once, after prepare."""
async def prepare(self, jobs: Sequence[WorkerJob]) -> None:
    """Before any worker starts; raise to stop the run with nothing spent."""
async def stop_worker(self, job: WorkerJob) -> None:
    """After the group kill and before the job's in-flight unit is settled."""
async def close(self) -> list[str]:
    """After every worker is gone: release everything; return problems, raise only KeyLeakError."""
```

- `LocalLauncher`: `bootstrap_frame` returns `None`; `prepare` and `stop_worker` do nothing; `close` returns `[]`.
- `_supervise`: after the worker process starts, `frame = launcher.bootstrap_frame(job)`. When it is not `None`, the frame is written to the worker's stdin, drained, and the local reference dropped. Only then is the job frame sent. The bootstrap frame is never logged, never kept in a variable past that point, and never part of any record.
- `_teardown(proc, anchor, log_path, ledger, in_flight, stop_worker=None)` (round-2 ruling A2). Order:
  1. kill the group;
  2. wait (bounded);
  3. reap the anchor;
  4. `await stop_worker()` in its own `try`, keeping any exception;
  5. scrub and publish the log, which always runs;
  6. settle the unit in `finally`;
  7. re-raise the kept exception, so the run aborts.
  The default keeps the three positional callers at `test_agentic_host.py:942,972,1006` working.
- `run_jobs`: after the `unsupported` check, `await launcher.prepare(jobs)`, then the supervisors. All of it sits inside `try/finally: problems = await launcher.close()`. If the gather succeeded and `problems` is not empty, raise `BenchmarkValidationError` listing them. If an exception is already propagating, print them to stderr.
- The refusal text for unsupported families keeps "the local launcher runs only scripted models" and ends "use --launcher container".
- Worker:
  - `BOOTSTRAP = "bootstrap"`.
  - `apply_bootstrap(frame: Mapping[str, Any]) -> None`. `frame["env"]` must hold at most one name, and that name must be in `PAID_KEY_ENVS`. Anything else raises `BenchmarkValidationError` naming the variables, never a value. It sets `os.environ[name] = value`.
  - `main_worker()`: `first = read_frame_fd(0)` before `protect_output()`. If `first` is a bootstrap frame: `apply_bootstrap(first)`, then `first = None`, `bootstrapped = True`. Then `protect_output()` (whose `_secrets()` now includes the key), then `_serve(channel, first, bootstrapped=bootstrapped)`.
  - `_serve(channel, first=None, *, bootstrapped=False)`, `run_worker(channel, reader, first=None, *, bootstrapped=False)` and `_run_job(channel, reader, first=None, *, bootstrapped=False)`. The job is `first` when it was already read, else the next frame.
  - `_run_job` refuses a family with `api != "scripted"` when `bootstrapped` is false, before `make_client`, with "a live job needs its key bootstrap frame first".
- Comment at `host.py:95`: "The host's `cat`; `run.sh` checks for it, and the worker image has it too (benchmarks/sandbox/Dockerfile)."
- Comment at `host.py:110`: "Reaches only this host's process tree. A container launcher's `stop_worker` removes its own container, which ends everything in it, before the unit is settled."

- [ ] **Step 1: Write the failing tests** (a `RecordingLauncher` wraps `LocalLauncher` and logs calls to a shared list)

```python
async def test_run_jobs_prepares_every_job_before_any_worker_starts(tmp_path) -> None
    # events start with ("prepare", [all job ids]); no "command" before it
async def test_a_failed_prepare_starts_no_worker_and_still_closes(tmp_path) -> None
async def test_supervise_writes_the_bootstrap_before_the_job_frame(tmp_path) -> None
    # a launcher whose bootstrap_frame returns {"type": "bootstrap", "env": {}} and whose command runs a
    # fixture worker that reports the types of its first two frames: ["bootstrap", "job"]
def test_host_environ_is_unchanged_after_a_launch(tmp_path, monkeypatch) -> None
    # a launcher that returns a bootstrap with a synthetic key: os.environ before == after, and
    # refuse_host_keys(os.environ) still passes
def test_bootstrap_key_is_redacted_from_worker_output() -> None
    # the existing protect_output subprocess pattern: the child reads a bootstrap frame from stdin
    # (synthetic "sk-ant-api03-" + "a" * 90), applies it, protects output, then prints the key to
    # stdout, stderr and fd 2; the parent sees only "[redacted]"
async def test_worker_refuses_a_live_job_without_its_bootstrap(tmp_path) -> None
    # run_worker with a claude-sonnet-5-5 job and bootstrapped=False -> error frame naming the
    # bootstrap; make_client is never called (patched to fail)
def test_a_bootstrap_with_a_foreign_or_second_variable_is_refused() -> None
    # {"HTTPS_PROXY": ...} and {"ANTHROPIC_API_KEY": ..., "OPENAI_API_KEY": ...} both raise
async def test_teardown_stops_the_worker_before_its_unit_is_settled(tmp_path) -> None
    # a worker lost with a unit in flight: order ["stop_worker:<job>", "settle:<unit>"], booked at
    # twice its bound (reuse the lost-worker fixture of
    # test_host_settles_a_lost_workers_unit_at_twice_its_bound_and_frees_the_provider)
async def test_a_failing_stop_worker_still_scrubs_the_log_and_settles_the_unit(tmp_path) -> None
    # stop_worker raises; the worker log held a key shape: the published log holds "[redacted]" (or is
    # removed), the unit is booked at twice its bound, and the stop_worker error propagates
async def test_close_problems_fail_a_clean_run(tmp_path) -> None
async def test_every_reserve_event_names_its_job(tmp_path) -> None
    # a scripted two-job run: every "reserve" event in ledger-events.jsonl has job_id equal to the
    # job that reserved it; Ledger.reserve without job_id still writes job_id None (old callers)
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --locked pytest tests/benchmarks/test_agentic_host.py -k "prepare or bootstrap or stop_worker or close_problems or environ_is_unchanged" -q`
Expected: FAIL (`AttributeError: prepare`, no `apply_bootstrap`).

- [ ] **Step 3: Implement the interfaces above**

- [ ] **Step 4: Run the host and run suites**

Run: `ISO=$(mktemp -d); uv run --locked env XDG_CACHE_HOME="$ISO" pytest tests/benchmarks/test_agentic_host.py tests/benchmarks/test_agentic_run.py -q`
Expected: PASS, with every existing teardown and anchor test unchanged.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/agentic/protocol.py benchmarks/agentic/host.py benchmarks/agentic/worker.py benchmarks/agentic/ledger.py tests/benchmarks/test_agentic_host.py tests/benchmarks/test_agentic_ledger.py
git commit -m "feat(benchmarks): launcher seam with a stdin key bootstrap, prepare, stop-worker and close"
```

---

### Task 6: Model limits at worker startup

This replaces the round-1 `run_once` and `agentic-limits` path (round-2
ruling D10). The smoke run surfaces Sonnet 5.5's exact limits.

**Files:**
- Modify: `benchmarks/agentic/clients.py` (`fetch_model_limits`)
- Modify: `benchmarks/agentic/worker.py` (`_run_job` calls it; `_system_info(..., model_limits=None)`)
- Modify: `benchmarks/agentic/run.py` (`limits_mismatch`; the collector records it; summary `model_limits_mismatch`)
- Modify: `docs/benchmarks/model-matrix.yml` (the comment at line 65 says the smoke run reports the limits)
- Test: `tests/benchmarks/test_agentic_clients.py`, `tests/benchmarks/test_agentic_run.py`

**Interfaces:**
- Produces: `clients.fetch_model_limits(client: ModelClient) -> dict[str, Any] | None`.
  - `AnthropicClient`: one `models.retrieve(model_id)`, giving `{"max_input_tokens", "max_tokens"}`.
  - `GeminiClient`: one `aio.models.get(model=model_id)`, giving `{"max_input_tokens": input_token_limit, "max_tokens": output_token_limit}`.
  - Any other client returns `None` without a request: the OpenAI and Ollama model endpoints report no token limits.
  - An SDK error returns `{"error": "<Type>"}` and never raises.
  - It reserves no ledger unit, because the endpoint is free.
- `_run_job` calls it once, after `make_client`, for a family with `api != "scripted"`. It passes the result as `model_limits` in the `system_info` frame.
- Produces: `run.limits_mismatch(reported: Mapping[str, Any] | None, family: ModelFamily) -> list[str]`. One line per difference:
  - `max_input_tokens != family.max_input_tokens`;
  - `max_tokens < family.max_output_tokens`;
  - "could not be read" for an `error` entry.
  It returns `[]` for `None`. The collector appends each line with the job id to `run-summary.json` `model_limits_mismatch`. The maintainer corrects `model-matrix.yml` by hand in a docs PR.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("family_id", ["claude-sonnet-5-5", "gemini-3.8-flash"])
async def test_fetch_model_limits_makes_one_free_get(family_id) -> None
    # MockTransport records exactly one GET to the provider's models endpoint; the two integers come back
async def test_fetch_model_limits_skips_providers_that_report_none() -> None   # openai, ollama: no request
async def test_fetch_model_limits_records_an_error_without_raising() -> None
def test_limits_mismatch_reports_each_difference() -> None
def test_a_mismatch_reaches_the_run_summary(tmp_path) -> None
    # a system_info message with model_limits {"max_input_tokens": 999} -> summary lists it with the job id
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --locked pytest tests/benchmarks/test_agentic_clients.py tests/benchmarks/test_agentic_run.py -k "limits" -q`
Expected: FAIL (`fetch_model_limits` missing).

- [ ] **Step 3: Implement the interfaces above**

- [ ] **Step 4: Run the agentic suite**

Run: `ISO=$(mktemp -d); uv run --locked env XDG_CACHE_HOME="$ISO" pytest tests/benchmarks -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/agentic/clients.py benchmarks/agentic/worker.py benchmarks/agentic/run.py docs/benchmarks/model-matrix.yml tests/benchmarks/
git commit -m "feat(benchmarks): live workers report model limits; the run summary lists mismatches"
```

---

### Task 7: The worker image

**Files:**
- Create: `benchmarks/sandbox/Dockerfile`
- Test: `tests/benchmarks/test_agentic_sandbox_files.py`

**Interfaces:**
- Produces: an image whose `WORKDIR` is `/app` (the repo at the build commit), whose `PATH` starts with `/app/.venv/bin`, with user `10001:10001`, `mcp-server-fetch` on `PATH`, `cat` present, no `node`, and `/cache/mcp-python-docs` present.
- The build context is always `git archive HEAD` (Task 16), so the image holds committed files only. `.env.local` (gitignored by `.env*`) can never enter it.

The Dockerfile is fixed copy. There is deliberately no `# syntax=` line,
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

Run: `IID=$(mktemp); git archive --format=tar HEAD | docker build --file benchmarks/sandbox/Dockerfile --label org.opencontainers.image.revision=$(git rev-parse HEAD) --iidfile "$IID" - && docker run --rm --pull never "$(cat "$IID")" sh -c 'id -u; command -v cat mcp-server-fetch; test ! -e /app/.env.local && echo no-env-file; test -z "$HTTPS_PROXY" && echo no-proxy; python -c "import benchmarks.agentic.clients"'`
Expected: `10001`, `/usr/bin/cat`, `/usr/local/bin/mcp-server-fetch`, `no-env-file`, `no-proxy`, and exit 0. Keep the image ID for Task 16.

---

### Task 8: Egress policy: allowlists, the proxy log and forbidden variables

**Files:**
- Create: `benchmarks/agentic/egress.py`
- Test: `tests/benchmarks/test_agentic_egress.py`

**Interfaces:**
- Produces:
  - `EGRESS_ALIAS = "egress"`, `EGRESS_PORT = 3128`, `EGRESS_URL = "http://egress:3128"`, `LOG_DIR_IN_PROXY = "/var/log/squid-bench"`.
  - `PROVIDER_HOSTS = {"anthropic": (("api.anthropic.com", 443),), "openai": (("api.openai.com", 443),), "google": (("generativelanguage.googleapis.com", 443),), "ollama": (("host.docker.internal", 11434),)}` and `SYSTEM_HOSTS = {"docs-fetch": (("docs.python.org", 443),)}`. These are the pinned base URLs in `clients.py` (Anthropic, OpenAI, Gemini) and design §7.4.
  - `allowlist(family: ModelFamily, system_id: str | None) -> tuple[tuple[str, int], ...]`: the provider's hosts unless `family.api == "scripted"`, plus `SYSTEM_HOSTS.get(system_id, ())`, in that order.
  - `@dataclass(frozen=True) class ProxyEvent: at: str; client: str; method: str; host: str; port: int; status: int; result: str`, with property `allowed: bool` (`not result.startswith("TCP_DENIED")`) and property `target: str` (`f"{host}:{port}"`).
  - `parse_proxy_log(text: str) -> list[ProxyEvent]`. It needs every line to end with `\n`. It raises `BenchmarkValidationError` naming the line number on a malformed or unterminated line.
  - `FORBIDDEN_ENV = re.compile(r"(?i)^(?:\w*_proxy|ssl_cert_\w*|requests_ca_bundle)$")` and `forbidden_env_names(environ: Mapping[str, str]) -> list[str]` (sorted names, never values).

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("family_id,system_id,expected", [
    ("claude-sonnet-5-5", "python-docs", (("api.anthropic.com", 443),)),
    ("gpt-6.1-sol", "docs-fetch", (("api.openai.com", 443), ("docs.python.org", 443))),
    ("gemma4-12b", "python-docs", (("host.docker.internal", 11434),)),
    ("scripted-anthropic", "no-docs", ()),
])
def test_allowlist_per_job(family_id, system_id, expected) -> None
def test_parse_proxy_log_reads_tunnels_and_denials() -> None
    # "2026-11-04T10:00:00.123Z 172.20.0.3 CONNECT api.anthropic.com 443 200 TCP_TUNNEL\n" -> allowed
    # "2026-11-04T10:00:01.004Z 172.20.0.3 CONNECT example.com 443 403 TCP_DENIED\n" -> not allowed
def test_parse_proxy_log_rejects_a_malformed_or_unterminated_line() -> None
def test_forbidden_env_names_match_every_case() -> None
    # HTTPS_PROXY, http_proxy, ALL_PROXY, NO_PROXY, SSL_CERT_FILE, SSL_CERT_DIR, REQUESTS_CA_BUNDLE
    # are named; PATH and PROXY_NOTE are not
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --locked pytest tests/benchmarks/test_agentic_egress.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `benchmarks/agentic/egress.py`**

Import nothing heavier than `benchmarks.model_matrix` and
`benchmarks.runner`, because the probe imports this module inside the
container.

- [ ] **Step 4: Run the tests**

Run: `uv run --locked pytest tests/benchmarks/test_agentic_egress.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/agentic/egress.py tests/benchmarks/test_agentic_egress.py
git commit -m "feat(benchmarks): egress allowlists, proxy log parser and forbidden env names"
```

---

### Task 9: The Squid configuration

**Files:**
- Modify: `benchmarks/agentic/egress.py` (`squid_conf`)
- Test: `tests/benchmarks/test_agentic_egress.py`

**Interfaces:**
- Produces: `squid_conf(allow: Sequence[tuple[str, int]]) -> str`.
  - Port-443 entries allow only `CONNECT`; any other port allows only plain HTTP.
  - Every allowlist ACL is `dstdomain -n` (round-2 ruling B3, sandbox note §C), so an IP-literal URL never gets a reverse lookup that could match.
  - The access log goes to a file in the bind-mounted `LOG_DIR_IN_PROXY`, with no rotation (round-2 ruling C5).
  - No `dst` ACL appears.

`squid_conf([("api.anthropic.com", 443), ("host.docker.internal", 11434)])`
returns exactly:

```
http_port 3128
visible_hostname egress
pid_filename none
cache deny all
shutdown_lifetime 1 seconds
httpd_suppress_version_string on
logfile_rotate 0
logformat bench %{%Y-%m-%dT%H:%M:%S}tg.%03tuZ %>a %rm %>rd %>rP %>Hs %Ss
access_log stdio:/var/log/squid-bench/access.log logformat=bench rotate=0
cache_log /var/log/squid-bench/cache.log
acl allow_1_host dstdomain -n api.anthropic.com
acl allow_1_port port 443
http_access allow CONNECT allow_1_host allow_1_port
acl allow_2_host dstdomain -n host.docker.internal
acl allow_2_port port 11434
http_access allow !CONNECT allow_2_host allow_2_port
http_access deny all
```

- [ ] **Step 1: Write the failing tests**

```python
def test_squid_conf_matches_the_template() -> None          # the exact text above
def test_squid_conf_with_no_host_denies_everything() -> None  # no allow line; ends "http_access deny all"
def test_every_allowlist_acl_disables_lookups() -> None
    # parse the conf: every "acl ... dstdomain" line has "-n" right after "dstdomain"; no " dst " ACL;
    # the access_log line names a file under /var/log/squid-bench and "rotate=0"
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --locked pytest tests/benchmarks/test_agentic_egress.py -k squid -q`
Expected: FAIL (`squid_conf` missing).

- [ ] **Step 3: Implement `squid_conf`**

- [ ] **Step 4: Run the tests**

Run: `uv run --locked pytest tests/benchmarks/test_agentic_egress.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/agentic/egress.py tests/benchmarks/test_agentic_egress.py
git commit -m "feat(benchmarks): Squid config with lookup-free allowlists and a file access log"
```

---

### Task 10: The sandbox and `ContainerLauncher`

**Files:**
- Create: `benchmarks/agentic/sandbox.py`
- Test: `tests/benchmarks/test_agentic_sandbox.py` (with a `FakeDocker` in the module)

**Interfaces:**
- Consumes: `egress.*` (Tasks 8, 9); the `Launcher` methods (Task 5); `EGRESS_URL_ENV`, `LIVE_PROVIDERS_ENABLED_ENV`, `PROVIDER_API_KEY_ENV`.
- Produces:
  - `SQUID_IMAGE` (the pinned ref), `WORKER_USER = "10001:10001"`, `PROXY_USER = "proxy"`, `RUN_LABEL = "org.python-docs-mcp.benchmark-run"`, `MIN_ENGINE = (28, 0)`.
  - Names:
    - the worker container is `pdmcp-<run_id>-<job_id>`;
    - its proxy is `<worker name>-proxy`;
    - its internal network is `<worker name>-net`;
    - its probe container is `<worker name>-probe`;
    - the run's outside network is `pdmcp-<run_id>-out`.
    Every container and network carries `--label RUN_LABEL=<run_id>`.
  - `read_key(keys_file: Path, name: str) -> str`.
    - It reads `NAME=VALUE` lines: optional `export `, matching single or double quotes stripped, `#` comment lines and blank lines skipped. It matches the exact name.
    - It refuses a file whose mode has any group or other bit, a missing name, or an empty value.
    - Every error names the file and the variable, never a value.
  - `DockerRun = Callable[[Sequence[str]], Awaitable[subprocess.CompletedProcess[bytes]]]`. `docker_runner(env: Mapping[str, str]) -> DockerRun` runs `docker` through `asyncio.to_thread(subprocess.run, ..., capture_output=True, env=env, timeout=120)`.
  - `cli_env(environ: Mapping[str, str]) -> dict[str, str]`: the docker CLI allowlist in Global Constraints. It never holds a key.
  - `@dataclass(frozen=True) class SandboxConfig: image: str; run_id: str; out_dir: Path; index_path: Path; keys_file: Path | None; repo_commit: str`. `__post_init__` refuses an `image` that does not match `^sha256:[0-9a-f]{64}$`.
  - `@dataclass(frozen=True) class Egress: job_id: str; network: str; proxy: str; allow: tuple[tuple[str, int], ...]; log_dir: Path; gateways: tuple[str, ...]; inspect: dict[str, Any]`.
  - `class Sandbox(config, docker: DockerRun | None = None)`:
    - `async verify_image() -> dict[str, str]`. All of these must hold, checked before any container starts:
      - `docker context inspect --format '{{.Endpoints.docker.Host}}'` (the active context) gives an endpoint starting `unix://`, and `DOCKER_HOST`, when set in `cli_env`, also starts `unix://` (round-3 item 4);
      - `docker image inspect` gives `Id == config.image` and the label `org.opencontainers.image.revision == config.repo_commit`;
      - `docker version` gives a server version ≥ `MIN_ENGINE`;
      - `git status --porcelain` is empty (the only dirty-tree check; `run.sh` has none).
      It writes `out_dir/sandbox.json` (image, revision, proxy image, engine version, docker context and endpoint).
    - `async open_egress(job_id: str, allow) -> Egress`:
      1. Create the outside network once per run.
      2. Create the internal network with `docker network create --internal --ipv6=false -o com.docker.network.bridge.gateway_mode_ipv4=isolated --label ...`.
      3. Run `docker network inspect`. If `EnableIPv6` is still true, raise `BenchmarkValidationError` naming the network (round-3 item 8; no IPv6 fallback). Keep every `IPAM.Config[].Gateway` and the inspect output in the `Egress`.
      4. Create `log_dir = out_dir/egress/<job_id>/` with `mkdir(parents=True)`, then `log_dir.chmod(0o777)`, because `mkdir(mode=...)` is cut by the umask and Squid's uid differs from the host user's (round-3 item 3). Write `squid.conf` into it.
      5. Start the proxy: `docker run -d --pull never --name <proxy> --label RUN_LABEL=<run_id> --network <outside> --read-only --tmpfs /run --tmpfs /var/spool/squid --cap-drop ALL --security-opt no-new-privileges --user proxy --memory 256m --cpus 0.5 --entrypoint /usr/sbin/squid --mount type=bind,src=<log_dir>/squid.conf,dst=/etc/squid/squid.conf,readonly --mount type=bind,src=<log_dir>,dst=/var/log/squid-bench SQUID_IMAGE -N -f /etc/squid/squid.conf`.
      6. Run `docker network connect --alias egress <internal> <proxy>`.
    - `container_argv(*, name: str, network: str, env: Mapping[str, str], mounts: Sequence[str], argv: Sequence[str], interactive: bool = True) -> list[str]`: the full `docker run` argv with the Global Constraints hardening, `--log-driver none` included (`-i` only when `interactive`). `env` becomes `-e NAME=VALUE` and each mount a `--mount`, then the image ID, then `argv`. It has no key parameter.
    - `async close_egress(egress: Egress) -> list[str]`:
      1. `docker inspect --format '{{json .State}}' <proxy>`, before our stop (round-3 item 1). The proxy ended abnormally when it is no longer `Running` (it ended on its own, for example exit 137 from a kill or an OOM) or `OOMKilled` is true.
      2. `docker stop -t 5 <proxy>`. Squid closes its log within `shutdown_lifetime`, and the access log is already on the host.
      3. Inspect `State` again: `OOMKilled` true is abnormal too. A non-zero exit caused by our own stop is not abnormal by itself; a truncated log still fails Task 12's checks.
      4. Check the key shape of `access.log` and `cache.log` with `redact_key_shaped`, rewriting a file in place when a pattern is found.
      5. `docker rm -f <proxy>`, then `docker network rm <internal>`.
      6. Write `egress/<job_id>/collected.json` as `{"ok": bool, "problems": [...], "state_before_stop": {...}, "state_after_stop": {...}}`. `ok` is false after any failed step or an abnormal end.
      Every step runs even after a failure, and failures are returned as problems. `ContainerLauncher.close` raises `KeyLeakError` for a key shape after all cleanup.
  - `ProbeFn = Callable[..., Awaitable[dict[str, Any]]]`, called as `probe(sandbox, egress, *, job_id, family, system_id)`.
  - `class ContainerLauncher(sandbox: Sandbox, probe: ProbeFn | None = None)` implements `Launcher`. `None` means Task 11's `isolation_probe`.
    - `supports(family)`: `True` for `scripted` and for every api `make_client` builds.
    - `command(job)`: argv `container_argv(name="pdmcp-<run_id>-<job_id>", network=<internal>, ..., argv=["python", "-m", "benchmarks", "agentic-worker"])` and env `cli_env(os.environ)`.
      - Worker env: `HOME=/tmp`, `XDG_CACHE_HOME=/cache`, `PYTHON_DOCS_MCP_DISABLE_AUTO_INDEX=1`, `BENCHMARK_EGRESS_URL=EGRESS_URL`. A live family adds `BENCHMARK_LIVE_PROVIDERS_ENABLED=1`; Ollama adds `OLLAMA_BASE_URL=http://host.docker.internal:11434/v1`.
      - Mounts: tmpfs `/cache/mcp-python-docs` (`tmpfs-mode=1777`). A `python-docs` job adds `index_path` read-only at `/cache/mcp-python-docs/index.db`. A scripted family adds its script read-only at its own absolute path.
    - `bootstrap_frame(job)`: `None` for a scripted family. For every live family it returns `{"type": "bootstrap", "env": {...}}`. For a metered family `env` is `{name: read_key(keys_file, name)}` with `name = PROVIDER_API_KEY_ENV[family.provider]`; for Ollama it is `{}`. A missing `keys_file` refuses.
    - `async prepare(jobs)`: `verify_image()`, then for every job `open_egress(job_id, allowlist(job.family, job.system.id))` and `probe(...)`. It raises on the first failure. Task 11 adds the run's control probe before the per-job probes.
    - `async stop_worker(job)`: `docker rm -f pdmcp-<run_id>-<job_id>`. It ignores "No such container" only.
    - `async close()`: `close_egress` for every egress it opened, then `docker network rm` of the outside network. It returns problems and raises `KeyLeakError` last.

- [ ] **Step 1: Write the failing tests**

```python
def test_read_key_returns_only_the_named_key(tmp_path) -> None
    # file (mode 0o600): "# c\nexport OPENAI_API_KEY_OLD=x\nOPENAI_API_KEY='sk-a'\nANTHROPIC_API_KEY=\"sk-b\"\n"
    assert read_key(f, "OPENAI_API_KEY") == "sk-a"
def test_read_key_refuses_a_group_or_world_readable_file(tmp_path) -> None   # 0o640 and 0o604
def test_read_key_errors_never_hold_a_value(tmp_path) -> None                # missing and empty names
def test_worker_argv_carries_the_design_hardening(tmp_path) -> None
    # contains --read-only, "--cap-drop ALL", "--security-opt no-new-privileges", "--user 10001:10001",
    # --init, --rm, -i, "--pull never", "--log-driver none", "--network <internal>", --memory, --cpus,
    # --pids-limit; no -t, --privileged, docker.sock or --network host; the image ID right before the command
def test_only_python_docs_jobs_mount_the_index_read_only(tmp_path) -> None
def test_worker_env_is_the_allowlist(tmp_path, monkeypatch) -> None
    # host env polluted with HTTPS_PROXY, ALL_PROXY, SSL_CERT_FILE, REQUESTS_CA_BUNDLE, LANG:
    # -e names in argv are within the allowlist; cli env has no FORBIDDEN_ENV name
async def test_no_key_in_argv_cli_env_or_container_config(tmp_path, monkeypatch) -> None
    # keys file with ANTHROPIC_API_KEY=<synthetic>; for the claude job: command() argv and env, and
    # every FakeDocker-recorded "docker run" argv and env (the container config), never contain the
    # key name or value; bootstrap_frame(job) == {"type": "bootstrap", "env": {"ANTHROPIC_API_KEY": <synthetic>}}
def test_each_live_bootstrap_holds_only_its_own_providers_key(tmp_path) -> None
    # openai job -> OPENAI_API_KEY only; gemma4-12b -> env {}; scripted -> None
def test_a_live_family_without_a_keys_file_is_refused(tmp_path) -> None
async def test_internal_networks_are_ipv4_only_with_an_isolated_gateway(tmp_path) -> None
    # "network create --internal --ipv6=false -o com.docker.network.bridge.gateway_mode_ipv4=isolated";
    # Egress.gateways == every IPAM Config Gateway; a FakeDocker inspect with EnableIPv6 still true ->
    # BenchmarkValidationError, and no proxy or worker run follows
async def test_prepare_builds_one_network_and_proxy_per_job_with_a_file_log(tmp_path, monkeypatch) -> None
    # one outside network; per job one squid run with the conf and the log dir bind-mounted, and one
    # "network connect --alias egress"; egress/<job>/squid.conf == squid_conf(allowlist(...)); probe per job;
    # with os.umask(0o077) in force, egress/<job>/ is still mode 0o777
async def test_prepare_refuses_a_daemon_that_is_not_local(tmp_path, monkeypatch) -> None
    # FakeDocker context endpoint "tcp://10.0.0.5:2376" -> refused; DOCKER_HOST=tcp://... with a unix
    # context -> refused; in both, FakeDocker recorded no "run" and no "network create"
async def test_prepare_refuses_an_image_from_another_commit(tmp_path) -> None
async def test_prepare_refuses_an_engine_older_than_28(tmp_path) -> None
def test_sandbox_config_refuses_a_tag(tmp_path) -> None                       # "python-docs:latest"
async def test_stop_worker_removes_only_its_own_container(tmp_path) -> None
    # exactly ["docker", "rm", "-f", "pdmcp-<run>-<job>"]; FakeDocker never sees ps, prune or --filter
async def test_close_stops_each_proxy_then_removes_what_it_created(tmp_path) -> None
    # per egress in order: inspect, stop, inspect, rm -f, network rm; collected.json ok true with no
    # problems; then the outside network
@pytest.mark.parametrize("state", [{"Running": False, "ExitCode": 137, "OOMKilled": False},
                                   {"Running": True, "ExitCode": 0, "OOMKilled": True}])
async def test_an_abnormal_proxy_exit_fails_the_collection(tmp_path, state) -> None
    # every cleanup command succeeds and the access log is well formed but partial; the pre-stop
    # inspect returns `state` -> collected.json ok false naming the exit code or OOMKilled; Task 12's
    # summary over this run dir says isolation_probe "fail"
async def test_close_redacts_a_key_shaped_proxy_log_and_raises_after_cleanup(tmp_path) -> None
async def test_close_keeps_going_after_a_failed_step(tmp_path) -> None       # collected.json ok false
def test_container_launcher_supports_scripted_and_every_live_api() -> None
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --locked pytest tests/benchmarks/test_agentic_sandbox.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `benchmarks/agentic/sandbox.py`**

It must not import a provider SDK, because `run.sh` imports `SQUID_IMAGE`
from it. Tests pass a stub `probe`; Task 11 supplies the real default.

- [ ] **Step 4: Run the tests**

Run: `ISO=$(mktemp -d); uv run --locked env XDG_CACHE_HOME="$ISO" pytest tests/benchmarks/test_agentic_sandbox.py tests/benchmarks/test_agentic_host.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/agentic/sandbox.py tests/benchmarks/test_agentic_sandbox.py
git commit -m "feat(benchmarks): container launcher with per-job egress proxy and keyless containers"
```

---

### Task 11: The isolation probe

**Files:**
- Create: `benchmarks/agentic/probe.py`
- Modify: `benchmarks/__main__.py` (internal `agentic-probe --spec JSON`)
- Modify: `benchmarks/agentic/sandbox.py` (`ContainerLauncher.prepare` uses it)
- Test: `tests/benchmarks/test_agentic_probe.py`

**Interfaces:**
- Consumes: `forbidden_env_names`, `parse_proxy_log`, `ProxyEvent`, `EGRESS_URL` (Task 8); `Sandbox`, `Egress`, `container_argv` (Task 10).
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
  - `class Canary`, an async context manager: a TCP listener on `0.0.0.0:0`, with `port` and `received: list[bytes]`.
  - `host_addresses() -> list[str]`: non-loopback IPv4 addresses from `socket.gethostbyname_ex(socket.gethostname())`, plus the source address of a UDP socket connected to `192.0.2.1:9` (no packet is sent). Each lookup sits in `suppress(OSError)`, so an unresolvable hostname or a missing route while offline (exactly when the air-gapped arm runs) leaves the list short, never raises (round-3 item 5). An empty list is allowed.
  - `class IsolationProbeFailed(BenchmarkValidationError)`.
  - `probe_verdict(checks, *, canary_received: Sequence[bytes], token: str, proxy_events: Sequence[ProxyEvent], denied: tuple[str, int], canary: tuple[str, int]) -> list[str]`. It names what failed: each failed check, `canary_saw_token`, and `denial_not_logged` unless both the `CONNECT` to `denied` and the plain request to `canary` appear as not-allowed events. An empty list means pass.
  - `isolation_probe(sandbox, egress, *, job_id, family, system_id) -> dict`, the default `ProbeFn`:
    - It opens its own `Canary` and runs `python -m benchmarks agentic-probe` in the container `<worker name>-probe` on `egress.network`. The container is built by `container_argv(..., interactive=False)` with the worker's non-secret env and no key.
    - `direct` targets: `host.docker.internal`, `gateway.docker.internal`, every `host_addresses()` entry and every `egress.gateways` entry (round-2 ruling B4), each on the canary port and on 11434.
    - It also sets `dns_name="pypi.org"`, `denied=["example.com", 443]`, the canary through the proxy, and, for an Ollama family, the Ollama `/api/version` URL.
    - Verdict: `probe_verdict(...)` over the checks, the canary's bytes and `parse_proxy_log(<log_dir>/access.log)`, read straight from the bind-mounted file.
    - Record: `checks`, `failed`, `passed`, `network_inspect` (the `docker network inspect` output), and `proxy_prefix`: the exact access-log lines present when the verdict was taken. Task 12 verifies that prefix and leaves it out of the query-time evidence.
    - It writes `out_dir/probe/<job_id>.json`, then returns the record or raises `IsolationProbeFailed` naming each failed check.
  - `control_probe(sandbox) -> dict`: with its own `Canary`, `run_control` in the container `pdmcp-<run_id>-control` on the outside network must reach `host.docker.internal:<canary port>`. Otherwise it raises `IsolationProbeFailed("inconclusive: ...")`. `ContainerLauncher.prepare` runs it once, before the per-job probes, and writes `out_dir/probe/control.json`.
  - Every one-shot probe container runs with `--rm` under its name. Its `docker run` sits in a `try` whose `finally` awaits `asyncio.shield(docker(["rm", "-f", name]))`, so a timeout or a cancellation still removes it (round-2 ruling D12).

- [ ] **Step 1: Write the failing tests**

```python
def test_direct_check_fails_and_sends_the_token_when_something_listens() -> None
    # a listener on 127.0.0.1: passed False; the listener received b"Bearer <token>"
def test_direct_check_passes_when_nothing_listens() -> None
def test_dns_check(monkeypatch) -> None                       # getaddrinfo raises -> pass; returns -> fail
def test_host_addresses_work_offline(monkeypatch) -> None
    # gethostbyname_ex raises socket.gaierror and the UDP connect raises OSError -> [] and no exception;
    # only the UDP source address available -> that one address
def test_proxy_checks_read_the_status_line() -> None          # a local fake proxy answering 403, then 200
def test_env_check_names_forbidden_variables_only(monkeypatch) -> None
    # HTTPS_PROXY=http://user:secret@x:1 -> passed False, "HTTPS_PROXY" in detail, "secret" not in detail
def test_verdict_fails_when_the_canary_saw_the_token() -> None             # -> ["canary_saw_token"]
def test_verdict_needs_both_denials_logged() -> None                       # only example.com -> ["denial_not_logged"]
def test_verdict_passes_with_every_check_and_both_denials() -> None        # -> []
async def test_the_probe_targets_every_ipam_gateway_and_records_the_inspect(tmp_path) -> None
    # Egress.gateways ("172.30.0.1", "fd00::1") appear in the spec's direct targets; the record holds
    # network_inspect and proxy_prefix (the lines of the fake access.log)
async def test_a_timed_out_or_cancelled_probe_container_is_removed(tmp_path) -> None
    # FakeDocker "run" raises TimeoutExpired, then a run cancelled mid-await: both end with
    # ["docker", "rm", "-f", "<worker name>-probe"]
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

### Task 12: C1 evidence: `egress/summary.json`

PR 5 (#199, owner question 9, ruled) reads C1's evidence through
`load_egress_evidence(run_dir)`, and only in this shape. Anything else
makes C1 "not evaluable".

**Files:**
- Modify: `benchmarks/agentic/egress.py` (`lookup_attempts`, `write_egress_summary`)
- Modify: `benchmarks/agentic/run.py` (`write_outcome` calls it for container runs)
- Test: `tests/benchmarks/test_agentic_egress.py`, `tests/benchmarks/test_agentic_run.py`

**Interfaces:**
- Consumes: `parse_proxy_log`, `PROVIDER_HOSTS` (Task 8); `probe/<job>.json` and `probe/control.json` (Task 11); `egress/<job>/access.log` and `collected.json` (Task 10); the run's `transcripts/` and `discarded/` records; `ledger.events(run_id)` with the `job_id` field of `reserve` events (Task 5).
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

  - `jobs` is keyed by `job_id` (`<family id>--<system id>`), with one entry per planned job.
  - `hosts_allowed` and `hosts_denied` are sorted, unique `"host:port"` strings. They come from the query-time events: the access-log lines after the probe's verified `proxy_prefix`, split by `ProxyEvent.allowed`. Probe traffic never appears.
  - `lookup_package_docs_attempts` counts the model's `lookup_package_docs` tool calls in that job's `transcripts/` and `discarded/` records (cells `<system>--<family>--r<k>`).
  - `isolation_probe` is `"pass"` only when all of these hold (round-2 rulings C5, C6; round-3 items 1, 2):
    - `probe/control.json` and every job's `probe/<job>.json` passed;
    - every job's `collected.json` says `ok`, so no failed collection step and no abnormal proxy end;
    - every job's `access.log` exists, parses, and starts with exactly its `proxy_prefix`;
    - every `ok` settle in the run's ledger events is attributed: its `unit_id` matches a `reserve` event whose `job_id` is a planned job. An `ok` settle that cannot be attributed fails the evidence;
    - every hosted live job (`arm == "hosted"`, `api != "scripted"`) with at least one attributed `ok` settle shows an allowed `PROVIDER_HOSTS` line after the prefix. The attribution comes from the ledger alone, so a worker that died before reporting its trajectory still counts.
    Otherwise it is `"fail"`.
- Produces in `egress.py`:
  - `lookup_attempts(out_dir: Path, family_id: str, system_id: str) -> int`.
  - `write_egress_summary(out_dir: Path, jobs: Sequence[WorkerJob], ledger_events: Sequence[Mapping[str, Any]]) -> dict[str, Any]`. It asserts the literal key sets (round-2 ruling E18):

```python
assert set(summary) == {"jobs", "isolation_probe"}
assert all(set(j) == {"hosts_allowed", "hosts_denied", "lookup_package_docs_attempts"} for j in summary["jobs"].values())
```

    It then writes `out_dir/egress/summary.json` with `_write_json` after a key scan.
- `run_agentic`'s `write_outcome` calls it when the launcher is a `ContainerLauncher`. It runs on every exit after the ledger opens, so a run stopped by a failed probe still writes `"fail"`. A local-launcher run writes no summary, and PR 5 reports C1 as not evaluable.

- [ ] **Step 1: Write the failing tests**

```python
def test_egress_summary_has_exactly_the_bound_keys(tmp_path) -> None
    # synthetic run dir with two jobs: the written file's key sets equal the two literal sets above
def test_probe_lines_never_reach_the_summary(tmp_path) -> None
    # access.log: the 2 probe lines (example.com and canary denied), then api.anthropic.com allowed and
    # pypi.org denied; proxy_prefix == the 2 lines -> hosts_allowed ["api.anthropic.com:443"],
    # hosts_denied ["pypi.org:443"]
def test_a_truncated_or_reprefixed_log_invalidates_the_evidence(tmp_path) -> None
    # an unterminated last line -> "fail"; a log whose first lines differ from proxy_prefix -> "fail"
def test_a_missing_log_invalidates_the_evidence(tmp_path) -> None
def test_no_false_clean_when_collection_failed_or_the_provider_never_appears(tmp_path) -> None
    # FakeDocker-built run dirs: (a) collected.json ok false -> "fail"; (b) a hosted job whose reserve
    # (job_id set) has an "ok" settle but whose log shows no api.anthropic.com line -> "fail"
def test_an_ok_settle_from_a_worker_that_died_early_fails_the_evidence(tmp_path) -> None
    # ledger events: reserve {job_id: "claude-sonnet-5-5--python-docs"} + settle ok; no transcript or
    # discarded record for that job (the worker died before reporting); access.log holds only the
    # probe prefix -> "fail"
def test_an_unattributed_ok_settle_fails_the_evidence(tmp_path) -> None
    # an ok settle whose reserve has job_id None, or a job_id outside the planned jobs -> "fail"
def test_lookup_attempts_count_scored_and_discarded_records(tmp_path) -> None
    # 2 calls in transcripts/python-docs--m--r1/EX-001.json, 1 in discarded/... -> 3;
    # a docs-fetch cell of the same family is not counted
async def test_a_failed_probe_run_still_writes_fail(tmp_path) -> None
    # run_agentic with ContainerLauncher(FakeDocker, probe failing on the second job) and a scripted
    # family: egress/summary.json exists with isolation_probe "fail"
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --locked pytest tests/benchmarks/test_agentic_egress.py tests/benchmarks/test_agentic_run.py -k "summary or lookup or probe_lines or invalidates or false_clean or probe_run or settle" -q`
Expected: FAIL (`write_egress_summary` missing).

- [ ] **Step 3: Implement the interfaces above**

- [ ] **Step 4: Run the tests**

Run: `ISO=$(mktemp -d); uv run --locked env XDG_CACHE_HOME="$ISO" pytest tests/benchmarks -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/agentic/egress.py benchmarks/agentic/run.py tests/benchmarks/
git commit -m "feat(benchmarks): write the C1 egress summary in the shape PR 5 reads"
```

---

### Task 13: Authorization gate, the full-run gate and the CLI

**Files:**
- Create: `benchmarks/agentic/live.py`
- Modify: `benchmarks/agentic/run.py` (`AgenticConfig.reference_run`, `run_agentic(..., confirm=...)`, the default model set, the mixed-arm refusal)
- Modify: `benchmarks/__main__.py` (`agentic-run` flags)
- Test: `tests/benchmarks/test_agentic_live.py`, `tests/benchmarks/test_agentic_run.py`

**Interfaces:**
- Consumes: `ContainerLauncher`, `Sandbox`, `SandboxConfig` (Task 10); `PROVIDER_CAPS`, `PROVIDER_MARGINS`, `UNMETERED_PROVIDERS`.
- Produces in `live.py`:
  - `PILOT_TRAJECTORIES_PER_PROVIDER = 90` (design §12: 10 questions × 3 systems × k = 3), `SAFETY = Decimal("1.25")`, `WINDOW_S = 86_400`.
  - `@dataclass(frozen=True) class ReferenceRate: family_id: str; provider: str; cells: int; usd_per_cell: Decimal; seconds_per_cell: float`.
  - `reference_rates(run_dir: Path) -> dict[str, ReferenceRate]`, keyed by family id (round-2 ruling D8).
    - A logical cell is one `(cell, corpus_id)` over the family's `transcripts/` and `discarded/` records, every attempt included.
    - `usd_per_cell` is the provider's booked spend in that run's `ledger-events.jsonl` (`settle` events) divided by the logical cells of that provider's families.
    - `seconds_per_cell` is the mean, over logical cells, of the sum of `timing.wall_ms − timing.grant_wait_ms` over every attempt of the cell.
  - `@dataclass(frozen=True) class ProviderPlan: provider: str; trajectories: int; spent: Decimal | None; cap: Decimal | None; estimate: Decimal | None`. The money fields are `None` for an unmetered provider.
  - `provider_plans(jobs, spent: Mapping[str, Decimal], caps, rates: Mapping[str, ReferenceRate] | None) -> list[ProviderPlan]`.
  - `full_run_gate(plans, jobs, rates) -> list[str]`. A problem is returned when:
    - a metered provider plans more than `PILOT_TRAJECTORIES_PER_PROVIDER` trajectories without `rates`;
    - `estimate × SAFETY` is over `cap − spent`;
    - a family's `trajectories × seconds_per_cell × SAFETY` is over `WINDOW_S`.
    The per-graded-answer term of design §12 comes from PR 5's `grading/summary.json`.
  - `confirmation_text(plans) -> str`. One line per provider: planned trajectories, spent, cap, remaining, the estimate or "no reference run", and "the ledger stops at the cap; one overrun is at most the $<margin> margin". An unmetered provider shows "local, unmetered".
  - `interactive_confirm(text: str, *, stdin: TextIO = sys.stdin, stderr: TextIO = sys.stderr) -> bool` (round-3 item 6).
    - It returns `False` when `stdin` is not a TTY.
    - It writes `text` and the question `Type yes to start this live run: ` to `stderr` and flushes.
    - It reads the answer with `stdin.readline()`, never `input()`, so nothing reaches stdout, which carries only the summary JSON.
    - It returns `True` only for the exact answer `yes`.
  - `authorize_live(text: str, *, environ: Mapping[str, str] = os.environ, confirm: Callable[[str], bool] = interactive_confirm) -> None` (round-2 ruling D9). It first raises `BenchmarkValidationError("live calls are refused under CI")` when `environ.get("CI", "").strip().lower()` is not one of `""`, `"0"`, `"false"`, `"no"`. Then it raises `BenchmarkValidationError("live run not confirmed")` unless `confirm(text)` is true. It is the one gate every live entry point calls; PR 5's grading entry points call it too.
- `run_agentic(config, *, launcher=None, caps=PROVIDER_CAPS, today=None, confirm: Callable[[str], bool] = interactive_confirm)`:
  - With `config.models` unset, it selects only the `hosted` families the launcher supports. The air-gapped arm must be named with `--models` (round-2 ruling D11).
  - It refuses a plan that mixes the `air-gapped` arm with any other arm: "the air-gapped arm runs alone, with the host offline".
  - After the ledger opens and the overrun check passes, when any family has `api != "scripted"`, it builds `provider_plans` and runs `full_run_gate` (problems raise). Then it calls `authorize_live(confirmation_text(plans), confirm=confirm)`, before `run_jobs`. So the gate runs before any key is read (`bootstrap_frame`) and before any container starts (`prepare`).
  - The summary records `live_confirmation: text`. A refusal ends as `status: "aborted"` through the existing `finally`.
- CLI `agentic-run` adds `--launcher {local,container}` (default `local`), `--image ID`, `--keys-file PATH`, `--index PATH` (default `get_index_path()`), `--live`, and `--reference-run DIR`.
  - `--live` and `--dry-run` are mutually exclusive, and `--live` with `--launcher local` is a parser error.
  - `--launcher container` without `--live` is a dry run.
  - The CLI computes `run_id` once and passes it to both `AgenticConfig` and `SandboxConfig`.

- [ ] **Step 1: Write the failing tests**

```python
def test_reference_rates_aggregate_every_attempt_of_a_cell(tmp_path) -> None
    # fixture run dir: 90 logical cells, each with one transcript and one discarded attempt (180 records),
    # ledger-events booking $9 in all -> usd_per_cell == Decimal("0.10"), not 0.05
def test_reference_rates_from_a_finished_scripted_run(tmp_path) -> None
def test_the_gate_refuses_a_provider_whose_scaled_cost_exceeds_its_remaining_cap() -> None
def test_the_gate_refuses_a_model_whose_scaled_time_exceeds_24_hours() -> None
def test_a_live_run_larger_than_the_pilot_needs_a_reference_run() -> None   # 91 openai trajectories
def test_confirmation_lists_every_provider_with_its_remaining_cap_and_estimate() -> None
def test_ci_refuses_a_live_run_even_with_a_terminal(monkeypatch) -> None
    # CI=1 and a confirm that would answer yes -> "refused under CI"; confirm never called
def test_a_non_tty_stdin_refuses_a_live_run(monkeypatch) -> None
    # CI unset; interactive_confirm with a stdin whose isatty() is False -> "not confirmed"
def test_interactive_confirm_accepts_only_yes() -> None                    # "y", "YES ", "" -> False
def test_the_confirmation_prompt_never_reaches_stdout(capsys) -> None
    # a TTY-like stdin answering "yes\n": True; capsys stdout == ""; the stderr stream holds the table
    # and the question
async def test_the_gate_runs_before_any_key_is_read_or_container_started(tmp_path, monkeypatch) -> None
    # CI=1, ContainerLauncher(FakeDocker), keys_file pointing at a missing path: the error is the CI
    # refusal, FakeDocker recorded no call, and run-summary.json status is "aborted"
def test_a_declined_confirmation_starts_nothing(tmp_path, monkeypatch) -> None
def test_the_default_model_set_is_the_hosted_arm(tmp_path) -> None
    # container launcher, models unset: claude-sonnet-5-5, gpt-6.1-sol, gemini-3.8-flash; gemma4-12b
    # only when named
def test_run_agentic_refuses_mixing_the_air_gapped_and_hosted_arms(tmp_path) -> None
def test_cli_container_launcher_defaults_to_a_dry_run(tmp_path, monkeypatch) -> None
    # main([... "--launcher", "container", "--image", "sha256:" + "0" * 64]) prints dry_run true;
    # Sandbox is patched to fail on any docker call
def test_cli_refuses_live_with_the_local_launcher() -> None
```

The existing scripted end-to-end tests stay unchanged: a run of scripted
families never reaches the gate.

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
git commit -m "feat(benchmarks): one live authorization gate, per-cell reference rates and the full-run gate"
```

---

### Task 14: The air-gapped arm's host checks

**Files:**
- Create: `benchmarks/agentic/airgap.py`
- Modify: `benchmarks/agentic/run.py` (preflight, per-trajectory checks, final check)
- Create: `tests/benchmarks/fixtures/agentic/model-matrix-local.yml` (one family, `scripted-local`: provider `ollama`, arm `air-gapped`, api `scripted`, `max_output_tokens: 1024`, `framing_allowance_tokens: 256`) and `script-local.yml`
- Test: `tests/benchmarks/test_agentic_airgap.py`

**Interfaces:**
- Produces:
  - `OUTSIDE_TARGETS = (("pypi.org", 443), ("1.1.1.1", 443))`, `REQUIRED_CONTEXT = 32_768`, `MIN_OLLAMA = (0, 40, 14)` (#200 ruling 6), `OLLAMA_LOCAL = "http://127.0.0.1:11434"`.
  - `default_routes(*, system: str | None = None, read: Callable[[], str] | None = None) -> list[str]`. On macOS it reads the lines of `netstat -rn` (IPv4 and IPv6) whose first column is `default`. On Linux it reads `/proc/net/route` lines with destination `00000000` and `/proc/net/ipv6_route` default entries.
  - `host_offline_checks(*, connect=socket.create_connection, routes=default_routes) -> list[dict]`. It returns `outside_connect:<host>` for each `OUTSIDE_TARGETS` entry (a 5 s connect that must raise) and `no_default_route` (detail: the offending lines). Each record carries `at` (UTC ISO) and `passed`.
  - `ollama_preflight(model_id: str, *, base: str = OLLAMA_LOCAL, fetch: Callable[[str, bytes | None], dict] | None = None) -> dict`.
    - The default `fetch` uses a urllib opener built with `ProxyHandler({})`, so host proxy variables are ignored.
    - It reads `/api/version`, then the model's `digest` and `details` from `/api/tags`. It loads the model (`POST /api/generate` `{"model", "prompt": "", "keep_alive": "60m"}`), then reads `/api/ps` `context_length` and `POST /api/show` `parameters`.
    - It raises `BenchmarkValidationError` when the version is below `MIN_OLLAMA`, the model is missing, or `context_length != REQUIRED_CONTEXT`.
  - `class AirGapBroken(BenchmarkValidationError)`.
  - `class AirGapWatch(out_dir: Path, checks: Callable[[], list[dict]] = host_offline_checks)` with `async check(when: str) -> None`. It appends each record, with `when`, to `out_dir/airgap/records.jsonl`, and raises `AirGapBroken` naming the first failed check. It exposes `rounds: int` (the number of `check` calls made) and `broken: bool`.
- `run_agentic(..., offline_checks=host_offline_checks, preflight=ollama_preflight)`. When the families are air-gapped and the run is not a dry run:
  - Before `run_jobs`, it writes `preflight(...)` for each air-gapped family to `out/ollama.json` (skipped for `api == "scripted"`) and runs `watch.check("before_first")`.
  - Each `trajectory` or `discarded` message from an air-gapped job runs `watch.check(f"after {cell}/{corpus_id}")` before the host acknowledges it, so before the worker starts its next trajectory.
  - After `run_jobs` it runs `watch.check("after_last")`.
  - The summary records `airgap: {"checks": <rounds>, "broken": <bool>}`. A broken arm aborts the run with the failed check's name (design §7.4: "The first failed check stops the arm").

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
def test_ollama_preflight_refuses_a_version_before_0_40_14() -> None       # "0.40.8" -> raises
def test_ollama_preflight_records_the_reported_digest() -> None
async def test_the_arm_checks_the_host_before_each_trajectory_and_after_the_last(tmp_path) -> None
    # scripted-local x no-docs, 2 questions x 1 repeat, offline_checks counting calls: 1 + 2 + 1 = 4 rounds
async def test_the_first_failed_check_stops_the_arm(tmp_path) -> None
    # the 2nd round fails: status "aborted", abort_reason names "no_default_route", the last record failed
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

### Task 15: C3 evidence: `airgap/checks.json`

PR 5 reads C3's offline evidence through `load_offline_evidence(run_dir)`.
It reads `all_passed` from this file alone and fails closed on any other
shape (#199, owner question 9).

**Files:**
- Modify: `benchmarks/agentic/airgap.py` (`write_airgap_evidence`)
- Modify: `benchmarks/agentic/run.py` (`write_outcome` calls it for an air-gapped live run)
- Test: `tests/benchmarks/test_agentic_airgap.py`

**Interfaces:**
- Consumes: `AirGapWatch.rounds` and `.broken` (Task 14); `out/egress/summary.json` (Task 12).
- The shape, bound by #199:

```json
{"all_passed": true, "checks": 302, "internet_hosts": []}
```

  - `checks` is the number of check rounds: before the first trajectory, after each reported trajectory or discard, and after the last.
  - `internet_hosts` lists sorted, unique `"host:port"` strings: every host the air-gapped jobs actually reached, from their `hosts_allowed` in `egress/summary.json`, except `host.docker.internal:11434` (round-2 ruling C7). A denied attempt means nothing left the sandbox. It stays visible in `hosts_denied`, and PR 5's report shows it there.
  - `all_passed` is `true` only when all of these hold: every round passed, the arm was not stopped, `egress/summary.json` exists with `isolation_probe` `"pass"`, and `internet_hosts` is empty.
- Produces: `airgap.write_airgap_evidence(out_dir: Path, *, rounds: int, broken: bool, job_ids: Sequence[str]) -> dict[str, Any]`. It reads `egress/summary.json` itself, asserts `set(result) == {"all_passed", "checks", "internet_hosts"}`, and writes `out_dir/airgap/checks.json`.
- `run_agentic`'s `write_outcome` calls it after `write_egress_summary` for every air-gapped run that is not a dry run, aborted runs included (`all_passed: false`).

- [ ] **Step 1: Write the failing tests**

```python
def test_airgap_evidence_has_exactly_the_bound_keys(tmp_path) -> None
def test_a_denied_pypi_attempt_keeps_all_passed_true(tmp_path) -> None
    # egress summary: gemma4-12b--python-docs hosts_allowed ["host.docker.internal:11434"],
    # hosts_denied ["pypi.org:443"], isolation_probe "pass"; rounds 4, not broken
    # -> {"all_passed": True, "checks": 4, "internet_hosts": []}
def test_a_reached_internet_host_fails_the_evidence(tmp_path) -> None
    # hosts_allowed ["pypi.org:443", "host.docker.internal:11434"] -> internet_hosts ["pypi.org:443"], False
def test_missing_egress_evidence_or_a_failed_probe_fails_the_evidence(tmp_path) -> None
async def test_an_air_gapped_run_writes_checks_json_even_when_aborted(tmp_path) -> None
    # the Task 14 scripted-local run with a failing second round: airgap/checks.json exists,
    # all_passed False, checks == 2
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --locked pytest tests/benchmarks/test_agentic_airgap.py -k "evidence or checks_json or pypi or internet" -q`
Expected: FAIL (`write_airgap_evidence` missing).

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

### Task 16: Run script, runbooks, opt-in docker test, then the PR

**Files:**
- Create: `benchmarks/sandbox/run.sh` (executable)
- Create: `docs/benchmarks/SANDBOX-RUNBOOK.md`
- Modify: `README.md` (new `## Air-gapped install` section after `## Retrieved docs cache`)
- Create: `tests/benchmarks/test_agentic_sandbox_docker.py`
- Modify: `CHANGELOG.md` (`## [Unreleased]`)
- Modify: `tests/benchmarks/test_agentic_sandbox_files.py` (`run.sh` checks)

**Interfaces:**
- Consumes: everything above.
- `run.sh` is fixed copy. It has no dirty-tree check; `verify_image` owns that check (round-2 ruling E16):

```bash
#!/usr/bin/env bash
# Build the sandbox image from HEAD, pull the pinned proxy, then run agentic-run in containers.
# A dry run unless --live is passed. IMAGE=sha256:... skips the build (the air-gapped arm).
# KEYS_FILE (default .env.local) is read only by the container launcher, one key per worker,
# into a bootstrap frame on that worker's stdin.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
for tool in git docker uv cat; do
  command -v "$tool" >/dev/null || { echo "run.sh: $tool not found" >&2; exit 2; }
done
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

- `SANDBOX-RUNBOOK.md` has these sections, each with its exact commands. Every `run.sh` command passes `--models` explicitly (round-2 ruling D11).
  1. **Prerequisites:** Docker Desktop ≥ 4.96.1 (Engine ≥ 28.0), context `desktop-linux`, and no `proxies` key in `~/.docker/config.json`; the index built for 3.10–3.14 (CONTRIBUTING §3); a keys file with mode 600 outside any commit.
  2. **Where keys live** (round-2 rulings A1 and E15):
     - A key exists only in the host process's memory, while `bootstrap_frame` builds the frame, and in that one worker process's environment.
     - It is not in any container's environment or config (`docker inspect`), not in the docker CLI's environment (`ps -E`), not in argv, and not in any file but the keys file.
     - This is a recorded departure from design §7.3's `--env-file`.
  3. **Dry run:** `benchmarks/sandbox/run.sh --models claude-sonnet-5-5,gpt-6.1-sol,gemini-3.8-flash --corpus docs/benchmarks/corpus.yml --out <dir>`.
  4. **Order of the first live calls** (#200 ruling 4, as amended by round-2 ruling D10):
     1. the keyless docker test, before any live call;
     2. the ~$1 smoke run, once PR 4 is merged. It checks the plumbing and the model limits (`run-summary.json` `model_limits_mismatch`) and produces no reported number;
     3. G1, after PR 5;
     4. the pilot.
     G1 still gates the pilot and every reported number.
  5. **Keyless docker test:** `BENCHMARK_DOCKER_TESTS=1 BENCHMARK_SANDBOX_IMAGE=<id> uv run --locked pytest tests/benchmarks/test_agentic_sandbox_docker.py -q`.
     - A **"Docker Desktop results"** subsection records the outcome of the three undocumented behaviours (#200 ruling): internal-network reach to the Mac, `host.docker.internal` against a 127.0.0.1 bind, and large `docker run -i` output.
     - It also records which DNS fallback was used, if any (#200 ruling 5).
  6. **Smoke run** (design §14): one question, k = 1, `--models claude-sonnet-5-5,gpt-6.1-sol,gemini-3.8-flash`, every system, `--live`.
  7. **Pilot:** the 10 fixed questions, k = 3, the same `--models`, `--live`.
  8. **Full run:** `--reference-run <pilot dir> --live`, plus what to do when the gate refuses: fewer repeats or questions, chosen before running and disclosed.
  9. **Artifacts:**
     - `egress/summary.json` (C1 evidence, read by PR 5);
     - `egress/<job>/access.log`, `squid.conf` and `collected.json`;
     - `probe/<job>.json` and `probe/control.json`;
     - `sandbox.json`;
     - `airgap/checks.json` (C3 evidence, read by PR 5) and `airgap/records.jsonl`;
     - `ollama.json`.
  10. **Air-gapped arm.** It does not run before 2026-11-17, when Ollama v0.40.14 is 14 days old (#200 ruling 6). In this order:
      - While online: install Ollama v0.40.14 or newer, the newest release at least 14 days old. Quit the menu-bar app, so no updater runs, and use the CLI only. Run `ollama pull gemma4:12b`, build the image with `run.sh` and note its ID, and pull the Squid image.
      - Install the index by following README "Air-gapped install" literally.
      - Set Docker Desktop's memory limit to 4 GB.
      - Start `OLLAMA_CONTEXT_LENGTH=32768 OLLAMA_NO_CLOUD=1 ollama serve` in its own terminal.
      - Go offline: turn off Wi-Fi, unplug Ethernet, and turn off VPNs and iCloud Private Relay (each can leave a `utun` default route).
      - Run the 5-question local smoke run first (design §14): `IMAGE=<id> benchmarks/sandbox/run.sh --models gemma4-12b --systems python-docs,no-docs --corpus docs/benchmarks/corpus.yml --questions <5 ids> --repeats 1 --out <dir> --live`. If tool calls fail often, stop and report; design §14 names `gemma4:e4b` or a stronger machine.
      - Then run the arm: the same command with all 50 questions, k = 3, and a new `--out`.
      - If the probe's Ollama check fails, set `OLLAMA_HOST=0.0.0.0:11434` for this arm only, while the Mac is offline, and revert it afterwards (#200 ruling 7).
  11. **Cleanup:** `docker ps -a --filter label=org.python-docs-mcp.benchmark-run=<run_id>` lists anything left. Nothing should be.
- README `## Air-gapped install`, per design §11.4:
  - On a connected machine with the same OS, CPU architecture and Python minor version (or using pip's `--platform`, `--python-version` and `--only-binary=:all:`), run `pip download python-docs-mcp-server==<version> -d wheels/` and `python-docs-mcp-server build-index --versions 3.10,3.11,3.12,3.13,3.14`.
  - Copy `wheels/` and `index.db` across.
  - On the target, run `pip install --no-index --find-links wheels/ python-docs-mcp-server==<version>`.
  - Place `index.db` at `~/Library/Caches/mcp-python-docs/index.db` (macOS) or `~/.cache/mcp-python-docs/index.db` (Linux, or `$XDG_CACHE_HOME/mcp-python-docs/`).
  - Set `PYTHON_DOCS_MCP_DISABLE_AUTO_INDEX=1` and run `python-docs-mcp-server doctor`.
  - `lookup_package_docs` needs PyPI and returns a clear note when offline. There is no new CLI command.
- The docker test module skips unless `BENCHMARK_DOCKER_TESTS=1` and `BENCHMARK_SANDBOX_IMAGE` is set. It uses scripted families and synthetic values only, with no key and no provider call.

- [ ] **Step 1: Write the failing tests**

`test_agentic_sandbox_files.py`:

```python
def test_run_sh_builds_from_git_archive_and_defaults_to_a_dry_run() -> None
    # executable bit set; "git archive --format=tar HEAD" present; "--live" never appears in it;
    # "set -euo pipefail" present; no "git status", "printenv", "env |", "set -x" or "cat .env" in it
```

`test_agentic_sandbox_docker.py` (opt-in):

```python
def test_image_runs_unprivileged_with_cat_without_node_or_proxy(image) -> None
async def test_a_scripted_run_completes_inside_containers(tmp_path, image) -> None
    # run_agentic with ContainerLauncher, scripted-anthropic x {no-docs, python-docs} (python-docs uses
    # an index built with tests/conftest.py's stability builder in tmp_path): status completed; every
    # probe record passed; egress/<job>/access.log holds both denials; egress/summary.json has the bound
    # keys, isolation_probe "pass" and empty host lists (probe lines excluded); the persistent docs
    # cache file was created on the tmpfs
async def test_a_trajectory_frame_over_100000_bytes_round_trips_through_a_container(tmp_path, image) -> None
    # the worker runs with --log-driver none: the attached stream still carries every frame intact
async def test_an_ip_literal_connect_whose_ptr_names_an_allowlisted_host_is_denied(tmp_path, image) -> None
    # an egress allowlisting ("localhost", 443); a probe-container CONNECT 127.0.0.1:443 through it
    # answers 403 and logs TCP_DENIED (Squid's hosts file maps 127.0.0.1 to localhost)
async def test_a_service_bound_to_the_macs_loopback_through_the_proxy(tmp_path, image) -> None
    # a host listener on 127.0.0.1:<port>; an egress allowlisting ("host.docker.internal", <port>);
    # a probe allowed_http GET reaches it. Record the result in the runbook either way.
async def test_a_docker_config_with_proxies_fails_the_isolation_probe(tmp_path, image, monkeypatch) -> None
    # DOCKER_CONFIG=<tmp dir> whose config.json sets proxies.default.httpsProxy (plus a copy of the
    # current context): IsolationProbeFailed naming HTTPS_PROXY
async def test_no_container_or_network_with_the_run_label_remains(tmp_path, image) -> None
```

- [ ] **Step 2: Run the static tests to see them fail**

Run: `uv run --locked pytest tests/benchmarks/test_agentic_sandbox_files.py -q`
Expected: FAIL (no `run.sh`).

- [ ] **Step 3: Write `run.sh`, the runbook, the README section, the docker test and the CHANGELOG**

CHANGELOG under `## [Unreleased]`:
- **Added:**
  - the benchmark sandbox (`benchmarks/sandbox/`), with one hardened container and one logging Squid egress proxy per (model, system);
  - `--launcher container`, a dry run by default, with `--live`, one authorization gate (refused under `CI`, interactive confirmation) and `--reference-run`;
  - the Ollama client for the air-gapped arm;
  - the "Air-gapped install" README section.
- **Changed:**
  - `docs-fetch` and `python-docs` reach the network only through the run's egress proxy.
  - Keys reach a worker only as a bootstrap frame on its stdin, never through a container's environment, `--env-file` or argv. This is a recorded departure from design §7.3.
- **Security:** the dependency audit also covers `benchmarks/sandbox/fetch-requirements.txt`.

- [ ] **Step 4: Run the full gate, then the opt-in docker test once**

Run the three gate commands.
Expected: `All checks passed!`, `0 errors`, every test passing, and the docker module skipped.

Then run the docker test (needs Docker Desktop and the image from Task 7; no key):
`BENCHMARK_DOCKER_TESTS=1 BENCHMARK_SANDBOX_IMAGE=<id> uv run --locked pytest tests/benchmarks/test_agentic_sandbox_docker.py -q`
Expected: 7 passed. Write the three behaviours' results into the runbook's "Docker Desktop results". If the DNS check fails (sandbox note §B.3), apply the #200 ruling 5 fallbacks in order and record which one held. Do not weaken the check.

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
  - the one workflow change;
  - the gate output and the docker-test output;
  - the owner rulings applied.

---

## Not in this plan

- **PR 5 (#199):**
  - the criteria file, the probes, the judge panel, the reference grader and post-hoc token counting, each in its own keyed sandbox job built on `Sandbox` and gated by `authorize_live`;
  - batch units and `split_batch`; `scoring.py` provenance; grounding;
  - the C1 and C3 report sections, which read only `egress/summary.json` and `airgap/checks.json`;
  - the per-graded-answer term of the full-run gate, from `grading/summary.json`;
  - the report sections.
- **Dropped in round 2 (ruling D10):** `Sandbox.run_once`, `limits.py` and the `agentic-limits` command. Model limits now arrive in `system_info` (Task 6).
- **Maintainer-run, after this PR merges, never by an implementer:** the smoke run, G1, the pilot, the full run and the air-gapped arm, in the runbook's order.
- **Round 2:** the Streamable HTTP transport, the competitor systems, and a fresh MCP session per cell for Ref.tools.
- **Not built:**
  - Podman or Colima support;
  - TLS inspection;
  - a CI workflow for the docker test;
  - an `OUTPUT_CAP_STOPS` entry for Ollama's `length` (its stop reason is recorded per call; the 1% rule covers the hosted pilot only);
  - limits for OpenAI and Ollama models, whose model endpoints report none.

## Owner rulings (applied)

From #200 (round 1), as amended by the round-2 message on #200:

1. **One PR**, labelled `supervisor-review`.
2. **`mcp-server-fetch`:** a hashed requirements file used only in the image, with no `pyproject.toml` or `uv.lock` change. PR 4 adds that file to the Security workflow's dependency audit, with a test (Task 4).
3. **Proxy:** `ubuntu/squid` 6.6 by digest.
4. **First live calls** (amended by D10):
   1. the keyless docker test;
   2. the smoke run, which now also reports model limits;
   3. G1, after PR 5;
   4. the pilot.
   The smoke run produces no reported number.
5. **DNS:** the probe stays strict. The fallbacks are tried in order, and the runbook records which one was used.
6. **Ollama:** v0.40.14 or newer, enforced by the preflight. The air-gapped arm does not run before 2026-11-17; the hosted arms do not wait for it.
7. **Reaching Ollama:** the default 127.0.0.1 bind. `OLLAMA_HOST=0.0.0.0:11434` only for the air-gapped arm, only while the Mac is offline, and reverted afterwards.
8. **Confirmation:** the remaining cap is shown as the worst case. More than 90 metered trajectories per provider needs `--reference-run`.
9. **Keys** (re-ruled, A1): a stdin bootstrap frame. No key in any container environment or config, any docker CLI environment, any argv, the host's `os.environ`, or any file but the keys file. This departs from design §7.3's `--env-file`, as recorded in the CHANGELOG and the runbook.
10. **Runtime:** Docker Desktop, Engine 28.0 or newer, and a local `unix://` daemon only (round 3).
11. **`egress/summary.json` meanings:**
    - hosts are `"host:port"`;
    - the lists cover query time only, with probe lines excluded;
    - `pypi.org:443` stays in `hosts_denied`;
    - `lookup_package_docs_attempts` counts the model's tool calls.
    PR 5 treats `pypi.org:443` as the C1 exception only when the attempts count is above 0.
12. **`airgap/checks.json`** (re-ruled, C7): `internet_hosts` comes from `hosts_allowed` only. `all_passed` needs every round passed, the arm not stopped, a passing probe, and no internet host. `checks` counts rounds.
13. **The three undocumented Docker behaviours** are settled by the keyless docker test before any live call, with results in the runbook. The two young pins are re-checked at implementation time. PR 4 merges before PR 5.

14. **Round 3 (reviews of `b4e54a1`):**
    - an abnormal proxy end (not running before our stop, for example exit 137, or `OOMKilled`) fails that job's collection (Task 10);
    - `reserve` events carry `job_id`, and the C6 rule attributes `ok` settles through it; an unattributed `ok` settle fails the evidence (Tasks 5, 12);
    - the log directory is `chmod`ed to 0o777 after `mkdir` (Task 10);
    - a local `unix://` daemon only (Task 10);
    - `host_addresses` works offline (Task 11);
    - the confirmation prompt goes to stderr and is read with `stdin.readline()` (Task 13);
    - workers run with `--log-driver none` (Task 10);
    - a network that keeps IPv6 is refused, with no fallback (Task 10);
    - sandbox note §§B.2 and B.3 are corrected.

No open question remains for this plan.
