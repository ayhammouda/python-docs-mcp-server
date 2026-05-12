FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    XDG_CACHE_HOME=/home/mcp/.cache

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN useradd --create-home --home-dir /home/mcp --shell /usr/sbin/nologin mcp
RUN pip install --no-cache-dir .

# Glama only needs the server to start and respond to MCP introspection.
# A symbol-only Python 3.13 index keeps the image fast and deterministic.
RUN python-docs-mcp-server build-index --versions 3.13 --skip-content \
    && chown -R mcp:mcp /home/mcp

USER mcp

CMD ["python-docs-mcp-server"]
