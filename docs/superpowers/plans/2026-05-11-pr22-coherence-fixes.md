# PR #22 Coherence Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the seven coherence / consistency gaps flagged in the deep code review of PR #22 (`fix/autonomous-launch-pack`) before launch, so the post-PyPI cleanup pass and back-compat experience are mechanically safe.

**Architecture:** Most fixes are isolated edits to a single file each (README, pyproject, two test files, one launch draft). The one cross-cutting fix is the PRE-PYPI marker scope in README, which is restructured so a single `<!-- PRE-PYPI -->`…`<!-- /PRE-PYPI -->` block encloses every transient section (heading + lead-in sentence + code), making the committed cleanup regex a complete safety net.

**Tech Stack:** Python 3.12+, pytest, ruff, `uv build`, click, FastMCP. No new dependencies.

---

## Scope check

Seven fixes from the review map to the following files:

| Fix | File(s) |
|-----|---------|
| **C1** + **I1** | `README.md` (marker scope) + `.github/RELEASE.md` (anchor expected regex output to the new structure) |
| **I4** | `pyproject.toml` (add 3.14 classifier) |
| **I5** | `docs/launch/show-hn.md` (reconcile build-index versions) |
| **I6** | `README.md` (Troubleshooting subsection for legacy CLI users) |
| **I3** | `tests/test_packaging.py` (harden source-tree fallback test) |
| **M1** | `tests/test_services.py` (assert FastMCP server name) |
| **M2** | `tests/test_packaging.py` (structurally parse entry_points.txt) |

I3 and M2 both touch `tests/test_packaging.py` and are merged into a single task to keep one atomic commit per file-touch. C1 and I1 are merged because they share a single structural change to the README markers. Total: 7 tasks across 5 files. Each task produces a green test suite and a commit.

---

## File structure

**Files to be modified (no new files):**

- `README.md` — restructure `<!-- PRE-PYPI -->` markers to enclose full transient sections; add Troubleshooting subsection on legacy CLI back-compat
- `.github/RELEASE.md` — anchor the post-PyPI cleanup checklist to the new marker structure (the regex itself can stay as-is once C1+I1 are fixed because the surrounding prose now lives inside marker blocks)
- `pyproject.toml` — add `Programming Language :: Python :: 3.14` classifier
- `docs/launch/show-hn.md` — change post-PyPI `build-index --versions` to the full 5-version list to match README
- `tests/test_packaging.py` — (I3) force the fallback path via monkey-patched `importlib.metadata.version` in the source-tree test; (M2) replace substring assertions on `entry_points.txt` with `configparser` parsing
- `tests/test_services.py` — (M1) add a one-liner asserting `create_server()` reports identity `python-docs-mcp-server`

**No file is created.** No package layout change. No dependency change.

---

## Task 1: Restructure PRE-PYPI markers to enclose full transient sections (C1 + I1)

**Files:**
- Modify: `README.md` — five disjoint regions:
  - Block A (30-second demo): lines 74-80
  - Block B (Install section): lines 84-100
  - Block C (First run): lines 115-123
  - Block D (Claude Desktop / Cursor / Codex configs): lines 147-164, 184-201, 218-226
  - Block E (Diagnostics): lines 314-324, 332-343
- Modify: `.github/RELEASE.md` (cleanup checklist around line 165; the verification regex around line 170 stays as-is once markers are widened)
- Test: ad-hoc shell command — `rg` execution against the marker-stripped README

**Why this matters:** The current `<!-- PRE-PYPI -->` markers only wrap code fences, leaving surrounding lead-in sentences ("Local source smoke test until the PyPI package is published:") and `### Before/After PyPI publishing` headings *outside* any marker. The committed cleanup regex at `.github/RELEASE.md:170` is also case-sensitive and misses line 74's lowercase phrasing. Combined, this means a maintainer who runs the regex post-launch will see zero hits and ship while transient prose remains on the published README.

Fix: widen each `<!-- PRE-PYPI -->` block to enclose the heading + lead-in sentence + the GitHub-source code fence. Then on cleanup day, a maintainer can sed/perl-delete every `<!-- PRE-PYPI -->`…`<!-- /PRE-PYPI -->` block and the README will end up clean in one pass.

- [ ] **Step 1: Write the failing verification command**

Create a file `scripts/verify_pre_pypi_cleanup.sh` (or just record the command in your terminal scratch — no need to commit the script). Run it against current README:

