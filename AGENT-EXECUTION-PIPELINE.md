# Autonomous Agent Execution Pipeline

**Purpose:** Define the policy, context, and guardrails for running autonomous coding agents (Claude Code or similar) against this project's GitHub issues while the maintainer is AFK.

**Companion to:** [`STRATEGIC-ROADMAP-2026-05-29.md`](STRATEGIC-ROADMAP-2026-05-29.md) (the *what*; this is the *how, with what guardrails*).

**OpenClaw operating layer:** [`OPENCLAW-FORGE-PROTOCOL.md`](OPENCLAW-FORGE-PROTOCOL.md) defines how Vision, Gilfoyle, and Heimdall apply this policy. This project has no UI, so Saga is not part of the default loop.

**Adopted:** 2026-05-29

---

## 1. Operating Principles

- Agents work in branches, never on `main`.
- Every PR requires human review before merge. **No auto-merge, ever.**
- Agents declare their scope explicitly and stay inside it.
- The canonical validation gate (§5) must pass before any PR is opened. Failing gate → no PR, just a `WORKING-NOTES.md` on the branch + comment on the issue.
- Automated review tools such as CodeRabbit provide review signal only. They do not approve, merge, or override the human-review gate.
- Forbidden territory (§2) is non-negotiable. Any drift triggers a hard stop.
- Recovery is always **stop and post a comment**, never **silently expand scope**.

The goal is to maximize what an agent can do unattended overnight, then catch anything that needed human judgment in a tight morning review.

---

## 2. Forbidden Territory (hard stop)

Autonomous agents may NOT modify the following without explicit human approval in the issue comments first:

| Path / Concern | Reason |
|---|---|
| Any tool name, parameter, or return shape | Public API surface; semver-significant |
| `schema.sql` and migrations | Index schema; rebuilds existing user caches |
| `.github/workflows/` (any workflow) | CI/CD and supply chain |
| `.github/workflows/release.yml` specifically | Release path; Trusted Publishing config |
| `pyproject.toml` `[project]` (anything other than `version`) | Identity, dependencies, classifiers |
| Major dependency bumps (anything ≥1 major) | Compatibility risk |
| `.planning/POSITIONING.md` | Load-bearing brand asset |
| `README.md` hero section (above the first install code block) | Load-bearing brand asset |
| `LICENSE` | Permanent commitment (MIT, always free) |
| `CHANGELOG.md` (creating entries is fine; rewriting history is not) | Release history |
| `SECURITY.md` | Trust posture; requires deliberate review |
| Existing tests (deletion or weakening assertions) | Regression cover |
| `.planning/ROADMAP.md` historical phase records | Archival history |
| `AGENT-EXECUTION-PIPELINE.md`, `OPENCLAW-FORGE-PROTOCOL.md`, and `STRATEGIC-ROADMAP-2026-05-29.md` | Governing policy and strategy docs |

If an agent's task appears to require touching any of these:
1. **Stop work.**
2. Post a comment on the issue explaining the conflict.
3. Tag with `🛑 needs-human-review`.
4. Wait for guidance.

---

## 3. Issue Structure (required for every agent-targetable issue)

Every issue intended for an autonomous agent **must** contain these sections, in this order:

| Section | Purpose | Required content |
|---|---|---|
| **Title** | Routability | `[v0.X.Y] <scope> — <verb> <thing>` e.g., `[v0.3.0] cache — add zstd codec layer` |
| **Context** | Self-containment | Links to: this pipeline doc, the strategic roadmap, any specific ADR or `.planning/phases/0X-*` directory, prior related issues |
| **Goal** | Single sentence | What outcome counts as success |
| **Acceptance criteria** | Testable definition of done | Checkbox list per §4 |
| **Scope boundaries** | Prevents creep | "In scope:" and "Out of scope:" subsections |
| **Forbidden-territory reminder** | Belt and suspenders | Repeat the §2 items relevant to this issue |
| **Validation commands** | Pre-PR gate | The exact canonical commands per §5 |
| **PR template** | What the PR description must include | §6 checklist |
| **Recovery** | What to do if blocked | Pointer to §8 |
| **Effort estimate** | Sanity check | Rough hours; agent should bail and escalate if work exceeds 2× |

