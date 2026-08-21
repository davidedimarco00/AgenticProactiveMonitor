import pytest

from agentic_system.agents.collaboration import normalize_peer_context


def test_peer_context_requires_tl_authorization_and_preserves_peer_evidence() -> None:
    context = normalize_peer_context(
        {
            "incident_id": "INC-PEER-001",
            "support_task_id": "TASK-PEER-002",
            "peer_role": "system_engineer",
            "target_role": "application_engineer",
            "authorized_by": "technical_lead",
            "support_domain": "application",
            "support_reason": "Correlate CPU evidence with application behaviour.",
            "specialist_result": {
                "summary": "CPU anomaly was observed.",
                "findings": ["CPU was above baseline."],
            },
        },
        sender="system-engineer@xmpp",
    )

    assert context["incident_id"] == "INC-PEER-001"
    assert context["support_task_id"] == "TASK-PEER-002"
    assert context["peer_role"] == "system_engineer"
    assert context["target_role"] == "application_engineer"
    assert context["peer_jid"] == "system-engineer@xmpp"
    assert context["specialist_result"]["findings"] == ["CPU was above baseline."]


def test_peer_context_rejects_unauthorized_specialist_fanout() -> None:
    with pytest.raises(ValueError, match="authorized by the Technical Lead"):
        normalize_peer_context(
            {
                "incident_id": "INC-PEER-001",
                "support_task_id": "TASK-PEER-002",
                "peer_role": "system_engineer",
                "target_role": "application_engineer",
                "authorized_by": "system_engineer",
                "specialist_result": {},
            },
            sender="system-engineer@xmpp",
        )