```bash
# 1) Strip everything between PRE-PYPI markers
perl -0777 -pe 's/<!-- PRE-PYPI:.*?<!-- \/PRE-PYPI -->//gs' README.md > /tmp/README.stripped.md

# 2) Confirm no transient prose survives outside markers
rg -in 'PRE[-]PYPI|Before PyPI publishing|Until.+PyPI|After PyPI publishing|git\+https://github.com/.*python-docs-mcp-server' /tmp/README.stripped.md
```

Expected: at least lines for `Before PyPI publishing`, `Until the first PyPI release is published`, `Local source smoke test until the PyPI package is published`, `After PyPI publishing, use:` show up — proving the current markers are too narrow.

- [ ] **Step 2: Widen marker scopes in README.md**

Apply the following edits (preserve exact existing text; only the marker placement changes).

**Note on fence rendering in this plan:** the snippets below contain code fences inside Markdown code fences. To avoid breaking this plan's own outer fences, every inner triple-backtick is rendered as space-padded ` ` ` (` ` `bash, ` ` `json, ` ` `toml, etc.). **When writing to README.md, translate each space-padded ` ` ` back to a real triple-backtick (\`\`\`).** Verify fence parity per Step 6 / Task 7.

**Block A — 30-second demo (lines 74-80):**

Current:
```markdown
Local source smoke test until the PyPI package is published:

<!-- PRE-PYPI: replace this temporary GitHub source command after the first PyPI publish -->
` ` ` bash
uvx --from git+https://github.com/ayhammouda/python-docs-mcp-server.git python-docs-mcp-server --version
` ` `
<!-- /PRE-PYPI -->
```

Replace with:
```markdown
<!-- PRE-PYPI: replace this temporary GitHub-source smoke test after the first PyPI publish -->
Local source smoke test until the PyPI package is published:

` ` ` bash
uvx --from git+https://github.com/ayhammouda/python-docs-mcp-server.git python-docs-mcp-server --version
` ` `
<!-- /PRE-PYPI -->
```

**Block B — Install section (lines 84-100):**

Current structure:
```markdown
### Before PyPI publishing (install from GitHub source)

Until the first PyPI release is published, run from GitHub:

<!-- PRE-PYPI: ... -->
` ` ` bash
uvx --from git+...
` ` `
<!-- /PRE-PYPI -->

### After PyPI publishing

Run directly with `uvx`:
` ` ` bash
uvx python-docs-mcp-server --version
` ` `
```

Replace with:
```markdown
<!-- PRE-PYPI: remove this entire "Before PyPI publishing" block (heading + prose + code) after the first PyPI publish -->
### Before PyPI publishing (install from GitHub source)

Until the first PyPI release is published, run from GitHub:

` ` ` bash
uvx --from git+https://github.com/ayhammouda/python-docs-mcp-server.git python-docs-mcp-server --version
` ` `
<!-- /PRE-PYPI -->

<!-- PRE-PYPI: after the first PyPI publish, drop this "After PyPI publishing" heading so the section reads simply as "## Install" -->
### After PyPI publishing
<!-- /PRE-PYPI -->

Run directly with `uvx`:
` ` ` bash
uvx python-docs-mcp-server --version
` ` `
```

**Block C — First run (lines 115-123):**

Current:
```markdown
Build the local documentation index:

<!-- PRE-PYPI: replace this temporary GitHub source command after the first PyPI publish -->
` ` ` bash
uvx --from git+https://github.com/ayhammouda/python-docs-mcp-server.git python-docs-mcp-server build-index --versions 3.10,3.11,3.12,3.13,3.14
` ` `
<!-- /PRE-PYPI -->

After PyPI publishing, `uvx python-docs-mcp-server build-index ...` is enough.

If you installed the package persistently, you can drop the `uvx` prefix:
```

Replace with:
```markdown
Build the local documentation index:

<!-- PRE-PYPI: remove the GitHub-source build-index command and the "After PyPI publishing" lead-in after the first PyPI publish; the post-PyPI code fence below survives -->
` ` ` bash
uvx --from git+https://github.com/ayhammouda/python-docs-mcp-server.git python-docs-mcp-server build-index --versions 3.10,3.11,3.12,3.13,3.14
` ` `

After PyPI publishing, `uvx python-docs-mcp-server build-index ...` is enough.
<!-- /PRE-PYPI -->

` ` ` bash
uvx python-docs-mcp-server build-index --versions 3.10,3.11,3.12,3.13,3.14
` ` `

If you installed the package persistently, you can drop the `uvx` prefix:
```

