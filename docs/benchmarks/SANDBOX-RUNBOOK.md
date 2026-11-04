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
- Use a new, empty output directory outside the checkout for each run (for example,
  `"$HOME/benchmark-runs/smoke"`). The host writes evidence before it checks the tree.
- A clean checkout: the image must hold exactly the commit you run. `run.sh` builds from
  `HEAD`, and the launcher refuses a dirty tree or an image built from another commit.

## 2. Where keys live

- The host creates each stopped worker by ID after all isolation probes pass. It attaches
  stdin to that ID with `docker start -ai` and removes the same ID on teardown.
- A key exists in the host process's memory during validation and bootstrap, and in the
  environment of the one worker process that uses it.
- The host reads the one key a worker needs from the keys file after the authorization
  gate and after every isolation probe passed. It checks every planned key before starting
  any worker, then rereads the matching key for each launch. It writes the key as a bootstrap frame on
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

First run: 2026-11-04, Docker Desktop on Apple silicon (context `desktop-linux`, Engine
29.9.2), worker image built from `05e2ff8`, Squid image by its pinned digest. Result:
7 passed, and no container or network with the run label remained.

| Behaviour (#200 record) | Result |
|---|---|
| Internal-network reach to the Mac | None. From a job's internal network, `host.docker.internal` and `gateway.docker.internal` do not resolve (`gaierror`), and the Mac's LAN address (192.168.1.28) gives `OSError`, on the canary port and on 11434. The canary never saw the probe's token. |
| The control probe | A container on the outside network reached the host canary through `host.docker.internal`, so the silent canary above is meaningful. |
| DNS on internal networks | `pypi.org` does not resolve (`gaierror`). The strict DNS check passed: **no #200 ruling 5 fallback was needed.** |
| `--ipv6=false` on Desktop | Honoured: `EnableIPv6` is false. The isolated gateway mode assigns no IPAM gateway address, so the probe has no gateway to try. |
| `host.docker.internal` against a 127.0.0.1 bind | Reachable through the proxy: a service bound to the Mac's 127.0.0.1 only answered 200 to a GET through an egress allowing it. Ollama's default bind should work for the air-gapped arm; ruling 7's `OLLAMA_HOST=0.0.0.0` fallback stays for the case where the probe's Ollama check fails. |
| Large attached worker output | A trajectory frame over 150,000 bytes round-tripped intact with the original `docker run -i` launcher and again on November 4 with `docker create` followed by `docker start -ai`. |
| Attach with `--log-driver none` | Works: every worker frame and reply passed over the attached stdin and stdout. |
| Squid writing to the bind mount | The #200 run produced `access.log` and `cache.log` (mode 640) on the mounted volume, with one access-log line per connection attempt. |
| Squid's exit after `docker stop` | Exit code 0, `Running` false, "Exiting normally", within `shutdown_lifetime`. |
| IP literal whose PTR names an allowed host | Denied: a `CONNECT 127.0.0.1:443` through an egress allowing `localhost:443` got 403 and logged `TCP_DENIED_ABORTED` (the probe closes after reading the status line). |
| Proxies in the docker CLI config | A `config.json` with `proxies` makes the CLI inject `HTTPS_PROXY` into the probe container, and the isolation probe refuses the run, naming it. |

Two observations for the owner:

- The image tagged `6.6-24.04_edge` at the pinned digest runs **Squid 6.14**
  ("Starting Squid Cache version 6.14"), not 6.6. Every directive used here works, and the
  exit status after SIGTERM is 0.
- Squid's ICMP `pinger` helper cannot start without `NET_RAW` (`cache.log`: "Unable to
  open any ICMP sockets"). The proxy runs normally without it.

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
- `egress/<job>/` holds the public `squid.conf`, the scanned `access.log` and `cache.log`,
  and `collected.json` with the stop state and collection result.
- `probe/<job>.json` and `probe/control.json`: the isolation probe records.
- `sandbox.json`: the image, its revision, the proxy image, the engine version, and the
  docker context and endpoint.
- `airgap/checks.json` (the C3 evidence, read by PR 5) and `airgap/records.jsonl` (every
  check round).
- `ollama.json`: the Ollama version, the model's digest and details, and its context.

Worker stderr stays in an unlinked file until teardown. The host publishes a scrubbed
snapshot in the run directory after stopping the worker. A snapshot over 8 MiB fails
the run and leaves that worker without a public stderr log. The ledger still accounts
for its open reservation.

Squid writes to a job-specific temporary spool outside the run directory. Its parent is
mode 0700. Docker Desktop mounts only the writable child directory (mode 0777) and the
read-only config file. The probe reads the private access log and scans its record before
publishing the exact prefix used to separate probe traffic from query traffic.

At close, the runner reads only regular `access.log` and `cache.log` files without
following symlinks. Each public log appears by atomic rename after its key scan. A key
match fails the run even if writing the scrubbed log fails. The runner removes the private
spool on teardown. A `probe/<job>.collection-pending` marker means collection is
incomplete or failed and C1 cannot pass, even if some public files are present.

The last four access-log fields record the client status (`%>Hs`), result (`%Ss`),
hierarchy (`%Sh`), and next-hop HTTP status (`%<Hs`). Each record must have a UTC
timestamp with millisecond precision and a valid client IP address.
The pinned Squid 6.14 image produced these records against a loopback origin in a
keyless container with networking disabled. [Squid log format](https://www.squid-cache.org/Doc/config/logformat/)

| Client status, result, hierarchy, next-hop status | Evidence |
| --- | --- |
| `500 TCP_MISS HIER_DIRECT 500` | The origin returned 500. Include the target in `hosts_allowed`. |
| `500 TCP_MISS_ABORTED HIER_DIRECT 500` | The origin responded before the transfer broke. Include the target. |
| `200 TCP_MISS_ABORTED HIER_DIRECT 200` | The origin returned 200 and the body ended early. The nine-field log proves contact. The seven-field equivalent leaves contact unconfirmed. |
| `0 TCP_MISS_ABORTED HIER_DIRECT -` | The client closed before Squid logged a reply. Reachability is unconfirmed. |
| `503 TCP_MISS_ABORTED HIER_DIRECT -` | A refused connection produced a Squid 503. Reachability is unconfirmed. |
| `200 TCP_TUNNEL HIER_DIRECT -` | Squid established the CONNECT tunnel. This can corroborate a billed provider call. |
| `403 TCP_DENIED HIER_NONE -` | The request ACL blocked the attempt. Include the target in `hosts_denied`. |

Squid can append `_TIMEDOUT` and then `_ABORTED` to the result. Either can occur
before an origin reply arrives. [Squid 6.14 result tags](https://github.com/squid-cache/squid/blob/SQUID_6_14/src/LogTags.cc#L82-L89)

An unconfirmed attempt can pass isolation only if its host, port and method fit that
job's ACL. In the air-gapped arm this permits plain HTTP to local Ollama. An
unconfirmed external attempt fails isolation and C3 even when `internet_hosts` is
empty. Unreadable or unrecognized evidence also fails isolation. Older seven-field
logs still support established 200 tunnels, forwarded 2xx HTTP responses and request ACL
denials. A recognized older failed MISS or aborted request can preserve isolation for a permitted destination, but cannot establish that it reached that destination.

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
7. Run the arm with all 50 questions, k = 3, a new `--out`, and
   `--reference-run <local smoke dir>` to check the 24-hour window. If the model changes,
   run its smoke sample first and use that directory as the reference.

If the probe's Ollama check fails, set `OLLAMA_HOST=0.0.0.0:11434` for this arm only,
while the Mac is offline, and revert it afterwards (#200 ruling 7).

## 11. Cleanup

```bash
docker ps -a --filter label=org.python-docs-mcp.benchmark-run=<run_id>
docker network ls --filter label=org.python-docs-mcp.benchmark-run=<run_id>
```

The first managed SIGTERM or SIGHUP stops the run. Docker management commands have
their own process group and do not read terminal input, so a later foreground signal
does not interrupt rollback. Worker cleanup failures are reported on stderr with the
job ID.

After cleanup both lists should be empty. A run ID can be reused. If a resource remains,
match its full container or network ID, or the exact invocation-specific
`org.python-docs-mcp.benchmark-resource` label from the cleanup diagnostic, to the
stopped invocation before removing it by ID.
