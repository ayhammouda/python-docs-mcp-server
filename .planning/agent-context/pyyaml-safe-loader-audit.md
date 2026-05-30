# Agent Context — PyYAML safe-loader audit

> One-read working context for issue `[v0.3.0] security — audit and document PyYAML safe-loader discipline`.

## 1. Roadmap excerpt

> **PyYAML safe-loader audit** (roadmap §4, v0.3.0): `synonyms.yaml` is loaded at
> startup; confirm only `yaml.safe_load` is used; document the trust boundary.
>
> **Decision 5.11 (locked):** PyYAML safe-loader-only discipline; `synonyms.yaml`
> is the only YAML input and is packaged with the wheel.

## 2. Code touch-points (already audited for you — verify, then lock in)

- `src/mcp_server_python_docs/server.py:54–57` — loads `data/synonyms.yaml` via
  `importlib.resources` and `yaml.safe_load(path.read_text())`. ✅ safe.
- `src/mcp_server_python_docs/ingestion/sphinx_json.py:595–603` — loads the same
  file via `importlib.resources` + `yaml.safe_load`, then type-checks it is a
  mapping. ✅ safe.
- `src/mcp_server_python_docs/data/synonyms.yaml` — the only YAML data input;
  packaged with the wheel.
- No other `yaml.load(` / `yaml.unsafe_load(` / custom-`Loader=` call sites were
  found in `src/`. Your job is to **prove** this with a regression test, not just
  assert it.

## 3. Patterns to follow

- `tests/test_synonyms.py` already exists — add the discipline test there.
- A clean way to assert the discipline: walk `src/` `.py` files and fail if any
  line matches `yaml.load(` or `yaml.unsafe_load(` or `Loader=` (excluding
  `SafeLoader`). Keep it simple and fast; no new deps.
- `tests/test_packaging.py` already verifies `synonyms.yaml` ships in the wheel —
  reference it; you don't need to duplicate that.

## 4. Known pitfalls

- **Do not edit `SECURITY.md`** (forbidden). Capture the trust-boundary write-up
  in a new `docs/architecture/YAML-TRUST-BOUNDARY.md` and recommend SECURITY.md
  wording for Vision.
- The two `safe_load` sites both also exist as `.pyc` in `__pycache__`; grep
  source dirs only (`src/`, `tests/`), not `__pycache__`.
- If the codebase is already clean (expected), the deliverable is the **lock-in**
  (regression test + doc note), not a code fix. Say so plainly in the PR.
- A literal `yaml.load(` string inside your *test* (as a pattern to search for)
  is fine and expected — the test asserts it does not appear in non-test `src/`.

## 5. Decision log

- Audit result (clean / findings):
- Regression test name + what it scans:
- Trust-boundary doc location:
- Recommended SECURITY.md wording (for Vision):