Rationale: post-cleanup, the section becomes "Build the local documentation index: → `uvx python-docs-mcp-server build-index ...` (full 5 versions) → If you installed persistently, drop uvx prefix". The canonical post-PyPI uvx command is preserved as the visible example. The transient GitHub-source command and its "After PyPI publishing" qualifier disappear together.

**Block D — Claude Desktop, Cursor, Codex configs (lines 147-201, 215-226):** wrap each `<!-- PRE-PYPI -->` block + the trailing `After PyPI publishing, use:` lead-in inside one marker pair, so cleanup removes the GitHub-source config block AND the "After PyPI publishing, use:" lead-in, leaving only the post-PyPI config code fence.

This applies uniformly to:
- Claude Desktop JSON config (lines 147-164)
- Cursor JSON config (lines 184-201)
- Codex **TOML** config (lines 218-226) — same wrap pattern even though the fence is ` ```toml ` not ` ```json `

Concrete pattern for each (Claude Desktop shown; Cursor and Codex are identical in structure):

Current:
```markdown
<!-- PRE-PYPI: ... -->
` ` ` json
[github-source config block]
` ` `
<!-- /PRE-PYPI -->

After PyPI publishing, use:

` ` ` json
[post-PyPI config block]
` ` `
```

Replace with:
```markdown
<!-- PRE-PYPI: remove the GitHub-source config and the "After PyPI publishing, use:" lead-in after the first PyPI publish; the post-PyPI config fence below survives -->
` ` ` json
[github-source config block]
` ` `

After PyPI publishing, use:
<!-- /PRE-PYPI -->

` ` ` json
[post-PyPI config block]
` ` `
```

After cleanup, the section reads "Add this to your Claude Desktop configuration file: [paths] → [post-PyPI JSON code fence]" with no transient verbiage. The Codex TOML variant follows the same pattern verbatim with TOML fences.

**Block E — Diagnostics (lines 314-324, 332-343):** wrap each "Before PyPI publishing, run `doctor` from the GitHub source package:" lead-in + its pre-PyPI command + the `After PyPI publishing:` lead-in inside one marker pair. The post-PyPI command (e.g., `uvx python-docs-mcp-server doctor`) stays outside the marker.

- [ ] **Step 3: Re-run the verification command**

```bash
perl -0777 -pe 's/<!-- PRE-PYPI:.*?<!-- \/PRE-PYPI -->//gs' README.md > /tmp/README.stripped.md
rg -in 'PRE[-]PYPI|Before PyPI publishing|Until.+PyPI|After PyPI publishing|git\+https://github.com/.*python-docs-mcp-server' /tmp/README.stripped.md
```

Expected: **zero output**. If anything matches, narrow the surviving fragment by widening the corresponding marker.

- [ ] **Step 4: Confirm no badge / verification comment was caught**

The MCP Registry verification comment at `README.md:3` (`<!-- mcp-name: io.github.ayhammouda/python-docs-mcp-server -->`) and the CI/license/PyPI badge lines must survive cleanup. Verify:

```bash
rg -n 'mcp-name|badge.svg|License: MIT|github.com/ayhammouda/python-docs-mcp-server/actions' /tmp/README.stripped.md
```

Expected: all four lines present, unaltered.

- [ ] **Step 5: Update RELEASE.md cleanup checklist to reflect the new marker shape**

In `.github/RELEASE.md` around line 165, update the cleanup instruction text. Change:

```markdown
- [ ] Remove every temporary PyPI pre-release block from `README.md`:
  - Delete each `<!-- PRE-PYPI: ... -->` through `<!-- /PRE-PYPI -->` region,
    including the marker comments and GitHub-source replacement commands
  - Remove or rewrite stale "Before PyPI publishing" headings, "Until the first
    PyPI release is published" text, and "After PyPI publishing" qualifiers
```

To:

```markdown
- [ ] Remove every temporary PyPI pre-release block from `README.md`:
  - Mechanical pass: delete every region from `<!-- PRE-PYPI:` to `<!-- /PRE-PYPI -->` (inclusive). Each block now encloses its surrounding heading + lead-in sentence + code, so a single pass produces a clean README.
  - Reference command:
    `perl -0777 -i -pe 's/<!-- PRE-PYPI:.*?<!-- \/PRE-PYPI -->\n*//gs' README.md`
```

