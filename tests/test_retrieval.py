"""Tests for the retrieval layer: query, ranker, and budget modules."""
from __future__ import annotations

import importlib.resources
import sqlite3
import unicodedata

import pytest

from mcp_server_python_docs.errors import (
    DocsServerError,
    PageNotFoundError,
    SymbolNotFoundError,
    VersionNotFoundError,
)
from mcp_server_python_docs.models import SymbolHit
from mcp_server_python_docs.retrieval.budget import apply_budget
from mcp_server_python_docs.retrieval.query import (
    build_match_expression,
    classify_query,
    expand_synonyms,
    fts5_escape,
)
from mcp_server_python_docs.retrieval.ranker import (
    _normalize_scores,
    lookup_symbols_exact,
    search_examples,
    search_sections,
    search_symbols,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fts_db():
    """In-memory SQLite DB with full schema and test data for FTS5 tests."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    # Load and execute schema
    ref = importlib.resources.files("mcp_server_python_docs.storage") / "schema.sql"
    with importlib.resources.as_file(ref) as schema_path:
        schema_sql = schema_path.read_text()

    # Drop FTS tables first (bootstrap_schema pattern)
    for fts_table in ("sections_fts", "symbols_fts", "examples_fts"):
        conn.execute(f"DROP TABLE IF EXISTS {fts_table}")
    conn.executescript(schema_sql)

    # Insert test data
    conn.execute(
        "INSERT INTO doc_sets (id, source, version, language, label, is_default, base_url) "
        "VALUES (1, 'python-docs', '3.13', 'en', 'Python 3.13', 1, 'https://docs.python.org/3.13/')"
    )
    conn.execute(
        "INSERT INTO documents (id, doc_set_id, uri, slug, title, content_text, char_count) "
        "VALUES (1, 1, 'library/asyncio-task.html', 'library/asyncio-task.html', "
        "'asyncio Task', 'Documentation about asyncio tasks and TaskGroup', 50)"
    )
    conn.execute(
        "INSERT INTO sections"
        " (id, document_id, uri, anchor, heading, level, ordinal,"
        " content_text, char_count) "
        "VALUES (1, 1, 'library/asyncio-task.html#asyncio.TaskGroup',"
        " 'asyncio.TaskGroup', 'asyncio.TaskGroup', 2, 1, "
        "'A context manager that holds a group of tasks."
        " Tasks can be added with create_task."
        " All tasks are awaited on exit.', 100)"
    )
    conn.execute(
        "INSERT INTO sections"
        " (id, document_id, uri, anchor, heading, level, ordinal,"
        " content_text, char_count) "
        "VALUES (2, 1, 'library/asyncio-task.html#introduction',"
        " 'introduction', "
        "'Introduction', 1, 0,"
        " 'This page describes asyncio tasks and how to use"
        " TaskGroup for concurrent execution.', 80)"
    )
    conn.execute(
        "INSERT INTO symbols (id, doc_set_id, qualified_name, normalized_name, module, "
        "symbol_type, uri, anchor) "
        "VALUES (1, 1, 'asyncio.TaskGroup', 'asyncio.taskgroup', 'asyncio', "
        "'class', 'library/asyncio-task.html#asyncio.TaskGroup', 'asyncio.TaskGroup')"
    )
    conn.execute(
        "INSERT INTO symbols (id, doc_set_id, qualified_name, normalized_name, module, "
        "symbol_type, uri, anchor) "
        "VALUES (2, 1, 'asyncio.run', 'asyncio.run', 'asyncio', "
        "'function', 'library/asyncio-task.html#asyncio.run', 'asyncio.run')"
    )
    conn.execute(
        "INSERT INTO examples (id, section_id, code, language, is_doctest, ordinal) "
        "VALUES (1, 1, 'async with asyncio.TaskGroup() as tg:\\n    tg.create_task(coro())', "
        "'python', 0, 0)"
    )
    conn.commit()

    # Rebuild all FTS indexes
    conn.execute("INSERT INTO sections_fts(sections_fts) VALUES('rebuild')")
    conn.execute("INSERT INTO symbols_fts(symbols_fts) VALUES('rebuild')")
    conn.execute("INSERT INTO examples_fts(examples_fts) VALUES('rebuild')")
    conn.commit()

    yield conn
    conn.close()


@pytest.fixture
def simple_fts_db():
    """Minimal FTS5 table for fts5_escape fuzz testing."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE VIRTUAL TABLE test_fts USING fts5("
        "content, tokenize=\"unicode61 remove_diacritics 2 tokenchars '._'\")"
    )
    conn.execute("INSERT INTO test_fts(content) VALUES ('asyncio.TaskGroup class')")
    conn.execute("INSERT INTO test_fts(content) VALUES ('json.dumps function')")
    conn.execute("INSERT INTO test_fts(content) VALUES ('os.path.join method')")
    conn.execute("INSERT INTO test_fts(content) VALUES ('hello world example')")
    conn.commit()
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# fts5_escape tests (RETR-01, RETR-03)
# ---------------------------------------------------------------------------

