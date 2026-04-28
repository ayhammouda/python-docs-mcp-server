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
import atexit
import os
import signal
import sys

# Save the real stdout fd for the MCP framer, then redirect fd 1 to stderr.
# After this, any print() or write to fd 1 goes to stderr, not the MCP pipe.
_saved_stdout_fd: int | None = os.dup(1)


def _close_saved_stdout_fd() -> None:
    """Close the saved stdout fd when the CLI exits without serving."""
    global _saved_stdout_fd
    if _saved_stdout_fd is None:
        return
    try:
        os.close(_saved_stdout_fd)
    except OSError:
        pass
    finally:
        _saved_stdout_fd = None


def _consume_saved_stdout_fd() -> int:
    """Hand off the saved stdout fd to the stdio MCP transport."""
    global _saved_stdout_fd
    if _saved_stdout_fd is None:
        raise RuntimeError("Saved stdout fd is not available")
    fd = _saved_stdout_fd
    _saved_stdout_fd = None
    return fd


atexit.register(_close_saved_stdout_fd)
os.dup2(2, 1)
sys.stdout = sys.stderr

# === SIGPIPE HANDLER (HYGN-03) ===
# Ignore SIGPIPE so client disconnect doesn't crash with BrokenPipeError.
# Windows does not expose SIGPIPE.
sigpipe = getattr(signal, "SIGPIPE", None)
if sigpipe is not None:
    signal.signal(sigpipe, signal.SIG_IGN)

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
        ctx.exit(0)
    if ctx.invoked_subcommand is None:
        ctx.invoke(serve)


@main.command()
def serve() -> None:
    """Start the MCP server (default command)."""
    from mcp_server_python_docs.server import create_server

    mcp_server = create_server()
    saved_stdout_fd = _consume_saved_stdout_fd()

    # Restore the real stdout fd for MCP protocol framing.
    # By this point all imports are done — no third-party code will
    # print to stdout during MCP communication.
    os.dup2(saved_stdout_fd, 1)
    os.close(saved_stdout_fd)

    try:
        mcp_server.run(transport="stdio")
    except BrokenPipeError:
        pass  # Client disconnected (HYGN-03)


