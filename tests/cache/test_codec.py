"""Cache codec coverage."""

from __future__ import annotations

import pytest

from mcp_server_python_docs.cache.codec import decode, encode, list_supported


def test_list_supported_is_stable() -> None:
    assert list_supported() == ["none", "zstd"]


def test_none_round_trips_text() -> None:
    text = '{"content":"plain json payload","version":"3.13"}'
    encoded = encode(text, "none")
    assert encoded == text.encode("utf-8")
    assert decode(encoded, "none") == text


def test_zstd_round_trips_text() -> None:
    text = '{"content":"compressed json payload","version":"3.13"}'
    encoded = encode(text, "zstd")
    assert encoded != text.encode("utf-8")
    assert decode(encoded, "zstd") == text


def test_none_decodes_payload_from_prior_server_version() -> None:
    prior_payload = b'{"content":"legacy uncompressed json","version":"3.12"}'
    assert decode(prior_payload, "none") == prior_payload.decode("utf-8")


def test_dictionary_codec_is_no_longer_supported() -> None:
    with pytest.raises(ValueError, match="Unsupported cache codec: zstd-dict-v1"):
        encode("x", "zstd-dict-v1")
    with pytest.raises(ValueError, match="Unsupported cache codec: zstd-dict-v1"):
        decode(b"x", "zstd-dict-v1")
