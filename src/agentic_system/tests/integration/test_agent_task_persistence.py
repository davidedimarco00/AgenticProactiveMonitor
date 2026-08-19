from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from pymongo import MongoClient

from agentic_system.incidents import AgentTaskWorkflow
from agentic_system.integrations import IncidentRepository


MONGODB_URI = os.getenv(
    "MONGODB_TEST_URI",
    "mongodb://agentic:change-this-local-password@127.0.0.1:27017/agentic_monitor?authSource=admin",
)
MONGODB_DATABASE = os.getenv("MONGODB_TEST_DATABASE", "agentic_monitor")


@pytest.mark.integration
def test_agent_task_is_idempotent_and_recovers_from_interrupted_running_state(
    backend_health: dict,
) -> None:
    assert backend_health["status"] == "ok"
    suffix = uuid.uuid4().hex[:10]
    incident_id = f"INC-FT-{suffix}"
    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    database = client[MONGODB_DATABASE]

    async def scenario() -> tuple[dict, dict, dict]:
        repository = IncidentRepository(MONGODB_URI, MONGODB_DATABASE)
        await repository.connect()
        try:
            workflow = AgentTaskWorkflow(repository, default_max_attempts=3)
            incident = {"incident_id": incident_id, "status": "TRIAGED"}
            first = await workflow.create_investigation_task(
                incident,
                primary_investigator="network_engineer",
            )
            duplicate = await workflow.create_investigation_task(
                incident,
                primary_investigator="network_engineer",
            )
            dispatched = await workflow.mark_dispatched(first["task_id"])
            running = await workflow.mark_running(dispatched["task_id"])
            summary = await workflow.recover_incomplete_tasks(incident_id=incident_id)
            recovered = await repository.get_task(running["task_id"])
            assert recovered is not None
            return duplicate, summary.to_dict(), recovered
        finally:
            await repository.close()

    try:
        duplicate, summary, recovered = asyncio.run(scenario())
        assert database["agent_tasks"].count_documents({"incident_id": incident_id}) == 1
        assert duplicate["task_id"] == recovered["task_id"]
        assert recovered["state"] == "RETRYING"
        assert recovered["attempt"] == 1
        assert recovered["last_error"]["type"] == "backend_restart"
        assert recovered["last_error"]["retryable"] is True
        assert summary == {"scanned": 1, "retrying": 1, "failed": 0}
    finally:
        database["agent_tasks"].delete_many({"incident_id": incident_id})
        client.close()
