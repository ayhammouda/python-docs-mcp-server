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

Grading workers check decoded content and serialized JSON for their own literal key
and provider key shapes. Settings are checked before requesting a batch reservation;
items are checked on receipt, before any upload or submission. The host reserves a
batch unit before delivering its items, so a detected item leak still leaves a held
unit for conservative teardown settlement. Results are checked before chunking can
drop raw evidence, and errors before retry decisions. Detection terminates the worker.
Count workers, including the premise command, check the whole normalized item before
its first endpoint call, then each envelope and endpoint result or error. Counting
creates no ledger reservation. SDK authentication headers and private bootstrap
frames stay outside these content checks.

MCP stdout replies are checked for literal worker credentials and key-shaped strings
before JSON-RPC parsing, result conversion or tool output-schema validation can drop
fields. This includes malformed replies, error details, tool listings and handshake
data. A detected credential ends the worker with exit 3 and stops new grants across
the run; already granted calls still settle and clean diagnostics are retained.
Task-group and chained exceptions cannot turn that detection into a scored MCP crash.
Clean replies keep the SDK's normal validation outcomes, including malformed
structure or trailing text whose strings can be inspected. Quoted fields are
checked independently of JSON structure, including duplicate fields and unfinished
strings. Broken escapes with an uninspectable suffix fail closed using the same
credential-failure path; no such content is replayed or published. Tool arguments
use this same bounded scan after returned provider usage has settled; integer and
nesting-limit errors are retained as invalid raw arguments, without losing usage.
Each stdout line is limited to 64 MiB, with no queued reply buffer, and subprocess
shutdown remains bounded.

A reply with invalid text encoding stops the run after the worker checks its readable prefix.

## 3. Dry run

```bash
benchmarks/sandbox/run.sh --models claude-sonnet-5-5,gpt-6.1-sol,gemini-3.8-flash \
  --corpus docs/benchmarks/corpus.yml --out <dir>
```

It builds the image, plans every cell and writes the plan, and opens no ledger and no
container.

For dry and live runs, `agentic-run` plans from the same corpus, manifest, matrix and
price snapshot bytes that it copies to `snapshots/`. A key-shaped CLI argument or YAML
input stops the command with exit code 3 before it writes snapshots or opens the ledger.
The check includes YAML comments and decoded scalars, even values a merge or duplicate
key overrides.

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

Before a hosted full run, grade the pilot's answers and run `agentic-score` (section 12)
to write `grading/summary.json`. Keep the complete pilot directory: frozen grading
inputs, answer items, votes, invocation records and ledger events are required alongside
the answering artifacts. A supplied hosted `--reference-run` requires this evidence even
when the new run has at most 90 trajectories per provider.
Invocation filenames must retain their recorded grading run IDs; ledger units and batch
IDs must be nonblank. Older honest archives without execution receipts remain usable
for financial estimates when their retained replies, identities and settlements reconcile.

For each provider, the gate adds projected answering cost to projected grading cost for
**every planned answer**, then multiplies the sum by 1.25 and compares it with the
remaining durable ledger cap. Judge providers are included even when none of their
models answer. Confirmation and budget refusals show the answering and grading terms;
confirmation also shows spent, cap, remaining and the combined estimate. The gate still
checks each answer model's projected time against its own 24-hour window, including all
pilot attempts and the 1.25 factor. When it refuses, choose fewer repeats or fewer
questions before you run, and disclose that choice with the results.

Each scored or discarded transcript records `execution.provider`, `execution.model_id`
and `execution.api` from the client instantiated by the worker. A reference must prove
all three agree with its saved model matrix and the requested family. Scripted responses
cannot supply live rates. Older pilots without executed identity are rejected; rerun the
pilot to obtain auditable evidence rather than adding an API label to old transcripts.
Local Ollama timing references need the same identity evidence, even though they are
unmetered. Grant waits remain excluded, and discarded attempts count toward cost and time.

The host retains a `transcript_inventory` in `run-summary.json`: the SHA256 of each exact
published scored or discarded JSON file, its relative path and its immutable cell,
question, system, family, provider, model, repeat and trajectory-attempt coordinates.
The reference reader checks these against the same captured bytes it parses before
using any timing or charges. Replacing a single file from another pilot, including one
with the same run label, fails closed. Missing inventories, missing files, extra files
and changed bytes are refused with an inventory or SHA256 diagnostic. Produce a new
pilot with the current runner for older evidence; adding hashes after a restore cannot
establish which bytes the host originally published. Copying a complete intact pilot
to another directory and regenerating scoring, token or report artifacts remain supported.
This is a consistency check for partial restores or edits, without signing infrastructure;
deliberately forging the entire unsigned evidence bundle is outside its scope.

