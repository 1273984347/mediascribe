"""Read-only local funnel metrics derived from US-013 event rows."""

from __future__ import annotations

import time
from typing import Any, Dict, Iterable, List

METRICS_SCHEMA = "mediascribe.local-event-metrics/v1"
FUNNEL_STAGES = (
    "first_run_started",
    "input_submitted",
    "transcription_finished_success",
    "artifact_opened",
    "recovery_action_selected",
)
_MEASURED_EVENTS = {
    "first_run_started",
    "input_submitted",
    "transcription_finished",
    "artifact_opened",
    "recovery_action_selected",
}


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _valid_success(event: Dict[str, Any]) -> bool:
    properties = event.get("properties") or {}
    return (
        properties.get("result") == "success"
        and properties.get("artifact_exists") is True
        and properties.get("text_nonempty") is True
        and properties.get("metadata_consistent") is True
    )


def _terminal_result(event: Dict[str, Any]) -> Any:
    return (event.get("properties") or {}).get("result")


def _summarize(rows: Iterable[Dict[str, Any]], install_ids: Iterable[str]) -> Dict[str, Any]:
    installs = set(install_ids)
    row_list = list(rows)

    def count(event_name: str, *, predicate=None) -> int:
        matches = set()
        for event in row_list:
            if event.get("install_id") not in installs or event.get("event") != event_name:
                continue
            if predicate is not None and not predicate(event):
                continue
            matches.add(str(event.get("install_id")))
        return len(matches)

    def terminal_count(result: str) -> int:
        return count(
            "transcription_finished",
            predicate=lambda event: _terminal_result(event) == result,
        )

    first_runs = count("first_run_started")
    submissions = count("input_submitted")
    successes = count(
        "transcription_finished",
        predicate=_valid_success,
    )
    opens = count("artifact_opened")
    recovery_selected = count("recovery_action_selected")

    recovered = set()
    for recovery in row_list:
        if recovery.get("event") != "recovery_action_selected":
            continue
        install = recovery.get("install_id")
        if install not in installs:
            continue
        recovery_ts = float(recovery.get("ts") or 0)
        for event in row_list:
            if (
                event.get("install_id") == install
                and event.get("event") == "transcription_finished"
                and _valid_success(event)
                and float(event.get("ts") or 0) > recovery_ts
            ):
                recovered.add(str(install))
                break

    totals = {
        "new_installations": first_runs,
        "submitted_installs": submissions,
        "successful_installs": successes,
        "opened_installs": opens,
        "recovery_selected_installs": recovery_selected,
        "recovered_installs": len(recovered),
        "failed_installs": terminal_count("failed"),
        "cancelled_installs": terminal_count("cancelled"),
        "no_speech_installs": terminal_count("no_speech"),
    }
    rates = {
        "submission_rate": _rate(submissions, first_runs),
        "success_rate": _rate(successes, first_runs),
        "open_rate": _rate(opens, successes),
        "recovery_success_rate": _rate(len(recovered), recovery_selected),
    }
    return {
        **totals,
        "totals": totals,
        "rates": rates,
    }


def calculate_metrics(events: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculate the first-use funnel locally from exported/listed event rows."""
    rows = [
        event
        for event in events
        if event.get("event") in _MEASURED_EVENTS and not event.get("is_internal_test")
    ]
    install_ids = {str(event["install_id"]) for event in rows if event.get("install_id")}
    overall = _summarize(rows, install_ids)

    by_surface: Dict[str, Any] = {}
    surface_groups: Dict[str, List[Dict[str, Any]]] = {}
    for event in rows:
        surface = str(event.get("surface") or "unknown")
        surface_groups.setdefault(surface, []).append(event)
    for surface, grouped_rows in sorted(surface_groups.items()):
        grouped_installs = {
            str(event["install_id"]) for event in grouped_rows if event.get("install_id")
        }
        by_surface[surface] = _summarize(grouped_rows, grouped_installs)

    by_hardware_tier: Dict[str, Any] = {}
    hardware_groups: Dict[str, List[Dict[str, Any]]] = {}
    for event in rows:
        tier = str(event.get("hardware_tier") or "unknown")
        hardware_groups.setdefault(tier, []).append(event)
    for tier, grouped_rows in sorted(hardware_groups.items()):
        grouped_installs = {
            str(event["install_id"]) for event in grouped_rows if event.get("install_id")
        }
        by_hardware_tier[tier] = _summarize(grouped_rows, grouped_installs)

    install_summaries = []
    for install in sorted(install_ids):
        install_rows = [event for event in rows if event.get("install_id") == install]
        summary = _summarize(install_rows, {install})["totals"]
        install_summaries.append({"install_id": install, **summary})

    return {
        "schema": METRICS_SCHEMA,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "funnel": list(FUNNEL_STAGES),
        "totals": overall["totals"],
        "rates": overall["rates"],
        "by_surface": by_surface,
        "by_hardware_tier": by_hardware_tier,
        "installs": install_summaries,
    }
