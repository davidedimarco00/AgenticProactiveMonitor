import json

import pytest

from agentic_system.agents.messages import (
    AGENTIC_PROTOCOL,
    AgentMessage,
    Performative,
    build_spade_message,
    parse_spade_message,
)


def test_message_round_trip_preserves_data() -> None:
    envelope = AgentMessage.create(
        type="probe",
        sender="sender",
        receiver="receiver",
        payload={"host": "processing-service"},
        correlation_id="corr-123",
    )

    assert AgentMessage.from_json(envelope.to_json()) == envelope


def test_message_rejects_non_object_payload() -> None:
    body = json.dumps(
        {
            "type": "probe",
            "sender": "sender",
            "receiver": "receiver",
            "timestamp": "2026-08-13T00:00:00+00:00",
            "correlation_id": "corr-1",
            "payload": ["invalid"],
        }
    )

    with pytest.raises(ValueError, match="payload must be a JSON object"):
        AgentMessage.from_json(body)


def test_spade_message_contains_trace_metadata() -> None:
    envelope = AgentMessage.create(
        type="probe",
        sender="sender",
        receiver="receiver",
        correlation_id="corr-42",
    )

    message = build_spade_message(envelope, performative=Performative.REQUEST)

    assert message.get_metadata("protocol") == AGENTIC_PROTOCOL
    assert message.get_metadata("performative") == "REQUEST"
    assert message.get_metadata("correlation_id") == "corr-42"
    assert message.get_metadata("message_type") == "probe"
    assert parse_spade_message(message) == envelope
