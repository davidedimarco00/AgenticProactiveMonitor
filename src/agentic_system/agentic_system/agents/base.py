from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any

from aiohttp import web as aioweb
from spade.behaviour import CyclicBehaviour, OneShotBehaviour
from spade_llm import LLMAgent
from spade_llm.mcp import MCPServerConfig
from spade_llm.providers import LLMProvider

from ..communication import AgentMessage, Performative, build_spade_message


LOGGER = logging.getLogger("agentic_system.agents")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BaseAgent(LLMAgent):
    """Common SPADE-LLM base class for every project agent.

    SPADE-LLM owns the LLM provider, conversation context, interaction memory,
    tool execution and MCP integration. This project layer only adds role
    metadata, XMPP observability and health endpoints.
    """

    class LifecycleBehaviour(CyclicBehaviour):
        async def run(self) -> None:
            self.agent.last_heartbeat_at = _utc_now()
            await asyncio.sleep(5)

    class SendMessageBehaviour(OneShotBehaviour):
        """Send one project control message through SPADE/XMPP."""

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
        provider: LLMProvider,
        mcp_servers: list[MCPServerConfig],
        system_prompt: str,
        interaction_memory_path: str,
    ) -> None:
        super().__init__(
            jid=jid,
            password=password,
            provider=provider,
            system_prompt=system_prompt,
            mcp_servers=mcp_servers,
            interaction_memory=(True, interaction_memory_path),
            verify_security=False,
        )
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
        self._tools_before_mcp = len(self.tools)
        self.mcp_tool_count = 0

    async def setup(self) -> None:
        # LLMAgent.setup() registers the SPADE-LLM behaviour and discovers MCP
        # tools. It must run before project-specific behaviours are installed.
        await super().setup()

        self.mcp_tool_count = len(self.tools) - self._tools_before_mcp
        if self.mcp_servers and self.mcp_tool_count <= 0:
            raise RuntimeError(
                f"{self.display_name} did not discover any tools from the configured MCP server"
            )

        self.lifecycle_state = "running"
        self.started_at = _utc_now()
        self.last_heartbeat_at = self.started_at

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
            "%s connected as SPADE-LLM agent %s with %d MCP tools; health endpoint on port %d",
            self.display_name,
            self.jid,
            self.mcp_tool_count,
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
        """Send a project control message using a one-shot SPADE behaviour."""

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
            "framework": "SPADE-LLM",
            "llm_agent": True,
            "provider_model": self.provider.model,
            "context_enabled": self.context is not None,
            "interaction_memory_enabled": self.interaction_memory is not None,
            "mcp_server_count": len(self.mcp_servers),
            "mcp_tool_count": self.mcp_tool_count,
            "tool_names": [tool.name for tool in self.tools],
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
            "framework": "SPADE-LLM",
            "llm_agent": True,
            "provider_model": self.provider.model,
            "context_enabled": self.context is not None,
            "interaction_memory_enabled": self.interaction_memory is not None,
            "mcp_server_count": len(self.mcp_servers),
            "mcp_tool_count": self.mcp_tool_count,
            "tool_names": [tool.name for tool in self.tools],
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
