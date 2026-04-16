---
phase: "08"
title: "Phase 8 Code Review: Ship"
status: findings
reviewer: gsd-code-reviewer
depth: standard
date: 2026-04-16
files_reviewed: 3
findings:
  critical: 1
  warning: 3
  info: 2
  total: 6
---

# Phase 8 Code Review

**Scope:** 3 files from Phase 8 (Ship)
**Depth:** standard
**Requirements:** SHIP-01, SHIP-02, SHIP-06, PKG-05, PKG-07

## Files Reviewed

1. `.github/workflows/release.yml`
2. `.github/RELEASE.md`
3. `.github/INTEGRATION-TEST.md`

---

## Findings

### CR-01: Release workflow missing concurrency control — duplicate publishes possible [CRITICAL]

**File:** `.github/workflows/release.yml`
**Lines:** 1-6

The release workflow has no `concurrency` block. If a tag is pushed, then quickly deleted and re-pushed (common during a botched release), two workflow runs execute in parallel. Both could attempt to publish the same version to PyPI, causing one to fail with a 409 Conflict -- or worse, a partial publish with inconsistent GitHub Release artifacts.

**Fix:** Add a concurrency group that cancels in-progress runs for the same tag:
```yaml
concurrency:
  group: release-${{ github.ref_name }}
  cancel-in-progress: true
```

---

### WR-01: No job timeout-minutes on any release job [WARNING]

**File:** `.github/workflows/release.yml`
**Lines:** 9, 59, 84

None of the three jobs (`build`, `publish`, `github-release`) set `timeout-minutes`. GitHub's default is 360 minutes (6 hours). A hung `sphinx-build`, test suite, or PyPI upload would burn CI minutes silently for hours. The CI workflow (`ci.yml`) has the same gap but that is out of scope for this phase.

**Fix:** Add `timeout-minutes: 15` to the `build` job and `timeout-minutes: 10` to `publish` and `github-release`. These are generous ceilings -- actual runs should complete in under 5 minutes.

---

### WR-02: Build job tests only on Python 3.13 — skips 3.12 matrix coverage [WARNING]

**File:** `.github/workflows/release.yml`
**Lines:** 19-20

The build job hardcodes `uv python install 3.13` and runs tests only against Python 3.13. The project targets both 3.12 and 3.13 (per `pyproject.toml` and the `ci.yml` matrix). If a release introduces a 3.12-only regression, the release workflow will not catch it.

This is acceptable because the CI workflow already runs a 2x2 matrix (3.12/3.13 x ubuntu/macos) on every push to `main`, so the tag is created from a commit that has already passed 3.12 tests. However, the release build is a different artifact build -- if a dependency resolves differently at tag time, the 3.12 gap could matter.

**Recommendation:** Either note this as an accepted risk in RELEASE.md, or add a quick 3.12 compatibility check to the build job.

---

### WR-03: Release workflow does not verify tag matches pyproject.toml version [WARNING]

**File:** `.github/workflows/release.yml`
**Lines:** 4-6, 34-36

The workflow triggers on `push: tags: ['v*']` but never validates that the tag name matches the version in `pyproject.toml`. A tag `v0.2.0` pushed against a commit where `pyproject.toml` says `version = "0.1.0"` would publish `0.1.0` to PyPI under a misleading `v0.2.0` GitHub Release.

**Fix:** Add a version-consistency check step to the build job:
```yaml
- name: Verify tag matches package version
  run: |
    TAG_VERSION="${GITHUB_REF_NAME#v}"
    PKG_VERSION=$(uv run python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
    if [ "$TAG_VERSION" != "$PKG_VERSION" ]; then
      echo "Tag $GITHUB_REF_NAME does not match pyproject.toml version $PKG_VERSION"
      exit 1
    fi
```

---

### IR-01: RELEASE.md uses placeholder `<owner>` in URLs [INFO]

**File:** `.github/RELEASE.md`
**Lines:** 42, 67, 109

Several URLs contain `<owner>` placeholder text (e.g., `https://github.com/<owner>/python-docs-mcp-server/actions/...`). These should be replaced with the actual GitHub owner once the repository is public, or documented more prominently as requiring substitution.

Not a functional issue -- the document is a human-consumed checklist -- but could cause confusion during a time-pressured release.

---

### IR-02: INTEGRATION-TEST.md Claude Desktop config shows macOS path only [INFO]

**File:** `.github/INTEGRATION-TEST.md`
**Lines:** 16-17

The setup instructions reference `~/Library/Application Support/Claude/claude_desktop_config.json` which is macOS-specific. The README (created in Phase 7, SHIP-03) documents all three platform paths (macOS, Linux, Windows). The integration test should either reference the README for path guidance or list the Linux path as well, since CI runs on Ubuntu.

Not blocking -- SHIP-05 explicitly states Windows is best-effort and the tester can look up the path -- but the asymmetry between README and integration test could cause confusion.

---

## Summary

| Severity | Count | IDs |
|----------|-------|-----|
| Critical | 1 | CR-01 |
| Warning | 3 | WR-01, WR-02, WR-03 |
| Info | 2 | IR-01, IR-02 |
| **Total** | **6** | |

### Requirement Coverage

| Requirement | Covered By | Assessment |
|-------------|-----------|------------|
| PKG-05 | `release.yml` publish job with `id-token: write` + `pypa/gh-action-pypi-publish@release/v1` + `actions/attest-build-provenance@v2` | Correct implementation of Trusted Publishing with attestations |
| PKG-07 | `release.yml` builds and publishes wheel; `RELEASE.md` documents the tag+push flow | Correct -- workflow will publish `0.1.0` when tag is pushed |
| SHIP-01 | `INTEGRATION-TEST.md` Test 1 with Claude Desktop config and asyncio.TaskGroup query | Complete checklist with setup, test steps, expected results, sign-off |
| SHIP-02 | `INTEGRATION-TEST.md` Test 2 with Cursor setup and asyncio.TaskGroup query | Complete checklist with setup, test steps, expected results, sign-off |
| SHIP-06 | `RELEASE.md` v0.1.0 Release Checklist post-release section + `INTEGRATION-TEST.md` Test 3 | Complete end-to-end verification flow |

All five Phase 8 requirements have adequate coverage in the deliverables.