An issue missing any of these is not agent-ready. The pre-flight checklist (§10) gates this.

---

## 4. Acceptance-Criteria Patterns

**Each criterion must be:** testable, atomic, achievable without touching forbidden territory, and sized so a competent dev could verify it in <5 minutes.

**Good examples:**

- "`uv run pytest tests/cache/test_codec.py -q` passes with at least 4 new tests covering: codec round-trip for `'none'`, codec round-trip for `'zstd'`, codec round-trip for `'zstd-dict-v1'`, and graceful read of pre-existing `compression='none'` rows."
- "After cherry-picking this branch, `python -c 'from mcp_server_python_docs.cache.codec import list_supported; print(list_supported())'` prints exactly `['none', 'zstd', 'zstd-dict-v1']`."
- "`grep -rn 'yaml.load(' src/ tests/` returns zero hits. `grep -rn 'yaml.safe_load(' src/` returns at least the expected call sites in `server.py` and `ingestion/sphinx_json.py`."
- "README.md `## Tools` section lists exactly six rows in the table, including `compare_versions`, and the row order matches the `@mcp.tool` declaration order in `src/mcp_server_python_docs/server.py`."

**Bad examples (do not allow):**

- "Improve cache performance." — not testable
- "Make it production-ready." — not specific
- "Refactor for clarity." — invites scope creep
- "Add tests." — what tests, asserting what?

---

## 5. Canonical Validation Gate

**Must pass, in this order, before any PR is opened:**

```bash
uv run ruff check src/ tests/
uv run pyright src/
uv run pytest --tb=short -q
uv run python-docs-mcp-server doctor
```

**Additional gates for specific change types:**

- Any change touching the MCP wire protocol or tool registration:
  ```bash
  uv run pytest tests/test_stdio_smoke.py -q
  ```
- Any change to ingestion or storage:
  ```bash
  uv run python-docs-mcp-server validate-corpus
  ```
- Any change touching dependencies:
  ```bash
  uv lock --check
  uv pip compile --quiet pyproject.toml -o /tmp/requirements-check.txt
  ```

**Failure handling:** If any gate fails, the agent writes the full output into a file `WORKING-NOTES.md` at the branch root, commits it as `agent: validation-gate-failed`, posts a comment on the issue with a link to the failing commit, and stops. **No PR is opened.**

---

## 6. Branch, Commit, PR Conventions

- **Branch name:** `agent/{issue-number}-{kebab-case-summary}` (e.g., `agent/47-zstd-cache-codec`)
- **Commit prefix:** `agent: ` followed by a short, imperative summary. Conventional-commit scopes optional but encouraged: `agent: cache(codec): add zstd round-trip path`
- **Atomic commits.** One logical change per commit. No squash-and-force-push during agent work.
- **PR title** matches the issue title verbatim
- **PR description** must include:
  - `Closes #<issue-number>` (or `Refs #` if intentionally not closing)
  - Each acceptance criterion as a checked or unchecked box, with a one-line explanation if unchecked
  - Output (or link to artifact) for the §5 validation gate
  - CodeRabbit triage summary when CodeRabbit comments on the PR: blocking, follow-up, false positive, or pending/unavailable
  - A short "Why this approach" paragraph if the design wasn't fully prescribed in the issue
  - The §7 "Why this triggered human review" disclosure (which doubles as a forbidden-territory near-miss log when applicable; CODEOWNERS is the mechanical enforcement)
- **PR is opened against** the milestone integration branch (e.g., `release/v0.3.0`) when one exists, otherwise `main`. Never auto-merge.

---

## 7. Human-Review Triggers (always pause)

The agent must open the PR but **NOT** request merge — and must add the `🛑 needs-human-review` label — if any of these are true:

