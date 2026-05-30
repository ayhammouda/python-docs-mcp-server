"""Versioned codecs for cache-at-rest payloads."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import zstandard as zstd

_SUPPORTED_CODECS = ["none", "zstd", "zstd-dict-v1"]


@dataclass(frozen=True)
class _Codec:
    encode: Callable[[str, object | None], bytes]
    decode: Callable[[bytes, object | None], str]


def list_supported() -> list[str]:
    """Return codec ids in stable preference order."""

    return list(_SUPPORTED_CODECS)


def encode(text: str, codec: str, *, dictionary: object | None = None) -> bytes:
    """Encode text using a supported cache codec."""

    try:
        handler = _REGISTRY[codec]
    except KeyError as e:
        raise ValueError(f"Unsupported cache codec: {codec}") from e
    return handler.encode(text, dictionary)


def decode(blob: bytes, codec: str, *, dictionary: object | None = None) -> str:
    """Decode text using the codec stored with the cache row."""

    try:
        handler = _REGISTRY[codec]
    except KeyError as e:
        raise ValueError(f"Unsupported cache codec: {codec}") from e
    return handler.decode(blob, dictionary)


def _encode_none(text: str, dictionary: object | None) -> bytes:
    _reject_dictionary("none", dictionary)
    return text.encode("utf-8")


def _decode_none(blob: bytes, dictionary: object | None) -> str:
    _reject_dictionary("none", dictionary)
    return blob.decode("utf-8")


def _encode_zstd(text: str, dictionary: object | None) -> bytes:
    _reject_dictionary("zstd", dictionary)
    try:
        return zstd.ZstdCompressor().compress(text.encode("utf-8"))
    except zstd.ZstdError as e:
        raise ValueError(f"zstd encode failed: {e}") from e


def _decode_zstd(blob: bytes, dictionary: object | None) -> str:
    _reject_dictionary("zstd", dictionary)
    try:
        return zstd.ZstdDecompressor().decompress(blob).decode("utf-8")
    except zstd.ZstdError as e:
        raise ValueError(f"zstd decode failed: {e}") from e


def _encode_zstd_dict(text: str, dictionary: object | None) -> bytes:
    try:
        return zstd.ZstdCompressor(dict_data=_coerce_dictionary(dictionary)).compress(
            text.encode("utf-8")
        )
    except zstd.ZstdError as e:
        raise ValueError(f"zstd dictionary encode failed: {e}") from e


def _decode_zstd_dict(blob: bytes, dictionary: object | None) -> str:
    try:
        return (
            zstd.ZstdDecompressor(dict_data=_coerce_dictionary(dictionary))
            .decompress(blob)
            .decode("utf-8")
        )
    except zstd.ZstdError as e:
        raise ValueError(f"zstd dictionary decode failed: {e}") from e


def _reject_dictionary(codec: str, dictionary: object | None) -> None:
    if dictionary is not None:
        raise ValueError(f"Codec {codec!r} does not use a dictionary")


def _coerce_dictionary(dictionary: object | None) -> zstd.ZstdCompressionDict:
    if dictionary is None:
        raise ValueError("Codec 'zstd-dict-v1' requires an explicit dictionary")
    if isinstance(dictionary, zstd.ZstdCompressionDict):
        return dictionary
    if isinstance(dictionary, bytes):
        return zstd.ZstdCompressionDict(dictionary)
    if isinstance(dictionary, bytearray | memoryview):
        return zstd.ZstdCompressionDict(bytes(dictionary))
    raise TypeError(f"Unsupported zstd dictionary object: {type(dictionary).__name__}")


_REGISTRY: dict[str, _Codec] = {
    "none": _Codec(_encode_none, _decode_none),
    "zstd": _Codec(_encode_zstd, _decode_zstd),
    "zstd-dict-v1": _Codec(_encode_zstd_dict, _decode_zstd_dict),
}

__all__ = ["decode", "encode", "list_supported"]
