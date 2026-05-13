# Security Policy

## Supported Versions

Security fixes are provided for the latest released version of
`python-docs-mcp-server`.

## Reporting a Vulnerability

Please report suspected vulnerabilities privately. Use GitHub private
vulnerability reporting if it is enabled for this repository, or email
`hammouda.aymen@gmail.com` with:

- a description of the issue
- reproduction steps or proof of concept, if available
- affected versions or commits
- any recommended mitigation

Please do not open a public issue for an unpatched vulnerability.

## Scope

This project is a read-only local MCP server. Security-sensitive areas include:

- dependency supply-chain vulnerabilities
- unsafe parsing or handling of downloaded documentation artifacts
- filesystem writes outside the configured cache/index locations
- MCP tool behavior that could expose data beyond the Python documentation index
