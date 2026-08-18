from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from pymongo import MongoClient

from agentic_system.application.incidents import IncidentWorkflow
from agentic_system.domain.anomalies import AnomalyObservation
from agentic_system.domain.incidents import IncidentCorrelationPolicy
from agentic_system.infrastructure.mongodb import IncidentRepository


MONGODB_URI = os.getenv(
    "MONGODB_TEST_URI",
    "mongodb://agentic:change-this-local-password@127.0.0.1:27017/agentic_monitor?authSource=admin",
)
MONGODB_DATABASE = os.getenv("MONGODB_TEST_DATABASE", "agentic_monitor")


def _observation(*, result_id: str, detector_id: str, grade: float) -> AnomalyObservation:
    return AnomalyObservation(
        result_id=result_id,
        result_index=".opendistro-anomaly-results-history-integration",
        detector_id=detector_id,
        anomaly_grade=grade,
        confidence=0.97,
        anomaly_score=5.1,
        data_start_time=1_787_057_103_398,
        data_end_time=1_787_057_163_398,
        execution_start_time=1_787_057_223_957,
        execution_end_time=1_787_057_223_957,
    )


async def _run_workflow(detector_id: str) -> tuple[dict, dict]:
    repository = IncidentRepository(MONGODB_URI, MONGODB_DATABASE)
    await repository.connect()
    try:
        workflow = IncidentWorkflow(
            repository,
            IncidentCorrelationPolicy(window_seconds=600),
        )
        first = await workflow.handle_anomaly(
            _observation(result_id="result-a", detector_id=detector_id, grade=0.8)
        )
        second = await workflow.handle_anomaly(
            _observation(result_id="result-b", detector_id=detector_id, grade=1.0)
        )
        return first, second
    finally:
        await repository.close()


@pytest.mark.integration
def test_same_detector_results_persist_as_one_active_incident(backend_health: dict) -> None:
    assert backend_health["status"] == "ok"
    detector_id = f"integration-detector-{uuid.uuid4().hex}"
    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    database = client[MONGODB_DATABASE]

    try:
        first, second = asyncio.run(_run_workflow(detector_id))

        assert first["incident_id"] == second["incident_id"]
        assert database["incidents"].count_documents(
            {"anomaly.detector_id": detector_id}
        ) == 1

        stored = database["incidents"].find_one({"anomaly.detector_id": detector_id})
        assert stored is not None
        assert stored["status"] == "NEW"
        assert stored["anomaly"]["grade"] == 1.0
        assert stored["anomaly"]["confidence"] == 0.97
        assert "anomaly_score" not in stored["anomaly"]

        events = list(
            database["incident_events"].find(
                {"incident_id": first["incident_id"]},
                {"event_type": 1},
            )
        )
        assert [event["event_type"] for event in events] == [
            "ANOMALY_DETECTED",
            "ANOMALY_REOBSERVED",
        ]
    finally:
        incident_ids = [
            document["incident_id"]
            for document in database["incidents"].find(
                {"anomaly.detector_id": detector_id},
                {"incident_id": 1},
            )
        ]
        if incident_ids:
            database["incident_events"].delete_many(
                {"incident_id": {"$in": incident_ids}}
            )
        database["incidents"].delete_many({"anomaly.detector_id": detector_id})
        client.close()
