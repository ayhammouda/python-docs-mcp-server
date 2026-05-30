# ADR-001: Source Adapters

- **Status:** Accepted
- **Date:** 2026-05-29
- **Deciders:** @ayhammouda
- **Roadmap refs:** principles 2.1, 2.2, 2.7

## Context and Problem Statement

`python-docs-mcp-server` needs documentation answers that are precise,
version-aware, and trustworthy inside MCP clients. The project therefore cannot
treat "source" as an arbitrary search result or scraped mirror. The first layer
of the architecture is a source-connector layer that accepts a version or
package identifier, reaches only canonical upstream sources, and hands stable
artifacts to ingestion.

Two source adapters exist today:

- CPython documentation source: `build-index` uses pinned CPython documentation
  build targets from
  [`src/mcp_server_python_docs/ingestion/cpython_versions.py`](../../src/mcp_server_python_docs/ingestion/cpython_versions.py),
  clones `python/cpython` at the configured tag, installs the configured Sphinx
  pin in a dedicated build virtual environment, and runs `sphinx-build -b json`
  before ingesting generated JSON files through
  [`src/mcp_server_python_docs/ingestion/sphinx_json.py`](../../src/mcp_server_python_docs/ingestion/sphinx_json.py).
  Symbol inventory ingestion uses `objects.inv` through
  [`src/mcp_server_python_docs/ingestion/inventory.py`](../../src/mcp_server_python_docs/ingestion/inventory.py).
- PyPI metadata source:
  [`src/mcp_server_python_docs/services/package_docs.py`](../../src/mcp_server_python_docs/services/package_docs.py)
  backs `lookup_package_docs` with `GET /pypi/<project>/json` from the official
  PyPI JSON API. It returns package-declared PyPI, documentation, homepage,
  source, and repository URLs from controlled metadata fields and does not crawl
  pages or perform generic web search.

This ADR records the contract for those adapters so later documentation
ecosystems can clone the layer boundary without weakening the trust model.

## Decision Drivers

- Principle 2.1: canonical source only. CPython comes from pinned upstream tags;
  PyPI package links come from PyPI project metadata. Scraped mirrors and
  third-party indexers are outside the contract.
- Principle 2.2: offline-first runtime. MCP docs queries should read the local
  index and cache, not reach remote documentation services at query time.
- Principle 2.7: layered design with stable contracts. Source connectors must
  have explicit inputs, outputs, and invariants so ingestion and downstream
  retrieval layers do not depend on source-specific behavior.
- The contract must describe current behavior only. Future adapters, such as
  other language ecosystems, should clone the contract rather than be documented
  as existing features.

## Considered Options

1. Keep source behavior implicit in ingestion and service code.
   - Rejected because future work would have to infer the trust boundary from
     implementation details, increasing the chance of accidental mirror,
     indexer, or runtime-network drift.
2. Allow generic web or third-party docs providers as source adapters.
   - Rejected because this conflicts with principle 2.1 and would make results
     less reproducible and less auditable.
3. Document a narrow source-connector contract for the adapters that exist
   today.
   - Accepted because it matches the current code and gives future adapters a
     stable layer boundary to copy.

## Decision Outcome
<!-- Canonical source only; pinned, reproducible; PyPI metadata is the one
     controlled network lookup and is not a query-time call. -->

The source-connector layer is limited to canonical upstream sources. CPython
documentation builds are pinned by version-specific CPython tags and Sphinx
pins, then converted into canonical ingestion artifacts by the build pipeline.
PyPI package documentation discovery is limited to the official PyPI JSON API
and allowlisted project metadata fields.

`lookup_package_docs` is the documented exception to the offline-first rule: it
performs a controlled PyPI metadata lookup when the package lookup runs. That is
a build/lookup-time metadata call, not a docs-query-time call against the local
stdlib documentation index, and it is not a general-purpose web fetch.

Future source adapters should clone this contract: accept a stable identifier,
retrieve canonical upstream artifacts, hand those artifacts to ingestion, and
avoid third-party indexers or scraped mirrors.

### Consequences

**Positive:** The source boundary is auditable, reproducible, and easy to test
against roadmap principles. CPython docs builds can be rebuilt from pinned
upstream tags, and PyPI package URLs are traceable to package-declared metadata.
Downstream ingestion, storage, retrieval, budget, serializer, cache, and
transport layers can rely on source artifacts without knowing source-specific
network details.

**Negative / risks:** CPython builds depend on GitHub availability and the
ability to build each pinned CPython docs tree with the configured Sphinx pin.
PyPI metadata quality depends on what each package declares, so results may be
missing, stale, or incomplete. The `lookup_package_docs` exception must remain
narrow; expanding it into page crawling or arbitrary web search would violate
the contract.

## Layer Contract (principle 2.7)

- **Inputs:** A stable source identifier. For CPython documentation, the input
  is a supported Python `X.Y` version resolved through
  `CPYTHON_DOCS_BUILD_CONFIG`. For PyPI metadata, the input is a package name
  normalized into a PyPI project identifier.
- **Outputs:** Canonical artifacts handed to ingestion or presentation. CPython
  outputs are `objects.inv` symbol data and Sphinx JSON documentation pages that
  ingestion stores in the local index. PyPI outputs are package-declared project,
  documentation, homepage, source, and repository URLs plus the metadata source
  URL returned by `lookup_package_docs`.
- **Invariants:** Source adapters use canonical upstreams only; CPython content
  is pinned and reproducible by tag and Sphinx pin; docs queries use local
  indexed artifacts and do not call remote documentation services at query time;
  PyPI metadata lookup is the sole documented network exception; adapters do not
  use scraped mirrors, third-party indexers, generic web search, or silent
  fallback sources.

## Links

- STRATEGIC-ROADMAP-2026-05-29.md §2.1, §2.2, §2.7
- [`src/mcp_server_python_docs/ingestion/cpython_versions.py`](../../src/mcp_server_python_docs/ingestion/cpython_versions.py)
- [`src/mcp_server_python_docs/__main__.py`](../../src/mcp_server_python_docs/__main__.py)
- [`src/mcp_server_python_docs/ingestion/sphinx_json.py`](../../src/mcp_server_python_docs/ingestion/sphinx_json.py)
- [`src/mcp_server_python_docs/ingestion/inventory.py`](../../src/mcp_server_python_docs/ingestion/inventory.py)
- [`src/mcp_server_python_docs/services/package_docs.py`](../../src/mcp_server_python_docs/services/package_docs.py)
- [`README.md`](../../README.md) "Why not Context7 or generic docs retrieval?"
  and "PyPI package docs lookup"