Then leave the existing `rg` verification command as the safety net.

- [ ] **Step 6: Commit**

```bash
git add README.md .github/RELEASE.md
git commit -m "$(cat <<'EOF'
docs(readme): widen PRE-PYPI markers to enclose full transient sections

Fixes C1+I1 from the PR #22 coherence review: previously the
<!-- PRE-PYPI --> markers wrapped only code fences, leaving
surrounding "Before/After PyPI publishing" headings and lead-in
sentences (including line 74's lowercase "until the PyPI package
is published") outside any marker. The committed cleanup regex
missed line 74, so post-launch a mechanical block-delete would
have left transient prose on the published README.

Each PRE-PYPI block now encloses its full transient region. The
RELEASE.md cleanup checklist references a perl one-liner for a
single-pass mechanical cleanup.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Add Python 3.14 to pyproject.toml classifiers (I4)

**Files:**
- Modify: `pyproject.toml:16-23`
- Test: `tests/test_packaging.py` (new test asserting classifier presence)

**Why this matters:** Every doc + the Slow E2E workflow advertises support for indexed Python documentation versions 3.10-3.14. The runtime is 3.12+. The classifier list currently stops at 3.13, so PyPI's "Programming Language" filter won't surface this project for 3.14 users. The asymmetry is cosmetic but visible.

(Note: we do NOT add 3.10 / 3.11 classifiers because `requires-python = ">=3.12"` — those runtimes can't run the server. The 3.10-3.14 range is for *indexed documentation versions*, not runtime support.)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_packaging.py` inside the `TestPyprojectDeps` class:

```python
    def test_classifiers_advertise_supported_python_versions(self):
        """Classifiers must list every Python runtime the project supports."""
        pyproject = (PROJECT_ROOT / "pyproject.toml").read_text()
        for minor in (12, 13, 14):
            classifier = f"Programming Language :: Python :: 3.{minor}"
            assert classifier in pyproject, f"Missing classifier: {classifier}"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_packaging.py::TestPyprojectDeps::test_classifiers_advertise_supported_python_versions -v
```

Expected: FAIL — `Missing classifier: Programming Language :: Python :: 3.14`.

- [ ] **Step 3: Add the classifier to `pyproject.toml`**

Insert at line 21 (after the `3.13` classifier and before `Topic :: Documentation`):

```toml
    "Programming Language :: Python :: 3.14",
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_packaging.py::TestPyprojectDeps::test_classifiers_advertise_supported_python_versions -v
```

Expected: PASS. Then run the full suite to confirm no regression:

```bash
uv run pytest -q
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml tests/test_packaging.py
git commit -m "$(cat <<'EOF'
build: advertise Python 3.14 runtime in PyPI classifiers

Fixes I4 from the PR #22 coherence review. Slow E2E runs on
Python 3.13 and 3.14, and all docs say the project runs on
Python 3.12+. The classifier list previously stopped at 3.13,
which hid the project from PyPI users filtering by 3.14.

Adds a classifier test to prevent future drift.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Reconcile Show HN `build-index` versions (I5)

**Files:**
- Modify: `docs/launch/show-hn.md:76`

**Why this matters:** The post-PyPI Show HN draft tells readers to run `uvx python-docs-mcp-server build-index --versions 3.12,3.13` while every other doc (README, AGENTS, CONTRIBUTING, INTEGRATION-TEST, RELEASE, reddit-posts) instructs the full `3.10,3.11,3.12,3.13,3.14` list. A reader who follows the HN post then visits the README will find the index incomplete and have to rebuild. Pick one. The right one is the full list to match the rest of the launch pack.

(Trade-off: the full build takes longer. We could keep `3.12,3.13` and annotate it as a quick-demo build, but consistency wins for a launch post.)

- [ ] **Step 1: Edit `docs/launch/show-hn.md` line 76**

Change:
```bash
uvx python-docs-mcp-server build-index --versions 3.12,3.13
```

To:
```bash
uvx python-docs-mcp-server build-index --versions 3.10,3.11,3.12,3.13,3.14
```

- [ ] **Step 2: Verify launch-pack and user-facing docs match**

```bash
rg -n 'build-index --versions' docs/launch/ README.md CONTRIBUTING.md .github/
```

Expected: every result lists `3.10,3.11,3.12,3.13,3.14`. No outliers.

Note: `AGENTS.md:42` deliberately uses `--versions 3.12,3.13` as a contributor quick-build for fast inner-loop iteration (it sits under the "Build and inspect a local docs index" instructions, not in the launch pack). It is **excluded** from this verification on purpose. If you need to confirm: open `AGENTS.md` around line 38-44 — the surrounding text is a contributor workflow, not a user-facing launch instruction.

Out-of-scope reconciliation: if Task 3 grows to also normalize `AGENTS.md`, that would conflict with its role as a fast contributor doc. Keep this task narrowly scoped to user-facing launch/install docs.

- [ ] **Step 3: Commit**

```bash
git add docs/launch/show-hn.md
git commit -m "$(cat <<'EOF'
docs(launch): reconcile Show HN build-index versions with README

