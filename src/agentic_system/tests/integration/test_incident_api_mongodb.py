from __future__ import annotations

import asyncio
import json
import os
import uuid
from urllib.request import urlopen

import pytest
from pymongo import MongoClient

from agentic_system.integrations import IncidentRepository


API_URL = os.getenv("AGENTIC_API_TEST_URL", "http://127.0.0.1:8082").rstrip("/")
MONGODB_URI = os.getenv(
    "MONGODB_TEST_URI",
    "mongodb://agentic:change-this-local-password@127.0.0.1:27017/agentic_monitor?authSource=admin",
)
MONGODB_DATABASE = os.getenv("MONGODB_TEST_DATABASE", "agentic_monitor")


def _get_json(path: str) -> dict:
    with urlopen(f"{API_URL}{path}", timeout=5) as response:  # noqa: S310 - local integration endpoint
        return json.loads(response.read().decode("utf-8"))


async def _seed_incident(incident_id: str) -> None:
    repository = IncidentRepository(MONGODB_URI, MONGODB_DATABASE)
    await repository.connect()
    try:
        created = await repository.create_incident(
            {
                "incident_id": incident_id,
                "status": "DIAGNOSED",
                "severity": "HIGH",
                "entity": "processing-service",
                "takeover_reason": "OpenSearch single-entity anomaly detected.",
                "anomaly": {
                    "detector_id": "CPU-processing-service",
                    "anomaly_type": "cpu_anomaly",
                    "grade": 1.0,
                    "confidence": 0.8,
                    "observed_value": 391.2,
                    "baseline_value": 3.1,
                    "raw_log": "this observability payload must not be persisted",
                },
                "diagnosis": {
                    "summary": "CPU saturation is the most probable cause.",
                    "root_cause": "CPU-bound processing workload",
                    "confidence": 0.9,
                },
                "remediation": {
                    "summary": "Review the processing workload before taking corrective action."
                },
            }
        )
        assert created["incident_id"] == incident_id

        event = await repository.add_event(
            incident_id,
            {
                "event_type": "diagnosis_produced",
                "agent_role": "technical_lead",
                "action": "Review specialist conclusions",
                "description": "The Technical Lead consolidated the diagnosis.",
                "status": "completed",
                "raw_tool_output": "must not be persisted",
            },
        )
        assert event is not None
        assert event["incident_id"] == incident_id
    finally:
        await repository.close()


@pytest.mark.integration
def test_repository_mongodb_public_api_and_pdf_work_together(backend_health: dict) -> None:
    assert backend_health["status"] == "ok"
    incident_id = f"INC-INTEGRATION-{uuid.uuid4().hex[:10].upper()}"
    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    database = client[MONGODB_DATABASE]

    try:
        asyncio.run(_seed_incident(incident_id))

        stored = database["incidents"].find_one({"incident_id": incident_id})
        stored_event = database["incident_events"].find_one({"incident_id": incident_id})
        assert stored is not None
        assert stored_event is not None
        assert stored["diagnosis"]["root_cause"] == "CPU-bound processing workload"
        assert "observed_value" not in stored["anomaly"]
        assert "baseline_value" not in stored["anomaly"]
        assert "raw_log" not in stored["anomaly"]
        assert "raw_tool_output" not in stored_event

        public_incident = _get_json(f"/api/v1/incidents/{incident_id}")
        assert public_incident["incident_id"] == incident_id
        assert public_incident["timeline"][0]["event_type"] == "DIAGNOSIS_PRODUCED"
        assert "observed_value" not in public_incident["anomaly"]
        assert "baseline_value" not in public_incident["anomaly"]

        with urlopen(  # noqa: S310 - local integration endpoint
            f"{API_URL}/api/v1/incidents/{incident_id}/report",
            timeout=5,
        ) as response:
            assert response.headers.get_content_type() == "application/pdf"
            assert response.read(5) == b"%PDF-"

        openapi = _get_json("/openapi.json")
        assert not any(path.startswith("/internal/") for path in openapi["paths"])
        assert "get" in openapi["paths"]["/api/v1/incidents"]
        assert "post" not in openapi["paths"]["/api/v1/incidents"]
    finally:
        database["incident_events"].delete_many({"incident_id": incident_id})
        database["incidents"].delete_many({"incident_id": incident_id})
        client.close()
