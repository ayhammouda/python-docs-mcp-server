# Release Artifact Integrity Implementation Plan (PR 8)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A release publishes, attests and attaches exactly the files the
build job produced, and the build backend is pinned by hash.

**Architecture:** One PR on branch `ci/release-artifact-integrity`. It
changes `.github/workflows/release.yml`, which is forbidden territory, so the
PR needs supervisor review.
- The `build` job exports two job outputs: the upload's artifact ID, and the
  sorted `sha256sum dist/*` listing.
- `publish` and `github-release` download that exact artifact ID. They
  compare the listing before any attest, publish or release step.
- If a later job replaces `dist`, the replacement gets a new ID, so the
  download fails. If a file changes, the comparison fails.
- The checksums travel in job outputs, never inside the artifact.
- The build installs `hatchling` and its five dependencies from a
  hash-pinned constraints file.

**Tech Stack:** GitHub Actions (`actions/upload-artifact` v7 at `043fb46`,
`actions/download-artifact` v8 at `3e5f45b`), bash with coreutils
`sha256sum`, uv (`uv build --build-constraint … --require-hashes`,
`uv pip compile --generate-hashes`), PyYAML (already a runtime dependency) and
pytest.

**Spec:** `docs/research/2026-10-30-audit-fix-best-practices.md`, section
"Ops, security and CI", item 5 (Finding 16). Binding decisions:
`docs/plans/2026-10-30-audit-fixes-roadmap.md` D13 and PR map row 8.

## Global Constraints

- **Precondition:** `docs/plans/2026-10-30-*.md` and `docs/research/2026-10-30-audit-fix-best-practices.md` are on `main` (roadmap Wave 0.5); stop if not.
- **Base:** `main` after #162 merges (Wave 1; no other dependency). Never
  merge `feat/mcp-sdk-v2` into another branch.
- **Line numbers** are cited at `c03e497`; #162 does not touch
  `.github/`. Early steps of Task 1 shift the lines that its later steps edit,
  so find each edit by its step name.
- **Forbidden territory:** `.github/workflows/release.yml`
  (`AGENT-EXECUTION-PIPELINE.md` §2 and §7). The PR gets the
  `supervisor-review` label and a `## Why this triggered supervisor review`
  section. That section cites: "The owner (Aymen Hammouda) asked on
  2026-10-30 to implement all audit findings; decisions in
  `docs/plans/2026-10-30-audit-fixes-roadmap.md`." CODEOWNERS also covers the
  new `.github/build-constraints.txt` and `.github/RELEASE.md`.
- **Keep Trusted Publishing as it is.** `publish` stays a top-level job with
  `environment: pypi` and `id-token: write`, never a reusable workflow.
  PEP 740 attestations from `pypa/gh-action-pypi-publish` stay on.
- **Keep e2e's name-based input.** `integration` still passes
  `artifact-name: dist`, and `e2e.yml` does not change. Scheduled e2e runs
  build their own package.
- **Checksums never go inside the artifact.** A replacement would replace
  them too.
- **No `pyproject.toml` change.** `[build-system]` keeps
  `requires = ["hatchling"]`, and the constraints file pins it. `[build-system]`
  lies outside `[project]`, but §7 flags any `pyproject.toml` field, so it
  stays untouched.
- **TDD:** superpowers:test-driven-development. Watch each new test fail
  before the change. One finding is one commit, with its test and its fix
  together.
- **Gate (`AGENTS.md`), run fresh before the PR:**
  - `uv run --locked ruff check src/ tests/ benchmarks/ ops/ .github/scripts/`
  - `uv run --locked pyright src/ benchmarks/`
  - `uv run --locked pytest --tb=short -q`
- **Existing tests:** none changes. `test_slow_e2e_workflow_runs_installed_build_index`
  stays as it is.
- **Dependencies and network:** no new runtime or dev dependency, and no
  runtime network access. The build already downloads its backend; now it
  checks the hashes.
- **CHANGELOG:** an entry under `## [Unreleased]`, `### Security`.
- **Commits and PRs:** conventional prefixes. Every commit ends with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. The PR body
  follows `.github/PULL_REQUEST_TEMPLATE/agent.md` and ends with
  `🤖 Generated with [Claude Code](https://claude.com/claude-code)`. Never
  self-merge.

