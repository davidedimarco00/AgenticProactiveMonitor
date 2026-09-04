from agentic_system.integrations.mongodb import sanitize_incident_payload


def test_collaboration_metadata_survives_incident_sanitization() -> None:
    sanitized = sanitize_incident_payload(
        {
            "incident_id": "INC-COLLAB-001",
            "status": "UNDER_ANALYSIS",
            "agentic": {
                "current_agent": "application_engineer",
                "active_agents": [
                    "technical_lead",
                    "system_engineer",
                    "application_engineer",
                ],
                "review_state": "COMPLETED",
                "review_decision": "resolve",
                "collaboration_roles": [
                    "system_engineer",
                    "application_engineer",
                ],
                "collaboration_round": 1,
                "peer_collaboration_state": "ACTIVE",
                "raw_private_field": "must-not-persist",
            },
        }
    )

    agentic = sanitized["agentic"]
    assert agentic["review_decision"] == "resolve"
    assert agentic["collaboration_roles"] == [
        "system_engineer",
        "application_engineer",
    ]
    assert agentic["collaboration_round"] == 1
    assert agentic["peer_collaboration_state"] == "ACTIVE"
    assert "raw_private_field" not in agentic


def test_retired_technical_lead_support_fields_are_not_persisted() -> None:
    """The TL cannot authorize a peer round anymore; the fields must not come back."""

    sanitized = sanitize_incident_payload(
        {
            "incident_id": "INC-COLLAB-002",
            "status": "RESOLVED",
            "agentic": {
                "review_decision": "resolve",
                "support_requested": True,
                "support_domain": "application",
                "support_reason": "Application evidence is required.",
            },
        }
    )

    agentic = sanitized["agentic"]
    assert "support_requested" not in agentic
    assert "support_domain" not in agentic
    assert "support_reason" not in agentic
