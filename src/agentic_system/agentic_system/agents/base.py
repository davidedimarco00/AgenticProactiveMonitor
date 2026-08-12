from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any

from aiohttp import web as aioweb
from spade.agent import Agent
from spade.behaviour import CyclicBehaviour, OneShotBehaviour

from ..communication import AgentMessage, Performative, build_spade_message


LOGGER = logging.getLogger("agentic_system.agents")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BaseRoleAgent(Agent):
    """Common SPADE runtime for every logical role in the MAS.

    This layer provides lifecycle, transport and per-agent observability concerns
    only. BDI deliberation and ReAct execution are added later instead of being
    simulated here.
    """

    class LifecycleBehaviour(CyclicBehaviour):
        async def run(self) -> None:
            self.agent.last_heartbeat_at = _utc_now()
            await asyncio.sleep(5)

    class SendMessageBehaviour(OneShotBehaviour):
        """Send one semantic AgentMessage through SPADE's Behaviour API."""

        def __init__(
            self,
            envelope: AgentMessage,
            performative: Performative,
        ) -> None:
            super().__init__()
            self.envelope = envelope
            self.performative = performative

        async def run(self) -> None:
            message = build_spade_message(
                self.envelope,
                performative=self.performative,
            )
            await self.send(message)
            self.agent.mark_message_sent()
            LOGGER.info(
                "%s sent %s/%s to %s correlation_id=%s",
                self.agent.display_name,
                self.performative.value,
                self.envelope.type,
                self.envelope.receiver,
                self.envelope.correlation_id,
            )

    def __init__(
        self,
        jid: str,
        password: str,
        *,
        role: str,
        display_name: str,
        health_port: int,
    ) -> None:
        super().__init__(jid, password, verify_security=False)
        self.role = role
        self.display_name = display_name
        self.health_port = health_port
        self.lifecycle_state = "created"
        self.started_at: str | None = None
        self.last_heartbeat_at: str | None = None
        self.messages_sent = 0
        self.messages_received = 0
        self.last_message_at: str | None = None
        self.xmpp_connected = False
        self.communication_ok = False
        self.last_xmpp_connected_at: str | None = None
        self.last_xmpp_disconnected_at: str | None = None
        self.last_communication_at: str | None = None

    async def setup(self) -> None:
        self.lifecycle_state = "running"
        self.started_at = _utc_now()
        self.last_heartbeat_at = self.started_at

        # setup() is invoked by SPADE only after the initial XMPP connection and
        # authentication succeed. From this point on we also observe Slixmpp's
        # connection events so a later server-side c2s disconnect is visible in
        # the per-agent health endpoint instead of being confused with is_alive().
        self._mark_xmpp_connected()
        if self.client is not None:
            self.client.add_event_handler("session_start", self._on_xmpp_session_start)
            self.client.add_event_handler("disconnected", self._on_xmpp_disconnected)

        self.add_behaviour(self.LifecycleBehaviour())

        self.web.add_get("/health", self._health_controller, template=None)
        self.web.add_get(
            "/ws/health",
            self._health_websocket_controller,
            template=None,
            raw=True,
        )
        await self.web.start(hostname="0.0.0.0", port=self.health_port)

        LOGGER.info(
            "%s connected to XMPP as %s; health endpoint on port %d",
            self.display_name,
            self.jid,
            self.health_port,
        )

    def _on_xmpp_session_start(self, _event: Any) -> None:
        self._mark_xmpp_connected()
        LOGGER.info("%s XMPP session connected", self.display_name)

    def _on_xmpp_disconnected(self, _event: Any) -> None:
        self.xmpp_connected = False
        self.communication_ok = False
        self.last_xmpp_disconnected_at = _utc_now()
        LOGGER.warning("%s XMPP session disconnected", self.display_name)

    def _mark_xmpp_connected(self) -> None:
        self.xmpp_connected = True
        self.last_xmpp_connected_at = _utc_now()

    async def _health_controller(self, _request: Any) -> dict[str, Any]:
        return self.health_snapshot()

    async def _health_websocket_controller(
        self,
        request: Any,
    ) -> aioweb.WebSocketResponse:
        websocket = aioweb.WebSocketResponse(heartbeat=15)
        await websocket.prepare(request)

        try:
            while not websocket.closed:
                await websocket.send_json(self.health_snapshot())
                await asyncio.sleep(1)
        except (ConnectionResetError, asyncio.CancelledError):
            pass

        return websocket

    async def send_agent_message(
        self,
        envelope: AgentMessage,
        *,
        performative: Performative,
        timeout: float = 5.0,
    ) -> None:
        """Send a semantic message using a one-shot SPADE behaviour."""

        behaviour = self.SendMessageBehaviour(envelope, performative)
        self.add_behaviour(behaviour)
        await behaviour.join(timeout=timeout)

        exit_code = behaviour.exit_code
        if isinstance(exit_code, BaseException):
            raise exit_code

    def mark_message_sent(self) -> None:
        self.messages_sent += 1
        self.last_message_at = _utc_now()

    def mark_message_received(self) -> None:
        self.messages_received += 1
        self.last_message_at = _utc_now()
        self.mark_communication_ok()

    def mark_communication_ok(self) -> None:
        if self.xmpp_connected:
            self.communication_ok = True
            self.last_communication_at = _utc_now()

    def mark_communication_failed(self) -> None:
        self.communication_ok = False

    def mark_stopping(self) -> None:
        self.lifecycle_state = "stopping"

    def mark_stopped(self) -> None:
        self.lifecycle_state = "stopped"
        self.xmpp_connected = False
        self.communication_ok = False

    def health_snapshot(self) -> dict[str, Any]:
        spade_alive = self.is_alive()
        if not spade_alive or not self.xmpp_connected:
            status = "OFFLINE"
        elif self.communication_ok:
            status = "ONLINE"
        else:
            status = "DEGRADED"

        return {
            "type": "agent_health",
            "role": self.role,
            "display_name": self.display_name,
            "jid": str(self.jid),
            "status": status,
            "spade_alive": spade_alive,
            "xmpp_connected": self.xmpp_connected,
            "communication_ok": self.communication_ok,
            "health_port": self.health_port,
            "started_at": self.started_at,
            "last_heartbeat_at": self.last_heartbeat_at,
            "last_xmpp_connected_at": self.last_xmpp_connected_at,
            "last_xmpp_disconnected_at": self.last_xmpp_disconnected_at,
            "last_communication_at": self.last_communication_at,
            "messages_sent": self.messages_sent,
            "messages_received": self.messages_received,
            "last_message_at": self.last_message_at,
            "timestamp": _utc_now(),
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "display_name": self.display_name,
            "jid": str(self.jid),
            "state": self.lifecycle_state,
            "started_at": self.started_at,
            "last_heartbeat_at": self.last_heartbeat_at,
            "messages_sent": self.messages_sent,
            "messages_received": self.messages_received,
            "last_message_at": self.last_message_at,
            "xmpp_connected": self.xmpp_connected,
            "communication_ok": self.communication_ok,
            "last_xmpp_connected_at": self.last_xmpp_connected_at,
            "last_xmpp_disconnected_at": self.last_xmpp_disconnected_at,
            "last_communication_at": self.last_communication_at,
            "health_port": self.health_port,
        }
