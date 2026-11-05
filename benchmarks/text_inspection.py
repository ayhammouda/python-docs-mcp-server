"""Bounded inspection of retained text, shared by capture and archive replay.

Recover finite character escapes and explicit attachments without parsing prose
quotes or constructing objects. Owning source readers still validate syntax.
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Iterator
from urllib.parse import unquote_to_bytes

import yaml
from yaml.tokens import DirectiveToken, ScalarToken, TagToken

from benchmarks.agentic.protocol import MAX_FRAME_BYTES as MAX_FRAME_BYTES
from benchmarks.agentic.protocol import KeyLeakError, assert_no_key_shaped_json

# Recover only finite character escapes, anywhere in a string. Unknown/incomplete
# escapes remain prose; quotes do not declare JSON syntax. Actual source loaders
# still validate JSON/YAML and schemas independently of credential inspection.
_LITERAL_ESCAPE = re.compile(r'\\(?:u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|["\\/bfnrt])')
_LITERAL_SIMPLE = {
    '"': '"', "\\": "\\", "/": "/",
    "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t",
}
_METADATA_LAYERS = 4
# Recognize declarations before validating their headers. The first comma ends
# the header even for unsupported media labels, whitespace or regex examples.
# Consume it once so repeated comma-free prefixes cannot cause quadratic work.
_DATA_URI = re.compile(r"(?<![\w])data:", re.IGNORECASE)
_URI_HEADER = re.compile(r"[^,]*")
_URI_PAYLOAD = re.compile(r'[^\s]*')
_URI_QUOTED_PAYLOAD = {
    close: re.compile(r'[^\s' + re.escape(close) + ']*') for close in ('"', "'", '`', '>')
}
_WHITESPACE = re.compile(r'\s*')
_URI_BRACKET_END = re.compile(r'([)\]}])[.,;!?]*$')
_BAD_PERCENT = re.compile(r"%(?![0-9a-fA-F]{2})")
# A payload is required. A bare tag name in prose is not an encoding layer.
_YAML_ENCODING = re.compile(r"(?:!![^\s!<>{}\[\],]+|!<[^<>\r\n]*>)(?=\s+\S)|%(?:TAG|YAML)\b")


def _attachment(header: str, payload: str) -> str:
    """Inspect explicit UTF-8 text; never infer codecs from unknown media labels."""
    parts = header.lower().split(";")
    media = parts.pop(0) or "text/plain"
    encoded = bool(parts and parts[-1] == "base64")
    if encoded:
        parts.pop()
    # Percent decoding does not depend on a media label. Inspect UTF-8 text even
    # with an unknown/bare/malformed label (including data: regex examples).
    # Base64/parameterized declarations retain the supported canonical grammar.
    if (encoded or parts) and media not in ("text/plain", "application/json"):
        raise KeyLeakError("unsupported text attachment media type")
    if parts not in ([], ["charset=utf-8"], ["charset=us-ascii"]):
        raise KeyLeakError("unsupported text attachment encoding")
    try:
        if (
            (encoded or parts or media in ("text/plain", "application/json"))
            and _BAD_PERCENT.search(payload)
        ):
            raise ValueError("invalid percent escape")
        body = unquote_to_bytes(payload)
        if encoded:
            decoded = base64.b64decode(body, validate=True)
            if base64.b64encode(decoded) != body:
                raise ValueError("noncanonical base64")
            body = decoded
        return body.decode("ascii" if parts == ["charset=us-ascii"] else "utf-8")
    except (ValueError, UnicodeError, binascii.Error):
        raise KeyLeakError("uninspectable text attachment") from None


def _uri_payload(
    value: str, opening: int, start: int, *, encoded: bool, last_percent: int,
) -> tuple[str, int]:
    """Consume a whole token; never silently accept a punctuation/space prefix.

    Quotes/markup can delimit a URI when they surround it. A closing bracket is
    a prose wrapper only at the end of the token; internal punctuation remains
    payload. Noncanonical whitespace continuations are refused rather than
    inventing a forgiving codec or silently inspecting only their first chunk.
    """
    wrapper = value[opening - 1] if opening else ""
    close = {'"': '"', "'": "'", '`': '`', '<': '>'}.get(wrapper)
    pattern = _URI_QUOTED_PAYLOAD[close] if close is not None else _URI_PAYLOAD
    match = pattern.match(value, start)
    assert match is not None
    end = match.end()
    terminated = False
    if close is not None and end < len(value) and value[end] == close:
        if end + 1 < len(value) and not (
            value[end + 1].isspace() or value[end + 1] in ',;:.)]}'
        ):
            raise KeyLeakError("ambiguous text attachment token")
        terminated = True
    payload = value[start:end]
    bracket = {'(': ')', '[': ']', '{': '}'}.get(wrapper)
    suffix = _URI_BRACKET_END.search(payload) if bracket is not None else None
    if suffix is not None and suffix[1] == bracket:
        payload = payload[:suffix.start()]
        terminated = True
    if not terminated and end < len(value) and value[end].isspace():
        gap = _WHITESPACE.match(value, end)
        assert gap is not None
        following = gap.end()
        if following < len(value) and (
            encoded and not payload.endswith('=')
            or not encoded and (last_percent >= end or any(c in gap[0] for c in '\t\r\n'))
        ):
            raise KeyLeakError("ambiguous text attachment whitespace")
    return payload, end + 1 if close is not None and terminated else end


def inspect_text(text: str, *, limit: int, yaml_token_limit: int) -> None:
    """Inspect character escapes and explicit data URIs with shared finite bounds.

    At most four decoding steps along any path, a frame of original UTF-8 and
    a frame of total decoded UTF-8 per string. URI and character scans may
    overlap; both charge the same budget. Every string uses the same rules.
    Iterator frames retain only the active path, not every decoded string.
    """
    def layers(value: str, layer: int) -> Iterator[str]:
        if len(value) > limit:
            raise KeyLeakError("retained text inspection exceeds the size limit")
        assert_no_key_shaped_json(value, where="retained text")
        if len(value.encode("utf-8")) > limit:
            raise KeyLeakError("retained text inspection exceeds the size limit")
        if _YAML_ENCODING.search(value):
            try:
                # Tokenize without constructors, alias resolution or merge expansion.
                for count, token in enumerate(yaml.scan(value, Loader=yaml.SafeLoader), 1):
                    if count > yaml_token_limit:
                        raise KeyLeakError("retained text exceeds the YAML token limit")
                    if isinstance(token, (TagToken, DirectiveToken)):
                        raise KeyLeakError("unsupported YAML encoding in retained text")
                    if (isinstance(token, ScalarToken) and token.style in ("'", '"')
                            and _YAML_ENCODING.search(token.value)):
                        if layer == _METADATA_LAYERS:
                            raise KeyLeakError("retained text exceeds the decoding depth")
                        yield token.value
            except yaml.YAMLError:
                raise KeyLeakError("uninspectable YAML retained text") from None
        position = 0
        last_percent = value.rfind('%')
        plain_percent_checked = False
        while opening := _DATA_URI.search(value, position):
            header = _URI_HEADER.match(value, opening.end())
            assert header is not None
            position = header.end()
            if position == len(value):
                continue  # No explicit payload: a mention, not an attachment.
            if ";" not in header[0] and header[0].lower() not in (
                "", "text/plain", "application/json",
            ):
                # Bare labels can be prose. Inspect their complete potential
                # percent layer once while preserving literal placeholders.
                position += 1
                if not plain_percent_checked:
                    plain_percent_checked = True
                    decoded = _attachment(header[0], value)
                    if decoded != value:
                        if layer == _METADATA_LAYERS:
                            raise KeyLeakError("retained text exceeds the decoding depth")
                        yield decoded
                continue
            payload, position = _uri_payload(
                value, opening.start(), position + 1,
                encoded=header[0].lower().endswith(';base64'), last_percent=last_percent,
            )
            if layer == _METADATA_LAYERS:
                raise KeyLeakError("retained text exceeds the decoding depth")
            yield _attachment(header.group(), payload)
        decoded = _LITERAL_ESCAPE.sub(
            lambda match: chr(int(match[0][2:], 16)) if match[0][1] in ("u", "x")
            else _LITERAL_SIMPLE[match[0][1]], value,
        )
        if decoded != value:
            if layer == _METADATA_LAYERS:
                raise KeyLeakError("retained text exceeds the decoding depth")
            yield decoded.encode("utf-8", errors="replace").decode("utf-8")

    pending = [(layers(text, 0), 0)]
    remaining = limit
    while pending:
        frame, layer = pending[-1]
        try:
            decoded = next(frame)
        except StopIteration:
            pending.pop()
            continue
        assert_no_key_shaped_json(decoded, where="retained text")
        remaining -= len(decoded.encode("utf-8"))
        if remaining < 0:
            raise KeyLeakError("retained text inspection exceeds the size limit")
        if (_LITERAL_ESCAPE.search(decoded) or _DATA_URI.search(decoded)
                or _YAML_ENCODING.search(decoded)):
            pending.append((layers(decoded, layer + 1), layer + 1))
