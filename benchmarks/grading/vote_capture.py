"""Per-consumer capture of the bidirectional vote/invocation inventory.

Unsigned manifests establish internal continuity only. No absent vote is
reconstructed from a receipt; receiptless history remains technical history.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

from benchmarks.agentic.inputs import json_mapping
from benchmarks.agentic.protocol import KeyLeakError, assert_no_key_shaped
from benchmarks.report_files import artifact_bytes, artifact_paths
from benchmarks.runner import BenchmarkValidationError

FINAL_WITHHELD = frozenset({"parse_failure", "refusal", "output_cap"})  # never retried (ruling 6)
VoteFile = tuple[Path, dict[str, Any], str]


class VoteInventoryUnavailable(BenchmarkValidationError):
    """An invocation declares evidence that is no longer available."""

    def __init__(self, message: str, missing: set[tuple[str, str | None, str]] | None = None):
        super().__init__(message)
        self.missing = missing or set()


class MissingIssuedVote(VoteInventoryUnavailable):
    """An otherwise reconciled inventory has lost an execution-issued vote."""


def vote_dir(
    root: Path, *, target: str, judge_id: str, effort: str | None, pass_number: int, item_id: str
) -> Path:
    return (
        root / "votes" / target / judge_id / (effort or "default") / f"pass{pass_number}" / item_id
    )


def is_final(record: Mapping[str, Any]) -> bool:
    """A vote, a withheld vote that is never retried, or a terminal record."""
    return (
        record.get("status") == "succeeded"
        or record.get("withheld") in FINAL_WITHHELD
        or bool(record.get("terminal"))
    )


def record_order(path: Path, record: Mapping[str, Any]) -> tuple[bool, str, str, int]:
    when, run, attempt = (
        record.get("graded_at"),
        record.get("grading_run_id"),
        record.get("attempt"),
    )
    if (
        not isinstance(when, str)
        or not isinstance(run, str)
        or type(attempt) is not int
        or attempt < 1
    ):
        raise BenchmarkValidationError("grading vote lacks its selection coordinates")
    if path.name != f"{run}--a{attempt}.json":
        raise BenchmarkValidationError("grading vote differs from its run or attempt coordinate")
    return is_final(record), when, run, attempt


def select_vote_file(records: list[VoteFile]) -> VoteFile | None:
    """Select captured bytes before replaying any candidate's reply receipt.

    Share final-first ordering between scoring and both report passes. The
    unselected records still belong to the version and attempt inventories.
    """
    return max(records, key=lambda pr: record_order(pr[0], pr[1]), default=None)


class VoteCapture:
    """Read each covered vote, invocation and declared receipt once per capture.

    Passes are checked separately so corrupt test–retest history does not erase
    supported pass-1 grades. Inventory work is linear in files and declarations;
    multiple batches, invocations and attempts are independent coordinates.
    """

    def __init__(
        self, root: Path, *, reader: Callable[[Path, Path], bytes] = artifact_bytes,
        paths: Callable[[Path, Path, str], list[Path]] = artifact_paths,
        runs: Mapping[str, dict[str, Any]] | None = None,
    ) -> None:
        self.root, self.reader, self.paths = root, reader, paths
        self.runs = runs if runs is not None else {
            path.stem: json_mapping(reader(root, path), "grading invocation")
            for path in paths(root, root / "runs", "*.json")
        }
        self.versions: dict[Path, str | None] = {}
        self._groups: dict[tuple[str, str, str, int, str], list[VoteFile]] = defaultdict(list)
        self._histories: dict[tuple[str, str], dict[str, Any]] = {}
        self._receipts: dict[str, dict[str, dict[str, Any]]] = {}
        self._missing_receipts: dict[str, set[str]] = defaultdict(set)
        self._receipt_errors: dict[str, BenchmarkValidationError] = {}
        self._checked: set[tuple[str, int]] = set()
        self._unavailable: dict[tuple[str, int], VoteInventoryUnavailable] = {}
        self._items: dict[Path, dict[str, Any]] = {}

    def item(self, path: Path) -> dict[str, Any]:
        if path not in self._items:
            self._items[path] = json_mapping(self.reader(self.root, path), "grading item")
        return self._items[path]

    def _invocation_receipts(
        self, run: str, invocation: Mapping[str, Any],
    ) -> dict[str, dict[str, Any]]:
        if run in self._receipt_errors:
            raise self._receipt_errors[run]
        try:
            return self._read_invocation_receipts(run, invocation)
        except KeyLeakError:
            raise
        except BenchmarkValidationError as error:
            self._receipt_errors[run] = error
            raise

    def _read_invocation_receipts(
        self, run: str, invocation: Mapping[str, Any],
    ) -> dict[str, dict[str, Any]]:
        if run in self._receipts:
            return self._receipts[run]
        manifest = invocation.get("execution_results")
        if (
            not isinstance(manifest, dict)
            or invocation.get("grading_run_id") != run
            or ("pass" in invocation and type(invocation["pass"]) is not int)
            or ("records_written" in invocation and (
                type(invocation["records_written"]) is not int
                or invocation["records_written"] != len(manifest)
            ))
        ):
            raise BenchmarkValidationError("invalid vote inventory receipt manifest")
        histories = {}
        for relative, digest in manifest.items():
            parts = Path(relative).parts if isinstance(relative, str) else ()
            if (len(parts) != 2 or any(p.startswith(".") for p in parts)
                    or Path(relative).as_posix() != relative or Path(relative).is_absolute()):
                raise BenchmarkValidationError("invalid vote inventory receipt path")
            item, separator, attempt = parts[1].removesuffix(".json").rpartition("--a")
            if (not item or not separator or not attempt.isascii() or not attempt.isdigit()
                or attempt.startswith("0") or parts[1] != f"{item}--a{attempt}.json"
            ):
                raise BenchmarkValidationError("invalid vote inventory receipt coordinate")
            try:
                body = self.reader(self.root, self.root / "executions" / run / relative)
            except FileNotFoundError:
                self._missing_receipts[run].add(relative)
                continue
            if hashlib.sha256(body).hexdigest() != digest:
                raise BenchmarkValidationError("vote inventory receipt digest differs")
            histories[relative] = json_mapping(body, "grading execution")
        self._receipts[run] = histories
        return histories

    def check(
        self, target: str, pass_number: int, *, allow_missing_repeats: bool = False,
    ) -> None:
        scope = target, pass_number
        optional = allow_missing_repeats and scope == ("answers", 2)
        if scope in self._unavailable:
            error = self._unavailable[scope]
            if not optional or not isinstance(error, MissingIssuedVote):
                raise error
            return
        if scope in self._checked:
            return
        files = {}
        coordinates = set()
        by_receipt = {}
        missing: set[tuple[str, str | None, str]] = set()
        missing_paths: set[Path] = set()
        missing_reasons: set[str] = set()
        for path in self.paths(
            self.root, self.root / "votes" / target, f"*/*/pass{pass_number}/*/*.json",
        ):
            self.versions[path] = None
            body = self.reader(self.root, path)
            row = json_mapping(body, "grading vote")
            self.versions[path] = row.get("reported_model")
            record_order(path, row)
            judge, effort, _pass, item, _name = path.relative_to(
                self.root / "votes" / target
            ).parts
            expected = {"target": target, "pass": pass_number, "judge_id": judge,
                        "item_id": item, "effort": None if effort == "default" else effort}
            if any(type(row.get(k)) is not type(v) or row[k] != v for k, v in expected.items()):
                assert_no_key_shaped(item, where="grading inventory item")
                raise BenchmarkValidationError(
                    f"vote inventory path differs from record for {item}"
                )
            entry = path, row, hashlib.sha256(body).hexdigest()
            files[path] = entry
            coordinates.add((row.get("grading_run_id"), judge, item, row.get("attempt")))
            by_receipt[row["grading_run_id"], f"{judge}/{item}--a{row['attempt']}.json"] = entry
            self._groups[target, judge, effort, pass_number, item].append(entry)

        covered = set()
        for run, invocation in self.runs.items():
            scoped = "target" in invocation and "pass" in invocation
            if "target" in invocation and invocation["target"] != target:
                continue
            manifest = invocation.get("execution_results")
            if manifest is None:
                continue  # Genuine older history makes no receipt inventory claim.
            if "pass" in invocation and (
                type(invocation["pass"]) is not int or invocation["pass"] not in (1, 2)
            ):
                raise BenchmarkValidationError("invalid vote inventory invocation pass")
            try:
                histories = self._invocation_receipts(run, invocation)
            except KeyLeakError:
                raise
            except BenchmarkValidationError:
                if scoped and invocation["pass"] != pass_number:
                    continue  # Keep a failed pass-2 capture out of pass-1 quantities.
                raise
            for relative in self._missing_receipts[run]:
                entry = by_receipt.get((run, relative))
                if entry is not None:
                    path, row, _digest = entry
                    missing.add((row["judge_id"], row["effort"], row["item_id"]))
                    missing_paths.add(path)
                elif not scoped or invocation["pass"] == pass_number:
                    # Without the receipt, its effort is known only if a vote
                    # survives. Restrict uncertainty to the declared judge.
                    judge, name = relative.split("/")
                    item = name.removesuffix(".json").rpartition("--a")[0]
                    missing.add((judge, None, item))
                else:
                    continue
                missing_reasons.add("missing vote inventory receipt")
            for relative, history in histories.items():
                if (history.get("target"), history.get("pass")) != scope:
                    if scoped and invocation["pass"] == pass_number:
                        raise BenchmarkValidationError("vote inventory receipt scope differs")
                    continue
                judge, item, attempt, effort = (
                    history.get(k) for k in ("judge_id", "item_id", "attempt", "effort")
                )
                if (
                    not all(isinstance(v, str) and v and Path(v).name == v
                            and not v.startswith(".") for v in (judge, item, effort or "default"))
                    or type(attempt) is not int or attempt < 1
                    or relative != f"{judge}/{item}--a{attempt}.json"
                    or history.get("grading_run_id") != run
                    or history.get("target") != target
                    or type(history.get("pass")) is not int or history["pass"] != pass_number
                    or ("pass" in invocation and (
                        type(invocation["pass"]) is not int or invocation["pass"] != pass_number
                    ))
                    or ("target" in invocation and invocation["target"] != target)
                ):
                    raise BenchmarkValidationError("vote inventory receipt identity differs")
                path = vote_dir(
                    self.root, target=target, judge_id=cast(str, judge), effort=effort,
                    pass_number=pass_number, item_id=cast(str, item),
                ) / f"{run}--a{attempt}.json"
                if path not in files:
                    if (run, judge, item, attempt) in coordinates:
                        raise BenchmarkValidationError("vote inventory receipt identity differs")
                    missing.add((cast(str, judge), effort, cast(str, item)))
                    missing_reasons.add("missing execution-issued vote in inventory")
                    continue
                _, row, vote_digest = files[path]
                fields = (
                    "grading_run_id", "unit_id", "item_id", "judge_id", "target", "pass",
                    "attempt", "provider_batch_id", "input_sha256", "model_id", "effort",
                )
                if history.get("vote_sha256") != vote_digest or any(
                    type(history.get(k)) is not type(row.get(k)) or history.get(k) != row.get(k)
                    for k in fields
                ):
                    raise BenchmarkValidationError("vote inventory receipt binding differs")
                covered.add(path)
                self._histories[run, relative] = history

        selected = {
            chosen[0]
            for (t, _judge, _effort, number, _item), group in self._groups.items()
            if (t, number) == scope and (chosen := select_vote_file(group)) is not None
        }
        for path, row, _ in files.values():
            invocation = self.runs.get(row["grading_run_id"], {})
            if path in missing_paths:
                continue  # The missing receipt is already an availability failure.
            # An unscoped empty legacy manifest issued nothing. Only an
            # unselected historical take may lack its receipt; current inventories
            # and every selected take still require the bidirectional join.
            if (invocation.get("execution_results") == {} and path not in selected
                    and "target" not in invocation and "pass" not in invocation
                    and "records_written" not in invocation):
                continue
            if invocation.get("execution_results") is not None and path not in covered:
                raise BenchmarkValidationError("present vote is absent from its inventory")
        if missing:
            missing_vote = missing_reasons == {"missing execution-issued vote in inventory"}
            kind = MissingIssuedVote if missing_vote else VoteInventoryUnavailable
            error = kind("; ".join(sorted(missing_reasons)), missing)
            self._unavailable[scope] = error
            if not optional or not missing_vote:
                raise error
        self._checked.add(scope)

    def records(
        self, *, allow_unavailable: bool = False, allow_missing_repeats: bool = False, **where: Any,
    ) -> list[VoteFile]:
        try:
            self.check(
                where["target"], where["pass_number"], allow_missing_repeats=allow_missing_repeats,
            )
        except VoteInventoryUnavailable:
            if not allow_unavailable:
                raise
        return self._groups.get((
            where["target"], where["judge_id"], where["effort"] or "default",
            where["pass_number"], where["item_id"],
        ), [])

    def files(self, target: str, pass_number: int) -> list[VoteFile]:
        self.check(target, pass_number)
        return [entry for (t, _j, _e, p, _i), group in self._groups.items()
                if (t, p) == (target, pass_number) for entry in group]

    def history(self, row: Mapping[str, Any]) -> dict[str, Any] | None:
        relative = f"{row['judge_id']}/{row['item_id']}--a{row['attempt']}.json"
        return self._histories.get((row["grading_run_id"], relative))
