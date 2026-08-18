from agentic_system.api_models import IncidentCreate, IncidentPatch


def test_incident_model_drops_raw_opensearch_metric_fields() -> None:
    incident = IncidentCreate.model_validate(
        {
            "incident_id": "INC-TEST-001",
            "entity": "CPU-processing-service",
            "anomaly": {
                "detector_id": "detector-id-1",
                "detector_name": "CPU-processing-service",
                "anomaly_type": "cpu_anomaly",
                "grade": 1.0,
                "confidence": 0.8,
                "observed_value": 391.2,
                "baseline_value": 3.1,
                "raw_log": "must not be persisted",
            },
        }
    )

    anomaly = incident.model_dump()["anomaly"]
    assert anomaly == {
        "detector_id": "detector-id-1",
        "detector_name": "CPU-processing-service",
        "anomaly_type": "cpu_anomaly",
        "grade": 1.0,
        "confidence": 0.8,
    }


def test_patch_dump_keeps_only_explicitly_updated_nested_fields() -> None:
    patch = IncidentPatch.model_validate(
        {
            "diagnosis": {
                "summary": "Updated diagnosis",
            }
        }
    )

    assert patch.model_dump(exclude_none=True, exclude_unset=True) == {
        "diagnosis": {"summary": "Updated diagnosis"}
    }
