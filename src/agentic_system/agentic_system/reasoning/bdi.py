from __future__ import annotations

import asyncio
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Awaitable, Callable

import agentspeak
import agentspeak.runtime
import agentspeak.stdlib


_ALLOWED_ROLES = {
    "system_engineer",
    "network_engineer",
    "application_engineer",
    "software_developer",
}
_ALLOWED_DOMAINS = {"system", "network", "application", "software"}


@dataclass(frozen=True, slots=True)
class BDITriageAssessment:
    probable_domain: str
    recommended_agent: str
    confidence: float
    rationale: str


@dataclass(frozen=True, slots=True)
class BDITriageResult:
    incident_id: str
    goal: str
    triage_intention: str
    selection_intention: str
    probable_domain: str
    primary_investigator: str
    confidence: float
    rationale: str


TriageCallback = Callable[[], Awaitable[BDITriageAssessment]]


class AgentSpeakBDIRuntime:
    """In-process Python AgentSpeak runtime used by the SPADE agents.

    The AgentSpeak interpreter owns beliefs, goals, plan selection and intentions.
    Python only hosts the interpreter and exposes bridge actions that call the
    surrounding asynchronous agent services.
    """

    def __init__(
        self,
        *,
        technical_lead_asl: str,
        action_timeout_seconds: float = 120.0,
        max_concurrency: int = 2,
    ) -> None:
        if action_timeout_seconds <= 0:
            raise ValueError("action_timeout_seconds must be greater than zero")
        if max_concurrency <= 0:
            raise ValueError("max_concurrency must be greater than zero")

        self.technical_lead_asl = technical_lead_asl
        self.action_timeout_seconds = action_timeout_seconds
        self._semaphore = asyncio.Semaphore(max_concurrency)

        plan_path = Path(technical_lead_asl)
        if not plan_path.is_file():
            raise RuntimeError(
                f"Technical Lead AgentSpeak source not found: {technical_lead_asl}"
            )
        self._technical_lead_plan = plan_path.read_text(encoding="utf-8")

    async def triage_incident(
        self,
        *,
        incident_id: str,
        anomaly: dict[str, object],
        available_agents: list[str],
        triage_callback: TriageCallback,
    ) -> BDITriageResult:
        available = self._validate_available_agents(available_agents)
        event_loop = asyncio.get_running_loop()

        async with self._semaphore:
            return await asyncio.to_thread(
                self._run_technical_lead,
                incident_id,
                dict(anomaly),
                available,
                event_loop,
                triage_callback,
            )

    def _run_technical_lead(
        self,
        incident_id: str,
        anomaly: dict[str, object],
        available_agents: list[str],
        event_loop: asyncio.AbstractEventLoop,
        triage_callback: TriageCallback,
    ) -> BDITriageResult:
        state: dict[str, object] = {
            "triage_intention": None,
            "selection_intention": None,
            "assessment": None,
            "primary_investigator": None,
        }

        actions = agentspeak.Actions(agentspeak.stdlib.actions)

        @actions.add_procedure(
            ".run_tl_triage",
            (agentspeak.runtime.Agent, agentspeak.asl_str),
        )
        def run_tl_triage(agent: agentspeak.runtime.Agent, action_incident_id: str) -> None:
            if action_incident_id != incident_id:
                raise RuntimeError("AgentSpeak triage action received a different incident_id")

            state["triage_intention"] = "triage_incident"
            future = asyncio.run_coroutine_threadsafe(triage_callback(), event_loop)
            try:
                assessment = future.result(timeout=self.action_timeout_seconds)
            except FutureTimeoutError:
                future.cancel()
                raise RuntimeError("Technical Lead triage reasoning timed out") from None

            normalized = self._validate_assessment(assessment, available_agents)
            state["assessment"] = normalized

            self._add_belief(
                agent,
                "triage_complete",
                incident_id,
            )
            self._add_belief(
                agent,
                "probable_domain",
                incident_id,
                agentspeak.Literal(normalized.probable_domain),
            )
            self._add_belief(
                agent,
                "triage_recommendation",
                incident_id,
                agentspeak.Literal(normalized.recommended_agent),
            )
            self._add_belief(
                agent,
                "triage_confidence",
                incident_id,
                normalized.confidence,
            )
            self._add_belief(
                agent,
                "triage_rationale",
                incident_id,
                normalized.rationale,
            )

        @actions.add_procedure(
            ".commit_primary_investigator",
            (agentspeak.asl_str, agentspeak.asl_str),
        )
        def commit_primary_investigator(
            action_incident_id: str,
            primary_investigator: str,
        ) -> None:
            if action_incident_id != incident_id:
                raise RuntimeError(
                    "AgentSpeak primary-investigator action received a different incident_id"
                )
            if primary_investigator not in available_agents:
                raise RuntimeError(
                    f"AgentSpeak selected unavailable specialist: {primary_investigator}"
                )
            state["selection_intention"] = "select_primary_investigator"
            state["primary_investigator"] = primary_investigator

        source = agentspeak.StringSource(
            "technical_lead_runtime.asl",
            self._build_program(incident_id, anomaly, available_agents),
        )
        environment = agentspeak.runtime.Environment()
        agent = environment.build_agent(source, actions, name="technical_lead_bdi")
        environment.run_agent(agent)

        assessment = state["assessment"]
        if not isinstance(assessment, BDITriageAssessment):
            raise RuntimeError("AgentSpeak did not execute the Technical Lead triage intention")
        if state["triage_intention"] != "triage_incident":
            raise RuntimeError("AgentSpeak did not commit to triage_incident")
        if state["selection_intention"] != "select_primary_investigator":
            raise RuntimeError("AgentSpeak did not commit to select_primary_investigator")

        primary = state["primary_investigator"]
        if not isinstance(primary, str):
            raise RuntimeError("AgentSpeak did not select a primary investigator")
        if primary != assessment.recommended_agent:
            raise RuntimeError(
                "AgentSpeak selected a specialist different from the triage recommendation"
            )

        return BDITriageResult(
            incident_id=incident_id,
            goal="manage_incident",
            triage_intention="triage_incident",
            selection_intention="select_primary_investigator",
            probable_domain=assessment.probable_domain,
            primary_investigator=primary,
            confidence=assessment.confidence,
            rationale=assessment.rationale,
        )

    def _build_program(
        self,
        incident_id: str,
        anomaly: dict[str, object],
        available_agents: list[str],
    ) -> str:
        incident = json.dumps(incident_id)
        lines = [
            f"incident({incident}).",
            f"incident_status({incident}, taken_in_charge).",
            f"anomaly_detected({incident}).",
            f"root_cause_unknown({incident}).",
        ]

        detector_id = str(anomaly.get("detector_id") or "").strip()
        if detector_id:
            lines.append(f"anomaly_detector({incident}, {json.dumps(detector_id)}).")

        grade = anomaly.get("grade")
        if isinstance(grade, (int, float)):
            lines.append(f"anomaly_grade({incident}, {float(grade)}).")

        confidence = anomaly.get("confidence")
        if isinstance(confidence, (int, float)):
            lines.append(f"anomaly_confidence({incident}, {float(confidence)}).")

        for role in available_agents:
            lines.append(f"agent_available({role}).")

        lines.append(f"!manage_incident({incident}).")
        lines.append("")
        lines.append(self._technical_lead_plan)
        return "\n".join(lines)

    @staticmethod
    def _add_belief(
        agent: agentspeak.runtime.Agent,
        functor: str,
        *args: object,
    ) -> None:
        agent.add_belief(agentspeak.Literal(functor, args), {})

    @staticmethod
    def _validate_available_agents(available_agents: list[str]) -> list[str]:
        available: list[str] = []
        for raw_role in available_agents:
            role = raw_role.strip().lower()
            if role not in _ALLOWED_ROLES:
                raise ValueError(f"Unsupported specialist role: {raw_role!r}")
            if role not in available:
                available.append(role)

        if not available:
            raise RuntimeError("No specialist agent is available for AgentSpeak deliberation")
        return available

    @staticmethod
    def _validate_assessment(
        assessment: BDITriageAssessment,
        available_agents: list[str],
    ) -> BDITriageAssessment:
        domain = assessment.probable_domain.strip().lower()
        recommendation = assessment.recommended_agent.strip().lower()
        rationale = assessment.rationale.strip()
        confidence = float(assessment.confidence)

        if domain not in _ALLOWED_DOMAINS:
            raise RuntimeError(f"Unsupported triage domain: {domain!r}")
        if recommendation not in _ALLOWED_ROLES:
            raise RuntimeError(f"Unsupported specialist role: {recommendation!r}")
        if recommendation not in available_agents:
            raise RuntimeError(
                f"Triage recommended unavailable specialist: {recommendation}"
            )
        if not 0.0 <= confidence <= 1.0:
            raise RuntimeError("Triage confidence must be between 0 and 1")
        if not rationale:
            raise RuntimeError("Triage rationale cannot be empty")

        return BDITriageAssessment(
            probable_domain=domain,
            recommended_agent=recommendation,
            confidence=confidence,
            rationale=rationale,
        )
