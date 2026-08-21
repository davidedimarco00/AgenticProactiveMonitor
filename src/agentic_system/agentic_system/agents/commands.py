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


@dataclass(frozen=True, slots=True)
class SpecialistTaskAssignment:
    """Durable investigation work delegated by the Technical Lead."""

    task_id: str
    incident_id: str
    task_type: str
    assigned_to: str
    attempt: int
    max_attempts: int
    severity: str
    entity: str
    anomaly: dict[str, Any]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SpecialistTaskAssignment":
        task_id = str(payload.get("task_id") or "").strip()
        incident_id = str(payload.get("incident_id") or "").strip()
        task_type = str(payload.get("task_type") or "").strip().upper()
        assigned_to = str(payload.get("assigned_to") or "").strip().lower()
        if not task_id or not incident_id or not task_type or not assigned_to:
            raise ValueError("Specialist task assignment is missing required identity fields")
        if task_type != "INVESTIGATE_INCIDENT":
            raise ValueError(f"Unsupported specialist task type: {task_type!r}")

        attempt = int(payload.get("attempt") or 0)
        max_attempts = int(payload.get("max_attempts") or 0)
        if attempt <= 0:
            raise ValueError("Dispatched specialist task must have attempt >= 1")
        if max_attempts <= 0:
            raise ValueError("Specialist task max_attempts must be greater than zero")

        anomaly = payload.get("anomaly")
        return cls(
            task_id=task_id,
            incident_id=incident_id,
            task_type=task_type,
            assigned_to=assigned_to,
            attempt=attempt,
            max_attempts=max_attempts,
            severity=str(payload.get("severity") or "MEDIUM").upper(),
            entity=str(payload.get("entity") or "unknown"),
            anomaly=dict(anomaly) if isinstance(anomaly, dict) else {},
        )
