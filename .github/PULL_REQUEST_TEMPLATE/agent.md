<!--
Autonomous-agent PR template. Enforces AGENT-EXECUTION-PIPELINE.md §6.
PR title MUST match the issue title verbatim. Never self-merge.
-->

Closes #<issue-number>

## Acceptance criteria
<!-- Copy every criterion from the issue. Check the box only when satisfied,
     and add one line of evidence (command + observed result) per item. -->
- [ ] <criterion 1> — <evidence>
- [ ] <criterion 2> — <evidence>

## Validation gate output
<!-- Paste the tail of each gate command. All must be green before opening this PR. -->
```text
$ uv run ruff check src/ tests/
$ uv run pyright src/
$ uv run pytest --tb=short -q
$ uv run python-docs-mcp-server doctor
```
<!-- Plus any change-type-specific gates from pipeline §5 (stdio smoke,
     validate-corpus, uv lock --check) that applied to this change. -->

## CodeRabbit review
<!-- After CodeRabbit comments, summarize findings as:
     - Blocking: <items or None>
     - Follow-up: <items or None>
     - False positive: <items or None>
     If CodeRabbit has not run yet, write "Pending." Do not mark findings green
     by silence. -->
Pending.

## Why this approach
<!-- One paragraph max. If the issue fully prescribed the approach, say so.
     If you cite a design choice NOT in the issue, that is a §7 trigger. -->

## Why this triggered supervisor review
<!-- List any pipeline §7 triggers and explain each. If none, write "None."
     If any fired: this PR is opened for supervisor review only; do not merge it yourself,
     and ensure the `supervisor-review` label is applied. -->
None.
