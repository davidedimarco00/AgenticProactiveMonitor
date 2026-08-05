from __future__ import annotations

import json
from enum import StrEnum

from spade.message import Message


class MessageType(StrEnum):
    COLLECT = "collect"
    EVIDENCE_READY = "evidence_ready"
    REASON = "reason"
    DIAGNOSIS_READY = "diagnosis_ready"
    REVIEW = "review"
    REVIEW_READY = "review_ready"
    REMEDIATE = "remediate"
    REMEDIATION_READY = "remediation_ready"


def message(to: str, kind: MessageType, incident_id: str, **payload) -> Message:
    msg = Message(to=to)
    msg.set_metadata("message_type", kind.value)
    msg.body = json.dumps({"incident_id": incident_id, **payload})
    return msg


def body(msg: Message) -> dict:
    return json.loads(msg.body or "{}")
