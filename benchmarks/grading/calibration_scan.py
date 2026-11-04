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

    Non-JSON replies are legitimate withheld history. Retain duplicate members
    during this scan, just as the normal vote producer does.
    """
    if not isinstance(value, str):
        return
    try:
        decoded = json.loads(value, object_pairs_hook=list)
    except RecursionError:
        raise BenchmarkValidationError("calibration reply exceeds the inspection limit") from None
    except ValueError:
        if value.lstrip().startswith(("{", "[", '"')):
            check_json_text(value, where="calibration reply")
        return
    assert_no_key_shaped_json(decoded, where="calibration reply")


def _source_json(body: bytes, *, replies: bool) -> None:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        # Inspect before construction drops a shadowed reply or metadata field.
        assert_no_key_shaped_json(values, where="calibration source")
        if replies:
            for name, value in values:
                # Votes, normalized receipts and original provider response
                # blocks store structured replies in these declared text slots.
                if name in ("text", "output_text"):
                    _reply(value)
        return dict(values)

    assert_no_key_shaped(body, where="calibration source")
    try:
        value = json.loads(body, object_pairs_hook=pairs)
    except KeyLeakError:
        raise
    except (ValueError, RecursionError):
        raise BenchmarkValidationError("invalid JSON calibration source") from None
    assert_no_key_shaped_json(value, where="calibration source")
    if not isinstance(value, dict):
        raise BenchmarkValidationError("invalid JSON calibration source")


def _unknown_history(value: Any) -> None:
    """Allow absent legacy sections and plain notes; refuse opaque serialized data."""
    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, (dict, list)) and not value:
        return
    if isinstance(value, str) and len(value) <= MAX_FRAME_BYTES and re.fullmatch(
        r"[\w .,():/-]+", value
    ):
        assert_no_key_shaped(value, where="calibration history note")
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
            if not isinstance(text, str):
                _unknown_history(text)
                continue
            body = text.encode("utf-8")
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
