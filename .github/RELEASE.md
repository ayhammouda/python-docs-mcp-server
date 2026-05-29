# Release Process

## One-Time Setup: PyPI Trusted Publishing

Already configured for this project — these steps are kept for reference when forking or recovering the publisher state.

1. Go to https://pypi.org/manage/account/publishing/
2. Add a publisher (pending or active):
   - **PyPI project name**: `python-docs-mcp-server`
   - **Owner**: your GitHub username or org (this repo: `ayhammouda`)
   - **Repository**: `python-docs-mcp-server`
   - **Workflow name**: `release.yml`
   - **Environment name**: `pypi`
3. In the GitHub repo, go to Settings > Environments
4. Create an environment named `pypi`
5. (Optional) Add environment protection rules:
   - Required reviewers (recommended)
   - Deployment branches: only `main` tags

## Coverage notes

**Runtime coverage:** the release workflow builds and tests against Python 3.13 only. Python 3.12 is covered by the CI workflow (`ci.yml`), which runs a 2×2 matrix (3.12/3.13 × ubuntu/macos) on every push to `main`. Since tags are created from commits that have already passed CI, 3.12 compatibility is verified before the release workflow runs. This is an accepted trade-off to keep the release artifact pipeline simple (single Python version produces the wheel).

**Documentation coverage:** the full docs index target is Python documentation versions 3.10 through 3.14.

---

## Release Checklist

Replace `X.Y.Z` below with the version you are releasing (e.g. `0.1.7`). Complete each step in order; do not skip ahead. The release workflow at `.github/workflows/release.yml` runs four jobs in sequence on every `v*` tag push: `build` → `publish` (PyPI) → `publish-mcp-registry` → `github-release`.

### Pre-Release Verification

- [ ] All CI tests green on `main`: https://github.com/ayhammouda/python-docs-mcp-server/actions/workflows/ci.yml
- [ ] Local test suite passes:
  ```bash
  uv run pytest --tb=short -q
  uv run ruff check src/ tests/
  uv run pyright src/
  ```
- [ ] All four version-bearing files agree on `X.Y.Z` (the release workflow's `Verify tag matches package version` step enforces this; matching here saves a CI round-trip):
  ```bash
  grep '^version' pyproject.toml
  python3 -c 'import json; d = json.load(open("server.json")); print("server.json:", d["version"], "/ packages[0]:", d["packages"][0]["version"])'
  grep '^name = "python-docs-mcp-server"' -A1 uv.lock | head -2
  ```
- [ ] `server.json` validates against the live MCP Registry schema. MCP Registry enforces stricter limits than PyPI (e.g. `description ≤ 100 chars`); validating before the tag prevents a half-published release (PyPI succeeds while MCP Registry rejects, as happened on v0.1.5 → recovered in v0.1.6):
  ```bash
  curl -L "https://github.com/modelcontextprotocol/registry/releases/latest/download/mcp-publisher_$(uname -s | tr '[:upper:]' '[:lower:]')_$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/').tar.gz" | tar xz mcp-publisher
  ./mcp-publisher validate server.json
  ```
  Must report `✅ server.json is valid`. If validation fails, shorten `server.json` `description` on `main` before tagging (the `pyproject.toml` description is unbounded by this constraint and can stay longer).
- [ ] Integration tests from `.github/INTEGRATION-TEST.md` are complete and signed off
- [ ] Refresh README `## Tools`, `glama.json`, and registry/version badges to match the current tool surface.
- [ ] `Doctor` subcommand passes:
  ```bash
  uv run python-docs-mcp-server doctor
  ```
- [ ] `CHANGELOG.md` has a dated entry for `[X.Y.Z]`

### Tag and Release

- [ ] Create the annotated tag (replace `X.Y.Z` everywhere):
  ```bash
  git tag -a vX.Y.Z -m "Release vX.Y.Z

  <one-line summary of what shipped>

  See CHANGELOG.md for the full entry."
  ```
- [ ] Push the tag to trigger the release workflow:
  ```bash
  git push origin vX.Y.Z
  ```
- [ ] Watch the workflow run to completion:
  ```bash
  gh run watch $(gh run list --workflow=release.yml --limit 1 --json databaseId -q '.[0].databaseId') --exit-status
  ```
- [ ] All four jobs green: `build` → `publish` → `publish-mcp-registry` → `github-release`

### Post-Release Verification

- [ ] PyPI listing exists with attestation badge: `https://pypi.org/project/python-docs-mcp-server/X.Y.Z/`
- [ ] Fresh install test from a clean shell (cache-busted so the new version actually resolves):
  ```bash
  uv cache clean python-docs-mcp-server 2>/dev/null || true
  uvx --refresh python-docs-mcp-server@X.Y.Z --version
  # Expected: python-docs-mcp-server X.Y.Z
  ```
- [ ] Full install flow:
  ```bash
  uvx python-docs-mcp-server build-index --versions 3.10,3.11,3.12,3.13,3.14
  uvx python-docs-mcp-server doctor
  ```
  Both must complete successfully.
- [ ] MCP Registry shows `X.Y.Z` as the `isLatest` entry:
  ```bash
  curl -s 'https://registry.modelcontextprotocol.io/v0.1/servers?search=python-docs-mcp-server' | \
    python3 -c "import json,sys; d=json.load(sys.stdin); print(next((e['server']['version'] for e in d['servers'] if e['_meta']['io.modelcontextprotocol.registry/official']['isLatest']), 'no latest'))"
  ```
- [ ] GitHub Release exists at `https://github.com/ayhammouda/python-docs-mcp-server/releases/tag/vX.Y.Z` with wheel + sdist attached and auto-generated notes
- [ ] **Slow E2E workflow** passes (manually triggered after publish):
  - Run GitHub Actions workflow `Slow E2E` (`.github/workflows/e2e.yml`)
  - Python 3.13 and 3.14 jobs both pass
  - Each job installs the built wheel and runs `build-index --versions 3.10,3.11,3.12,3.13,3.14`, `doctor`, and `validate-corpus`
- [ ] Claude Desktop manual smoke test:
  Configure `mcpServers` with `uvx python-docs-mcp-server` and verify a known query (e.g. "what is asyncio.TaskGroup") returns a correct hit.

### Release Complete

- [ ] All four `release.yml` jobs green for `vX.Y.Z`
- [ ] PyPI page shows `X.Y.Z` with the "Provenance" attestation badge
- [ ] MCP Registry `isLatest` entry points at `X.Y.Z`
- [ ] GitHub Release `vX.Y.Z` exists with both dist artifacts attached
- [ ] `CHANGELOG.md` `[X.Y.Z]` entry is committed on `main`