## Checked on 2026-10-30

- **`download-artifact` at the pinned `3e5f45b`** (`src/download-artifact.ts`):
  - With `artifact-ids` and exactly one match, files land directly in `path`.
    There is no per-artifact subfolder, so `merge-multiple` is not needed.
  - An ID missing from the run's latest artifacts fails with
    `None of the provided artifact IDs were found`.
  - It lists the current run's artifacts with the runtime token, so it needs
    neither `actions: read` nor a `github-token` input.
  - It checks each artifact's digest on download by default.
- **`upload-artifact` at the pinned `043fb46`** (`action.yml`): it outputs
  `artifact-id`, `artifact-url` and `artifact-digest`. `overwrite: true`
  deletes the old artifact before it uploads, so the new one has a new ID.
- **uv 0.13.6 flags.** `--build-constraint` is accepted, as an alias of
  `--build-constraints`. With `--require-hashes`:
  - a dependency missing from the file fails with
    ``Hash-checking is required, but no trusted hash was provided for: `tomlkit` ``;
  - a wrong hash fails with ``Hash mismatch for `hatchling==1.33.0` ``.
- **The constraints file.**
  `echo hatchling | uv pip compile --generate-hashes --universal --python-version 3.12 - -o .github/build-constraints.txt`
  pins six packages with two hashes each: hatchling 1.33.0, packaging 26.3,
  pathspec 1.1.1, pluggy 1.7.0, tomlkit 0.15.2 and trove-classifiers
  2026.9.21.13. `uv build --build-constraint .github/build-constraints.txt --require-hashes`
  then builds the sdist and the wheel.
- **How GitHub runs a step with no `shell:` key.** It uses `bash -e {0}`,
  which has no `pipefail`. Two consequences:
  - Without `set -o pipefail`, an empty `dist/` makes
    `sha256sum dist/* | LC_ALL=C sort` exit 0 with an empty listing.
  - `download-artifact` treats an empty `artifact-ids` as "download every
    artifact of the run".

  The scripts therefore start with `set -o pipefail`, and the verify step
  refuses an empty expected listing. The upload step uses
  `if-no-files-found: error`.
- **The new tests.** On today's `release.yml`, 11 of the 13 fail and the 2
  that pin unchanged behaviour pass. On the prototype all 13 pass. Under
  `bash -e`, an empty `dist/` makes the checksum step fail with
  `sha256sum: dist/*: No such file or directory`, because the script sets
  `pipefail`.

## Decisions This Plan Makes

- **Where the constraints file lives.** `.github/build-constraints.txt`, next
  to the release tooling and outside `.github/workflows/`.
- **How the verify step compares.** It reads the expected listing from `env:`,
  never from a `${{ }}` expression inside `run:`. It refuses an empty
  listing, then compares exact strings. That also catches extra or missing
  files that `sha256sum -c` would miss. `publish` and `github-release` run
  the identical script, and a test pins that.
- **`pipefail` in every script.** The checksum and verify scripts start with
  `set -o pipefail`, because GitHub's default `bash -e` would let a failing
  `sha256sum` pass through `sort`.
- **Where the checksum step sits.** Immediately before the upload, so nothing
  can touch `dist/` between them. A test pins that, and the upload fails when
  it finds no files.
- **What the tests run.** They execute the workflow's own `run:` scripts with
  `bash -e`, the way GitHub runs a step without a `shell:` key. That tests the
  multi-line output format and the comparison itself, not just their
  presence.
- **The overwrite drill.** It runs once, on a fork, by the owner (Task 2),
  because CI cannot overwrite an artifact inside its own run.

## Review Focus

1. **The downloaded `dist` has an extra, missing or changed file, or the
   build exported no checksums.** Expected: the verify step fails before any
   attest or publish step. Pinned by Task 1,
   `test_release_verify_step_refuses_any_change_to_dist`.
2. **The build produced no files.** Expected: under GitHub's default
   `bash -e`, the checksum step fails, so nothing is uploaded. Pinned by
   Task 1, `test_release_checksum_step_fails_on_an_empty_dist`.
