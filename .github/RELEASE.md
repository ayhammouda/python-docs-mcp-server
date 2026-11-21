# Release process

The project owner releases under the 2026-10-30 amendment and, for v0.5.0,
the 2026-11-04 delegation, the 2026-11-07 handover to Claude Code and the
later 2026-11-07 handback to Codex in
`AGENT-EXECUTION-PIPELINE.md`. Release eligibility fails closed. Do not bypass
or disable checks.

1. Merge the version/changelog PR after independent current-head verification
   and all required CI. `pyproject.toml`, `uv.lock`, `server.json` and its package
   entry must agree. CI covers ordinary product tests on Python 3.12–3.14 on
   Ubuntu and macOS, static analysis, security checks and installed-wheel stdio.
   Full-index installation E2E remains required. Benchmarks run locally and do
   not gate releases. See the [local benchmark workflow](../CONTRIBUTING.md#local-benchmark-workflow).
   If an update from `main` changed the head, carry verification forward only
   under the amendment's rule: a conflict-free update, green required checks,
   a new record for the new head, and either (a) a rebase whose
   `git range-diff` shows no patch changes or (b) a merge of `main` whose
   `git show --remerge-diff <merge-commit>` prints no diff. Otherwise
   re-verify the changed part with all three parts (Fable reviewer, Codex
   `gpt-6-astra` at `xhigh` in a read-only sandbox, `ponytail-review`), none
   run by the implementer. The partial record adds a
   `scope: delta <old-head>..<new-head>` line and a link to the full record it
   extends, and states the three delta verdicts and the new head's check result.
2. Wait for the required checks on the resulting **main commit** to pass.
   On that exact commit, run
   `uv build --build-constraint .github/build-constraints.txt --require-hashes --out-dir "$(mktemp -d)"`.
   It checks the hash-pinned build backend described below. Do not tag if it
   fails.
3. The owner tags that exact commit and pushes the tag:
   `git tag -a vX.Y.Z <exact main sha> -m "vX.Y.Z"`, then
   `git push origin vX.Y.Z`. Check the versions from step 1 first: release tags
   cannot be moved or deleted, so a failed release still spends the tag. The
   eligibility job confirms main ancestry, a single-parent commit from one
   merged PR, CodeQL on its head, the owner's verification record for that head
   and the required main checks. The record is a process tripwire, not a
   security boundary; the 2026-10-30 amendment defines it and its limits.
4. The release workflow runs eligibility → build/test/manifest validation →
   installed-wheel integration on Python 3.13 and 3.14 → PyPI → MCP Registry →
   GitHub release. Integration builds documentation 3.10–3.14 before publishing.
   The build job records the SHA-256 of every file in `dist/` and the artifact ID
   as job outputs. The PyPI and GitHub-release jobs download that artifact ID and
   refuse to attest, publish or attach anything whose checksum listing differs.
   PyPI receives the same wheel that passed integration, with provenance.
5. Confirm every job succeeded, the exact version is visible on PyPI and in the
   MCP Registry, the GitHub release has both artifacts, and a clean
   `uvx --refresh python-docs-mcp-server@X.Y.Z --version` reports that version.
   Confirm a representative real MCP client query before claiming client
   compatibility; automated stdio tests are a distinct proof.

The Linux publisher helper `.github/scripts/install_publisher.sh` pins the
release URL and verifies its SHA-256 before extracting the executable. The
manifest is validated **before** PyPI publication. Do not use an unverified
`curl | tar` download or a moving `latest` URL.

The release build installs its build backend from `.github/build-constraints.txt`.
That file pins `hatchling` and every dependency with SHA-256 hashes, and the build
runs `uv build --build-constraint .github/build-constraints.txt --require-hashes`.
Every project build in the build job uses that file. The job syncs dependencies with
`uv sync --locked --dev --no-install-project --no-build`, which builds neither the
project nor any source distribution, in a fresh `UV_PROJECT_ENVIRONMENT` under
`RUNNER_TEMP`. It installs the built wheel with
`uv pip install --python "$UV_PROJECT_ENVIRONMENT/bin/python" --no-deps dist/*.whl`
and runs later commands with `uv run --no-sync`. Product tests also inherit
`UV_NO_SYNC=1`, protecting the installation from nested `uv run` calls. Before
tests, the job requires imports from that environment's site-packages, compares
installed `schema.sql` and `synonyms.yaml` bytes with the wheel, and bootstraps an
in-memory database including FTS5. No source-tree resource fallback is used.
The test suite's own wheel build gets the same constraints through
`UV_BUILD_CONSTRAINT` and `UV_REQUIRE_HASHES`. Benchmark tests remain excluded.
To update it, run
`echo hatchling | uv pip compile --generate-hashes --universal --python-version 3.12 --upgrade - -o .github/build-constraints.txt`,
then
`uv build --build-constraint .github/build-constraints.txt --require-hashes --out-dir "$(mktemp -d)"`,
and review the diff like a lockfile change. A broken file fails the release in
the build job, before anything is published. The tag is spent by then, though,
so check the file before you tag.

PyPI Trusted Publishing remains bound to this repository, `release.yml` and the
`pypi` environment. The intended settings limit that environment to `v*` tags
and tag creation to repository admins; the separate immutable-tag rule stays
unchanged, with no bypass actors.
No routine human reviewer is required. If publication partially fails, record
which registries succeeded and diagnose the failure; never overwrite a published
version, move a tag, or claim a release complete from a partial result.

## One-time v0.5.0 recovery

The failed v0.5.0 release spent its tag. It must continue to peel to
`3a48f832404a11fb18de7d56c27c8e84ba57c2cc`. The original wheel contains
`storage/schema.sql`: a local reproduction of the benchmark sandbox's nested
`uv run --locked` replaced the installed wheel with an editable install while
the parent test process retained its old site-packages imports. The next database
bootstrap then failed to read the removed schema. Running the same nested command
with the inherited no-sync guard preserved the wheel and allowed the bootstrap to
succeed. The sandbox test itself filters its subprocess environment, so excluding
benchmark tests remains necessary. This repairs the test environment; it does
not change the v0.5.0 package source.

After independent review, merge of this repair, and green required checks on its
exact main commit, the owner may create **only** `v0.5.0-recovery.1` on that commit.
The existing `v*` creation restrictions, immutable-tag rules (no update/delete or
bypass actors), and `pypi` tag environment must cover this control tag as well as
`v0.5.0`. Keep those protections; do not move either tag. This implementation does
not create tags or change repository settings.

Only the exact protected `refs/tags/v0.5.0-recovery.1` push selects recovery. The
eligibility job checks that its tag still names the event commit and applies the
normal main ancestry, merged-PR, CodeQL, owner-record and main-check gates. It
also peels the original tag to the pinned SHA and rechecks that commit's evidence
under its original frozen gate, including the historical `Product regression`
success. A missing, changed or failing record/check aborts recovery. Other tag
pushes retain the ordinary release path; no arbitrary ref or manual artifact
input is accepted.

The repaired workflow builds and validates the manifest from the **original**
pinned source commit. Its verified target tag is `v0.5.0`, including the explicit
GitHub Release `tag_name` and package/manifest version checks. The existing
full-index E2E, artifact-ID/checksum checks, attestations, Trusted Publishing job,
`pypi` environment, MCP Registry publication and GitHub Release job remain in the
path. Provenance identifies the recovery workflow run; the gate identifies the
original source commit. Confirm all publication results using step 5 above.
