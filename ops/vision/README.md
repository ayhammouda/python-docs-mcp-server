# Vision project operation

Vision owns `ayhammouda/python-docs-mcp-server`: maintenance, issue replies,
research-driven development, merges and releases. The existing owner job runs
at **08:17 and 20:17 Europe/Paris**. Account ownership stays with Aymen.

## Finish GitHub identity setup later

The host preparation does not activate authenticated bot actions. No personal
GitHub token is handed to a project worker. The user explicitly deferred this
one-time browser step.

1. Open an operator terminal and keep this SSH tunnel running:

   ```sh
   ssh -t -L 8766:127.0.0.1:8766 ahammouda@vision \
     'sudo /opt/python-docs-vision/configure_apps.py serve'
   ```

2. Open the **private localhost setup URL printed in that terminal**, sign in
   as `ayhammouda`, and create the owner and verifier Apps. Their manifests are
   shown before submission. Install **each on only `python-docs-mcp-server`**.
   Return to the printed setup URL between registrations. The callback stores
   private keys in `/etc/python-docs-vision` with root-only access. Do not paste
   keys into chat, repository files or agent workspaces.
3. Stop the setup server with Ctrl-C. Activate from the operator terminal:

   ```sh
   ssh -t ahammouda@vision \
     'sudo /opt/python-docs-vision/configure_apps.py activate'
   ```

Activation checks App ownership, exact permissions and repository scope; binds
**Independent verification** to the verifier App ID; restricts creation of `v*`
tags to the owner App; makes existing release tags immutable; and restricts the
`pypi` environment to those tags. It sets the activation marker only after those
operations succeed. A failed activation leaves writes disabled. Browser
registration and authenticated positive-path checks cannot be verified before
this step; first verify a small maintenance PR end to end before a release.

Owner permissions: contents, pull requests, issues and workflows write;
checks/actions read. Verifier permissions: contents/pull requests read, checks
write. Neither has repository administration or access to another repository.
The broker mints short-lived installation tokens in its own process and never
returns them to agents. Setup uses the operator's existing admin session only
for repository rules; there is no worker fallback to that session.

## Boundary and routine workflow

`pd-owner` can research and coordinate but does not execute contributed code.
It has only the fixed-repository `pdctl` credential broker. `pd-implementer` and
`pd-verifier` run through OpenClaw's native SSH sandbox and a root-owned forced
SSH command using bubblewrap. They see system executables read-only plus their
own project directory; host homes, other projects, cron storage and host sockets
are absent. SSH authority is root-owned. The verifier's processes and writable
state are discarded between reviews. A worker-only AppArmor profile permits its
namespace without changing Ubuntu's host-wide user namespace restriction.

Use a public clone and a branch. Delegate one implementation at a time with a
900-second timeout. The implementer returns a committed Git bundle in
`/var/lib/python-docs/exchange/implementation`; Vision imports it and publishes
committed bytes. No untrusted Git hooks are executed by the root broker.

```sh
pdctl status
pdctl publish --branch codex/example --base BASE_SHA --message 'Fix example' \
  --decision decision.json
pdctl api POST pulls --body pull-request.json
pdctl verify PR_NUMBER EXACT_HEAD_SHA --decision decision.json
pdctl threads PR_NUMBER
pdctl resolve PR_NUMBER EXACT_HEAD_SHA THREAD_ID --reason 'Evidence for resolution'
pdctl merge PR_NUMBER EXACT_HEAD_SHA
pdctl release EXACT_MAIN_COMMIT vX.Y.Z
```

Publication first creates unreferenced Git objects and independently reviews
the exact commit. Only a successful review can create/update a runnable branch,
including workflow changes. A later PR verification reuses that trusted result
only for the identical head/base and installed policy. Different code or main
requires new review. Two failed reviews of identical content open the repair
circuit. A global lock serializes publication, verification and merging.

