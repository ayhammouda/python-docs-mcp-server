---
phase: 4
plan_id: 04-C
title: "CLI build-index integration with CPython clone and Sphinx build"
wave: 2
depends_on:
  - 04-A
  - 04-B
files_modified:
  - src/mcp_server_python_docs/__main__.py
requirements:
  - INGR-C-01
  - INGR-C-02
  - INGR-C-03
autonomous: true
---

<objective>
Enhance the `build-index` CLI command in `__main__.py` to orchestrate the full content ingestion pipeline: shallow-clone CPython source at a pinned tag, create a dedicated Sphinx venv with branch-pinned requirements, run `sphinx-build -b json`, parse the output through `sphinx_json.py`, publish through `publish.py`, and clean up temp files. The existing objects.inv ingestion is preserved and runs first.
</objective>

<tasks>

<task id="1">
<title>Add CPython clone and Sphinx build orchestration to build-index</title>
<read_first>
- src/mcp_server_python_docs/__main__.py (current build_index function — lines 69-103)
- src/mcp_server_python_docs/ingestion/inventory.py (existing objects.inv ingestion — called first)
- src/mcp_server_python_docs/ingestion/sphinx_json.py (created in Plan A — ingest_sphinx_json_dir, populate_synonyms, rebuild_fts_indexes)
- src/mcp_server_python_docs/ingestion/publish.py (created in Plan B — generate_build_path, publish_index)
- src/mcp_server_python_docs/storage/db.py (bootstrap_schema, get_readwrite_connection, assert_fts5_available)
- .planning/phases/04-sphinx-json-ingestion-atomic-swap-publishing/04-RESEARCH.md (RQ1 — Sphinx pins, RQ6 — no make json target)
</read_first>
<action>
Rewrite the `build_index()` function in `__main__.py` to orchestrate the full pipeline. The function currently only does objects.inv ingestion. The new version must:

**1. Version tag mapping (near top of function):**
```python
# Map version to CPython git tag and Sphinx constraints
VERSION_CONFIG = {
    "3.12": {"tag": "v3.12.13", "sphinx_pin": "sphinx~=8.2.0"},
    "3.13": {"tag": "v3.13.12", "sphinx_pin": "sphinx<9.0.0"},
}
```
Update tags to latest patch releases as of April 2026. These are the tags to clone.

**2. Build artifact setup:**
- Import `generate_build_path` from `ingestion.publish`
- `build_db_path = generate_build_path()` — timestamped artifact (PUBL-01)
- Open RW connection to `build_db_path`
- `bootstrap_schema(conn)` + `assert_fts5_available(conn)`

**3. For each version in version_list:**
  
  a. **Objects.inv ingestion** (existing — keep as-is):
  ```python
  from mcp_server_python_docs.ingestion.inventory import ingest_inventory
  count = ingest_inventory(conn, version)
  logger.info(f"Ingested {count} symbols for Python {version}")
  ```
  
  b. **Clone CPython source** (INGR-C-01):
  ```python
  import subprocess
  import tempfile
  
  config = VERSION_CONFIG.get(version)
  if not config:
      logger.warning(f"No CPython build config for {version}, skipping content ingestion")
      continue
  
  clone_dir = tempfile.mkdtemp(prefix=f"cpython-{version}-")
  logger.info(f"Cloning CPython {config['tag']} into {clone_dir}...")
  subprocess.run(
      ["git", "clone", "--depth", "1", "--branch", config["tag"],
       "https://github.com/python/cpython.git", clone_dir],
      check=True,
      capture_output=True,
      text=True,
  )
  ```
  
  c. **Create dedicated Sphinx venv** (INGR-C-02):
  ```python
  import venv
  
  venv_dir = os.path.join(clone_dir, "_sphinx_venv")
  logger.info(f"Creating Sphinx venv at {venv_dir}...")
  venv.create(venv_dir, with_pip=True)
  pip_path = os.path.join(venv_dir, "bin", "pip")
  
  # Install Sphinx with the version pin for this CPython branch
  subprocess.run(
      [pip_path, "install", config["sphinx_pin"]],
      check=True,
      capture_output=True,
      text=True,
  )
  
  # Install remaining Doc/requirements.txt deps
  doc_reqs = os.path.join(clone_dir, "Doc", "requirements.txt")
  if os.path.exists(doc_reqs):
      subprocess.run(
          [pip_path, "install", "-r", doc_reqs],
          check=True,
          capture_output=True,
          text=True,
      )
  ```
  
  d. **Run sphinx-build -b json** (INGR-C-03):
  ```python
  sphinx_build = os.path.join(venv_dir, "bin", "sphinx-build")
  doc_dir = os.path.join(clone_dir, "Doc")
  json_out = os.path.join(doc_dir, "build", "json")
  
  logger.info(f"Running sphinx-build -b json for Python {version}...")
  logger.info("This may take 3-8 minutes...")
  result = subprocess.run(
      [sphinx_build, "-b", "json", "-j", "auto", doc_dir, json_out],
      capture_output=True,
      text=True,
      cwd=doc_dir,  # Run from Doc/ so conf.py extension paths resolve
  )
  if result.returncode != 0:
      logger.error(f"sphinx-build failed for {version}:\n{result.stderr}")
      # Continue with other versions rather than abort entirely
      continue
  logger.info(f"sphinx-build complete for Python {version}")
  ```
  Note: `-j auto` uses all CPU cores for parallel building. `cwd=doc_dir` ensures `Doc/conf.py`'s `sys.path` manipulation for custom extensions works.
  
  e. **Ingest fjson files:**
  ```python
  from mcp_server_python_docs.ingestion.sphinx_json import (
      ingest_sphinx_json_dir,
      populate_synonyms,
      rebuild_fts_indexes,
  )
  
  # Get doc_set_id for this version
  doc_set_id = conn.execute(
      "SELECT id FROM doc_sets WHERE source='python-docs' AND version=? AND language='en'",
      (version,),
  ).fetchone()[0]
  
  success, failures = ingest_sphinx_json_dir(conn, Path(json_out), doc_set_id)
  logger.info(f"Ingested {success} documents ({failures} failures) for Python {version}")
  ```
  
  f. **Cleanup clone:**
  ```python
  import shutil
  shutil.rmtree(clone_dir, ignore_errors=True)
  logger.info(f"Cleaned up {clone_dir}")
  ```

