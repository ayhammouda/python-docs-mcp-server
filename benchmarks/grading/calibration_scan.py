"""Credential inspection of typed calibration history, independent of verdict replay.

Archives are exported whole. Inspect every declared source, including sources for
other compositions and optional tuning history, before selecting any verdict.
Declared source layers, bounded embedded JSON strings and explicit text/JSON
data URIs are inspected. Ordinary prose stays prose; recorded paths are never opened.
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

from benchmarks.agentic.protocol import (
    MAX_FRAME_BYTES,
    KeyLeakError,
    assert_no_key_shaped,
    assert_no_key_shaped_json,
    check_json_text,
)
from benchmarks.grading.calibration_storage import MAX_SOURCE_BYTES, MAX_SOURCE_FILES, source_files
from benchmarks.grading.inputs import SNAPSHOT_FILES
from benchmarks.runner import BenchmarkValidationError

# Like the wire recovery checker, consume each plain run/escape once, retaining
# duplicate keys and incomplete string prefixes without parsing an object. Gaps
# are prose here: Windows paths and literal escape examples are not JSON layers.
_QUOTED_PART = re.compile(r'[^"\\]+|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4})')
_METADATA_LAYERS = 4
# Recognize explicit URI prefixes, not mentions or regexes such as data:[^,]+,.*.
# Consume each header/payload once, including failed headers: a search for the
# entire URI could rescan a long comma-free run at every embedded data: prefix.
_DATA_URI = re.compile(
    r"(?<![\w])data:(?=(?:[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+)?[;,])",
    re.IGNORECASE,
)
_URI_HEADER = re.compile(r"[^,\s<>`\\)\]}]*")
_URI_PAYLOAD = re.compile(r'[^\s<>"`\\)\]}]*')
_BAD_PERCENT = re.compile(r"%(?![0-9a-fA-F]{2})")


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
            body = base64.b64decode(body, validate=True)
        return body.decode("ascii" if parts == ["charset=us-ascii"] else "utf-8")
    except (ValueError, UnicodeError, binascii.Error):
        raise KeyLeakError("uninspectable calibration attachment") from None


def _metadata_text(text: str, *, quoted: bool = True) -> None:
    """Inspect quoted JSON and explicit data URIs with shared finite bounds.

    At most four decoding steps along any path, a frame of original UTF-8 and
    a frame of total decoded UTF-8 per string. URI and quote scans may overlap;
    both charge the same budget. Literal prompt/regex inputs skip only their
    outer quote recovery. Decoded attachments always receive full inspection.
    Iterator frames retain only the active path, not every decoded string.
    """
    def layers(value: str, layer: int, recover_quotes: bool) -> Iterator[str]:
        if len(value) > MAX_FRAME_BYTES:
            raise KeyLeakError("calibration metadata inspection exceeds the size limit")
        assert_no_key_shaped_json(value, where="calibration metadata")
        if len(value.encode("utf-8")) > MAX_FRAME_BYTES:
            raise KeyLeakError("calibration metadata inspection exceeds the size limit")
        position = 0
        while opening := _DATA_URI.search(value, position):
            header = _URI_HEADER.match(value, opening.end())
            assert header is not None
            position = header.end()
            if position == len(value) or value[position] != ",":
                continue  # No explicit payload: a mention, not an attachment.
            payload = _URI_PAYLOAD.match(value, position + 1)
            assert payload is not None
            position = payload.end()
            if layer == _METADATA_LAYERS:
                raise KeyLeakError("calibration metadata exceeds the decoding depth")
            yield _attachment(header.group(), payload.group())
        if not recover_quotes:
            return
        position = 0
        while (start := value.find('"', position)) != -1:
            slash = start - 1
            while slash >= position and value[slash] == "\\":
                slash -= 1
            if (start - slash - 1) % 2:
                # An escaped quote in prose/regex is not a JSON string opener.
                position = start + 1
                continue
            if layer == _METADATA_LAYERS:
                raise KeyLeakError("calibration metadata exceeds the decoding depth")
            end = start + 1
            while match := _QUOTED_PART.match(value, end):
                end = match.end()
            yield json.loads('"' + value[start + 1:end] + '"', strict=False)
            if end < len(value) and value[end] != '"':
                raise KeyLeakError("uninspectable quoted calibration metadata")
            position = end + 1

    pending = [(layers(text, 0, quoted), 0)]
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
        if '"' in decoded or _DATA_URI.search(decoded):
            pending.append((layers(decoded, layer + 1, True), layer + 1))


def _metadata(value: Any) -> None:
    """Inspect every key and string in anonymous retained metadata, iteratively."""
    assert_no_key_shaped_json(
        value, where="calibration metadata",
        check_content=lambda body: _metadata_text(body.decode("utf-8")),
    )


# Only schema applicators carry schema semantics into child objects. Unknown
# keywords, annotations and instance data (const/enum/default) remain metadata.
_SCHEMA_MAPS = {"properties", "$defs", "definitions", "dependentSchemas", "dependencies"}
_SCHEMA_ARRAYS = {"allOf", "anyOf", "oneOf", "prefixItems"}
_SCHEMA_SINGLE = {
    "additionalProperties", "unevaluatedProperties", "propertyNames", "contains",
    "additionalItems", "unevaluatedItems", "not", "if", "then", "else", "contentSchema",
}
_RESPONSES_FORMATS = (
    ("result", "raw", "response", "body", "text", "format"),
    ("raw", "response", "body", "text", "format"),
)
_REGEX_LITERAL_ESCAPE = re.compile(r"\\(\\|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2})")


def _regex_text(text: str) -> None:
    _metadata_text(text, quoted=False)
    literal = _REGEX_LITERAL_ESCAPE.sub(
        lambda match: match[0] if match[1] == "\\" else chr(int(match[1][1:], 16)), text,
    )
    # Inspect decoded character escapes without treating regex operators as JSON.
    _metadata_text(literal.encode("utf-8", errors="replace").decode("utf-8"), quoted=False)


def _schema_metadata(value: Any) -> None:
    """Inspect schemas completely; only declared regex strings are literal text.

    Objects are duplicate-preserving pairs from the source JSON parser. Regex
    quotes/backslashes already crossed that JSON layer; interpreting them again
    as JSON metadata changes their meaning. Attachments and literal credentials
    still cross the common inspector, including in patternProperties keys.
    """
    pending = [(value, "schema")]
    while pending:
        node, mode = pending.pop()
        if mode == "array" and isinstance(node, list):
            pending.extend((child, "schema") for child in node)
        elif isinstance(node, tuple):
            for name, child in node:
                if mode == "regexes":
                    _regex_text(name)
                else:
                    _metadata_text(name)
                if mode in ("schemas", "regexes"):
                    pending.append((child, "schema"))
                elif name == "pattern" and isinstance(child, str):
                    _regex_text(child)
                elif name == "patternProperties" and isinstance(child, tuple):
                    pending.append((child, "regexes"))
                elif name in _SCHEMA_MAPS and isinstance(child, tuple):
                    pending.append((child, "schemas"))
                elif name in _SCHEMA_ARRAYS and isinstance(child, list):
                    pending.append((child, "array"))
                elif name in _SCHEMA_SINGLE or name == "items":
                    pending.append((child, "array" if isinstance(child, list)
                                    and name == "items" else "schema"))
                else:
                    _metadata(child)
        else:
            _metadata(node)


def _response_metadata(value: Any) -> None:
    """Recognize only the captured Responses configuration, never arbitrary text."""
    pending: list[tuple[Any, tuple[str, ...]]] = [(value, ())]
    while pending:
        node, path = pending.pop()
        if not isinstance(node, tuple):
            _metadata(node)
            continue
        declared = path in _RESPONSES_FORMATS and [
            child for name, child in node if name == "type"
        ] == ["json_schema"]
        for name, child in node:
            _metadata_text(name)
            child_path = (*path, name)
            if declared and name == "schema":
                _schema_metadata(child)
            elif any(p[:len(child_path)] == child_path for p in _RESPONSES_FORMATS):
                pending.append((child, child_path))
            else:
                _metadata(child)


def _structured_input(body: bytes, *, schema: bool = False) -> None:
    """Scan YAML/JSON scalars without expanding aliases or YAML merge mappings.

    Representation nodes retain duplicate values and share alias nodes. Only
    binary scalars need construction; constructing mappings could amplify a
    small merge graph into unbounded work. Replay still validates the document.
    """
    if schema:
        try:
            value = json.loads(body, object_pairs_hook=tuple)
        except (ValueError, RecursionError):
            pass  # Non-JSON inputs retain the complete YAML scalar inspection.
        else:
            _schema_metadata(value)
            return
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


def _reply(value: Any) -> None:
    """A vote/normalized receipt's structured reply has one additional JSON layer.

    Withheld replies can wrap JSON in fences or prose. Retain duplicate members
    and inspect recoverable strings even when the complete reply is not JSON.
    """
    if not isinstance(value, str):
        return
    try:
        decoded = json.loads(value, object_pairs_hook=list)
    except RecursionError:
        raise BenchmarkValidationError("calibration reply exceeds the inspection limit") from None
    except ValueError:
        if value.lstrip().startswith('"') or "{" in value or "[" in value:
            check_json_text(value, where="calibration reply")
        else:
            # Prose gaps can contain literal Windows paths or escape examples.
            # Decode only quoted reply strings; an ambiguous string tail still
            # crosses the existing fail-closed recovery checker.
            decoder = json.JSONDecoder(strict=False)
            position = 0
            while (start := value.find('"', position)) != -1:
                try:
                    decoded, position = decoder.raw_decode(value, start)
                except ValueError:
                    check_json_text(value[start:], where="calibration reply")
                    break
                assert_no_key_shaped_json(decoded, where="calibration reply")
        return
    assert_no_key_shaped_json(decoded, where="calibration reply")


def _source_json(body: bytes, *, replies: bool) -> None:
    assert_no_key_shaped(body, where="calibration source")
    try:
        # Tuples distinguish objects from JSON arrays and retain every duplicate
        # member, including shadowed non-string replies, until inspection ends.
        value = json.loads(body, object_pairs_hook=tuple)
    except KeyLeakError:
        raise
    except (ValueError, RecursionError):
        raise BenchmarkValidationError("invalid JSON calibration source") from None
    if replies:
        _response_metadata(value)
    else:
        _metadata(value)
    if not isinstance(value, tuple):
        raise BenchmarkValidationError("invalid JSON calibration source")
    if replies:
        pending: list[tuple[Any, bool]] = [(value, True)]
        while pending:
            node, normalized = pending.pop()
            if isinstance(node, tuple):
                for name, child in node:
                    if name in ("text", "output_text"):
                        if isinstance(child, str):
                            _reply(child)
                        elif normalized:
                            _unknown_history(child)
                        # Structured raw text (including Responses text.format)
                        # already crossed _metadata above. Reply recovery belongs
                        # to actual text strings, not every configuration scalar.
                        continue
                    pending.append((child, normalized and name == "result"))
            elif isinstance(node, list):
                pending.extend((child, False) for child in node)


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
        # Serialized objects, binary YAML and escaped quoted strings are opaque
        # outside a declared serialization slot; do not invent a decoder here.
        if not re.search(r"[{}\[\]`]|!!binary|!<|%(?:TAG|YAML)", value):
            position = 0
            while (start := value.find('"', position)) != -1:
                end = value.find('"', start + 1)
                end = len(value) if end == -1 else end
                if value.find("\\", start + 1, end) != -1:
                    break
                position = end + 1
            else:
                _metadata_text(value, quoted=False)
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
            # Explicit non-JSON serialization remains unsupported. Anonymous
            # structured extensions and JSON notes can be inspected normally.
            if isinstance(value, str) and re.search(r"!!binary|!<|%(?:TAG|YAML)", value):
                _unknown_history(value)
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
                _structured_input(body, schema=section == "inputs" and name == "judge_schema")
            elif filename.endswith(".json"):
                _source_json(body, replies=name.startswith(("votes/", "executions/")))
            elif section == "files":
                _unknown_history(text)
            else:
                # Literal prompts/regexes (notably masking.txt) are not JSON.
                # Explicit attachments still cross the common decoded boundary.
                _metadata_text(text, quoted=False)


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
