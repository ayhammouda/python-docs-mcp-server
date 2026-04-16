# Plan 01-04 Summary

## What was built
SQLite connection factory (db.py) with RO/RW split, WAL/NORMAL/FK PRAGMAs, FTS5 availability check, platformdirs cache resolution, and minimal schema bootstrap. sphobjinv-based symbol ingestion (inventory.py) that downloads objects.inv, expands URI shorthand, handles dispname fallback and duplicates via priority ordering, and populates symbols_fts.

## Key files created
- `src/mcp_server_python_docs/storage/db.py` -- get_readonly_connection, get_readwrite_connection, assert_fts5_available, bootstrap_schema
- `src/mcp_server_python_docs/ingestion/inventory.py` -- ingest_inventory with sphobjinv download, URI expansion, dedup, FTS rebuild

## Deviations
- FTS5 external-content table requires `INSERT INTO symbols_fts(symbols_fts) VALUES('rebuild')` instead of DELETE + INSERT. The rebuild command is the correct way to repopulate external-content FTS5 tables.

## Self-Check: PASSED
- RO connection uses mode=ro URI parameter
- Both handles set WAL, NORMAL, FK PRAGMAs
- FTS5 check raises platform-aware FTS5UnavailableError
- Cache path via platformdirs (no hardcoded ~/.cache)
- ~9.9K deduplicated Python domain symbols ingested from 3.13 objects.inv
- No unexpanded $ in URIs
- FTS row count matches symbol count
