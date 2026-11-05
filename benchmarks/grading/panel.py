"""The grading panel: judges, the fallback, the reference and the probe generators."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from benchmarks.agentic.inputs import CapturedInput
from benchmarks.grading.inputs import GRADING_DOCS
from benchmarks.model_matrix import KNOWN_APIS, ModelFamily, model_family_from_raw
from benchmarks.runner import BenchmarkValidationError

PANEL_PATH = GRADING_DOCS / "panel.yml"
PROVIDERS = ("anthropic", "openai", "google")
_ROLES = ("panel", "panel-fallback", "reference", "generator")
_CLIENTS = {"anthropic": "anthropic", "openai": "openai", "google": "google-genai"}
_TOKEN_FIELDS = ("max_input_tokens", "max_output_tokens", "framing_allowance_tokens")

Role = Literal["panel", "panel-fallback", "reference", "generator"]


@dataclass(frozen=True)
class JudgeSpec:
    id: str
    role: Role
    provider: str
    model_id: str
    api: str
    effort_levels: tuple[str, ...]  # ascending; the tuning step walks them
    effort: str | None  # frozen by the tuning step; None until then
    max_input_tokens: int
    max_output_tokens: int
    framing_allowance_tokens: int
    raw: dict[str, Any]  # a scripted judge's "script" is absolute after loading


@dataclass(frozen=True)
class Panel:
    judges: dict[str, JudgeSpec]
    panel: tuple[str, str, str]  # one judge id per provider: anthropic, openai, google
    fallback: dict[str, str]  # panel judge id -> its pre-registered fallback id
    reference: str
    generators: tuple[str, str, str]  # one per provider


def _judge(judge_id: str, entry: Any, base: Path) -> JudgeSpec:
    def refuse(problem: str) -> BenchmarkValidationError:
        return BenchmarkValidationError(f"judge {judge_id!r} {problem}")

    if not isinstance(entry, dict):
        raise refuse("must be a mapping")
    if entry.get("role") not in _ROLES:
        raise refuse(f"has unknown role {entry.get('role')!r}")
    if entry.get("provider") not in PROVIDERS:
        raise refuse(f"has unknown provider {entry.get('provider')!r}")
    if entry.get("api") not in KNOWN_APIS:
        raise refuse(f"has unknown api {entry.get('api')!r}")
    if not isinstance(entry.get("model_id"), str) or not entry["model_id"]:
        raise refuse("needs a model_id")
    levels = entry.get("effort_levels")
    if not isinstance(levels, list) or not all(isinstance(x, str) for x in levels):
        raise refuse("needs an effort_levels list")
    effort = entry.get("effort")
    if effort is not None and effort not in levels:
        raise refuse(f"effort {effort!r} is not in its effort_levels {levels}")
    for field in _TOKEN_FIELDS:
        value = entry.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise refuse(f"must set {field} to a positive integer")
    raw = {**entry, "id": judge_id}
    if entry["api"] == "scripted":
        raw["script"] = str((base / str(entry.get("script", ""))).resolve())
    return JudgeSpec(
        id=judge_id,
        role=entry["role"],
        provider=entry["provider"],
        model_id=entry["model_id"],
        api=entry["api"],
        effort_levels=tuple(levels),
        effort=effort,
        max_input_tokens=entry["max_input_tokens"],
        max_output_tokens=entry["max_output_tokens"],
        framing_allowance_tokens=entry["framing_allowance_tokens"],
        raw=raw,
    )


def _one_per_provider(judges: dict[str, JudgeSpec], role: str) -> tuple[str, str, str]:
    ids: list[str] = []
    for provider in PROVIDERS:
        found = [j.id for j in judges.values() if j.role == role and j.provider == provider]
        if len(found) != 1:
            raise BenchmarkValidationError(
                f"the panel file needs exactly one {role} judge for provider {provider!r}, "
                f"found {found}"
            )
        ids.append(found[0])
    return ids[0], ids[1], ids[2]


def load_panel(path: Path) -> Panel:
    return panel_from_mapping(CapturedInput.read(path, "panel", "grading panel").mapping, path)


def panel_from_mapping(data: dict[str, Any], path: Path) -> Panel:
    entries = data.get("judges")
    if data.get("schema_version") != 1 or not isinstance(entries, dict):
        raise BenchmarkValidationError(f"grading panel {path} needs schema_version: 1 and judges")
    judges = {str(k): _judge(str(k), v, path.parent) for k, v in entries.items()}
    panel = _one_per_provider(judges, "panel")
    generators = _one_per_provider(judges, "generator")
    references = [j.id for j in judges.values() if j.role == "reference"]
    if len(references) != 1:
        raise BenchmarkValidationError(f"the panel file needs one reference judge: {references}")
    fallback: dict[str, str] = {}
    for judge_id in panel:
        target = judges[judge_id].raw.get("fallback")
        if target is None:
            continue
        spare = judges.get(target)
        if spare is None or spare.role != "panel-fallback":
            raise BenchmarkValidationError(
                f"judge {judge_id!r} names fallback {target!r}, which is no panel-fallback judge"
            )
        if spare.provider != judges[judge_id].provider:
            raise BenchmarkValidationError(
                f"judge {judge_id!r} and its fallback {target!r} must share a provider"
            )
        fallback[judge_id] = target
    return Panel(
        judges=judges,
        panel=panel,
        fallback=fallback,
        reference=references[0],
        generators=generators,
    )


def as_model_family(judge: JudgeSpec) -> ModelFamily:
    """The launcher contract for a judge: provider, model id, api and token limits."""
    raw: dict[str, Any] = {
        "id": judge.id,
        "provider": judge.provider,
        "model_id": judge.model_id,
        "api": judge.api,
        "arm": "hosted",
        "client": "scripted" if judge.api == "scripted" else _CLIENTS[judge.provider],
        "token_count_method": "not_applicable",
        "latency_scope": "not_applicable",
        "headline_eligible": False,
        "headline_ineligibility_reason": "a judge, not an evaluated model",
        "max_input_tokens": judge.max_input_tokens,
        "max_output_tokens": judge.max_output_tokens,
        "framing_allowance_tokens": judge.framing_allowance_tokens,
        "output_cap_includes_reasoning": True,
    }
    if "script" in judge.raw:
        raw["script"] = judge.raw["script"]
    return model_family_from_raw(raw, label=f"judge {judge.id!r}")


def composition_key(judge_ids: Sequence[str]) -> str:
    """The judges that graded together, in provider order (the caller's order)."""
    return "+".join(judge_ids)
