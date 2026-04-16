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
