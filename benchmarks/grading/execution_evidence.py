"""Consistency of exported grading execution receipts, without running a grader.

These are local, unsigned records. A matching receipt establishes provenance
within the bundle, not independent proof that a provider was contacted.
"""

from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
from typing import Any

from benchmarks.agentic.clients import _LIVE_APIS
from benchmarks.agentic.reference import nonnegative_decimal
from benchmarks.grading.aggregate import withheld_reason
from benchmarks.grading.batch import anthropic_result, gemini_result, openai_result
from benchmarks.grading.inputs import EXPLANATION_FIELD, VOTE_FIELDS


def grading_execution_matches(
    vote: dict[str, Any],
    digest: str,
    judge: dict[str, Any],
    invocation: dict[str, Any],
    events: list[dict[str, Any]],
    history: dict[str, Any],
) -> bool:
    """Join one selected vote to its host receipt and uniquely settled item.

    The caller captures/scans files and verifies the receipt hash from the run
    manifest. No report, worker, network, repository or filesystem dependency.
    """
    run, unit, item = vote["grading_run_id"], vote["unit_id"], vote["item_id"]
    if (
        _LIVE_APIS.get(judge["api"]) != judge["provider"]
        or invocation.get("status") != "completed"
        or invocation.get("dry_run") is not False
        or invocation.get("not_run") != []
        or invocation.get("grading_run_id") != run
        or type(invocation.get("pass")) is not int
        or invocation["pass"] != 1
        or type(vote.get("pass")) is not int
        or vote["pass"] != 1
        or type(vote.get("terminal")) is not bool
        or not isinstance(vote.get("provider_batch_id"), str)
        or not vote["provider_batch_id"].strip()
        or vote["provider_batch_id"].lower().startswith("scripted")
    ):
        return False
    expected = {
        "grading_run_id": run,
        "unit_id": unit,
        "item_id": item,
        "judge_id": vote["judge_id"],
        "target": vote["target"],
        "pass": vote["pass"],
        "attempt": vote["attempt"],
        "provider_batch_id": vote["provider_batch_id"],
        "input_sha256": vote["input_sha256"],
        "vote_sha256": digest,
        "provider": judge["provider"],
        "api": judge["api"],
        "model_id": judge["model_id"],
        "effort": vote["effort"],
    }
    if any(type(history.get(k)) is not type(v) or history.get(k) != v for k, v in expected.items()):
        return False
    raw = history.get("result", {}).get("raw")
    if not isinstance(raw, dict) or "scripted_rule" in raw:
        return False
    normalize = {"anthropic": anthropic_result, "openai": openai_result, "google": gemini_result}
    result = normalize[judge["provider"]](raw)
    # Registered judge vote shape, expressed from the shared field names. The
    # caller separately proves the grade against the captured frozen schema;
    # receipt checking must not reopen repository inputs after an export moves.
    schema = {"properties": {
        EXPLANATION_FIELD: {"type": "string"},
        **{field: {"type": "boolean"} for field in VOTE_FIELDS},
    }}
    withheld = withheld_reason(result, schema)
    if (
        result.item_id != item
        or vote.get("withheld") != withheld
        or (withheld is not None and vote.get("vote") is not None)
        or (result.status != "succeeded" and vote["terminal"] is not True)
        or (
            result.status == "succeeded" and (
                not isinstance(result.reported_model, str)
                or not result.reported_model.strip()
                or result.reported_model.lower().startswith("scripted")
            )
        )
    ):
        return False
    # Match the host's stricter free-error admission, not the normalizer's
    # compatibility fallback for unknown Anthropic result kinds.
    if judge["provider"] == "anthropic" and raw.get("result", {}).get("type") != result.status:
        return False
    if judge["provider"] == "openai" and (result.unbilled or result.status == "expired"):
        response = raw.get("response")
        if response is not None:
            code = response.get("status_code")
            if type(code) is not int or not 400 <= code < 500:
                return False
    normalized = asdict(result)
    if history["result"] != normalized or any(
        vote.get(k) != normalized[k]
        for k in ("status", "text", "stop_reason", "raw_usage", "buckets", "reported_model")
    ):
        return False

    # The normalizers accept int-coercible usage for SDK compatibility; evidence
    # requires actual nonnegative integer measurements, including nested details.
    def counts(value: Any) -> bool:
        if isinstance(value, dict):
            return all(
                counts(v)
                for k, v in value.items()
                if v is not None
                and (isinstance(v, dict) or k.endswith(("tokens", "token_count", "TokenCount")))
            )
        return type(value) is int and value >= 0

    usage = result.raw_usage
    if usage is not None and (not isinstance(usage, dict) or not counts(usage)):
        return False
    if result.buckets is not None:
        if judge["provider"] in ("anthropic", "openai") and (
            not isinstance(usage, dict)
            or any(type(usage.get(k)) is not int for k in ("input_tokens", "output_tokens"))
        ):
            return False
        if any(type(n) is not int or n < 0 for n in asdict(result.buckets).values()):
            return False
    # A successful usable vote must retain measured, nonzero paid usage. A
    # withheld result may instead have documented free or no-usage settlement.
    if withheld is None and (
        result.unbilled or result.buckets is None or result.buckets.is_empty
    ):
        return False
    reserves, settlements = {}, {}
    for event in events:
        kind = event.get("event")
        if kind not in ("reserve", "settle"):
            continue
        target = reserves if kind == "reserve" else settlements
        key = event["unit_id"]
        if key in target or (kind == "settle" and key not in reserves):
            return False
        if kind == "settle" and any(
            event.get(field) != reserves[key].get(field) for field in ("run_id", "provider")
        ):
            return False
        target[key] = event
    reservation, settlement = reserves[unit], settlements[unit]
    if (
        reservation.get("kind") != "batch"
        or reservation.get("job_id") != f"{vote['judge_id']}--grading"
        or reservation.get("model_id") != judge["model_id"]
        or reservation.get("api", judge["api"]) != judge["api"]
        or any(
            e.get("run_id") != run or e.get("provider") != judge["provider"]
            for e in (reservation, settlement)
        )
        or settlement.get("outcome") != "batch"
    ):
        return False
    units = [u for u, e in reserves.items() if e.get("run_id") == run]
    if sorted(invocation["units"]) != sorted(units) or any(u not in settlements for u in units):
        return False
    if (
        invocation["spent_before"].keys() != invocation["spent_after"].keys()
        or {reserves[u]["provider"] for u in units} - invocation["spent_before"].keys()
    ):
        return False
    for provider, before in invocation["spent_before"].items():
        booked = sum(
            (
                nonnegative_decimal(settlements[u]["booked"])
                for u in units
                if reserves[u]["provider"] == provider
            ),
            Decimal(0),
        )
        if (
            nonnegative_decimal(invocation["spent_after"][provider]) - nonnegative_decimal(before)
            != booked
        ):
            return False
    items = settlement["items"]
    if len({i["item_id"] for i in items}) != len(items):
        return False
    if (
        any(i["status"] not in ("ok", "no_usage", "unbilled") for i in items)
        or sum((nonnegative_decimal(i["booked"]) for i in items), Decimal(0))
        != nonnegative_decimal(settlement["booked"])
        or sum((nonnegative_decimal(i["booked"]) for i in items if i["status"] == "ok"), Decimal(0))
        != nonnegative_decimal(settlement["actual"])
    ):
        return False
    if any(
        (i["status"] == "unbilled" and nonnegative_decimal(i["booked"]) != 0)
        or (i["status"] == "no_usage" and nonnegative_decimal(i["booked"]) <= 0)
        for i in items
    ):
        return False
    # The compact ledger has no per-item bounds. Its aggregate bound still
    # limits conservative bookings; when every item lacks usage it is exact.
    no_usage = [i for i in items if i["status"] == "no_usage"]
    conservative = sum((nonnegative_decimal(i["booked"]) for i in no_usage), Decimal(0))
    bound = nonnegative_decimal(reservation["bound"])
    if conservative > 2 * bound or (len(no_usage) == len(items) and conservative != 2 * bound):
        return False
    matched = [i for i in items if i["item_id"] == item]
    if len(matched) != 1:
        return False
    row = matched[0]
    amount = nonnegative_decimal(row["booked"])
    if result.unbilled:
        return withheld is not None and row["status"] == "unbilled" and amount == 0
    # The host books zero-token successes conservatively. Only an unsuccessful
    # result with explicit zero usage gets its reported_zero, actual=0 treatment.
    if result.buckets is None or (result.buckets.is_empty and result.status == "succeeded"):
        return withheld is not None and row["status"] == "no_usage" and amount > 0
    return row["status"] == "ok" and (
        amount == 0 if result.buckets.is_empty else amount > 0
    )
