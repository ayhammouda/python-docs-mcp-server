#!/usr/bin/env bash
set -euo pipefail
# Linux amd64 release runners only. Deliberate version/digest update required.
curl --fail --location --proto '=https' --tlsv1.2 \
  https://github.com/modelcontextprotocol/registry/releases/download/v1.8.1/mcp-publisher_linux_amd64.tar.gz \
  --output "${RUNNER_TEMP}/mcp-publisher.tar.gz"
echo "a06c9096dcb9727c13555b6be26c7effa707b01f06a4c561ba7a3635443cf2cc  ${RUNNER_TEMP}/mcp-publisher.tar.gz" | sha256sum --check
tar -xzf "${RUNNER_TEMP}/mcp-publisher.tar.gz" -C "${RUNNER_TEMP}" mcp-publisher
