"""Credential inspection of typed calibration history, independent of verdict replay.

Archives are exported whole. Inspect every declared source, including sources for
other compositions and optional tuning history, before selecting any verdict.
Only schema-declared serialization layers are decoded; prose stays prose.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

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
                assert_no_key_shaped(node.value, where="calibration source input")
                if node.tag == "tag:yaml.org,2002:binary":
                    assert_no_key_shaped_json(
                        loader.construct_object(node), where="calibration source input"
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
    assert_no_key_shaped_json(value, where="calibration source")
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
                        else:
                            # Raw text.format may be structured. Its strings, including
                            # object names, still cross the same reply boundary.
                            assert_no_key_shaped_json(
                                child, where="calibration reply",
                                check_content=lambda body: _reply(body.decode("utf-8")),
                            )
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
    # Only the declared input/file/reply layers have serialization semantics;
    # unknown members may contain plain historic notes, never opaque payloads.
    for name, value in proof.items():
        assert_no_key_shaped(name, where="calibration source member")
        if name not in ("inputs", "files", "blobs"):
            _unknown_history(value)
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
            assert_no_key_shaped(name, where="calibration source member")
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
                _source_json(body, replies=name.startswith(("votes/", "executions/")))
            elif section == "files":
                _unknown_history(text)


def scan_calibration_sources(data: dict[str, Any]) -> None:
    """Inspect all exported entries, never just the run's selected composition.

    The caller bounds the complete archive before parsing. Each source text is
    visited once in its declared slot, and decoding stops after the reply layer.
    No recorded path is opened and no receipt/provenance decision is made here.
    """
    budget = [0, 0]
    for entry in data["entries"].values():
        if not isinstance(entry, dict):
            continue
        scan_calibration_source(entry.get("source"), budget)
        tuning = entry.get("tuning")
        if isinstance(tuning, list):
            for record in tuning:
                if isinstance(record, dict):
                    scan_calibration_source(record.get("source"), budget)
                else:
                    _unknown_history(record)
        else:
            _unknown_history(tuning)
