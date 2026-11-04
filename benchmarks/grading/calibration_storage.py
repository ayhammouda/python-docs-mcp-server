"""Bounded, self-contained calibration storage; ordinary frames stay unchanged.

Large source proofs use independently bounded bzip2 members keyed by the digest
of the exact original UTF-8 bytes. Decompression never follows recorded paths,
expands a whole cohort at once, or accepts trailing/concatenated streams.
"""

from __future__ import annotations

import base64
import binascii
import bz2
import hashlib
import json
import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from benchmarks.agentic.protocol import MAX_FRAME_BYTES
from benchmarks.report_files import bounded_artifact_bytes
from benchmarks.runner import BenchmarkValidationError

MAX_CALIBRATION_BYTES = 1024 * 1024 * 1024
MAX_METADATA_BYTES = MAX_FRAME_BYTES
MAX_SOURCE_BYTES = 2 * MAX_CALIBRATION_BYTES
MAX_SOURCE_FILES = 32768
PACK_SOURCE_BYTES = 8 * 1024 * 1024
_STRUCTURE = re.compile(rb"[\[\]{}:,]")


def check_transport_structure(body: bytes) -> None:
    """Bound JSON allocation before parsing a transport larger than a frame.

    Large payloads must consist of bounded string members, not millions of
    tiny arrays/objects/numbers. This lexical preflight does not decode content
    or replace JSON validity and credential inspection.
    """
    if len(body) <= MAX_FRAME_BYTES:
        return
    position = outside = fields = depth = 0
    while position < len(body):
        opening = body.find(b'"', position)
        end = opening if opening != -1 else len(body)
        outside += end - position
        if outside > MAX_METADATA_BYTES:
            raise BenchmarkValidationError("calibration transport structure exceeds the limit")
        for match in _STRUCTURE.finditer(body, position, end):
            fields += 1
            token = body[match.start()]
            depth += (token in (91, 123)) - (token in (93, 125))
            if fields > 1_000_000 or depth > 128:
                raise BenchmarkValidationError("calibration transport structure exceeds the limit")
        if opening == -1:
            return
        fields += 1
        if fields > 1_000_000:
            raise BenchmarkValidationError("calibration transport structure exceeds the limit")
        closing = opening
        while True:
            closing = body.find(b'"', closing + 1)
            if closing == -1:
                raise BenchmarkValidationError("invalid calibration transport structure")
            slash = closing - 1
            while slash > opening and body[slash] == 92:
                slash -= 1
            if (closing - slash - 1) % 2 == 0:
                break
        if closing - opening > 2 * MAX_FRAME_BYTES:
            raise BenchmarkValidationError("calibration transport string exceeds the limit")
        position = closing + 1


def calibration_bytes(root: Path, path: Path) -> bytes:
    """Use confined regular-file reads with a calibration-only aggregate bound."""
    body = bounded_artifact_bytes(root, path, max_bytes=MAX_CALIBRATION_BYTES)
    check_transport_structure(body)
    return body


