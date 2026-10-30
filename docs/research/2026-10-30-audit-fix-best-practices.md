# Audit fixes: best-practice check

- **Date:** 2026-10-30
- **Scope:** Every fix proposed by the 2026-10-30 whole-repo audit of `python-docs-mcp-server`: the 19 numbered findings and the 11 smaller ones. The audit ran on branch `feat/mcp-sdk-v2` at `c03e497`. For each fix, the question is whether it matches established practice, and what should ship instead if it does not.
- **Status:** Research only. No product code was changed. The implementation plans are in `docs/plans/2026-10-30-audit-fixes-roadmap.md` and the plan files it lists.
- **Method:** Four researchers each took one area. They used primary sources only: official docs, specs, RFCs, CWE and SEI CERT entries, source code at pinned tags or commits, first-party engineering guidance and peer-reviewed papers. They also ran local probes on scratch copies of the repo. Those probe scripts lived in a session scratchpad and are not in the repo, so a probe result counts as evidence, not as a source. Anything marked **(inferred)** is the researcher's own reasoning.

## TL;DR

- **All 19 findings hold up.** 3 fixes are right as proposed. 12 are right but need changes. 4 have a better alternative.
- **The four better alternatives:**
  - **#1 search:** change the `sections_fts` tokenizer instead of the query. Dropping `.` from `tokenchars` fixes sentence-final words, `module...`, `.pyc` and bare `TaskGroup`. All 319 related tests pass unchanged. The audit's query patch breaks 3 tests and over-ranks `word.` in bm25. The cost: a `schema.sql` change, which needs owner approval, and an index rebuild.
  - **#11 URLs:** use one shared module with label-anchored parsing plus the CommonMark and GFM rules. The new harness reuses it for grounding.
  - **#14 installer:** do the host user's file work as that user (`runuser`), or with descriptor-based calls. `follow_symlinks=False` alone leaves four holes.
  - **#19 verifier:** accept exactly one verdict object, found with `json.JSONDecoder.raw_decode`, and check its types strictly. Parsing from the first `{` to the last `}` still fails on common replies.
- **Eleven new issues in the same code** (table below): ten found by this research and one by the plan review.
  - Two are security issues in `ops/vision/install.py`.
  - One lets a JSON `false` exit code pass as success in the Vision review gate.
  - One is a test that writes into the developer's real cache folder.
- **Practice:**
  - Ship one self-contained change per fix, with a test that fails on the parent commit.
  - Keep refactors in separate changes.
  - Write a changelog entry for every tool-description change and every required index rebuild.

  Sources: Google's engineering practices, *Software Engineering at Google*, Keep a Changelog and SemVer (section "Benchmarks and engineering practice", item 6).

## Verdicts at a glance

| # | Finding | Verdict | What should ship | Section |
|---|---|---|---|---|
| 1 | Search misses words that end a sentence | Better alternative | `sections_fts` tokenizer `unicode61 remove_diacritics 2 tokenchars '_'`. No query-side change. Rebuild, then the regression gate | Search 1 |
| 2 | Code examples filed under the page's first section | Aligned with changes | Walk the ancestors: inside a `dd`, use its `dt` anchor; otherwise the enclosing `<section id>`. Keep the heading lookup as a fallback | Ingestion 1 |
| 3 | `get_docs` rejects the slug format its schema shows | Aligned with changes | Try the slug as given, then without `.html`, before the cache lookup. Return the canonical slug, give an actionable not-found error, fix the description | Search 2 |
| 4 | `compare_versions` reads version notes wrong | Aligned with changes | Match "New" or "Added" with patch versions. `changed_in` is the newest note in v2 that is absent from v1. `new_in` is the smallest "Added" version in v2 | Search 3 |
| 5 | `pdctl publish` can send the wrong files | Aligned with changes | `git diff-tree -r -z --no-renames` plus `git cat-file blob`. The broker requires a `tree_sha` check before the review | Ops 1 |
| 6 | Context7 adapter gives every secret to npm code | Aligned with changes | Pass only the key, and only in key mode. In the new harness: a container installed from a lockfile with `npm ci --ignore-scripts` | Ops 2 |
| 7 | Merge and release gates have no failing test | Aligned | Parametrized `review()` and `release()` tests, which kill 10 of 10 hand-made mutants. Mutation testing later, as a non-blocking job | Ops 3 |
| 8 | Benchmark failures all show up as crashes | Aligned with changes | `cell_failure_from` walks nested groups (two deep on `mcp` 2.3.0) and keeps every reason | Benchmarks 1 |
| 9 | The benchmark report misstates the run | Aligned with changes | Show success latency, failed-cell latency and error rate side by side. Render exclusions, delete the constant `external_provider_calls`, drop the echo alias | Benchmarks 2 |
| 10 | Live token counting uses a retired model | Aligned with changes | Pin `claude-sonnet-5-5`. Send a valid `tool_use`/`tool_result` envelope, write `count_model` into each record, and show failed counts as failed | Benchmarks 3 |
| 11 | Competitor answers parsed with stray punctuation | Better alternative | A shared URL module: CommonMark link destinations, GFM trailing-punctuation rules, label-anchored ids | Benchmarks 4 |
| 12 | The documented FTS5 fallback does nothing | Aligned | Delete the extra. Keep one FTS5 hint. `doctor` reuses `assert_fts5_available` | Ingestion 2 |
| 13 | Failed builds leave files in the cache folder | Aligned with changes | Close, then delete, on every failure path. Run `PRAGMA journal_mode=DELETE` before publishing | Ingestion 3 |
| 14 | The installer follows symlinks as root | Better alternative | Do host-user file work through `runuser`, or with `dir_fd` plus `O_NOFOLLOW` plus `fchown`/`fchmod`. Validate the `.pub` content | Ops 4 |
| 15 | `detect_python_version` blocks the server | Aligned | Cache the raw version and its source at startup. Keep `async def` | Ingestion 5 |
| 16 | Release files unchecked between build and publish | Aligned with changes | Job outputs `dist-id` and `dist-sha256`. Download by artifact ID and compare the listing before attesting and publishing. Build constraints with hashes | Ops 5 |
| 17 | Code and schema nothing uses at runtime | Aligned with changes | Snapshot the served `tools/list`. Drop dead tables and columns together with their SELECTs. Stamp `PRAGMA user_version` | Ingestion 6 |
| 18 | Pagination can split an accent from its letter | Aligned with changes | Replace the `text[end-1]` check with `text[end]`. Add a property test | Search 4 |
| 19 | A verifier formatting slip costs a review | Better alternative | Exactly one verdict object, found with `raw_decode`. Strict types; failures report `malformed_verdict` | Ops 6 |

### Smaller findings

| Finding | Verdict | What should ship | Section |
|---|---|---|---|
| Synonym expansion runs twice per search | Aligned (tidiness) | Precompile once, expand once | Search 5 |
| `index.db` briefly missing during publish | Aligned with changes | Hard-link to a temp name, replace that onto `.previous`, then replace the build into place; copy where hard links fail | Ingestion 4 |
| `score` accepts a changed corpus | Aligned (now) | Default to the run snapshot; refuse a hash mismatch | Benchmarks 5a |
| HTTP client left open per cell | Aligned (new harness) | `async with` the client in the bridge | Benchmarks 5b |
| Regression gate's network block has gaps | Aligned with changes | Full network and process event set, kept as a tripwire; audit hooks are not a sandbox | Benchmarks 5c |
| About 400 duplicated benchmark lines | Wait | No refactor before the planned deletion, except the shared failure helper | Benchmarks 5d |
| `pdctl status`, ref pre-check, variable paging, permission map, dead rerun entry | Aligned; ref check with changes | Receipt ids; branch check that follows git's rules plus a ref pre-check before the review; read the variable by name; one permission map; drop the `e2e.yml` entry | Ops 7 |

### New issues found during the research

| # | Issue | Where | Section |
|---|---|---|---|
| N1 | Root reads the SSH `.pub` file through any symlink and writes it into a world-readable `authorized_keys`, which could leak a root-only file such as an App private key | `ops/vision/install.py` | Ops 4 |
| N2 | Root copies `uv` through a symlinked source path | `ops/vision/install.py` | Ops 4 |
| N3 | A JSON `"exit_code": false` (or `0.0`) compares equal to 0, so it counts as a passing command | `ops/vision/control.py` `review()`, `public_evidence()` | Ops 6 |
| N4 | The committed input-schema snapshots already differ from what `tools/list` serves | `tests/test_schema_snapshot.py` | Ingestion 6 |
| N5 | Publishing renames the open `index.db`. Readers then share name-based WAL sidecars, which SQLite calls undefined behaviour | `ingestion/publish.py` | Ingestion 3 |
| N6 | The token-count envelope sends `tool_result` blocks with no matching `tool_use`, and omits `tools` and `system` | `benchmarks/adapters/claude_tokens.py` | Benchmarks 3 |
| N7 | `.html` callers would always miss the persistent cache unless the slug is resolved before the lookup | `services/content.py` | Search 2 |
| N8 | `artifact_hash` is computed before later writes, so it never matches the published file | `ingestion/publish.py` | Ingestion 3 |
| N9 | The first-party benchmark adapter also passes the whole parent environment to its server | `benchmarks/adapters/python_docs_mcp_adapter.py` | Ops 2 |
| N10 | The real-server adapter smoke test accepts `mcp_protocol_crash` as a pass | `tests/benchmarks/test_python_docs_mcp_adapter.py` | Benchmarks 1 |
| N11 | `test_build_index_bad_version_stderr_only` runs `build-index --versions 99.99` against the developer's real cache folder. Each `pytest` run leaves one `build-*.db-wal`/`-shm` pair there, and a stale-build sweep would turn it into a deletion. These test runs, not offline auto-builds, produced the orphan pairs the audit counted (20 on 2026-10-30) | `tests/test_stdio_hygiene.py` | Found in the plan review; fixed in PR 4, Task 1 |

---

## Search and retrieval

I checked all five findings against primary sources: the SQLite FTS5 docs, the MCP spec revision 2026-07-28 (the revision that the installed `mcp` 2.3.0 targets), Anthropic's tool-writing guidance, RFC 9413, SemVer 2.0.0, Sphinx and CPython source at the pinned tags, UAX #29 revision 49, and the Python docs. I also ran local experiments on scratch copies of `src/` and `tests/`. No repo file was changed. Each finding has a probe that fails today, and all the probes and test runs can be reproduced from `<session scratchpad>/scripts/`. In short: (1) the query-side dot expansion works, but it is a workaround with a ranking side effect. Removing `.` from the `sections_fts` tokenizer fixes more cases and passes all 319 related tests unchanged. The audit's query-side patch breaks 3 existing assertions, so both routes need owner approval. (2) Accept `.html`, return the canonical slug and make the error actionable. The description edit trips the schema snapshot guard. (3) The regex fix is right, but `changed_in` should mean "newest note that is new in v2". A `(v1, v2]` window breaks an existing test. (4) The minimal pager fix is enough for CPython docs text. It must replace the current check, not sit beside it. (5) This one is tidiness only.

### 1. Search misses words that end a sentence

