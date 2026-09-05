from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False, sort_keys=True)
        handle.write("\n")


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.casefold()
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).casefold()


def contains_any(text: str, values: list[str]) -> bool:
    return any(value.casefold() in text for value in values)


def contains_all(text: str, values: list[str]) -> bool:
    return all(value.casefold() in text for value in values)


def specialist_outcomes(incident: dict[str, Any]) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []
    for event in incident.get("timeline") or []:
        if str(event.get("event_type") or "").upper() != "SPECIALIST_INVESTIGATION_COMPLETED":
            continue
        outcome = event.get("outcome")
        if isinstance(outcome, dict):
            outcomes.append(outcome)
    return outcomes


def collected_evidence(outcomes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for outcome in outcomes:
        items = [item for item in (outcome.get("evidence") or []) if isinstance(item, dict)]
        items.sort(key=lambda item: int(item.get("step") or 0))
        evidence.extend(items)
    return evidence


def final_diagnostic_text(incident: dict[str, Any], outcomes: list[dict[str, Any]]) -> str:
    parts: list[Any] = []
    diagnosis = incident.get("diagnosis")
    if isinstance(diagnosis, dict):
        parts.extend([diagnosis.get("summary"), diagnosis.get("root_cause")])

    for outcome in outcomes:
        parts.extend(
            [
                outcome.get("summary"),
                outcome.get("root_cause"),
                outcome.get("causal_chain"),
            ]
        )

    return normalize_text(parts)


def evaluate_location(text: str, location: dict[str, Any]) -> tuple[str, str, list[str]]:
    mode = str(location.get("mode") or "single").lower()

    if mode == "path":
        matched_endpoints: list[str] = []
        for endpoint in location.get("endpoints") or []:
            aliases = [str(value) for value in endpoint.get("aliases") or []]
            if contains_any(text, aliases):
                matched_endpoints.append(str(endpoint.get("label") or "unknown"))

        expected_endpoints = len(location.get("endpoints") or [])
        if expected_endpoints > 0 and len(matched_endpoints) == expected_endpoints:
            return "correct", str(location.get("label") or "path"), matched_endpoints
        if matched_endpoints:
            return "partial", str(location.get("label") or "path"), matched_endpoints
        return "incorrect", str(location.get("label") or "path"), []

    aliases = [str(value) for value in location.get("aliases") or []]
    matched = [alias for alias in aliases if alias.casefold() in text]
    if matched:
        return "correct", str(location.get("label") or matched[0]), matched
    return "incorrect", str(location.get("label") or "unknown"), []


def evaluate_fault_type(text: str, fault_type: dict[str, Any]) -> tuple[bool, str, list[str]]:
    aliases = [str(value) for value in fault_type.get("aliases") or []]
    matched = [alias for alias in aliases if alias.casefold() in text]
    return bool(matched), str(fault_type.get("label") or "unknown"), matched


def evidence_rule_matches(item: dict[str, Any], rule: dict[str, Any]) -> bool:
    tool = str(item.get("tool") or "").casefold()
    text = normalize_text(item)

    tools_any = [str(value).casefold() for value in rule.get("tools_any") or []]
    text_any = [str(value) for value in rule.get("text_any") or []]
    text_all = [str(value) for value in rule.get("text_all") or []]

    if tools_any and not any(value in tool for value in tools_any):
        return False
    if text_any and not contains_any(text, text_any):
        return False
    if text_all and not contains_all(text, text_all):
        return False
    return True


def evaluate_evidence(
    evidence: list[dict[str, Any]],
    rules: list[dict[str, Any]],
) -> tuple[int, int, list[dict[str, Any]]]:
    results: list[dict[str, Any]] = []
    for rule in rules:
        matched = any(evidence_rule_matches(item, rule) for item in evidence)
        results.append(
            {
                "id": rule.get("id"),
                "description": rule.get("description"),
                "matched": matched,
            }
        )

    matched_count = sum(1 for item in results if item["matched"])
    return matched_count, len(results), results


def parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def elapsed_seconds(start_value: Any, end_value: Any) -> float | None:
    start = parse_iso(start_value)
    end = parse_iso(end_value)
    if start is None or end is None:
        return None
    return max((end - start).total_seconds(), 0.0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--ground-truth", required=True)
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    ground_truth_path = Path(args.ground_truth).resolve()

    metadata = load_json(run_dir / "metadata.json")
    trigger_path = run_dir / "trigger.json"
    trigger = load_json(trigger_path) if trigger_path.exists() else {"accepted": False}
    incident_path = run_dir / "incident.json"
    incident = load_json(incident_path) if incident_path.exists() else {"found": False}
    ground_truth = load_json(ground_truth_path)

    scenario = str(metadata["scenario"])
    scenario_gt = ground_truth["scenarios"][scenario]

    found_incident = bool(incident.get("found", True)) and bool(incident.get("incident_id"))
    outcomes = specialist_outcomes(incident) if found_incident else []
    evidence = collected_evidence(outcomes)
    diagnostic_text = final_diagnostic_text(incident, outcomes) if found_incident else ""

    location_result, expected_location, location_matches = evaluate_location(
        diagnostic_text,
        scenario_gt["location"],
    )
    fault_type_correct, expected_fault_type, fault_type_matches = evaluate_fault_type(
        diagnostic_text,
        scenario_gt["fault_type"],
    )
    evidence_matched, evidence_expected, evidence_points = evaluate_evidence(
        evidence,
        list(scenario_gt.get("evidence_points") or []),
    )

    diagnosis_status = "missing"
    root_cause = None
    total_react_steps = 0
    for outcome in outcomes:
        total_react_steps += int(outcome.get("react_steps") or 0)
        if outcome.get("diagnosis_status"):
            diagnosis_status = str(outcome.get("diagnosis_status"))
        if outcome.get("root_cause"):
            root_cause = outcome.get("root_cause")

    if found_incident and isinstance(incident.get("diagnosis"), dict):
        root_cause = incident["diagnosis"].get("root_cause") or root_cause

    tool_sequence = [str(item.get("tool") or "") for item in evidence]

    scores = {
        "scenario": scenario,
        "profile": metadata.get("profile"),
        "repetition": metadata.get("repetition"),
        "synthetic_trigger_accepted": bool(trigger.get("accepted", False)),
        "incident_found": found_incident,
        "incident_status": incident.get("status") if found_incident else None,
        "run_outcome": metadata.get("run_outcome"),
        "diagnosis_status": diagnosis_status,
        "root_cause": root_cause,
        "location_result": location_result,
        "location_correct": location_result == "correct",
        "expected_location": expected_location,
        "location_matches": location_matches,
        "fault_type_correct": fault_type_correct,
        "expected_fault_type": expected_fault_type,
        "fault_type_matches": fault_type_matches,
        "evidence_matched": evidence_matched,
        "evidence_expected": evidence_expected,
        "evidence_points": evidence_points,
        "react_steps": total_react_steps,
        "specialist_results": len(outcomes),
        "tool_calls": len(tool_sequence),
        "tool_sequence": tool_sequence,
        "diagnosis_time_seconds": elapsed_seconds(
            metadata.get("synthetic_triggered_at_utc"),
            metadata.get("diagnosis_completed_at_utc"),
        ),
    }

    write_json(run_dir / "scores.json", scores)
    print(json.dumps(scores, ensure_ascii=False))


if __name__ == "__main__":
    main()
