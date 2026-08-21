from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from pymongo import MongoClient

from agentic_system.incidents import AnomalyObservation
from agentic_system.integrations import MongoAnomalyInbox


MONGODB_URI = os.getenv(
    "MONGODB_TEST_URI",
    "mongodb://agentic:change-this-local-password@127.0.0.1:27017/agentic_monitor?authSource=admin",
)
MONGODB_DATABASE = os.getenv("MONGODB_TEST_DATABASE", "agentic_monitor")


@pytest.mark.integration
def test_waiting_anomaly_survives_reconnect_and_interrupted_processing_recovers(
    backend_health: dict,
) -> None:
    assert backend_health["status"] == "ok"
    suffix = uuid.uuid4().hex[:12]
    observation = AnomalyObservation(
        result_id=f"integration-anomaly-{suffix}",
        result_index=".opensearch-anomaly-results-history-integration-test",
        detector_id=f"single-entity-test-{suffix}",
        detector_name="CPU-processing-service",
        detector_description="Integration test detector",
        detector_indices=("metrics-processing-service-*",),
        anomaly_grade=1.0,
        confidence=0.88,
        anomaly_score=5.7,
        data_start_time=1_700_000_000_000,
        data_end_time=1_700_000_060_000,
        execution_start_time=1_700_000_120_000,
        execution_end_time=1_700_000_121_000,
    )
    anomaly_key = observation.deduplication_key
    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    database = client[MONGODB_DATABASE]

    async def scenario() -> tuple[dict, dict, dict, dict]:
        first = MongoAnomalyInbox(MONGODB_URI, MONGODB_DATABASE)
        await first.connect()
        try:
            waiting = await first.record_anomaly(observation.to_dict())
        finally:
            await first.close()

        # A new repository instance simulates a restarted backend process.
        second = MongoAnomalyInbox(MONGODB_URI, MONGODB_DATABASE)
        await second.connect()
        try:
            restored = await second.get_anomaly(anomaly_key)
            assert restored is not None
            processing = await second.mark_anomaly_processing(anomaly_key)
            assert processing is not None
            recovery = await second.recover_interrupted_processing()
            recovered = await second.get_anomaly(anomaly_key)
            assert recovered is not None
            return waiting, restored, recovery, recovered
        finally:
            await second.close()

    try:
        waiting, restored, recovery, recovered = asyncio.run(scenario())
        assert waiting["state"] == "WAITING"
        assert waiting["detector_name"] == "CPU-processing-service"
        assert restored["state"] == "WAITING"
        assert restored["detector_name"] == "CPU-processing-service"
        assert recovered["state"] == "WAITING"
        assert recovered["processing_attempt"] == 1
        assert recovered["last_error"] == "backend restart interrupted anomaly processing"
        assert recovery["interrupted"] >= 1
        assert recovery["reset_to_waiting"] >= 1
        assert database["anomaly_inbox"].count_documents({"anomaly_key": anomaly_key}) == 1
    finally:
        database["anomaly_inbox"].delete_many({"anomaly_key": anomaly_key})
        client.close()


@pytest.mark.integration
def test_waiting_false_positive_can_be_dismissed_without_becoming_processing(
    backend_health: dict,
) -> None:
    assert backend_health["status"] == "ok"
    suffix = uuid.uuid4().hex[:12]
    observation = AnomalyObservation(
        result_id=f"integration-dismiss-{suffix}",
        result_index=".opensearch-anomaly-results-history-integration-test",
        detector_id=f"single-entity-dismiss-{suffix}",
        detector_name="NETLAT-processing-service-data-service",
        detector_description="Integration false-positive detector",
        detector_indices=("metrics-processing-service-*",),
        anomaly_grade=1.0,
        confidence=0.99,
        anomaly_score=6.2,
        data_start_time=1_700_000_000_000,
        data_end_time=1_700_000_060_000,
        execution_start_time=1_700_000_120_000,
        execution_end_time=1_700_000_121_000,
    )
    anomaly_key = observation.deduplication_key
    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    database = client[MONGODB_DATABASE]

    async def scenario() -> tuple[dict, dict | None, dict | None]:
        inbox = MongoAnomalyInbox(MONGODB_URI, MONGODB_DATABASE)
        await inbox.connect()
        try:
            waiting = await inbox.record_anomaly(observation.to_dict())
            dismissed = await inbox.dismiss_waiting_anomaly(anomaly_key)
            processing = await inbox.mark_anomaly_processing(anomaly_key)
            return waiting, dismissed, processing
        finally:
            await inbox.close()

    try:
        waiting, dismissed, processing = asyncio.run(scenario())
        assert waiting["state"] == "WAITING"
        assert dismissed is not None
        assert dismissed["state"] == "DISMISSED"
        assert dismissed["dismissed_by"] == "operator"
        assert "not a true anomaly" in dismissed["dismissal_reason"]
        assert processing is None
        stored = database["anomaly_inbox"].find_one({"anomaly_key": anomaly_key})
        assert stored is not None
        assert stored["state"] == "DISMISSED"
        assert stored["incident_id"] is None
    finally:
        database["anomaly_inbox"].delete_many({"anomaly_key": anomaly_key})
        client.close()
