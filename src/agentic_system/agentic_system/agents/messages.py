from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
import json
from typing import Any
from uuid import uuid4

from spade.message import Message


AGENTIC_PROTOCOL = "agentic-proactive-monitor/v1"


class Performative(StrEnum):
    REQUEST = "REQUEST"
    AGREE = "AGREE"
    INFORM = "INFORM"
    REFUSE = "REFUSE"
    FAILURE = "FAILURE"


@dataclass(frozen=True)
class AgentMessage:
    type: str
    sender: str
    receiver: str
    timestamp: str
    correlation_id: str
    payload: dict[str, Any]

    @classmethod
    def create(
        cls,
        *,
        type: str,
        sender: str,
        receiver: str,
        payload: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> "AgentMessage":
        return cls(
            type=type,
            sender=sender,
            receiver=receiver,
            timestamp=datetime.now(timezone.utc).isoformat(),
            correlation_id=correlation_id or str(uuid4()),
            payload=dict(payload or {}),
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_json(cls, body: str) -> "AgentMessage":
        raw = json.loads(body)
        if not isinstance(raw, dict):
            raise ValueError("Agent message body must be a JSON object")

        required = {
            "type",
            "sender",
            "receiver",
            "timestamp",
            "correlation_id",
            "payload",
        }
        missing = required - set(raw)
        if missing:
            raise ValueError(
                "Agent message is missing fields: " + ", ".join(sorted(missing))
            )

        payload = raw["payload"]
        if not isinstance(payload, dict):
            raise ValueError("Agent message payload must be a JSON object")

        return cls(
            type=str(raw["type"]),
            sender=str(raw["sender"]),
            receiver=str(raw["receiver"]),
            timestamp=str(raw["timestamp"]),
            correlation_id=str(raw["correlation_id"]),
            payload=payload,
        )


def build_spade_message(
    envelope: AgentMessage,
    *,
    performative: Performative,
) -> Message:
    message = Message(to=envelope.receiver)
    message.body = envelope.to_json()
    message.set_metadata("protocol", AGENTIC_PROTOCOL)
    message.set_metadata("performative", performative.value)
    message.set_metadata("correlation_id", envelope.correlation_id)
    message.set_metadata("message_type", envelope.type)
    return message


def parse_spade_message(message: Message) -> AgentMessage:
    if message.body is None:
        raise ValueError("SPADE message body is empty")
    return AgentMessage.from_json(message.body)
