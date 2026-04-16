"""MCP server for Python standard library documentation.

Entry point with stdio hygiene. The order of operations is load-bearing:
1. Save real stdout fd, redirect fd 1 to stderr (HYGN-01, B3 blocker)
2. Install SIGPIPE handler (HYGN-03)
3. Configure logging to stderr (HYGN-02)
4. Import everything else
"""
# === STDIO HYGIENE (HYGN-01, B3 blocker) ===
# These MUST be the first imports and operations, before anything
# that might write to stdout.
import os
import signal
import sys

# Save the real stdout fd for the MCP framer, then redirect fd 1 to stderr.
# After this, any print() or write to fd 1 goes to stderr, not the MCP pipe.
_real_stdout_fd = os.dup(1)
os.dup2(2, 1)
sys.stdout = sys.stderr

# === SIGPIPE HANDLER (HYGN-03) ===
# Ignore SIGPIPE so client disconnect doesn't crash with BrokenPipeError.
# Windows doesn't have SIGPIPE.
if hasattr(signal, "SIGPIPE"):
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)

# === LOGGING TO STDERR (HYGN-02) ===
import logging  # noqa: E402

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("mcp_server_python_docs")

# === Now safe to import everything else ===
import click  # noqa: E402


@click.group(invoke_without_command=True)
@click.pass_context
def main(ctx: click.Context) -> None:
    """MCP server for Python standard library documentation."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(serve)


@main.command()
def serve() -> None:
    """Start the MCP server (default command)."""
    from mcp_server_python_docs.server import create_server

    mcp_server = create_server()

    # Restore the real stdout fd for MCP protocol framing.
    # By this point all imports are done — no third-party code will
    # print to stdout during MCP communication.
    os.dup2(_real_stdout_fd, 1)
    os.close(_real_stdout_fd)

    try:
        mcp_server.run(transport="stdio")
    except BrokenPipeError:
        pass  # Client disconnected (HYGN-03)


@main.command("build-index")
@click.option(
    "--versions",
    required=True,
    help="Comma-separated Python versions (e.g., 3.12,3.13)",
)
def build_index(versions: str) -> None:
    """Build the documentation index from objects.inv."""
    import platformdirs

    from mcp_server_python_docs.ingestion.inventory import ingest_inventory
    from mcp_server_python_docs.storage.db import (
        assert_fts5_available,
        get_readwrite_connection,
    )

    version_list = [v.strip() for v in versions.split(",") if v.strip()]
    if not version_list:
        logger.error("No valid versions specified. Example: --versions 3.13")
        raise SystemExit(1)

    cache_dir = platformdirs.user_cache_dir("mcp-python-docs")
    os.makedirs(cache_dir, exist_ok=True)
    index_path = os.path.join(cache_dir, "index.db")

    conn = get_readwrite_connection(index_path)
    assert_fts5_available(conn)

    for version in version_list:
        logger.info(f"Ingesting objects.inv for Python {version}...")
        count = ingest_inventory(conn, version)
        logger.info(f"Ingested {count} symbols for Python {version}")

    conn.close()
    logger.info(f"Index built at {index_path}")


@main.command("validate-corpus")
def validate_corpus() -> None:
    """Validate the current index (stub for Phase 5)."""
    logger.info("validate-corpus: not yet implemented (Phase 5)")


if __name__ == "__main__":
    main()