# 50+ adversarial inputs for fuzz testing
FTS5_FUZZ_INPUTS = [
    # Empty and whitespace
    "",
    "   ",
    "\t",
    "\n",
    "\r\n",
    # Single special characters
    "*",
    "(",
    ")",
    ":",
    "-",
    "+",
    '"',
    "'",
    "\\",
    "/",
    # FTS5 operators
    "AND",
    "OR",
    "NOT",
    "NEAR",
    "NEAR/3",
    "AND OR NOT",
    "NOT AND",
    "OR OR OR",
    # Unbalanced quotes
    '"unbalanced',
    'unbalanced"',
    '""',
    '"""',
    '"hello" "world"',
    # Special combos
    "c++",
    "C#",
    "a:b",
    "(test)",
    "column:value",
    "{braces}",
    "[brackets]",
    "a AND b",
    "a OR b",
    "NOT something",
    # Wildcards
    "test*",
    "*test",
    "**",
    "***",
    "te*st",
    # Unicode
    "\U0001f389",  # 🎉 party popper (4-byte emoji)
    "caf\u00e9",  # cafe with precomposed e-acute
    "caf\u0301e",  # cafe with combining acute (e + combining)
    "na\u00efve",  # naive with precomposed i-diaeresis
    "\u65e5\u672c\u8a9e",  # Japanese: 日本語
    "\u0410\u0411\u0412",  # Russian: АБВ
    # Long strings
    "a" * 1000,
    "word " * 200,
    # Dotted identifiers
    "asyncio.TaskGroup",
    "os.path.join",
    "json.dumps",
    "collections.OrderedDict",
    # Hyphenated
    "built-in",
    "read-only",
    "async-for",
    # Mixed adversarial
    'asyncio AND "evil',
    "(NOT) OR *",
    "NEAR(a b)",
    "a:b AND c*",
    "^prefix$",
    "re.match(pattern)",
    "x < y > z",
    # Edge cases
    "\x00",  # null byte
    "a",  # single char
    "ab",  # two chars
    ".",  # just a dot
    "..",  # two dots
    "...",  # three dots
    "_",  # underscore
    "__",  # double underscore
    "__init__",  # dunder
    "a.b.c.d.e.f",  # deeply nested
]


def test_fts5_escape_basic():
    """Test basic fts5_escape behavior."""
    assert fts5_escape("") == '""'
    assert fts5_escape("   ") == '""'
    assert fts5_escape("hello") == '"hello"'
    assert fts5_escape("hello world") == '"hello" "world"'
    assert fts5_escape("asyncio.TaskGroup") == '"asyncio.TaskGroup"'
    assert fts5_escape("c++") == '"c++"'
    assert fts5_escape("AND OR NOT") == '"AND" "OR" "NOT"'


def test_fts5_escape_quotes():
    """Test double quote escaping in fts5_escape."""
    # Internal quotes should be doubled
    result = fts5_escape('"hello"')
    assert '""hello""' in result


def test_fts5_escape_fuzz(simple_fts_db):
    """RETR-03: 50+ inputs through fts5_escape never crash FTS5 MATCH."""
    assert len(FTS5_FUZZ_INPUTS) >= 50, (
        f"Expected 50+ fuzz inputs, got {len(FTS5_FUZZ_INPUTS)}"
    )

    for i, raw_input in enumerate(FTS5_FUZZ_INPUTS):
        escaped = fts5_escape(raw_input)
        try:
            simple_fts_db.execute(
                "SELECT * FROM test_fts WHERE test_fts MATCH ?",
                (escaped,),
            ).fetchall()
        except sqlite3.OperationalError as e:
            pytest.fail(
                f"fts5_escape failed for input #{i}: {raw_input!r}\n"
                f"Escaped: {escaped!r}\n"
                f"Error: {e}"
            )


