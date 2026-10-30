# Release process

Vision releases through the project broker after the scoped GitHub Apps are
activated (`ops/vision/README.md`). While setup is pending, release eligibility
fails closed. Do not substitute personal tokens or bypass checks.

1. Merge the version/changelog PR after independent current-head verification
   and all required CI. `pyproject.toml`, `uv.lock`, `server.json` and its package
   entry must agree. CI covers Python 3.12–3.14 on Ubuntu and macOS, the real
   benchmark corpus, offline full-index regression, and installed-wheel stdio.
2. Wait for the required checks on the resulting **main commit** to pass.
3. Run `pdctl release EXACT_MAIN_COMMIT vX.Y.Z`. The broker confirms main
   ancestry, the merged PR's verifier App check, main CI and manifest versions
   before creating the tag. Release tags cannot be moved or deleted.
4. The release workflow runs eligibility → build/test/manifest validation →
   installed-wheel integration on Python 3.13 and 3.14 → PyPI → MCP Registry →
   GitHub release. Integration builds documentation 3.10–3.14 before publishing.
   The build job records the SHA-256 of every file in `dist/` and the artifact ID
   as job outputs. The PyPI and GitHub-release jobs download that artifact ID and
   refuse to attest, publish or attach anything whose checksum listing differs.
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
To update it, run
`echo hatchling | uv pip compile --generate-hashes --universal --python-version 3.12 --upgrade - -o .github/build-constraints.txt`,
then
`uv build --build-constraint .github/build-constraints.txt --require-hashes --out-dir "$(mktemp -d)"`,
and review the diff like a lockfile change. A broken file fails the release in
the build job, before anything is published. The tag is spent by then, though,
so check the file before you tag.

PyPI Trusted Publishing remains bound to this repository, `release.yml` and the
`pypi` environment. Activation restricts that environment to `v*` tags and tag
creation to the owner App; the separate immutable-tag rule has no bypass actors.
No routine human reviewer is required. If publication partially fails, record
which registries succeeded and diagnose the failure; never overwrite a published
version, move a tag, or claim a release complete from a partial result.
