from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ..incidents import AnomalyObservation
from ..incidents.tasks import AgentTaskState, AgentTaskWorkflow, normalize_task_state
from ..integrations import IncidentRepository
from ..runtime import AgentRuntime


TEST_RESULT_INDEX = "agentic-test-anomaly-results"


class TestAnomalyRequest(BaseModel):
    detector_id: str = Field(default="test-single-entity-detector", min_length=1)
    detector_name: str = Field(default="TEST-CPU-processing-service", min_length=1)
    detector_description: str = "Synthetic SINGLE_ENTITY anomaly injected for agentic workflow testing."
    anomaly_grade: float = Field(default=1.0, gt=0.0)
    confidence: float = Field(default=0.95, ge=0.0, le=1.0)
    anomaly_score: float | None = 7.0
    result_id: str | None = None


class TestCompletionRequest(BaseModel):
    summary: str = "Synthetic Technical Lead acceptance used to release the test workflow."


def attach_test_support_api(
    app: FastAPI,
    *,
    runtime: AgentRuntime,
    repository: IncidentRepository,
    task_workflow: AgentTaskWorkflow,
) -> None:
    """Attach explicit development-only hooks for fast workflow testing.

    These endpoints are mounted only when ENABLE_TEST_ANOMALY_INJECTION=1. They
    bypass OpenSearch detection latency but do not bypass MongoDB persistence,
    global FIFO admission, incident creation, Technical Lead BDI, XMPP dispatch,
    specialist BDI or ReAct execution. The completion hook only substitutes the
    future Technical Lead critic/terminal decision that follows a specialist result.
    """

    @app.post("/internal/v1/test/anomalies", tags=["Testing"])
    async def inject_test_anomaly(payload: TestAnomalyRequest) -> dict[str, Any]:
        now_ms = int(time.time() * 1000)
        result_id = (payload.result_id or f"mock-{uuid.uuid4().hex}").strip()
        if not result_id:
            raise HTTPException(status_code=400, detail="result_id cannot be empty")

        observation = AnomalyObservation(
            result_id=result_id,
            result_index=TEST_RESULT_INDEX,
            detector_id=payload.detector_id.strip(),
            detector_name=payload.detector_name.strip(),
            detector_description=payload.detector_description.strip() or None,
            detector_indices=(),
            anomaly_grade=payload.anomaly_grade,
            confidence=payload.confidence,
            anomaly_score=payload.anomaly_score,
            data_start_time=now_ms - 60_000,
            data_end_time=now_ms,
            execution_start_time=now_ms,
            execution_end_time=now_ms,
            source="test_injector",
        )
        accepted = await runtime.anomaly_intake.enqueue(observation)
        if not accepted:
            raise HTTPException(
                status_code=409,
                detail="Synthetic anomaly already exists or is already owned by the FIFO.",
            )

        return {
            "status": "queued",
            "source": "test_injector",
            "anomaly_key": observation.deduplication_key,
            "observation": observation.to_dict(),
            "workflow": runtime.anomaly_watch_snapshot(),
        }

    @app.post(
        "/internal/v1/test/incidents/{incident_id}/complete",
        tags=["Testing"],
    )
    async def complete_test_incident(
        incident_id: str,
        payload: TestCompletionRequest,
    ) -> dict[str, Any]:
        """Simulate Technical Lead terminal acceptance and release the FIFO."""

        incident = await repository.get_incident(incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail="Incident not found")

        current_status = str(incident.get("status") or "").upper()
        if current_status in {"RESOLVED", "CLOSED", "OPERATOR_ACTION_REQUIRED"}:
            raise HTTPException(
                status_code=409,
                detail=f"Incident is already terminal: {current_status}",
            )

        agentic = dict(incident.get("agentic") or {})
        task_id = str(agentic.get("investigation_task_id") or "").strip()
        if not task_id:
            raise HTTPException(
                status_code=409,
                detail="Incident has no durable investigation task yet.",
            )

        task = await repository.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=409, detail="Incident task was not found")
        task_state = normalize_task_state(task.get("state") or "")
        if task_state not in {AgentTaskState.RUNNING, AgentTaskState.COMPLETED}:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Test terminal acceptance requires a RUNNING legacy test task or a "
                    "COMPLETED ReAct task; current state is "
                    f"{task_state.value}."
                ),
            )

        if task_state == AgentTaskState.RUNNING:
            completed_task = await task_workflow.mark_completed(
                task_id,
                outcome={
                    "status": "completed",
                    "summary": payload.summary,
                    "result_ref": "test_completion_hook",
                },
            )
        else:
            completed_task = task

        resolved = await repository.update_incident(
            incident_id,
            {
                "status": "RESOLVED",
                "diagnosis": {
                    "summary": payload.summary,
                    "confidence": 1.0,
                },
                "agentic": {
                    "current_agent": "technical_lead",
                    "active_agents": [],
                    "task_state": "COMPLETED",
                },
            },
        )
        if resolved is None:
            raise HTTPException(
                status_code=409,
                detail="Incident disappeared during synthetic terminal acceptance.",
            )

        await repository.add_event(
            incident_id,
            {
                "event_type": "TEST_TECHNICAL_LEAD_ACCEPTED",
                "agent_role": "technical_lead",
                "action": "simulate_terminal_critic_acceptance",
                "reason": (
                    "Development-only hook simulated the future Technical Lead critic "
                    "accepting the specialist result so terminal FIFO release can be tested."
                ),
                "status": "RESOLVED",
                "task_id": task_id,
                "outcome": payload.summary,
            },
        )

        for agent in runtime.agents:
            if agent.activity_incident_id == incident_id:
                agent.set_activity("IDLE", detail="test_workflow_completed")

        return {
            "status": "resolved",
            "incident": resolved,
            "task": completed_task,
            "note": (
                "Only the future Technical Lead terminal critic decision was simulated. "
                "The exclusive FIFO worker will observe RESOLVED and advance normally."
            ),
        }
