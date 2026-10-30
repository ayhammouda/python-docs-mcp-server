"""Versioned codecs for cache-at-rest payloads."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import zstandard as zstd

_SUPPORTED_CODECS = ["none", "zstd"]


@dataclass(frozen=True)
class _Codec:
    encode: Callable[[str], bytes]
    decode: Callable[[bytes], str]


def list_supported() -> list[str]:
    """Return codec ids in stable preference order."""

    return list(_SUPPORTED_CODECS)


def encode(text: str, codec: str) -> bytes:
    """Encode text using a supported cache codec."""

    try:
        handler = _REGISTRY[codec]
    except KeyError as e:
        raise ValueError(f"Unsupported cache codec: {codec}") from e
    return handler.encode(text)


def decode(blob: bytes, codec: str) -> str:
    """Decode text using the codec stored with the cache row."""

    try:
        handler = _REGISTRY[codec]
    except KeyError as e:
        raise ValueError(f"Unsupported cache codec: {codec}") from e
    return handler.decode(blob)


def _encode_none(text: str) -> bytes:
    return text.encode("utf-8")


def _decode_none(blob: bytes) -> str:
    return blob.decode("utf-8")


def _encode_zstd(text: str) -> bytes:
    try:
        return zstd.ZstdCompressor().compress(text.encode("utf-8"))
    except zstd.ZstdError as e:
        raise ValueError(f"zstd encode failed: {e}") from e


def _decode_zstd(blob: bytes) -> str:
    try:
        return zstd.ZstdDecompressor().decompress(blob).decode("utf-8")
    except zstd.ZstdError as e:
        raise ValueError(f"zstd decode failed: {e}") from e


_REGISTRY: dict[str, _Codec] = {
    "none": _Codec(_encode_none, _decode_none),
    "zstd": _Codec(_encode_zstd, _decode_zstd),
}

__all__ = ["decode", "encode", "list_supported"]
