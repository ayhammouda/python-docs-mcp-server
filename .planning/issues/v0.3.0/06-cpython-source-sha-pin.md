# [v0.3.0] ingestion — pin CPython source by commit SHA

> **Confidence:** PARTIAL (agent does the pin; Vision handles the SECURITY.md threat model) · **Wave:** trailing · **Slug:** `cpython-source-sha-pin`
> Create with: `gh issue create -F .planning/issues/v0.3.0/06-cpython-source-sha-pin.md -l area:build,compliance,priority:P1`
> Branch: `agent/<issue-number>-cpython-source-sha-pin`

## Context

- **Per-issue context file (read first):** [`.planning/agent-context/cpython-source-sha-pin.md`](../../agent-context/cpython-source-sha-pin.md)
- Pipeline: [`AGENT-EXECUTION-PIPELINE.md`](../../../AGENT-EXECUTION-PIPELINE.md)
- Roadmap: [`STRATEGIC-ROADMAP-2026-05-29.md`](../../../STRATEGIC-ROADMAP-2026-05-29.md) §4 (v0.3.0, build-time supply-chain hardening), decision **5.10**
- Touch-points: `ingestion/cpython_versions.py` (`CPythonDocsBuildConfig`, `CPYTHON_DOCS_BUILD_CONFIG`), `__main__.py:210–226` (the `git clone --depth 1 --branch <tag>` call), `tests/test_ingestion.py:53`

## Goal

Make a pinned commit SHA — not a mutable tag — the integrity anchor for every CPython docs build, so a re-tagged or moved tag fails the build instead of silently changing canonical content.

## Acceptance criteria

- [ ] `CPythonDocsBuildConfig` gains a `sha: str` field; each of the five entries in `CPYTHON_DOCS_BUILD_CONFIG` carries the 40-char lowercase-hex commit SHA that the existing `tag` currently resolves to (resolve via `git ls-remote https://github.com/python/cpython.git <tag>`). The `tag` field is **kept** for human readability with a comment noting the SHA is authoritative.
- [ ] After the clone in `__main__.py`, the code verifies `git -C <clone_dir> rev-parse HEAD` equals `config["sha"]` and **aborts that version's build with a clear error** on mismatch (no silent fallback). The shallow `--branch <tag>` fetch may stay; the SHA check is what enforces integrity.
- [ ] `tests/test_ingestion.py` asserts every config entry has a `sha` matching `^[0-9a-f]{40}$`, alongside the existing tag assertion at line 53.
- [ ] `uv run pytest tests/test_ingestion.py -q` passes.
- [ ] A draft SECURITY.md threat-model paragraph (the `build-index` CPython clone as the largest non-runtime attack surface, now SHA-pinned) is written **into the PR description and the context file's decision log** for Vision to apply — `SECURITY.md` itself is **not** edited.

## Scope boundaries

**In scope:** `ingestion/cpython_versions.py`, the SHA-verification step in `__main__.py`, and `tests/test_ingestion.py`.

**Out of scope (stop and comment):**
- Editing `SECURITY.md` (forbidden — draft the wording only).
- Changing the clone transport, the Sphinx pin, or any build behavior beyond adding the SHA verification.
- Bumping any tag/version to a newer CPython release — pin the SHA of the **current** tag only.

## Forbidden-territory reminders (pipeline §2)

- `SECURITY.md` — do not edit; provide draft text for Vision review (this is the "Vision" half of this PARTIAL issue).
- `.github/workflows/` — do not touch the release/CI path.
- `pyproject.toml [project]` — untouched.

## Validation commands (pipeline §5)

Run the canonical four-command gate from `AGENT-EXECUTION-PIPELINE.md` §5, then
the change-specific gate below (ingestion-touching change):

```bash
uv run python-docs-mcp-server validate-corpus
```

> Note: a full `build-index` clones CPython over the network and takes minutes;
> the unit tests in `tests/test_ingestion.py` cover the config/verification logic
> without a live clone. Do not gate the PR on a full multi-version build.

## PR template & recovery (pipeline §6, §7)

- This is a **supervisor-review-required** PR: it touches the supply-chain integrity path and produces SECURITY.md wording for Vision. Open the PR, add `supervisor-review`, do not self-merge. Fill the "Why this triggered supervisor review" section.
- Blocked (e.g. can't resolve a SHA offline)? Stop and comment per §8.

## Effort estimate

~2 hours.
