# Release Process

## One-Time Setup: PyPI Trusted Publishing

Before the first release, configure PyPI Trusted Publishing:

1. Go to https://pypi.org/manage/account/publishing/
2. Add a new pending publisher:
   - **PyPI project name**: `mcp-server-python-docs`
   - **Owner**: your GitHub username or org
   - **Repository**: `python-docs-mcp-server`
   - **Workflow name**: `release.yml`
   - **Environment name**: `pypi`
3. In the GitHub repo, go to Settings > Environments
4. Create an environment named `pypi`
5. (Optional) Add environment protection rules:
   - Required reviewers (recommended for first release)
   - Deployment branches: only `main` tags

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
   # Should show: version = "0.1.0"
   ```

3. Create and push the tag:
   ```bash
   git tag -a v0.1.0 -m "Release v0.1.0"
   git push origin v0.1.0
   ```

4. Monitor the release workflow at:
   https://github.com/<owner>/python-docs-mcp-server/actions/workflows/release.yml

5. Verify the package on PyPI:
   https://pypi.org/project/mcp-server-python-docs/0.1.0/

## Post-Release Verification

After the package is published:

```bash
# In a fresh environment:
uvx mcp-server-python-docs --version
# Should print: 0.1.0

# Or via pipx:
pipx run mcp-server-python-docs --version
# Should print: 0.1.0
```

## v0.1.0 Release Checklist

Complete these steps in order. Each step has a checkbox -- do not skip ahead.

### Pre-Release Verification

- [ ] All CI tests green on main: check https://github.com/<owner>/python-docs-mcp-server/actions/workflows/ci.yml
- [ ] Local test suite passes:
  ```bash
  uv run pytest --tb=short -q
  ```
- [ ] Version in `pyproject.toml` is `0.1.0`:
  ```bash
  grep '^version' pyproject.toml
  ```
- [ ] Integration tests from `.github/INTEGRATION-TEST.md` are complete and signed off
- [ ] Doctor subcommand passes:
  ```bash
  mcp-server-python-docs doctor
  ```

### PyPI Trusted Publishing Setup (one-time)

- [ ] PyPI pending publisher configured at https://pypi.org/manage/account/publishing/:
  - PyPI project name: `mcp-server-python-docs`
  - Owner: `<your-github-username>`
  - Repository: `python-docs-mcp-server`
  - Workflow name: `release.yml`
  - Environment name: `pypi`
- [ ] GitHub environment `pypi` created in repo Settings > Environments

### Tag and Release

- [ ] Create the annotated tag:
  ```bash
  git tag -a v0.1.0 -m "Release v0.1.0

  First public release of mcp-server-python-docs.

  A read-only, version-aware MCP retrieval server over Python
  standard library documentation (3.12 + 3.13).

  Installable via: uvx mcp-server-python-docs"
  ```
- [ ] Push the tag to trigger the release workflow:
  ```bash
  git push origin v0.1.0
  ```
- [ ] Monitor the workflow run: https://github.com/<owner>/python-docs-mcp-server/actions/workflows/release.yml
- [ ] Verify all three jobs pass: `build` -> `publish` -> `github-release`

### Post-Release Verification (SHIP-06)

- [ ] Package visible on PyPI: https://pypi.org/project/mcp-server-python-docs/0.1.0/
- [ ] Attestation visible on PyPI package page (look for "Provenance" badge)
- [ ] Fresh install test:
  ```bash
  # Clear any cached version
  uv cache clean mcp-server-python-docs 2>/dev/null || true

  # Install and verify version
  uvx mcp-server-python-docs --version
  # Expected output: 0.1.0
  ```
- [ ] Full README flow test (from a clean environment):
  ```bash
  # Step 1: Install
  uvx mcp-server-python-docs --version
  # Should print 0.1.0

  # Step 2: Build index
  uvx mcp-server-python-docs build-index --versions 3.12,3.13
  # Should complete successfully

  # Step 3: Doctor check
  uvx mcp-server-python-docs doctor
  # All checks should PASS
  ```
- [ ] Claude Desktop test with published package:
  Configure `mcpServers` with `uvx mcp-server-python-docs` and verify
  "what is asyncio.TaskGroup" returns a correct hit

### Release Complete

- [ ] GitHub Release exists with attached artifacts
- [ ] PyPI page shows 0.1.0 with attestation
- [ ] README install instructions verified end-to-end
- [ ] Tag v0.1.0 exists in git

**Release date**: _______________
**Released by**: _______________
