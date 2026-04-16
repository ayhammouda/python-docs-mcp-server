---
plan: 08a
title: "GitHub Actions Release Workflow with PyPI Trusted Publishing"
status: complete
started: 2026-04-16
completed: 2026-04-16
---

## Summary

Created a GitHub Actions release workflow (`release.yml`) triggered on `v*` tag push with three jobs: build (lint + typecheck + test + wheel build + content verification), publish (PyPI Trusted Publishing via OIDC with attestations), and github-release (auto-generated release notes). Also created `RELEASE.md` documenting the one-time PyPI Trusted Publishing setup and release creation steps.

## Self-Check: PASSED

- [x] `.github/workflows/release.yml` exists and passes YAML validation
- [x] Workflow triggers on `push: tags: ['v*']`
- [x] Build job runs linter, type checker, tests, `uv build`, and wheel content verification
- [x] Publish job has `permissions: id-token: write` (OIDC Trusted Publishing)
- [x] Publish job has `environment: pypi`
- [x] Publish job uses `pypa/gh-action-pypi-publish@release/v1` without `password` key
- [x] Publish job uses `actions/attest-build-provenance@v2` for attestations
- [x] GitHub Release job uses `softprops/action-gh-release@v2`
- [x] `.github/RELEASE.md` documents PyPI setup and release steps

## Key Files

### Created
- `.github/workflows/release.yml` -- Release workflow with 3 jobs (build, publish, github-release)
- `.github/RELEASE.md` -- One-time setup and release creation instructions

## Deviations

None.