@main.command("build-index")
@click.option(
    "--versions",
    required=True,
    help="Comma-separated Python versions (e.g., 3.10,3.11,3.12,3.13,3.14)",
)
@click.option(
    "--skip-content",
    is_flag=True,
    help="Skip Sphinx JSON build and publish a symbol-only index (search_docs only).",
)
def build_index(versions: str, skip_content: bool) -> None:
    """Build the documentation index from objects.inv and Sphinx JSON."""
    import shutil
    import subprocess
    import tempfile
    import venv
    from pathlib import Path

    from mcp_server_python_docs.ingestion.cpython_versions import (
        CPYTHON_DOCS_BUILD_CONFIG,
    )
    from mcp_server_python_docs.ingestion.inventory import ingest_inventory
    from mcp_server_python_docs.ingestion.publish import (
        _version_sort_key,
        generate_build_path,
        parse_expected_versions,
        publish_index,
    )
    from mcp_server_python_docs.ingestion.sphinx_json import (
        build_sphinx_bootstrap_requirements,
        build_sphinx_json_command,
        ingest_sphinx_json_dir,
        make_sphinx_json_env,
        populate_synonyms,
        rebuild_fts_indexes,
        write_json_build_requirements,
        write_sphinx_json_sitecustomize,
    )
    from mcp_server_python_docs.storage.db import (
        assert_fts5_available,
        bootstrap_schema,
        get_readwrite_connection,
    )

    version_list = parse_expected_versions(versions)
    if not version_list:
        logger.error(
            "No valid versions specified. Example: --versions 3.10,3.11,3.12,3.13,3.14"
        )
        raise SystemExit(1)

    # Validate version format before sorting (CR-03, WR-04)
    for v in version_list:
        parts = v.split(".")
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            logger.error(
                "Invalid version format %r. Expected 'X.Y' (e.g., 3.13)", v
            )
            raise SystemExit(1)

    # Determine default version: highest version number (MVER-02)
    default_version = max(version_list, key=_version_sort_key)

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
                config = CPYTHON_DOCS_BUILD_CONFIG.get(version)
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

                    # Install Sphinx with the version pin for this CPython branch.
                    # Older Sphinx releases still import pkg_resources, which
                    # modern venvs do not always seed by default.
                    subprocess.run(
                        [
                            pip_path,
                            "install",
                            *build_sphinx_bootstrap_requirements(
                                config["sphinx_pin"]
                            ),
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                    )

                    # Install remaining Doc/requirements.txt deps
                    doc_reqs = Path(clone_dir) / "Doc" / "requirements.txt"
                    if doc_reqs.exists():
                        json_doc_reqs = doc_reqs.with_name(
                            "_json-build-requirements.txt"
                        )
                        omitted_reqs = write_json_build_requirements(
                            doc_reqs, json_doc_reqs
                        )
                        if omitted_reqs:
                            logger.info(
                                "Omitted HTML-only Sphinx extensions for JSON build: %s",
                                ", ".join(omitted_reqs),
                            )
                        subprocess.run(
                            [pip_path, "install", "-r", str(json_doc_reqs)],
                            check=True,
                            capture_output=True,
                            text=True,
                        )

                    # Run sphinx-build -b json directly (INGR-C-03)
                    # Never use 'make json' — that target does not exist
                    sphinx_build = os.path.join(scripts_dir, "sphinx-build")
                    doc_dir = os.path.join(clone_dir, "Doc")
                    json_out = os.path.join(doc_dir, "build", "json")
                    sphinx_compat_dir = Path(clone_dir) / "_sphinx_json_compat"
                    write_sphinx_json_sitecustomize(sphinx_compat_dir)
                    sphinx_env = make_sphinx_json_env(sphinx_compat_dir)

                    logger.info(
                        "Running sphinx-build -b json for Python %s "
                        "(this may take 3-8 minutes)...",
                        version,
                    )
                    result = subprocess.run(
                        build_sphinx_json_command(sphinx_build, doc_dir, json_out),
                        capture_output=True,
                        text=True,
                        cwd=doc_dir,
                        env=sphinx_env,
                    )
                    if result.returncode != 0:
                        logger.error(
                            "sphinx-build failed for %s:\n%s",
                            version,
                            result.stderr[-2000:] if result.stderr else "(no output)",
                        )
                        logger.warning(
                            "Version %s has SYMBOLS ONLY (sphinx-build failed). "
                            "search_docs will work but get_docs will fail until content "
                            "ingestion succeeds.",
                            version,
                        )
                        any_version_succeeded = True
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
    success = publish_index(build_db_path, versions_str, require_content=not skip_content)
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

    from mcp_server_python_docs.ingestion.publish import (
        parse_expected_versions,
        run_smoke_tests,
    )
    from mcp_server_python_docs.storage.db import get_index_path, get_readonly_connection

    if db_path is not None:
        target = Path(db_path)
    else:
        target = get_index_path()

    if not target.exists():
        logger.error("Index not found at %s", target)
        logger.error(
            "Run: mcp-server-python-docs build-index --versions "
            "3.10,3.11,3.12,3.13,3.14"
        )
        raise SystemExit(1)

    logger.info("Validating corpus at %s", target)

    # Auto-detect symbol-only builds from the last published ingestion run
    require_content = True
    expected_versions: list[str] | None = None
    try:
        ro_conn = get_readonly_connection(target)
        row = ro_conn.execute(
            "SELECT version, notes FROM ingestion_runs "
            "WHERE status = 'published' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        ro_conn.close()
        if row and row[0]:
            expected_versions = parse_expected_versions(row[0])
        if row and row[1] and "build_mode=symbol_only" in row[1]:
            require_content = False
            logger.info("Detected symbol-only build — skipping content checks")
    except Exception as e:
        # If we can't read the metadata, default to full validation
        logger.debug("Could not read ingestion_runs metadata: %s", e)

    passed, messages = run_smoke_tests(
        target,
        require_content=require_content,
        expected_versions=expected_versions,
    )

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


@main.command()
def doctor() -> None:
    """Check environment health and report issues (CLI-02)."""
    import shutil
    import sqlite3
    from pathlib import Path

    from mcp_server_python_docs.diagnostics import check_build_venv_support
    from mcp_server_python_docs.storage.db import get_cache_dir, get_index_path

    results: list[tuple[str, bool, str]] = []  # (probe_name, passed, detail)

    # 1. Python version
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    py_ok = sys.version_info >= (3, 12)
    results.append((
        "Python version",
        py_ok,
        f"{py_ver}" + ("" if py_ok else " (requires >= 3.12)"),
    ))

    # 2. SQLite FTS5 availability
    fts5_ok = False
    fts5_detail = ""
    try:
        mem_conn = sqlite3.connect(":memory:")
        mem_conn.execute("CREATE VIRTUAL TABLE _fts5_check USING fts5(x)")
        mem_conn.execute("DROP TABLE _fts5_check")
        mem_conn.close()
        fts5_ok = True
        fts5_detail = f"SQLite {sqlite3.sqlite_version}"
    except sqlite3.OperationalError:
        import platform as plat

        if plat.system() == "Linux" and plat.machine() == "x86_64":
            fts5_detail = (
                "FTS5 unavailable -- pip install 'mcp-server-python-docs[pysqlite3]'"
            )
        else:
            fts5_detail = (
                "FTS5 unavailable -- install Python from python.org or uv python install"
            )
    results.append(("SQLite FTS5", fts5_ok, fts5_detail))

    # 3. Build-index Sphinx venv support
    build_venv_result = check_build_venv_support()
    results.append((
        "Build venv support",
        build_venv_result.passed,
        build_venv_result.detail,
    ))

    # 4. Cache directory
    cache_dir = get_cache_dir()
    cache_exists = cache_dir.exists()
    cache_writable = False
    if cache_exists:
        try:
            test_file = cache_dir / ".doctor-write-test"
            test_file.touch()
            test_file.unlink()
            cache_writable = True
        except OSError:
            pass
    cache_ok = True  # Not existing yet is OK (will be created on first build)
    cache_detail = str(cache_dir)
    if not cache_exists:
        cache_detail += " (does not exist -- will be created on first build-index)"
    elif not cache_writable:
        cache_detail += " (not writable)"
        cache_ok = False
    results.append(("Cache directory", cache_ok, cache_detail))

    # 5. Index database presence
    index_path = get_index_path()
    index_exists = index_path.exists()
    index_detail = str(index_path)
    if not index_exists:
        index_detail += (
            " (not found -- run: mcp-server-python-docs build-index --versions "
            "3.10,3.11,3.12,3.13,3.14)"
        )
    else:
        size_mb = index_path.stat().st_size / (1024 * 1024)
        index_detail += f" ({size_mb:.1f} MB)"
    results.append(("Index database", index_exists, index_detail))

    # 6. Free disk space
    check_path = cache_dir if cache_exists else cache_dir.parent
    # Ensure path exists for disk_usage
    if not check_path.exists():
        check_path = Path.home()
    disk_usage = shutil.disk_usage(check_path)
    free_gb = disk_usage.free / (1024**3)
    disk_ok = free_gb >= 1.0
    disk_detail = f"{free_gb:.1f} GB free"
    if not disk_ok:
        disk_detail += " (recommend >= 1 GB for index builds)"
    results.append(("Disk space", disk_ok, disk_detail))

    # Print report (all to stderr for stdio hygiene)
    click.echo("\nmcp-server-python-docs doctor\n", err=True)
    all_passed = True
    for probe_name, passed, detail in results:
        status = "PASS" if passed else "FAIL"
        marker = "+" if passed else "x"
        click.echo(f"  {marker} {status}: {probe_name} -- {detail}", err=True)
        if not passed:
            all_passed = False

    click.echo("", err=True)
    if all_passed:
        click.echo("All checks passed.", err=True)
    else:
        click.echo("Some checks failed. See details above.", err=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
