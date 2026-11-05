"""Credential inspection of typed calibration history, independent of verdict replay.

Archives are exported whole. Inspect every declared source, including sources for
other compositions and optional tuning history, before selecting any verdict.
Declared source layers, bounded character escapes and explicit text/JSON
data URIs are inspected. Explicit YAML metadata encodings are unsupported.
Ordinary prose stays prose, and recorded paths are never opened.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Iterator, Mapping
from typing import Any
from urllib.parse import unquote_to_bytes

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode
from yaml.tokens import DirectiveToken, ScalarToken, TagToken

from benchmarks.agentic import inputs as captured_inputs
from benchmarks.agentic.protocol import (
    MAX_FRAME_BYTES,
    KeyLeakError,
    assert_no_key_shaped,
    assert_no_key_shaped_json,
)
from benchmarks.grading.calibration_storage import MAX_SOURCE_BYTES, MAX_SOURCE_FILES, source_files
from benchmarks.grading.inputs import SNAPSHOT_FILES
from benchmarks.runner import BenchmarkValidationError

# Recover only finite character escapes, anywhere in a string. Unknown/incomplete
# escapes remain prose; quotes do not declare JSON syntax. Actual source loaders
# still validate JSON/YAML and schemas independently of credential inspection.
_LITERAL_ESCAPE = re.compile(r'\\(?:u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|["\\/bfnrt])')
_LITERAL_SIMPLE = {
    '"': '"', "\\": "\\", "/": "/",
    "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t",
}
_METADATA_LAYERS = 4
# Recognize explicit URI prefixes, not mentions or regexes such as data:[^,]+,.*.
# Consume each header/payload once, including failed headers: a search for the
# entire URI could rescan a long comma-free run at every embedded data: prefix.
_DATA_URI = re.compile(
    r"(?<![\w])data:(?=(?:[a-z0-9!#$%&'*+.^_`|~ \t\r\n\f-]+/"
    r"[a-z0-9!#$%&'*+.^_`|~ \t\r\n\f-]+|[ \t\r\n\f]*)[;,])",
    re.IGNORECASE,
)
_URI_HEADER = re.compile(r"[^,\s<>`\\)\]}]*")
_URI_PAYLOAD = re.compile(r'[^\s]*')
_URI_QUOTED_PAYLOAD = {
    close: re.compile(r'[^\s' + re.escape(close) + ']*') for close in ('"', "'", '`', '>')
}
_WHITESPACE = re.compile(r'\s*')
_BASE64_CONTINUATION = re.compile(r'[A-Za-z0-9+/%=]')
_URI_BRACKET_END = re.compile(r'([)\]}])[.,;!?]*$')
_BAD_PERCENT = re.compile(r"%(?![0-9a-fA-F]{2})")
# A payload is required. A bare tag name in prose is not an encoding layer.
_YAML_ENCODING = re.compile(r"(?:!![^\s!<>{}\[\],]+|!<[^<>\r\n]*>)(?=\s+\S)|%(?:TAG|YAML)\b")


def _attachment(header: str, payload: str) -> str:
    """Only explicit plain-text/JSON attachments, never inferred codecs or paths."""
    parts = header.lower().split(";")
    media = parts.pop(0) or "text/plain"
    if media not in ("text/plain", "application/json"):
        raise KeyLeakError("unsupported calibration attachment media type")
    encoded = bool(parts and parts[-1] == "base64")
    if encoded:
        parts.pop()
    if parts not in ([], ["charset=utf-8"], ["charset=us-ascii"]):
        raise KeyLeakError("unsupported calibration attachment encoding")
    try:
        if _BAD_PERCENT.search(payload):
            raise ValueError("invalid percent escape")
        body = unquote_to_bytes(payload)
        if encoded:
            decoded = base64.b64decode(body, validate=True)
            if base64.b64encode(decoded) != body:
                raise ValueError("noncanonical base64")
            body = decoded
        return body.decode("ascii" if parts == ["charset=us-ascii"] else "utf-8")
    except (ValueError, UnicodeError, binascii.Error):
        raise KeyLeakError("uninspectable calibration attachment") from None


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
            raise KeyLeakError("ambiguous calibration attachment token")
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
            and _BASE64_CONTINUATION.match(value, following)
            or not encoded and (last_percent >= end or any(c in gap[0] for c in '\t\r\n'))
        ):
            raise KeyLeakError("ambiguous calibration attachment whitespace")
    return payload, end + 1 if close is not None and terminated else end


def _metadata_text(text: str) -> None:
    """Inspect character escapes and explicit data URIs with shared finite bounds.

    At most four decoding steps along any path, a frame of original UTF-8 and
    a frame of total decoded UTF-8 per string. URI and character scans may
    overlap; both charge the same budget. Every string uses the same rules.
    Iterator frames retain only the active path, not every decoded string.
    """
    def layers(value: str, layer: int) -> Iterator[str]:
        if len(value) > MAX_FRAME_BYTES:
            raise KeyLeakError("calibration metadata inspection exceeds the size limit")
        assert_no_key_shaped_json(value, where="calibration metadata")
        if len(value.encode("utf-8")) > MAX_FRAME_BYTES:
            raise KeyLeakError("calibration metadata inspection exceeds the size limit")
        if _YAML_ENCODING.search(value):
            try:
                # Tokenize without constructors, alias resolution or merge expansion.
                for count, token in enumerate(yaml.scan(value, Loader=yaml.SafeLoader), 1):
                    if count > captured_inputs._MAX_YAML_NODES:
                        raise KeyLeakError("calibration metadata exceeds the YAML token limit")
                    if isinstance(token, (TagToken, DirectiveToken)):
                        raise KeyLeakError("unsupported YAML encoding in calibration metadata")
                    if (isinstance(token, ScalarToken) and token.style in ("'", '"')
                            and _YAML_ENCODING.search(token.value)):
                        if layer == _METADATA_LAYERS:
                            raise KeyLeakError("calibration metadata exceeds the decoding depth")
                        yield token.value
            except yaml.YAMLError:
                raise KeyLeakError("uninspectable YAML calibration metadata") from None
        position = 0
        last_percent = value.rfind('%')
        while opening := _DATA_URI.search(value, position):
            header = _URI_HEADER.match(value, opening.end())
            assert header is not None
            position = header.end()
            if position == len(value):
                continue  # No explicit payload: a mention, not an attachment.
            if value[position] != ",":
                raise KeyLeakError("unsupported calibration attachment header")
            payload, position = _uri_payload(
                value, opening.start(), position + 1,
                encoded=header[0].lower().endswith(';base64'), last_percent=last_percent,
            )
            if layer == _METADATA_LAYERS:
                raise KeyLeakError("calibration metadata exceeds the decoding depth")
            yield _attachment(header.group(), payload)
        decoded = _LITERAL_ESCAPE.sub(
            lambda match: chr(int(match[0][2:], 16)) if match[0][1] in ("u", "x")
            else _LITERAL_SIMPLE[match[0][1]], value,
        )
        if decoded != value:
            if layer == _METADATA_LAYERS:
                raise KeyLeakError("calibration metadata exceeds the decoding depth")
            yield decoded.encode("utf-8", errors="replace").decode("utf-8")

    pending = [(layers(text, 0), 0)]
    remaining = MAX_FRAME_BYTES
    while pending:
        frame, layer = pending[-1]
        try:
            decoded = next(frame)
        except StopIteration:
            pending.pop()
            continue
        assert_no_key_shaped_json(decoded, where="calibration metadata")
        remaining -= len(decoded.encode("utf-8"))
        if remaining < 0:
            raise KeyLeakError("calibration metadata inspection exceeds the size limit")
        if (_LITERAL_ESCAPE.search(decoded) or _DATA_URI.search(decoded)
                or _YAML_ENCODING.search(decoded)):
            pending.append((layers(decoded, layer + 1), layer + 1))


def _metadata(value: Any) -> None:
    """Inspect every key and string in anonymous retained metadata, iteratively."""
    assert_no_key_shaped_json(
        value, where="calibration metadata",
        check_content=lambda body: _metadata_text(body.decode("utf-8")),
    )


def _structured_input(body: bytes) -> None:
    """Scan YAML/JSON scalars without expanding aliases or YAML merge mappings.

    Representation nodes retain duplicate values and share alias nodes. Only
    binary scalars need construction; constructing mappings could amplify a
    small merge graph into unbounded work. Replay still validates the document.
    """
    loader = None
    try:
        loader = yaml.SafeLoader(body)
        pending = [loader.get_single_node()]
        visited: set[int] = set()
        while pending:
            node = pending.pop()
            if node is None or id(node) in visited:
                continue
            visited.add(id(node))
            if isinstance(node, ScalarNode):
                _metadata_text(node.value)
                if node.tag == "tag:yaml.org,2002:binary":
                    assert_no_key_shaped_json(
                        loader.construct_object(node), where="calibration source input",
                        check_content=lambda data: _metadata_text(
                            data.decode("utf-8", errors="replace")
                        ),
                    )
            elif isinstance(node, MappingNode):
                for key, value in node.value:
                    pending.extend((key, value))
            elif isinstance(node, SequenceNode):
                pending.extend(node.value)
    except KeyLeakError:
        raise
    except (yaml.YAMLError, ValueError, TypeError, RecursionError):
        raise BenchmarkValidationError("invalid structured calibration source input") from None
    finally:
        if loader is not None:
            loader.dispose()


def _source_json(body: bytes) -> None:
    assert_no_key_shaped(body, where="calibration source")
    try:
        # Tuples distinguish objects from JSON arrays and retain every duplicate
        # member, including shadowed non-string replies, until inspection ends.
        value = json.loads(body, object_pairs_hook=tuple)
    except KeyLeakError:
        raise
    except (ValueError, RecursionError):
        raise BenchmarkValidationError("invalid JSON calibration source") from None
    _metadata(value)
    if not isinstance(value, tuple):
        raise BenchmarkValidationError("invalid JSON calibration source")


def _unknown_history(value: Any) -> None:
    """Allow absent legacy sections and plain notes; refuse opaque serialized data."""
    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, (dict, list, tuple)) and not value:
        return
    if isinstance(value, str) and len(value) <= MAX_FRAME_BYTES:
        assert_no_key_shaped(value, where="calibration history note")
        if re.fullmatch(r"\s*(?:\{\s*\}|\[\s*\])\s*", value):
            return
        # Punctuation, Unicode and literal escape examples are ordinary prose.
        # Serialized objects and binary YAML remain opaque outside a declared
        # serialization slot. Prose still receives shared character inspection.
        if not re.search(r"[{}\[\]`]|!!binary|!<|%(?:TAG|YAML)", value):
            _metadata_text(value)
            return
    raise KeyLeakError("uninspectable calibration history section")


def scan_calibration_source(proof: Any, budget: list[int] | None = None) -> None:
    if budget is None:
        budget = [0, 0]
    # Missing/unknown optional history is still replay's decision. This boundary
    # inspects typed contents without upgrading any history into evidence.
    if not isinstance(proof, dict):
        _unknown_history(proof)
        return
    # Every retained member crosses the boundary, including legacy extensions.
    # Declared input/file/reply layers have their own formats. Other members use
    # the bounded JSON metadata inspector without opening any recorded path.
    for name, value in proof.items():
        _metadata_text(name)
        if name not in ("inputs", "files", "blobs"):
            _metadata(value)
    for section in ("inputs", "files"):
        contents = proof.get(section)
        if section == "files" and (
            isinstance(contents, dict) or proof.get("version") == 2 or "blobs" in proof
        ):
            contents = source_files(proof)
        if not isinstance(contents, Mapping):
            _unknown_history(contents)
            continue
        for name, text in contents.items():
            _metadata_text(name)
            if not isinstance(text, str):
                _unknown_history(text)
                continue
            body = text.encode("utf-8")
            assert_no_key_shaped(body, where="calibration source")
            budget[0] += len(body)
            budget[1] += 1
            if budget[0] > MAX_SOURCE_BYTES or budget[1] > MAX_SOURCE_FILES:
                raise BenchmarkValidationError("calibration source exceeds the aggregate limit")
            if len(body) > MAX_FRAME_BYTES:
                raise BenchmarkValidationError("calibration source exceeds the size limit")
            filename = (
                SNAPSHOT_FILES.get(name, {"corpus": "corpus.yml", "tuning": "tuning.yml"}.get(name))
                if section == "inputs" else name
            )
            if not isinstance(filename, str):
                _unknown_history(text)
                continue
            if filename.endswith((".yml", ".yaml")) or (
                section == "inputs" and filename.endswith(".json")
            ):
                _structured_input(body)
            elif filename.endswith(".json"):
                _source_json(body)
            elif section == "files":
                _unknown_history(text)
            else:
                # Literal prompts/regexes (notably masking.txt) are not JSON.
                # Character escapes and attachments cross the common boundary.
                _metadata_text(text)


def scan_calibration_sources(data: dict[str, Any]) -> None:
    """Inspect all exported entries, never just the run's selected composition.

    The caller bounds the complete archive before parsing. Each source text is
    visited once in its declared slot. Other retained keys/values use the same
    bounded metadata inspector, without expanding packed sources again.
    No recorded path is opened and no receipt/provenance decision is made here.
    """
    budget = [0, 0]
    for name, value in data.items():
        _metadata_text(name)
        if name != "entries":
            _metadata(value)
    entries = data["entries"]
    for name in entries:
        _metadata_text(name)
    pending = list(entries.values())
    visited: set[int] = set()
    while pending:
        entry = pending.pop()
        if not isinstance(entry, dict):
            _metadata(entry)
            continue
        if id(entry) in visited:
            continue
        visited.add(id(entry))
        for name, value in entry.items():
            _metadata_text(name)
            if name == "source":
                scan_calibration_source(value, budget)
            elif name == "tuning":
                if isinstance(value, list):
                    for record in value:
                        if isinstance(record, dict):
                            pending.append(record)
                        else:
                            _unknown_history(record)
                else:
                    _unknown_history(value)
            else:
                _metadata(value)
