# YAML Trust Boundary

`src/mcp_server_python_docs/data/synonyms.yaml` is the project's only packaged
YAML data input. It is shipped inside the wheel and read through
`importlib.resources`; users do not provide YAML at runtime.

The file is parsed only with `yaml.safe_load`, in one call site:
`src/mcp_server_python_docs/server.py` when the MCP server starts.

There are no `yaml.load` or `yaml.unsafe_load` parser call sites in `src/` or
`tests/`. The regression test
`tests/test_synonyms.py::test_yaml_loaded_only_via_safe_load` scans source files
and tests for unsafe YAML loaders, confirms the expected source `safe_load`
call site, and asserts that `synonyms.yaml` is the only YAML file under
`src/mcp_server_python_docs/`.

Recommended future `SECURITY.md` wording for human review:

> The server parses only one packaged YAML input, `synonyms.yaml`, using
> `yaml.safe_load`; user-supplied YAML is not accepted.
