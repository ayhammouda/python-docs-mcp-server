"""Cache codec coverage."""

from __future__ import annotations

import zstandard as zstd

from mcp_server_python_docs.cache.codec import decode, encode, list_supported


def _test_dictionary() -> zstd.ZstdCompressionDict:
    samples = [
        (
            f"Python documentation section {i}: json dumps loads encoder decoder "
            "arguments return values exceptions examples. "
        ).encode("utf-8")
        * 8
        for i in range(64)
    ]
    return zstd.train_dictionary(512, samples)


def test_list_supported_is_stable() -> None:
    assert list_supported() == ["none", "zstd", "zstd-dict-v1"]


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


def test_zstd_dict_v1_round_trips_with_explicit_dictionary() -> None:
    dictionary = _test_dictionary()
    text = "Python documentation section 7: json dumps loads encoder decoder arguments."
    encoded = encode(text, "zstd-dict-v1", dictionary=dictionary)
    assert decode(encoded, "zstd-dict-v1", dictionary=dictionary) == text


def test_none_decodes_payload_from_prior_server_version() -> None:
    prior_payload = b'{"content":"legacy uncompressed json","version":"3.12"}'
    assert decode(prior_payload, "none") == prior_payload.decode("utf-8")
