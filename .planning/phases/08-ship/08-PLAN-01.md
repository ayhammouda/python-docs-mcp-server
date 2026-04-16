---
phase: 8
plan: 1
title: "GitHub Actions Release Workflow with PyPI Trusted Publishing"
wave: 1
depends_on: []
files_modified:
  - .github/workflows/release.yml
requirements:
  - PKG-05
autonomous: true
---

# Plan 08-01: GitHub Actions Release Workflow with PyPI Trusted Publishing

<objective>
Create a GitHub Actions workflow that publishes `mcp-server-python-docs` to PyPI using Trusted Publishing (OIDC) with attestations on tag push. No manual API token upload required.
</objective>

## Tasks

<task id="1">
<title>Create release.yml GitHub Actions workflow</title>

<read_first>
- .github/workflows/ci.yml
- pyproject.toml
</read_first>

<action>
Create `.github/workflows/release.yml` with the following structure:

1. **Trigger**: on push of tags matching `v*` (e.g., `v0.1.0`)
2. **Build job** (`build`):
   - runs-on: `ubuntu-latest`
   - Steps:
     - `actions/checkout@v4`
     - `astral-sh/setup-uv@v4`
     - `uv python install 3.13`
     - `uv sync --dev`
     - `uv run ruff check src/ tests/`
     - `uv run pyright src/`
     - `uv run pytest --tb=short -q`
     - `uv build` (produces sdist + wheel in `dist/`)
     - Verify wheel contains `synonyms.yaml` (same check as ci.yml)
     - Upload `dist/` as artifact using `actions/upload-artifact@v4` with name `dist`

3. **Publish job** (`publish`):
   - needs: `build`
   - runs-on: `ubuntu-latest`
   - environment: `pypi` (GitHub environment for protection rules)
   - permissions:
     - `id-token: write` (for OIDC)
     - `contents: read`
     - `attestations: write` (for artifact attestations)
   - Steps:
     - Download artifact `dist` using `actions/download-artifact@v4`
     - Generate artifact attestation using `actions/attest-build-provenance@v2` with `subject-path: 'dist/*'`
     - Publish to PyPI using `pypa/gh-action-pypi-publish@release/v1` with no API token (Trusted Publishing via OIDC)

4. **GitHub Release job** (`github-release`):
   - needs: `publish`
   - runs-on: `ubuntu-latest`
   - permissions:
     - `contents: write`
   - Steps:
     - Download artifact `dist`
     - Create GitHub release using `softprops/action-gh-release@v2` with `files: dist/*` and `generate_release_notes: true`
</action>

<acceptance_criteria>
- `.github/workflows/release.yml` exists
- Workflow triggers on `push: tags: ['v*']`
- Build job runs linter, type checker, tests, and `uv build`
- Build job contains wheel content verification for `synonyms.yaml`
- Publish job has `permissions: id-token: write` for OIDC Trusted Publishing
- Publish job has `environment: pypi` for GitHub environment protection
- Publish job uses `pypa/gh-action-pypi-publish@release/v1` without `password` key (Trusted Publishing)
- Publish job uses `actions/attest-build-provenance@v2` for attestations
- GitHub Release job uses `softprops/action-gh-release@v2`
- Publish job has `needs: build`; GitHub Release job has `needs: publish`
</acceptance_criteria>
</task>

<task id="2">
<title>Add PyPI Trusted Publishing setup instructions</title>

<read_first>
- .github/workflows/release.yml (just created in task 1)
</read_first>

<action>
Create `.github/RELEASE.md` with instructions the repository owner must follow before the first release:

Content:

```markdown
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
```
</action>

<acceptance_criteria>
- `.github/RELEASE.md` exists
- File contains PyPI Trusted Publishing setup steps (pypi.org publisher config)
- File specifies workflow name `release.yml` and environment name `pypi`
- File contains the `git tag` command for v0.1.0
- File contains post-release verification commands (`uvx`, `pipx`)
</acceptance_criteria>
</task>

## Verification

<verification>
- [ ] `.github/workflows/release.yml` passes YAML syntax validation
- [ ] Workflow uses Trusted Publishing (no `password` or `PYPI_TOKEN` in workflow)
- [ ] Attestation step is present before publish step
- [ ] `.github/RELEASE.md` documents the one-time PyPI setup
</verification>

<must_haves>
- PKG-05: PyPI Trusted Publishing via GitHub Actions with attestations
</must_haves>
