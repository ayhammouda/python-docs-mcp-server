# [v0.3.0] security — audit and document PyYAML safe-loader discipline

> **Confidence:** MEDIUM · **Wave:** lead · **Slug:** `pyyaml-safe-loader-audit`
> Create with: `gh issue create -F .planning/issues/v0.3.0/03-pyyaml-safe-loader-audit.md -l compliance,priority:P2`
> Branch: `agent/<issue-number>-pyyaml-safe-loader-audit`

## Context

- **Per-issue context file (read first):** [`.planning/agent-context/pyyaml-safe-loader-audit.md`](../../agent-context/pyyaml-safe-loader-audit.md)
- Pipeline: [`AGENT-EXECUTION-PIPELINE.md`](../../../AGENT-EXECUTION-PIPELINE.md)
- Roadmap: [`STRATEGIC-ROADMAP-2026-05-29.md`](../../../STRATEGIC-ROADMAP-2026-05-29.md) §4 (v0.3.0), decision **5.11**
- Known YAML call sites: `src/mcp_server_python_docs/server.py:57`, `src/mcp_server_python_docs/ingestion/sphinx_json.py:597` (both already `yaml.safe_load`); input file `src/mcp_server_python_docs/data/synonyms.yaml`

## Goal

Prove and lock in that `synonyms.yaml` is the only YAML input and is loaded only
via `yaml.safe_load`, with the trust boundary documented and regression-guarded.

## Acceptance criteria

- [ ] `grep -rn 'yaml.load(' src/` returns **zero** hits.
- [ ] `grep -rn 'yaml.safe_load(' src/` returns at least the two expected call sites (`server.py`, `ingestion/sphinx_json.py`).
- [ ] `grep -rln '\.ya\?ml' src/mcp_server_python_docs/` shows `data/synonyms.yaml` is the only YAML **data input** loaded at runtime/ingestion (any others are config, not parsed input — enumerate them in the PR).
- [ ] A new test `tests/test_synonyms.py::test_yaml_loaded_only_via_safe_load` (or a clearly named addition) asserts the discipline programmatically — e.g. scans `src/` for `yaml.load(` and fails if any unsafe loader appears, and confirms the synonyms loaders use `safe_load`.
- [ ] A short "YAML trust boundary" note is added to the in-repo docs the agent IS allowed to edit (a new `docs/architecture/YAML-TRUST-BOUNDARY.md`, or the per-issue context decision log) stating: synonyms.yaml is packaged with the wheel, is the sole YAML input, and is parsed only with `safe_load`. **Do not edit `SECURITY.md`** (forbidden).

## Scope boundaries

**In scope:** read-only audit (grep), a regression test asserting the discipline, and a new architecture note documenting the trust boundary. If a genuinely unsafe `yaml.load(` is found, the fix (switch to `safe_load`) is in scope — but surface the finding in a comment first.

**Out of scope:** changing `synonyms.yaml` contents or schema; touching ingestion behavior; editing `SECURITY.md`.

## Forbidden-territory reminders (pipeline §2)

- `SECURITY.md` — trust-posture prose requires deliberate Vision review. Capture findings in a new `docs/architecture/` note instead and recommend the `SECURITY.md` wording for Vision to apply.
- Existing tests — extend, never weaken.

## Validation commands (pipeline §5)

Run the canonical four-command gate from `AGENT-EXECUTION-PIPELINE.md` §5. No
change-type-specific additional gates apply.

## PR template & recovery

- Use `.github/PULL_REQUEST_TEMPLATE/agent.md`. If the audit finds the codebase already clean, say so explicitly and ship the regression test + doc note (the value is the lock-in, not a fix).
- Found something genuinely unsafe? That is a security finding — comment on the issue before changing code.

## Effort estimate

~1–1.5 hours.
