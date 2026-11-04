# Benchmark sandbox tooling: pins and documented behaviour

**Date:** 2026-11-04. **For:** `docs/plans/2026-11-04-agentic-sandbox-runner.md`
(PR 4 of `docs/benchmarks/AGENTIC-BENCHMARK-DESIGN.md` §12).

Every value below was read from the vendor's own page, registry or API on
2026-11-04. The age rule is the project's: a pinned version or image digest
must be at least 14 days old, so published on or before **2026-10-21**,
unless a security advisory requires a newer one. Where the docs are silent,
this note says "not documented" and names the check that settles it.

## A. Pins

| Item | Pin | Published | Source |
|---|---|---|---|
| Python base image | `python:3.14.8-slim-trixie@sha256:fbecd461fc72d21b2143c6eaba25143738079154a6d0ea3da3aec724f50eed4a` (index; arm64 `sha256:eaecc1a821ac5f032c7cc29d6fdd2cca4199ca97f8d11ea79417e9f647dbd809`) | pushed 2026-10-21T08:09Z | https://hub.docker.com/v2/repositories/library/python/tags/3.14.8-slim-trixie |
| uv | `ghcr.io/astral-sh/uv:0.13.4@sha256:9f130421e930bfbdc563c00740466603f8723158e90297d07957c2294640262b` (index) | release 2026-10-20T15:24Z | https://github.com/astral-sh/uv/releases/tag/0.13.4 ; digest from the ghcr.io registry manifest |
| Squid proxy image | `ubuntu/squid:6.6-24.04_edge@sha256:8a3baed477e2c282ab8aa5edad442f69873246964f225c5c2ae8364b6610963c` (index; arm64 `sha256:2580be07409d64c1b5bff32d218649771a53616b06ef102bc3005e81534beccf`), Squid 6.6 | pushed 2026-05-07T21:56Z | https://hub.docker.com/v2/repositories/ubuntu/squid/tags/6.6-24.04_edge |
| `mcp-server-fetch` | `2026.10.19`, `requires_dist` includes `mcp<3,>=2.3` and `httpx[socks]>=0.27` | 2026-10-19T06:48Z | https://pypi.org/pypi/mcp-server-fetch/2026.10.19/json |
| Docker Desktop (maintainer's machine) | 4.96.1, bundling Engine 29.9.2 | 2026-10-21 | https://docs.docker.com/desktop/release-notes/ |
| Ollama | v0.40.8 is the newest release on or before 2026-10-21 (2026-10-21T16:20Z); v0.40.14 (2026-11-03) changes OpenAI-endpoint sampler defaults, see D.3 | as stated | https://github.com/ollama/ollama/releases/tag/v0.40.8 , https://github.com/ollama/ollama/releases/tag/v0.40.14 |

Notes on the pins:

- `python:3.14-slim` now points at 3.14.10 (pushed 2026-11-02) and 3.14.9
  was pushed 2026-10-22: both are too new. 3.14.8 is exactly 14 days old.
- The Squid image is Canonical's (Docker Hub "Verified Publisher"). There
  is no Docker Official Squid image. Its config runs `entrypoint.sh` as
  root (`User` is empty), which calls `make-ssl-cert` and needs root, so
  the sandbox overrides the entrypoint with `/usr/sbin/squid`.
- `mcp-server-fetch` releases since 2026-09-01: 2026.10.10 (2026-10-11),
  2026.10.19, 2026.10.24, 2026.10.28. **2026.10.10 still required
  `mcp<2,>=1.29.0`**; 2026.10.19 is the first on the 2.x SDK, so it is
  also the release the design §5.1 note describes.
- `ollama.com/library/gemma4:12b` exists (7.7–8.0 GB, 256K native context,
  tools label). Its tag page lists two builds with different digests, an
  MLX build (`c7597fc90b86`) and a llama.cpp build (`6114515d63c1`).
  MLX is the Apple Silicon default since v0.40.0. The run records the
  digest `/api/tags` reports instead of assuming one.

## B. Docker

1. **Internal networks.** `docker network create --internal`: no default
   route; firewall rules drop traffic to other networks. But
   "Communication with the gateway IP address (and thus appropriately
   configured host services) is possible."
   https://docs.docker.com/reference/cli/docker/network/create/
2. **Isolated gateway mode.** `-o com.docker.network.bridge.gateway_mode_ipv4=isolated`
   is allowed only with `--internal`; "No address is assigned to the
   bridge". https://docs.docker.com/engine/network/port-publishing/ . The
   Engine 28 release notes introduce it: `gateway_mode_ipv4` and
   `gateway_mode_ipv6` "now accept mode isolated, when the network is
   also internal" (moby/moby#49262). So it needs Engine 28.0 or newer.
   https://docs.docker.com/engine/release-notes/28/
