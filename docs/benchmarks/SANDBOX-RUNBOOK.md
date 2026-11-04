# Agentic benchmark sandbox: operator runbook

How the maintainer runs the agentic benchmark in its sandbox (design §§7.2–7.4, §12, §14).
Every live call in this runbook is the maintainer's, after PR 4 merges. An implementer never
runs one. Every `run.sh` command names its models with `--models`.

`benchmarks/sandbox/run.sh` builds the worker image from `git archive HEAD`, pulls the
pinned Squid image, and runs `agentic-run --launcher container`. It is a dry run unless you
add `--live`.

## 1. Prerequisites

- Docker Desktop 4.96.1 or newer (Engine 28.0 or newer), with the context `desktop-linux`.
  The sandbox refuses any daemon that is not a local `unix://` socket, including a
  `DOCKER_HOST` that points elsewhere.
- No `proxies` key in `~/.docker/config.json`. The docker CLI would put those proxy
  variables into every new container, and the isolation probe then refuses the run.
- The docs index built for 3.10–3.14 (CONTRIBUTING §3):
  `uv run python-docs-mcp-server build-index --versions 3.10,3.11,3.12,3.13,3.14`.
- A keys file, mode 600, outside any commit (`.env.local` by default, which `.gitignore`
  covers): one `NAME=VALUE` line per provider key. `chmod 600 .env.local`.
- A clean checkout: the image must hold exactly the commit you run. `run.sh` builds from
  `HEAD`, and the launcher refuses a dirty tree or an image built from another commit.

## 2. Where keys live

- A key exists only in the host process's memory, while `bootstrap_frame` builds the
  frame, and in the environment of the one worker process that uses it.
- The host reads the one key a worker needs from the keys file after the authorization
  gate and after every isolation probe passed. It writes the key as a bootstrap frame on
  that worker's stdin, before the job frame. The worker puts it in its own environment, in
  memory, before it protects its output or imports any SDK.
- A key is never in a container's environment or config (`docker inspect`), never in the
  docker CLI's environment (`ps -E`), never in argv, and never in any file but the keys
  file. The host's own environment never holds one.
- This is a recorded departure from design §7.3, which passed keys with `--env-file`
  (#200, ruling A1): Docker stores `-e` and `--env-file` values in the container config.

## 3. Dry run

```bash
benchmarks/sandbox/run.sh --models claude-sonnet-5-5,gpt-6.1-sol,gemini-3.8-flash \
  --corpus docs/benchmarks/corpus.yml --out <dir>
```

It builds the image, plans every cell and writes the plan, and opens no ledger and no
container.

## 4. Order of the first live calls

#200 ruling 4, as amended by round-2 ruling D10:

1. the keyless docker test (section 5), before any live call;
2. the ~$1 smoke run (section 6), once PR 4 is merged. It checks the plumbing and the model
   limits (`run-summary.json` `model_limits_mismatch`) and produces no reported number;
3. G1, after PR 5;
4. the pilot (section 7).

G1 still gates the pilot and every reported number.

## 5. Keyless docker test

```bash
IID=$(mktemp)
git archive --format=tar HEAD | docker build --file benchmarks/sandbox/Dockerfile \
  --label "org.opencontainers.image.revision=$(git rev-parse HEAD)" --iidfile "$IID" -
docker pull "$(uv run --locked python -c 'from benchmarks.agentic.sandbox import SQUID_IMAGE; print(SQUID_IMAGE)')"
BENCHMARK_DOCKER_TESTS=1 BENCHMARK_SANDBOX_IMAGE="$(cat "$IID")" \
  uv run --locked pytest tests/benchmarks/test_agentic_sandbox_docker.py -q
```

It uses scripted models only, with no key and no provider call, on a clean checkout.

### Docker Desktop results

Pending: the implementer fills this in from the first run of the keyless docker test.

## 6. Smoke run (design §14)

One question, k = 1, every system:

```bash
benchmarks/sandbox/run.sh --models claude-sonnet-5-5,gpt-6.1-sol,gemini-3.8-flash \
  --corpus docs/benchmarks/corpus.yml --questions <one id> --repeats 1 --out <dir> --live
```

The run shows each provider's spend, cap, remaining budget and estimate, and starts only
after you type `yes` at the terminal. It refuses under `CI`. Then read
`run-summary.json` `model_limits_mismatch` and correct `docs/benchmarks/model-matrix.yml`
by hand in a docs PR if it lists anything.

## 7. Pilot

The 10 fixed questions, k = 3, every system:

```bash
benchmarks/sandbox/run.sh --models claude-sonnet-5-5,gpt-6.1-sol,gemini-3.8-flash \
  --corpus docs/benchmarks/corpus.yml --questions <10 ids> --repeats 3 --out <pilot dir> --live
```

That is 90 trajectories per provider, the most a run may plan without a reference run.

## 8. Full run

```bash
benchmarks/sandbox/run.sh --models claude-sonnet-5-5,gpt-6.1-sol,gemini-3.8-flash \
  --corpus docs/benchmarks/corpus.yml --out <dir> --reference-run <pilot dir> --live
```

The full-run gate scales the pilot's cost and time per logical cell (every attempt of a
cell included) by 1.25. It refuses a provider whose estimate is over its remaining cap and
a model whose time is over 24 hours. When it refuses, choose fewer repeats or fewer
questions before you run, and disclose that choice with the results.

