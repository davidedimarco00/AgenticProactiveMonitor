from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class IncidentAssignment:
    """Minimal persisted incident view delivered to the Technical Lead agent."""

    incident_id: str
    status: str
    severity: str
    entity: str
    anomaly: dict[str, Any]

    @classmethod
    def from_incident(cls, incident: dict[str, Any]) -> "IncidentAssignment":
        incident_id = str(incident.get("incident_id") or "").strip()
        if not incident_id:
            raise ValueError("Incident assignment requires incident_id")

        anomaly = incident.get("anomaly")
        return cls(
            incident_id=incident_id,
            status=str(incident.get("status") or "NEW").upper(),
            severity=str(incident.get("severity") or "MEDIUM").upper(),
            entity=str(incident.get("entity") or "unknown"),
            anomaly=dict(anomaly) if isinstance(anomaly, dict) else {},
        )
