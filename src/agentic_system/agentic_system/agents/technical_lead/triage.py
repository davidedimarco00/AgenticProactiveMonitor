from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from spade_llm.context import ContextManager
from spade_llm.providers.base_provider import BaseLLMProvider

from .commands import IncidentAssignment


_ALLOWED_DOMAINS = {"system", "network", "application", "software"}
_ALLOWED_AGENTS = {
    "system_engineer",
    "network_engineer",
    "application_engineer",
    "software_developer",
}


@dataclass(frozen=True, slots=True)
class TriageAssessment:
    probable_domain: str
    recommended_agent: str
    confidence: float
    rationale: str


@dataclass(frozen=True, slots=True)
class TechnicalLeadTriageDecision:
    incident_id: str
    probable_domain: str
    primary_investigator: str
    confidence: float
    rationale: str
    bdi_goal: str
    bdi_triage_intention: str
    bdi_intention: str


class TechnicalLeadTriageReasoner:
    """Gemma-only first analysis for delegation, explicitly excluding diagnosis."""

    SYSTEM_PROMPT = """You are the Technical Lead of an IT monitoring multi-agent team.
Your task in this stage is TRIAGE ONLY. Do not diagnose the incident, do not claim a
root cause, and do not propose remediation. Identify the most plausible technical
domain to investigate first and recommend exactly one available specialist.

Specialist responsibilities:
- system_engineer: host/container resources, CPU, memory, disk, runtime state.
- network_engineer: latency, connections, communication and network behaviour.
- application_engineer: application health, logs, service errors and runtime behaviour.
- software_developer: implementation/design behaviour and code-level investigation.

Return only a JSON object with these fields:
probable_domain: one of system, network, application, software
recommended_agent: one available specialist role
confidence: number from 0 to 1
rationale: short explanation of why this specialist should investigate first
Never include diagnosis, root_cause, remediation, commands or corrective actions."""

    def __init__(self, provider: BaseLLMProvider) -> None:
        self.provider = provider

    async def assess(
        self,
        assignment: IncidentAssignment,
        *,
        detector_context: dict[str, Any],
        available_agents: list[str],
    ) -> TriageAssessment:
        available = [role for role in available_agents if role in _ALLOWED_AGENTS]
        if not available:
            raise RuntimeError("No specialist agent is available for Technical Lead triage")

        conversation_id = f"technical-lead-triage:{assignment.incident_id}"
        context = ContextManager(system_prompt=self.SYSTEM_PROMPT)
        context.add_message_dict(
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "incident_id": assignment.incident_id,
                        "status": assignment.status,
                        "severity": assignment.severity,
                        "entity": assignment.entity,
                        "anomaly": assignment.anomaly,
                        "detector": detector_context,
                        "available_agents": available,
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            },
            conversation_id,
        )

        # HybridLLMProvider routes tool-less requests to Gemma, preserving the
        # project split: Gemma reasons while Qwen remains the tool-calling model.
        response = await self.provider.get_llm_response(
            context,
            tools=None,
            conversation_id=conversation_id,
        )
        raw_text = str(response.get("text") or "").strip()
        assessment = self._parse_response(raw_text)

        if assessment.recommended_agent not in available:
            raise RuntimeError(
                "Technical Lead triage recommended an unavailable specialist: "
                f"{assessment.recommended_agent}"
            )
        return assessment

    @staticmethod
    def _parse_response(raw_text: str) -> TriageAssessment:
        text = raw_text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Technical Lead triage did not return valid JSON: {raw_text[:300]!r}"
            ) from exc

        if not isinstance(payload, dict):
            raise RuntimeError("Technical Lead triage response must be a JSON object")

        forbidden = {"diagnosis", "root_cause", "remediation"} & set(payload)
        if forbidden:
            raise RuntimeError(
                "Technical Lead triage attempted to produce diagnostic content: "
                + ", ".join(sorted(forbidden))
            )

        domain = str(payload.get("probable_domain") or "").strip().lower()
        agent = str(payload.get("recommended_agent") or "").strip().lower()
        rationale = str(payload.get("rationale") or "").strip()
        try:
            confidence = float(payload.get("confidence"))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Technical Lead triage confidence is invalid") from exc

        if domain not in _ALLOWED_DOMAINS:
            raise RuntimeError(f"Technical Lead triage returned invalid domain: {domain!r}")
        if agent not in _ALLOWED_AGENTS:
            raise RuntimeError(f"Technical Lead triage returned invalid agent: {agent!r}")
        if not 0.0 <= confidence <= 1.0:
            raise RuntimeError("Technical Lead triage confidence must be between 0 and 1")
        if not rationale:
            raise RuntimeError("Technical Lead triage rationale cannot be empty")

        return TriageAssessment(
            probable_domain=domain,
            recommended_agent=agent,
            confidence=confidence,
            rationale=rationale,
        )
