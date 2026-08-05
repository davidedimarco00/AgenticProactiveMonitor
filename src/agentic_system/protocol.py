from __future__ import annotations

import json
from enum import StrEnum
from typing import Any

from spade.message import Message


class MessageType(StrEnum):
    INCIDENT_OPENED = "incident_opened"
    EVIDENCE_REQUEST = "evidence_request"
    EVIDENCE_SUBMITTED = "evidence_submitted"
    HYPOTHESIS_REQUEST = "hypothesis_request"
    HYPOTHESES_SUBMITTED = "hypotheses_submitted"
    CRITIQUE_REQUEST = "critique_request"
    CRITIQUE_SUBMITTED = "critique_submitted"
    INVESTIGATION_REQUEST = "investigation_request"
    TEST_PLAN_SUBMITTED = "test_plan_submitted"
    DIAGNOSTIC_TEST_REQUEST = "diagnostic_test_request"
    DIAGNOSTIC_TEST_COMPLETED = "diagnostic_test_completed"


def build_message(to: str, message_type: MessageType, payload: dict[str, Any]) -> Message:
    message = Message(to=to)
    message.set_metadata("performative", "inform")
    message.set_metadata("message_type", message_type.value)
    message.body = json.dumps(payload)
    return message


def parse_payload(message: Message) -> dict[str, Any]:
    if not message.body:
        raise ValueError("Message body is empty")
    payload = json.loads(message.body)
    if not isinstance(payload, dict):
        raise ValueError("Message body must contain a JSON object")
    return payload
