from __future__ import annotations

import json
import os
import uuid
from urllib.request import Request, urlopen

import pytest
from pymongo import MongoClient


API_URL = os.getenv("AGENTIC_API_TEST_URL", "http://127.0.0.1:8082").rstrip("/")
MONGODB_URI = os.getenv(
    "MONGODB_TEST_URI",
    "mongodb://agentic:change-this-local-password@127.0.0.1:27017/agentic_monitor?authSource=admin",
)
MONGODB_DATABASE = os.getenv("MONGODB_TEST_DATABASE", "agentic_monitor")


def _json_request(method: str, path: str, payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        f"{API_URL}{path}",
        data=body,
        headers={"Content-Type": "application/json"} if body is not None else {},
        method=method,
    )
    with urlopen(request, timeout=5) as response:  # noqa: S310 - local integration endpoint
        return json.loads(response.read().decode("utf-8"))


@pytest.mark.integration
def test_api_persists_incident_history_in_mongodb_and_generates_pdf(backend_health: dict) -> None:
    assert backend_health["status"] == "ok"
    incident_id = f"INC-INTEGRATION-{uuid.uuid4().hex[:10].upper()}"
    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    database = client[MONGODB_DATABASE]

    try:
        created = _json_request(
            "POST",
            "/internal/v1/incidents",
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
                },
                "diagnosis": {
                    "summary": "CPU saturation is the most probable cause.",
                    "root_cause": "CPU-bound processing workload",
                    "confidence": 0.9,
                },
                "remediation": {
                    "summary": "Review the processing workload before taking corrective action."
                },
            },
        )
        assert created["incident_id"] == incident_id

        event = _json_request(
            "POST",
            f"/internal/v1/incidents/{incident_id}/events",
            {
                "event_type": "diagnosis_produced",
                "agent_role": "technical_lead",
                "action": "Review specialist conclusions",
                "description": "The Technical Lead consolidated the diagnosis.",
                "status": "completed",
            },
        )
        assert event["incident_id"] == incident_id

        stored = database["incidents"].find_one({"incident_id": incident_id})
        stored_event = database["incident_events"].find_one({"incident_id": incident_id})
        assert stored is not None
        assert stored_event is not None
        assert stored["diagnosis"]["root_cause"] == "CPU-bound processing workload"

        public_incident = _json_request("GET", f"/api/v1/incidents/{incident_id}")
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

        openapi = _json_request("GET", "/openapi.json")
        assert "/internal/v1/incidents" not in openapi["paths"]
        assert "get" in openapi["paths"]["/api/v1/incidents"]
        assert "post" not in openapi["paths"]["/api/v1/incidents"]
    finally:
        database["incident_events"].delete_many({"incident_id": incident_id})
        database["incidents"].delete_many({"incident_id": incident_id})
        client.close()
