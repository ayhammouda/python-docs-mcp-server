# Release Process

## One-Time Setup: PyPI Trusted Publishing

Before the first release, configure PyPI Trusted Publishing:

1. Go to https://pypi.org/manage/account/publishing/
2. Add a new pending publisher:
   - **PyPI project name**: `python-docs-mcp-server`
   - **Owner**: your GitHub username or org
   - **Repository**: `python-docs-mcp-server`
   - **Workflow name**: `release.yml`
   - **Environment name**: `pypi`
3. In the GitHub repo, go to Settings > Environments
4. Create an environment named `pypi`
5. (Optional) Add environment protection rules:
   - Required reviewers (recommended for first release)
   - Deployment branches: only `main` tags

## Notes

**Runtime coverage:** The release workflow builds and tests against Python 3.13 only.
Python 3.12 is covered by the CI workflow (`ci.yml`) which runs a 2x2 matrix
(3.12/3.13 x ubuntu/macos) on every push to `main`. Since tags are created
from commits that have already passed CI, 3.12 compatibility is verified before
the release workflow runs. This is an accepted trade-off to keep the release
artifact pipeline simple (single Python version produces the wheel).

**Documentation coverage:** The full docs index target is Python documentation
versions 3.10 through 3.14.

## Creating a Release

1. Ensure all tests pass on main:
   ```bash
   uv run pytest --tb=short -q
   uv run ruff check src/ tests/
   uv run pyright src/
   ```

2. Verify the version in `pyproject.toml` is correct:
   ```bash
   grep '^version' pyproject.toml
   # Should show: version = "0.1.2"
   ```

3. Create and push the tag:
   ```bash
   git tag -a v0.1.2 -m "Release v0.1.2"
   git push origin v0.1.2
   ```

4. Monitor the release workflow at:
   https://github.com/<owner>/python-docs-mcp-server/actions/workflows/release.yml

5. Verify the package on PyPI:
   https://pypi.org/project/python-docs-mcp-server/0.1.2/

## Post-Release Verification

After the package is published:

```bash
# In a fresh environment:
uvx python-docs-mcp-server --version
# Should print: 0.1.1

# Or via pipx:
pipx run python-docs-mcp-server --version
# Should print: 0.1.1
```

## v0.1.1 Release Checklist

Complete these steps in order. Each step has a checkbox -- do not skip ahead.

### Pre-Release Verification

- [ ] All CI tests green on main: check https://github.com/<owner>/python-docs-mcp-server/actions/workflows/ci.yml
- [ ] Local test suite passes:
  ```bash
  uv run pytest --tb=short -q
  ```
- [ ] Version in `pyproject.toml` is `0.1.1`:
  ```bash
  grep '^version' pyproject.toml
  ```
- [ ] Integration tests from `.github/INTEGRATION-TEST.md` are complete and signed off
- [ ] Doctor subcommand passes:
  ```bash
  uv run python-docs-mcp-server doctor
  ```

### PyPI Trusted Publishing Setup (one-time)

- [ ] PyPI pending publisher configured at https://pypi.org/manage/account/publishing/:
  - PyPI project name: `python-docs-mcp-server`
  - Owner: `<your-github-username>`
  - Repository: `python-docs-mcp-server`
  - Workflow name: `release.yml`
  - Environment name: `pypi`
- [ ] GitHub environment `pypi` created in repo Settings > Environments

### Tag and Release

- [ ] Create the annotated tag:
  ```bash
  git tag -a v0.1.1 -m "Release v0.1.1

  First public release of python-docs-mcp-server.

  A read-only, version-aware MCP retrieval server over Python
  standard library documentation (3.10 through 3.14).

  Installable via: uvx python-docs-mcp-server"
  ```
- [ ] Push the tag to trigger the release workflow:
  ```bash
  git push origin v0.1.1
  ```
- [ ] Monitor the workflow run: https://github.com/<owner>/python-docs-mcp-server/actions/workflows/release.yml
- [ ] Verify all three jobs pass: `build` -> `publish` -> `github-release`

### Post-Release Verification (SHIP-06)

- [ ] Package visible on PyPI: https://pypi.org/project/python-docs-mcp-server/0.1.2/
- [ ] Attestation visible on PyPI package page (look for "Provenance" badge)
- [ ] Fresh install test:
  ```bash
  # Clear any cached version
  uv cache clean python-docs-mcp-server 2>/dev/null || true

  # Install and verify version
  uvx python-docs-mcp-server --version
  # Expected output: 0.1.1
  ```
- [ ] Full README flow test (from a clean environment):
  ```bash
  # Step 1: Install
  uvx python-docs-mcp-server --version
  # Should print 0.1.1

  # Step 2: Build index
  uvx python-docs-mcp-server build-index --versions 3.10,3.11,3.12,3.13,3.14
  # Should complete successfully

  # Step 3: Doctor check
  uvx python-docs-mcp-server doctor
  # All checks should PASS
  ```
- [ ] Slow E2E workflow passes:
  - Run GitHub Actions workflow `Slow E2E`
  - Confirm Python 3.13 and Python 3.14 jobs both pass
  - Confirm each job installs the built wheel, runs
    `build-index --versions 3.10,3.11,3.12,3.13,3.14`, `doctor`, and
    `validate-corpus`

### Post-PyPI Launch Pack Cleanup

- [ ] Remove every temporary PyPI pre-release block from `README.md`:
  - Mechanical pass: delete every region from `<!-- PRE-PYPI:` to `<!-- /PRE-PYPI -->` (inclusive). Each block now encloses its surrounding heading + lead-in sentence + code, so a single pass produces a clean README.
  - Reference command:
    `perl -0777 -i -pe 's/<!-- PRE-PYPI:.*?<!-- \/PRE-PYPI -->\n*//gs' README.md`
  - Make the published package commands (`uvx python-docs-mcp-server ...`) the
    primary install, build-index, MCP client, `doctor`, and `validate-corpus`
    examples
- [ ] Verify `README.md` has no temporary pre-release install artifacts:
  ```bash
  rg -n 'PRE[-]PYPI|Before PyPI publishing|Until the first PyPI|After PyPI publishing|git\\+https://github.com/.*/python-docs-mcp-server' README.md
  ```
  The command should return no output.
- [ ] Review `docs/launch/` so no public launch copy still asks users to install
  from GitHub source after the PyPI package is available. Intentional historical
  pre-release drafts may remain, but they must stay clearly labeled as
  pre-PyPI-only.
- [ ] Submit `server.json` to https://registry.modelcontextprotocol.io/ via the
  `mcp-publisher` CLI after the PyPI smoke test passes; verify the registry
  listing appears and points at version 0.1.1
- [ ] Use the post-PyPI draft in `docs/launch/show-hn.md` for the HN submission
- [ ] Use `docs/launch/reddit-posts.md` for the r/Python and r/LocalLLaMA
  submissions after the PyPI release smoke test passes
- [ ] Commit and push the README cleanup before public launch posts go out

- [ ] Claude Desktop test with published package:
  Configure `mcpServers` with `uvx python-docs-mcp-server` and verify
  "what is asyncio.TaskGroup" returns a correct hit

### Release Complete

- [ ] GitHub Release exists with attached artifacts
- [ ] PyPI page shows 0.1.1 with attestation
- [ ] README install instructions verified end-to-end
- [ ] README no longer contains temporary pre-PyPI GitHub-source install blocks
- [ ] Slow E2E workflow passed for the release candidate
- [ ] Tag v0.1.1 exists in git

**Release date**: _______________
**Released by**: _______________
