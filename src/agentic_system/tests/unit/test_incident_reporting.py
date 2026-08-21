from agentic_system.incidents.reporting import build_incident_report


def test_incident_report_builds_pdf_with_structured_remediation() -> None:
    incident = {
        "incident_id": "INC-TEST-001",
        "status": "OPERATOR_ACTION_REQUIRED",
        "severity": "HIGH",
        "entity": "processing-service",
        "created_at": "2026-08-21T16:00:00+00:00",
        "updated_at": "2026-08-21T16:05:00+00:00",
        "takeover_reason": "OpenSearch detected an abnormal CPU deviation.",
        "anomaly": {
            "detector_name": "CPU-processing-service",
            "detector_id": "detector-test",
            "anomaly_type": "CPU",
            "grade": 1.0,
            "confidence": 0.92,
        },
        "diagnosis": {
            "summary": "CPU saturation is concentrated in processing-service workers.",
            "root_cause": "CPU-bound worker execution is saturating processing-service.",
            "confidence": 0.88,
            "evidence": [
                "Container CPU is elevated compared with the normal baseline.",
                "The highest-CPU process belongs to processing-service.",
            ],
        },
        "agentic": {"review_confidence": 0.96},
        "remediation": {
            "status": "ADVISORY",
            "summary": "Reduce the abnormal worker load and verify CPU recovery.",
            "steps": [
                {
                    "title": "Verify current worker processes",
                    "target": "processing-service",
                    "command_type": "verification",
                    "command": "docker exec processing-service ps -eo pid,ppid,comm,%cpu,%mem --sort=-%cpu",
                    "purpose": "Confirm which worker processes consume CPU before remediation.",
                    "expected_result": "The CPU-heavy worker processes are visible at the top.",
                    "what_to_verify": "Confirm PID and CPU usage match the collected evidence.",
                },
                {
                    "title": "Restart processing-service after operator approval",
                    "target": "processing-service",
                    "command_type": "remediation",
                    "command": "docker restart processing-service",
                    "purpose": "Clear the abnormal worker execution after the cause is accepted.",
                    "expected_result": "The container restarts and returns to running state.",
                    "what_to_verify": "Re-check CPU and service health after restart.",
                },
            ],
        },
        "validation": {
            "status": "OPERATOR_ACTION_PENDING",
            "summary": "Human approval is required before applying remediation.",
        },
    }
    events = [
        {
            "timestamp": "2026-08-21T16:01:00+00:00",
            "agent_role": "system_engineer",
            "action": "observe",
            "reason": "Collected live process evidence.",
        }
    ]

    pdf = build_incident_report(incident, events)

    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1500
