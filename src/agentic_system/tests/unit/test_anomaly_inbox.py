from agentic_system.incidents import AnomalyObservation
from agentic_system.integrations import normalize_anomaly_record


def test_anomaly_record_uses_opensearch_result_identity_and_waiting_state() -> None:
    observation = AnomalyObservation(
        result_id="result-123",
        result_index=".opensearch-anomaly-results-history-2026.08.19",
        detector_id="detector-single-entity-123",
        detector_name="CPU-processing-service",
        detector_description="Processing service CPU anomaly detector",
        detector_indices=("metrics-processing-service-*",),
        anomaly_grade=1.0,
        confidence=0.87,
        anomaly_score=6.4,
        data_start_time=1_700_000_000_000,
        data_end_time=1_700_000_060_000,
        execution_start_time=1_700_000_120_000,
        execution_end_time=1_700_000_121_000,
    )

    record = normalize_anomaly_record(observation.to_dict())

    assert record["anomaly_key"] == (
        ".opensearch-anomaly-results-history-2026.08.19:result-123"
    )
    assert record["state"] == "WAITING"
    assert record["source"] == "opensearch"
    assert record["incident_id"] is None
    assert record["processing_attempt"] == 0
    assert record["detector_id"] == "detector-single-entity-123"
    assert record["detector_name"] == "CPU-processing-service"
    assert record["detector_description"] == "Processing service CPU anomaly detector"
    assert record["detector_indices"] == ["metrics-processing-service-*"]
    assert record["anomaly_grade"] == 1.0
    assert record["confidence"] == 0.87


def test_anomaly_observation_can_be_restored_from_durable_record() -> None:
    original = AnomalyObservation(
        result_id="result-restore",
        result_index=".opensearch-anomaly-results-history-test",
        detector_id="detector-restore",
        detector_name="NETLAT-processing-service-data-service",
        detector_description="Network latency detector",
        detector_indices=("metrics-processing-service-*",),
        anomaly_grade=0.92,
        confidence=0.81,
        anomaly_score=5.2,
        data_start_time=100,
        data_end_time=200,
        execution_start_time=300,
        execution_end_time=400,
    )
    record = normalize_anomaly_record(original.to_dict())

    restored = AnomalyObservation.from_dict(record)

    assert restored == original
    assert restored.deduplication_key == record["anomaly_key"]


def test_synthetic_anomaly_source_survives_durable_round_trip() -> None:
    original = AnomalyObservation(
        result_id="mock-result-1",
        result_index="agentic-test-anomaly-results",
        detector_id="mock-system-detector",
        detector_name="TEST-CPU-processing-service",
        anomaly_grade=1.0,
        confidence=0.99,
        anomaly_score=9.1,
        data_start_time=100,
        data_end_time=200,
        execution_start_time=300,
        execution_end_time=400,
        source="test_injector",
    )

    record = normalize_anomaly_record(original.to_dict())
    restored = AnomalyObservation.from_dict(record)

    assert record["source"] == "test_injector"
    assert restored.source == "test_injector"
    assert restored.detector_name == "TEST-CPU-processing-service"