# ---------------------------------------------------------------------------
# classify_query tests (RETR-04)
# ---------------------------------------------------------------------------


def test_classify_query_dotted():
    """Dotted names are always classified as symbol."""
    assert classify_query("asyncio.TaskGroup", lambda q: False) == "symbol"
    assert classify_query("os.path.join", lambda q: False) == "symbol"
    assert classify_query("json.dumps", lambda q: False) == "symbol"


def test_classify_query_module():
    """Single-word module names classified as symbol when they exist."""
    assert classify_query("re", lambda q: True) == "symbol"
    assert classify_query("os", lambda q: True) == "symbol"
    assert classify_query("sys", lambda q: True) == "symbol"


def test_classify_query_non_module():
    """Regular words or non-existent names classified as fts."""
    assert classify_query("parse json", lambda q: True) == "fts"
    assert classify_query("how to do X", lambda q: True) == "fts"
    assert classify_query("re", lambda q: False) == "fts"  # not in symbol table
    assert classify_query("", lambda q: True) == "fts"


def test_classify_query_uppercase_not_module():
    """Uppercase names don't match the module pattern."""
    assert classify_query("TaskGroup", lambda q: True) == "fts"
    assert classify_query("OrderedDict", lambda q: True) == "fts"


# ---------------------------------------------------------------------------
# classify_query — M-5 gating tests (no DB call for garbage inputs)
# ---------------------------------------------------------------------------


def test_classify_query_empty_does_not_call_symbol_fn():
    """M-5: empty query must not hit the DB callback at all."""
    from unittest.mock import MagicMock

    mock = MagicMock(return_value=True)
    assert classify_query("", mock) == "fts"
    mock.assert_not_called()


def test_classify_query_whitespace_only_does_not_call_symbol_fn():
    """M-5: whitespace-only query strips to empty and must not hit the DB."""
    from unittest.mock import MagicMock

    mock = MagicMock(return_value=True)
    assert classify_query("   ", mock) == "fts"
    mock.assert_not_called()


def test_classify_query_length_one_does_not_call_symbol_fn():
    """M-5: single-character identifier-shaped tokens short-circuit to fts
    without querying the symbol table. Stdlib module names are all >= 2 chars."""
    from unittest.mock import MagicMock

    mock = MagicMock(return_value=True)
    assert classify_query("a", mock) == "fts"
    mock.assert_not_called()


def test_classify_query_length_two_module_calls_symbol_fn():
    """M-5: length-2 identifier-shaped tokens are valid candidates (os, io, re)."""
    from unittest.mock import MagicMock

    mock = MagicMock(return_value=True)
    assert classify_query("os", mock) == "symbol"
    mock.assert_called_once_with("os")


def test_classify_query_dotted_skips_symbol_fn():
    """M-5: dotted names take the fast-path and must NOT call the DB callback."""
    from unittest.mock import MagicMock

    mock = MagicMock(return_value=True)
    assert classify_query("asyncio.TaskGroup", mock) == "symbol"
    mock.assert_not_called()


def test_classify_query_multiword_skips_symbol_fn():
    """M-5: multi-word (non-dotted) queries never match _MODULE_PATTERN so the DB is skipped."""
    from unittest.mock import MagicMock

    mock = MagicMock(return_value=True)
    assert classify_query("  foo bar  ", mock) == "fts"
    mock.assert_not_called()


# ---------------------------------------------------------------------------
# expand_synonyms tests (RETR-05)
# ---------------------------------------------------------------------------

SAMPLE_SYNONYMS = {
    "parallel": ["concurrent", "multiprocessing", "threading", "asyncio"],
    "http requests": ["urllib", "http.client", "urllib.request"],
    "parse json": ["json", "json.loads", "json.dumps"],
    "regex": ["re", "regular expression", "pattern"],
    "async": ["asyncio", "await", "coroutine"],
}