3. **DNS on internal networks.** Engine 26.0.0 and 25.0.5 stopped
   forwarding external names for containers attached only to internal
   networks (CVE-2024-29018, moby/moby#47589). From Engine 26 on, the
   embedded DNS server does not forward external names for a container
   attached only to internal networks, so such a container should not
   resolve them. The advisory's "Docker Desktop is not affected" refers to
   the earlier leak, which Desktop's own internal resolver avoided; it
   does not describe a different behaviour now. The maintainer runs
   Engine 29.9.2. The isolation probe's DNS check still decides.
   https://docs.docker.com/engine/release-notes/26.0/ ,
   https://github.com/moby/moby/security/advisories/GHSA-mq39-4gv4-mvpx
4. **`host.docker.internal`** "Resolves to the internal IP address of your
   host" on Docker Desktop. Whether it reaches a Mac service bound only to
   127.0.0.1 is **not documented**.
   https://docs.docker.com/desktop/features/networking/networking-how-tos/
5. **`docker run`** (https://docs.docker.com/reference/cli/docker/container/run/,
   https://docs.docker.com/engine/storage/tmpfs/):
   `--read-only`; `--tmpfs` takes mount options including `mode`, `uid`,
   `gid`, `size`; `--mount type=tmpfs` has `tmpfs-mode` (default `1777`)
   and `tmpfs-size`; `--cap-drop`; `--security-opt no-new-privileges`;
   `--user`; `--memory`; `--cpus`; `--pids-limit`; `--init` "forwards
   signals and reaps processes"; `--rm`; `-i` without `-t`. With `-e NAME`
   and no value, the CLI takes the value from its own environment, and an
   unset name stays unset in the container. A single-file bind mount
   inside a tmpfs mount is **not documented**; whether stdout through
   `docker run -i` is binary-safe for large frames is **not documented**.
6. **Proxy injection.** The CLI's `~/.docker/config.json` `proxies` keys
   set `HTTP_PROXY`/`http_proxy`, `HTTPS_PROXY`, `FTP_PROXY`, `NO_PROXY`
   and `ALL_PROXY`, upper and lower case, in **new containers** and as
   build args. https://docs.docker.com/engine/cli/proxy/ . The CLI source
   fills each name only when it is not already set (`ParseProxyConfig` in
   `cli/config/configfile/file.go`).
7. **Build from a tarball.** With a tarball context on stdin, `--file` is
   "relative to the root of the tarball".
   https://docs.docker.com/build/building/context/ ; `--iidfile` and
   `--label`: https://docs.docker.com/reference/cli/docker/buildx/build/ .
   Predefined proxy build args are left out of `docker history`; `ENV`
   values persist in the image. https://docs.docker.com/reference/dockerfile/
8. **Docker Desktop resources.** The VM memory limit "Defaults to 50% of
   your host's memory" (8 GB on a 16 GB Mac).
   https://docs.docker.com/desktop/settings-and-maintenance/settings/
9. **Podman and Colima.** Podman's `--internal` adds no default route and
   its DNS resolves only container names; under `podman machine` on macOS,
   `host.containers.internal` comes from gvproxy.
   https://docs.podman.io/en/latest/markdown/podman-network-create.1.html .
   Colima documents `host.docker.internal` only in an issue thread
   (https://github.com/abiosoft/colima/issues/277). Neither is the
   maintainer's active context (`desktop-linux`).

## C. Squid

Directive pages: https://www.squid-cache.org/Doc/config/ (they describe v7;
every directive and format code used here is also in the v6 manual,
https://www.squid-cache.org/Versions/v6/cfgman/).

- `acl NAME dstdomain [-n] host` matches the URL host "[fast]", with no
  DNS lookup for a named host. But "a reverse lookup is tried if a IP
  based URL is used and no match is found", so a CONNECT to an IP literal
  whose PTR names an allowlisted host could match. `-n` will "Disable
  lookups and address type conversions": the ACL then declares a
  mismatch. Every allowlist ACL uses `-n`. `acl NAME port N` and
  `acl NAME method CONNECT` are also fast. The `dst` type is slow and
  resolves names; avoid it. https://www.squid-cache.org/Doc/config/acl/
- `logfile_rotate 0` disables file-name rotation on `squid -k rotate`.
  From Squid 4 it is only the default for `stdio:` access logs, which
  also take their own `rotate=N` option.
  https://www.squid-cache.org/Doc/config/logfile_rotate/
- `http_access` lines are checked in order; with no match the default is
  the opposite of the last line, so the config ends with
  `http_access deny all`.
- `access_log stdio:<path> logformat=<name>` writes each line at once
  (`buffered_logs` applies only to the daemon, tcp and udp modules).
- `logformat` codes: `%tg` GMT time with a strftime argument, `%tu`
  sub-second milliseconds, `%>a` client IP, `%rm` method, `%>rd` request
  domain, `%>rP` request port, `%>Hs` status sent, `%Ss` Squid result
  (`TCP_DENIED`, `TCP_TUNNEL`, ...; https://wiki.squid-cache.org/SquidFaq/SquidLogs).
- `pid_filename none` disables the pid file; `cache deny all` disables
  caching; `shutdown_lifetime` (default 30 s) is how long Squid waits for
  open clients after SIGTERM; `cache_effective_user` applies only when
  Squid starts as root.
- **Exit status after SIGTERM.** The directive docs do not state it; the
  Squid 6.6 source does (`src/main.cc` at tag `SQUID_6_6`,
  https://github.com/squid-cache/squid/blob/SQUID_6_6/src/main.cc):
  - `shutdown_status` starts as `EXIT_SUCCESS` (line 154);
  - the SIGTERM/SIGINT handler `shut_down()` sets `EXIT_FAILURE` only for `SIGTTIN` (lines 756–763);
  - `SquidShutdown()` ends with `exit(shutdown_status)` (line 2142).
  So a Squid that shuts down on SIGTERM within `shutdown_lifetime` exits
  0. Run with `-N`, Squid is PID 1 in its container and installs that
  handler itself, so `docker stop` reaches it. Ubuntu's 6.6 package may
  carry patches; the opt-in docker test observes the real exit code.
- Why not tinyproxy 1.11.3 (https://github.com/tinyproxy/tinyproxy/releases/tag/1.11.3):
  its log format is not configurable, and its `Filter` does not bind a
  host to a port for plain HTTP, so it cannot give one line per
  connection with host, port and decision, which C1 needs. mitmproxy is
  not needed: no TLS inspection is wanted.

## D. Ollama

1. **OpenAI compatibility** (https://docs.ollama.com/api/openai-compatibility):
   `/v1/chat/completions` supports `tools` and `max_tokens`; **`tool_choice`
   is not supported**. "The client requires an API key value, but Ollama
   ignores it." The context size cannot be set on this endpoint.
2. **Context length** (https://docs.ollama.com/context-length): below
   24 GiB of VRAM the default context is 4k; `OLLAMA_CONTEXT_LENGTH` sets
   it; `ollama ps` shows a `CONTEXT` column, and `/api/ps` returns
   `context_length` (https://docs.ollama.com/api/ps).
3. **Sampler defaults.** v0.40.14's notes: OpenAI-compatible endpoints
   "no longer override a model's packaged sampler defaults". Before it,
   a client that sends no `temperature`/`top_p` gets 1.0 on that endpoint.
4. **Environment on macOS** (https://docs.ollama.com/faq): the app reads
   variables set with `launchctl setenv`; `ollama serve` takes them inline.
   Default bind: 127.0.0.1:11434. `OLLAMA_NO_CLOUD=1` turns off cloud
   models and web search.
5. **Updates.** "Ollama on macOS and Windows will automatically download
   updates" (FAQ). No documented switch turns this off; a maintainer in
   https://github.com/ollama/ollama/issues/4498 recommends the standalone
   binary, "which does not include the tray application, or auto-update
   logic".
6. **Recording endpoints:** `/api/version`, `/api/tags` (per model
   `digest`, `details`), `/api/ps` (`context_length`), `/api/show`
   (`parameters`, `model_info`). https://docs.ollama.com/api/tags ,
   https://docs.ollama.com/api-reference/show-model-details

## E. uv and mcp-server-fetch

- uv Docker guide (https://docs.astral.sh/uv/guides/integration/docker/):
  `COPY --from=ghcr.io/astral-sh/uv:<version> /uv /uvx /bin/`, with the
  digest pinned for reproducible builds; `UV_COMPILE_BYTECODE=1`;
  `UV_LINK_MODE=copy`; `uv sync --locked`; `--no-editable`;
  `UV_PYTHON_DOWNLOADS=0`.
- uv CLI (https://docs.astral.sh/uv/reference/cli/): `uv pip compile
  --generate-hashes --universal --python-version --exclude-newer <date>`;
  `uv pip install --require-hashes`.
- `mcp-server-fetch` 2026.10.19
  (https://github.com/modelcontextprotocol/servers/tree/main/src/fetch):
  options `--user-agent`, `--ignore-robots-txt`, `--proxy-url`,
  `--allow-private-ips`. It builds `httpx.AsyncClient(proxy=proxy_url)`
  without `trust_env`, so it honours `HTTPS_PROXY` and `HTTP_PROXY` from
  its environment. Node is optional: readabilipy uses Readability.js when
  `node` is on `PATH`, else its pure-Python extractor. Its private-IP
  guard skips a name it cannot resolve itself, because the proxy resolves
  it.

## Not documented: settled by the isolation probe or the smoke run

- Whether an internal-network container reaches the Mac through
  `host.docker.internal` or the LAN address (B.1, B.4): the isolation
  probe tests it before every run.
- Whether an internal-network container can still resolve external
  names on Desktop (B.3): the probe's DNS check.
- Whether `host.docker.internal` reaches Ollama bound to 127.0.0.1 (B.4):
  the probe's allowed-host check on the air-gapped arm.
- Binary safety of `docker run -i` stdout for frames over 100,000 bytes
  (B.5): the opt-in docker test.
- A single-file bind mount inside a tmpfs (B.5): the opt-in docker test.

## Retrieved

2026-11-04, from the URLs above. Registry digests were read from
`hub.docker.com/v2/repositories/...` and the `ghcr.io` manifest API; PyPI
values from `pypi.org/pypi/<name>/<version>/json`; release dates from the
GitHub releases API.