- **Current recommendation:** In `fts5_escape`, strip a trailing `.`, emit `("w" OR "w.")` for each word, and join the groups with ` AND `.
- **Verdict:** Better alternative exists.
- **Evidence:**
  - By default unicode61 treats punctuation as separators, and `tokenchars` turns listed characters into token characters (https://sqlite.org/fts5.html#unicode61_tokenizer). With `.` in `tokenchars`, `module.` is a separate term. A local check of the vocabulary listed `module`, `module.` and `module...` as three terms (inferred; SQLite 3.50.4).
  - A quoted string goes through the tokenizer, and several tokens form a phrase. The docs show `one.two.three` matching the phrase "one two three" when the tokenizer splits on dots (https://sqlite.org/fts5.html#fts5_phrases, https://sqlite.org/fts5.html#fts5_strings). So dotted names still match as adjacent-token phrases after `.` is removed.
  - The docs list `'(one OR two) three'` as a syntax error. Implicit AND is never inserted next to parentheses (https://sqlite.org/fts5.html#fts5_boolean_operators). A local check confirmed this, and confirmed that the explicit `AND` form parses.
  - bm25 splits the query into phrases, and each phrase gets its own IDF (https://sqlite.org/fts5.html#the_bm25_function). A hand-written `("module" OR "module.")` is two phrases, and the rarer `module.` weighs more. In the local check with the query-side fix, the sentence-final row scored -3.45 and the mid-sentence row scored -1.17. With a `.`-free tokenizer the same rows scored -1.15 and -0.90 (inferred).
  - On synonyms, FTS5's own guidance says mapping variants to one token at tokenization is, in many cases, "the best approach". It also says that only a tokenizer-level `(first OR 1st)` counts as one phrase for auxiliary functions (https://sqlite.org/fts5.html#synonym_support). Custom tokenizers need `sqlite3_bind_pointer` (https://sqlite.org/fts5.html#extending_fts5), which stdlib `sqlite3` does not expose (inferred). So the only stdlib tokenizer-level fix is to change the unicode61 options.
  - Scale: in 6 sampled docs.python.org 3.13 pages, 3.2% to 6.7% of word tokens end in `.` under the current rule (inferred; local scan).
  - Coverage (local check): the query-side fix still misses `module...`, leading-dot words (`.pyc`), and partial identifiers (`TaskGroup` in `asyncio.TaskGroup`, `assertEqual` in `self.assertEqual(`). The tokenizer change finds all of them. Its one precision cost: `"asyncio.TaskGroup"` also matched "asyncio. TaskGroup" across a sentence break (inferred; rare).
  - Trigram is not a fit. With trigram, substrings shorter than 3 characters match no rows in full-text queries (https://sqlite.org/fts5.html#the_trigram_tokenizer), so `re`, `os` and `io` would vanish. It also matches inside words.
  - Test impact (local runs of 319 related tests on scratch copies): with only the `sections_fts` tokenizer changed to `tokenchars '_'`, all 319 pass. With the audit's `fts5_escape` patch, 3 fail on exact-string assertions: `tests/test_retrieval.py` `test_fts5_escape_basic` (L225-233), `test_build_match_expression_keeps_original_multiword_query` (L358-363) and `test_build_match_expression_without_synonyms` (L366-370).
  - Rebuild cost: `'rebuild'` re-derives an external-content index from its content table (https://sqlite.org/fts5.html#the_rebuild_command, https://sqlite.org/fts5.html#external_content_tables). `build-index` already writes a fresh `build-*.db` and swaps it in with `os.replace` (`ingestion/publish.py` L52, L377). So the new schema needs no migration code, but users must rerun `build-index`.
- **Recommended fix:**
  - In `storage/schema.sql` L120-124, set the `sections_fts` tokenizer to `unicode61 remove_diacritics 2 tokenchars '_'`. Update the comments at L5-8 and L112-117: dotted names now match as phrases.
  - Keep `symbols_fts`, which holds identifiers with no sentence punctuation. Keep `examples_fts` too. Dropping `.` there would also help partial-name recall, but benchmark that in a separate step.
  - Do not touch `fts5_escape` or `build_match_expression`. Open a supervisor-review PR, because `schema.sql` is forbidden territory.
  - Optional: let `doctor` read `sqlite_master.sql` for `sections_fts` and tell users with an old index to rerun `build-index`.
  - Fallback if the schema change is refused: put the dot expansion behind a new opt-in path (a keyword argument on `build_match_expression`, used only by `SearchService`), so existing assertions stay untouched. Use explicit `AND`, skip tokens that are empty after stripping, and document the ranking skew.
  - The change must not break the STOR-02 tests in `tests/test_schema.py`, `test_fts5_escape_fuzz`, the retrieval-regression fixtures, or the hit@5 gate in `benchmarks/regression.py`.
- **Test that proves it:** Build a DB with `bootstrap_schema` and add one section: "Encode objects with the json module." Then `search_sections(conn, build_match_expression("json module", {}), None, 5)` must return it. It returns 0 hits today and 1 with the schema change (checked on a scratch copy). Add two guards: `"asyncio.TaskGroup"` still finds a section that mentions it, and `TaskGroup` now finds it too.
- **Risks and compatibility:**
  - `schema.sql` change: needs owner approval.
  - Users must rerun `build-index`. Old indexes keep the old behaviour; nothing breaks.
  - Ranking shifts: section-mode queries for a module name now also hit `module.*` headings, which have weight 10.
  - Rerun the regression gate on a rebuilt index before merging. That is not verified here: the local `index.db` holds symbols only.

### 2. `get_docs` rejects the slug format its own schema shows

- **Current recommendation:** In `ContentService.get_docs`, try the slug as given, then without `.html`. Delete compare's loop. Change the description example to `library/asyncio-task`.
- **Verdict:** Aligned with changes.
- **Evidence:**
  - MCP 2026-07-28 classes input validation errors as tool execution errors that carry "actionable feedback that language models can use to self-correct" (https://modelcontextprotocol.io/specification/2026-07-28/server/tools#error-handling; source at https://github.com/modelcontextprotocol/modelcontextprotocol/blob/9850cae8923b0ee3d19a4476f1010b97aa161a55/docs/specification/2026-07-28/server/tools.mdx#L738-L785). Servers must validate all tool inputs (same file, L787-793).
  - SDK v2.3.0 turns `ToolError` into `is_error=True` with the message text (https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/mcpserver/server.py#L440-L456). So the error string is the model's only repair hint.
  - Anthropic says to make expected inputs explicit in descriptions and to enforce them with strict data models. It also says error responses should give "specific and actionable improvements, rather than opaque error codes or tracebacks" (https://www.anthropic.com/engineering/writing-tools-for-agents#optimizing-tool-responses-for-token-efficiency, https://www.anthropic.com/engineering/writing-tools-for-agents#prompt-engineering-your-tool-descriptions). The API docs say to return "semantic, stable identifiers (for example, slugs or UUIDs)" (https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools#best-practices-for-tool-definitions).
  - RFC 9413 makes three points:
    - Tolerating unexpected input leads to protocol decay (https://www.rfc-editor.org/rfc/rfc9413.html#section-4.1).
    - When a specification and its implementation diverge, fix the divergence (https://www.rfc-editor.org/rfc/rfc9413.html#section-5).
    - "Any intolerance also needs to be strongly supported by specifications" (https://www.rfc-editor.org/rfc/rfc9413.html#section-5.1).

    Here the spec (the description) shows `.html`, and the server itself emits `.html` in `uri` fields. Accepting `.html` follows the spec. Rejecting it is intolerance the spec contradicts (inferred).
  - SemVer: the public API "could be declared in the code itself or exist strictly in documentation" (spec item 1). A bug fix is an internal change that fixes incorrect behaviour (item 6). In 0.y.z anything may change (item 4) (https://semver.org/#semantic-versioning-specification-semver). So the description is part of the API. Making the code accept the documented form is a PATCH-level fix, and the canonical example removes no accepted input (inferred). The project is at 0.4.0, but `AGENT-EXECUTION-PIPELINE.md` §2 still gates any tool-surface text.
  - Local check on a scratch copy (normalization, new error text, both description edits): 318 of 319 tests pass. The only failure is `test_schema_snapshot[schema-get_docs-input.json-GetDocsInput]`, because the drift guard snapshots the unused `models.py` copy (`tests/fixtures/schema-get_docs-input.json` L5). The regression fixtures store `.html` slugs and still pass, because the exact form is tried first.
- **Recommended fix:**
  - `services/content.py` L68-82: try `dict.fromkeys((slug, slug.removesuffix(".html")))` in order, and return `slug=doc_row["slug"]` (the canonical slug).
  - Resolve the canonical slug before the persistent-cache lookup. `put` keys entries by `result.slug` (`services/persistent_cache.py` L145-157), so `.html` callers would always miss the cache.
  - Not-found error: name the form and the next step, e.g. "Slugs look like 'library/asyncio-task' (no '#anchor'); use search_docs to find one." Close-match suggestions are optional.
  - Do not strip a `#fragment` silently. Return the actionable error instead (inferred).
  - Description in `server.py` L261-264 and `models.py` L76-79: "Page slug from a search_docs hit, e.g. 'library/asyncio-task' ('.html' suffix accepted)". Regenerate the snapshot fixture in the same approved PR.
  - `services/compare.py` L144-158: replace the loop with one call. Keep `ranker._document_candidates`, which uses direct SQL.
- **Test that proves it:** Use a production-shaped fixture: slug `library/json`, uri `library/json.html`. `ContentService(db).get_docs("library/json.html", "3.13")` should return content with `slug == "library/json"`. Today it raises `PageNotFoundError`. Add a second test: the error for an unknown slug contains the canonical example.
- **Risks and compatibility:** The description is tool-surface text, so it needs approval and a snapshot regeneration. The returned `slug` value only changes for inputs that fail today. Calls that work today behave the same.

### 3. `compare_versions` reads version notes wrong

- **Current recommendation:** Match `(?:New|Added) in version`, collect all matches, and keep the newest version in `(v1, v2]`, compared as integer tuples.
- **Verdict:** Aligned with changes.
- **Evidence:**
  - Sphinx 3.4.3 and 7.2.6 print "New in version %s". Sphinx 7.3.0 and 8.2.3 print "Added in version %s":
    - https://github.com/sphinx-doc/sphinx/blob/v3.4.3/sphinx/domains/changeset.py#L28-L38
    - https://github.com/sphinx-doc/sphinx/blob/v7.2.6/sphinx/domains/changeset.py#L22-L32
    - https://github.com/sphinx-doc/sphinx/blob/v7.3.0/sphinx/domains/changeset.py#L22-L34
    - https://github.com/sphinx-doc/sphinx/blob/v8.2.3/sphinx/domains/changeset.py#L25-L37

    The change is #11015 in the 7.3.0 changelog (https://github.com/sphinx-doc/sphinx/blob/v7.3.0/CHANGES.rst#L46-L48). So the cut-off is Sphinx 7.3, not Sphinx 8.
  - CPython pins Sphinx 3.4.3 for 3.10, ~=7.2.0 for 3.11 and ~=8.2.0 for 3.12 (https://github.com/python/cpython/blob/v3.10.20/Doc/requirements.txt#L6, https://github.com/python/cpython/blob/v3.11.15/Doc/requirements.txt#L9, https://github.com/python/cpython/blob/v3.12.13/Doc/requirements.txt#L10). So `new_in` is always None when v2 is 3.12 or later. A probe confirmed it.
  - Full label set: `versionadded`, `versionchanged`, `deprecated`, and `versionremoved` ("Removed in version %s", new in Sphinx 7.3; https://github.com/sphinx-doc/sphinx/blob/v8.2.3/doc/usage/restructuredtext/directives.rst#L590-L596). CPython adds three more:
    - `deprecated-removed`, rendered "Deprecated since version X, will be removed in version Y" or "..., removed in version Y" (https://github.com/python/cpython/blob/v3.13.13/Doc/tools/extensions/changes.py#L36-L73).
    - `next`, which renders as "<release> (unreleased)" (same file, L20-24).
    - In 3.15.0, `soft-deprecated`, rendered "Soft deprecated since version %s" (https://github.com/python/cpython/blob/v3.15.0/Doc/tools/extensions/changes.py#L81-L109).
  - Stable classes: the HTML writer wraps each note in `<div class="{type}">` (https://github.com/sphinx-doc/sphinx/blob/v8.2.3/sphinx/writers/html5.py#L315-L319; same at https://github.com/sphinx-doc/sphinx/blob/v3.4.3/sphinx/writers/html5.py#L196-L200). The label sits in `<span class="versionmodified added|changed|deprecated|removed">` (changeset.py v8.2.3 L76). One exception: the CPython 3.11 `deprecated-removed` span has only `versionmodified` (https://github.com/python/cpython/blob/v3.11.15/Doc/tools/extensions/pyspecific.py#L435-L490). The live 3.13 page shows this markup (https://docs.python.org/3.13/library/asyncio-task.html#asyncio.TaskGroup).
  - The `.fjson` body comes from `StandaloneHTMLBuilder` (https://github.com/sphinx-doc/sphinxcontrib-serializinghtml/blob/2.0.0/sphinxcontrib/serializinghtml/__init__.py#L38, #L153-L166), so the classes are present at ingestion. markdownify then drops them (`ingestion/sphinx_json.py` L267).
  - Translations change the label but not the class. The French catalog says "Ajouté dans la version %s" (https://github.com/sphinx-doc/sphinx/blob/v8.2.3/sphinx/locale/fr/LC_MESSAGES/sphinx.po#L741-L757). docs.python.org/fr shows those labels with the same `versionmodified added` classes (https://docs.python.org/fr/3.13/library/asyncio-task.html). The index is English-only today.
  - Patch versions exist: the 3.13 page shows "Added in version 3.5.1." The pattern `\d+\.\d+` truncates it.
  - A `dt` section includes nested members (`ingestion/sphinx_json.py` L356-367), so a class section carries its methods' notes (inferred).
  - Local check: the `(v1, v2]` window passes the probes but breaks the existing `test_compare_changed_signature`, which asserts `changed_in == "3.10"` for 3.10 to 3.11 (`tests/test_compare_versions.py` L209-216). The set-difference rule passes all 15 existing compare tests and the 3 probes.
- **Recommended fix** (in `services/compare.py` only; no schema change):
  - Patterns: `\b(?:New|Added) in version\s+(\d+(?:\.\d+)+)`, with the same version capture for "Changed in" and "Deprecated since". Keep "Deprecated since" case-sensitive so that "Soft deprecated since" stays out. Add `_vt(v)` to turn a version into an integer tuple.
  - `changed_in`: the newest version among notes that are in v2 but not in v1, i.e. `max(notes(v2) - notes(v1), key=_vt)`. This defines what changed between the two snapshots without version arithmetic, and it handles backports.
  - `new_in` in the "added" branch: the smallest Added/New version in the v2 section, because a member cannot predate its class (inferred).
  - `deprecated_in`: keep the current meaning and use the new capture.
  - Long-term option: extract `div.versionadded` and the other note divs and spans into a table at ingestion. That works in any language and attributes notes to members. It needs a `schema.sql` change and a full re-ingestion, so defer it until i18n or member attribution is needed.
- **Test that proves it** (verified fail-before and pass-after on a scratch copy):
  - "Added in version 3.11." gives `new_in == "3.11"`. Today it gives None.
  - v1 has "Changed in version 3.3"; v2 has that note plus "Changed in version 3.11". The result is `changed_in == "3.11"`. Today it is "3.3".
  - "Added in version 3.5.1." gives `new_in == "3.5.1"`.
- **Risks and compatibility:** The return shape is unchanged; only values change. `new_in` now fills for 3.12 and later. `changed_in` can be None when only old notes exist, but `section_diff` still reports the change. No existing test needs editing.

### 4. Page splitting can separate an accent from its letter

- **Current recommendation:** Check `text[end]`.
- **Verdict:** Aligned with changes.
- **Evidence:**
  - User-perceived characters are extended grapheme clusters. Boundaries must not fall:
    - before Extend or ZWJ (GB9);
    - before a SpacingMark or after a Prepend (GB9a, GB9b);
    - inside a Hangul syllable (GB6-8);
    - between CR and LF (GB3);
    - inside an emoji ZWJ sequence (GB11);
    - inside a regional-indicator pair (GB12, GB13).

    Also, "There is no limit on the size of a grapheme cluster" (https://www.unicode.org/reports/tr29/tr29-49.html#Grapheme_Cluster_Boundaries, https://www.unicode.org/reports/tr29/tr29-49.html#Grapheme_Cluster_Boundary_Rules).
  - Python 3.13 `unicodedata` has no segmentation function (https://docs.python.org/3.13/library/unicodedata.html). Python 3.15 adds `unicodedata.iter_graphemes()`, which uses the UAX #29 extended rules (https://docs.python.org/3.15/library/unicodedata.html#unicodedata.iter_graphemes).
  - `regex` 2026.9.29 matches a grapheme with `\X`, per UAX #29 (https://pypi.org/project/regex/). It is not in `uv.lock`, so adding it would trigger supervisor review.
  - Local check: the current code splits `"éx"` at budget 1. It also creates pages that start with a mark for `caf́e`, `näive` and `ab́c`, because the `text[end-1]` loop backs up into the cluster. The minimal fix replaces that loop with a `text[end]` check. It removes every mark-led page, and all 13 existing `test_budget_*` tests pass. It still splits ZWJ emoji, skin-tone modifiers (category Sk), regional-indicator pairs, Hangul jamo, CR LF and Prepend.
  - In 6 sampled 3.13 pages, including the Unicode HOWTO and the `unicodedata` page, visible text held zero marks, ZWJ, regional indicators, jamo, CR or Prepend characters (inferred).
- **Recommended fix:**
  - In `retrieval/budget.py` L47-50, replace the condition with `unicodedata.category(text[end]).startswith("M")`. Replace it; do not add a second loop, or the pager backs up one cluster too far.
  - Keep the forward-advance branch; it guarantees progress.
  - Add the comment `shortcut: combining marks only, switch to unicodedata.iter_graphemes when the floor is 3.15`.
  - The fix must keep `services/whatsnew.py` (L31, L142) working. Its binary search relies on page length growing monotonically with the budget, which the fix keeps.
  - The invariant a pager must guarantee:
    - Pages concatenate to the original text.
    - `next_start_index` is greater than `start_index`.
    - Every `next_start_index` is a cluster boundary.
    - A page's length is at most `max_chars`, unless one cluster is longer.
    - `truncated` is true exactly when `next_start_index` is set, and `next_start_index` equals `start_index + len(page)`.
- **Test that proves it:** `apply_budget("éx", 1)` should return `("é", True, 2)`. Today it returns `"e"`. Add a property loop over strings and budgets: the pages join to the text, and no page starts with category `M*`.
- **Risks and compatibility:** A page can be shorter than the budget, or longer by one cluster. The persistent-cache fingerprint ignores code version (`services/persistent_cache.py` L81-83), so pages cached before the fix keep their old boundaries until the index changes. The impact is low. There is no tool-surface change.

### 5. Synonym expansion runs twice per search

- **Current recommendation:** Precompile the patterns once in `SearchService.__init__` and expand once.
- **Verdict:** Aligned (tidiness only).
- **Evidence:**
  - The `re` docs say that with a few patterns, programs "needn't worry about compiling regular expressions" (https://docs.python.org/3.12/library/re.html#re.compile).
  - The cache is a 512-entry LRU plus a 256-entry FIFO (https://github.com/python/cpython/blob/v3.12.13/Lib/re/__init__.py#L272-L328). Python 3.13.10 has the same sizes (local check). The 31 multi-word concepts out of 150 fit easily.
  - Measured (inferred; local timeit): lazy `expand_synonyms` takes 42 µs and the precompiled version 11 µs. One search goes from about 90 µs to about 20 µs. Baseline cases take about 7.5 ms (`docs/benchmarks/regression-baseline.json`), so the gain is under 1%.
- **Recommended fix:** In `SearchService.__init__`, store `_build_concept_patterns(synonyms)`. Expand once in `search` and pass the result or the patterns into `build_match_expression`. Ship it alone, or with the finding-1 fallback.
- **Test that proves it:** None is needed. Optionally, monkeypatch `_build_concept_patterns` and assert it is not called during `search`.
- **Risks and compatibility:** None. Only internal functions change.

### Sources

- https://sqlite.org/fts5.html#unicode61_tokenizer
- https://sqlite.org/fts5.html#fts5_strings
- https://sqlite.org/fts5.html#fts5_phrases
- https://sqlite.org/fts5.html#fts5_boolean_operators
- https://sqlite.org/fts5.html#the_bm25_function
- https://sqlite.org/fts5.html#synonym_support
- https://sqlite.org/fts5.html#the_trigram_tokenizer
- https://sqlite.org/fts5.html#external_content_tables
- https://sqlite.org/fts5.html#the_rebuild_command
- https://sqlite.org/fts5.html#extending_fts5
- https://modelcontextprotocol.io/specification/2026-07-28/server/tools#error-handling
- https://github.com/modelcontextprotocol/modelcontextprotocol/blob/9850cae8923b0ee3d19a4476f1010b97aa161a55/docs/specification/2026-07-28/server/tools.mdx#L738-L785
- https://github.com/modelcontextprotocol/modelcontextprotocol/blob/9850cae8923b0ee3d19a4476f1010b97aa161a55/docs/specification/2026-07-28/server/tools.mdx#L787-L793
- https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/mcpserver/server.py#L440-L456
- https://www.anthropic.com/engineering/writing-tools-for-agents#optimizing-tool-responses-for-token-efficiency
- https://www.anthropic.com/engineering/writing-tools-for-agents#prompt-engineering-your-tool-descriptions
- https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools#best-practices-for-tool-definitions
- https://www.rfc-editor.org/rfc/rfc9413.html#section-4.1
- https://www.rfc-editor.org/rfc/rfc9413.html#section-5
- https://www.rfc-editor.org/rfc/rfc9413.html#section-5.1
- https://semver.org/#semantic-versioning-specification-semver
- https://github.com/sphinx-doc/sphinx/blob/v3.4.3/sphinx/domains/changeset.py#L28-L38
- https://github.com/sphinx-doc/sphinx/blob/v7.2.6/sphinx/domains/changeset.py#L22-L32
- https://github.com/sphinx-doc/sphinx/blob/v7.3.0/sphinx/domains/changeset.py#L22-L34
- https://github.com/sphinx-doc/sphinx/blob/v8.2.3/sphinx/domains/changeset.py#L25-L37
- https://github.com/sphinx-doc/sphinx/blob/v7.3.0/CHANGES.rst#L46-L48
- https://github.com/sphinx-doc/sphinx/blob/v8.2.3/doc/usage/restructuredtext/directives.rst#L590-L596
- https://github.com/sphinx-doc/sphinx/blob/v8.2.3/sphinx/writers/html5.py#L315-L319
- https://github.com/sphinx-doc/sphinx/blob/v3.4.3/sphinx/writers/html5.py#L196-L200
- https://github.com/sphinx-doc/sphinx/blob/v8.2.3/sphinx/locale/fr/LC_MESSAGES/sphinx.po#L741-L757
- https://github.com/sphinx-doc/sphinxcontrib-serializinghtml/blob/2.0.0/sphinxcontrib/serializinghtml/__init__.py#L38
- https://github.com/python/cpython/blob/v3.10.20/Doc/requirements.txt#L6
- https://github.com/python/cpython/blob/v3.11.15/Doc/requirements.txt#L9
- https://github.com/python/cpython/blob/v3.12.13/Doc/requirements.txt#L10
- https://github.com/python/cpython/blob/v3.13.13/Doc/tools/extensions/changes.py#L20-L84
- https://github.com/python/cpython/blob/v3.15.0/Doc/tools/extensions/changes.py#L81-L109
- https://github.com/python/cpython/blob/v3.11.15/Doc/tools/extensions/pyspecific.py#L435-L490
- https://docs.python.org/3.13/library/asyncio-task.html#asyncio.TaskGroup
- https://docs.python.org/fr/3.13/library/asyncio-task.html
- https://www.unicode.org/reports/tr29/tr29-49.html#Grapheme_Cluster_Boundaries
- https://www.unicode.org/reports/tr29/tr29-49.html#Grapheme_Cluster_Boundary_Rules
- https://docs.python.org/3.13/library/unicodedata.html
- https://docs.python.org/3.15/library/unicodedata.html#unicodedata.iter_graphemes
- https://pypi.org/project/regex/
- https://docs.python.org/3.12/library/re.html#re.compile
- https://github.com/python/cpython/blob/v3.12.13/Lib/re/__init__.py#L272-L328
## Ingestion, storage and runtime

I checked the six findings against primary sources: docutils and Sphinx source at the pinned versions, CPython `Doc/` files at the pinned tags, SQLite docs and source at `version-3.50.4`, distro and installer build scripts, the PyPI JSON API, POSIX Issue 8, the Python docs, Microsoft's Win32 docs, and the MCP Python SDK at tag `v2.3.0` (commit `2118f14`). I also ran local experiments with SQLite 3.50.4 and Python 3.13.10 on macOS. No repo file was changed. The scripts are in `<session scratchpad>/research/scripts/`. In short: (1) The id-copy idea is right, but blocks inside an API entry should attach to that entry's `dt` anchor. On the real 3.13 and 3.10 pages, 32 of 32 and 26 of 26 blocks fall back today. (2) Delete the extra. Every mainstream Python build ships FTS5, the extra was never wired in, and `pysqlite3-binary` has been replaced by `pysqlite3` wheels. (3) Closing first is the SQLite-blessed cleanup. Also switch the published file to `journal_mode=DELETE`, so readers never create `-wal`/`-shm` files. (4) The hard-link swap closes the gap. It needs a temp name plus a fallback copy, and Windows still cannot replace an open index. (5) Cache the detection at startup. (6) Delete the dead code, but snapshot what `tools/list` actually serves (it has already drifted from the committed snapshot). Do the schema and test deletions in one supervisor-review PR.

### 1. Code examples are filed under the page's first section

- **Current recommendation:** Share `extract_sections`' copy of the section id onto the heading with `extract_code_blocks`.
- **Verdict:** Aligned with changes.
- **Evidence:**
  - All pinned toolchains render the body with Sphinx's HTML5 translator. Sphinx 3.4.3 picks `HTML5Translator` unless `html4_writer` is set, and that setting defaults to off (https://github.com/sphinx-doc/sphinx/blob/v3.4.3/sphinx/builders/html/__init__.py#L322-L326, #L1245). CPython 3.10's `conf.py` does not set it (https://github.com/python/cpython/blob/v3.10.20/Doc/conf.py). Sphinx 7.2.6 and 8.2.3 always use it (https://github.com/sphinx-doc/sphinx/blob/v7.2.6/sphinx/builders/html/__init__.py#L161, https://github.com/sphinx-doc/sphinx/blob/v8.2.3/sphinx/builders/html/__init__.py#L115).
  - The JSON builder subclasses the HTML builder and writes the same `body` fragment into `.fjson` (https://github.com/sphinx-doc/sphinx/blob/v3.4.3/sphinx/builders/html/__init__.py#L578, #L599; https://github.com/sphinx-doc/sphinxcontrib-serializinghtml/blob/1.1.5/sphinxcontrib/serializinghtml/__init__.py#L35, #L149-L160). CPython 3.10 constrains serializinghtml to `<1.1.10` (https://github.com/python/cpython/blob/v3.10.20/Doc/constraints.txt#L21).
  - Docutils 0.17 (2021-04-03) switched the HTML5 writer to `<section>` elements (https://github.com/docutils/docutils/blob/f2cf464d21bd5a9f632fffceac53d5cff0a101c2/docutils/HISTORY.rst#L1480, #L1556-L1559). The writer puts the section's ids on the `<section>` tag (https://github.com/docutils/docutils/blob/f2cf464d21bd5a9f632fffceac53d5cff0a101c2/docutils/docutils/writers/html5_polyglot/__init__.py#L368-L377). Extra ids become empty `<span id>` children (https://github.com/docutils/docutils/blob/f2cf464d21bd5a9f632fffceac53d5cff0a101c2/docutils/docutils/writers/_html_base.py#L577-L595). The heading is built from the title node, which carries no id (same file, #L1739-L1790). Sphinx's permalink also points at the parent section's id (https://github.com/sphinx-doc/sphinx/blob/v3.4.3/sphinx/writers/html5.py#L362-L373).
  - Every pinned docutils is 0.17 or newer. CPython 3.10 pins `sphinx==3.4.3` and `docutils==0.17.1` (https://github.com/python/cpython/blob/v3.10.20/Doc/requirements.txt#L6-L10). Sphinx 7.2.6 needs docutils 0.18.1 or newer, and Sphinx 8.2.3 needs 0.20 or newer (https://github.com/sphinx-doc/sphinx/blob/v7.2.6/pyproject.toml#L66, https://github.com/sphinx-doc/sphinx/blob/v8.2.3/pyproject.toml#L78). `write_json_build_requirements` does not filter docutils (`sphinx_json.py` L41-L45). So no supported build emits `<h2 id>`, which the fixture uses (inferred).
  - Live pages agree. The 3.10 asyncio-task page (its generator meta says Docutils 0.17.1 and Sphinx 3.4.3) has 15 `<section id>` elements and no `div.section`. The 3.13 page has id-less `<h1>`/`<h2>` inside `<section id>` (https://docs.python.org/3.10/library/asyncio-task.html, https://docs.python.org/3.13/library/asyncio-task.html, fetched 2026-10-30).
  - Reproduced: running the project's `extract_code_blocks` on the real page bodies gives an empty `section_anchor` for 32 of 32 blocks (3.13) and 26 of 26 blocks (3.10). So every block falls back to the first section (`sphinx_json.py` L527-L533). An ancestor walk maps every block to an anchor that `extract_sections` produced (`check_code_block_anchors.py`).
  - API entries: Sphinx renders an object description as `<dl>`, each signature as `<dt>` carrying the ids, and the description as `<dd>` (https://github.com/sphinx-doc/sphinx/blob/v3.4.3/sphinx/writers/html5.py#L94-L102, #L190-L191; https://github.com/sphinx-doc/sphinx/blob/v7.2.6/sphinx/writers/html5.py#L92-L94, #L112-L113; https://github.com/sphinx-doc/sphinx/blob/v8.2.3/sphinx/writers/html5.py#L88-L90, #L108-L109). Sphinx defines `desc_content` as the content of an object description, shared by the signatures in that `desc` (https://www.sphinx-doc.org/en/master/extdev/nodes.html#sphinx.addnodes.desc_content). So a block inside a `dd` documents that object. `extract_sections` already makes each `dt[id]` a section (`sphinx_json.py` L356-L373) (inferred).
  - The docutils doctree allows body elements before subsections in a section, but not after them (https://docutils.sourceforge.io/docs/ref/doctree.html#section). So, after the id copy, the previous heading is always the enclosing section. The current recommendation is therefore correct at section level. It only misses the `dd` case (inferred).
- **Recommended fix:** In `extract_code_blocks`, replace the loop at L419-L426 with one small helper. Walk `div.parents`. At a `dd`, return the `dt[id]` that precedes it in the same `dl`. At a `section` (or `div.section`) with an id, return that id. If nothing matches, keep today's `find_previous(h1-h6, id=True)` as the legacy fallback. The fallback keeps `tests/test_ingestion.py::test_section_anchor_association` green on the old fixture. Leave `extract_sections` and the first-section fallback in `ingest_fjson_file` as they are. Add a new fixture copied from real `<section>`/`dl` output, and do not edit `sample_library.fjson`. It must not break: anchors that match `extract_sections`, the `examples.section_id` foreign keys, or the old-fixture tests.
- **Test that proves it:** `extract_code_blocks('<section id="task-groups"><h2>Task Groups</h2><div class="highlight-python3"><pre>a = 1</pre></div><dl class="py class"><dt id="asyncio.TaskGroup">class TaskGroup</dt><dd><div class="highlight-python3"><pre>b = 2</pre></div></dd></dl></section>')` must return anchors `["task-groups", "asyncio.TaskGroup"]`. Today it returns `["", ""]`. The id copy alone would return `["task-groups", "task-groups"]`.
- **Risks and compatibility:** Users must rebuild the index to see it. There is no schema or tool-surface change. `kind="example"` hits will point at API-entry anchors, which `get_docs` already serves as `dt` sections. Rerun the retrieval regression suite, because example-to-section joins change.

### 2. The documented FTS5 fallback does nothing

- **Current recommendation:** Delete the `pysqlite3` extra and its hint, point users to `uv python install` or python.org, and let `doctor` call `assert_fts5_available`.
- **Verdict:** Aligned.
- **Evidence:**
  - The extra cannot work. No module imports `pysqlite3`. Only `pyproject.toml`, `uv.lock`, `README.md`, `docs/architecture/DESIGN.md` L265, and the hints in `db.py` and `__main__.py` mention it (repo grep).
  - SQLite has shipped FTS5 in the amalgamation since 3.9.0. It is enabled by `SQLITE_ENABLE_FTS5` or `--enable-fts5`, and the source-tree configure leaves it off by default (https://www.sqlite.org/fts5.html#building_fts5_as_part_of_sqlite, https://www.sqlite.org/compile.html#enable_fts5). So a Python without FTS5 has a custom-built SQLite (inferred).
  - python.org: the macOS installer compiles SQLite with `-DSQLITE_ENABLE_FTS5` (https://github.com/python/cpython/blob/v3.13.13/Mac/BuildScript/build-installer.py#L362-L366, and the same lines at v3.12.13). The Windows build defines `SQLITE_ENABLE_FTS5` (https://github.com/python/cpython/blob/v3.13.13/PCbuild/sqlite3.vcxproj#L101).
  - `uv python install` uses python-build-standalone (https://docs.astral.sh/uv/concepts/python-versions/#cpython-distributions). Its Unix build passes `-DSQLITE_ENABLE_FTS5` (https://github.com/astral-sh/python-build-standalone/blob/20261026/cpython-unix/build-sqlite.sh#L26-L31). Its Windows patch keeps FTS5 and adds FTS3 (https://github.com/astral-sh/python-build-standalone/blob/20261026/cpython-windows/patch-sqlite-build-flags.patch).
  - Debian 12, Debian 13 and Ubuntu 24.04 build libsqlite3 with `--enable-fts5` (https://sources.debian.org/data/main/s/sqlite3/3.40.1-2+deb12u2/debian/rules, https://sources.debian.org/data/main/s/sqlite3/3.46.1-7+deb13u2/debian/rules, https://git.launchpad.net/ubuntu/+source/sqlite3/plain/debian/rules?h=ubuntu/noble-updates). Debian's Python build-depends on `libsqlite3-dev`, so it uses that library (https://sources.debian.org/data/main/p/python3.13/3.13.5-2+deb13u5/debian/control) (inferred).
  - Fedora: src.fedoraproject.org answered with a bot-check page, which I did not bypass. CentOS Stream 9 and 10 build with `--enable-fts5`, and their changelog carries a 2017 Fedora entry that turned it on (https://gitlab.com/redhat/centos-stream/rpms/sqlite/-/raw/c9s/sqlite.spec, https://gitlab.com/redhat/centos-stream/rpms/sqlite/-/raw/c10s/sqlite.spec). Fedora itself is inferred from that, not verified.
  - Alpine builds with `--enable-fts5` (https://github.com/alpinelinux/aports/blob/f8081b25590214e2695a4faa77c7892bd902a4e8/main/sqlite/APKBUILD#L96-L98). Homebrew sets `-DSQLITE_ENABLE_FTS5=1`, and `python@3.13` depends on that formula (https://github.com/Homebrew/homebrew-core/blob/8945c3086b567862234ff0630f9274003b7e0726/Formula/s/sqlite.rb#L45-L47, https://github.com/Homebrew/homebrew-core/blob/d6dbf6fae5c1e5983b33191f918571cfbe031687/Formula/p/python@3.13.rb#L29). conda-forge enables FTS5 on Unix and Windows, and its Python unvendors SQLite and uses `libsqlite` (https://github.com/conda-forge/sqlite-feedstock/blob/395e516ef8367a8d462160e8fc80386c2acf36e8/recipe/build.sh#L18-L22, https://github.com/conda-forge/sqlite-feedstock/blob/395e516ef8367a8d462160e8fc80386c2acf36e8/recipe/bld.bat#L9-L13, https://github.com/conda-forge/python-feedstock/blob/aa3d279f76e5a4faabf8c08a4aeaaa138d96b639/recipe/meta.yaml#L74, #L209). The Anaconda defaults channel also enables it (https://github.com/AnacondaRecipes/sqlite-feedstock/blob/b22f11d23ab28212b70e393af3f5f1fe019d868e/recipe/build.sh#L18).
  - `pysqlite3-binary` is stale. Its last release is 0.5.4.post2, from 2025-12-03. It has seven wheels, all manylinux2014 x86_64 for CPython 3.8 to 3.14, and no sdist (https://pypi.org/pypi/pysqlite3-binary/json). The pysqlite3 README now says self-contained wheels ship in `pysqlite3` from 0.6.0, and that new work happens in cysqlite (https://github.com/coleifer/pysqlite3/blob/2121dfb570d60455d4bc3b10b8a2fd36c805e463/README.md#L4-L6, #L23-L24). `pysqlite3` 0.6.1 (2026-10-04) ships wheels for macOS arm64, manylinux_2_28 x86_64, musllinux x86_64, win32 and win_amd64, for CPython 3.9 to 3.14. It has no Linux aarch64 wheel (https://pypi.org/pypi/pysqlite3/json).
  - Swap mechanics: the README shows only install commands, with no import pattern. The package re-exports `pysqlite3.dbapi2`, which star-imports its own compiled `pysqlite3._sqlite3` (https://github.com/coleifer/pysqlite3/blob/2121dfb570d60455d4bc3b10b8a2fd36c805e463/pysqlite3/dbapi2.py#L28). So it has its own exception classes and `Row` type (inferred). CPython's `sqlite3.Row` accepts only the stdlib cursor type (https://github.com/python/cpython/blob/v3.13.13/Modules/_sqlite/row.c#L72). Mixing the two modules therefore raises `TypeError`, and `except sqlite3.OperationalError` misses pysqlite3 errors (inferred). The repo imports `sqlite3` in 16 modules, with 6 `sqlite3.Row` uses and 9 `except sqlite3.*` clauses. A correct swap would have to alias the module before any of those imports, which is a global side effect (inferred).
- **Recommended fix:** Remove `[project.optional-dependencies] pysqlite3` (`pyproject.toml` L37-L38) and regenerate `uv.lock`. In `assert_fts5_available` (`db.py` L100-L104), drop the Linux branch and use one hint: install a Python whose SQLite has FTS5 (`uv python install`, or python.org). Note that distro, Homebrew and conda Pythons already include FTS5. In `doctor` (`__main__.py` L516-L537), call `assert_fts5_available(sqlite3.connect(":memory:"))`, catch `FTS5UnavailableError`, and report `str(e)`. Update `README.md` L342-L348 and `DESIGN.md` L265. It must not break: the read-only `compile_options` path in `assert_fts5_available`, the `FTS5UnavailableError` type, or doctor's probe names.
- **Test that proves it:** Monkeypatch `platform.system` to `Linux` and `platform.machine` to `x86_64`. Pass a stub connection whose `execute` raises `sqlite3.OperationalError("no such module: fts5")`. Assert that `"pysqlite3"` does not appear in the `FTS5UnavailableError` message. This fails today. A second test makes `assert_fts5_available` raise inside `doctor` and checks that the message appears in the output. This fails today because doctor runs its own probe.
- **Risks and compatibility:** `[project]` is forbidden territory (`AGENT-EXECUTION-PIPELINE.md` §2), so this needs a supervisor-review PR. Scripts that install `[pysqlite3]` should get a warning, not a failure, from installers (inferred; not checked against pip or uv docs). No index rebuild is needed and the tool surface does not change.

### 3. Failed builds leave files in the cache folder

- **Current recommendation:** Close the connection first, then delete `.db`, `-wal` and `-shm`. On a smoke-test failure, delete the build or log its path.
- **Verdict:** Aligned with changes.
- **Evidence:**
  - SQLite deletes the WAL when the last connection closes. That connection checkpoints, then removes both the WAL and the shared-memory file (https://www.sqlite.org/wal.html#the_wal_file, https://www.sqlite.org/wal.html#avoiding_excessively_large_wal_files). The same section says the only safe way to remove a WAL is to open the database and then close it.
  - Reproduced: unlinking `a.db` before `close()` leaves `a.db-shm` and `a.db-wal`. Closing first and then unlinking leaves nothing (`wal_sidecars.py`). That matches the 11 orphan pairs. A likely source is the "no versions succeeded" path, for example an offline auto-build (`__main__.py` L394-L399) (inferred).
  - WAL mode is stored in the file and persists (https://www.sqlite.org/wal.html#persistence_of_wal_mode). The build sets it on every read-write connection (`db.py` L42-L45). So the published `index.db` is a WAL file, with header bytes 18-19 equal to 2 (reproduced).
  - A `mode=ro` reader of a WAL file creates a 0-byte `-wal` and a 32 KiB `-shm`, and both stay after it closes (reproduced). Even a cleanly closed, fully checkpointed WAL file triggers this (reproduced). SQLite says a read-only WAL database can only be read if the sidecars exist, the directory is writable, or `immutable` is used. It recommends switching to `journal_mode=DELETE` before shipping a database as read-only (https://www.sqlite.org/wal.html#read_only_databases).
  - `PRAGMA journal_mode=DELETE` resets the file-format numbers to 1 (https://www.sqlite.org/wal.html#backwards_compatibility). The pragma returns the old mode if it could not switch, and it cannot switch inside a transaction (https://www.sqlite.org/pragma.html#pragma_journal_mode). `wal_checkpoint(TRUNCATE)` only empties the WAL, and the file stays in WAL mode (https://www.sqlite.org/pragma.html#pragma_wal_checkpoint).
  - Reproduced: after a smoke-test reader left sidecars, a read-write connection that commits and then runs `PRAGMA journal_mode=DELETE` removed both sidecars at once. The header became 1/1. Later `mode=ro` readers created no files.
  - `immutable=1` makes SQLite skip all locking and change detection. If the file changes anyway, queries may return wrong results or `SQLITE_CORRUPT` (https://www.sqlite.org/uri.html).
  - Mismatched sidecars: SQLite says renaming or unlinking an open database and then creating a new file under the old name is undefined, because both files share the name-based journal or WAL (https://www.sqlite.org/howtocorrupt.html#_unlinking_or_renaming_a_database_file_while_in_use). A journal must stay with its own database (https://www.sqlite.org/howtocorrupt.html#_mispairing_database_files_and_hot_journals_). Today's publish does this rename while servers hold `index.db` open (`publish.py` L373-L377) (inferred).
  - Why it is harmless in practice, and how to keep it so: a pager opens WAL mode when the `-wal` file "exists" (https://github.com/sqlite/sqlite/blob/version-3.50.4/src/pager.c#L3319-L3364). The Unix and Windows VFSes count a zero-length file as absent (https://github.com/sqlite/sqlite/blob/version-3.50.4/src/os_unix.c#L6596-L6599, https://github.com/sqlite/sqlite/blob/version-3.50.4/src/os_win.c#L5873-L5880). Readers only ever create empty WAL files, and nothing writes to `index.db`. Reproduced: with an empty stale `-wal` beside a DELETE-mode file, the reader stayed in `delete` mode and created no `-shm` (inferred and reproduced). Today the new file is WAL mode, so new readers do attach to the old inode's `-shm`. That causes no corruption while the WAL is empty, but it is exactly the case SQLite warns about (inferred).
- **Recommended fix (safest protocol):**
  1. In `build_index`, wrap the build so any failure (including "no versions", FTS5 errors and Ctrl-C) closes the connection and then runs `build_db_path.unlink(missing_ok=True)`. Close first. An extra `unlink(missing_ok=True)` of `-wal`/`-shm` is harmless but redundant after a clean close.
  2. In `publish_index`, in the last read-write block (the `published` update), run `PRAGMA journal_mode = DELETE` before `close()`. Abort the publish if it does not return `delete`. This has to be the last read-write open, because `get_readwrite_connection` turns WAL back on.
  3. On a smoke-test failure, close the build, delete it, and log the smoke messages at ERROR. The `failed` row is lost with the file, which is fine. If the owner wants to keep failed builds for debugging, log the full path at ERROR instead (inferred).
  4. Keep readers on `?mode=ro`. Do not add `immutable=1` by default. It is valid for a rename-only protocol. But if a user runs `cp` over `index.db`, open servers could return wrong results or `SQLITE_CORRUPT` with no warning. DELETE mode already removes the sidecar problem (inferred).
  5. Optional: sweep `build-*.db*` files older than a day at the start of `build-index`. The server's 180 s auto-build timeout kills the child without cleanup (`server.py` L103) (inferred).
  It must not break: the smoke tests, `rollback()`, or the stat-based `PersistentDocsCache` fingerprint. Side note: `artifact_hash` is computed before later writes (`publish.py` L442 and after), so it never matches the published bytes (inferred).
- **Test that proves it:** (a) Monkeypatch `get_cache_dir` to `tmp_path` and make `ingest_inventory` raise. Run `build-index --versions 3.13 --skip-content` through `CliRunner` and assert exit code 1 and `list(tmp_path.glob("build-*")) == []`. This fails today because `-wal`/`-shm` remain. (b) After a successful `publish_index` on a tiny valid build, assert `index.db` bytes `[18:20] == b"\x01\x01"`, then open `get_readonly_connection` and query. Assert that no `index.db-wal` or `index.db-shm` exists. This fails today.
- **Risks and compatibility:** Existing WAL-mode indexes keep working, and the first new publish switches the mode. `.previous` stays in WAL mode until it is replaced, and rollback still works. Rollback-mode readers no longer need a writable cache folder (inferred). There is no schema, tool-surface or rebuild requirement.

### 4. `index.db` is briefly missing during publish

- **Current recommendation:** Back up with a hard link (`os.link`), then `os.replace`, so the path always exists.
- **Verdict:** Aligned with changes.
- **Evidence:**
  - POSIX `rename()` replaces an existing `new`. Other threads keep seeing an entry named `new` throughout, pointing at the old or the new file (https://pubs.opengroup.org/onlinepubs/9799919799/functions/rename.html). Python's `os.replace` is atomic when it succeeds (https://docs.python.org/3/library/os.html#os.replace). On Windows, `os.rename` always fails if the destination exists (https://docs.python.org/3/library/os.html#os.rename).
  - POSIX `link()` creates the new entry atomically and fails with `EEXIST` if it already exists (https://pubs.opengroup.org/onlinepubs/9799919799/functions/link.html). `os.link` works on Unix and on Windows since 3.2 (https://docs.python.org/3/library/os.html#os.link). CPython uses `CreateHardLinkW`, and `MoveFileExW` with `MOVEFILE_REPLACE_EXISTING`, on Windows (https://github.com/python/cpython/blob/v3.13.13/Modules/posixmodule.c#L4296, #L5805).
  - Today `os.rename(index, previous)` runs before `os.replace(build, index)` (`publish.py` L373, L377). A server starting in that gap auto-builds (`server.py` L134, L72-L118) and may publish a symbol-only index over the full one (inferred).
  - Windows: SQLite opens database files with `FILE_SHARE_READ | FILE_SHARE_WRITE` only (https://github.com/sqlite/sqlite/blob/version-3.50.4/src/os_win.c#L5541). Without `FILE_SHARE_DELETE`, no other open can get delete access, and delete access covers renames too (https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew). So on Windows, renaming or replacing `index.db` while a server has it open fails, both today and with the fix (inferred). SQLite also notes that Windows blocks unlinking an open database (https://www.sqlite.org/howtocorrupt.html#_unlinking_or_renaming_a_database_file_while_in_use).
  - Locks: `O_CREAT|O_EXCL` gives an atomic create (https://pubs.opengroup.org/onlinepubs/9799919799/functions/open.html), but a crashed holder leaves a stale lock file (inferred). `flock` locks go away when all descriptors close (https://man7.org/linux/man-pages/man2/flock.2.html). Python has `fcntl.flock` on Unix and `msvcrt.locking` on Windows (https://docs.python.org/3/library/fcntl.html#fcntl.flock, https://docs.python.org/3/library/msvcrt.html#msvcrt.locking). A lock only serializes builders. Servers check `exists()` without taking it, so a lock cannot replace the no-gap swap (inferred).
  - Reproduced: `os.link(index, previous)` followed by `os.replace(build, index)` keeps `index.db` present and keeps the old content in `.previous`.
- **Recommended fix:** In `atomic_swap`, when `target_path` exists, set `tmp = previous.with_name(f"{previous.name}.{os.getpid()}.tmp")` and run `tmp.unlink(missing_ok=True)`. Then call `os.link(target_path, tmp)`, and fall back to `shutil.copy2` on `OSError` for filesystems without hard links. Then `os.replace(tmp, previous)`, then `os.replace(new_db_path, target_path)`. Linking to a unique temp name and then replacing avoids the `EEXIST` race between two publishers (inferred). On Windows, catch `PermissionError` from the final replace and log that MCP clients must be stopped, with the kept build path (inferred). Skip the build lock for now. If concurrent builds become common, hold a non-blocking advisory lock on `cache_dir/build.lock` across build and publish. It must not break: `rollback()`, the return value, or `tests/test_publish.py`.
- **Test that proves it:** Wrap `publish.os.replace` with a check that asserts `target.exists()` whenever `dst == target`. Then call `atomic_swap(build, target)` with an existing target, and assert the new bytes at `target` and the old bytes at `index.db.previous`. This fails today because the target is gone at the final replace.
- **Risks and compatibility:** `.previous` shares the old inode, which is safe because nothing writes `index.db` in place (inferred). The copy fallback costs one copy of the file. Windows still needs servers stopped before publishing, as it does today. There is no schema or tool-surface change.

### 5. `detect_python_version` blocks the server on every call

- **Current recommendation:** Store the raw version at startup and return stored values.
- **Verdict:** Aligned.
- **Evidence:**
  - The MCP SDK v2.3.0 awaits `async def` tools directly on the event loop. It runs plain `def` tools through `anyio.to_thread.run_sync` (https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/mcpserver/utilities/func_metadata.py#L149-L164, https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/mcpserver/tools/base.py#L86, #L179). The SDK docs say a sync tool runs in a thread so that it does not block the server (https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/servers/tools.md#L135-L139).
  - The Python docs say not to call blocking code directly from coroutines. They point to executors instead (https://docs.python.org/3/library/asyncio-dev.html#running-blocking-code), and debug mode logs callbacks that take over 100 ms (https://docs.python.org/3/library/asyncio-dev.html#debug-mode). `asyncio.to_thread` exists for IO-bound calls that would otherwise block the loop (https://docs.python.org/3/library/asyncio-task.html#asyncio.to_thread).
  - The tool runs `subprocess.run(["python3", "--version"], timeout=5)` when there is no `.python-version` (`detection.py` L50-L66). It does so inside an `async def` (`server.py` L379-L397). The DB tools share the loop on purpose (`tests/test_services.py` L460-L472), so every tool can stall for up to 5 s (inferred).
  - Re-detection is not needed for correctness. `get_docs` uses the startup match (`server.py` L337-L338), and the tool itself returns the startup match next to a freshly detected raw version (L386-L397). A mid-session change would make the tool disagree with `get_docs` (inferred). The project already uses restart-to-refresh for the index and caches (`publish.py` L407, ADR-007).
  - Alternatives: `asyncio.to_thread` or `anyio.to_thread.run_sync` would unblock the loop, but they still spawn a process per call and keep the mismatch. `asyncio.create_subprocess_exec` adds async plumbing and needs a Proactor loop on Windows (https://docs.python.org/3/library/asyncio-subprocess.html#asyncio.create_subprocess_exec). AnyIO documents the same thread-offload pattern (https://anyio.readthedocs.io/en/stable/threads.html). Caching is the smallest correct option (inferred).
- **Recommended fix:** Add `detected_python_raw: str = ""` to `AppContext`. Set it from the single `detect_python_version()` call at startup (`server.py` L173). In the tool, return `detected_version=app_ctx.detected_python_raw`, `source=app_ctx.detected_python_source or ""`, and the stored match. Delete the in-function import and the `_detect()` call. Keep `async def`, because `test_docs_tools_are_async` asserts it and changing that test is forbidden territory. It must not break: the return shape or `test_detection.py`.
- **Test that proves it:** Monkeypatch `mcp_server_python_docs.detection.detect_python_version` to call `pytest.fail`. Get `create_server()._tool_manager._tools["detect_python_version"].fn`, then await it with a stub `ctx` whose `lifespan_context` holds raw `"3.9"`, source `"python3 in PATH"` and match `None`. Assert the four result fields. This fails today because the tool calls the patched function.
- **Risks and compatibility:** A `.python-version` edited mid-session is picked up on restart, the same as `get_docs` today. There is no tool-surface or schema change.

### 6. Code and schema nothing uses at runtime

- **Current recommendation:** Delete the unused models, the `synonyms` and `redirects` tables, the `zstd-dict-v1` codec and the dead ranker branches, together with their fixtures and tests. Snapshot the real served input schema instead.
- **Verdict:** Aligned with changes.
- **Evidence:**
  - Served schema: `MCPServer` builds each tool's `parameters` with `arg_model.model_json_schema(by_alias=True)` from the function signature (https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/mcpserver/tools/base.py#L107). `list_tools()` returns them as `input_schema` (https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/mcpserver/server.py#L516-L531), and the `tools/list` handler sends exactly that list (same file, #L427-L430). The wire names come from a camelCase alias generator (https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp-types/mcp_types/_types.py#L48). The spec defines `inputSchema` and `outputSchema` as the contract returned by `tools/list` (https://modelcontextprotocol.io/specification/2026-07-28/server/tools).
  - Drift is real. Reproduced: `await create_server().list_tools()` differs from the committed fixtures. `get_docs.version` has a different description, and the titles differ (`get_docsArguments` versus `GetDocsInput`) (`served_vs_snapshot.py`). The snapshot test passes while guarding a schema that clients never see (inferred).
  - Testing guidance: the SDK's testing page uses the in-memory `Client(mcp)` and snapshots whole results (https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/get-started/testing.md#L3-L5, #L41-L66). Software Engineering at Google says to test through public APIs, the way users call the system (https://abseil.io/resources/swe-book/html/ch12.html). So snapshot what clients receive (inferred).
  - Dead code: Fowler describes the ongoing "cost of carry" of code built for presumed needs (https://martinfowler.com/bliki/Yagni.html). Google treats code as a liability, not an asset (https://abseil.io/resources/swe-book/html/ch15.html). ADR-007 says `zstd-dict-v1` is test-only and unusable in production (`docs/architecture/ADR-007-cache.md` L102-L105, L149-L151). Rows with an unknown codec decode as cache misses (`services/persistent_cache.py` L134-L141), so removing the codec cannot break an existing cache (inferred).
  - Schema: `CREATE TABLE IF NOT EXISTS` does nothing when the table exists (https://www.sqlite.org/lang_createtable.html). Each build starts from a new timestamped file (`publish.py` L40). So dropped tables simply disappear from new indexes, and old indexes keep them harmlessly (inferred). Columns are different. `ranker.py` selects `document_id, section_id` from `symbols` at L269 and L392. Those SELECTs and the branches at L52-L73 must go in the same change as the columns, or new indexes fail with "no such column" (inferred). `symbols_fts` only uses `qualified_name` and `module` (`schema.sql`).
  - Versioning: `PRAGMA user_version` is a header integer at offset 60 that SQLite never uses itself (https://www.sqlite.org/pragma.html#pragma_user_version). `application_id` at offset 68 marks the file type (https://www.sqlite.org/pragma.html#pragma_application_id). Existing indexes read 0.
- **Recommended fix:** Make one supervisor-review PR, because it touches `schema.sql` and deletes tests.
  - Replace the input pairs in `tests/test_schema_snapshot.py` with one served snapshot: `[t.model_dump(by_alias=True, mode="json", exclude_none=True) for t in anyio.run(create_server().list_tools)]`, compared with `tests/fixtures/tools-list.json` and keeping `UPDATE_SCHEMAS=1`. Use `server.list_tools()` instead of `Client(mcp)`, because the client runs the lifespan, which needs or auto-builds an index. This snapshot also covers all seven output schemas and the annotations.
  - Delete `SearchDocsInput`, `GetDocsInput` and their two fixtures. Delete `populate_synonyms` and its call (`__main__.py` L402-L403). Delete `zstd-dict-v1` and update `test_codec.py` and the ADR-007 text, but keep the `codec` column for forward compatibility. Delete the ranker branches and the two selected columns.
  - In `schema.sql`, drop `synonyms`, `redirects` and the two `symbols` columns, and add `PRAGMA user_version = 1;`. Add a startup check only when a future change makes old indexes unreadable. Then compare `user_version` in `app_lifespan`, treat 0 as pre-versioning, and fail with "run build-index" (inferred, following YAGNI).
  It must not break: `tools/list` output (the new snapshot pins it), FTS rebuilds, the retrieval regression suite, or existing installed indexes.
- **Test that proves it:** The new `test_tools_list_snapshot` fails today if its fixture is generated from the old Pydantic models: the `version` description and the titles differ (reproduced). After regenerating the fixture from `list_tools()`, it passes. Editing any `Field(description=...)` in `server.py` then makes it fail, which the old test never did.
- **Risks and compatibility:** This touches forbidden territory: `schema.sql` and test deletions. The tool surface does not change, and the snapshot now proves that. Old indexes keep unused tables until they are rebuilt. Running `symbols` queries on old indexes still works, because the code only stops reading columns.

### Sources

- https://github.com/sphinx-doc/sphinx/blob/v3.4.3/sphinx/builders/html/__init__.py
- https://github.com/sphinx-doc/sphinx/blob/v7.2.6/sphinx/builders/html/__init__.py
- https://github.com/sphinx-doc/sphinx/blob/v8.2.3/sphinx/builders/html/__init__.py
- https://github.com/sphinx-doc/sphinx/blob/v3.4.3/sphinx/writers/html5.py
- https://github.com/sphinx-doc/sphinx/blob/v7.2.6/sphinx/writers/html5.py
- https://github.com/sphinx-doc/sphinx/blob/v8.2.3/sphinx/writers/html5.py
- https://github.com/sphinx-doc/sphinx/blob/v7.2.6/pyproject.toml#L66
- https://github.com/sphinx-doc/sphinx/blob/v8.2.3/pyproject.toml#L78
- https://github.com/sphinx-doc/sphinxcontrib-serializinghtml/blob/1.1.5/sphinxcontrib/serializinghtml/__init__.py
- https://www.sphinx-doc.org/en/master/extdev/nodes.html#sphinx.addnodes.desc_content
- https://github.com/docutils/docutils/blob/f2cf464d21bd5a9f632fffceac53d5cff0a101c2/docutils/HISTORY.rst#L1480
- https://github.com/docutils/docutils/blob/f2cf464d21bd5a9f632fffceac53d5cff0a101c2/docutils/docutils/writers/html5_polyglot/__init__.py#L368-L377
- https://github.com/docutils/docutils/blob/f2cf464d21bd5a9f632fffceac53d5cff0a101c2/docutils/docutils/writers/_html_base.py#L577-L595
- https://docutils.sourceforge.io/docs/ref/doctree.html#section
- https://github.com/python/cpython/blob/v3.10.20/Doc/requirements.txt#L6-L10
- https://github.com/python/cpython/blob/v3.10.20/Doc/constraints.txt#L21
- https://github.com/python/cpython/blob/v3.10.20/Doc/conf.py
- https://docs.python.org/3.10/library/asyncio-task.html
- https://docs.python.org/3.13/library/asyncio-task.html
- https://www.sqlite.org/fts5.html#building_fts5_as_part_of_sqlite
- https://www.sqlite.org/compile.html#enable_fts5
- https://github.com/python/cpython/blob/v3.13.13/Mac/BuildScript/build-installer.py#L362-L366
- https://github.com/python/cpython/blob/v3.12.13/Mac/BuildScript/build-installer.py#L362-L366
- https://github.com/python/cpython/blob/v3.13.13/PCbuild/sqlite3.vcxproj#L101
- https://docs.astral.sh/uv/concepts/python-versions/#cpython-distributions
- https://github.com/astral-sh/python-build-standalone/blob/20261026/cpython-unix/build-sqlite.sh#L26-L31
- https://github.com/astral-sh/python-build-standalone/blob/20261026/cpython-windows/patch-sqlite-build-flags.patch
- https://sources.debian.org/data/main/s/sqlite3/3.40.1-2+deb12u2/debian/rules
- https://sources.debian.org/data/main/s/sqlite3/3.46.1-7+deb13u2/debian/rules
- https://sources.debian.org/data/main/p/python3.13/3.13.5-2+deb13u5/debian/control
- https://git.launchpad.net/ubuntu/+source/sqlite3/plain/debian/rules?h=ubuntu/noble-updates
- https://gitlab.com/redhat/centos-stream/rpms/sqlite/-/raw/c9s/sqlite.spec
- https://gitlab.com/redhat/centos-stream/rpms/sqlite/-/raw/c10s/sqlite.spec
- https://src.fedoraproject.org/rpms/sqlite/raw/rawhide/f/sqlite.spec (bot-check page, not read)
- https://github.com/alpinelinux/aports/blob/f8081b25590214e2695a4faa77c7892bd902a4e8/main/sqlite/APKBUILD#L96-L98
- https://github.com/Homebrew/homebrew-core/blob/8945c3086b567862234ff0630f9274003b7e0726/Formula/s/sqlite.rb#L45-L47
- https://github.com/Homebrew/homebrew-core/blob/d6dbf6fae5c1e5983b33191f918571cfbe031687/Formula/p/python@3.13.rb#L29
- https://github.com/conda-forge/sqlite-feedstock/blob/395e516ef8367a8d462160e8fc80386c2acf36e8/recipe/build.sh#L18-L22
- https://github.com/conda-forge/sqlite-feedstock/blob/395e516ef8367a8d462160e8fc80386c2acf36e8/recipe/bld.bat#L9-L13
- https://github.com/conda-forge/python-feedstock/blob/aa3d279f76e5a4faabf8c08a4aeaaa138d96b639/recipe/meta.yaml#L209
- https://github.com/AnacondaRecipes/sqlite-feedstock/blob/b22f11d23ab28212b70e393af3f5f1fe019d868e/recipe/build.sh#L18
- https://pypi.org/pypi/pysqlite3-binary/json
- https://pypi.org/pypi/pysqlite3/json
- https://github.com/coleifer/pysqlite3/blob/2121dfb570d60455d4bc3b10b8a2fd36c805e463/README.md#L4-L6
- https://github.com/coleifer/pysqlite3/blob/2121dfb570d60455d4bc3b10b8a2fd36c805e463/pysqlite3/dbapi2.py#L28
- https://github.com/python/cpython/blob/v3.13.13/Modules/_sqlite/row.c#L72
- https://www.sqlite.org/wal.html#the_wal_file
- https://www.sqlite.org/wal.html#avoiding_excessively_large_wal_files
- https://www.sqlite.org/wal.html#persistence_of_wal_mode
- https://www.sqlite.org/wal.html#read_only_databases
- https://www.sqlite.org/wal.html#backwards_compatibility
- https://www.sqlite.org/pragma.html#pragma_journal_mode
- https://www.sqlite.org/pragma.html#pragma_wal_checkpoint
- https://www.sqlite.org/pragma.html#pragma_user_version
- https://www.sqlite.org/pragma.html#pragma_application_id
- https://www.sqlite.org/uri.html
- https://www.sqlite.org/howtocorrupt.html#_unlinking_or_renaming_a_database_file_while_in_use
- https://www.sqlite.org/howtocorrupt.html#_mispairing_database_files_and_hot_journals_
- https://www.sqlite.org/lang_createtable.html
- https://github.com/sqlite/sqlite/blob/version-3.50.4/src/pager.c#L3319-L3364
- https://github.com/sqlite/sqlite/blob/version-3.50.4/src/os_unix.c#L6596-L6599
- https://github.com/sqlite/sqlite/blob/version-3.50.4/src/os_win.c#L5541
- https://github.com/sqlite/sqlite/blob/version-3.50.4/src/os_win.c#L5873-L5880
- https://pubs.opengroup.org/onlinepubs/9799919799/functions/rename.html
- https://pubs.opengroup.org/onlinepubs/9799919799/functions/link.html
- https://pubs.opengroup.org/onlinepubs/9799919799/functions/open.html
- https://docs.python.org/3/library/os.html#os.replace
- https://docs.python.org/3/library/os.html#os.rename
- https://docs.python.org/3/library/os.html#os.link
- https://github.com/python/cpython/blob/v3.13.13/Modules/posixmodule.c#L4296
- https://github.com/python/cpython/blob/v3.13.13/Modules/posixmodule.c#L5805
- https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew
- https://man7.org/linux/man-pages/man2/flock.2.html
- https://docs.python.org/3/library/fcntl.html#fcntl.flock
- https://docs.python.org/3/library/msvcrt.html#msvcrt.locking
- https://docs.python.org/3/library/asyncio-dev.html#running-blocking-code
- https://docs.python.org/3/library/asyncio-dev.html#debug-mode
- https://docs.python.org/3/library/asyncio-task.html#asyncio.to_thread
- https://docs.python.org/3/library/asyncio-subprocess.html#asyncio.create_subprocess_exec
- https://anyio.readthedocs.io/en/stable/threads.html
- https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/mcpserver/utilities/func_metadata.py#L149-L164
- https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/mcpserver/tools/base.py
- https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/mcpserver/server.py
- https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp-types/mcp_types/_types.py#L48
- https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/servers/tools.md#L135-L139
- https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/get-started/testing.md
- https://modelcontextprotocol.io/specification/2026-07-28/server/tools
- https://abseil.io/resources/swe-book/html/ch12.html
- https://abseil.io/resources/swe-book/html/ch15.html
- https://martinfowler.com/bliki/Yagni.html
## Ops, security and CI

I checked seven audit findings against primary sources. These were git's own docs, the MCP Python SDK at tag v2.3.0, the npm docs and registry, CWE, OWASP and SEI CERT, the Python and Linux man-page docs, GitHub's Actions docs plus the toolkit and runner source at pinned commits, PyPA, SLSA v1.0, OpenClaw and OpenAI docs, and two peer-reviewed Google papers on mutation testing. I also reproduced findings 1, 3, 4, 6 and 7b with small scratch scripts. Their absolute paths are given in each finding. No repository file was changed. In short: four of the proposed fixes are sound but need additions (1, 2, 5, 7). One is right as proposed (3). Two have a better alternative (4, 6). Finding 1 should use git plumbing and make the tree-SHA check mandatory. Finding 2 should also pin the whole npm tree inside the benchmark container. For finding 3, the proposed tests kill all ten hand-made mutants that survive today. Mutation testing should run as a scoped, non-blocking job, not as a merge gate. In finding 4, `follow_symlinks=False` leaves four holes, so host-user work should run as the host user, or use descriptor-based calls. In finding 5, the checksums must travel as job outputs, and the downloads must use the artifact ID, not the name. In finding 6, "first `{` to last `}`" still fails on common replies and is weaker against injected JSON. A strict single-object parse with exact type checks is better. Only finding 5 (and a CI job for finding 3) touches forbidden territory.

### 1. `pdctl publish` can send the wrong files

- **Current recommendation:** use `git diff --no-renames` and `git ls-tree --full-tree`. Optionally, the broker refuses when its built tree SHA differs from the client's `HEAD^{tree}`.
- **Verdict:** Aligned with changes.
- **Evidence:**
  - Reproduced with git 2.56.0. I rebuilt a tree from each change list and compared it with `HEAD^{tree}`. The current `publication()` failed from the repo root: it left the old path of a rename in place. From a subfolder it marked `docs/readme.txt`, `src/new.py` and `tool.sh` as deleted, although they still exist in HEAD. The plumbing variant matched `HEAD^{tree}` from both places. Scripts: `<session scratchpad>/f1/publication_probe.py` and `<session scratchpad>/f1/repro.sh`.
  - git(1) says the interface of low-level (plumbing) commands is meant to stay far more stable than porcelain, because scripts use them. Porcelain may change to suit users. https://git-scm.com/docs/git#_low_level_commands
  - `diff.renames` defaults to true. It changes only porcelain such as `git diff` and `git log`, not lower-level diff commands. https://git-scm.com/docs/git-config#Documentation/git-config.txt-diffrenames. Checked locally: `git -c diff.renames=true diff-tree` still lists both paths of a rename.
  - `git ls-tree` reads its paths relative to the current directory. `--full-tree` ignores the current directory and implies `--full-name`. https://git-scm.com/docs/git-ls-tree
  - `git diff-tree --raw` prints both modes, both object IDs and a status letter for each path. An add or delete shows `000000` and an all-zero ID. `T` marks a change between regular file, symlink and submodule. With `-z`, paths come out verbatim, so `core.quotePath` quoting does not apply. https://git-scm.com/docs/git-diff-tree#_raw_output_format
  - `git cat-file <type> <object>` returns the raw object contents. https://git-scm.com/docs/git-cat-file
  - GitHub's create-tree endpoint accepts modes 100644, 100755, 040000, 160000 and 120000. A `sha` of null deletes the path. Deleting a path that does not exist returns an error. Entries override `base_tree` entries with the same path. https://docs.github.com/en/rest/git/trees?apiVersion=2022-11-28#create-a-tree
  - Git stores objects by content hash, and each tree entry records mode, type, object ID and name. Equal tree IDs therefore mean equal snapshots. https://git-scm.com/book/en/v2/Git-Internals-Git-Objects. Since 2.13, Git has used hardened SHA-1, which resists the SHAttered collision. https://git-scm.com/docs/hash-function-transition
  - The broker already refuses symlinks and submodules (`ops/vision/control.py#L180-L181`). The current client reads each path a second time by name (`ops/vision/client.py#L18-L27`).
- **Recommended fix:**
  - `ops/vision/client.py` `publication()`: make one call, `git diff-tree -r -z --no-renames <base> HEAD`. For each record:
    - If the new mode is `000000`, send `{"path": ..., "content": None}`.
    - If the new mode is `100644` or `100755`, send `mode`, `sha` and the base64 of `git cat-file blob <new_sha>`.
    - For any other mode (120000 symlink, 160000 submodule), raise before sending.
  - Mode-only changes (exec bit) appear with identical IDs and are kept. Also send `"tree_sha"`, taken from `git rev-parse HEAD^{tree}`.
  - `ops/vision/control.py` `validate_publication()`: require a 40-hex `tree_sha`. Accept an optional 40-hex `sha` per change.
  - `ops/vision/control.py` `publish()`: right after the `git/trees` POST, raise when `tree["sha"] != data["tree_sha"]`. Do this before `git/commits` and before the 25-minute `review()`. Optionally, compare each `git/blobs` result with `change["sha"]` to get a precise error.
  - The minimal variant (porcelain with `--no-renames` and `--full-tree`) also passes my probe. The plumbing variant is better because it removes the second lookup by path and uses the stable interface (inferred).
  - The tree check is the right end-to-end guard for publishing what was committed. It turns every listing bug into a refusal before the review. It is not a security boundary: the client is untrusted anyway, and the independent review of the broker-built commit stays the security control (inferred).
  - Must not break: the clean-checkout gate, the 500-file and 8 MiB limits, the rule of no ref before review, and the refusal of symlinks and submodules. Deleting an existing symlink still sends the default mode 100644 with `sha: null`. GitHub's docs do not say whether the mode matters for a delete (unverified). The tree check makes any mismatch fail closed.
- **Test that proves it:**
  - New `tests/test_vision_client.py`. Build a temp repo with a base commit, then commit a rename, a delete, a `chmod +x` and an edit under `docs/`. Run `monkeypatch.chdir(repo / "docs")` and call `publication()`.
  - Rebuild the tree in a temporary index: set `GIT_INDEX_FILE`, run `read-tree <base>`, then `update-index --add --cacheinfo` or `--force-remove` per change, then `write-tree`. Assert the result equals `HEAD^{tree}` and `data["tree_sha"]`.
  - Today this fails: the root run leaves the rename's old path in place, and the subfolder run deletes three live files. After the fix it passes. I verified this with the prototype.
  - Broker test: a fake `api` returns a tree SHA that differs from `tree_sha`. Expect `ValueError`, with no `git/commits` call and no `review()` call.
  - Add `tree_sha` to the fixture in `test_unreviewed_publication_never_creates_an_executable_ref`.
- **Risks and compatibility:** client and broker must change together. `install.py` installs both (`ops/vision/install.py#L171-L172`). An old client that sends no `tree_sha` is refused, which is intended. A repo created with `--object-format=sha256` would never match. The client can assert SHA-1 with `git rev-parse --show-object-format` (inferred). Not forbidden territory.

### 2. The Context7 benchmark adapter gives every secret to npm code

- **Current recommendation:** `env = {API_KEY_ENV: key} if key else None`, and read the key only in key mode.
- **Verdict:** Aligned with changes.
- **Evidence:**
  - At MCP SDK v2.3.0, `stdio_client` spawns the server with `get_default_environment()` merged with `server.env`. https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/client/stdio.py#L129
  - The defaults on POSIX are HOME, LOGNAME, PATH, SHELL, TERM and USER. On Windows they are APPDATA, HOMEDRIVE, HOMEPATH, LOCALAPPDATA, PATH, PATHEXT, PROCESSOR_ARCHITECTURE, SYSTEMDRIVE, SYSTEMROOT, TEMP, USERNAME and USERPROFILE (#L41-L58).
  - Values that start with `()` (shell functions) are skipped (#L76-L91). The `env` field is documented as extra variables merged over these defaults (#L101-L102).
  - So `{**os.environ, ...}` (`benchmarks/adapters/context7_adapter.py#L326`) defeats that allowlist. The project locks mcp 2.3.0 (`uv.lock`), and the installed version is 2.3.0.
  - CWE-526 says environment contents can reach the child processes that dependencies run in, which often do not need them. One listed mitigation is to remove a variable that the behaviour does not need. https://cwe.mitre.org/data/definitions/526.html
  - The OWASP Secrets Management Cheat Sheet asks for least privilege (section 2.3). It says environment variables are generally readable by all processes and can end up in logs or dumps (section 5.1). https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html. Source pinned at https://github.com/OWASP/CheatSheetSeries/blob/920293be317721982fa50324a6f26af3f29dc45e/cheatsheets/Secrets_Management_Cheat_Sheet.md#L470
  - `npx` installs missing packages into a folder in the npm cache. It assumes `--yes` without a TTY or in CI. A spec with a version only matches a local dependency of exactly that version. https://docs.npmjs.com/cli/v11/commands/npm-exec
  - `package-lock.json` cannot be published and is ignored outside the root project. Only `npm-shrinkwrap.json` can be published. https://docs.npmjs.com/cli/v11/configuring-npm/package-lock-json
  - The registry record for `@upstash/context7-mcp@3.2.3` lists eight runtime dependencies with caret ranges, among them `@modelcontextprotocol/sdk ^1.29.0`, `express ^5.1.0` and `zod ^4.4.3`. It has no shrinkwrap, no install scripts and no provenance attestation. https://registry.npmjs.org/@upstash%2fcontext7-mcp/3.2.3. So `@3.2.3` pins one tarball, and the transitive tree is resolved again on each fresh install (inferred).
  - `npm ci` requires a lockfile, fails on a mismatch with `package.json`, and never rewrites the lockfile. `ignore-scripts` stops lifecycle scripts. https://docs.npmjs.com/cli/v11/commands/npm-ci
  - `npm audit signatures` checks the registry signatures and provenance attestations of installed packages. https://docs.npmjs.com/cli/v11/commands/npm-audit#audit-signatures
  - MCP's security best practices say to run local servers in a sandbox with minimal default privileges and restricted file and network access. Containers are one of the listed sandboxes. https://modelcontextprotocol.io/docs/2025-11-25/tutorials/security/security_best_practices#local-mcp-server-compromise
  - In Docker, `-e NAME` without a value takes the value from the local environment, and leaves it unset when it is not exported. https://docs.docker.com/reference/cli/docker/container/run/
- **Recommended fix:**
  - `Context7Adapter.__init__` (`#L282`): `self._api_key = (api_key if api_key is not None else os.environ.get(API_KEY_ENV)) if key_mode else None`.
  - `_run_async` (`#L326`): `env = {API_KEY_ENV: self._api_key} if self._api_key else None`.
  - This also stops HTTP mode from sending the key header in keyless runs. Keyless results then really are keyless (inferred).
  - For the approved container design (inferred engineering from the sources above):
    - Build the image from a committed `package.json` and `package-lock.json`. They pin `@upstash/context7-mcp` 3.2.3 and its whole tree with integrity hashes.
    - At build time, run `npm ci --ignore-scripts --omit=dev`, then `npm audit signatures`.
    - Start the package's entry point with `node` from `node_modules`, not with `npx -y`.
    - Use a non-root user, a read-only root filesystem and a base image pinned by digest.
    - The adapter command becomes `docker run --rm -i --read-only --cap-drop=ALL -e CONTEXT7_API_KEY <image@digest>`. The adapter env then holds only the key, so `-e` copies only that one variable.
  - Sibling: `benchmarks/adapters/python_docs_mcp_adapter.py#L252` uses the same `{**os.environ, ...}` pattern for the first-party server. The risk is lower, but it can be narrowed the same way once its cache-directory needs are known (inferred).
  - Must not break: keyless runs (env `None` gives the SDK defaults), the key-mode guard, which requires the key variable (`benchmarks/adapters/guard.py#L115-L122`), and `npx`'s need for PATH and HOME (kept). Proxy and npm config variables are dropped. Pass them explicitly only if a run needs them (inferred).
- **Test that proves it:**
  - In `tests/benchmarks/test_competitor_adapters.py`, set `CONTEXT7_API_KEY=k` and `UNRELATED_SECRET=s`.
  - Patch the `_transport_factory` seam to record its keyword arguments and raise a sentinel. Then run `asyncio.run(adapter._run_async("q"))`.
  - Key mode: the recorded env equals `{"CONTEXT7_API_KEY": "k"}`.
  - Keyless stdio: env is `None`. Keyless HTTP: headers are `None`.
  - This fails today and passes after the fix.
- **Risks and compatibility:** not forbidden territory. The container work changes the benchmark harness. Live Context7 runs still wait for the separate ToS permission noted in `context7_adapter.py#L21-L27`.

### 3. The checks that gate merges and releases have no failing test

- **Current recommendation:** a parametrized `review()` test and a `release()` test.
- **Verdict:** Aligned.
- **Evidence:**
  - Reproduced. I applied ten hand-made mutants to `review()` and `release()`, one at a time:
    - drop the SHA check;
    - drop only the base-SHA check;
    - drop the missing-command check;
    - drop the exit-code check;
    - drop the `approved` check;
    - drop the blockers check;
    - empty the mandatory command list;
    - drop the version check;
    - accept any tag;
    - skip the release gate.
  - All ten survive the current 29 tests (0.65 s per run). After appending the prototype tests (13 cases), all ten are killed. On unmodified code, all 42 tests pass in 0.67 s. Scripts: `<session scratchpad>/f3/hand_mutants.py` and `<session scratchpad>/f3/proposed_tests.py`.
  - Google (Petrović and Ivanković, ICSE-SEIP 2018) calls full mutation analysis too costly for industry. They mutate only covered, changed, non-"arid" lines, such as non-logging code, and generate at most one mutant per line. They surface surviving mutants as code-review findings. The reported usefulness rose from 20% to 80%, and 75% of findings that got feedback were rated useful. https://storage.googleapis.com/gweb-research2023-media/pubtools/4203.pdf (sections 3, 4.1 and 5.4); https://doi.org/10.1145/3183519.3183521
  - Petrović, Ivanković, Fraser and Just (ICSE 2021) found that developers who see mutants write more tests, and that mutants are coupled with real faults. https://arxiv.org/abs/2103.07189
  - mutmut 3.8.0 needs `fork`, so on Windows it must run in WSL. It runs only the tests that cover a mutated function, in parallel and incrementally. It supports wildcard runs per function and `# pragma: no mutate`. It mutates only code inside functions, so module-level constants such as `BRANCH` are not mutated. https://github.com/boxed/mutmut/blob/3.8.0/README.rst. Its dependencies are click, coverage, libcst, pytest, setproctitle and textual. https://pypi.org/pypi/mutmut/3.8.0/json
  - cosmic-ray 8.7.0 runs the test command once per mutant, with a timeout. `cr-filter-git` keeps only lines changed against a branch, and `cr-filter-pragma` skips marked lines. https://github.com/sixty-north/cosmic-ray/blob/release/v8.7.0/docs/source/how-tos/filters.rst and https://github.com/sixty-north/cosmic-ray/blob/release/v8.7.0/docs/source/concepts.rst. Its dependency set is heavier, including aiohttp, gitpython and sqlalchemy. https://pypi.org/pypi/cosmic-ray/json
  - GitHub-hosted Linux runners for public repos have 4 vCPUs. https://docs.github.com/en/actions/reference/runners/github-hosted-runners
- **Recommended fix:**
  - Land the two tests now, as in the prototype file.
  - `review()` cases:
    - a wrong head or base SHA must give an infrastructure failure with reason `sha_mismatch`;
    - `approved` false, or the non-bool `"true"`, must give a rejection;
    - so must any blocker, an empty command list, each missing mandatory command, a failing mandatory command and an extra failing command;
    - in every case, assert that no `review-*.json` checkpoint is written.
  - `release()` cases:
    - malformed tags must fail;
    - each of the three version sources mismatched in turn must fail;
    - the gate must be called with `(sha, app_id)`;
    - there is exactly one `git/refs` POST, and only on success.
  - Should CI run mutation testing on `control.py`? Not as a blocking PR gate (inferred from Google's design and the cost). Run it as a non-blocking, owner-approved job (weekly plus `workflow_dispatch`) or locally. Scope it to the security functions: `review`, `release`, `validate_api`, `validate_publication`, `require_verified` and `rerun`. Then triage survivors in review, as Google does.
    - mutmut is the faster choice.
    - cosmic-ray with `cr-filter-git` gives diff-only runs on PRs that touch `ops/vision/`.
    - Mark arid lines, such as error-message strings, with `# pragma: no mutate`.
  - Cost (inferred estimate):
    - Counted by AST, `control.py` has about 1,250 candidate sites. 746 of them are string constants, so about 500 are not.
    - At about 0.7 s per run, a full mutmut run is at most about 4 minutes on 4 vCPUs. A scoped run takes about 1 minute.
    - A serial cosmic-ray run takes about 10–25 minutes for the whole file. Diff-only runs take seconds to minutes.
    - Needs a spike: the tests load `control.py` through `runpy` by file path. Whether mutmut's copied `mutants/` tree is the one that gets loaded is unverified.
- **Test that proves it:** the two tests above. The proof is the hand-mutant run: 10 of 10 mutants survive today and 10 of 10 are killed with the tests.
- **Risks and compatibility:** the tests change no production code. A CI mutation job means editing `.github/workflows/`, which is forbidden territory. A mutation tool as a dev dependency changes `uv.lock` and adds to the supply chain (inferred).

### 4. The installer follows symlinks while running as root

- **Current recommendation:** refuse a symlinked key, create directories with `directory()`, and pass `follow_symlinks=False` to `os.chown`.
- **Verdict:** Better alternative exists.
- **Evidence:**
  - Local probe: `<session scratchpad>/f4/symlink_probe.py`. It showed:
    - `Path.exists()` is False for a dangling symlink. So `install.py#L193-L194` would run `ssh-keygen` as root through a planted dangling link.
    - `os.chown` followed by `Path.chmod` changed a link target's mode to 0600.
    - With `follow_symlinks=False` on the chown, the `Path.chmod` that follows still changed the target.
    - `mkdir(exist_ok=True)` silently accepts a symlink to a directory.
    - `O_NOFOLLOW` refused the symlinked file, and `O_DIRECTORY|O_NOFOLLOW` with `dir_fd` refused the symlinked directory. The target stayed unchanged.
  - The Python docs say:
    - `follow_symlinks=False` acts only when the last path element is a link (https://docs.python.org/3.12/library/os.html#follow-symlinks);
    - `dir_fd` makes paths relative to a directory descriptor, using the `*at` system call (#dir-fd);
    - `lchown` is the same as `chown(..., follow_symlinks=False)` (#os.lchown);
    - `fchown` and `fchmod` act on an open descriptor (#os.fchown, #os.fchmod);
    - `O_NOFOLLOW`, `O_DIRECTORY` and `O_PATH` exist where libc defines them (#os.O_NOFOLLOW).
  - `Path.chmod` follows symlinks by default. https://docs.python.org/3.12/library/pathlib.html#pathlib.Path.chmod
  - open(2): `O_NOFOLLOW` refuses a symlink only as the last component, and links in earlier components are still followed. Using `openat()` with a directory descriptor avoids races on the directory prefix. https://man7.org/linux/man-pages/man2/open.2.html
  - openat2(2), on Linux 5.6 and later, has `RESOLVE_NO_SYMLINKS`, which blocks links in every component. https://man7.org/linux/man-pages/man2/openat2.2.html. CPython's `os` module has no `openat2` wrapper. A code search of python/cpython finds the name only in `Lib/test/support/os_helper.py` and `Misc/NEWS.d/3.12.0a1.rst`, both about WASI test skips. https://github.com/python/cpython/blob/main/Lib/test/support/os_helper.py
  - CWE-59 covers following a link to an unintended file. https://cwe.mitre.org/data/definitions/59.html. CWE-367 says a check followed by a use on a file name can be invalidated in between. One listed mitigation is to run the operation with the uid and gid of the user who owns the file. https://cwe.mitre.org/data/definitions/367.html
  - SEI CERT POS35-C calls an `lstat`-then-`open` sequence a TOCTOU race. The compliant code opens with `O_NOFOLLOW` and no separate check, or compares inode and device from `lstat` and `fstat`. https://cmu-sei.github.io/secure-coding-standards/sei-cert-c-coding-standard/rules/posix-pos/pos35-c/
  - SEI CERT FIO15-C asks for file operations only in secure directories. Those are directories where no other user can create, rename or delete entries, in the directory itself or in any ancestor. https://cmu-sei.github.io/secure-coding-standards/sei-cert-c-coding-standard/recommendations/input-output-fio/fio15-c/
  - Linux `protected_symlinks` restricts following only in sticky world-writable directories. It does not cover `~/.ssh`, where the directory owner is the link owner. https://docs.kernel.org/admin-guide/sysctl/fs.html#protected-symlinks
  - Python's own `shutil.rmtree` resists symlink attacks by using descriptor-based directory functions. https://docs.python.org/3.12/library/shutil.html#shutil.rmtree.avoids_symlink_attacks
  - More instances in the same file (inferred from the code):
    - Root reads `key.with_suffix(".pub")` through any link (`install.py#L197`). It then writes that text into a world-readable, mode 0644 `authorized_keys` (`#L247`). A `.pub` linked to a root-only file, such as an App private key in `/etc/python-docs-vision`, would leak it.
    - `shutil.copyfile(~/.local/bin/uv, /usr/local/bin/uv)` followed by `chmod 0755` follows a symlinked source (`#L188-L191`).
    - `owned()` sets `O_NOFOLLOW` only on the last component. A symlinked `workspace-pd-*` parent therefore redirects root's writes of `AGENTS.md` and `SKILL.md` (`#L248-L264`).
  - Is check-then-act racy here? Yes, where the parent directory is writable by a process that runs during the install. That covers `~/.ssh` and `/srv/openclaw/.openclaw`: the host user's OpenClaw gateway and host agents keep running (inferred). It is not racy in root-owned parents, such as `/opt` and `/var/lib/python-docs/<role>` after the first install. Those are secure directories in CERT's sense, so `directory()` is fine there (inferred).
- **Recommended fix:**
  - Preferred, following the CWE-367 mitigation: do the host user's file work as the host user.
    - Generate the key with `runuser -u ahammouda -- ssh-keygen ...`, and drop root's `os.chown` and `chmod` on it (`#L192-L196`).
    - Read the `.pub` file as that user too. Check its format (`ssh-ed25519` followed by base64) before writing `authorized_keys`.
    - Create the workspaces and write `AGENTS.md` and `SKILL.md` as that user, for example with `runuser ... install -d -m 700` and a file write fed from stdin. `install.py` already uses `runuser` (`#L337-L345`).
  - If root must act in a non-secure directory, use a small helper:
    - open the trusted parent;
    - walk each component with `os.open(name, O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC, dir_fd=fd)`;
    - create directories with `os.mkdir(name, mode, dir_fd=fd)`;
    - open the final file with `O_NOFOLLOW|O_NONBLOCK`;
    - check `fstat` for the type, the owner and `st_nlink == 1`;
    - then call `fchown` and `fchmod`.
    - No `is_symlink()` pre-check is needed.
  - uv: copy from a descriptor opened with `O_NOFOLLOW` after an `fstat` check. Better, install a pinned uv with a checksum, as `.github/scripts/install_publisher.sh` does for mcp-publisher (inferred).
  - Keep `directory()` for root-owned trees. Skip `openat2`: it has no stdlib wrapper, and a ctypes syscall is not worth it here (inferred).
  - Must not break: the first install, re-install idempotence, OpenClaw's expectations (workspace owned by the host user with mode 0700, files 0600), and `test_installer_rejects_worker_symlinks_before_ownership_changes`.
- **Test that proves it:**
  - An unprivileged pytest. Create `secret` with mode 0644 and a symlink `key -> secret`, then run the key step.
  - Expect `OSError`, with `secret` still at 0644. The old code leaves it at 0600, as measured.
  - Simulate the race: monkeypatch `Path.is_symlink` and `Path.exists` to return False, as if the link were swapped in after the check. The new code still refuses because it does not rely on the check.
  - A `.pub` linked to a fake PEM file is refused, and `authorized_keys` is not written.
  - Assert only `OSError`, because the errno differs between Linux and macOS.
- **Risks and compatibility:** behaviour changes only for the key, the `.pub` read, the workspaces and the uv copy. Not forbidden territory.

### 5. Release files are not checked between build and publish

- **Current recommendation:** `build` exports `sha256sum dist/*`. `publish` and `github-release` verify it before attesting. Pin `hatchling`.
- **Verdict:** Aligned with changes.
- **Evidence:**
  - From the workflow files:
    - `build` uploads `dist` by name (`.github/workflows/release.yml#L110-L114`).
    - `integration` calls `e2e.yml` (#L116-L120).
    - `e2e.yml` downloads `dist` (`.github/workflows/e2e.yml#L41-L46`). It installs unpinned packages: pip itself, the wheel's dependencies and pytest (#L52-L63). It ends with its own `actions/upload-artifact` step (#L75-L83).
    - `publish` downloads `dist` by name, attests it and uploads it (`release.yml#L134-L146`). `github-release` does the same (#L182-L192).
    - `uv build` (#L92) resolves `hatchling` unpinned (`pyproject.toml#L1-L3`).
  - `actions/upload-artifact` v7, at the pinned commit 043fb46, says an artifact cannot be changed but can be overwritten. Overwriting gives it a new ID, and the old ID no longer exists. The README's example overwrites from a later job in the same run. The action outputs `artifact-id` and `artifact-digest` (SHA-256). https://github.com/actions/upload-artifact/blob/043fb46d1a93c77aae656e7c1c64a875d1fc6a0a/README.md (Inputs, Outputs, "Overwriting an Artifact")
  - The overwrite path deletes by name through `@actions/artifact`. The internal path authenticates with `ACTIONS_RUNTIME_TOKEN`, so no `actions: write` permission is needed:
    - https://github.com/actions/upload-artifact/blob/043fb46d1a93c77aae656e7c1c64a875d1fc6a0a/src/upload/upload-artifact.ts#L11-L23
    - https://github.com/actions/toolkit/blob/0c3a9c332b678ff1e776f93d728a4ad279803abe/packages/artifact/src/internal/client.ts#L251-L274
    - https://github.com/actions/toolkit/blob/0c3a9c332b678ff1e776f93d728a4ad279803abe/packages/artifact/src/internal/shared/artifact-twirp-client.ts#L34
    - https://github.com/actions/toolkit/blob/0c3a9c332b678ff1e776f93d728a4ad279803abe/packages/artifact/src/internal/shared/config.ts#L10-L16
  - The runner injects that token into every JavaScript action step. https://github.com/actions/runner/blob/110142c2c501c6ae9373d553efa826bedbaab5f7/src/Runner.Worker/Handlers/NodeScriptActionHandler.cs#L57. e2e's final upload step is such a step.
  - GitHub warns that jobs in one workflow can interact with, and compromise, later jobs. https://docs.github.com/en/actions/reference/security/secure-use#using-third-party-actions. Hosted runners use passwordless sudo. https://docs.github.com/en/actions/reference/runners/github-hosted-runners
  - So code that runs in the integration job could replace `dist` before `publish` downloads it by name (inferred). I found no GitHub setting that limits which jobs in a run may write artifacts, so the defence must sit with the consumer (inferred).
  - `actions/download-artifact` v8, at the pinned commit 3e5f45b, can download by `artifact-ids`. It checks the downloaded hash against the expected hash and fails by default (`digest-mismatch`). https://github.com/actions/download-artifact/blob/3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c/README.md. That protects the transfer, not against replacement: a replaced artifact carries its own new digest (inferred).
  - Job outputs reach jobs that depend on the job. They are limited to 1 MB per job and are evaluated on the runner when the job ends. https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idoutputs
  - The `needs` context holds outputs from direct dependencies only. https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#needs-context. `github-release` lists only `publish` and `publish-mcp-registry` today.
  - PyPA says to keep the build job separate. Publishing jobs should only download the files already built and upload them. Since v1.11.0, `pypa/gh-action-pypi-publish` makes PEP 740 attestations by default. https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/
  - The README of that action, at the pinned commit dc37677, adds more advice. Give `id-token: write` only to the publishing job. Build in a separate job with fewer privileges. Keep the publishing job in a top-level workflow, not a reusable one. `print-hash` shows the SHA-256 of each uploaded file. https://github.com/pypa/gh-action-pypi-publish/blob/dc37677b2e1c63e2034f94d8a5b11f265b73ba33/README.md
  - `attest-build-provenance` v4 is a wrapper over `actions/attest`, and new code should use `actions/attest`. https://github.com/actions/attest-build-provenance/blob/4d101475d8b20a2381f78447822ac1eab6504dd8/README.md
  - GitHub's how-to puts the attestation step in the workflow that builds the artifact and verifies with `gh attestation verify`. https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations. Building and attesting in a reusable workflow reaches SLSA Build L3. https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/increase-security-rating
  - `gh attestation verify` can enforce `--signer-workflow`, `--source-digest`, `--source-ref` and `--deny-self-hosted-runners`. https://cli.github.com/manual/gh_attestation_verify
  - SLSA v1.0 Build L2: a hosted platform signs the provenance, which prevents tampering after the build. Build L3: runs cannot influence one another, and build steps cannot reach the signing material. https://slsa.dev/spec/v1.0/levels#build-l2 and #build-l3. Today the attestation is signed after `dist` has passed through a job that runs unpinned code, so it could sign a swapped file (inferred).
  - `uv build --build-constraint <file> --require-hashes` pins build requirements and checks their hashes. `--require-build-hashes`, which is experimental, also covers transitive build dependencies. https://docs.astral.sh/uv/concepts/projects/build/#build-constraints. The `[tool.uv] build-constraint-dependencies` setting is documented for `uv lock`, `uv sync` and `uv run`, not for `uv build`. https://docs.astral.sh/uv/reference/settings/#build-constraint-dependencies
  - hatchling 1.33.0 pulls in packaging, pathspec, pluggy, tomlkit and trove-classifiers. https://pypi.org/pypi/hatchling/json. Pinning only `hatchling==X` leaves those five floating (inferred).
- **Recommended fix (`release.yml`):**
  - `build`:
    - give the upload step an `id`;
    - add a step that writes the sorted output of `sha256sum dist/*` to a multi-line step output;
    - expose job outputs `dist-id` (the upload's `artifact-id`) and `dist-sha256`.
  - `publish` and `github-release`:
    - download with `artifact-ids: ${{ needs.build.outputs.dist-id }}`, which fails if `dist` was overwritten;
    - compare the exact `sha256sum dist/*` listing with `needs.build.outputs.dist-sha256` as a string, which also catches extra files that `sha256sum -c` would miss;
    - do this before the attest, `pypi-publish` and `action-gh-release` steps;
    - add `build` to the `needs` of `github-release`.
  - Keep the checksums out of the artifact itself, because a replacement would replace them too (inferred).
  - Hatchling: run `uv build --build-constraint <constraints file> --require-hashes`. List hatchling and its five dependencies with hashes, or use `--require-build-hashes`. `[build-system] requires = ["hatchling==1.33.0"]` alone is a weaker fallback.
  - Optional SLSA L3 path:
    - Attest in `build`, or in a dedicated `attest` job that downloads by ID. Both need `id-token: write` and `attestations: write`. Weigh this against PyPA's advice to keep `id-token` in the publishing job only.
    - Then run `gh attestation verify --signer-workflow <repo>/.github/workflows/release.yml --source-digest $GITHUB_SHA` in `publish`.
    - Switch to `actions/attest` when that step is touched.
  - Optional: pass the artifact ID to `e2e.yml` so that `integration` tests the exact bytes that ship.
  - Must not break: Trusted Publishing (the publish job stays top-level with `environment: pypi`), PEP 740 attestations from the publish action, the tag and environment policies, and e2e's name-based `artifact-name` input for scheduled runs.
- **Test that proves it:**
  - A static policy test on `release.yml`, using PyYAML, which is already a runtime dependency. It asserts that:
    - `build` exposes `dist-id` and `dist-sha256`;
    - every `download-artifact` in `publish` and `github-release` uses `artifact-ids` from `needs.build.outputs`;
    - both jobs list `build` in `needs`;
    - a verification step comes before attest, publish and release.
  - This fails today and passes after the fix.
  - Plus a one-off drill on a fork: an extra job between `build` and `publish` re-uploads `dist` with `overwrite: true`. `publish` must fail.
- **Risks and compatibility:** `.github/workflows/release.yml` is forbidden territory. It needs owner approval in the issue and a supervisor-review PR. The constraints file is new. `pyproject.toml` `[build-system]` lies outside `[project]`.

### 6. One formatting slip in the verifier's reply costs a 25-minute review

- **Current recommendation:** parse from the first `{` to the last `}`.
- **Verdict:** Better alternative exists.
- **Evidence:**
  - Local probe: `<session scratchpad>/f6/parse_probe.py`.
    - The current parser fails on a plain code fence and on a "Verdict:" prefix.
    - First-to-last fixes those cases, but still fails when prose after the JSON contains a brace.
    - The proposed scan uses `raw_decode` and requires exactly one object that has all five verdict keys. It accepts every benign shape I tried. It refuses a reply that holds a forged approval plus the real verdict, where a naive "first object" parse would approve.
  - `json.JSONDecoder.raw_decode` decodes a JSON document at the start of a string and returns the index where it ended, for strings that carry extra data after it. https://docs.python.org/3.12/library/json.html#json.JSONDecoder.raw_decode
  - `openclaw agent` has no flag for structured output or a schema. `--json` reserves stdout for the JSON result and sends diagnostics to stderr. Exit code 0 means a completed turn; 1 means an error, a timeout or a cancel. https://docs.openclaw.ai/cli/agent
  - OpenClaw's `llm-task` tool checks output against a JSON Schema, but it runs a fresh call with no tools, so it cannot perform the review. https://docs.openclaw.ai/tools/llm-task
  - OpenAI: Structured Outputs enforce a JSON Schema, while JSON mode only ensures valid JSON. Use function calling when the model is connected to tools. Even with a schema, a refusal or a token limit can give output that does not match. https://developers.openai.com/api/docs/guides/structured-outputs
  - OWASP LLM05:2025 says to treat model output like untrusted user input and validate it before passing it on. https://genai.owasp.org/llmrisk/llm052025-improper-output-handling/
  - Type confusion today (checked in Python): JSON `false` or `0.0` as `exit_code` compares equal to 0. So `review()` (`ops/vision/control.py#L539-L564`) and `public_evidence()` (`#L390-L395`) count it as a pass.
  - Classification today: a JSON decode error becomes `invalid_response_or_execution_error`, an infrastructure failure with a 15-minute retry delay (`#L569-L584`). `malformed_verdict` is already in the public reason vocabulary (`#L371-L374`), but it is used only after a successful parse (`#L535-L538`).
- **Recommended fix (`ops/vision/control.py` `review()`):**
  - Envelope: since `--json` reserves stdout, `json.loads(run.stdout)` is enough. Keep the tolerant slice only if older CLI versions print banners (inferred). Read the last payload, rather than joining all payloads, because the envelope of `openclaw agent --json` is not documented (inferred).
  - Add an `extract_verdict(text)` helper:
    - cap the length, for example at 256 KiB (inferred);
    - run `raw_decode` at each `{` and keep the top-level dicts that hold `head_sha`, `base_sha`, `approved`, `commands` and `blockers`;
    - require exactly one;
    - otherwise raise `ReviewFailure("infrastructure", {"reason": "malformed_verdict"})`.
  - Validate the types strictly:
    - `type(approved) is bool`;
    - both SHAs are 40-hex;
    - `commands` is a list of dicts with `type(command) is str` and `type(exit_code) is int`;
    - `blockers` is a list of strings;
    - anything else is `malformed_verdict`.
  - Keep failing closed, and keep the retry counters. The distinct reason makes `pdctl status` actionable.
  - Do not let an LLM, such as `llm-task`, rewrite a verdict, because it could turn a rejection into an approval. Do not read a verdict file from the sandbox, because the code under review can write there (inferred).
  - Later, if malformed verdicts persist: a verdict submitted through a tool call with a schema, as providers recommend for models connected to tools. That needs an OpenClaw plugin (inferred).
  - Must not break: the existing `malformed` case in `test_review_deadline_and_failure_handoff` stays an infrastructure failure; checkpoints are written only on approval; the public reason vocabulary stays the same.
- **Test that proves it:**
  - Parametrize the payload text. A plain fence, another fence tag, a "Verdict:" prefix, and trailing prose that contains braces must each return the approved verdict.
  - A forged object plus the real one must raise `ReviewFailure` with reason `malformed_verdict`.
  - `"exit_code": false` must be a failure.
  - This fails today and passes after the fix.
- **Risks and compatibility:** not forbidden territory. Any edit to `control.py` changes its policy hash. That invalidates cached reviews and temporary receipts, by design (`#L470`, `#L275`).

### 7. Smaller ops items

- **Current recommendation:** fix (a) to (e) as listed in the audit.
- **Verdict:** Aligned with changes. (a), (c), (d) and (e) are aligned; (b) is aligned with changes.
- **Evidence:**
  - (a) Confirmed. `verify()` stores only attempts, status, timestamps, decision, policy, verdict, failure kind and diagnostics (`ops/vision/control.py#L606-L644`). `receipt_status()` copies SHAs only from top-level keys (`#L410-L412`). The test `test_status_omits_raw_verdict_and_owner_rationale` builds a receipt with a top-level `head_sha` that `verify()` never writes (`tests/test_vision_control.py#L579-L589`).
  - (b) Confirmed against git's rules. A component may not start with `.` or end with `.lock`. A name may not contain `..` or repeated slashes, and may not end with `/` or `.`. https://git-scm.com/docs/git-check-ref-format
    - I fuzzed 1,713 names over the regex's own alphabet against `git check-ref-format --branch`. The current `BRANCH` (`#L36`) accepts 305 names that git rejects. A component-wise check accepts none. Script: `<session scratchpad>/f7/branch_fuzz.py`
    - GitHub documents only the `refs/` prefix and two-slash rule for creating a ref. Updating a ref with `force: false` allows a fast-forward only, and failures return 409 or 422. https://docs.github.com/en/rest/git/refs?apiVersion=2022-11-28#update-a-reference
    - `publish()` reaches that update only after `review()` (`#L214-L225`).
  - (c) Confirmed. Listing repository variables returns 10 per page by default, 30 at most. https://docs.github.com/en/rest/actions/variables?apiVersion=2022-11-28#list-repository-variables. The code lists once without paging (`ops/vision/configure_apps.py#L209-L216`).
  - (d) Confirmed. The maps are duplicated (`control.py#L96-L107`, `configure_apps.py#L23-L33`). `activate()` checks the App against configure_apps' map, while `token()` requests control.py's map, so the two can drift apart (inferred).
  - (e) Confirmed. `e2e.yml` runs only on `workflow_dispatch`, `workflow_call` and `schedule` (`.github/workflows/e2e.yml#L3-L12`). The rerun gate requires `event == "push"` (`control.py#L441`). A called reusable workflow uses the caller's `github` context, so its jobs belong to the `release.yml` run. https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations#github-context
- **Recommended fix:**
  - (a) In `verify()`, also store `pr`, `head_sha` and `base_sha` in the receipt. Let `receipt_status()` keep `pr` when it is a positive int.
  - (b) Replace `BRANCH` with a `valid_branch(name)` function, used by both `validate_publication()` and `validate_api()`. It uses the regex `(?:codex|agent)(?:/[A-Za-z0-9][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)*)+`, refuses any component ending in `.lock`, and keeps a length cap. In `publish()`, before `review()`, read `git/ref/heads/<branch>`. Refuse unless the ref is missing or equals `base_sha`. Keep `force: False` as the final guard.
  - (c) Call `GET actions/variables/VISION_VERIFIER_APP_ID`: POST on 404, PATCH otherwise.
  - (d) Define one module-level `PERMISSIONS` in `control.py`. Set `configure_apps.PERMISSIONS = CONTROL["PERMISSIONS"]`.
  - (e) Remove the `.github/workflows/e2e.yml` entry from the rerun allowlist (`control.py#L447`).
- **Test that proves it:**
  - (a) Make `verify()` fail through a raising `review`. Then the first `pdctl status` entry under `blocked_verifications` holds `pr`, `head_sha` and `base_sha`.
  - (b) Parametrize `codex/x.lock`, `codex/a//b`, `codex/a/.b`, `codex/a.` and `codex/a/`; both validators must raise. A publish where the existing ref differs from base must raise before `review` is called.
  - (c) A fake `operator_api` with more than 10 variables, the target outside page 1. Expect a PATCH, not a POST.
  - (d) Assert `setup["PERMISSIONS"] is CONTROL["PERMISSIONS"]`.
  - (e) A policy test: every allowlisted workflow declares a `push` trigger. PyYAML loads the `on` key as boolean `True`, so read `wf.get("on", wf.get(True))` (checked locally).
- **Risks and compatibility:** no forbidden territory. Only `control.py`, `configure_apps.py` and tests change. For (b), the new check also rejects 38 of the 1,713 sampled names that git accepts, for example components that start with `-` or `_`. That is acceptable for agent branches (inferred). Every `control.py` edit changes the policy hash.

### Sources

- https://git-scm.com/docs/git#_low_level_commands
- https://git-scm.com/docs/git-config#Documentation/git-config.txt-diffrenames
- https://git-scm.com/docs/git-ls-tree
- https://git-scm.com/docs/git-diff-tree#_raw_output_format
- https://git-scm.com/docs/git-cat-file
- https://git-scm.com/docs/git-check-ref-format
- https://git-scm.com/book/en/v2/Git-Internals-Git-Objects
- https://git-scm.com/docs/hash-function-transition
- https://docs.github.com/en/rest/git/trees?apiVersion=2022-11-28#create-a-tree
- https://docs.github.com/en/rest/git/refs?apiVersion=2022-11-28#update-a-reference
- https://docs.github.com/en/rest/actions/variables?apiVersion=2022-11-28#list-repository-variables
- https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/client/stdio.py#L41-L58
- https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/client/stdio.py#L76-L91
- https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/client/stdio.py#L101-L102
- https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/client/stdio.py#L129
- https://cwe.mitre.org/data/definitions/526.html
- https://cwe.mitre.org/data/definitions/59.html
- https://cwe.mitre.org/data/definitions/367.html
- https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html
- https://github.com/OWASP/CheatSheetSeries/blob/920293be317721982fa50324a6f26af3f29dc45e/cheatsheets/Secrets_Management_Cheat_Sheet.md#L470
- https://docs.npmjs.com/cli/v11/commands/npm-exec
- https://docs.npmjs.com/cli/v11/configuring-npm/package-lock-json
- https://docs.npmjs.com/cli/v11/commands/npm-ci
- https://docs.npmjs.com/cli/v11/commands/npm-audit#audit-signatures
- https://registry.npmjs.org/@upstash%2fcontext7-mcp/3.2.3
- https://modelcontextprotocol.io/docs/2025-11-25/tutorials/security/security_best_practices#local-mcp-server-compromise
- https://docs.docker.com/reference/cli/docker/container/run/
- https://storage.googleapis.com/gweb-research2023-media/pubtools/4203.pdf
- https://doi.org/10.1145/3183519.3183521
- https://arxiv.org/abs/2103.07189
- https://github.com/boxed/mutmut/blob/3.8.0/README.rst
- https://pypi.org/pypi/mutmut/3.8.0/json
- https://github.com/sixty-north/cosmic-ray/blob/release/v8.7.0/docs/source/how-tos/filters.rst
- https://github.com/sixty-north/cosmic-ray/blob/release/v8.7.0/docs/source/concepts.rst
- https://pypi.org/pypi/cosmic-ray/json
- https://docs.github.com/en/actions/reference/runners/github-hosted-runners
- https://docs.python.org/3.12/library/os.html#follow-symlinks
- https://docs.python.org/3.12/library/os.html#dir-fd
- https://docs.python.org/3.12/library/os.html#os.lchown
- https://docs.python.org/3.12/library/os.html#os.fchown
- https://docs.python.org/3.12/library/os.html#os.fchmod
- https://docs.python.org/3.12/library/os.html#os.O_NOFOLLOW
- https://docs.python.org/3.12/library/pathlib.html#pathlib.Path.chmod
- https://docs.python.org/3.12/library/shutil.html#shutil.rmtree.avoids_symlink_attacks
- https://docs.python.org/3.12/library/json.html#json.JSONDecoder.raw_decode
- https://man7.org/linux/man-pages/man2/open.2.html
- https://man7.org/linux/man-pages/man2/openat2.2.html
- https://github.com/python/cpython/blob/main/Lib/test/support/os_helper.py
- https://cmu-sei.github.io/secure-coding-standards/sei-cert-c-coding-standard/rules/posix-pos/pos35-c/
- https://cmu-sei.github.io/secure-coding-standards/sei-cert-c-coding-standard/recommendations/input-output-fio/fio15-c/
- https://docs.kernel.org/admin-guide/sysctl/fs.html#protected-symlinks
- https://github.com/actions/upload-artifact/blob/043fb46d1a93c77aae656e7c1c64a875d1fc6a0a/README.md
- https://github.com/actions/upload-artifact/blob/043fb46d1a93c77aae656e7c1c64a875d1fc6a0a/src/upload/upload-artifact.ts#L11-L23
- https://github.com/actions/toolkit/blob/0c3a9c332b678ff1e776f93d728a4ad279803abe/packages/artifact/src/internal/client.ts#L251-L274
- https://github.com/actions/toolkit/blob/0c3a9c332b678ff1e776f93d728a4ad279803abe/packages/artifact/src/internal/shared/artifact-twirp-client.ts#L34
- https://github.com/actions/toolkit/blob/0c3a9c332b678ff1e776f93d728a4ad279803abe/packages/artifact/src/internal/shared/config.ts#L10-L16
- https://github.com/actions/runner/blob/110142c2c501c6ae9373d553efa826bedbaab5f7/src/Runner.Worker/Handlers/NodeScriptActionHandler.cs#L57
- https://github.com/actions/download-artifact/blob/3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c/README.md
- https://github.com/actions/attest-build-provenance/blob/4d101475d8b20a2381f78447822ac1eab6504dd8/README.md
- https://github.com/pypa/gh-action-pypi-publish/blob/dc37677b2e1c63e2034f94d8a5b11f265b73ba33/README.md
- https://docs.github.com/en/actions/reference/security/secure-use#using-third-party-actions
- https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idoutputs
- https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#needs-context
- https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations
- https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/increase-security-rating
- https://cli.github.com/manual/gh_attestation_verify
- https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/
- https://slsa.dev/spec/v1.0/levels#build-l2
- https://slsa.dev/spec/v1.0/levels#build-l3
- https://docs.astral.sh/uv/concepts/projects/build/#build-constraints
- https://docs.astral.sh/uv/reference/settings/#build-constraint-dependencies
- https://pypi.org/pypi/hatchling/json
- https://docs.openclaw.ai/cli/agent
- https://docs.openclaw.ai/tools/llm-task
- https://developers.openai.com/api/docs/guides/structured-outputs
- https://genai.owasp.org/llmrisk/llm052025-improper-output-handling/
- https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations#github-context
## Benchmarks and engineering practice

All six findings are real, and most proposed fixes point the right way, but four need changes before they ship. First, unwrapping only single-member exception groups does not fix the crash label: on `mcp` 2.3.0 a failure raised inside `ClientSession` arrives two groups deep (the session's task group inside the transport's), so the shared helper must walk the whole tree and keep every leaf's reason, and the new bridge should catch `MCPError` per call so known failures are never wrapped at all. Second, the report should show success latency and failed-cell latency side by side instead of only dropping failures, as Google's SRE book advises, and it must read the token record's status. Third, `claude-3-5-sonnet-20241022` was retired on 2025-10-28; `claude-sonnet-5-5` is active, a fixed snapshot and free to count against, but the current envelope also sends `tool_result` blocks with no matching `tool_use`, which breaks the documented message contract. Fourth, Context7 3.2.3, GitMCP and Ref.tools all return plain text, so text parsing is the live path, yet `rstrip(".,;:")` misses `)`, `]` and `>`; label-anchored parsing plus the RFC 3986 and GFM rules is better, and because these adapters are deleted before any live run, those rules belong in the new harness's grounding step. The small items check out (`socket.connect_ex` is not even an audit event). The five adapters should not be refactored before deletion, and each fix should ship as its own small change with a test that fails before it.

Repository references are to the worktree at commit `c03e497`; "design" means `docs/benchmarks/AGENTIC-BENCHMARK-DESIGN.md` on branch `docs/agentic-benchmark-design` (commit `68a7086`); "methodology" means `docs/benchmarks/PUBLIC-BENCHMARK-METHODOLOGY.md`. "Local check" marks an experiment I ran on mcp 2.3.0, anyio 4.14.2 and CPython 3.13.10 in the worktree's `.venv`; it is evidence, not a source.

### 1. Benchmark failures all show up as protocol crashes

- **Current recommendation:** a shared helper that unwraps single-member exception groups before the `run()` ladder maps them to a category.
- **Verdict:** Aligned with changes.
- **Evidence:**
  - Since anyio 4.0, any exception leaving a task group, including one raised in the `async with` body, is nested in an `ExceptionGroup` ([anyio changelog 4.0.0](https://github.com/agronholm/anyio/blob/4.14.2/docs/versionhistory.rst#L614-L616)). anyio 4.14.2 appends the body's exception to its list and raises a new group, with no flattening ([_asyncio.py#L778](https://github.com/agronholm/anyio/blob/4.14.2/src/anyio/_backends/_asyncio.py#L778), [#L815-L817](https://github.com/agronholm/anyio/blob/4.14.2/src/anyio/_backends/_asyncio.py#L815-L817)).
  - `mcp` 2.3.0 stacks two such groups. `ClientSession.__aexit__` passes the body's exception into its own task group ([session.py#L504-L519](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/client/session.py#L504-L519)). `stdio_client` yields inside another task group ([stdio.py#L201-L213](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/client/stdio.py#L201-L213)), and so does `streamable_http_client` ([streamable_http.py#L771](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/client/streamable_http.py#L771)).
  - Local check: a `BenchmarkCellFailure` raised inside `ClientSession` over `stdio_client` came out as `ExceptionGroup` > `ExceptionGroup` > the failure. Unwrapping one level still leaves a group, so the cell stays `mcp_protocol_crash`. A `wait_for` timeout came out as a bare `TimeoutError`.
  - The tests miss this because they patch `_run_async` to raise a bare `BenchmarkCellFailure` (`tests/benchmarks/test_python_docs_mcp_adapter.py#L268`), and the real-server smoke test accepts `mcp_protocol_crash` as a pass (same file, `#L305`).
  - anyio's docs point to `except*` for task-group errors ([anyio tasks](https://anyio.readthedocs.io/en/stable/tasks.html#handling-multiple-errors-in-a-task-group)). The language reference adds limits: a `try` cannot mix `except` and `except*`, `return` cannot appear in an `except*` clause, and whatever the clauses raise is merged with what is left unhandled ([compound statements](https://docs.python.org/3/reference/compound_stmts.html#except-star)). PEP 654 says a single newly raised naked exception propagates unwrapped ([PEP 654](https://peps.python.org/pep-0654/#raising-new-exceptions)). Local check: with leaves `[cell failure, OSError]`, two `except*` clauses that each raise a mapped failure produced a new `ExceptionGroup`, which the runner's `except BenchmarkCellFailure` (`benchmarks/runner.py#L269`) misses again. So `except*` suits handlers that record and continue, not a sync boundary that must emit exactly one exception (inferred).
  - PEP 654 expects group handlers to query with `split`/`subgroup` or to format with the `traceback` module ([PEP 654, Handling Exception Groups](https://peps.python.org/pep-0654/#handling-exception-groups)). Both methods walk nested groups and keep their shape ([exceptions docs](https://docs.python.org/3/library/exceptions.html#BaseExceptionGroup.subgroup)).
  - How the v2 client reports errors (tag v2.3.0): a tool execution error returns a `CallToolResult` with `is_error=True` and does not raise ([session.py#L1101-L1108](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/client/session.py#L1101-L1108)). JSON-RPC error responses, per-request timeouts (`REQUEST_TIMEOUT`, -32001) and a closed connection (`CONNECTION_CLOSED`) raise `MCPError` from the call ([jsonrpc_dispatcher.py#L413](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/shared/jsonrpc_dispatcher.py#L413), [#L442](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/shared/jsonrpc_dispatcher.py#L442), [#L461](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/shared/jsonrpc_dispatcher.py#L461)). A transport whose bounded send times out raises a raw `TimeoutError` instead ([#L421-L427](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/shared/jsonrpc_dispatcher.py#L421-L427)). Structured output that breaks the tool's output schema raises `RuntimeError` ([session.py#L1140-L1154](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/client/session.py#L1140-L1154)). A per-request timeout also sends `notifications/cancelled` to the server ([migration.md#L1975](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md#L1975)).
  - The SDK's migration guide moves error handling to individual calls (`MCPError`), and for connect failures that escape the transport context says to "keep context-level handling for those only" ([migration.md#L2229](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md#L2229)).
  - The MCP spec puts tool execution errors in the result (`isError`), treats protocol errors as JSON-RPC errors, and says clients should pass tool execution errors to the model ([MCP tools spec, Error Handling](https://modelcontextprotocol.io/specification/2025-11-25/server/tools#error-handling)).
  - `asyncio.wait_for` may propagate an exception raised during cancellation instead of `TimeoutError` ([asyncio.wait_for](https://docs.python.org/3.12/library/asyncio-task.html#asyncio.wait_for)). `asyncio.timeout()` offers `expired()`, which says whether the deadline fired whatever the exception looks like ([asyncio.Timeout.expired](https://docs.python.org/3.12/library/asyncio-task.html#asyncio.Timeout.expired)).
- **Recommended fix (both):**
  - Now. In `benchmarks/runner.py`, next to `BenchmarkCellFailure`, add `cell_failure_from(exc, *, timeout_seconds, label)`. It walks `BaseExceptionGroup.exceptions` recursively to the leaves. Category: the first leaf `BenchmarkCellFailure`'s category; else `timeout` if any leaf is a `TimeoutError`; else `mcp_protocol_crash`. Message: a single leaf keeps its own text; several leaves become `Type: message` items joined by `; ` in tree order, so no reason is lost. In all five adapters, replace the three-clause ladder with `except Exception as exc: raise cell_failure_from(exc, ...) from exc`. `except Exception` catches `ExceptionGroup` but lets a group holding `KeyboardInterrupt` or `CancelledError` through, which is right (inferred). `from exc` keeps the whole group in `__cause__`. A local prototype mapped the real nested stdio case to `tool_failure` and the timeout case to `timeout`.
  - New harness. Do not raise known failures through the task groups. Wrap each `session.call_tool(..., read_timeout_seconds=30)` in `try/except MCPError` and branch on `exc.code`: `REQUEST_TIMEOUT` is a tool-cap hit; `CONNECTION_CLOSED` is a server crash; other codes go back to the model as an error tool result and count as invalid calls (design §11.3; inferred). Pass `is_error` results to the model unchanged. Return a trajectory outcome value instead of raising. Enforce the 300 s trajectory cap with `asyncio.timeout()` and classify with `expired()`, not by exception type. Keep `cell_failure_from` at the sync boundary for what still escapes (connect errors, spawn errors, bugs), and store `traceback.format_exception(exc)` in the failure artifact.
- **Test that proves it:** in `tests/benchmarks/test_python_docs_mcp_adapter.py`, patch `_run_async` to raise `ExceptionGroup("tg", [ExceptionGroup("tg", [BenchmarkCellFailure("tool_failure", "search_docs returned an error: boom")])])`. Assert category `tool_failure` and `boom` in the message; today it is `mcp_protocol_crash`. Add a two-leaf case (`BenchmarkCellFailure` plus `OSError("pipe closed")`) that asserts both reasons appear. Parametrise over the five adapter classes, as `tests/benchmarks/test_competitor_adapters.py` already does for `_run_async`. Optional: one offline test that raises inside a real `ClientSession` over `stdio_client` against `sys.executable -c "import sys; sys.stdin.read()"`, to catch future changes in SDK nesting.
- **Risks and compatibility:** failure categories change (more `tool_failure`, fewer `mcp_protocol_crash`), so failure counts are not comparable with older runs. Messages get longer. Stored tracebacks can contain local paths (inferred).

### 2. The benchmark report misstates the run

- **Current recommendation:** latency and token medians over successful cells plus a separate error rate; delete the hard-coded `external_provider_calls`; drop the `no-mcp-baseline` alias but keep `fake` for tests; render manifest `exclusions:`.
- **Verdict:** Aligned with changes.
- **Evidence:**
  - Google's SRE book asks for separate latency for successful and failed requests. Fast errors distort a blended number, but "a slow error is even worse than a fast error", so it says to track error latency rather than only filter errors out ([Monitoring Distributed Systems, The Four Golden Signals](https://sre.google/sre-book/monitoring-distributed-systems/)). The same section counts a request that breaks a latency policy as an error.
  - `_basic_stats` takes the latency median over every cell (`benchmarks/report.py#L372-L409`), and the report says so on purpose (`report.py#L629`). With 26 failed cells at 5 ms and 24 successes at 1,000 ms the median is 5 ms (arithmetic).
  - The token median also keeps failed cells' counts (`report.py#L383-L387`); in a live run the runner writes a token record for every cell, failed ones included (`runner.py#L323-L403`) (inferred: failure-prone systems look token-cheap).
  - The methodology measures latency from dispatch to final answer receipt ("Latency Measurement"); a failed cell has no final answer (inferred). The design asks for median, p95, min and max, split into model, tool and harness time, with cold and warm tool calls (§10); for every failure with its reason, cap hits and invalid calls (§11.3); and it counts failed trajectories' tokens with score 0 in tokens per correct answer (§8), so failures cannot hide in C2.
  - `external_provider_calls: False` is a constant in the run summary, every transcript and the environment record (`runner.py#L153`, `#L294`, `#L717`), and the environment record is printed in REPORT.md (`report.py#L479-L486`). Competitor adapters and the live token counter do use the network. The design's C1 evidence is the egress-proxy log, one line per connection attempt (§2, §7.3). An unmeasured constant is not evidence (inferred).
  - `no-mcp-baseline` and `no_mcp_baseline` dispatch to `_fake_adapter_answer`, which echoes the prompt (`runner.py#L422-L432`, `#L609-L610`), while `_tool_model_key` labels the row with the manifest's provider and model (`runner.py#L637`). The test fixture renders such a row as `no-mcp-baseline:gpt-4o-mini` (`tests/benchmarks/test_report.py#L158`). The methodology defines the baseline as the same model with no docs context ("Systems Under Test"). Heiser counts benchmarking a simplified, simulated system without saying so, and using an improper baseline, as benchmarking crimes ([simulated system](https://gernot-heiser.org/benchmarking-crimes.html#sim), [baseline](https://gernot-heiser.org/benchmarking-crimes.html#base)).
  - Exclusions: the template says they must be reported (`docs/benchmarks/competitor-manifest.template.yml#L1-L6`, `#L155-L170`); eligibility moves an excluded competitor into `exclusions:` (`benchmarks/adapters/eligibility.py#L24-L31`); the report reads only `competitors` (`report.py#L318`), nothing else in `benchmarks/` reads `exclusions`, and the module docstring still promises "failures/exclusions" (`report.py#L12`). The methodology forbids dropping a failed system silently ("Honesty Rules"). On subsets, Heiser says: "There must be convincing explanation for each missing program." ([subsetting](https://gernot-heiser.org/benchmarking-crimes.html#subset)).
  - MLPerf and HELM rules were not consulted; the sources above settle these points.
- **Recommended fix (both):**
  - Now, `benchmarks/report.py`. In `_basic_stats`, split cells by `status`. Compute latency median, p95, min and max over succeeded cells. Add a failed-cell latency median with the failed count. Take the token median over succeeded, counted, non-approximate cells. In the latency table and the README summary, show success median and p95, failed-cell median with n, and error rate side by side, and rewrite the paragraph at `#L629`.
  - Now, `report.py`. In `_load_run_bundle`, also read `exclusions` from `snapshots/competitor-manifest.yml`. Render id, reason, terms verdict, `decided_on` and `source_url` under "Failures And Exclusions", and add a one-line excluded list to the README summary.
  - Now, `benchmarks/runner.py`. Delete the three `external_provider_calls` entries. Delete the two baseline aliases from `_ADAPTER_DISPATCH`; an unknown adapter already becomes a recorded `tool_failure` (`runner.py#L619-L626`).
  - New harness. Report §10 latency over successful trajectories, with failed and capped trajectories shown separately (count, median, p95). Derive C1 only from proxy logs. Make `no-docs` a real model call with no tools (design §5).
- **Test that proves it:** in `tests/benchmarks/test_report.py`, write 26 failed cells at 5 ms and 24 succeeded cells at 1,000 ms; assert a success median of 1000.0, a failed-cell median of 5.0 and an error rate of 52.0%. Today the median is 5.0. Also: a manifest snapshot with one `exclusions:` entry renders its id and reason; a fresh run's `environment.json` has no `external_provider_calls`; a manifest with `adapter: no-mcp-baseline` yields `tool_failure` cells.
- **Risks and compatibility:** latency medians rise for failure-prone systems, so old reports are not comparable. The README summary gains columns. Tests that assert the constant (`tests/benchmarks/test_runner.py#L173`, `#L295`, `#L451`; `tests/benchmarks/test_competitor_adapters.py#L1132`) and fixtures that use the alias (`test_report.py`, `test_runner.py`, `test_competitor_adapters.py`) change on purpose; say so in the PR. `test_model_matrix.py` uses `no-mcp-baseline` as an id and should be checked (inferred).

### 3. Live token counting uses a retired model

- **Current recommendation:** pin `claude-sonnet-5-5`, as design §8 says; record the count model in each token record; report count failures as failures.
- **Verdict:** Aligned with changes.
- **Evidence:**
  - `claude-3-5-sonnet-20241022` was retired on 2025-10-28, with `claude-sonnet-5-5` listed as its replacement, and "Requests to retired models fail." ([model deprecations](https://platform.claude.com/docs/en/about-claude/model-deprecations)). `benchmarks/adapters/claude_tokens.py#L63` still defaults to it, so every live count would fail (inferred).
  - `claude-sonnet-5-5` is Active, with a tentative retirement not before 2027-09-28 (same page). It is in the models overview ([models overview](https://platform.claude.com/docs/en/about-claude/models/overview)) and in the count endpoint's model list ([token counting](https://platform.claude.com/docs/en/build-with-claude/token-counting)). From the 4.6 generation on, a dateless ID is one fixed snapshot; Anthropic does not update its weights or configuration, though serving infrastructure can change ([model IDs and versioning](https://platform.claude.com/docs/en/about-claude/models/model-ids-and-versions)).
  - The count endpoint takes the same input as Messages, including `system` and `tools`, and returns one `input_tokens` total for messages, system prompt and tools. All active models are supported. It is free but limited in requests per minute by tier (5,000 Start, 10,000 Build, 20,000 Scale), and it does not use prompt caching ([token counting](https://platform.claude.com/docs/en/build-with-claude/token-counting), "Pricing and rate limits" and FAQ). Over a limit the API returns 429; wait `retry-after` seconds before retrying ([rate limits](https://platform.claude.com/docs/en/api/rate-limits)).
  - The same page says the tokenizer introduced with Opus 4.7 counts about 30% more than earlier models. I found no statement on which tokenizer Sonnet 5.5 uses (not traced). Any change of count model breaks comparison with older counts, as the design says (§8) (inferred).
  - The envelope is not a valid conversation. `build_client_wrapped_envelope` sends the user prompt and then a user message of `tool_result` blocks with invented ids and no assistant `tool_use` (`claude_tokens.py#L207-L222`); the request carries only `model` and `messages` (`#L142`). The docs define `tool_use_id` as the id of the tool use request the result answers, sent in the user message after Claude's `tool_use` turn ([handle tool calls](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls)); all results go together in the next user message, before any text ([parallel tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use)); the documented request-time error covers `tool_use` ids with no `tool_result` right after ([troubleshooting tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/troubleshooting-tool-use)). I found no official page for the reverse case, a `tool_result` with no `tool_use`; a user report quotes a 400 "unexpected `tool_use_id` found in `tool_result` blocks" ([anthropics/claude-code#10693](https://github.com/anthropics/claude-code/issues/10693); secondary source, not verified). Two consecutive user turns are legal; the API merges them ([token counting](https://platform.claude.com/docs/en/build-with-claude/token-counting), `messages` parameter).
  - The envelope also omits `tools` and `system`, which the endpoint counts and which the design's normalized count includes (§8) (inferred: current counts understate).
  - Count failures are hidden. `_build_token_record` writes `status: "failed"` with a note (`runner.py#L387-L397`), but the report never reads the token status (`report.py#L225-L243`), so `_fmt_tokens` calls the cell pending integration (`report.py#L426-L433`). The methodology says approximate counts must not be reported as exact or used for headlines ("Token Measurement", "Honesty Rules").
  - The code sends `anthropic-version: 2023-06-01` (`claude_tokens.py#L58`). The versions page lists 2026-10-21 as newest and recommends the latest when possible; its listed changes do not touch this request ([versions](https://platform.claude.com/docs/en/api/versioning)) (inferred).
  - Claude tool names must match `^[a-zA-Z0-9_-]{1,128}$` ([define tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools)); MCP tool names may contain dots ([MCP tools spec, Tool names](https://modelcontextprotocol.io/specification/2025-11-25/server/tools#tool-names)). The bridge must map or reject dotted names before it counts or sends `tools` (inferred).
- **Recommended fix (both):**
  - Now, `benchmarks/adapters/claude_tokens.py`. Set `DEFAULT_COUNT_MODEL = "claude-sonnet-5-5"`. Build a valid envelope: the user prompt; one assistant message with a `tool_use` block per recorded call (`id`, `name`, `input` = recorded arguments); then one user message with the matching `tool_result` blocks first. On `urllib.error.HTTPError`, put the status and response body in the failure note. Mark these fixed-flow counts `approximation: true` with a note that tool definitions and system prompt are missing; the methodology asks for that label when the real client envelope is not available, and the design does not publish fixed-flow results (§3).
  - Now, `benchmarks/runner.py` `_build_token_record`. Add `count_model` and `anthropic_version` to `base_fields`, so placeholder and failed records carry them too.
  - Now, `benchmarks/report.py`. Add a `token_status` field to `CellRecord`, count `failed` separately in `_basic_stats`, show "N failed" in `_fmt_tokens`, and list token-count failures with their notes under "Failures And Exclusions".
  - New harness. Count after the run from stored transcripts, one call per model call, with `model="claude-sonnet-5-5"`, `system`, the converted `tools` and the conversation so far (design §8). Write `count_model`, API version, a sha256 of the request body and `input_tokens` into each record. On 429, wait `retry-after` and retry. If a count still fails, mark that (model, system) C2 ratio "incomplete" and list the failures; never compute it on a subset (inferred from the methodology's honesty rules).
- **Test that proves it:** extend `test_live_claude_token_counter_calls_count_tokens_endpoint_when_guard_passes` (`tests/benchmarks/test_claude_tokens.py#L200`) with `assert captured["body"]["model"] == "claude-sonnet-5-5"`; it fails today. Add an envelope test: every `tool_result` follows an assistant message that holds a `tool_use` with the same id, and no text block comes before the results; it fails today. In `test_report.py`, a token record with `status: "failed"` renders as failed, not pending; it fails today.
- **Risks and compatibility:** live counts change scale with the newer model and are not comparable with older numbers (design §8). The approximation flag takes fixed-flow counts out of the headline median, which is intended. Counting needs `ANTHROPIC_API_KEY` but costs nothing.

### 4. Competitor answers are parsed with stray punctuation

- **Current recommendation:** tighten the URL regex and add `rstrip(".,;:")`; anchor Context7 on `library ID:`.
- **Verdict:** Better alternative exists.
- **Evidence:**
  - None of the three servers returns structured content for these tools, so the adapters' structured branches never run and the regex is the live path. Context7 3.2.3 `resolve-library-id` returns one text block, "Available Libraries:" plus results ([index.ts#L190-L218](https://github.com/upstash/context7/blob/98baa58d3625e2529e000f1c9f658fff25081bbb/packages/mcp/src/index.ts#L190-L218), the commit behind tag `@upstash/context7-mcp@3.2.3`). Each result has a `- Context7-compatible library ID: <id>` line ([utils.ts#L24-L30](https://github.com/upstash/context7/blob/98baa58d3625e2529e000f1c9f658fff25081bbb/packages/mcp/src/lib/utils.ts#L24-L30)). With team library filters, a note with `https://context7.com/dashboard?tab=policies` comes first ([utils.ts#L73-L80](https://github.com/upstash/context7/blob/98baa58d3625e2529e000f1c9f658fff25081bbb/packages/mcp/src/lib/utils.ts#L73-L80)). GitMCP code search prints `- **URL**: <url>` lines ([commonTools.ts#L678](https://github.com/idosal/git-mcp/blob/c487a29895dcfcb5b672247e646426a56e2051c1/src/api/tools/commonTools.ts#L678)), and its doc search prints `#### (<file>)[<url>] (Score: ...)` ([commonTools.ts#L480](https://github.com/idosal/git-mcp/blob/c487a29895dcfcb5b672247e646426a56e2051c1/src/api/tools/commonTools.ts#L480)), at the pinned commit `c487a29`. Ref.tools returns one text block per hit with `overview:`, `url:` and `moduleId:` lines ([index.ts#L301-L308](https://github.com/ref-tools/ref-tools-mcp/blob/f5feb829cc7bdd18fb9b276c27e3f391b2c4132c/index.ts#L301-L308) on `main`; version 3.0.3 has no tag and the remote backend is unpinned, so this is not pinned).
  - Local check of the current regexes on these formats: Context7's pattern returns `/context7.com/dashboard` when the filter note is present. The URL pattern returns `...pairwise).` for a markdown link, `...README.rst]` for GitMCP's doc-search line, and `...os.html>,` for an angle-bracketed URL. `rstrip(".,;:")` fixes none of these three.
  - RFC 3986: delimiters around a URI are not part of it, and software that reads URIs from text should strip them ([Appendix C](https://www.rfc-editor.org/rfc/rfc3986#appendix-C)). Square brackets are legal only around an IP-literal host, "the only place where square bracket characters are allowed in the URI syntax" ([§3.2.2](https://www.rfc-editor.org/rfc/rfc3986#section-3.2.2)). `)`, `,`, `;` and `'` are legal sub-delimiters ([§2.2](https://www.rfc-editor.org/rfc/rfc3986#section-2.2)), so a trailing `)` cannot be dropped blindly.
  - GFM autolinks drop trailing `?`, `!`, `.`, `,`, `:`, `*`, `_` and `~`; drop a trailing `)` only while closing parentheses outnumber opening ones; and end a URL at `<` ([GFM spec, Autolinks (extension)](https://github.github.com/gfm/#autolinks-extension-); [cmark-gfm spec.txt#L9229-L9246](https://github.com/github/cmark-gfm/blob/27d942c8b0a62d192f616e5bf3578f4b6a89e180/test/spec.txt#L9229-L9246)).
  - CommonMark: a link destination is either `<...>` or text whose parentheses are escaped or balanced ([CommonMark 0.31.2, link destination](https://spec.commonmark.org/0.31.2/#link-destination)).
  - MCP spec: a tool that returns `structuredContent` should also return the JSON as text; with an output schema, servers must conform and clients should validate ([MCP tools spec, Structured Content](https://modelcontextprotocol.io/specification/2025-11-25/server/tools#structured-content)). The design's canonical result rule (structured content when present, else text, §6) matches this (inferred).
  - The design deletes these adapters once the bridge covers `python-docs` and `docs-fetch`, and runs their systems only in round 2 (§3, §5, §11.1), so these regexes never run live before deletion (inferred). URL parsing still matters: grounding must normalize every cited URL in answers and every page in tool arguments and results (§9.2).
- **Recommended fix (new harness; now only if a fixed-flow live run is planned):**
  - Flow control needs no parsing, because the model reads the tool text. Keep structured content first (§6).
  - Grounding normalizer (a new module under `benchmarks/`): take markdown link destinations per CommonMark (angle form or balanced parentheses); take bare URLs with ``https?://[^\s<>"'`{}|\\^\[\]]+``, then strip GFM's trailing punctuation and any unmatched trailing `)`; parse with `urllib.parse.urlsplit` into (version, page path, anchor). Where the harness itself needs a field, anchor on its label, multiline: `^- Context7-compatible library ID: (\S+)$` and `^url: (\S+)$`. The excluded characters are ones RFC 3986 does not allow in a URI (brackets only around an IP-literal host, which docs URLs never use), plus the single quote, which is legal but acts as a delimiter in tool text (inferred from §2.2, §2.3 and §3.2.2).
  - If the old adapters must run first, apply the same rules in `gitmcp_adapter._extract_first_url`, `ref_tools_adapter._extract_first_url` (anchor on `url:`) and `context7_adapter._extract_library_id`.
- **Test that proves it:** a table test of the extractor. `[pairwise](https://docs.python.org/3/library/itertools.html#itertools.pairwise).` gives no `).`; the GitMCP doc-search line gives no `]`; `<https://docs.python.org/3/library/os.html>,` gives no `>,`; `**https://docs.python.org/3/library/re.html**` gives no `*`; `https://en.wikipedia.org/wiki/Python_(programming_language)` keeps its `)` as a guard against over-stripping. Context7 text with the filter note gives `/python/cpython`. Today's patterns fail the five punctuation cases; a local prototype of the rules above passed all six.
- **Risks and compatibility:** these rules are heuristics; a URL that really ends in `.` or an unbalanced `)` would be cut, which is rare for docs.python.org (inferred). Servers can change text formats without notice (Ref.tools and GitMCP are unpinned), so keep the raw tool text and re-parse offline.

### 5. Smaller benchmark items

- **Current recommendation:** (a) score against the run's `snapshots/corpus.yml`; (b) close the per-cell `httpx2.AsyncClient`; (c) widen `deny_network`; (d) decide whether to deduplicate the five adapters now.
- **Verdict:** (a) Aligned. (b) Aligned, new harness only. (c) Aligned with changes. (d) Wait, except for the shared failure helper.
- **Evidence:**
  - (a) `score_run` and `ingest_adjudication_verdicts` load answer keys from whatever `--corpus` names (`benchmarks/scoring.py#L110`, `#L140`, `#L232`, `#L260`; `benchmarks/__main__.py#L107-L112`, `#L137-L142`). The runner snapshots the corpus (`runner.py#L119`), the report publishes that snapshot's sha256 (`report.py#L200`, `#L475`), and the regression gate already rejects a changed corpus hash (`benchmarks/regression.py#L79`, `#L90-L91`). The methodology forbids changing the corpus after results unless the change is documented and the benchmark rerun ("Honesty Rules"); the design hashes every grading input (§9.1, §11.3). Pinning inputs by hash is established practice; pip's hash-checking mode uses it against tampering ([pip secure installs](https://pip.pypa.io/en/stable/topics/secure-installs/)).
  - (b) At v2.3.0 the SDK closes only a client it created itself ([streamable_http.py#L747-L761](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/client/streamable_http.py#L747-L761)). httpx2 recommends a context-managed client or an explicit `await client.aclose()`, and warns against creating clients in a hot loop ([httpx2 async.md#L48-L66](https://github.com/pydantic/httpx2/blob/v2.16.0/docs/async.md#L48-L66), [clients.md#L33-L49](https://github.com/pydantic/httpx2/blob/v2.16.0/docs/advanced/clients.md#L33-L49)). The leak happens only when headers are set: Context7 in HTTP key mode, and Ref.tools always (`benchmarks/adapters/context7_adapter.py#L248-L252`, `ref_tools_adapter.py#L215-L219`).
  - (c) The audit events table has no `socket.connect_ex` event; `connect_ex` raises `socket.connect`. The hook misses `socket.sendto` and `socket.sendmsg` (UDP needs no connect), `socket.bind`, `socket.gethostbyname`, `socket.gethostbyaddr` and `socket.getnameinfo`, and all process-related events, such as `subprocess.Popen`, `os.system`, `os.exec`, `os.posix_spawn`, `os.spawn`, `os.fork`, `os.forkpty`, `pty.spawn`, `os.startfile` and `webbrowser.open` ([audit events table](https://docs.python.org/3.12/library/audit_events.html)). A hook covers the current (sub)interpreter only, and hooks are "not suitable for implementing a 'sandbox'" ([sys.addaudithook](https://docs.python.org/3.12/library/sys.html#sys.addaudithook)). PEP 578 says the feature reports use rather than restricting it, and "Hooks cannot be removed or replaced." ([PEP 578](https://peps.python.org/pep-0578/)). A child process starts without the parent's hook, so it can reach the network unseen (inferred).
  - (d) The design deletes the five adapters and their tests once the bridge covers `python-docs` and `docs-fetch` (§11.1). Google's review guide puts refactorings in a separate change from bug fixes ([Small CLs, Separate Out Refactorings](https://google.github.io/eng-practices/review/developer/small-cls.html#refactoring)).
- **Recommended fix:**
  - (a) Now: default `--corpus` to `run_dir/snapshots/corpus.yml`; if a path is given, refuse it unless its sha256 matches the snapshot; write the hash into `scoring-rollups.json`.
  - (b) New harness: the bridge opens `async with httpx2.AsyncClient(...) as client:` around `streamable_http_client(url, http_client=client)`. Fix now only if these adapters will run: turn each HTTP `_transport_factory` into an `@asynccontextmanager` that enters both.
  - (c) Now: add the missing network and process events, drop `socket.connect_ex`, and append each attempt to a module-level list before raising; `main()` fails when the list is not empty, even if library code swallowed the `RuntimeError` (inferred). Treat it as a tripwire; the benchmark's real egress control is the design's internal network and proxy (§7.3).
  - (d) Wait: no deduplication before deletion. The one exception is the failure helper from finding 1, because the bug is identical in all five ladders and the bridge reuses it.
- **Test that proves it:**
  - (a) A run directory with snapshot A, and `score_run(..., corpus_path=B)` where B changes one answer key, raises `BenchmarkValidationError`; today it scores silently.
  - (b) Patch `streamable_http_client` with a fake async context manager that captures `http_client`; after the factory's context exits, `client.is_closed` is true; today it is false.
  - (c) Call `deny_network("socket.sendto", ())` and `deny_network("subprocess.Popen", ())` directly and expect `RuntimeError`; today they pass. Never install the hook in the pytest process, because hooks cannot be removed.
  - (d) None needed.
- **Risks and compatibility:** (a) a script that passes a different corpus now fails loudly, which is the point. (c) blocking process creation can trip on a library that starts a helper process during evaluation; the gate then fails loudly and the event list can be adjusted (inferred).

### 6. General practices for executing these fixes

- **Current recommendation:** small single-purpose changes, a regression test with each bug fix, refactors kept apart from behaviour changes, and changelog and versioning entries for user-visible changes.
- **Verdict:** Aligned.
- **Evidence:**
  - Google's review guide: a change should be one self-contained change that does one thing, with its tests in the same change; refactorings go in their own change ([Small CLs](https://google.github.io/eng-practices/review/developer/small-cls.html), [test code](https://google.github.io/eng-practices/review/developer/small-cls.html#test-code)).
  - Software Engineering at Google: "the bug fix should include that missing test case" ([ch. 12, Unit Testing](https://abseil.io/resources/swe-book/html/ch12.html)); a pure refactor should not need test changes (same chapter); a bug fix should focus only on the bug ([ch. 9, Code Review](https://abseil.io/resources/swe-book/html/ch09.html)). Writing the test first and seeing it fail proves that it catches the bug (inferred).
  - Keep a Changelog 1.1.0: changelogs are written for people, entries are grouped as Added, Changed, Deprecated, Removed, Fixed and Security, and an Unreleased section sits at the top ([keepachangelog.com](https://keepachangelog.com/en/1.1.0/)). SemVer 2.0.0: software must declare a public API; in 0.y.z anything may change; a patch release is for backward-compatible fixes ([semver.org](https://semver.org/spec/v2.0.0.html)).
  - The project already follows both (`CHANGELOG.md#L1-L5`). The wheel ships only `src/mcp_server_python_docs` (`pyproject.toml#L52-L53`), so `benchmarks/` fixes are not part of the package's API.
- **Recommended fix (both):** one PR per finding, each with the test that fails on its parent commit. A sensible order (inferred): failure helper; report statistics, flag, alias and exclusions; token pin, envelope and status; scoring snapshot and audit hook. Do the adapter regex and httpx2 fixes only if a fixed-flow live run happens. Record changes to how public benchmark numbers are computed in the methodology amendment the design requires (§11.2), not as a version bump. For server changes, add a "Changed" entry when a tool description changes, since every client's model reads it and pays for its tokens, and when an index rebuild is required, with the exact rebuild command (inferred). Declare the API that SemVer asks for: tool names, parameters and result shapes (kept fixed by design §3), plus index compatibility (inferred).
- **Test that proves it:** each PR shows its new test failing on the parent commit and passing with the fix, and the canonical gate from `AGENTS.md` passes (design §13).
- **Risks and compatibility:** more PRs mean more review rounds, but each one is faster to review ([Small CLs](https://google.github.io/eng-practices/review/developer/small-cls.html)).

### Sources

External, primary:

- https://github.com/agronholm/anyio/blob/4.14.2/docs/versionhistory.rst#L614-L616
- https://github.com/agronholm/anyio/blob/4.14.2/src/anyio/_backends/_asyncio.py#L778
- https://github.com/agronholm/anyio/blob/4.14.2/src/anyio/_backends/_asyncio.py#L815-L817
- https://anyio.readthedocs.io/en/stable/tasks.html#handling-multiple-errors-in-a-task-group
- https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/client/session.py#L504-L519
- https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/client/session.py#L1101-L1108
- https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/client/session.py#L1140-L1154
- https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/client/stdio.py#L201-L213
- https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/client/streamable_http.py#L747-L761
- https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/client/streamable_http.py#L771
- https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/shared/jsonrpc_dispatcher.py#L413
- https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/shared/jsonrpc_dispatcher.py#L421-L427
- https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/shared/jsonrpc_dispatcher.py#L442
- https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/shared/jsonrpc_dispatcher.py#L461
- https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md#L1975
- https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md#L2229
- https://modelcontextprotocol.io/specification/2025-11-25/server/tools#error-handling
- https://modelcontextprotocol.io/specification/2025-11-25/server/tools#structured-content
- https://modelcontextprotocol.io/specification/2025-11-25/server/tools#tool-names
- https://docs.python.org/3/reference/compound_stmts.html#except-star
- https://peps.python.org/pep-0654/#raising-new-exceptions
- https://peps.python.org/pep-0654/#handling-exception-groups
- https://docs.python.org/3/library/exceptions.html#BaseExceptionGroup.subgroup
- https://docs.python.org/3.12/library/asyncio-task.html#asyncio.wait_for
- https://docs.python.org/3.12/library/asyncio-task.html#asyncio.Timeout.expired
- https://sre.google/sre-book/monitoring-distributed-systems/
- https://gernot-heiser.org/benchmarking-crimes.html#sim
- https://gernot-heiser.org/benchmarking-crimes.html#base
- https://gernot-heiser.org/benchmarking-crimes.html#subset
- https://platform.claude.com/docs/en/about-claude/model-deprecations
- https://platform.claude.com/docs/en/about-claude/models/overview
- https://platform.claude.com/docs/en/about-claude/models/model-ids-and-versions
- https://platform.claude.com/docs/en/build-with-claude/token-counting
- https://platform.claude.com/docs/en/api/rate-limits
- https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls
- https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use
- https://platform.claude.com/docs/en/agents-and-tools/tool-use/troubleshooting-tool-use
- https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools
- https://platform.claude.com/docs/en/api/versioning
- https://github.com/upstash/context7/blob/98baa58d3625e2529e000f1c9f658fff25081bbb/packages/mcp/src/index.ts#L190-L218
- https://github.com/upstash/context7/blob/98baa58d3625e2529e000f1c9f658fff25081bbb/packages/mcp/src/lib/utils.ts#L24-L30
- https://github.com/upstash/context7/blob/98baa58d3625e2529e000f1c9f658fff25081bbb/packages/mcp/src/lib/utils.ts#L73-L80
- https://github.com/idosal/git-mcp/blob/c487a29895dcfcb5b672247e646426a56e2051c1/src/api/tools/commonTools.ts#L480
- https://github.com/idosal/git-mcp/blob/c487a29895dcfcb5b672247e646426a56e2051c1/src/api/tools/commonTools.ts#L678
- https://github.com/ref-tools/ref-tools-mcp/blob/f5feb829cc7bdd18fb9b276c27e3f391b2c4132c/index.ts#L301-L308
- https://www.rfc-editor.org/rfc/rfc3986#appendix-C
- https://www.rfc-editor.org/rfc/rfc3986#section-2.2
- https://www.rfc-editor.org/rfc/rfc3986#section-2.3
- https://www.rfc-editor.org/rfc/rfc3986#section-3.2.2
- https://github.github.com/gfm/#autolinks-extension-
- https://github.com/github/cmark-gfm/blob/27d942c8b0a62d192f616e5bf3578f4b6a89e180/test/spec.txt#L9229-L9246
- https://spec.commonmark.org/0.31.2/#link-destination
- https://pip.pypa.io/en/stable/topics/secure-installs/
- https://github.com/pydantic/httpx2/blob/v2.16.0/docs/async.md#L48-L66
- https://github.com/pydantic/httpx2/blob/v2.16.0/docs/advanced/clients.md#L33-L49
- https://docs.python.org/3.12/library/audit_events.html
- https://docs.python.org/3.12/library/sys.html#sys.addaudithook
- https://peps.python.org/pep-0578/
- https://google.github.io/eng-practices/review/developer/small-cls.html
- https://google.github.io/eng-practices/review/developer/small-cls.html#refactoring
- https://google.github.io/eng-practices/review/developer/small-cls.html#test-code
- https://abseil.io/resources/swe-book/html/ch12.html
- https://abseil.io/resources/swe-book/html/ch09.html
- https://keepachangelog.com/en/1.1.0/
- https://semver.org/spec/v2.0.0.html

Secondary (cited only to flag an untraced claim):

- https://github.com/anthropics/claude-code/issues/10693

Repository files (worktree commit `c03e497`; design at `68a7086`):

- `benchmarks/adapters/python_docs_mcp_adapter.py`, `context7_adapter.py`, `gitmcp_adapter.py`, `deepwiki_adapter.py`, `ref_tools_adapter.py`, `claude_tokens.py`, `eligibility.py`
- `benchmarks/runner.py`, `report.py`, `scoring.py`, `__main__.py`, `regression.py`
- `tests/benchmarks/test_python_docs_mcp_adapter.py`, `test_competitor_adapters.py`, `test_claude_tokens.py`, `test_report.py`, `test_runner.py`, `test_regression.py`
- `docs/benchmarks/PUBLIC-BENCHMARK-METHODOLOGY.md`, `docs/benchmarks/competitor-manifest.template.yml`, `CHANGELOG.md`, `pyproject.toml`
- `docs/benchmarks/AGENTIC-BENCHMARK-DESIGN.md` (branch `docs/agentic-benchmark-design`)
