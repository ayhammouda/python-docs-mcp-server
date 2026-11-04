FROM python:3.14.10-slim-trixie@sha256:930e1abba7c4e8f96ed93e8012c35d174d7732752731bbd5e8e757d7ec6f3966
COPY --from=ghcr.io/astral-sh/uv:0.13.4@sha256:9f130421e930bfbdc563c00740466603f8723158e90297d07957c2294640262b /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
# The build context is `git archive HEAD`: exactly the run commit, nothing untracked.
COPY . /app
RUN uv sync --locked --no-editable \
 && uv venv /opt/fetch --python /usr/local/bin/python3.14 \
 && uv pip install --python /opt/fetch/bin/python --require-hashes --no-deps \
      -r benchmarks/sandbox/fetch-requirements.txt \
 && ln -s /opt/fetch/bin/mcp-server-fetch /usr/local/bin/mcp-server-fetch \
 && mkdir -p /cache/mcp-python-docs \
 && command -v cat \
 && ! command -v node \
 && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin bench
ENV PATH=/app/.venv/bin:$PATH
USER 10001:10001