3. **A step writes into `dist/` between the checksum and the upload.**
   Expected: impossible, because the two steps are adjacent. Pinned by
   Task 1, `test_release_build_exports_the_dist_id_and_checksums`.
4. **Only the failed `publish` job is re-run** after a transient PyPI error.
   Expected: it reuses the build's outputs and passes. Pinned by Task 2's
   drill, step 4.
5. **The constraints file is stale, misses a build requirement, or has a pin
   without a hash.** In CI that would show up only when a tag runs, and tags
   cannot be moved, so the version would be lost.
   - A missing build requirement or an unhashed pin: caught before merge by
     Task 1, `test_build_constraints_pin_every_build_requirement` and
     `test_release_build_pins_hatchling_and_its_dependencies_by_hash`.
   - A stale pin, such as a new hatchling dependency or a yanked version:
     caught only by the local build that `.github/RELEASE.md` asks for before
     tagging (Task 2). Task 1 runs that build once.

## Not In This Plan

- **SLSA Build L3** (roadmap D13). That means attesting in `build` or in a
  dedicated job that downloads by ID, then running
  `gh attestation verify --signer-workflow` in `publish`. Moving to
  `actions/attest` goes with it.
- **The mutation-testing job** (roadmap D16).
- **Passing the artifact ID into `e2e.yml`**, so that integration tests the
  exact bytes. The research marks it optional, and it would touch a second
  workflow.
- **Building with the constraints file in `ci.yml`, `e2e.yml` or
  `product-quality.yml`.** That would catch a broken constraints file on
  every PR instead of at tag time. It is another forbidden-territory edit, so
  it is the owner's call.

---

### Task 1: Ship only what the build produced, from a hash-pinned backend (Finding 16)

Forbidden territory: this task edits `.github/workflows/release.yml`.

Research item 5 is one finding, so it gets one commit. That commit carries
two test cycles: the artifact ID and checksums (Steps 1 to 7), then the build
constraints (Steps 8 to 13).

**Files:**
- Modify: `.github/workflows/release.yml`:
  - `build` (`:34-114`): add `outputs` after `:38`, change "Build package"
    (`:91-92`), add a checksum step before `:110`, and give the upload step
    at `:110` an `id`;
  - `publish` (`:122-146`): the download at `:134-138`, plus a verify step
    after it;
  - `github-release` (`:171-192`): `needs` at `:172-174`, the download at
    `:182-186`, plus a verify step after it
- Create: `.github/build-constraints.txt` (generated)
- Test: `tests/test_ci_workflows.py`