Fixes I5 from the PR #22 coherence review. The Show HN draft
previously instructed building only 3.12,3.13 while every other
doc instructed the full 3.10,3.11,3.12,3.13,3.14 list. A reader
who followed the HN post and then the README would find the
index incomplete.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Add Troubleshooting subsection for legacy CLI users (I6)

**Files:**
- Modify: `README.md` — add a subsection under `## Troubleshooting`

**Why this matters:** Once the new PyPI project goes live, a user whose MCP config still says `uvx mcp-server-python-docs` will hit a PyPI lookup error because the new project name is `python-docs-mcp-server`. The legacy CLI alias works only *after* the new package is installed by its real name; it does NOT survive `uvx`-style "install-and-run by project name" lookups. This needs an explicit note.

- [ ] **Step 1: Read current Troubleshooting structure**

```bash
sed -n '350,410p' README.md
```

Note where the `### uvx cache stale` subsection starts (~ line 384). Insert the new subsection immediately before it.

- [ ] **Step 2: Add the subsection**

Insert (immediately before `### uvx cache stale`):

```markdown
### Migrating from the pre-rename CLI

Earlier development snapshots of this project used the PyPI name
`mcp-server-python-docs`. The published PyPI project is
`python-docs-mcp-server`. If your MCP client config still references
the old name via `uvx`, you will see a `Package not found` error,
because `uvx` resolves projects by PyPI name.

Update your config's `args` from:

` ` ` json
"args": ["mcp-server-python-docs"]
` ` `

to:

` ` ` json
"args": ["python-docs-mcp-server"]
` ` `

The wheel still installs a legacy `mcp-server-python-docs` console
script for users who already have the package installed and invoke
the binary by name on `$PATH`. That script is an alias and will be
removed in a future release.
```