Agentic scoring also checks legacy scoring records and supplied LLM grades for
provider-key shapes before changing cells, queues, rollups or the grading summary.
A refusal identifies the pattern without printing the matched text and preserves
the existing files. Restore clean evidence in the working copy before rerunning;
the scorer does not redact or repair the original evidence.

Every actual model-call attempt must have a unique `unit_id`. For metered providers,
that ID must join to exactly one reservation and settlement in the summary's `run_id`,
with the same provider, family/system job and model. Execution identity establishes the
API; a recorded request model must agree too. Outcomes, usage, booked charges and summary
balances must reconcile, including retries and discarded trajectories. Mixed pilot
channels, missing attempt IDs and contradictory accounting are refused before estimation.
This checks consistency using existing producer IDs; it does not require signed records.
Local calls have no spend events, and opening recovery settlements for older pending units
remain part of `spent_before`, outside the current pilot's rate.

Reference summaries, matrices, scored and discarded transcripts, and ledger JSONL are
scanned as raw bytes and decoded values before validation can quote them. YAML values
hidden by aliases or merge overrides and overwritten JSON values are included. Both dry
and live runs validate an explicit `--reference-run`; a key-shaped value stops the CLI
with exit 3 before confirmation or any provider call, with no key in the diagnostic.
Grading rates use reconciled observed provider dollars divided by the pilot's actual
fully graded answer count, without rounding the projection to the displayed average.
Missing, pending, scripted, mixed live/scripted, or inconsistent evidence is refused
before confirmation, key access or worker preparation. Votes must match the pilot's
rendered answers and their paid batch settlements. A copied summary alone cannot
authorize a full run. Incomplete grading invocations, unmatched recovery settlements and
grading with older rendering inputs also require a usable pilot measurement. These
local files establish consistency; they are not signed provider billing receipts.

Smoke and pilot runs without a reference retain their existing interactive authorization
and per-unit ledger caps. The separate air-gapped arm keeps its offline checks and
answer-model time gate; it does not require hosted grading evidence while offline.
The preflight estimates future grading spend; it does not execute or authorize grading.

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

Scoring and report export derive offline status from the current planned jobs,
trajectory and discard artifacts, isolation result, egress summary and host checks.
They require one check round per reported attempt plus the initial and final rounds;
the cached offline verdict in `grading/summary.json` cannot establish a pass.
Missing, malformed or incomplete evidence is unknown. Denied proxy attempts remain
separate from internet hosts reached, including when the overall verdict is unknown.

Worker stderr stays in an unlinked file until teardown. The host publishes a scrubbed
snapshot in the run directory after stopping the worker. A snapshot over 8 MiB fails
the run and leaves that worker without a public stderr log. The ledger still accounts
for its open reservation.

Squid writes to a job-specific temporary spool outside the run directory. Its parent is
mode 0700. Docker Desktop mounts only the writable child directory (mode 0777) and the
read-only config file. The probe reads the private access log and scans its record before
publishing the exact prefix used to separate probe traffic from query traffic.

At close, the runner reads only regular `access.log` and `cache.log` files without
following symlinks. Each file has an 8 MiB limit on its logical size, including sparse
regions. If a log exceeds the limit or changes size during the read, the runner refuses
to publish it. Each public log appears by atomic rename after its key scan. A key
match fails the run even if writing the scrubbed log fails. The runner removes the private
spool on teardown. A `probe/<job>.collection-pending` marker means collection is
incomplete or failed and C1 cannot pass, even if some public files are present.

If a Docker state field or other collection diagnostic contains a key-shaped string,
`collected.json` records a failed collection and omits the state details. The runner
continues closing the remaining jobs and the outside network. Check the reported
resource IDs if a removal failed, even when the runner could not publish the record.

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

When replaying a graded run, `agentic-score` renders the answer again with the frozen
question, criteria, system prompt and schema. It masks links and escapes answer delimiters
as the judge saw them. Each selected answer or reference vote must match that request's
`input_sha256` and its item, target, judge, model, effort and pass. An unsubmitted
pass-1 vote can leave a grade pending; a missing execution-issued pass-1 vote stops
scoring before writes. A missing issued answer pass-2 vote withholds test–retest
detail while preserving supported primary grades, gates, costs and comparisons.
A present vote with missing or contradictory input evidence
stops scoring before it updates the run. To replay an incomplete export, restore the
original item and vote files from the grading run.

The calibration run exports each judge’s held-out scores so the scorer can recompute
G1 when it imports the handoff. [G1 evidence format](grading/G1-EVIDENCE.md).

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