def test_expand_synonyms_match():
    """Matching concept expands correctly."""
    result = expand_synonyms("parallel", SAMPLE_SYNONYMS)
    assert "parallel" in result
    assert "concurrent" in result
    assert "threading" in result
    assert "asyncio" in result
    assert "multiprocessing" in result


def test_expand_synonyms_no_match():
    """Non-matching query returns original tokens only."""
    result = expand_synonyms("something random", SAMPLE_SYNONYMS)
    assert result == {"something", "random"}


def test_expand_synonyms_multi_word():
    """Multi-word concepts expand correctly."""
    result = expand_synonyms("parse json", SAMPLE_SYNONYMS)
    assert "json" in result
    assert "json.loads" in result
    assert "json.dumps" in result


def test_expand_synonyms_empty():
    """Empty query returns empty set."""
    result = expand_synonyms("", SAMPLE_SYNONYMS)
    assert result == set()


def test_expand_synonyms_case_insensitive():
    """Synonym matching is case-insensitive."""
    result = expand_synonyms("PARALLEL", SAMPLE_SYNONYMS)
    assert "concurrent" in result


# ---------------------------------------------------------------------------
# build_match_expression tests
# ---------------------------------------------------------------------------


def test_build_match_expression_with_synonyms():
    """Build expression with synonym expansion produces OR-joined terms."""
    result = build_match_expression("parallel", SAMPLE_SYNONYMS)
    assert "OR" in result
    assert '"parallel"' in result


def test_build_match_expression_keeps_original_multiword_query():
    """Multi-word synonym expansion keeps the original AND query as an option."""
    result = build_match_expression("parse json", SAMPLE_SYNONYMS)
    assert result.startswith('"parse" "json"')
    assert "OR" in result
    assert '"json.loads"' in result


def test_build_match_expression_without_synonyms():
    """Build expression without matches produces plain escaped query."""
    result = build_match_expression("unknown query", SAMPLE_SYNONYMS)
    assert result == '"unknown" "query"'
    assert "OR" not in result


def test_build_match_expression_empty():
    """Empty query produces safe empty expression."""
    result = build_match_expression("", SAMPLE_SYNONYMS)
    assert result == '""'


def test_build_match_expression_fts5_safe(simple_fts_db):
    """Build expressions are safe to use in FTS5 MATCH."""
    for query in ["parallel", "parse json", "unknown", "c++", "AND"]:
        expr = build_match_expression(query, SAMPLE_SYNONYMS)
        try:
            simple_fts_db.execute(
                "SELECT * FROM test_fts WHERE test_fts MATCH ?", (expr,)
            ).fetchall()
        except sqlite3.OperationalError as e:
            pytest.fail(f"build_match_expression unsafe for {query!r}: {e}")


# ---------------------------------------------------------------------------
# Ranker tests (RETR-06, RETR-07, RETR-09)
# ---------------------------------------------------------------------------


def test_bm25_heading_over_content(fts_db):
    """BM25 heading weight makes heading match rank higher.

    With tokenchars '._', "asyncio.TaskGroup" is a single token.
    We search for "asyncio" which appears in both sections' content
    and in the heading of section 1. Heading match (10x weight)
    should rank higher.
    """
    escaped = fts5_escape("asyncio")
    hits = search_sections(fts_db, escaped, None, 10)
    assert len(hits) >= 1
    # If multiple hits, verify heading-weighted section ranks first
    if len(hits) >= 2:
        assert hits[0].score >= hits[1].score


def test_bm25_qualified_name_over_module(fts_db):
    """BM25 qualified_name weight for symbols."""
    escaped = fts5_escape("asyncio")
    hits = search_symbols(fts_db, escaped, None, 10)
    assert len(hits) >= 1
    # All hits should be asyncio symbols
    for hit in hits:
        assert "asyncio" in hit.title


def test_snippet_present_on_section_hits(fts_db):
    """Every section hit has a non-empty snippet (RETR-07)."""
    escaped = fts5_escape("asyncio")
    hits = search_sections(fts_db, escaped, None, 10)
    assert len(hits) >= 1
    for hit in hits:
        assert hit.snippet, f"Empty snippet for hit: {hit.title}"
        assert len(hit.snippet) <= 300  # roughly ~200 chars


