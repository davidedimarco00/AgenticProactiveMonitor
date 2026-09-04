from __future__ import annotations

import logging

from spade.behaviour import CyclicBehaviour
from spade.template import Template

from .base import BaseAgent
from .messages import AGENTIC_PROTOCOL, AgentMessage, Performative, parse_spade_message
from .specialist import (
    INVESTIGATION_TASK_EXECUTION_FAILED_TYPE,
    INVESTIGATION_TASK_RESULT_TYPE,
)


LOGGER = logging.getLogger("agentic_system.agents.investigation_results")


class InvestigationResultInboxBehaviour(CyclicBehaviour):
    """Capture asynchronous specialist outcomes on the Technical Lead agent."""

    async def run(self) -> None:
        message = await self.receive(timeout=1)
        if message is None:
            return

        try:
            envelope = parse_spade_message(message)
            task_id = str(envelope.payload.get("task_id") or "").strip()
            incident_id = str(envelope.payload.get("incident_id") or "").strip()
            if not task_id or not incident_id:
                raise ValueError("Specialist result is missing task_id or incident_id")

            self.agent.mark_message_received()
            self.agent._investigation_result_messages[task_id] = envelope
            self.agent.investigation_results_received += 1
            self.agent.last_investigation_result = envelope
            LOGGER.warning(
                "Technical Lead received specialist outcome type=%s task=%s incident=%s from=%s",
                envelope.type,
                task_id,
                incident_id,
                envelope.sender,
            )
        except Exception as exc:
            self.agent.last_investigation_result_error = str(exc)
            LOGGER.exception("Technical Lead could not capture specialist outcome: %s", exc)


def install_investigation_result_inbox(technical_lead: BaseAgent) -> None:
    """Install result listeners once after the Technical Lead has started."""

    if getattr(technical_lead, "_investigation_result_inbox_installed", False):
        return

    technical_lead._investigation_result_messages = {}
    technical_lead.investigation_results_received = 0
    technical_lead.last_investigation_result = None
    technical_lead.last_investigation_result_error = None

    for performative, message_type in (
        (Performative.INFORM, INVESTIGATION_TASK_RESULT_TYPE),
        (Performative.FAILURE, INVESTIGATION_TASK_EXECUTION_FAILED_TYPE),
    ):
        template = Template()
        template.set_metadata("protocol", AGENTIC_PROTOCOL)
        template.set_metadata("performative", performative.value)
        template.set_metadata("message_type", message_type)
        technical_lead.add_behaviour(InvestigationResultInboxBehaviour(), template)

    technical_lead._investigation_result_inbox_installed = True


def pop_investigation_result(
    technical_lead: BaseAgent,
    task_id: str,
) -> AgentMessage | None:
    messages = getattr(technical_lead, "_investigation_result_messages", None)
    if not isinstance(messages, dict):
        return None
    value = messages.pop(task_id, None)
    return value if isinstance(value, AgentMessage) else None
