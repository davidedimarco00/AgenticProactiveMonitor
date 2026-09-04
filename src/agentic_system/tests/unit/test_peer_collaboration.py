import asyncio
from types import SimpleNamespace

import pytest

from agentic_system.agents.collaboration import (
    PEER_HELP_REQUEST_TYPE,
    PEER_HELP_RESPONSE_TYPE,
    PeerHelpResponseBehaviour,
    request_peer_help,
)
from agentic_system.agents.messages import AgentMessage, Performative


class _FakeAgent:
    def __init__(self) -> None:
        self.jid = "system-engineer@xmpp"
        self._pending_peer_help: dict[str, asyncio.Future] = {}
        self.sent: list[tuple[AgentMessage, Performative]] = []
        self.messages_received = 0

    def mark_message_received(self) -> None:
        self.messages_received += 1

    async def send_agent_message(self, envelope, *, performative, timeout: float = 5.0):
        self.sent.append((envelope, performative))


def test_request_peer_help_sends_a_direct_request_and_returns_the_response() -> None:
    agent = _FakeAgent()

    async def scenario() -> dict:
        task = asyncio.create_task(
            request_peer_help(
                agent,
                receiver="application-engineer@xmpp",
                incident_id="INC-1",
                requester_role="system_engineer",
                reason="Correlate CPU with application retries.",
                requester_result={"summary": "CPU high"},
                severity="MEDIUM",
                entity="processing-service",
                anomaly={"detector_id": "CPU-processing-service"},
                timeout=5.0,
            )
        )
        await asyncio.sleep(0)  # let the request go out
        envelope, performative = agent.sent[0]
        assert performative == Performative.REQUEST
        assert envelope.type == PEER_HELP_REQUEST_TYPE
        assert envelope.payload["hop"] == 0
        assert envelope.payload["requester_role"] == "system_engineer"

        # Simulate the peer's direct response.
        future = agent._pending_peer_help[envelope.correlation_id]
        future.set_result({"agent_role": "application_engineer", "confidence": 0.7})
        return await task

    result = asyncio.run(scenario())
    assert result["agent_role"] == "application_engineer"


def test_peer_help_response_behaviour_rejects_a_failure_payload() -> None:
    agent = _FakeAgent()
    loop = asyncio.new_event_loop()
    try:
        future = loop.create_future()
        agent._pending_peer_help["corr-1"] = future

        envelope = AgentMessage.create(
            type=PEER_HELP_RESPONSE_TYPE,
            sender="application-engineer@xmpp",
            receiver="system-engineer@xmpp",
            correlation_id="corr-1",
            payload={"error": "peer ReAct failed", "responder_role": "application_engineer"},
        )
        message = SimpleNamespace(
            body=envelope.to_json(),
            get_metadata=lambda key: Performative.FAILURE.value if key == "performative" else None,
        )

        behaviour = PeerHelpResponseBehaviour()
        behaviour.agent = agent
        behaviour.receive = _make_receive(message)  # type: ignore[method-assign]

        loop.run_until_complete(behaviour.run())
        assert future.done()
        with pytest.raises(RuntimeError, match="peer ReAct failed"):
            future.result()
    finally:
        loop.close()


def _make_receive(message):
    delivered = {"done": False}

    async def _receive(timeout: float = 1.0):
        if delivered["done"]:
            return None
        delivered["done"] = True
        return message

    return _receive