**Interfaces:**
- Produces:
  - job outputs `build.outputs.dist-id` (the upload's `artifact-id`) and
    `build.outputs.dist-sha256` (the listing);
  - the step ids `upload-dist` and `dist-sha256`;
  - the step output `dist-sha256`, written with the heredoc delimiter
    `DIST_SHA256_EOF`;
  - a verify step whose `env` sets `EXPECTED: ${{ needs.build.outputs.dist-sha256 }}`,
    with the same `run:` text in `publish` and `github-release`;
  - `set -o pipefail` as the first line of the checksum and verify
    scripts, and `if-no-files-found: error` on the upload step;
  - `.github/build-constraints.txt` in uv's hashed requirements format. Every
    pin is `name==version \` followed by `--hash=sha256:<64 hex>` lines.
- Test module helper: `release_jobs() -> dict`.

- [ ] **Step 1: Add these imports to `tests/test_ci_workflows.py`** (ruff's
  isort order): `import os`, `import shutil`, `import subprocess`,
  `import pytest` and `import yaml`.

- [ ] **Step 2: Append the tests**

```python
RELEASE = PROJECT_ROOT / ".github" / "workflows" / "release.yml"
DIST_ID = "${{ needs.build.outputs.dist-id }}"
DIST_SHA256 = "${{ needs.build.outputs.dist-sha256 }}"
SHIPPING = (
    "actions/attest-build-provenance@",
    "pypa/gh-action-pypi-publish@",
    "softprops/action-gh-release@",
)


def release_jobs() -> dict:
    return yaml.safe_load(RELEASE.read_text(encoding="utf-8"))["jobs"]


def step_id(output: str, suffix: str) -> str:
    """`${{ steps.X.outputs.<suffix> }}` -> X."""
    return output.removeprefix("${{ steps.").removesuffix(f".outputs.{suffix} }}}}")


def test_release_build_exports_the_dist_id_and_checksums() -> None:
    build = release_jobs()["build"]
    ids = [step.get("id") for step in build["steps"]]
    upload = ids.index(step_id(build["outputs"]["dist-id"], "artifact-id"))
    sums = ids.index(step_id(build["outputs"]["dist-sha256"], "dist-sha256"))
    assert build["steps"][upload]["uses"].startswith("actions/upload-artifact@")
    assert build["steps"][upload]["with"]["if-no-files-found"] == "error"
    assert build["steps"][sums]["run"].startswith("set -o pipefail\n")
    assert "sha256sum dist/* | LC_ALL=C sort" in build["steps"][sums]["run"]
    assert sums == upload - 1  # nothing can touch dist/ between checksum and upload


@pytest.mark.parametrize("job_name", ["publish", "github-release"])
def test_release_ships_only_the_bytes_the_build_checksummed(job_name: str) -> None:
    job = release_jobs()[job_name]
    steps = job["steps"]
    assert "build" in job["needs"]
    downloads = [i for i, s in enumerate(steps) if "download-artifact@" in s.get("uses", "")]
    assert downloads
    for index in downloads:
        assert steps[index]["with"] == {"artifact-ids": DIST_ID, "path": "dist/"}
    verify = [i for i, s in enumerate(steps) if s.get("env", {}).get("EXPECTED") == DIST_SHA256]
    assert len(verify) == 1
    assert "sha256sum dist/* | LC_ALL=C sort" in steps[verify[0]]["run"]
    assert steps[verify[0]]["run"] == release_scripts()[1]  # one script for both jobs
    shipping = [i for i, s in enumerate(steps) if s.get("uses", "").startswith(SHIPPING)]
    assert shipping
    assert max(downloads) < verify[0] < min(shipping)


def test_release_keeps_trusted_publishing_and_the_named_e2e_input() -> None:
    jobs = release_jobs()
    assert jobs["publish"]["environment"] == "pypi"
    assert jobs["publish"]["permissions"]["id-token"] == "write"
    assert "uses" not in jobs["publish"]
    assert jobs["integration"]["with"] == {"artifact-name": "dist"}


def run_step(script: str, cwd: Path, **env: str) -> subprocess.CompletedProcess[str]:
    """Run a workflow `run:` block the way GitHub runs a step without a shell: key."""
    return subprocess.run(
        ["bash", "-e", "-c", script],
        cwd=cwd,
        env={"PATH": os.environ["PATH"], **env},
        capture_output=True,
        text=True,
    )


def release_scripts() -> tuple[str, str]:
    jobs = release_jobs()
    record = next(s for s in jobs["build"]["steps"] if s.get("id") == "dist-sha256")["run"]
    verify = next(s for s in jobs["publish"]["steps"] if "EXPECTED" in s.get("env", {}))["run"]
    return record, verify


needs_sha256sum = pytest.mark.skipif(shutil.which("sha256sum") is None, reason="no sha256sum")


@needs_sha256sum
@pytest.mark.parametrize(
    "tamper", ["none", "extra_file", "changed_byte", "missing_file", "no_checksums"]
)
def test_release_verify_step_refuses_any_change_to_dist(tmp_path: Path, tamper: str) -> None:
    record, verify = release_scripts()
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "pkg-1.0-py3-none-any.whl").write_bytes(b"wheel")
    (dist / "pkg-1.0.tar.gz").write_bytes(b"sdist")
    output = tmp_path / "github_output"
    assert run_step(record, tmp_path, GITHUB_OUTPUT=str(output)).returncode == 0
    lines = output.read_text().splitlines()
    assert lines[0] == "dist-sha256<<DIST_SHA256_EOF" and lines[-1] == "DIST_SHA256_EOF"
    expected = "" if tamper == "no_checksums" else "\n".join(lines[1:-1])
    if tamper == "extra_file":
        (dist / "evil-1.0-py3-none-any.whl").write_bytes(b"evil")
    elif tamper == "changed_byte":
        (dist / "pkg-1.0.tar.gz").write_bytes(b"sdisT")
    elif tamper == "missing_file":
        (dist / "pkg-1.0.tar.gz").unlink()
    result = run_step(verify, tmp_path, EXPECTED=expected)
    assert result.returncode == (0 if tamper == "none" else 1), result.stdout


