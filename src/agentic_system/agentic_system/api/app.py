from __future__ import annotations

from io import BytesIO
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import StreamingResponse

from ..incidents import build_incident_report
from ..integrations import ACTIVE_STATUSES, IncidentRepository
from ..runtime import AgentRuntime


def _overview(incidents: list[dict[str, Any]]) -> dict[str, int]:
    active = [i for i in incidents if str(i.get("status", "")).upper() in ACTIVE_STATUSES]
    under_analysis = [
        i for i in incidents if str(i.get("status", "")).upper() == "UNDER_ANALYSIS"
    ]
    operator_action = [
        i
        for i in incidents
        if str(i.get("status", "")).upper() == "OPERATOR_ACTION_REQUIRED"
    ]
    resolved = [
        i
        for i in incidents
        if str(i.get("status", "")).upper() in {"RESOLVED", "CLOSED"}
    ]
    critical = [
        i for i in active if str(i.get("severity", "")).upper() == "CRITICAL"
    ]
    return {
        "total": len(incidents),
        "active": len(active),
        "under_analysis": len(under_analysis),
        "operator_action": len(operator_action),
        "resolved": len(resolved),
        "critical": len(critical),
    }


def create_api_app(runtime: AgentRuntime, repository: IncidentRepository) -> FastAPI:
    app = FastAPI(
        title="Agentic Proactive Monitor API",
        summary="Read-only operator API for the autonomous agentic monitoring backend.",
        description=(
            "The operator-facing API is read-only: the autonomous multi-agent core starts from "
            "OpenSearch anomaly events, not from operator commands. MongoDB stores incidents, "
            "durable agent tasks, diagnoses, remediations and structured incident history. "
            "Raw metrics and logs remain in OpenSearch."
        ),
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        openapi_tags=[
            {"name": "Health", "description": "Backend API and persistence health."},
            {"name": "Incidents", "description": "Read-only incident information for operators."},
            {
                "name": "Tasks",
                "description": "Read-only durable agent task state for fault-tolerance observability.",
            },
            {"name": "Agents", "description": "Structured runtime and agent activity information."},
        ],
    )

    @app.get("/health", tags=["Health"])
    async def health(response: Response) -> dict[str, Any]:
        mongo_ok = await repository.ping()
        runtime_ok = runtime.started and runtime.running_count == len(runtime.agents)
        if not (mongo_ok and runtime_ok):
            response.status_code = 503
        return {
            "status": "ok" if mongo_ok and runtime_ok else "degraded",
            "component": "agentic-backend-api",
            "mongodb": "reachable" if mongo_ok else "unreachable",
            "agents_running": runtime.running_count,
            "agents_configured": len(runtime.agents),
        }

    @app.get("/ready", tags=["Health"])
    async def ready(response: Response) -> dict[str, Any]:
        return await health(response)

    @app.get("/api/v1/system/status", tags=["Health"])
    async def system_status() -> dict[str, Any]:
        return {
            "mongodb_reachable": await repository.ping(),
            "agents_running": runtime.running_count,
            "agents": runtime.snapshot(),
            "team_communication_ok": runtime.team_communication_ok,
            "unreachable_specialists": runtime.unreachable_specialists,
        }

    @app.get("/api/v1/overview", tags=["Incidents"])
    async def overview() -> dict[str, Any]:
        incidents = await repository.list_incidents(limit=500)
        return {"overview": _overview(incidents), "recent_incidents": incidents[:7]}

    @app.get("/api/v1/incidents", tags=["Incidents"])
    async def list_incidents(
        limit: int = Query(100, ge=1, le=500),
        status: str | None = Query(None),
        q: str | None = Query(None),
    ) -> dict[str, Any]:
        incidents = await repository.list_incidents(limit=limit, status=status, query=q)
        return {"incidents": incidents}

    @app.get("/api/v1/incidents/{incident_id}", tags=["Incidents"])
    async def incident_detail(incident_id: str) -> dict[str, Any]:
        incident = await repository.get_incident(incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail="Incident not found")
        incident["timeline"] = await repository.list_events(
            incident_id=incident_id, limit=500, ascending=True
        )
        incident["tasks"] = await repository.list_tasks(
            incident_id=incident_id,
            limit=200,
        )
        return incident

    @app.get("/api/v1/incidents/{incident_id}/timeline", tags=["Incidents"])
    async def incident_timeline(incident_id: str) -> dict[str, Any]:
        if await repository.get_incident(incident_id) is None:
            raise HTTPException(status_code=404, detail="Incident not found")
        events = await repository.list_events(
            incident_id=incident_id, limit=500, ascending=True
        )
        return {"incident_id": incident_id, "events": events}

    @app.get(
        "/api/v1/incidents/{incident_id}/report",
        tags=["Incidents"],
        responses={
            200: {
                "content": {"application/pdf": {}},
                "description": "Incident PDF report",
            }
        },
    )
    async def incident_report(incident_id: str) -> StreamingResponse:
        incident = await repository.get_incident(incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail="Incident not found")
        events = await repository.list_events(
            incident_id=incident_id, limit=500, ascending=True
        )
        pdf = build_incident_report(incident, events)
        filename = f"{incident_id}-incident-report.pdf"
        return StreamingResponse(
            BytesIO(pdf),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/v1/tasks", tags=["Tasks"])
    async def list_tasks(
        limit: int = Query(100, ge=1, le=500),
        state: str | None = Query(None),
        incident_id: str | None = Query(None),
    ) -> dict[str, Any]:
        states = [state.upper()] if state else None
        tasks = await repository.list_tasks(
            states=states,
            incident_id=incident_id,
            limit=limit,
        )
        return {"tasks": tasks}

    @app.get("/api/v1/tasks/{task_id}", tags=["Tasks"])
    async def task_detail(task_id: str) -> dict[str, Any]:
        task = await repository.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Agent task not found")
        return task

    @app.get("/api/v1/agents", tags=["Agents"])
    async def agents() -> dict[str, Any]:
        return {
            "team_communication_ok": runtime.team_communication_ok,
            "agents": runtime.snapshot(),
        }

    @app.get("/api/v1/agent-events", tags=["Agents"])
    async def agent_events(
        agent_role: str | None = Query(None),
        limit: int = Query(100, ge=1, le=500),
    ) -> dict[str, Any]:
        events = await repository.list_events(
            agent_role=agent_role, limit=limit, ascending=False
        )
        return {"events": events}

    return app
