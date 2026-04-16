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
@click.option("--version", "show_version", is_flag=True, help="Show version and exit.")
@click.pass_context
def main(ctx: click.Context, show_version: bool) -> None:
    """MCP server for Python standard library documentation."""
    if show_version:
        from mcp_server_python_docs import __version__

        click.echo(f"mcp-server-python-docs {__version__}", err=True)
        raise SystemExit(0)
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
@click.option(
    "--skip-content",
    is_flag=True,
    help="Skip Sphinx JSON build, only ingest objects.inv symbols",
)
def build_index(versions: str, skip_content: bool) -> None:
    """Build the documentation index from objects.inv and Sphinx JSON."""
    import shutil
    import subprocess
    import tempfile
    import venv
    from pathlib import Path

    from mcp_server_python_docs.ingestion.inventory import ingest_inventory
    from mcp_server_python_docs.ingestion.publish import (
        generate_build_path,
        publish_index,
    )
    from mcp_server_python_docs.ingestion.sphinx_json import (
        ingest_sphinx_json_dir,
        populate_synonyms,
        rebuild_fts_indexes,
    )
    from mcp_server_python_docs.storage.db import (
        assert_fts5_available,
        bootstrap_schema,
        get_readwrite_connection,
    )

    # Version tag mapping: CPython git tag and Sphinx constraints (INGR-C-02)
    VERSION_CONFIG: dict[str, dict[str, str]] = {
        "3.12": {"tag": "v3.12.13", "sphinx_pin": "sphinx~=8.2.0"},
        "3.13": {"tag": "v3.13.12", "sphinx_pin": "sphinx<9.0.0"},
    }

    version_list = [v.strip() for v in versions.split(",") if v.strip()]
    if not version_list:
        logger.error("No valid versions specified. Example: --versions 3.13")
        raise SystemExit(1)

    # Determine default version: highest version number (MVER-02)
    sorted_versions = sorted(version_list, key=lambda v: [int(x) for x in v.split(".")])
    default_version = sorted_versions[-1]

    # Build into a timestamped artifact, not directly to index.db (PUBL-01)
    build_db_path = generate_build_path()
    logger.info("Building index at %s", build_db_path)

    conn = get_readwrite_connection(build_db_path)
    try:
        bootstrap_schema(conn)
        assert_fts5_available(conn)

        any_version_succeeded = False

        for version in version_list:
            try:
                # === Objects.inv ingestion (existing — INGR-I-*) ===
                logger.info("Ingesting objects.inv for Python %s...", version)
                count = ingest_inventory(conn, version, is_default=(version == default_version))
                logger.info("Ingested %d symbols for Python %s", count, version)

                if skip_content:
                    any_version_succeeded = True
                    continue

                # === Content ingestion (INGR-C-01 through INGR-C-03) ===
                config = VERSION_CONFIG.get(version)
                if not config:
                    logger.warning(
                        "No CPython build config for %s, skipping content ingestion",
                        version,
                    )
                    any_version_succeeded = True
                    continue

                # Clone CPython source at pinned tag (INGR-C-01)
                clone_dir = tempfile.mkdtemp(prefix=f"cpython-{version}-")
                try:
                    logger.info(
                        "Cloning CPython %s into %s...", config["tag"], clone_dir
                    )
                    subprocess.run(
                        [
                            "git", "clone", "--depth", "1",
                            "--branch", config["tag"],
                            "https://github.com/python/cpython.git",
                            clone_dir,
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                    )

                    # Create dedicated Sphinx venv (INGR-C-02)
                    venv_dir = os.path.join(clone_dir, "_sphinx_venv")
                    logger.info("Creating Sphinx venv at %s...", venv_dir)
                    venv.create(venv_dir, with_pip=True)
                    # Use Scripts/ on Windows, bin/ elsewhere
                    scripts_dir = os.path.join(
                        venv_dir,
                        "Scripts" if sys.platform == "win32" else "bin",
                    )
                    pip_path = os.path.join(scripts_dir, "pip")

                    # Install Sphinx with the version pin for this CPython branch
                    subprocess.run(
                        [pip_path, "install", config["sphinx_pin"]],
                        check=True,
                        capture_output=True,
                        text=True,
                    )

                    # Install remaining Doc/requirements.txt deps
                    doc_reqs = os.path.join(clone_dir, "Doc", "requirements.txt")
                    if os.path.exists(doc_reqs):
                        subprocess.run(
                            [pip_path, "install", "-r", doc_reqs],
                            check=True,
                            capture_output=True,
                            text=True,
                        )

                    # Run sphinx-build -b json directly (INGR-C-03)
                    # Never use 'make json' — that target does not exist
                    sphinx_build = os.path.join(scripts_dir, "sphinx-build")
                    doc_dir = os.path.join(clone_dir, "Doc")
                    json_out = os.path.join(doc_dir, "build", "json")

                    logger.info(
                        "Running sphinx-build -b json for Python %s "
                        "(this may take 3-8 minutes)...",
                        version,
                    )
                    result = subprocess.run(
                        [
                            sphinx_build, "-b", "json",
                            "-j", "auto",
                            doc_dir, json_out,
                        ],
                        capture_output=True,
                        text=True,
                        cwd=doc_dir,
                    )
                    if result.returncode != 0:
                        logger.error(
                            "sphinx-build failed for %s:\n%s",
                            version,
                            result.stderr[-2000:] if result.stderr else "(no output)",
                        )
                        any_version_succeeded = True  # symbols still ingested
                        continue

                    logger.info("sphinx-build complete for Python %s", version)

                    # Get doc_set_id for this version
                    row = conn.execute(
                        "SELECT id FROM doc_sets "
                        "WHERE source='python-docs' AND version=? AND language='en'",
                        (version,),
                    ).fetchone()
                    if row is None:
                        logger.error("No doc_set found for version %s", version)
                        continue
                    doc_set_id = row[0]

                    # Ingest fjson files (INGR-C-04 through INGR-C-07)
                    success, failures = ingest_sphinx_json_dir(
                        conn, Path(json_out), doc_set_id
                    )
                    logger.info(
                        "Ingested %d documents (%d failures) for Python %s",
                        success, failures, version,
                    )
                    any_version_succeeded = True

                except subprocess.CalledProcessError as e:
                    logger.error(
                        "Subprocess failed for %s: %s\n%s",
                        version, e, e.stderr[:2000] if e.stderr else "",
                    )
                    any_version_succeeded = True  # symbols still ingested
                finally:
                    # Cleanup clone directory
                    shutil.rmtree(clone_dir, ignore_errors=True)
                    logger.info("Cleaned up %s", clone_dir)

            except Exception as e:
                logger.error("Error processing version %s: %s", version, e)
                continue

        if not any_version_succeeded:
            logger.error("No versions were successfully ingested")
            # Clean up the failed build artifact
            if build_db_path.exists():
                build_db_path.unlink()
            raise SystemExit(1)

        # Populate synonyms from synonyms.yaml (INGR-C-09)
        synonym_count = populate_synonyms(conn)
        logger.info("Populated %d synonyms", synonym_count)

        # Rebuild FTS indexes (INGR-C-08)
        rebuild_fts_indexes(conn)
    finally:
        conn.close()

    # Publish: smoke test + atomic swap (PUBL-01 through PUBL-05)
    versions_str = ",".join(version_list)
    success = publish_index(build_db_path, versions_str)
    if not success:
        logger.error("Publishing failed — smoke tests did not pass")
        raise SystemExit(1)


@main.command("validate-corpus")
@click.option(
    "--db-path",
    default=None,
    type=click.Path(),
    help="Path to index database. Defaults to the standard cache location.",
)
def validate_corpus(db_path: str | None) -> None:
    """Validate the current index by running smoke tests.

    Runs the same smoke-test suite used during build-index publishing.
    Exits 0 if all checks pass, non-zero if any fail.
    """
    from pathlib import Path

    from mcp_server_python_docs.ingestion.publish import run_smoke_tests
    from mcp_server_python_docs.storage.db import get_index_path

    if db_path is not None:
        target = Path(db_path)
    else:
        target = get_index_path()

    if not target.exists():
        logger.error("Index not found at %s", target)
        logger.error("Run: mcp-server-python-docs build-index --versions 3.13")
        raise SystemExit(1)

    logger.info("Validating corpus at %s", target)

    passed, messages = run_smoke_tests(target)

    for msg in messages:
        if msg.startswith("OK:"):
            logger.info("  %s", msg)
        elif msg.startswith("WARN:"):
            logger.warning("  %s", msg)
        else:
            logger.error("  %s", msg)

    if passed:
        logger.info("Corpus validation PASSED")
        # Return normally -- Click exits with code 0 by default
    else:
        logger.error("Corpus validation FAILED")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