## 9. Artifacts

- `egress/summary.json`: the C1 evidence, read by PR 5.
- `egress/<job>/access.log`, `squid.conf` and `collected.json`: each job's proxy log,
  config and how its proxy ended.
- `probe/<job>.json` and `probe/control.json`: the isolation probe records.
- `sandbox.json`: the image, its revision, the proxy image, the engine version, and the
  docker context and endpoint.
- `airgap/checks.json` (the C3 evidence, read by PR 5) and `airgap/records.jsonl` (every
  check round).
- `ollama.json`: the Ollama version, the model's digest and details, and its context.

## 10. Air-gapped arm

It does not run before 2026-11-17, when Ollama v0.40.14 is 14 days old (#200 ruling 6). In
this order:

1. While online:
   - install Ollama v0.40.14 or newer, the newest release at least 14 days old;
   - quit the menu-bar app, so no updater runs, and use the CLI only;
   - run `ollama pull gemma4:12b`;
   - build the image with `run.sh` and note its ID;
   - pull the Squid image.
2. Install the index by following README "Air-gapped install" literally.
3. Set Docker Desktop's memory limit to 4 GB.
4. Start `OLLAMA_CONTEXT_LENGTH=32768 OLLAMA_NO_CLOUD=1 ollama serve` in its own terminal.
5. Go offline: turn off Wi-Fi, unplug Ethernet, and turn off VPNs and iCloud Private Relay
   (each can leave a `utun` default route).
6. Run the 5-question local smoke run first (design §14):

   ```bash
   IMAGE=<id> benchmarks/sandbox/run.sh --models gemma4-12b --systems python-docs,no-docs \
     --corpus docs/benchmarks/corpus.yml --questions <5 ids> --repeats 1 --out <dir> --live
   ```

   If tool calls fail often, stop and report: design §14 names `gemma4:e4b` or a stronger
   machine.
7. Then run the arm: the same command with all 50 questions, k = 3, and a new `--out`.

If the probe's Ollama check fails, set `OLLAMA_HOST=0.0.0.0:11434` for this arm only,
while the Mac is offline, and revert it afterwards (#200 ruling 7).

## 11. Cleanup

```bash
docker ps -a --filter label=org.python-docs-mcp.benchmark-run=<run_id>
docker network ls --filter label=org.python-docs-mcp.benchmark-run=<run_id>
```

Nothing should be listed. Every container and network the sandbox creates carries that
label; remove only what it lists, by name.
