# Agent Context — README / glama 6-tool refresh

> One-read working context for issue `[v0.3.0] docs — refresh public surfaces to the 6-tool surface`.

## 1. Roadmap excerpt

> **README + PyPI + glama.json refresh** (roadmap §4, v0.3.0): Reflect the 6-tool
> surface including `compare_versions`. **Decision 5.9:** adopt as a release-cycle
> discipline going forward — every release updates the public-facing tool table.
> Roadmap §3 notes the surface "still lists 5 in some surfaces."

## 2. Code / file touch-points

- **Tool order of truth:** `src/mcp_server_python_docs/server.py`, the `@mcp.tool`
  declarations in this order: `search_docs` (≈297), `get_docs` (≈318),
  `lookup_package_docs` (≈341), `list_versions` (≈358), `detect_python_version`
  (≈372), `compare_versions` (≈394).
- `README.md`:
  - `## Tools` section at **line ~178**: already a six-row table in the correct
    order. Verify, don't churn it.
  - **Stale badge near the top:** `MCP%20Registry-v0.1.4`. Current published
    registry/PyPI version is **0.2.1**. This is the concrete fix and the only
    allowed edit above the first install code block.
  - Hero section = everything **above the first install code block** (~line 125)
    — FORBIDDEN except the stale registry/version badge above.
- `glama.json`: `description` field (prose, no tool list today).
- `.github/RELEASE.md`: add one checklist line for decision 5.9.

## 3. Patterns to follow

- `tests/test_packaging.py` asserts packaging consistency — run it; if you add a
  surface, see whether a cheap assertion belongs there (optional, not required).
- Badge lines in `README.md` are markdown image links; match the existing style
  when updating the version.

## 4. Known pitfalls

- **False positive — do NOT "fix":** `.github/INTEGRATION-TEST.md:139` says
  "all five versions". That is the **five Python versions (3.10–3.14)**, not five
  tools. Leave it alone.
- **PyPI short description is forbidden.** It comes from `pyproject.toml`
  `[project].description`. If it's stale, comment — do not edit `pyproject.toml`.
  (The README *body* you edit *is* the PyPI long description on the next release,
  which is fine; only the hero is off-limits.)
- `README.md`, `.github/RELEASE.md`, and `glama.json` are all CODEOWNERS-owned.
  Your PR will request maintainer review by design — note it, don't fight it.
- Don't bump `server.json` / package versions here; that's release-managed.

## 5. Decision log

- Badge: pinned to `0.2.1` vs made version-agnostic — which and why:
- Surfaces audited and their state (README Tools / glama / server.json):
- RELEASE.md checklist line added:
- Anything left for the maintainer (e.g. stale pyproject short description):
