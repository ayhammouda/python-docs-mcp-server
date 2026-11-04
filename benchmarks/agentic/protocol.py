"""Length-prefixed JSON frames between host and worker, and the key scans (ruling B3).

A frame is an 8-byte big-endian length and compact UTF-8 JSON. Frames of any
size up to MAX_FRAME_BYTES round-trip; a larger one is a harness error on the
side that meets it.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
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
_KEY_SHAPED_BYTES = re.compile(KEY_SHAPED.pattern.encode())


class KeyLeakError(BenchmarkValidationError):
    """A provider key, or a string shaped like one, was about to leave a process."""


class FrameTooLarge(BenchmarkValidationError):
    """A frame over the size limit: a harness error, never a model failure."""


def encode_frame(
    message: dict[str, Any], *, limit: int = MAX_FRAME_BYTES, where: str | None = None
) -> bytes:
    # Only the private host-to-worker bootstrap may carry a credential.
    if where is None:
        body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    else:
        body = scanned_json_bytes(message, where=where, ensure_ascii=False, separators=(",", ":"))
    if len(body) > limit:
        raise FrameTooLarge(f"a {len(body)}-byte frame is over the {limit}-byte limit")
    return len(body).to_bytes(8, "big") + body


async def read_frame(
    reader: asyncio.StreamReader, *, limit: int = MAX_FRAME_BYTES, where: str | None = None
) -> dict[str, Any] | None:
    """The next frame; None at a clean EOF or a partial frame (the writer died)."""
    try:
        length = int.from_bytes(await reader.readexactly(8), "big")
        if length > limit:
            raise FrameTooLarge(f"a {length}-byte frame is over the {limit}-byte limit")
        body = await reader.readexactly(length)
    except asyncio.IncompleteReadError:
        return None
    if where is not None:
        assert_no_key_shaped(body, where=where)
    message = json.loads(body)
    if where is not None:
        assert_no_key_shaped_json(message, where=where)
    return message


def _read_exactly(fd: int, size: int) -> bytes | None:
    parts: list[bytes] = []
    while size:
        chunk = os.read(fd, size)
        if not chunk:
            return None
        parts.append(chunk)
        size -= len(chunk)
    return b"".join(parts)


def read_frame_fd(fd: int, *, limit: int = MAX_FRAME_BYTES) -> dict[str, Any] | None:
    """Exactly one frame from ``fd``, unbuffered, so no byte of the next frame is consumed.

    None at EOF or a partial frame. The worker reads its first frame this way, before any
    stream wraps its stdin.
    """
    header = _read_exactly(fd, 8)
    if header is None:
        return None
    length = int.from_bytes(header, "big")
    if length > limit:
        raise FrameTooLarge(f"a {length}-byte frame is over the {limit}-byte limit")
    body = _read_exactly(fd, length)
    return None if body is None else json.loads(body)


def redact_key_shaped(data: bytes) -> tuple[bytes, str | None]:
    """``data`` with every key shape replaced by ``[redacted]``, and the first pattern's name."""
    match = _KEY_SHAPED_BYTES.search(data)
    if match is None:
        return data, None
    redacted = _KEY_SHAPED_BYTES.sub(b"[redacted]", data)
    # Removing a key can give an adjacent key a token boundary. Withhold ambiguous output.
    if _KEY_SHAPED_BYTES.search(redacted):
        redacted = b"[redacted]"
    return redacted, match.lastgroup


def redact_key_shaped_text(text: str) -> str:
    """Use the same key scan for printable errors and byte-oriented artifacts."""
    redacted, pattern = redact_key_shaped(text.encode("utf-8", errors="surrogatepass"))
    return redacted.decode("utf-8", errors="surrogatepass") if pattern else text


def assert_no_key_shaped(text: str | bytes, *, where: str) -> None:
    match = _KEY_SHAPED_BYTES.search(text) if isinstance(text, bytes) else KEY_SHAPED.search(text)
    if match:
        # Names the place and the pattern, never the match.
        raise KeyLeakError(f"a key-shaped string ({match.lastgroup} pattern) was found in {where}")


def assert_no_key_shaped_json(value: object, *, where: str) -> None:
    """Check decoded JSON strings and object names before escaping changes token boundaries."""
    if isinstance(value, str):
        assert_no_key_shaped(value, where=where)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            assert_no_key_shaped_json(key, where=where)
            assert_no_key_shaped_json(item, where=where)
    elif isinstance(value, (list, tuple)):
        for item in value:
            assert_no_key_shaped_json(item, where=where)


def scanned_json_bytes(value: object, *, where: str, **options: Any) -> bytes:
    """Serialize only after checking the fields, then check the exact UTF-8 JSON for publication."""
    assert_no_key_shaped_json(value, where=where)
    body = json.dumps(value, **options).encode("utf-8")
    assert_no_key_shaped(body, where=where)
    return body


def write_json_evidence(path: Path, record: dict[str, Any]) -> None:
    data = scanned_json_bytes(record, where="JSON evidence", indent=2, sort_keys=True) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