| Trigger | Why |
|---|---|
| Any forbidden-territory item (§2) was modified | By definition |
| Any existing test was deleted | Possible regression-cover loss |
| Diff exceeds 500 lines of source code (excluding generated and lockfiles) | Bigger than a single agent task should be |
| A new third-party runtime dependency was introduced | Trust-posture and footprint review |
| Any `pyproject.toml` field changed (other than `version` during a release issue) | Identity / metadata review |
| `.github/workflows/` was modified | CI/release-path review |
| The PR introduces network access at runtime | Violates principle 2.2 (offline-first) |
| The PR introduces async code in a previously-sync code path | Concurrency review |
| The agent's "Why this approach" paragraph cites a design choice not in the issue | Verify scope |

For each trigger, the PR description must include a `## Why this triggered human review` section explaining what changed and why the agent believes it was necessary.

---

## 8. Recovery Procedures

If the agent encounters any of these conditions, **stop work** and post a comment on the issue:

- A previously passing test now fails for an unclear reason
- A change to forbidden territory appears necessary to complete the task
- The acceptance criteria turn out to be ambiguous or contradictory
- An upstream dependency is broken or unavailable
- Work appears to exceed 2× the original effort estimate

The stop comment must contain:
1. What was attempted (1–3 sentences)
2. What failed or blocked (with error output if applicable)
3. The agent's best read on the path forward
4. An explicit "I am stopping pending guidance" line

**Forbidden recovery moves:**

- Silently expanding scope
- Trying alternative implementations not specified in the issue
- Merging to `main`
- Deleting tests to make others pass
- Suppressing warnings or skipping tests as a "workaround"

---

## 9. Context Files Required Before Agents Run

These files must exist on `main` before the v0.3.0 issues are unleashed to autonomous agents.

| File | Purpose | Status |
|---|---|---|
| [`AGENTS.md`](AGENTS.md) | Existing repo-conventions doc; should reference this pipeline | **Needs update** — add a one-paragraph link to this file |
| [`STRATEGIC-ROADMAP-2026-05-29.md`](STRATEGIC-ROADMAP-2026-05-29.md) | The *what and why*; mandatory reading | **Exists** |
| `AGENT-EXECUTION-PIPELINE.md` (this file) | The *how, with what guardrails* | **Exists** |
| [`OPENCLAW-FORGE-PROTOCOL.md`](OPENCLAW-FORGE-PROTOCOL.md) | OpenClaw role split and MCP-specific execution loop | **Exists** |
| `.github/ISSUE_TEMPLATE/autonomous-agent.yml` | Issue template enforcing §3 structure | **Create** — see §11 sketch |
| `.github/PULL_REQUEST_TEMPLATE/agent.md` | PR template enforcing §6 | **Create** — see §11 sketch |
| `.github/CODEOWNERS` | Forces human review on forbidden-territory paths | **Create** — see §11 sketch |
| `docs/architecture/TOKEN-STUDY-METHODOLOGY.md` | Methodology spec for the v0.3.0 first issue | **Create as part of that issue spec** |
| GitHub label: `🛑 needs-human-review` | Marks PRs paused at §7 triggers | **Create** |
| GitHub label: `agent-ready` | Confirms issue passed §10 pre-flight | **Create** |
| Branch protection on `main` | Requires at least one human approval before merge | **Confirm enabled** |
| Branch protection on `release/v0.3.0` (when created) | Same | **Configure at branch creation** |

---

## 10. Pre-flight Checklist (run before unleashing agents on a milestone)

Run this checklist before pushing the first agent-ready issue to the queue.

- [ ] All §9 context files exist on `main`.
- [ ] The §5 canonical validation gate passes on `main` (clean baseline).
- [ ] Each issue has been read end-to-end by a human and labeled `agent-ready`.
- [ ] Each issue includes its §3 sections in full.
- [ ] The `🛑 needs-human-review` and `agent-ready` labels exist in the repo.
- [ ] CODEOWNERS forces review on at least: `pyproject.toml`, `.github/workflows/`, `LICENSE`, `README.md`, `.planning/POSITIONING.md`, `schema.sql`.
- [ ] Branch protection on `main` requires ≥1 human approval before merge.
- [ ] At least one issue is small enough (≤4 hours) to serve as a confidence-building first run.

