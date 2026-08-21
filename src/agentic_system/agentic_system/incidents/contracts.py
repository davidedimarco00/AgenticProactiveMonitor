from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class DetectorContextPort(Protocol):
    """Read-only contract for normalized OpenSearch detector metadata."""

    async def get_detector_context(self, detector_id: str) -> dict[str, Any]: ...


class AnomalyInboxPort(Protocol):
    """Durable persistence contract for OpenSearch anomaly observations."""

    async def record_anomaly(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    async def get_anomaly(self, anomaly_key: str) -> dict[str, Any] | None: ...

    async def update_detector_metadata(
        self,
        anomaly_key: str,
        detector_context: dict[str, Any],
    ) -> dict[str, Any] | None: ...

    async def mark_anomaly_processing(self, anomaly_key: str) -> dict[str, Any] | None: ...

    async def mark_anomaly_completed(self, anomaly_key: str) -> dict[str, Any] | None: ...

    async def mark_anomaly_retryable(
        self,
        anomaly_key: str,
        *,
        error: str,
    ) -> dict[str, Any] | None: ...

    async def dismiss_waiting_anomaly(
        self,
        anomaly_key: str,
        *,
        dismissed_by: str = "operator",
        reason: str = "Marked as not a true anomaly by the operator.",
    ) -> dict[str, Any] | None: ...

    async def link_anomaly_to_incident(
        self,
        anomaly_key: str,
        incident_id: str,
    ) -> dict[str, Any] | None: ...

    async def mark_incident_anomalies_processing(self, incident_id: str) -> int: ...

    async def mark_incident_anomalies_completed(self, incident_id: str) -> int: ...


@dataclass(frozen=True, slots=True)
class IncidentAssigneeReceipt:
    incident_id: str
    agent_role: str
    agent_jid: str


@dataclass(frozen=True, slots=True)
class IncidentTriageReceipt:
    incident_id: str
    probable_domain: str
    primary_investigator: str
    confidence: float
    rationale: str
    bdi_goal: str
    bdi_triage_intention: str
    bdi_intention: str


@dataclass(frozen=True, slots=True)
class InvestigationTaskDispatchReceipt:
    task_id: str
    incident_id: str
    agent_role: str
    agent_jid: str
    correlation_id: str
    bdi_goal: str
    bdi_acceptance_intention: str
    bdi_investigation_intention: str


@dataclass(frozen=True, slots=True)
class InvestigationTaskResultReceipt:
    """Structured ReAct outcome delivered by a specialist to the Technical Lead."""

    task_id: str
    incident_id: str
    agent_role: str
    agent_jid: str
    correlation_id: str
    succeeded: bool
    summary: str = ""
    confidence: float = 0.0
    findings: tuple[str, ...] = ()
    evidence: tuple[dict[str, Any], ...] = ()
    hypotheses: tuple[str, ...] = ()
    recommended_next_steps: tuple[str, ...] = ()
    assistance_required: bool = False
    assistance_domain: str | None = None
    react_steps: int = 0
    tools_used: tuple[str, ...] = ()
    conversation_id: str | None = None
    error: str | None = None
    retryable: bool = True

    def task_outcome(self) -> dict[str, Any]:
        return {
            "status": "completed" if self.succeeded else "failed",
            "summary": self.summary,
            "confidence": self.confidence,
            "agent_role": self.agent_role,
            "findings": list(self.findings),
            "evidence": [dict(item) for item in self.evidence],
            "hypotheses": list(self.hypotheses),
            "recommended_next_steps": list(self.recommended_next_steps),
            "assistance_required": self.assistance_required,
            "assistance_domain": self.assistance_domain,
            "react_steps": self.react_steps,
            "tools_used": list(self.tools_used),
            "conversation_id": self.conversation_id,
        }


@dataclass(frozen=True, slots=True)
class TechnicalLeadReviewReceipt:
    """BDI critic decision after reviewing one completed specialist result."""

    incident_id: str
    decision: str
    confidence: float
    diagnosis_summary: str
    root_cause: str
    rationale: str
    remediation_summary: str
    remediation_steps: tuple[str, ...]
    support_domain: str | None
    support_reason: str | None
    bdi_goal: str
    bdi_review_intention: str
    bdi_decision_intention: str


class IncidentAssigneePort(Protocol):
    """Contract for assigning, triaging, dispatching and reviewing persisted work."""

    async def assign_incident(
        self,
        incident: dict[str, Any],
    ) -> IncidentAssigneeReceipt: ...

    async def triage_incident(
        self,
        incident: dict[str, Any],
        *,
        detector_context: dict[str, Any],
    ) -> IncidentTriageReceipt: ...

    async def dispatch_investigation_task(
        self,
        incident: dict[str, Any],
        task: dict[str, Any],
    ) -> InvestigationTaskDispatchReceipt: ...

    async def collect_investigation_result(
        self,
        incident: dict[str, Any],
        task: dict[str, Any],
    ) -> InvestigationTaskResultReceipt | None: ...

    async def review_investigation_result(
        self,
        incident: dict[str, Any],
        result: InvestigationTaskResultReceipt,
    ) -> TechnicalLeadReviewReceipt: ...

    def apply_review_activity(self, incident_id: str, decision: str) -> None: ...


class IncidentRepositoryPort(Protocol):
    """Persistence contract required by the autonomous incident workflow."""

    async def find_active_incident_by_detector(
        self,
        detector_id: str,
    ) -> dict[str, Any] | None:
        ...

    async def list_incidents(
        self,
        *,
        limit: int = 100,
        status: str | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        ...

    async def get_incident(self, incident_id: str) -> dict[str, Any] | None:
        ...

    async def create_incident(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    async def update_incident(
        self,
        incident_id: str,
        patch: dict[str, Any],
    ) -> dict[str, Any] | None:
        ...

    async def add_event(
        self,
        incident_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        ...

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        ...


class AgentTaskRepositoryPort(Protocol):
    """Atomic persistence contract for durable agent work items."""

    async def create_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        ...

    async def list_tasks(
        self,
        *,
        states: list[str] | None = None,
        incident_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        ...

    async def transition_task(
        self,
        task_id: str,
        *,
        expected_states: list[str],
        new_state: str,
        patch: dict[str, Any] | None = None,
        increment_attempt: bool = False,
    ) -> dict[str, Any] | None:
        ...
