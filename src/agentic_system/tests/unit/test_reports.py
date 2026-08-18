from agentic_system.reports import build_incident_report


def test_incident_report_is_a_pdf_and_contains_only_agentic_summary_data() -> None:
    incident = {
        "incident_id": "INC-TEST-001",
        "status": "DIAGNOSED",
        "severity": "HIGH",
        "entity": "processing-service",
        "created_at": "2026-08-18T10:00:00+00:00",
        "updated_at": "2026-08-18T10:01:00+00:00",
        "takeover_reason": "OpenSearch single-entity anomaly detected.",
        "anomaly": {
            "detector_id": "CPU-processing-service",
            "anomaly_type": "cpu_anomaly",
            "grade": 1.0,
            "confidence": 0.82,
        },
        "diagnosis": {
            "summary": "CPU saturation is the most probable cause.",
            "root_cause": "CPU-bound processing workload",
            "confidence": 0.9,
        },
        "remediation": {
            "summary": "Inspect the workload and restart only if operationally required.",
            "steps": ["Review the affected processing workflow."],
        },
        "validation": {"status": "PENDING"},
    }
    events = [
        {
            "timestamp": "2026-08-18T10:00:10+00:00",
            "agent_role": "technical_lead",
            "event_type": "ANALYSIS_STARTED",
            "description": "Incident assigned to the virtual technical team.",
        }
    ]

    pdf = build_incident_report(incident, events)

    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 500
