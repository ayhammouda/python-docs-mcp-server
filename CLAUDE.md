# Claude Compatibility Notes

Use `AGENTS.md` as the canonical repository guidance.

This file is intentionally thin so Claude-compatible tools can find the repo
rules without carrying a second copy of project policy.

Key points:

- Start with `README.md` and `CONTRIBUTING.md` for current repo truth.
- Treat `.planning/` as archival context, not live instructions.
- Use official docs first for MCP, OpenAI/Codex, and Python SDK behavior.
- Do not add extra MCP servers or repo-local custom skills unless there is a
  clear, repeated project need.
- Use `.github/INTEGRATION-TEST.md` for manual MCP QA and `.github/RELEASE.md`
  for release-specific steps.