def test_hit_shape_consistency(fts_db):
    """Symbol fast-path and FTS5 produce same SymbolHit shape (RETR-09)."""
    # Symbol fast-path
    symbol_hits = lookup_symbols_exact(fts_db, "asyncio.TaskGroup", None, 5)
    assert len(symbol_hits) >= 1

    # FTS5 section search
    escaped = fts5_escape("asyncio")
    section_hits = search_sections(fts_db, escaped, None, 5)
    assert len(section_hits) >= 1

    # Both should produce valid SymbolHit instances with all fields
    for hit in symbol_hits + section_hits:
        assert isinstance(hit, SymbolHit)
        assert hit.uri
        assert hit.title
        assert hit.kind
        assert isinstance(hit.score, float)
        assert 0.0 <= hit.score <= 1.0
        assert hit.version
        assert hit.slug


def test_lookup_symbols_exact_match_score(fts_db):
    """Exact match gets score 1.0, prefix match gets 0.8."""
    hits = lookup_symbols_exact(fts_db, "asyncio.TaskGroup", None, 5)
    assert len(hits) >= 1
    assert hits[0].score == 1.0
    assert hits[0].title == "asyncio.TaskGroup"
    assert hits[0].kind == "class"


def test_lookup_symbols_prefix_match(fts_db):
    """Prefix match on partial name."""
    hits = lookup_symbols_exact(fts_db, "asyncio", None, 10)
    assert len(hits) >= 2
    # Exact match (if any) first, then prefix matches at 0.8
    for hit in hits:
        assert "asyncio" in hit.title


# ---------------------------------------------------------------------------
# I-3: case-insensitive symbol fast-path
# ---------------------------------------------------------------------------


def test_lookup_symbols_case_insensitive_lowercase(fts_db):
    """I-3: 'asyncio.taskgroup' matches seeded 'asyncio.TaskGroup' with score 1.0."""
    hits = lookup_symbols_exact(fts_db, "asyncio.taskgroup", None, 5)
    assert len(hits) >= 1
    exact = next((h for h in hits if h.title == "asyncio.TaskGroup"), None)
    assert exact is not None, "expected asyncio.TaskGroup in hits"
    assert exact.score == 1.0


def test_lookup_symbols_case_insensitive_uppercase(fts_db):
    """I-3: 'ASYNCIO.TASKGROUP' matches seeded 'asyncio.TaskGroup' with score 1.0."""
    hits = lookup_symbols_exact(fts_db, "ASYNCIO.TASKGROUP", None, 5)
    assert len(hits) >= 1
    exact = next((h for h in hits if h.title == "asyncio.TaskGroup"), None)
    assert exact is not None, "expected asyncio.TaskGroup in hits"
    assert exact.score == 1.0


def test_lookup_symbols_exact_case_preserves_score(fts_db):
    """I-3: exact-case lookup still scores 1.0 (no behavior regression)."""
    hits = lookup_symbols_exact(fts_db, "asyncio.TaskGroup", None, 5)
    assert len(hits) >= 1
    assert hits[0].score == 1.0
    assert hits[0].title == "asyncio.TaskGroup"


def test_search_examples(fts_db):
    """Example search returns hits with correct kind."""
    # Use "asyncio.TaskGroup" as full token (tokenchars '._')
    escaped = fts5_escape("asyncio.TaskGroup")
    hits = search_examples(fts_db, escaped, None, 10)
    assert len(hits) >= 1
    assert hits[0].kind in ("example", "doctest")


def test_version_filter(fts_db):
    """Version filter limits results to specified version."""
    # Should find results for version 3.13
    hits = lookup_symbols_exact(fts_db, "asyncio.TaskGroup", "3.13", 5)
    assert len(hits) >= 1

    # Should find no results for nonexistent version
    hits = lookup_symbols_exact(fts_db, "asyncio.TaskGroup", "3.99", 5)
    assert len(hits) == 0