(Use real triple-backticks in the file; the spacing here is just to avoid breaking this plan's own fences.)

- [ ] **Step 3: Verify no Markdown breakage**

```bash
uv run python -c "import pathlib; print(pathlib.Path('README.md').read_text().count('\`\`\`'))"
```

Expected: even number (each code fence opens and closes).

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
docs(readme): document migration from pre-rename CLI

Fixes I6 from the PR #22 coherence review. After the new PyPI
project goes live, users whose MCP client config still references
`uvx mcp-server-python-docs` will get a PyPI lookup error. The
legacy console-script alias only helps users who already have the
package installed and invoke the binary by name on $PATH.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Harden `tests/test_packaging.py` — force source-tree fallback + structurally parse entry_points.txt (I3 + M2)

**Files:**
- Modify: `tests/test_packaging.py` (two unrelated tests touched together to keep one commit per file)

**Why this matters:**

- **I3:** The current `test_source_tree_import_without_installed_metadata` uses `python -S` to disable `site.py`. The reviewer empirically confirmed that `-S` does NOT clear an editable install's `.dist-info` from `sys.path` discovery — the test currently passes because the metadata version coincidentally equals the pyproject version. If a developer bumps the pyproject version without reinstalling the editable distribution, the test will silently pass while exercising the *installed-metadata* path, not the fallback. We need to force the fallback.
- **M2:** `test_wheel_has_entry_point` uses `assert DIST_NAME in content` against the raw `entry_points.txt` text. Three substrings can all appear without being bound to the right callable in the right section. Parse the file structurally via `configparser`.

- [ ] **Step 1: Write the hardened fallback test**

Replace the body of `test_source_tree_import_without_installed_metadata` (currently `tests/test_packaging.py:122-144`) with:

```python
    def test_source_tree_import_without_installed_metadata(self, tmp_path: Path):
        """Source-tree import falls back to pyproject.toml when metadata is absent.

        Forces the fallback path by monkey-patching importlib.metadata.version
        to raise PackageNotFoundError, since `-S` alone does not reliably
        suppress editable-install dist-info discovery.
        """
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
        prelude = (
            "import importlib.metadata as m\n"
            "def _raise(_):\n"
            "    raise m.PackageNotFoundError\n"
            "m.version = _raise\n"
        )
        result = subprocess.run(
            [
                sys.executable,
                "-S",
                "-c",
                prelude + "import mcp_server_python_docs; print(mcp_server_python_docs.__version__)",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=str(tmp_path),
        )
        assert result.returncode == 0, (
            f"Source-tree import failed.\n"
            f"stdout: {result.stdout!r}\n"
            f"stderr: {result.stderr!r}"
        )
        # The fallback reads pyproject.toml directly; assert it equals
        # whatever pyproject currently declares (decoupled from installed metadata).
        import tomllib
        pyproject_version = tomllib.loads(
            (PROJECT_ROOT / "pyproject.toml").read_text()
        )["project"]["version"]
        assert result.stdout.strip() == pyproject_version
```

- [ ] **Step 2: Write the hardened entry-point test**

Replace the body of `test_wheel_has_entry_point` (currently `tests/test_packaging.py:79-90`) with:

```python
    def test_wheel_has_entry_point(self, built_wheel):
        """PKG-01: Wheel metadata declares both console scripts, structurally."""
        import configparser
        import io

        with zipfile.ZipFile(built_wheel) as zf:
            entry_point_files = [
                n for n in zf.namelist() if n.endswith("entry_points.txt")
            ]
            assert len(entry_point_files) == 1
            content = zf.read(entry_point_files[0]).decode()

        parser = configparser.ConfigParser()
        parser.read_file(io.StringIO(content))
        assert parser.has_section("console_scripts"), (
            f"entry_points.txt missing [console_scripts]:\n{content}"
        )
        scripts = dict(parser.items("console_scripts"))
        target = "mcp_server_python_docs.__main__:main"
        assert scripts.get(DIST_NAME) == target, (
            f"Expected {DIST_NAME} -> {target}, got {scripts!r}"
        )
        assert scripts.get(LEGACY_CLI_NAME) == target, (
            f"Expected {LEGACY_CLI_NAME} -> {target}, got {scripts!r}"
        )
```

- [ ] **Step 3: Run both updated tests, expect green**

```bash
uv run pytest tests/test_packaging.py::TestVersionFlag::test_source_tree_import_without_installed_metadata tests/test_packaging.py::TestWheelContent::test_wheel_has_entry_point -v
```

Expected: PASS for both.

- [ ] **Step 4: Verify the hardened fallback test actually fails when the fallback is broken**

This step has three sub-steps that MUST be completed in order. The temporary edit MUST be reverted before continuing.

**Step 4a — break:** In `src/mcp_server_python_docs/__init__.py`, replace the line:
```python
            return tomllib.load(fh)["project"]["version"]
```
with:
```python
            return "BOGUS"  # TEMPORARY — revert before commit
```

**Step 4b — assert failure:**
```bash
uv run pytest tests/test_packaging.py::TestVersionFlag::test_source_tree_import_without_installed_metadata -v
```
Expected: FAIL — assertion mismatch `'BOGUS' != '0.1.1'`. If the test PASSES, the fallback is not being exercised and you must re-examine the monkey-patch from Step 1 before proceeding.

**Step 4c — revert and verify clean tree:** Restore `__init__.py` exactly:
```python
            return tomllib.load(fh)["project"]["version"]
```

Then confirm the revert was complete:
```bash
git diff src/mcp_server_python_docs/__init__.py
```
Expected: **empty output**. If the diff shows anything, fix it before continuing — the test commit MUST NOT include any change to `src/`.

Re-run the test to confirm green:
```bash
uv run pytest tests/test_packaging.py::TestVersionFlag::test_source_tree_import_without_installed_metadata -v
```
Expected: PASS.

- [ ] **Step 5: Run full suite**

```bash
uv run pytest -q
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add tests/test_packaging.py
git commit -m "$(cat <<'EOF'
test(packaging): harden source-tree fallback + entry-point assertions

Fixes I3 + M2 from the PR #22 coherence review.

I3: -S does not reliably suppress editable-install dist-info
discovery, so the source-tree fallback test was previously passing
because installed metadata happened to match pyproject. Force the
fallback by monkey-patching importlib.metadata.version to raise
PackageNotFoundError.

M2: Replace substring assertions on entry_points.txt with
configparser parsing that verifies each console script is bound
to mcp_server_python_docs.__main__:main, not merely substring-
present.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Assert FastMCP server identity (M1)

**Files:**
- Modify: `tests/test_services.py` (add one test method to an existing class)

**Why this matters:** `src/mcp_server_python_docs/server.py:222` constructs `FastMCP("python-docs-mcp-server", ...)`. No test currently asserts that identity. If a future rename touches the FastMCP constructor without updating docs (or vice versa), nothing breaks loudly. One-line lock.

- [ ] **Step 1: Empirically confirm the FastMCP identity attribute**

Before writing the test, verify that `server.name` is the correct attribute (it is, for `mcp>=1.27.0`, the version pinned in `pyproject.toml`):

```bash
uv run python -c "from mcp_server_python_docs.server import create_server; s=create_server(); print([a for a in dir(s) if 'name' in a.lower()])"
```

Expected output: `['name']` (a single attribute). If you see additional candidates (e.g., `_server_name`, `server_info`), test each with `print(getattr(s, attr))` and pick the one that returns `"python-docs-mcp-server"`. Note the chosen attribute for use in Step 2.

- [ ] **Step 2: Write the test**

In `tests/test_services.py`, locate `def test_create_server_has_three_tools(self):` around line 411. Add a sibling method (assuming `server.name` from Step 1; substitute the verified attribute if different):

```python
    def test_create_server_identifies_as_dist_name(self):
        """FastMCP server name must match the public distribution name."""
        from mcp_server_python_docs.server import create_server

        server = create_server()
        # FastMCP exposes the constructor name via .name (mcp >= 1.27)
        assert server.name == "python-docs-mcp-server", (
            f"Expected FastMCP name 'python-docs-mcp-server', got {server.name!r}"
        )
```

- [ ] **Step 2b: Run test to verify it passes**

```bash
uv run pytest tests/test_services.py -k test_create_server_identifies_as_dist_name -v
```

Expected: PASS.

- [ ] **Step 3: Mutation-test the assertion**

Three sub-steps; the temporary edit MUST be reverted before continuing.

**Step 3a — break:** In `src/mcp_server_python_docs/server.py:222`, change:
```python
    mcp = FastMCP(
        "python-docs-mcp-server",
        lifespan=app_lifespan,
    )
```
to:
```python
    mcp = FastMCP(
        "nope",  # TEMPORARY — revert before commit
        lifespan=app_lifespan,
    )
```

**Step 3b — assert failure:**
```bash
uv run pytest tests/test_services.py -k test_create_server_identifies_as_dist_name -v
```
Expected: FAIL with the assertion message `Expected FastMCP name 'python-docs-mcp-server', got 'nope'`. If it PASSES, the assertion is not exercising the constructor — re-examine Step 2 before proceeding.

**Step 3c — revert and verify clean tree:** Restore `server.py:222` exactly:
```python
    mcp = FastMCP(
        "python-docs-mcp-server",
        lifespan=app_lifespan,
    )
```

Confirm the revert was complete:
```bash
git diff src/mcp_server_python_docs/server.py
```
Expected: **empty output**. The test commit MUST NOT include any change to `src/`.

Re-run to confirm green:
```bash
uv run pytest tests/test_services.py -k test_create_server_identifies_as_dist_name -v
```
Expected: PASS.

- [ ] **Step 4: Run full suite**

```bash
uv run pytest -q
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add tests/test_services.py
git commit -m "$(cat <<'EOF'
test(services): lock FastMCP server identity to dist name

Fixes M1 from the PR #22 coherence review. server.py:222
constructs FastMCP("python-docs-mcp-server", ...), but no test
asserts that identity. A future rename that touches the
constructor without updating docs (or vice versa) would have
been a silent drift. One assertion locks it.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Final cross-check + PR update

**Files:**
- None modified — verification + PR description / commit log

**Why this matters:** A coherence fix-up batch should end with the same kind of cross-cutting verification it was kicked off to enforce.

- [ ] **Step 1: Full local test sweep**

```bash
uv run ruff check src/ tests/
uv run pyright src/
uv run pytest -q
uv build
```

Expected: ruff clean, pyright clean, all tests pass, `uv build` produces both wheel and sdist in `dist/` named `python_docs_mcp_server-0.1.1*`.

- [ ] **Step 2: Re-run the cleanup verification on README**

```bash
perl -0777 -pe 's/<!-- PRE-PYPI:.*?<!-- \/PRE-PYPI -->//gs' README.md > /tmp/README.stripped.md
rg -in 'PRE[-]PYPI|Before PyPI publishing|Until.+PyPI|After PyPI publishing|git\+https://github.com/.*python-docs-mcp-server' /tmp/README.stripped.md
```

Expected: zero output.

- [ ] **Step 3: Re-run the dist-name leak scan**

```bash
rg -n 'mcp-server-python-docs' --type-add 'web:*.{md,toml,json,yml,yaml,sql,py}' -t web \
  | grep -v '^\.planning/' \
  | grep -v '^docs/superpowers/' \
  | grep -v '^uv.lock' \
  | grep -v '^CODE-REVIEW-PR' \
  | grep -v '^\.claude/'
```

Expected output (exact, one line per occurrence — counts may differ slightly by README Troubleshooting line numbers):

- 1 line: `pyproject.toml:48:mcp-server-python-docs = "mcp_server_python_docs.__main__:main"` (legacy `[project.scripts]` alias)
- 1 line: `tests/test_packaging.py:20:LEGACY_CLI_NAME = "mcp-server-python-docs"` — this is the **only** line in `tests/test_packaging.py` where the string literal appears. The entry-point structural test rewritten in Task 5 references `LEGACY_CLI_NAME` by variable name, so it does **not** show up in this scan.
- 2-4 lines: `README.md` Troubleshooting subsection added in Task 4 (the heading mentions the old name, plus 1-3 lines of body text with `"mcp-server-python-docs"` literal)

If you see any other hit, treat it as a leak and fix it before pushing. Compare against this expected list explicitly — do not just count.

- [ ] **Step 4: Push and update PR description**

```bash
git push
```

In the PR description, add a section noting the coherence fix-pack:

> **Coherence fix-pack (post-review):**
>
> - C1+I1: widened `<!-- PRE-PYPI -->` markers in README to enclose full transient sections; updated RELEASE.md cleanup checklist with a perl one-liner for a mechanical single-pass cleanup
> - I3: hardened source-tree fallback test by monkey-patching `importlib.metadata.version`
> - I4: added `Programming Language :: Python :: 3.14` classifier + classifier-presence test
> - I5: reconciled Show HN `build-index` to the full 5-version list
> - I6: added Troubleshooting subsection for legacy CLI users
> - M1: added FastMCP server identity assertion
> - M2: replaced substring assertions on `entry_points.txt` with `configparser` parsing

- [ ] **Step 5: Request a second-pass code review**

Use the `superpowers:requesting-code-review` skill with `BASE_SHA=<head of original PR>` and `HEAD_SHA=<HEAD>` to verify the fix-pack cleanly addresses the review findings.

---

## Notes for the executor

- Steps in Task 1 modify a single file (README.md) in multiple disjoint hunks. Apply them one block at a time with `Edit` so the file is never in an intermediate state where markers are unbalanced.
- For Task 5 Step 4 and Task 6 Step 3 (mutation-test the assertion), it is essential to revert the deliberate breakage before re-running the suite. If you forget, the next commit will include the breakage. The plan flags these explicitly because they are easy to skip.
- Each task ends with one commit. Seven tasks = seven commits. Keep them atomic.
- Do not touch `uv.lock` manually. If `uv build` re-locks for some reason, include the lock change in the relevant task's commit and note it in the message.
- Do not modify any file under `src/mcp_server_python_docs/` except as explicitly part of a mutation-test step that is immediately reverted. The fix-pack should be doc + test + pyproject only.

## Out of scope

The reviewer's M3 (banner UX inconsistency where `mcp-server-python-docs --version` prints `python-docs-mcp-server`) is intentional per the PR design and was already documented in the first-pass review. Not touched here.
