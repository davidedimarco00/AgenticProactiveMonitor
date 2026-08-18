from agentic_system.incident_repository import deep_merge, normalize_event, normalize_incident


def test_normalize_incident_keeps_agentic_conclusions_without_raw_metrics() -> None:
    incident = normalize_incident(
        {
            "status": "diagnosed",
            "severity": "high",
            "entity": "processing-service",
            "anomaly": {
                "detector_id": "CPU-processing-service",
                "anomaly_type": "cpu_anomaly",
                "grade": 1.0,
                "confidence": 0.81,
                "observed_value": 391.2,
                "baseline_value": 3.1,
                "raw_log": "must not be persisted",
            },
            "diagnosis": {"summary": "CPU saturation is the most probable cause."},
            "raw_metrics": {"cpu": [1, 2, 3]},
            "logs": ["raw log entry"],
        }
    )

    assert incident["status"] == "DIAGNOSED"
    assert incident["severity"] == "HIGH"
    assert incident["entity"] == "processing-service"
    assert incident["service"] == "processing-service"
    assert incident["anomaly"]["detector_id"] == "CPU-processing-service"
    assert "observed_value" not in incident["anomaly"]
    assert "baseline_value" not in incident["anomaly"]
    assert "raw_log" not in incident["anomaly"]
    assert "raw_metrics" not in incident
    assert "logs" not in incident
    assert incident["incident_id"].startswith("INC-")


def test_deep_merge_updates_diagnosis_without_losing_existing_fields() -> None:
    current = {
        "diagnosis": {
            "summary": "Initial diagnosis",
            "confidence": 0.65,
        },
        "status": "UNDER_ANALYSIS",
    }

    merged = deep_merge(
        current,
        {
            "diagnosis": {"confidence": 0.9, "root_cause": "Dependency failure"},
            "status": "DIAGNOSED",
        },
    )

    assert merged["diagnosis"]["summary"] == "Initial diagnosis"
    assert merged["diagnosis"]["confidence"] == 0.9
    assert merged["diagnosis"]["root_cause"] == "Dependency failure"
    assert merged["status"] == "DIAGNOSED"


def test_normalize_event_links_event_to_incident_and_drops_raw_tool_payloads() -> None:
    event = normalize_event(
        {
            "event_type": "diagnosis_produced",
            "agent_role": "network_engineer",
            "description": "Network path degradation identified.",
            "raw_tool_output": {"metrics": [1, 2, 3]},
        },
        "INC-TEST-001",
    )

    assert event["incident_id"] == "INC-TEST-001"
    assert event["event_type"] == "DIAGNOSIS_PRODUCED"
    assert event["event_id"].startswith("AEV-")
    assert event["timestamp"]
    assert event["reason"] == "Network path degradation identified."
    assert "raw_tool_output" not in event