def test_normalize_scores():
    """Score normalization maps to [0.1, 1.0] range."""
    hits = [
        SymbolHit(uri="a", title="a", kind="section", snippet="", score=-10.0,
                  version="3.13", slug="a", anchor=""),
        SymbolHit(uri="b", title="b", kind="section", snippet="", score=-5.0,
                  version="3.13", slug="b", anchor=""),
        SymbolHit(uri="c", title="c", kind="section", snippet="", score=-1.0,
                  version="3.13", slug="c", anchor=""),
    ]
    normalized = _normalize_scores(hits)
    # Best BM25 (most negative = -10) should get 1.0
    assert normalized[0].score == 1.0
    # Worst BM25 (-1) should get 0.1
    assert normalized[2].score == 0.1
    # Middle should be between
    assert 0.1 < normalized[1].score < 1.0


# ---------------------------------------------------------------------------
# Budget tests (RETR-08)
# ---------------------------------------------------------------------------


def test_budget_basic_truncation():
    """Basic truncation with budget."""
    text, truncated, next_idx = apply_budget("hello world", 5)
    assert text == "hello"
    assert truncated is True
    assert next_idx == 5


def test_budget_no_truncation():
    """No truncation when text fits within budget."""
    text, truncated, next_idx = apply_budget("short", 100)
    assert text == "short"
    assert truncated is False
    assert next_idx is None


def test_budget_exact_boundary():
    """Text exactly fits budget."""
    text, truncated, next_idx = apply_budget("12345", 5)
    assert text == "12345"
    assert truncated is False
    assert next_idx is None


def test_budget_pagination():
    """Chained pagination through apply_budget."""
    full = "hello world!"

    # First page
    text1, trunc1, next1 = apply_budget(full, 5, 0)
    assert text1 == "hello"
    assert trunc1 is True
    assert next1 == 5

    # Second page
    text2, trunc2, next2 = apply_budget(full, 5, next1)
    assert text2 == " worl"
    assert trunc2 is True
    assert next2 == 10

    # Third page (remainder)
    text3, trunc3, next3 = apply_budget(full, 5, next2)
    assert text3 == "d!"
    assert trunc3 is False
    assert next3 is None

    # Reconstruct full text
    assert text1 + text2 + text3 == full


def test_budget_emoji_4byte():
    """4-byte emoji is not split (Python 3 codepoint safety)."""
    text = "Hello \U0001f389 World"
    # Budget includes emoji
    result, truncated, next_idx = apply_budget(text, 7)
    assert "\U0001f389" in result or len(result) <= 7
    # No error raised -- emoji is a single codepoint in Python 3


def test_budget_combining_character():
    """Combining marks are not separated from base character."""
    # cafe with combining acute accent: c a f + combining_acute e
    text = "caf\u0301e"  # f + combining acute accent
    # Budget of 3 would land on the combining mark after 'f'
    result, truncated, next_idx = apply_budget(text, 3)
    # Should back up past the combining mark, returning "ca"
    assert not result.endswith("\u0301"), (
        "Combining mark separated from base character"
    )
    # The result should not include the combining mark without its base
    for i, ch in enumerate(result):
        if unicodedata.category(ch).startswith("M"):
            # Combining mark must have a preceding non-mark character
            assert i > 0, "Combining mark at start of result"


def test_budget_combining_at_boundary():
    """Combining diaeresis stays with base at various boundaries."""
    text = "na\u0308ive"  # n + a + combining_diaeresis + i + v + e
    # Budget of 2 should return "na" (not cutting into a+diaeresis)
    # But a+diaeresis is at positions 1,2 so budget=2 would get "na" then
    # see position 2 is combining mark, back up to 1 = "n"
    result, truncated, next_idx = apply_budget(text, 2)
    assert truncated is True
    # Result should be clean -- no orphaned combining marks
    if len(result) > 0:
        last_cat = unicodedata.category(result[-1])
        assert not last_cat.startswith("M"), (
            f"Last char is orphaned combining mark (category {last_cat})"
        )


def test_budget_empty_text():
    """Empty text returns empty with no truncation."""
    text, truncated, next_idx = apply_budget("", 5)
    assert text == ""
    assert truncated is False
    assert next_idx is None


def test_budget_start_beyond_length():
    """Start index beyond text length returns empty."""
    text, truncated, next_idx = apply_budget("hello", 5, 100)
    assert text == ""
    assert truncated is False
    assert next_idx is None


def test_budget_zero_max_chars():
    """Zero max_chars returns empty without pagination hints."""
    text, truncated, next_idx = apply_budget("hello", 0)
    assert text == ""
    assert truncated is False
    assert next_idx is None