---

## 11. Templates (authoritative implementations)

The §9 templates live as checked-in files; this section just points at them so
this doc never drifts from the implementations:

- `.github/ISSUE_TEMPLATE/autonomous-agent.yml`
- `.github/PULL_REQUEST_TEMPLATE/agent.md`
- `.github/CODEOWNERS`

---

## 12. Per-Issue Context Files (v0.3.0 wave)

For the first wave of agent-targetable issues, Claude Code should include — as linked references in each issue — a dedicated `.planning/agent-context/<issue-slug>.md` file that captures the agent's working notes, design decisions to honor, and any test fixtures or code excerpts the agent will need to look at.

Each per-issue context file should contain:

1. **The relevant excerpt from `STRATEGIC-ROADMAP-2026-05-29.md`** (don't make the agent re-derive the goal).
2. **Pointer to the existing code touch-points** (file paths + symbols).
3. **Existing test patterns to follow** (one or two example tests from the same area).
4. **Known pitfalls** specific to this task.
5. **Decision log placeholder** for the agent to fill in.

The point is to give the agent everything it needs in one read, so it doesn't go fishing across the repo and pick up incorrect patterns from `.planning/` archive material.

---

## 13. Suggested Agent-Targetable Issues for v0.3.0

Mapping the v0.3.0 deliverables to agent-friendliness, to help prioritize issue generation:

| Deliverable | Agent-friendly? | Why | Recommended owner |
|---|---|---|---|
| Workstream J — zstd cache codec layer | **Yes (high)** | Well-bounded, testable, no API surface change | Agent |
| README + PyPI + glama.json refresh to 6-tool surface | **Yes (high)** | Mechanical, easily verified, low risk | Agent |
| PyYAML safe-loader audit | **Yes (medium)** | Simple grep + fix; need agent to surface findings before changing | Agent |
| ADR-001 (Source Adapters) draft | **Yes (medium)** | Writing task; needs clear template + style guide | Agent with strict template |
| ADR-006 (Serialization) draft | **Yes (medium)** | Same as ADR-001 | Agent with strict template |
| Build-time supply-chain hardening (CPython SHA pin + SECURITY.md update) | **Partial** | Pinning is mechanical; SECURITY.md text needs judgment | Agent for pinning; human for SECURITY.md |
| 30-minute TOON Python port audit | **No** | Requires subjective quality judgment | Human |
| Empirical token study | **No** | Methodology choices and corpus selection require judgment | Human (with agent scaffolding the harness) |

The v0.3.0 issue wave should therefore lead with the **high-confidence agent issues** so the overnight run produces obvious wins, then escalate to the partial / human-judgment items the following day with the maintainer at the keyboard.

---

## Amendments

*Append amendments below as `## Amendment YYYY-MM-DD` sections. Do not edit historical content above this line; the locked sections are the authoritative current policy.*

## Amendment 2026-05-29 — OpenClaw Role Split

OpenClaw execution for this repo is governed by `OPENCLAW-FORGE-PROTOCOL.md`.
The default loop is Vision → Gilfoyle → Heimdall → Vision/Aymen:

- Vision owns issue pre-flight, `agent-ready`, review synthesis, branch protection, and pause/resume decisions.
- Gilfoyle owns scoped implementation on exactly one issue at a time.
- Heimdall owns independent verification, packaging/install smoke, security-sensitive checks, and release-readiness checks.
- CodeRabbit findings are mandatory review signal when present. Vision/Heimdall must triage them as blocking, follow-up, or false positive before `verified`.
- Saga is not in the default loop because this MCP has no UI.
- Pipeline Monitor remains disabled unless Aymen explicitly asks for assisted merge checks; no auto-merge is allowed.
