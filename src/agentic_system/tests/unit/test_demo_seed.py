from agentic_system.demo_seed import _prepare_demo


def test_prepare_demo_adapts_legacy_dashboard_mock_without_raw_metrics() -> None:
    incident, events = _prepare_demo(
        {
            "incident_id": "DEMO-CPU-001",
            "host_id": "processing-service",
            "anomaly": {
                "detector_id": "detector-1",
                "metric": "docker_container_cpu.usage_percent",
                "grade": 1.0,
                "confidence": 0.8,
                "observed_value": 390.0,
                "baseline_value": 20.0,
            },
            "agentic": {"current_agent": "coordinator@xmpp"},
            "agent_events": [
                {
                    "event_id": "AEV-DEMO-001",
                    "event_type": "diagnosis_produced",
                    "agent_jid": "reasoning@xmpp",
                    "called_by": "coordinator@xmpp",
                    "outcome": "CPU approximately 392%; memory stable; processing-service container running.",
                }
            ],
            "timeline": [{"legacy": True}],
            "diagnosis": {"summary": "Mock diagnosis"},
        }
    )

    assert incident["anomaly"] == {
        "detector_id": "detector-1",
        "grade": 1.0,
        "confidence": 0.8,
        "anomaly_type": "cpu_saturation",
    }
    assert "host_id" not in incident
    assert "timeline" not in incident
    assert "agent_events" not in incident
    assert incident["agentic"]["current_agent"] == "technical_lead"
    assert incident["diagnosis"]["root_cause"]
    assert incident["validation"]["status"] == "NOT_EXECUTED"

    assert len(events) == 1
    assert events[0]["agent_role"] == "application_engineer"
    assert events[0]["agent_jid"] == "application-engineer@xmpp"
    assert events[0]["called_by"] == "technical-lead@xmpp"
    assert "392%" not in events[0]["outcome"]