## 12. Grading workspaces

`uv run --locked python -m benchmarks agentic-grade --help` lists the grading flags.
Grade an existing answering run with a panel whose efforts have been frozen by tuning:

```bash
uv run --locked python -m benchmarks agentic-grade --target answers --run-dir <answering dir> \
  --panel <frozen panel.yml> --probes <probes.yml>
```

For live judges, this command writes a dry-run plan under `grading/plans/`, with item
counts, batches, standard-rate reservation bounds and batch-rate estimates. It opens
no ledger or container. Unfrozen panel efforts are refused, including during planning.
Scripted judges execute locally; synthetic checks must pass a disposable `--ledger-dir`.

Answering and grading share the durable spend ledger. Its directory must be a real
directory; `.lock` and every provider `.jsonl` must be regular files with exactly
one hard link. Symlinks and special files are refused, including links inserted
after opening. Existing parent aliases (such as macOS `/var`) remain supported.
Each journal event's provider must match its filename, including legacy histories
and recovery of pending reservations. Refusal leaves pending spend for a later
safe reopen; do not discard history to clear an error or reset the caps.

The ledger pins the opened directory through locking, recovery, writes and sync.
If that directory is renamed during a run, the run continues using the original
directory and lock. Keep the directory under operator control: the advisory lock
coordinates cooperating runs; it cannot stop another process with write access
from altering history or replacing entries during a file operation.

After the pilot's panel and any needed reference grades finish, refresh its scoring
summary using the same frozen criteria and panel:

```bash
uv run --locked python -m benchmarks agentic-score --run-dir <pilot dir> \
  --criteria <criteria.yml> --panel <frozen panel.yml> --g1 <held-out dir>/g1.json
```

This file-only step writes `grading/summary.json`, including observed provider dollars,
the graded-answer count and per-provider averages used by the full-run preflight.

For an operator's paid run after merge, add `--launcher container --image <id> --live
--confirm-max-spend <USD>` and the same `--keys-file` and `--index` options as answering.
The confirmed amount must cover the plan's total reservation bound. Retries also need
room in that invocation's allocation and the shared durable provider caps. Live jobs
refuse under CI and require the interactive authorization before reading keys or
starting containers. A container invocation without `--live` always plans only.

| Target | Destination | Items |
|---|---|---|
| `probe-gen` | `--workspace`, kind `probe-build` | Agent-written slots not already checked successfully |
| `probe-check` | The same probe-build workspace | Generated probes for `--round` (1–3) |
| `tuning` | A new workspace, kind `tuning` | Tuning probes, one `--judges` id and `--effort` |
| `probes` | A new workspace, kind `held-out` | Held-out probes for the frozen panel |
| `reference-probes` | The same held-out workspace | The reference subset of held-out probes |
| `answers` | `--run-dir` | Successful, nonempty answers |
| `reference` | The same answering run | Settlements, suspect answers and the stored G3 sample |
| `key-audit` | The same answering run, with `--index` | Answer keys and their cited docs sections |

Create a workspace on first use with `--workspace-kind`. Tuning requires `--probes`.
After `agentic-score --workspace <tuning dir> --step tuning`, create the held-out
workspace with `--probes` and `--handoff-tuning <tuning dir>/tuning.json` (repeat for
additional evidence). Frozen efforts must equal the lowest passing tuning efforts.
New held-out workspaces require source-bearing tuning exports. Creation and every
grading preflight replay their selected raw attempts with the canonical G1 verifier,
binding the initial panel settings (apart from the effort being frozen) to the
selected final models, token bounds and fallback composition. Editing summary
accuracy, attempts or chosen effort cannot authorize a held-out run. The retained
`inputs/tuning-questions.yml` contains the real tuning rubric and is checked against
the workspace's tuning digest; keep the matching tuning input when resuming.
Creation, explicit selection and resumed preflight use the calibration transport
reader. It allows 1 GiB per transport, 64 MiB of metadata excluding packed blobs,
and 2 GiB of expanded source. Individual frozen inputs and source
members keep their 64 MiB bounds. Typed inspection gives each original source
string its own decoding budget while retaining exact copied bytes and digests.
Explicit external file aliases are resolved once before bounded capture. Copied
workspace handoffs remain confined regular files.
Use a new tuning workspace for a prompt revision and a new held-out workspace for
the pre-registered fallback composition (`--judges haiku,luna,flash`). Probe assembly,
tuning summaries and G1 computation remain the file-only `agentic-score` steps.

Existing archives remain replayable: source exports with sufficient raw replies
can predate execution receipts. Summary-only historical G1 exports disclose tuning
attempts as unknown; they cannot bootstrap a new held-out workspace. Do not add
invented source attempts to upgrade an old summary. These unsigned archives prove
internal consistency, not provider execution, acceptance or charges. Each real run
still requires Aymen's own spending confirmation; for v0.5.0 the owner session
supplies it within the caps, under the
[v0.5.0 owner handback to Codex](../../AGENT-EXECUTION-PIPELINE.md#amendment-2026-11-07--v050-owner-handback-to-codex).

Full calibration covers 50 held-out questions across 14 probe types. Each
of the 700 slots needs a probe or a documented drop under the registered
drop rules. The reference check requires 25 scores per type, 350 in total.
A smaller scripted workspace still reports accuracy and coverage. Its G1
and reference checks remain unevaluable.

Inputs and handoffs are copied and hashed once. An invocation with different frozen
inputs is refused. Finished votes, including terminal failures, are skipped on rerun;
unfinished items remain eligible. `reference` waits for a final pass-1 record from
every panel judge for every answer. `--pass 2` runs test–retest for answers or held-out
probes while preserving pass 1. Every invocation needs a fresh `--grading-run-id`
(generated by default), including interrupted or dry invocations.

Grading writes under `<answering dir>/grading/` or the workspace. Votes and item
inputs remain immutable; `runs/<grading_run_id>.json` discloses spend, withheld records
and items not run. Container evidence stays in `sandbox/<grading_run_id>/`, preserving
each invocation and the answering run's original egress, probe and sandbox evidence.
Exit codes are 0 on success, 2 for validation errors, and 3 for a detected key leak.

## 13. Counting, scoring and reporting

`agentic-count-tokens --run-dir <answering dir>` plans free count calls by default.
For real counts after merge, add `--launcher container --image <id> --keys-file <path>
--live`. It uses the same CI refusal and interactive authorization as grading,
but opens no spend ledger. `--all` recounts every trajectory; the default resumes
only missing counts or counts from another method. An accepted worker record
identifies the counter's provider, API and model. A scripted worker records a
`bytes/4` approximation. A hosted worker records the Anthropic count endpoint
and model. Legacy records without an execution identity are preserved and
recounted. Immutable count history and per-invocation evidence stay under
`grading/`; successful records fill `tokens/`.

After grading and counting, use the file-only commands:

```bash
uv run --locked python -m benchmarks agentic-score --run-dir <answering dir> \
  --panel <frozen panel.yml> --criteria <criteria.yml> --g1 <held-out dir>/g1.json
