"""Shared report records, Markdown formatting and percentiles for both renderers."""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CellRecord:
    """One merged competitor/question result, joined across artifact kinds."""

    competitor_id: str
    corpus_id: str
    tool_model_key: str
    category: str
    status: str
    error_category: str | None
    score: float | None
    requires_manual_scoring: bool
    included_in_correctness_denominator: bool
    latency_ms: float | None
    client_wrapped_tokens: int | None
    raw_payload_tokens: int | None
    token_approximation: bool
    """True when the token record's ``client_wrapped_tokens`` count (if any) is an
    approximation -- issue #89 / methodology amendment of 2026-10-30: the fixed-flow
    envelope has name-only tool stubs and no system prompt. Approximate counts must
    never be used for headline claims (see ``_basic_stats``, which excludes them)."""
    token_status: str | None
    """The token record's ``status`` (``counted``, ``placeholder``, ``failed``), or
    ``None`` when the cell has no token record or the record has no status."""


@dataclass(frozen=True)
class RunBundle:
    """Loaded and validated raw artifacts for one benchmark run."""

    run_dir: Path
    run_summary: dict[str, Any]
    environment: dict[str, Any]
    corpus_hash: str
    manifest_hash: str
    competitors: dict[str, dict[str, Any]]
    exclusions: list[dict[str, Any]]
    cells: list[CellRecord]
    scoring: dict[tuple[str, str], dict[str, Any]]
    latency: dict[tuple[str, str], dict[str, Any]]
    tokens: dict[tuple[str, str], dict[str, Any]]
    failure_records: list[dict[str, Any]]
    token_failure_records: list[dict[str, Any]]


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[int(rank)]
    return ordered[lower] * (upper - rank) + ordered[upper] * (rank - lower)


def _relative_link(target: Path, from_dir: Path) -> str:
    return os.path.relpath(target.resolve(), start=from_dir.resolve())


#: Every Markdown-significant ASCII punctuation character, a dot not followed by a
#: digit, the dot of "www.", and the first slash of "//". Digits are exempt so numbers
#: such as 1000.0 and 52.0% stay unescaped; a dot before any letter, ASCII or not, is
#: escaped, so a non-ASCII TLD cannot form a domain either. The escaped slash stops
#: protocol-relative links ("//host"), which a dot before a digit does not. Escaping
#: keeps raw HTML, images, code spans and extra columns out of a cell, and it stops
#: "scheme://", "www.", "//host" and bare-domain autolinks (an escaped ":", "." or
#: "/"), so no link's visible text differs from its target. It cannot stop every link:
#: see the email shortcut in _cell. "_" counts only at a word edge: between letters or
#: digits it cannot open or close emphasis.
_CELL_MARKDOWN = re.compile(
    r"[\\`*\[\]<>()!|&#~:]|\.(?![0-9])|(?<=www)\.|/(?=/)|(?<![^\W_])_|_(?![^\W_])"
)
#: C0 controls and DEL: one of them inside text can change how a renderer parses it.
_CELL_CONTROLS = dict.fromkeys([*range(0x20), 0x7F], "�")


def _cell(text: str) -> str:
    """Render ``text`` as one literal plain-text table cell.

    Line breaks and tabs become spaces (a newline would end the row), any other
    control character becomes U+FFFD, and then one linear pass puts a backslash before
    every significant character, so a proxy error body such as "Error | Gateway"
    cannot add a column and ``__init__`` stays ``__init__``.
    """
    text = re.sub(r"\r\n|[\r\n\t]", " ", text).translate(_CELL_CONTROLS)
    # shortcut: cmark-gfm (GitHub's renderer) still links an email address in cell text,
    # because its autolink pass runs after escapes resolve; the link text equals the
    # address, so it is not deceptive (owner accepted, 2026-10-30). Upgrade if a renderer
    # used for reports links an email with text that differs from its target, or if the
    # cell contract is tightened to forbid every link.
    return _CELL_MARKDOWN.sub(r"\\\g<0>", text)


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    lines.extend("| " + " | ".join(_cell(cell) for cell in row) + " |" for row in rows)
    return lines
