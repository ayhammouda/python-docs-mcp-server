# Release process

The Claude Code project owner releases under the 2026-10-30 amendment in
`AGENT-EXECUTION-PIPELINE.md`. Release eligibility fails closed. Do not bypass
or disable checks.

1. Merge the version/changelog PR after independent current-head verification
   and all required CI. `pyproject.toml`, `uv.lock`, `server.json` and its package
   entry must agree. CI covers Python 3.12–3.14 on Ubuntu and macOS, the real
   benchmark corpus, offline full-index regression, and installed-wheel stdio.
   If an update from `main` changed the head, carry verification forward only
   under the amendment's rule: a conflict-free update, green required checks,
   a new record for the new head, and either (a) a rebase whose
   `git range-diff` shows no patch changes or (b) a merge of `main` whose
   `git show --remerge-diff <merge-commit>` prints no diff. Otherwise re-verify the changed part with all three parts (Fable reviewer,
   Codex `gpt-6-astra` at `xhigh` in a read-only sandbox, `ponytail-review`),
   none run by the implementer. The partial record adds a
   `scope: delta <old-head>..<new-head>` line and a link to the full record it
   extends, and states the three delta verdicts and the new head's check result.
2. Wait for the required checks on the resulting **main commit** to pass.
3. The owner tags that exact commit and pushes the tag:
   `git tag -a vX.Y.Z <exact main sha> -m "vX.Y.Z"`, then
   `git push origin vX.Y.Z`. Check the versions from step 1 first: release tags
   cannot be moved or deleted, so a failed release still spends the tag. The
   eligibility job confirms main ancestry, a single-parent squash merge of one
   merged PR, CodeQL on its head, the owner's verification record for that head
   and the required main checks. The record is an unedited PR comment, posted
   no later than the merge, by the repository owner. Its first line is exactly
   `Independent verification: PASS` and it has a `head: <sha>` line. It is a
   process tripwire, not a security boundary: the owner account writes it and
   also merges, so anyone who can act as that account can forge it. A
   credential-bound check needs a verifier identity separate from the merger,
   such as a dedicated GitHub App with `checks: write` or a machine account.
   Restoring one is escalated to Aymen.
4. The release workflow runs eligibility → build/test/manifest validation →
   installed-wheel integration on Python 3.13 and 3.14 → PyPI → MCP Registry →
   GitHub release. Integration builds documentation 3.10–3.14 before publishing.
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

PyPI Trusted Publishing remains bound to this repository, `release.yml` and the
`pypi` environment. The intended settings limit that environment to `v*` tags
and tag creation to repository admins; the separate immutable-tag rule stays
unchanged, with no bypass actors.
No routine human reviewer is required. If publication partially fails, record
which registries succeeded and diagnose the failure; never overwrite a published
version, move a tag, or claim a release complete from a partial result.