uv run --locked python -m benchmarks report --run-dir <answering dir>
```

Scoring verifies the frozen inputs, uses 10,000 bootstrap draws for gates and
claims, and writes `grading/summary.json` and per-cell scoring provenance. Reporting
writes `REPORT.md` and `README-SUMMARY.md`; a tier below `Validated` produces no
public README claim. Missing egress/offline evidence remains not evaluable.
The offline regression in `tests/benchmarks/test_agentic_grading_e2e.py` exercises
these entry points with scripted providers and a disposable ledger. It supplies
`count_family(script=...)` at the test seam; there is no count CLI script flag.

The financial acceptance regression can run without network access, credentials,
containers or sending signals:

```bash
uv run --locked --offline pytest -q tests/benchmarks/test_grading_sdk_preflight.py \
  tests/benchmarks/test_agentic_full_budget.py
```

The SDK regression runs one answered cell through the ordinary worker, the three
production batch adapters, `agentic-grade` and `agentic-score`. All SDK traffic uses
mock transports; launcher/framing and initial authorization are test seams. It
freezes a test panel's efforts before execution and uses the committed price snapshot.
The resulting synthetic grading dollars are $0.000135 (Anthropic), $0.0000535
(OpenAI) and $0.00021375 (Google). The reader reconciles those saved votes,
invocations and settlements without rewriting them. The full-run gate then scales
the observed rates to 91 planned answers, includes the answering cost and existing
durable balances, and receives a declined confirmation. The test checks that the
pilot evidence and durable journals remain byte-for-byte intact. The financial boundary
and corruption cases share this mock-transport path, with synthetic flat prices and
usage that book $0.10 for answering and $0.000060 per grading provider. Every corruption
starts from a verified portable pilot. A separate scripted pilot tests scripted rejection;
successful replies for a suspect answer test refusal while its reference grade is missing.

This proves local accounting and artifact compatibility, including unrounded rates
behind the rounded confirmation display. It is mock evidence, not live spending or
a provider receipt. It does not verify real authorization, provider acceptance,
remote batch timing, container isolation, tuned judge quality or a Validated tier.
