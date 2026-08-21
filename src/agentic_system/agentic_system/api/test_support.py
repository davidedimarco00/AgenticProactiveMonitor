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
    summary: str = "Synthetic specialist investigation completed successfully."


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
    specialist BDI or durable task state transitions.
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
        """Simulate the not-yet-implemented ReAct result and release the FIFO."""

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
        if task_state != AgentTaskState.RUNNING:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Synthetic completion requires the selected specialist to own a RUNNING task; "
                    f"current state is {task_state.value}."
                ),
            )

        completed_task = await task_workflow.mark_completed(
            task_id,
            outcome={
                "synthetic": True,
                "source": "test_completion_hook",
                "summary": payload.summary,
            },
        )
        resolved = await repository.update_incident(
            incident_id,
            {
                "status": "RESOLVED",
                "diagnosis": {
                    "summary": payload.summary,
                    "confidence": 1.0,
                    "source": "test_completion_hook",
                },
                "agentic": {
                    "current_agent": "technical_lead",
                    "active_agents": [],
                },
            },
        )
        if resolved is None:
            raise HTTPException(
                status_code=409,
                detail="Incident disappeared during synthetic completion.",
            )

        await repository.add_event(
            incident_id,
            {
                "event_type": "TEST_WORKFLOW_COMPLETED",
                "agent_role": "technical_lead",
                "action": "simulate_terminal_specialist_result",
                "reason": (
                    "Development-only completion hook simulated the future ReAct result "
                    "to validate terminal workflow release and FIFO progression."
                ),
                "status": "RESOLVED",
                "task_id": task_id,
                "task_state": completed_task.get("state"),
                "outcome": payload.summary,
            },
        )

        # Agent activity is runtime observability rather than durable task state.
        # Release only agents that currently advertise this incident as context.
        for agent in runtime.agents:
            if agent.activity_incident_id == incident_id:
                agent.set_activity("IDLE", detail="test_workflow_completed")

        return {
            "status": "resolved",
            "incident": resolved,
            "task": completed_task,
            "note": (
                "The exclusive FIFO worker will observe RESOLVED and advance to the "
                "next durable WAITING anomaly on its normal polling cycle."
            ),
        }