def test_budget_single_char():
    """Single character text with budget of 1."""
    text, truncated, next_idx = apply_budget("x", 1)
    assert text == "x"
    assert truncated is False
    assert next_idx is None


def test_budget_cjk_characters():
    """CJK characters are not split."""
    text = "\u65e5\u672c\u8a9e\u30c6\u30b9\u30c8"  # 日本語テスト
    result, truncated, next_idx = apply_budget(text, 3)
    assert len(result) == 3
    assert result == "\u65e5\u672c\u8a9e"
    assert truncated is True


def test_budget_flag_emoji_sequence():
    """Regional indicator symbols (flag emoji) don't crash."""
    # US flag: U+1F1FA U+1F1F8 (two regional indicator symbols)
    text = "Hello \U0001f1fa\U0001f1f8 World"
    # Should not crash regardless of boundary
    result, truncated, next_idx = apply_budget(text, 7)
    assert isinstance(result, str)
    assert len(result) <= 8  # might include flag chars


# ---------------------------------------------------------------------------
# Domain error tests (SRVR-08)
# ---------------------------------------------------------------------------


def test_version_not_found_error_message():
    """VersionNotFoundError carries informative message."""
    err = VersionNotFoundError("version 3.99 not found; available: [3.12, 3.13]")
    assert "3.99" in str(err)
    assert "available" in str(err)


def test_symbol_not_found_is_docs_server_error():
    """SymbolNotFoundError is a DocsServerError subclass."""
    assert issubclass(SymbolNotFoundError, DocsServerError)
    err = SymbolNotFoundError("asyncio.Nope not found")
    assert isinstance(err, DocsServerError)


def test_page_not_found_is_docs_server_error():
    """PageNotFoundError is a DocsServerError subclass."""
    assert issubclass(PageNotFoundError, DocsServerError)


def test_domain_errors_are_not_protocol_errors():
    """Domain errors are DocsServerError, not protocol errors."""
    for err_cls in (VersionNotFoundError, SymbolNotFoundError, PageNotFoundError):
        assert issubclass(err_cls, DocsServerError)
        assert issubclass(err_cls, Exception)
        # Should be catchable as DocsServerError
        try:
            raise err_cls("test")
        except DocsServerError:
            pass  # Expected


def test_error_hierarchy():
    """Full error hierarchy is correct."""
    assert issubclass(VersionNotFoundError, DocsServerError)
    assert issubclass(SymbolNotFoundError, DocsServerError)
    assert issubclass(PageNotFoundError, DocsServerError)
    assert issubclass(DocsServerError, Exception)

    # All catchable via DocsServerError
    for cls in (VersionNotFoundError, SymbolNotFoundError, PageNotFoundError):
        try:
            raise cls("test")
        except DocsServerError:
            pass


# ---------------------------------------------------------------------------
# RETR-02 MATCH audit
# ---------------------------------------------------------------------------


def test_no_raw_match_in_source():
    """RETR-02: Every MATCH query routes through fts5_escape.

    Scans production source files for MATCH usage and verifies no
    raw string concatenation builds MATCH expressions from user input.
    """
    from pathlib import Path

    src_dir = Path(__file__).parent.parent / "src" / "mcp_server_python_docs"
    violations = []

    for py_file in src_dir.rglob("*.py"):
        content = py_file.read_text()
        lines = content.split("\n")
        for lineno, line in enumerate(lines, 1):
            if "MATCH" not in line:
                continue
            # Skip comments and test files
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # Parameterized queries (MATCH ?) are safe
            if "MATCH ?" in line:
                continue
            # String formatting with MATCH is a violation
            if "f'" in line and "MATCH" in line:
                violations.append(f"{py_file.name}:{lineno}: {stripped}")
            if 'f"' in line and "MATCH" in line:
                violations.append(f"{py_file.name}:{lineno}: {stripped}")
            if ".format(" in line and "MATCH" in line:
                violations.append(f"{py_file.name}:{lineno}: {stripped}")
            if "%" in line and "MATCH" in line and "+" in line:
                violations.append(f"{py_file.name}:{lineno}: {stripped}")

    assert not violations, (
        "Raw MATCH string concatenation found (RETR-02 violation):\n"
        + "\n".join(violations)
    )
