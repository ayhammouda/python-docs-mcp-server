"""Captured, scanned input bytes shared by planning and reference evidence readers.

Scan raw bytes and decoded values before any schema validator can quote them. YAML
scalars are checked before construction loses aliases, merges or duplicate values.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from yaml.tokens import ScalarToken

from benchmarks.agentic.protocol import (
    KeyLeakError,
    assert_no_key_shaped,
    assert_no_key_shaped_json,
    check_json_text,
)
from benchmarks.runner import BenchmarkValidationError, _require_yaml_mapping


def read_input_bytes(
    path: Path, name: str, label: str, *, max_bytes: int | None = None
) -> bytes:
    where = f"input {name}"
    assert_no_key_shaped(str(path), where="input path")
    try:
        if max_bytes is None:
            content = path.read_bytes()
        else:
            with path.open("rb") as stream:
                content = stream.read(max_bytes + 1)
    except FileNotFoundError:
        raise BenchmarkValidationError(f"{label} file does not exist: {path}") from None
    except OSError as exc:
        raise BenchmarkValidationError(f"cannot read {label} file {path}: {exc.strerror}") from None
    if max_bytes is not None and len(content) > max_bytes:
        raise BenchmarkValidationError(f"{label} exceeds the artifact size limit")
    assert_no_key_shaped(content, where=where)  # includes comments and discarded values
    return content


@dataclass(frozen=True)
class CapturedInput:
    name: str
    content: bytes
    mapping: dict[str, Any]

    @classmethod
    def read(cls, path: Path, name: str, label: str) -> CapturedInput:
        content = read_input_bytes(path, name, label)
        return cls.from_bytes(content, name, label)

    @classmethod
    def from_bytes(cls, content: bytes, name: str, label: str) -> CapturedInput:
        where = f"input {name}"
        assert_no_key_shaped(content, where=where)
        try:
            text = content.decode("utf-8")  # same encoding as the path-based loaders
            for token in yaml.scan(text, Loader=yaml.SafeLoader):
                if isinstance(token, ScalarToken):
                    # Construction can discard a value under a merge or a duplicate key.
                    assert_no_key_shaped(token.value, where=where)
            data = yaml.safe_load(text)
        except KeyLeakError:
            raise
        except (
            yaml.YAMLError,
            ValueError,
            TypeError,
            LookupError,
            AttributeError,
            OverflowError,
            RecursionError,
        ) as exc:
            # PyYAML messages can quote source, even for constructor errors without a mark.
            mark = getattr(exc, "problem_mark", None)
            location = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
            raise BenchmarkValidationError(f"{label} file is not valid YAML{location}") from None
        assert_no_key_shaped_json(data, where=where)  # e.g. bytes constructed by !!binary
        return cls(name, content, _require_yaml_mapping(data, label))


def json_mapping(content: bytes, label: str) -> dict[str, Any]:
    assert_no_key_shaped(content, where=label)

    def checked_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        # Scan before dict construction discards a duplicate key's earlier value.
        assert_no_key_shaped_json(pairs, where=label)
        return dict(pairs)

    try:
        data = json.loads(content, object_pairs_hook=checked_pairs)
    except KeyLeakError:
        raise
    except (ValueError, TypeError, RecursionError):
        # Malformed structure can hide a decoded string from the object hook.
        check_json_text(content.decode("utf-8", errors="replace"), where=label)
        raise BenchmarkValidationError(f"{label} file is not valid JSON") from None
    assert_no_key_shaped_json(data, where=label)
    return _require_yaml_mapping(data, label)


@dataclass(frozen=True)
class CapturedJSON:
    content: bytes
    mapping: dict[str, Any]

    @classmethod
    def read(cls, path: Path, label: str) -> CapturedJSON:
        content = read_input_bytes(path, label, label)
        return cls(content, json_mapping(content, label))


def read_json(path: Path, label: str) -> dict[str, Any]:
    return CapturedJSON.read(path, label).mapping


def read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    content = read_input_bytes(path, label, label)
    return [json_mapping(line, label) for line in content.splitlines() if line]
