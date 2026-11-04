#!/usr/bin/env bash
# Build the sandbox image from HEAD, pull the pinned proxy, then run agentic-run in containers.
# A dry run unless the operator adds the live flag. IMAGE=sha256:... skips the build (the
# air-gapped arm).
# KEYS_FILE (default .env.local) is read only by the container launcher, one key per worker,
# into a bootstrap frame on that worker's stdin.
set -euo pipefail
for name in ANTHROPIC_API_KEY OPENAI_API_KEY GOOGLE_API_KEY; do
  if [[ -n ${!name:-} ]]; then
    echo "run.sh: unset $name before starting the sandbox" >&2
    exit 2
  fi
done
cd "$(git rev-parse --show-toplevel)"
for tool in git docker uv cat; do
  command -v "$tool" >/dev/null || { echo "run.sh: $tool not found" >&2; exit 2; }
done
sandbox_docker() {
  uv run --locked python -c '
import os, sys
from benchmarks.agentic.sandbox import cli_env
os.execvpe("docker", ["docker", *sys.argv[1:]], cli_env(os.environ))
' "$@"
}
if [ -z "${IMAGE:-}" ]; then
  iid=$(mktemp)
  trap 'rm -f "$iid"' EXIT
  git archive --format=tar HEAD | sandbox_docker build --file benchmarks/sandbox/Dockerfile \
    --label "org.opencontainers.image.revision=$(git rev-parse HEAD)" --iidfile "$iid" -
  IMAGE=$(cat "$iid"); rm -f "$iid"; trap - EXIT
  sandbox_docker pull "$(uv run --locked python -c 'from benchmarks.agentic.sandbox import SQUID_IMAGE; print(SQUID_IMAGE)')"
fi
echo "run.sh: image $IMAGE" >&2
exec uv run --locked python -m benchmarks agentic-run --launcher container --image "$IMAGE" \
  --keys-file "${KEYS_FILE:-.env.local}" "$@"