class PackedFiles(Mapping[str, str]):
    """Lazy exact files: at most one frame is expanded per lookup, without a cache."""

    def __init__(self, files: Any, blobs: Any):
        if not isinstance(files, dict) or not isinstance(blobs, dict):
            raise BenchmarkValidationError("invalid packed calibration source")
        if len(files) > MAX_SOURCE_FILES or len(blobs) > len(files):
            raise BenchmarkValidationError("calibration source exceeds the file limit")
        if any(
            not isinstance(name, str)
            or not name.endswith(".json")
            or Path(name).is_absolute()
            or str(Path(name)) != name
            or any(part.startswith(".") for part in Path(name).parts)
            or name.split("/")[0] not in {"votes", "items", "runs", "executions"}
            for name in files
        ):
            raise BenchmarkValidationError("invalid packed calibration file layout")
        if (
            any(not isinstance(v, str) for v in files.values())
            or set(files.values()) != blobs.keys()
        ):
            raise BenchmarkValidationError("invalid packed calibration file references")
        self.names: dict[str, str] = dict(files)
        self.blobs: dict[str, Any] = {}
        self.size = 0
        for digest, record in blobs.items():
            if (
                len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest)
                or not isinstance(record, dict)
                or record.keys() != {"size", "bz2"}
                or type(record["size"]) is not int
                or not 0 <= record["size"] <= MAX_FRAME_BYTES
                or not isinstance(record["bz2"], str)
                or len(record["bz2"]) > min(2 * MAX_FRAME_BYTES, 2 * record["size"] + 1024)
            ):
                raise BenchmarkValidationError("invalid packed calibration member")
            self.blobs[digest] = dict(record)
        self.size = sum(blobs[digest]["size"] for digest in files.values())
        if self.size > MAX_SOURCE_BYTES:
            raise BenchmarkValidationError("calibration source exceeds the size limit")

    def __len__(self) -> int:
        return len(self.names)

    def __iter__(self) -> Iterator[str]:
        return iter(self.names)

    def __contains__(self, name: object) -> bool:
        return name in self.names

    def __getitem__(self, name: str) -> str:
        digest = self.names[name]
        record = self.blobs[digest]
        try:
            compressed = base64.b64decode(record["bz2"].replace("\n", ""), validate=True)
            decoder = bz2.BZ2Decompressor()
            body = decoder.decompress(compressed, max_length=record["size"] + 1)
            if (
                len(body) != record["size"]
                or not decoder.eof
                or decoder.unused_data
                or hashlib.sha256(body).hexdigest() != digest
            ):
                raise ValueError("size, stream or digest differs")
            return body.decode("utf-8")
        except (ValueError, OSError, EOFError, binascii.Error):
            raise BenchmarkValidationError("invalid packed calibration content") from None


def source_files(proof: Mapping[str, Any]) -> Mapping[str, str]:
    if proof.get("version") == 2 and type(proof.get("version")) is int:
        return PackedFiles(proof.get("files"), proof.get("blobs"))
    if "blobs" in proof:
        raise BenchmarkValidationError("invalid packed calibration source version")
    files = proof.get("files", {})
    if not isinstance(files, dict):
        raise BenchmarkValidationError("invalid calibration source files")
    return files


def pack_files(files: Mapping[str, str]) -> dict[str, Any]:
    blobs: dict[str, Any] = {}
    names = {}
    for name, text in files.items():
        body = text.encode("utf-8")
        digest = hashlib.sha256(body).hexdigest()
        names[name] = digest
        if digest not in blobs:
            encoded = base64.b64encode(bz2.compress(body, compresslevel=3)).decode("ascii")
            # Short lines cannot accidentally form credential-shaped tokens in
            # opaque transport. The typed guard scans the decompressed original.
            blobs[digest] = {
                "size": len(body),
                "bz2": "\n".join(encoded[i : i + 32] for i in range(0, len(encoded), 32)),
            }
    return {"version": 2, "files": names, "blobs": blobs}


def check_calibration_metadata(
    entries: Mapping[str, Any], *, archive_metadata: Mapping[str, Any] | None = None,
) -> None:
    """Only packed file payloads get the larger calibration transport allowance."""
    def source(proof: Any) -> Any:
        if isinstance(proof, dict) and proof.get("version") == 2:
            return {k: v for k, v in proof.items() if k != "blobs"}
        return proof

    def entry(value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        result = {**value}
        if "source" in result:
            result["source"] = source(result["source"])
        if isinstance(result.get("tuning"), list):
            result["tuning"] = [entry(r) for r in result["tuning"]]
        return result

    metadata = {k: entry(v) for k, v in entries.items()}
    if archive_metadata:
        metadata = {"entries": metadata, "archive_metadata": archive_metadata}
    size = 0
    for chunk in json.JSONEncoder(ensure_ascii=True).iterencode(metadata):
        size += len(chunk)
        if size > MAX_METADATA_BYTES:
            raise BenchmarkValidationError("calibration metadata exceeds the artifact size limit")
