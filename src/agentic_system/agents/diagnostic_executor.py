from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from spade.behaviour import CyclicBehaviour

from ..models import DiagnosticTest, Evidence, EvidenceType
from ..protocol import MessageType, build_message, parse_payload
from .base import WorkspaceAgent

DiagnosticHandler = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class DiagnosticExecutorAgent(WorkspaceAgent):
    coordinator_jid: str
    handlers: dict[str, DiagnosticHandler]
    allowed_actions = {"inspect_container", "get_container_stats", "check_health_endpoint", "query_metrics", "query_logs"}

    class Behaviour(CyclicBehaviour):
        async def run(self) -> None:
            message = await self.receive(timeout=10)
            if message is None or message.metadata.get("message_type") != MessageType.DIAGNOSTIC_TEST_REQUEST.value:
                return
            test = DiagnosticTest.model_validate(parse_payload(message))
            if test.action not in self.agent.allowed_actions:
                result = {"success": False, "error": f"Action {test.action} is not allowlisted"}
            elif test.action not in self.agent.handlers:
                result = {"success": False, "error": f"No handler configured for {test.action}"}
            else:
                try:
                    result = {"success": True, "output": await self.agent.handlers[test.action](test.target, test.parameters)}
                except Exception as exc:  # execution boundary: return evidence, do not crash the agent
                    result = {"success": False, "error": str(exc)}
            evidence = Evidence(
                incident_id=test.incident_id,
                source_agent=str(self.agent.jid),
                evidence_type=EvidenceType.DIAGNOSTIC_TEST,
                summary=f"Diagnostic test {test.action} on {test.target}: {'succeeded' if result['success'] else 'failed'}",
                details={"test": test.model_dump(mode="json"), "result": result},
                confidence=0.9 if result["success"] else 0.3,
            )
            await self.agent.workspace.add_evidence(test.incident_id, evidence)
            await self.send(build_message(self.agent.coordinator_jid, MessageType.DIAGNOSTIC_TEST_COMPLETED, {
                "incident_id": test.incident_id,
                "test_id": test.test_id,
                "evidence_id": evidence.evidence_id,
            }))

    async def setup(self) -> None:
        self.handlers = getattr(self, "handlers", {})
        self.add_behaviour(self.Behaviour())