@needs_sha256sum
def test_release_checksum_step_fails_on_an_empty_dist(tmp_path: Path) -> None:
    record, _ = release_scripts()
    (tmp_path / "dist").mkdir()
    output = tmp_path / "github_output"
    assert run_step(record, tmp_path, GITHUB_OUTPUT=str(output)).returncode != 0
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run --locked pytest tests/test_ci_workflows.py -q`
Expected: `9 failed, 2 passed`.
- `KeyError: 'outputs'`.
- `assert {'name': 'dist', 'path': 'dist/'} == {'artifact-ids': …, 'path': 'dist/'}`
  for `publish`.
- `assert 'build' in ['publish', 'publish-mcp-registry']` for
  `github-release`.
- `StopIteration` for the six script runs, because there is no `dist-sha256`
  step yet.
- The e2e test and the Trusted Publishing test pass.

- [ ] **Step 4: Export the ID and the listing from `build`.** Add this under
  `timeout-minutes: 15` (`:38`):

```yaml
    outputs:
      dist-id: ${{ steps.upload-dist.outputs.artifact-id }}
      dist-sha256: ${{ steps.dist-sha256.outputs.dist-sha256 }}
```

  Insert the checksum step after "Verify wheel contents" and directly before
  "Upload dist artifacts". Then give the upload step `id: upload-dist` and
  add `if-no-files-found: error` to its `with:`. Its `uses:`, `name:` and
  `path:` lines stay as they are. The script's first line is
  `set -o pipefail`, because GitHub runs a step with no `shell:` key as
  `bash -e {0}`, without `pipefail`:

```yaml
      - name: Record dist checksums
        id: dist-sha256
        run: |
          set -o pipefail
          {
            echo "dist-sha256<<DIST_SHA256_EOF"
            sha256sum dist/* | LC_ALL=C sort
            echo "DIST_SHA256_EOF"
          } >> "$GITHUB_OUTPUT"

      - name: Upload dist artifacts
        id: upload-dist
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7
        with:
          name: dist
          path: dist/
          if-no-files-found: error
```

- [ ] **Step 5: Download by ID and verify in `publish`.**
  - In "Download dist artifacts" (`:134-138`), replace `name: dist` with
    `artifact-ids: ${{ needs.build.outputs.dist-id }}`. Keep `path: dist/`.
  - Insert this step right after it, before "Generate artifact attestation".
    The empty-listing guard matters because `download-artifact` treats an
    empty `artifact-ids` as "download every artifact":

```yaml
      - name: Verify dist matches the build job
        env:
          EXPECTED: ${{ needs.build.outputs.dist-sha256 }}
        run: |
          set -o pipefail
          [ -n "$EXPECTED" ] || { echo "::error::build exported no checksums"; exit 1; }
          ACTUAL="$(sha256sum dist/* | LC_ALL=C sort)"
          if [ "$ACTUAL" != "$EXPECTED" ]; then
            echo "::error::dist differs from the files the build job checksummed"
            printf 'expected:\n%s\nactual:\n%s\n' "$EXPECTED" "$ACTUAL"
            exit 1
          fi
```

- [ ] **Step 6: Do the same in `github-release`.**
  - Add `- build` as the first entry of its `needs` (`:172-174`).
  - Make the same two changes to "Download dist artifacts" (`:182-186`).
  - Insert the identical verify step before "Create GitHub Release". A test
    checks that its `run:` text equals `publish`'s.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run --locked pytest tests/test_ci_workflows.py -q`
Expected: `11 passed`

- [ ] **Step 8: Add `import re` to the imports, then append the
  constraints tests**

```python
def test_build_constraints_pin_every_build_requirement() -> None:
    import tomllib

    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    text = (PROJECT_ROOT / ".github" / "build-constraints.txt").read_text(encoding="utf-8")
    pinned = {
        line.split("==")[0]
        for line in text.splitlines()
        if "==" in line and not line.startswith("#")
    }
    requires = pyproject["build-system"]["requires"]
    names = {re.split(r"[\s<>=!~;\[]", r, maxsplit=1)[0].lower() for r in requires}
    assert names <= pinned


def test_release_build_pins_hatchling_and_its_dependencies_by_hash() -> None:
    build = next(s for s in release_jobs()["build"]["steps"] if s.get("name") == "Build package")
    assert build["run"] == (
        "uv build --build-constraint .github/build-constraints.txt --require-hashes"
    )
    text = (PROJECT_ROOT / ".github" / "build-constraints.txt").read_text(encoding="utf-8")
    hashed = re.findall(
        r"^([a-z0-9_.-]+)==\S+ \\\n(?:    --hash=sha256:[0-9a-f]{64}(?: \\)?\n)+", text, re.M
    )
    pinned = [line for line in text.splitlines() if "==" in line and not line.startswith("#")]
    assert "hatchling" in hashed
    assert len(hashed) == len(pinned) >= 6
```

- [ ] **Step 9: Run them to verify they fail**

Run: `uv run --locked pytest tests/test_ci_workflows.py -q -k "pins_hatchling or pin_every"`
Expected: `2 failed`.
- The hatchling test fails with
  `assert 'uv build' == 'uv build --build-constraint .github/build-constraints.txt --require-hashes'`.
- The requirement test fails with `FileNotFoundError` for
  `.github/build-constraints.txt`.

Neither test can see a stale pin, such as a new hatchling dependency or a
yanked version. Only the local build in Step 11, and the same build before
each tag (`.github/RELEASE.md`), catches that.

- [ ] **Step 10: Generate the constraints file**

Run: `echo hatchling | uv pip compile --generate-hashes --universal --python-version 3.12 - -o .github/build-constraints.txt`
Expected: a file whose header records that command. It pins hatchling and
its five dependencies (packaging, pathspec, pluggy, tomlkit,
trove-classifiers) with two hashes each. `--python-version 3.12` matches
`requires-python`, so no `tomli` pin appears.

- [ ] **Step 11: Build with it locally**

Run: `uv build --build-constraint .github/build-constraints.txt --require-hashes --out-dir "$(mktemp -d)"`
Expected: `Successfully built …python_docs_mcp_server-<version>.tar.gz` and
`Successfully built …python_docs_mcp_server-<version>-py3-none-any.whl`

- [ ] **Step 12: Pin the release build.** In "Build package", the `run`
  line becomes
  `run: uv build --build-constraint .github/build-constraints.txt --require-hashes`.

- [ ] **Step 13: Run the workflow tests**

Run: `uv run --locked pytest tests/test_ci_workflows.py -q`
Expected: `13 passed`

- [ ] **Step 14: Commit**

```bash
git add .github/build-constraints.txt .github/workflows/release.yml tests/test_ci_workflows.py
git commit -m "ci: publish only the release files the build produced" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 2: Document, run the gate, open the PR, run the drill

**Files:**
- Modify: `.github/RELEASE.md` (step 4, and a new paragraph after the
  publisher-helper paragraph), `CHANGELOG.md` (`## [Unreleased]`)

- [ ] **Step 1: Update the release runbook.**
  - In step 4 of `.github/RELEASE.md`, replace "PyPI receives the same wheel
    that passed integration, with provenance." with: "The build job records
    the SHA-256 of every file in `dist/` and the artifact ID as job outputs.
    The PyPI and GitHub-release jobs download that artifact ID and refuse to
    attest, publish or attach anything whose checksum listing differs."
  - After the paragraph about `.github/scripts/install_publisher.sh`, add:
    > The release build installs its build backend from
    > `.github/build-constraints.txt`. That file pins `hatchling` and every
    > dependency with SHA-256 hashes, and the build runs
    > `uv build --build-constraint .github/build-constraints.txt --require-hashes`.
    > To update it, run
    > `echo hatchling | uv pip compile --generate-hashes --universal --python-version 3.12 --upgrade - -o .github/build-constraints.txt`,
    > then
    > `uv build --build-constraint .github/build-constraints.txt --require-hashes --out-dir "$(mktemp -d)"`,
    > and review the diff like a lockfile change. A broken file fails the
    > release in the build job, before anything is published. The tag is
    > spent by then, though, so check the file before you tag.

- [ ] **Step 2: Add the changelog entry** under `## [Unreleased]`, in
  `### Security` (create the heading after `### Fixed` if it is missing):

```markdown
### Security

- Releases publish only the exact files the build job produced. The PyPI and
  GitHub-release jobs download `dist` by artifact ID. They refuse to attest,
  upload or attach any file whose SHA-256 listing differs from the build's.
  The release build pins `hatchling` and its dependencies by hash.
```

- [ ] **Step 3: Run the gate in a fresh shell**

| Run | Expected |
|---|---|
| `uv run --locked ruff check src/ tests/ benchmarks/ ops/ .github/scripts/` | `All checks passed!` |
| `uv run --locked pyright src/ benchmarks/` | `0 errors` |
| `uv run --locked pytest --tb=short -q` | all passed |
| `uv run --locked python -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml'))"` | exit code 0 |

- [ ] **Step 4: Push and open the PR with the `supervisor-review` label.**
  Title: `ci: publish only the release files the build produced`. Fill in
  `.github/PULL_REQUEST_TEMPLATE/agent.md` with
  `Refs docs/plans/2026-10-30-audit-fixes-roadmap.md (PR 8)`. The body
  states:
  - the acceptance criteria, with test names as evidence;
  - a `## Why this triggered supervisor review` section. It names
    `.github/workflows/release.yml` (pipeline §2 and §7) and quotes the
    owner approval from Global Constraints;
  - under "Product decision": kind `policy`. Security impact: it closes the
    window in which a job between `build` and `publish` could swap `dist`.
    Every release script sets `pipefail`, because GitHub's default `bash -e`
    does not. Trusted Publishing, PEP 740 attestations, tag policy and e2e's
    name-based input are unchanged;
  - the drill below, as an unchecked acceptance item for the owner;
  - the gate output and the CodeRabbit triage once it comments;
  - the closing line `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

- [ ] **Step 5: Owner step: run the overwrite drill once on a fork, before
  merging.** The executor cannot run it. The owner does, and records the
  result in the PR.
  1. On a fork with Actions enabled, push this branch. Then add one drill
     commit that edits only the fork's `release.yml`:
     - Delete the `eligibility` job and the `needs: eligibility` line in
       `build`. The fork has no verifier App.
     - Replace the `integration` job with:

```yaml
  tamper:
    needs: build
    runs-on: ubuntu-latest
    steps:
      - run: mkdir dist && printf 'swapped' > dist/python_docs_mcp_server-0.0.0-py3-none-any.whl
      - uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7
        with:
          name: dist
          path: dist/
          overwrite: true
```

     - Set `publish.needs` to `[build, tamper]`.
     - Replace both "Generate artifact attestation" and "Publish to PyPI"
       with `- run: ls -l dist`. On a public fork the attestation step would
       leave a permanent Sigstore record for a test wheel that carries the
       real version.
     - Delete the `publish-mcp-registry` job.
     - Set `github-release.needs` to `[build, publish]`, and replace its
       "Create GitHub Release" step with `- run: ls -l dist`.
  2. Push the tag `v<version in pyproject.toml>` to the fork.
     Expected: `build` and `tamper` succeed. `publish` fails at "Download
     dist artifacts" with `None of the provided artifact IDs were found`.
     The steps after it are skipped, and so is `github-release`.
  3. Control run: remove the `tamper` job and set `publish.needs` to
     `[build]`. Then delete and re-push the fork tag.
     Expected: `publish` and `github-release` pass "Download dist artifacts"
     and "Verify dist matches the build job", and `ls -l dist` lists one
     wheel and one sdist.
  4. In the control run, re-run the `publish` job alone. Expected: it passes
     again with the same artifact ID (Review Focus 4).
  5. Paste the two run URLs into the PR. Delete the fork tag and the drill
     branch.
