from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from .anomalies import AnomalyObservation
from .contracts import IncidentRepositoryPort, IncidentTriageReceipt
from .models import IncidentWorkflowResult
from .policies import IncidentCorrelationPolicy


LOGGER = logging.getLogger("agentic_system.incidents.workflow")


def _epoch_ms_to_iso(value: int | None) -> str | None:
    if value is None or value <= 0:
        return None
    return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).isoformat(timespec="seconds")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _detector_display_name(
    detector_context: dict[str, Any] | None,
    detector_id: str,
    observation_name: str | None = None,
) -> str:
    name = str((detector_context or {}).get("name") or "").strip()
    if name and name.lower() != "unknown":
        return name
    carried_name = str(observation_name or "").strip()
    if carried_name:
        return carried_name
    return f"single-entity-detector:{detector_id}"


def _anomaly_payload(
    observation: AnomalyObservation,
    *,
    detector_name: str,
    detector_context: dict[str, Any] | None,
) -> dict[str, Any]:
    """Persist detector semantics needed to keep later diagnosis on the right signal."""

    context = dict(detector_context or {})
    description = str(
        context.get("description") or observation.detector_description or ""
    ).strip()
    raw_indices = context.get("indices") or observation.detector_indices or []
    indices = [str(index) for index in raw_indices] if isinstance(raw_indices, (list, tuple)) else []

    return {
        "detector_id": observation.detector_id,
        "detector_name": detector_name,
        "detector_type": str(context.get("detector_type") or "SINGLE_ENTITY"),
        "detector_description": description,
        "detector_indices": indices,
        "time_field": context.get("time_field"),
        "measurement_name": context.get("measurement_name"),
        "feature_name": context.get("feature_name"),
        "feature_field": context.get("feature_field"),
        "anomaly_type": (
            "opensearch_anomaly"
            if observation.source == "opensearch"
            else "synthetic_test_anomaly"
        ),
        "grade": observation.anomaly_grade,
        "confidence": observation.confidence,
        "anomaly_score": observation.anomaly_score,
        "data_start_time": observation.data_start_time,
        "data_end_time": observation.data_end_time,
        "execution_start_time": observation.execution_start_time,
        "execution_end_time": observation.execution_end_time,
        "source": observation.source,
    }


