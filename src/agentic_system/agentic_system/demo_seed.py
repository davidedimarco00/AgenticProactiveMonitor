from __future__ import annotations

import asyncio
import json
import os
import sys
from copy import deepcopy
from typing import Any

from .integrations import IncidentRepository


ROLE_BY_LEGACY_JID = {
    "coordinator@xmpp": ("technical_lead", "technical-lead@xmpp"),
    "evidence@xmpp": ("system_engineer", "system-engineer@xmpp"),
    "critic@xmpp": ("network_engineer", "network-engineer@xmpp"),
    "reasoning@xmpp": ("application_engineer", "application-engineer@xmpp"),
    "remediation@xmpp": ("software_developer", "software-developer@xmpp"),
}


def _prepare_demo(raw: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    incident = deepcopy(raw)
    events = incident.pop("agent_events", []) or []
    incident.pop("timeline", None)
    incident.pop("host_id", None)

    anomaly = incident.setdefault("anomaly", {})
    anomaly["anomaly_type"] = anomaly.get("anomaly_type") or "cpu_saturation"
    anomaly.pop("metric", None)
    anomaly.pop("observed_value", None)
    anomaly.pop("baseline_value", None)

    diagnosis = incident.setdefault("diagnosis", {})
    diagnosis.setdefault(
        "root_cause",
        "Abnormal processing workload causing sustained CPU saturation in processing-service.",
    )
    evidence = diagnosis.get("evidence")
    if isinstance(evidence, list):
        diagnosis["evidence"] = [
            (
                "System Engineer confirmed sustained CPU saturation while memory and host pressure remained normal."
                if isinstance(item, str) and "392%" in item
                else item
            )
            for item in evidence
        ]

    incident.setdefault(
        "validation",
        {
            "status": "NOT_EXECUTED",
            "summary": "The mock incident contains advisory remediation only; no corrective action was executed.",
        },
    )

    agentic = incident.setdefault("agentic", {})
    current_agent = str(agentic.get("current_agent") or "")
    if current_agent in ROLE_BY_LEGACY_JID:
        role, jid = ROLE_BY_LEGACY_JID[current_agent]
        agentic["current_agent"] = role
        agentic["current_agent_jid"] = jid

    prepared_events: list[dict[str, Any]] = []
    for raw_event in events:
        event = deepcopy(raw_event)
        event.pop("agent_name", None)
        legacy_jid = str(event.get("agent_jid") or "")
        role_mapping = ROLE_BY_LEGACY_JID.get(legacy_jid)
        if role_mapping:
            event["agent_role"], event["agent_jid"] = role_mapping

        called_by = str(event.get("called_by") or "")
        if called_by in ROLE_BY_LEGACY_JID:
            _, canonical_jid = ROLE_BY_LEGACY_JID[called_by]
            event["called_by"] = canonical_jid

        # Keep only concise operational conclusions. Raw metric values remain in OpenSearch.
        outcome = str(event.get("outcome") or "")
        if "392%" in outcome:
            event["outcome"] = "CPU saturation confirmed on processing-service; container remained available."

        prepared_events.append(event)

    return incident, prepared_events


async def seed_demo(raw: dict[str, Any]) -> str:
    uri = os.getenv(
        "MONGODB_URI",
        "mongodb://agentic:change-this-local-password@mongodb:27017/agentic_monitor?authSource=admin",
    )
    database = os.getenv("MONGODB_DATABASE", "agentic_monitor")
    repository = IncidentRepository(uri, database)
    await repository.connect()

    try:
        incident, events = _prepare_demo(raw)
        incident_id = str(incident.get("incident_id") or "DEMO-CPU-001")

        # Idempotent demo loading: rerunning the seed refreshes the same mock incident.
        await repository.events.delete_many({"incident_id": incident_id})
        await repository.incidents.delete_many({"incident_id": incident_id})

        created = await repository.create_incident(incident)
        for event in events:
            await repository.add_event(incident_id, event)

        return str(created["incident_id"])
    finally:
        await repository.close()


def main() -> None:
    try:
        raw = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid demo incident JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise SystemExit("Demo incident JSON root must be an object")

    incident_id = asyncio.run(seed_demo(raw))
    print(f"Demo incident loaded into MongoDB: {incident_id}")


if __name__ == "__main__":
    main()
