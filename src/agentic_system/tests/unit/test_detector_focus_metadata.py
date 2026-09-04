from agentic_system.incidents.anomalies import AnomalyObservation
from agentic_system.incidents.workflow import _anomaly_payload
from agentic_system.integrations.opensearch_catalog import _feature_context, _term_value


def test_catalog_extracts_measurement_and_feature_from_detector_definition() -> None:
    detector = {
        "filter_query": {"term": {"measurement_name": "docker_container_cpu"}},
        "feature_attributes": [
            {
                "feature_name": "CPU_ANOMALY",
                "feature_enabled": True,
                "aggregation_query": {
                    "cpu_anomaly": {
                        "avg": {"field": "docker_container_cpu.usage_percent"}
                    }
                },
            }
        ],
    }

    assert _term_value(detector["filter_query"], "measurement_name") == "docker_container_cpu"
    assert _feature_context(detector) == (
        "CPU_ANOMALY",
        "docker_container_cpu.usage_percent",
    )


def test_incident_anomaly_payload_preserves_signal_and_time_window() -> None:
    observation = AnomalyObservation(
        result_id="result-1",
        result_index="results",
        detector_id="detector-1",
        detector_name="CPU-processing-service",
        anomaly_grade=1.0,
        confidence=0.95,
        anomaly_score=6.4,
        data_start_time=1000,
        data_end_time=2000,
        execution_start_time=3000,
        execution_end_time=4000,
    )
    payload = _anomaly_payload(
        observation,
        detector_name="CPU-processing-service",
        detector_context={
            "detector_type": "SINGLE_ENTITY",
            "description": "Container CPU usage anomaly detector for processing-service",
            "indices": ["metrics-processing-service-*"],
            "time_field": "@timestamp",
            "measurement_name": "docker_container_cpu",
            "feature_name": "CPU_ANOMALY",
            "feature_field": "docker_container_cpu.usage_percent",
        },
    )

    assert payload["detector_type"] == "SINGLE_ENTITY"
    assert payload["measurement_name"] == "docker_container_cpu"
    assert payload["feature_name"] == "CPU_ANOMALY"
    assert payload["feature_field"] == "docker_container_cpu.usage_percent"
    assert payload["anomaly_score"] == 6.4
    assert payload["data_start_time"] == 1000
    assert payload["data_end_time"] == 2000