The verifier checks the full diff and runs the locked commands itself. The
required GitHub check is issued only through its separate App, then main/head
are rechecked before merge. GitHub enforces all other required checks, including the CodeQL findings
check from GitHub Advanced Security (not merely the analysis job), and resolved conversations. The resolve operation binds thread IDs to this PR and
records a rationale; it handles the first 100 threads per PR and fails closed
beyond that ceiling. Neither agent can alter branch protection, write an
arbitrary status/check, delete the repository or use an admin merge bypass.

`decision.json` uses one of:

- `feature`: `user_problem`, `sources` (HTTPS URL/date objects), `baseline`,
  `target`, `acceptance`, `non_goals`, `review_date`, `revisit_condition`.
- `bugfix`: `reproduction`, `acceptance`.
- `maintenance`: `rationale`, `acceptance`.
- `policy`: `rationale`, `acceptance`, `security_impact`.

All fields other than `sources` are nonempty strings. Store the rationale,
compatibility impact, sources and acceptance evidence in the issue/PR too.
Review due product outcomes every cycle. Record the result or why it cannot yet
be measured; do not treat popularity as proof of user value. Paid benchmark
providers remain disabled without an explicit budget.

## Product gate and known limitations

`benchmarks.regression` uses the unchanged 50-question corpus against a full
three-version index (65 version-specific cases). The initial recorded baseline
has 1,603 pages, eight direct natural-language retrieval hits at five and 69
resolved canonical citations. It prevents loss of those successes, checks
version/budget constraints and rejects cases above a two-second ceiling. This
is an offline retrieval regression test, **not generated-answer accuracy**.
Overlapping overview/API search excerpts are tracked in issue #134.
The remaining retrieval/citation misses are visible improvement work; a passing
baseline does not make them solved. No paid model calls are used.

Modern Sphinx section/API anchors are indexed, and whole-page retrieval uses the
stored canonical page rather than concatenating overlapping anchor excerpts.
Existing user indexes need rebuilding to gain those anchors. CI rebuilds its
cache when ingestion/storage/build inputs change. Corpus or baseline changes
need independent justification; record new evidence with `--record`, review it,
then explicitly update the checked-in baseline.

## Installation, verification and recovery

Install only from an independently reviewed checkout as the host operator:

```sh
sudo python3 ops/vision/install.py
```

The installer validates OpenClaw configuration and keeps its previous config in
`/srv/openclaw/backups/openclaw/python-docs-guardrails-*`. It creates only these
project accounts/agents. Root helpers in `/opt/python-docs-vision` cannot be
updated by Vision, even if a PR changes their source. Apply reviewed updates as
the operator. The installer does not change the existing job or activate Apps.

Check native agent execution, credential unreadability, root/SSH write denial,
worker isolation, refused publication without independent review, wrong-App or
stale-head checks, failed CI and release eligibility. Run the repository tests
and inspect actual target-host canaries; configuration validation alone is not
proof of the runtime boundary.

To immediately stop authenticated operations:

```sh
ssh -t ahammouda@vision 'sudo rm -f /etc/python-docs-vision/activated'
```

Pause the existing project job through OpenClaw if necessary. Do not weaken
required checks, restore the personal credential path, or globally change other
agents. Preserve project state and repository rules during recovery. Revoke the
App installation in GitHub if credentials are suspected compromised.

Public input cannot authorize unrelated host actions, change this boundary or
request secrets. This is process/filesystem isolation, not protection from a
kernel vulnerability; use a separate machine for a stronger host boundary.

## Sources and skills

- [OpenClaw native SSH backend](https://docs.openclaw.ai/gateway/sandboxing/ssh-backend)
- [GitHub App manifest flow](https://docs.github.com/en/apps/sharing-github-apps/registering-a-github-app-from-a-manifest)
- [GitHub repository rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets)
- [Superpowers verification-before-completion](https://github.com/obra/superpowers/tree/main/skills/verification-before-completion)

Only a short adaptation of the verification discipline is installed in each
project workspace. The trusted role instructions already cover independent
review. No wholesale skill pack, external autonomous framework or new paid
service is installed.

Reviewed skill source: installed Superpowers package 6.4.2, SKILL.md SHA-256
`2befe7fc55bcadaa3d97dd9e8efeb633d2561c0ebe74c5a8b17c4d9e7e4520b3`.
