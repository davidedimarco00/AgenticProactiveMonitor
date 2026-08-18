from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from ...domain.anomalies import AnomalyObservation
from ...domain.incidents import IncidentCorrelationPolicy
from ..ports.incident_repository import IncidentRepositoryPort


LOGGER = logging.getLogger("agentic_system.application.incidents")


def _epoch_ms_to_iso(value: int | None) -> str | None:
    if value is None or value <= 0:
        return None
    return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).isoformat(timespec="seconds")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class IncidentWorkflow:
    """Create or correlate incidents from normalized anomaly observations."""

    def __init__(
        self,
        repository: IncidentRepositoryPort,
        correlation_policy: IncidentCorrelationPolicy,
    ) -> None:
        self.repository = repository
        self.correlation_policy = correlation_policy
        self.created_count = 0
        self.correlated_count = 0
        self.last_incident_id: str | None = None

    async def handle_anomaly(self, observation: AnomalyObservation) -> dict[str, Any]:
        existing = await self.repository.find_active_incident_by_detector(
            observation.detector_id
        )

        if existing is not None and self.correlation_policy.can_correlate(
            existing,
            detector_id=observation.detector_id,
        ):
            incident_id = str(existing["incident_id"])
            updated = await self.repository.update_incident(
                incident_id,
                {
                    "anomaly": {
                        "detector_id": observation.detector_id,
                        "anomaly_type": "opensearch_anomaly",
                        "grade": observation.anomaly_grade,
                        "confidence": observation.confidence,
                    }
                },
            )
            if updated is None:
                raise RuntimeError(f"Active incident disappeared during correlation: {incident_id}")

            await self.repository.add_event(
                incident_id,
                {
                    "event_type": "ANOMALY_REOBSERVED",
                    "action": "correlate_anomaly",
                    "reason": (
                        "A new OpenSearch result from the same SINGLE_ENTITY detector "
                        "was correlated with the active incident."
                    ),
                    "status": updated.get("status", "NEW"),
                },
            )
            self.correlated_count += 1
            self.last_incident_id = incident_id
            LOGGER.info(
                "Correlated anomaly result=%s detector=%s with incident=%s",
                observation.result_id,
                observation.detector_id,
                incident_id,
            )
            return updated

        detected_at = _epoch_ms_to_iso(observation.execution_start_time) or _utc_now_iso()
        incident = await self.repository.create_incident(
            {
                "status": "NEW",
                "severity": "MEDIUM",
                "entity": f"single-entity-detector:{observation.detector_id}",
                "service": "unknown",
                "takeover_reason": "OpenSearch SINGLE_ENTITY detector reported an anomaly.",
                "takeover_factors": [
                    "automatic_opensearch_anomaly",
                    "single_entity_detector",
                ],
                "anomaly": {
                    "detector_id": observation.detector_id,
                    "anomaly_type": "opensearch_anomaly",
                    "grade": observation.anomaly_grade,
                    "confidence": observation.confidence,
                },
                "detected_at": detected_at,
            }
        )
        incident_id = str(incident["incident_id"])
        await self.repository.add_event(
            incident_id,
            {
                "event_type": "ANOMALY_DETECTED",
                "action": "create_incident",
                "reason": "Incident created automatically from an OpenSearch anomaly result.",
                "status": "NEW",
            },
        )

        self.created_count += 1
        self.last_incident_id = incident_id
        LOGGER.warning(
            "Created incident=%s from anomaly result=%s detector=%s",
            incident_id,
            observation.result_id,
            observation.detector_id,
        )
        return incident
