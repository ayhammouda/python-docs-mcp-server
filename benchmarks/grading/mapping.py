"""Judge votes and the design §9.1 score mapping."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from benchmarks.grading.inputs import MAPPING_TABLE as MAPPING_TABLE
from benchmarks.grading.inputs import VOTE_FIELDS, Weight, judge_schema

Label = Literal["CORRECT", "PARTIAL", "INCORRECT", "NOT_ATTEMPTED"]

_JSON_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "boolean": bool,
    "integer": int,
    "number": (int, float),
    "object": dict,
    "array": list,
}


@dataclass(frozen=True)
class Vote:
    explanation: str
    attempted: bool
    p1_met: bool
    p2_met: bool
    contradicts_key: bool
    wrong_version: bool

    def fields(self) -> dict[str, bool]:
        return {name: getattr(self, name) for name in VOTE_FIELDS}


def _typed(value: Any, json_type: str) -> bool:
    if isinstance(value, bool) and json_type != "boolean":
        return False  # a bool is never an int or a number
    return isinstance(value, _JSON_TYPES.get(json_type, object))


def parse_object(text: str | None, schema: Mapping[str, Any]) -> dict[str, Any] | None:
    """Exactly the schema's keys, each of its JSON type; anything else is ``None``."""
    if text is None:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    properties = schema["properties"]
    if not isinstance(data, dict) or set(data) != set(properties):
        return None
    if all(_typed(data[key], spec.get("type", "")) for key, spec in properties.items()):
        return data
    return None


def parse_vote(text: str | None) -> Vote | None:
    data = parse_object(text, judge_schema())
    return None if data is None else Vote(**data)


def grade(fields: Mapping[str, bool], weights: tuple[Weight, Weight]) -> tuple[float, Label]:
    if not fields["attempted"]:
        return 0.0, "NOT_ATTEMPTED"
    if fields["contradicts_key"] or fields["wrong_version"]:
        return 0.0, "INCORRECT"
    met = (fields["p1_met"], fields["p2_met"])
    if any(weight == "core" and not ok for weight, ok in zip(weights, met, strict=True)):
        return 0.0, "INCORRECT"
    if all(met):
        return 1.0, "CORRECT"
    return 0.5, "PARTIAL"


@dataclass(frozen=True)
class PanelFields:
    fields: dict[str, bool | None]  # None = open: the reference settles it
    split: tuple[str, ...]
    valid_votes: int

    @property
    def needs_reference(self) -> bool:
        return any(value is None for value in self.fields.values())


def panel_fields(votes: Mapping[str, Vote | None]) -> PanelFields:
    """Per-field majority of three votes; two must agree; fewer than two decide nothing."""
    valid = [vote.fields() for vote in votes.values() if vote is not None]
    fields: dict[str, bool | None] = dict.fromkeys(VOTE_FIELDS)
    split: list[str] = []
    if len(valid) >= 2:
        for name in VOTE_FIELDS:
            yes = sum(vote[name] for vote in valid)
            if 2 * yes > len(valid):
                fields[name] = True
            elif 2 * yes < len(valid):
                fields[name] = False
            else:
                split.append(name)
    return PanelFields(fields=fields, split=tuple(split), valid_votes=len(valid))