class IncidentWorkflow:
    """Create, correlate and update the lifecycle of persisted incidents."""

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

    async def handle_anomaly(
        self,
        observation: AnomalyObservation,
        *,
        detector_context: dict[str, Any] | None = None,
    ) -> IncidentWorkflowResult:
        detector_name = _detector_display_name(
            detector_context,
            observation.detector_id,
            observation.detector_name,
        )
        anomaly_payload = _anomaly_payload(
            observation,
            detector_name=detector_name,
            detector_context=detector_context,
        )
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
                    "entity": detector_name,
                    "anomaly": anomaly_payload,
                },
            )
            if updated is None:
                raise RuntimeError(
                    f"Active incident disappeared during correlation: {incident_id}"
                )

            await self.repository.add_event(
                incident_id,
                {
                    "event_type": "ANOMALY_REOBSERVED",
                    "action": "correlate_anomaly",
                    "reason": (
                        "A new OpenSearch result from the same SINGLE_ENTITY detector "
                        "was correlated with the active incident."
                        if observation.source == "opensearch"
                        else "A synthetic test result from the same SINGLE_ENTITY detector was correlated with the active incident."
                    ),
                    "status": updated.get("status", "NEW"),
                },
            )
            self.correlated_count += 1
            self.last_incident_id = incident_id
            LOGGER.info(
                "Correlated anomaly result=%s detector=%s (%s) with incident=%s",
                observation.result_id,
                detector_name,
                observation.detector_id,
                incident_id,
            )
            return IncidentWorkflowResult(
                incident=updated,
                created=False,
                correlated=True,
            )

        detected_at = _epoch_ms_to_iso(observation.execution_start_time) or _utc_now_iso()
        is_opensearch = observation.source == "opensearch"
        takeover_reason = (
            "OpenSearch SINGLE_ENTITY detector reported an anomaly."
            if is_opensearch
            else "Synthetic test SINGLE_ENTITY detector observation reported an anomaly."
        )
        event_reason = (
            "Incident created automatically from an OpenSearch anomaly result."
            if is_opensearch
            else "Incident created automatically from a synthetic test anomaly observation."
        )
        incident = await self.repository.create_incident(
            {
                "status": "NEW",
                "severity": "MEDIUM",
                "entity": detector_name,
                "service": "unknown",
                "takeover_reason": takeover_reason,
                "takeover_factors": [
                    "automatic_opensearch_anomaly" if is_opensearch else "synthetic_test_anomaly",
                    "single_entity_detector",
                ],
                "anomaly": anomaly_payload,
                "detected_at": detected_at,
            }
        )
        incident_id = str(incident["incident_id"])
        await self.repository.add_event(
            incident_id,
            {
                "event_type": "ANOMALY_DETECTED",
                "action": "create_incident",
                "reason": event_reason,
                "status": "NEW",
            },
        )

        self.created_count += 1
        self.last_incident_id = incident_id
        LOGGER.warning(
            "Created incident=%s from anomaly result=%s detector=%s (%s) source=%s",
            incident_id,
            observation.result_id,
            detector_name,
            observation.detector_id,
            observation.source,
        )
        return IncidentWorkflowResult(
            incident=incident,
            created=True,
            correlated=False,
        )

    async def mark_taken_in_charge(
        self,
        incident_id: str,
        *,
        agent_role: str,
        agent_jid: str,
    ) -> dict[str, object]:
        updated = await self.repository.update_incident(
            incident_id,
            {
                "status": "TAKEN_IN_CHARGE",
                "agentic": {
                    "current_agent": agent_role,
                    "active_agents": [agent_role],
                },
            },
        )
        if updated is None:
            raise RuntimeError(
                f"Incident disappeared before takeover could be persisted: {incident_id}"
            )

        event = await self.repository.add_event(
            incident_id,
            {
                "event_type": "INCIDENT_TAKEN_IN_CHARGE",
                "agent_role": agent_role,
                "agent_jid": agent_jid,
                "action": "take_incident",
                "reason": "Technical Lead accepted the persisted incident for investigation.",
                "status": "TAKEN_IN_CHARGE",
            },
        )
        if event is None:
            raise RuntimeError(
                f"Could not persist takeover event for incident: {incident_id}"
            )

        LOGGER.warning(
            "Incident=%s taken in charge by %s [%s]",
            incident_id,
            agent_role,
            agent_jid,
        )
        return updated

    async def mark_triaged(
        self,
        receipt: IncidentTriageReceipt,
        *,
        agent_role: str,
        agent_jid: str,
    ) -> dict[str, object]:
        updated = await self.repository.update_incident(
            receipt.incident_id,
            {
                "status": "TRIAGED",
                "agentic": {
                    "current_agent": agent_role,
                    "active_agents": [agent_role],
                    "primary_investigator": receipt.primary_investigator,
                    "triage_domain": receipt.probable_domain,
                    "triage_confidence": receipt.confidence,
                    "triage_rationale": receipt.rationale,
                    "bdi_goal": receipt.bdi_goal,
                    "bdi_triage_intention": receipt.bdi_triage_intention,
                    "bdi_intention": receipt.bdi_intention,
                },
            },
        )
        if updated is None:
            raise RuntimeError(
                f"Incident disappeared before triage could be persisted: {receipt.incident_id}"
            )

        event = await self.repository.add_event(
            receipt.incident_id,
            {
                "event_type": "INCIDENT_TRIAGED",
                "agent_role": agent_role,
                "agent_jid": agent_jid,
                "action": "select_primary_investigator",
                "reason": receipt.rationale,
                "status": "TRIAGED",
                "outcome": receipt.primary_investigator,
            },
        )
        if event is None:
            raise RuntimeError(
                f"Could not persist triage event for incident: {receipt.incident_id}"
            )

        LOGGER.warning(
            "Incident=%s triaged: domain=%s primary=%s confidence=%.3f BDI=%s/%s->%s",
            receipt.incident_id,
            receipt.probable_domain,
            receipt.primary_investigator,
            receipt.confidence,
            receipt.bdi_goal,
            receipt.bdi_triage_intention,
            receipt.bdi_intention,
        )
        return updated

    async def mark_investigation_task_created(
        self,
        incident_id: str,
        *,
        task_id: str,
        primary_investigator: str,
    ) -> dict[str, object]:
        """Expose only the durable task reference in the incident document.

        Task state is intentionally not duplicated in the incident. The
        `agent_tasks` collection remains the single source of truth for the
        mutable task state machine, avoiding stale denormalized state.
        """

        updated = await self.repository.update_incident(
            incident_id,
            {
                "agentic": {
                    "investigation_task_id": task_id,
                    "primary_investigator": primary_investigator,
                }
            },
        )
        if updated is None:
            raise RuntimeError(
                f"Incident disappeared before its task could be linked: {incident_id}"
            )

        event = await self.repository.add_event(
            incident_id,
            {
                "event_type": "INVESTIGATION_TASK_CREATED",
                "agent_role": "technical_lead",
                "action": "create_durable_agent_task",
                "reason": (
                    "A persistent investigation task was created for the selected "
                    "primary investigator."
                ),
                "status": updated.get("status", "TRIAGED"),
                "outcome": primary_investigator,
                "task_id": task_id,
            },
        )
        if event is None:
            raise RuntimeError(
                f"Could not persist task creation event for incident: {incident_id}"
            )
        return updated

    async def mark_operator_action_required(
        self,
        incident_id: str,
        *,
        task_id: str,
        reason: str,
    ) -> dict[str, Any]:
        """Close autonomous ownership after a terminal task failure.

        The incident remains open for the human operator, while the global
        anomaly FIFO is allowed to continue with the next work item. Agent health
        is intentionally unchanged because the failure belongs to the task.
        """

        updated = await self.repository.update_incident(
            incident_id,
            {
                "status": "OPERATOR_ACTION_REQUIRED",
                "agentic": {
                    "current_agent": "technical_lead",
                    "active_agents": [],
                },
            },
        )
        if updated is None:
            raise RuntimeError(
                f"Incident disappeared before operator escalation: {incident_id}"
            )

        event = await self.repository.add_event(
            incident_id,
            {
                "event_type": "AGENT_TASK_FAILED",
                "agent_role": "technical_lead",
                "action": "escalate_to_operator",
                "reason": reason,
                "status": "OPERATOR_ACTION_REQUIRED",
                "task_id": task_id,
                "outcome": "Autonomous investigation stopped after terminal task failure.",
            },
        )
        if event is None:
            raise RuntimeError(
                f"Could not persist operator escalation event for incident: {incident_id}"
            )
        return updated