**4. After all versions:**
- `populate_synonyms(conn)` — INGR-C-09
- `rebuild_fts_indexes(conn)` — INGR-C-08
- `conn.close()`

**5. Publish:**
```python
from mcp_server_python_docs.ingestion.publish import publish_index

versions_str = ",".join(version_list)
success = publish_index(build_db_path, versions_str)
if not success:
    logger.error("Publishing failed — smoke tests did not pass")
    raise SystemExit(1)
```

**6. Error handling:**
- Wrap the entire per-version block in try/except
- On subprocess.CalledProcessError: log error, continue to next version
- On any other exception: log error, cleanup clone dir, continue
- After loop: if NO version succeeded, raise SystemExit(1)

The function should also add a `--skip-content` flag to the click command that skips the Sphinx build and only does objects.inv ingestion (useful for quick testing):
```python
@click.option("--skip-content", is_flag=True, help="Skip Sphinx JSON build, only ingest objects.inv")
```
</action>
<acceptance_criteria>
- `__main__.py` build_index function contains `git clone --depth 1`
- `__main__.py` contains `sphinx-build` or `sphinx_build`
- `__main__.py` contains `-b json` (Sphinx JSON builder)
- `__main__.py` does NOT contain `make json` (no make json target exists)
- `__main__.py` contains `from mcp_server_python_docs.ingestion.sphinx_json import`
- `__main__.py` contains `from mcp_server_python_docs.ingestion.publish import`
- `__main__.py` contains `generate_build_path()` (timestamped build artifact)
- `__main__.py` contains `publish_index(` (atomic swap orchestration)
- `__main__.py` contains `populate_synonyms(` (synonym population)
- `__main__.py` contains `rebuild_fts_indexes(` (FTS rebuild)
- `__main__.py` contains `shutil.rmtree` (clone cleanup)
- `__main__.py` contains `venv.create` or `venv_dir` (dedicated venv creation)
- `__main__.py` contains `--skip-content` option
- `__main__.py` contains VERSION_CONFIG or equivalent version→tag mapping
- `mcp-server-python-docs build-index --help` shows `--versions` and `--skip-content` options
</acceptance_criteria>
</task>

</tasks>

<verification>
- [ ] build-index CLI orchestrates clone → venv → sphinx-build → parse → publish pipeline
- [ ] Shallow clone at pinned tag (INGR-C-01)
- [ ] Dedicated venv with branch-pinned Sphinx (INGR-C-02)
- [ ] sphinx-build -b json invoked directly, never make json (INGR-C-03)
- [ ] Objects.inv ingestion runs first (existing behavior preserved)
- [ ] Synonyms populated after all versions
- [ ] FTS indexes rebuilt after all versions
- [ ] Build artifact is timestamped, published via atomic swap
- [ ] Clone directories cleaned up after ingestion
- [ ] --skip-content flag available for quick testing
</verification>

<must_haves>
- Shallow git clone at pinned CPython tag (INGR-C-01)
- Dedicated venv per version with correct Sphinx pin (INGR-C-02)
- sphinx-build -b json direct invocation (INGR-C-03) — never make json
- Timestamped build artifact + atomic swap publish
- Clone cleanup via shutil.rmtree
</must_haves>
