from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


ACTIVE_INCIDENT_STATUSES = {
    "NEW",
    "TAKEN_IN_CHARGE",
    "UNDER_ANALYSIS",
    "DIAGNOSED",
    "OPERATOR_ACTION_REQUIRED",
}


class IncidentCorrelationPolicy:
    """Decide whether a new anomaly belongs to an existing active incident.

    Project detectors are SINGLE_ENTITY. Therefore detector_id is the stable
    correlation key for the monitored entity at this stage. Human-readable
    entity enrichment can be added later without changing the policy boundary.
    """

    def __init__(self, *, window_seconds: int = 600) -> None:
        if window_seconds <= 0:
            raise ValueError("window_seconds must be greater than zero")
        self.window_seconds = window_seconds

    def can_correlate(
        self,
        incident: dict[str, Any],
        *,
        detector_id: str,
        now: datetime | None = None,
    ) -> bool:
        if str(incident.get("status") or "").upper() not in ACTIVE_INCIDENT_STATUSES:
            return False

        anomaly = incident.get("anomaly") or {}
        if anomaly.get("detector_id") != detector_id:
            return False

        updated_at = incident.get("updated_at") or incident.get("created_at")
        if not updated_at:
            return False

        try:
            updated = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
        except ValueError:
            return False

        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)

        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)

        return current - updated <= timedelta(seconds=self.window_seconds)
