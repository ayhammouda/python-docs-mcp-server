# Contributing to python-docs-mcp-server

Start here for current contributor workflow. You should not need `.planning/`
to set up, test, or validate the repo.

## 1. Install tooling

Install `uv` if you do not already have it:

```bash
python -m pip install uv
```

Then bootstrap the repo:

```bash
uv sync --locked --dev
```

If `uv` is not on your `PATH` after installation, reopen the shell or use
`python -m uv ...` as a fallback.

## 2. Run the standard checks

Run the local checks (default pytest discovery includes `tests/benchmarks`):

```bash
uv run --locked ruff check src/ tests/ benchmarks/ ops/ .github/scripts/
uv run --locked pyright src/ benchmarks/
uv run --locked pytest --tb=short -q
```

CI and release run ordinary product tests with `pytest --ignore=tests/benchmarks`.
All benchmarks, including scripted development tests, run locally. Static
analysis of benchmark code and dependency security scanning remain in CI,
alongside product correctness, package/stdio smoke and full-index installation
E2E checks. See the [local benchmark workflow](#local-benchmark-workflow) below.

If you are working on retrieval behavior specifically, the curated regression
suite is:

```bash
uv run pytest tests/test_retrieval_regression.py -q
```

## 3. Build a local docs index

The server needs a local SQLite index before runtime validation:

```bash
uv run python-docs-mcp-server build-index --versions 3.10,3.11,3.12,3.13,3.14
uv run python-docs-mcp-server doctor
uv run python-docs-mcp-server validate-corpus
```

`build-index` downloads the symbol inventories, clones CPython docs sources,
runs the Sphinx JSON build, and writes the local cache database.

## 4. Validate MCP behavior

Use this validation order:

1. Run the automated checks.
2. Use MCP Inspector for fast local iteration.
3. Confirm client behavior in Claude Desktop and Cursor.

The detailed manual runbook lives in
[`.github/INTEGRATION-TEST.md`](.github/INTEGRATION-TEST.md).

## 5. Package and release checks

For a local package smoke check:

```bash
uv build
```

PyPI wheels and source distributions ship the server software only. To run
benchmark tooling and research, use a Git checkout; Git export archives omit
the assembled grading probe suite (`docs/benchmarks/grading/probes.yml`).

For release workflow details, PyPI trusted publishing setup, and the full
release checklist, see [`.github/RELEASE.md`](.github/RELEASE.md).

## Local benchmark workflow

Run from a clean checkout at the repository root. Each run gets a new directory.
Keep prior results. The ignored `.cache/benchmarks` directory holds the index.
For benchmark suite development, run `uv run --locked pytest tests/benchmarks -q` separately.

```bash
uv sync --locked --dev
BENCHMARK_RUN="$(date -u +%Y-%m-%dT%H%M%SZ)-$(git rev-parse --short=12 HEAD)"
BENCHMARK_OUTPUT="docs/benchmarks/results/$BENCHMARK_RUN"
BENCHMARK_CACHE="$PWD/.cache/benchmarks"
git switch -c "benchmarks/$BENCHMARK_RUN"
mkdir -p docs/benchmarks/results
mkdir "$BENCHMARK_OUTPUT"
git rev-parse HEAD > "$BENCHMARK_OUTPUT/run.txt"
uv run --locked python -c 'import platform; print(platform.platform(), platform.machine(), platform.python_implementation(), platform.python_version())' >> "$BENCHMARK_OUTPUT/run.txt"
XDG_CACHE_HOME="$BENCHMARK_CACHE" uv run --locked python-docs-mcp-server build-index --versions 3.10,3.11,3.12,3.13,3.14
uv run --locked python -m benchmarks.regression --index "$BENCHMARK_CACHE/mcp-python-docs/index.db" --output "$BENCHMARK_OUTPUT/retrieval.json"
```

The build downloads full documentation. To reuse a retained index, skip the build
and pass its path to `--index`. It must include Python 3.11–3.13 with content.
The offline evaluator validates the frozen corpus and compares against the baseline.
Review failures. Baseline changes require independent review.
Review the result and provenance before publishing. Exclude credentials, private raw
runs and transcripts. Open a PR after pushing the reviewed files:

```bash
git add -- "$BENCHMARK_OUTPUT/retrieval.json" "$BENCHMARK_OUTPUT/run.txt"
git diff --cached --check
git diff --cached
git commit -m "docs: publish local retrieval measurements $BENCHMARK_RUN"
git push -u origin "benchmarks/$BENCHMARK_RUN"
```

## Project conventions

- Keep the MCP tool surface small and read-only unless a change is explicitly
  justified.
- Prefer official docs and primary sources over community summaries when
  working on MCP/OpenAI/Python SDK behavior.
- Do not add repo-local custom skills by default.
- Do not treat `.planning/` as live repo truth. It is archival project history.
