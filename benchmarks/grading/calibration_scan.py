"""Credential inspection of typed calibration history, independent of verdict replay.

Archives are exported whole. Inspect every declared source, including sources for
other compositions and optional tuning history, before selecting any verdict.
Declared source layers, bounded character escapes and explicit text/JSON
data URIs are inspected. Explicit YAML metadata encodings are unsupported.
Ordinary prose stays prose, and recorded paths are never opened.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

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
from benchmarks.text_inspection import inspect_text


def _metadata_text(text: str) -> None:
    inspect_text(text, limit=MAX_FRAME_BYTES, yaml_token_limit=captured_inputs._MAX_YAML_NODES)


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
