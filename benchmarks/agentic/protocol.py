"""Length-prefixed JSON frames between host and worker, and the key scans (ruling B3).

A frame is an 8-byte big-endian length and compact UTF-8 JSON. Frames of any
size up to MAX_FRAME_BYTES round-trip; a larger one is a harness error on the
side that meets it.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from benchmarks.runner import BenchmarkValidationError

MAX_FRAME_BYTES = 64 * 1024 * 1024
#: A key starts a token, or follows a URL escape such as %20; a run of base64 never does.
_START = r"(?:(?<=%[0-9A-Fa-f]{2})|(?<![A-Za-z0-9_-]))"
_END = r"(?![A-Za-z0-9_-])"
#: Real key shapes, by provider. The Anthropic literal is specific enough to need no start
#: boundary, so a key glued to a digit (``7sk-ant-api03-...``) is still caught.
KEY_PATTERNS = {
    "anthropic": r"sk-ant-api\d\d-[A-Za-z0-9_-]{80,}" + _END,
    "openai": _START + r"sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{40,}" + _END,
    "google": _START + r"AIza[0-9A-Za-z_-]{35}" + _END,
}
KEY_SHAPED = re.compile("|".join(f"(?P<{name}>{p})" for name, p in KEY_PATTERNS.items()))


class KeyLeakError(BenchmarkValidationError):
    """A provider key, or a string shaped like one, was about to leave a process."""


class FrameTooLarge(BenchmarkValidationError):
    """A frame over the size limit: a harness error, never a model failure."""


def encode_frame(message: dict[str, Any], *, limit: int = MAX_FRAME_BYTES) -> bytes:
    body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > limit:
        raise FrameTooLarge(f"a {len(body)}-byte frame is over the {limit}-byte limit")
    return len(body).to_bytes(8, "big") + body


async def read_frame(
    reader: asyncio.StreamReader, *, limit: int = MAX_FRAME_BYTES
) -> dict[str, Any] | None:
    """The next frame; None at a clean EOF or a partial frame (the writer died)."""
    try:
        length = int.from_bytes(await reader.readexactly(8), "big")
        if length > limit:
            raise FrameTooLarge(f"a {length}-byte frame is over the {limit}-byte limit")
        body = await reader.readexactly(length)
    except asyncio.IncompleteReadError:
        return None
    return json.loads(body)


def assert_no_key_shaped(text: str, *, where: str) -> None:
    if match := KEY_SHAPED.search(text):
        # Names the place and the pattern, never the match.
        raise KeyLeakError(f"a key-shaped string ({match.lastgroup} pattern) was found in {where}")


def contains_own_key(text: str, own_key: str | None) -> bool:
    return bool(own_key) and own_key in text
