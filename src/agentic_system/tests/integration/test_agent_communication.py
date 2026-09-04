from __future__ import annotations

from typing import Any

import pytest


@pytest.mark.integration
def test_technical_lead_and_system_engineer_exchange_real_xmpp_messages(
    backend_communication_health: dict[str, Any],
) -> None:
    """TEST E: validate the real TL -> System Engineer -> TL XMPP round trip."""

    probe = backend_communication_health["communication_probe"]

    assert probe["status"] == "passed"
    assert probe["protocol"] == "agentic-proactive-monitor/v1"
    assert probe["request_performative"] == "REQUEST"
    assert probe["response_performative"] == "AGREE"

    assert probe["sender"] == "technical-lead@xmpp"
    assert probe["receiver"] == "system-engineer@xmpp"
    assert probe["response_sender"] == "system-engineer@xmpp"
    assert probe["response_receiver"] == "technical-lead@xmpp"

    assert probe["request_type"] == "runtime_connectivity_probe"
    assert probe["response_type"] == "request_acknowledged"
    assert probe["acknowledged_by"] == "system_engineer"


@pytest.mark.integration
def test_request_and_acknowledgement_keep_the_same_correlation_id(
    backend_communication_health: dict[str, Any],
) -> None:
    """The response must be traceable to exactly the request that caused it."""

    probe = backend_communication_health["communication_probe"]
    request_correlation_id = probe["request_correlation_id"]
    response_correlation_id = probe["response_correlation_id"]

    assert request_correlation_id
    assert response_correlation_id
    assert request_correlation_id == response_correlation_id


@pytest.mark.integration
def test_runtime_observes_bidirectional_message_activity(
    backend_communication_health: dict[str, Any],
) -> None:
    """Both agents must report transport activity produced by the real XMPP round trip."""

    agents = {
        agent["role"]: agent
        for agent in backend_communication_health["agents"]
    }
    technical_lead = agents["technical_lead"]
    system_engineer = agents["system_engineer"]

    assert technical_lead["messages_sent"] >= 1
    assert technical_lead["messages_received"] >= 1
    assert technical_lead["last_message_at"]

    assert system_engineer["messages_sent"] >= 1
    assert system_engineer["messages_received"] >= 1
    assert system_engineer["last_message_at"]
