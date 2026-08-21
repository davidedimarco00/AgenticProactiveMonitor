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


@dataclass(frozen=True, slots=True)
class BDISpecialistTaskResult:
    """Specialist BDI decision after receiving one durable investigation task."""

    task_id: str
    incident_id: str
    role: str
    goal: str
    acceptance_intention: str
    investigation_intention: str


TriageCallback = Callable[[], Awaitable[BDITriageAssessment]]


class AgentSpeakBDIRuntime:
    """In-process AgentSpeak runtime used by the SPADE agents.

    Each SPADE agent owns its own instance of this bridge. AgentSpeak owns
    beliefs, goals, plan selection and intentions; Python only hosts the
    interpreter and exposes narrow bridge actions to the asynchronous runtime.
    """

    def __init__(
        self,
        *,
        technical_lead_asl: str | None = None,
        specialist_asl: str | None = None,
        action_timeout_seconds: float = 120.0,
        max_concurrency: int = 2,
    ) -> None:
        if action_timeout_seconds <= 0:
            raise ValueError("action_timeout_seconds must be greater than zero")
        if max_concurrency <= 0:
            raise ValueError("max_concurrency must be greater than zero")
        if not technical_lead_asl and not specialist_asl:
            raise ValueError("At least one AgentSpeak plan path must be configured")

        self.technical_lead_asl = technical_lead_asl
        self.specialist_asl = specialist_asl
        self.action_timeout_seconds = action_timeout_seconds
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._technical_lead_plan = self._load_plan(
            technical_lead_asl,
            label="Technical Lead",
        )
        self._specialist_plan = self._load_plan(
            specialist_asl,
            label="Specialist",
        )

    @staticmethod
    def _load_plan(path: str | None, *, label: str) -> str | None:
        if path is None:
            return None
        plan_path = Path(path)
        if not plan_path.is_file():
            raise RuntimeError(f"{label} AgentSpeak source not found: {path}")
        return plan_path.read_text(encoding="utf-8")

    async def triage_incident(
        self,
        *,
        incident_id: str,
        anomaly: dict[str, object],
        available_agents: list[str],
        triage_callback: TriageCallback,
    ) -> BDITriageResult:
        if self._technical_lead_plan is None:
            raise RuntimeError("Technical Lead AgentSpeak plan is not configured")

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

    async def accept_specialist_task(
        self,
        *,
        task_id: str,
        incident_id: str,
        role: str,
        task_type: str,
        peer_role: str | None = None,
    ) -> BDISpecialistTaskResult:
        """Deliberate a received specialist task before operational ReAct starts.

        A normal task produces an `investigate_incident` intention. When an
        authorized peer context has already been received over XMPP, AgentSpeak
        also receives a `peer_context_available` belief and commits the distinct
        `investigate_with_peer` intention. ReAct executes either intention using
        operational MCP/RAG evidence.
        """

        if self._specialist_plan is None:
            raise RuntimeError("Specialist AgentSpeak plan is not configured")

        normalized_task = task_id.strip()
        normalized_incident = incident_id.strip()
        normalized_role = role.strip().lower()
        normalized_type = task_type.strip().upper()
        normalized_peer = peer_role.strip().lower() if peer_role else None
        if not normalized_task:
            raise ValueError("Specialist BDI requires task_id")
        if not normalized_incident:
            raise ValueError("Specialist BDI requires incident_id")
        if normalized_role not in _ALLOWED_ROLES:
            raise ValueError(f"Unsupported specialist role: {role!r}")
        if normalized_type != "INVESTIGATE_INCIDENT":
            raise ValueError(f"Unsupported specialist task type: {task_type!r}")
        if normalized_peer is not None:
            if normalized_peer not in _ALLOWED_ROLES:
                raise ValueError(f"Unsupported peer specialist role: {peer_role!r}")
            if normalized_peer == normalized_role:
                raise ValueError("A specialist cannot collaborate with itself")

        async with self._semaphore:
            return await asyncio.to_thread(
                self._run_specialist,
                normalized_task,
                normalized_incident,
                normalized_role,
                normalized_type,
                normalized_peer,
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

            self._add_belief(agent, "triage_complete", incident_id)
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
            self._add_belief(agent, "triage_rationale", incident_id, normalized.rationale)

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
            self._build_technical_lead_program(incident_id, anomaly, available_agents),
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

    def _run_specialist(
        self,
        task_id: str,
        incident_id: str,
        role: str,
        task_type: str,
        peer_role: str | None,
    ) -> BDISpecialistTaskResult:
        state: dict[str, object] = {
            "acceptance_intention": None,
            "investigation_intention": None,
        }
        actions = agentspeak.Actions(agentspeak.stdlib.actions)

        @actions.add_procedure(
            ".accept_specialist_task",
            (
                agentspeak.runtime.Agent,
                agentspeak.asl_str,
                agentspeak.asl_str,
                agentspeak.asl_str,
            ),
        )
        def accept_specialist_task(
            agent: agentspeak.runtime.Agent,
            action_task_id: str,
            action_incident_id: str,
            action_role: str,
        ) -> None:
            if action_task_id != task_id or action_incident_id != incident_id:
                raise RuntimeError("Specialist AgentSpeak received a different task identity")
            if action_role != role:
                raise RuntimeError("Specialist AgentSpeak task was assigned to another role")
            state["acceptance_intention"] = "accept_task"
            self._add_belief(agent, "task_accepted", task_id)

        @actions.add_procedure(
            ".commit_specialist_investigation",
            (agentspeak.asl_str, agentspeak.asl_str),
        )
        def commit_specialist_investigation(
            action_task_id: str,
            action_incident_id: str,
        ) -> None:
            if action_task_id != task_id or action_incident_id != incident_id:
                raise RuntimeError("Specialist AgentSpeak committed a different task identity")
            state["investigation_intention"] = "investigate_incident"

        @actions.add_procedure(
            ".commit_collaborative_investigation",
            (agentspeak.asl_str, agentspeak.asl_str, agentspeak.asl_str),
        )
        def commit_collaborative_investigation(
            action_task_id: str,
            action_incident_id: str,
            action_peer_role: str,
        ) -> None:
            if action_task_id != task_id or action_incident_id != incident_id:
                raise RuntimeError("Collaborative AgentSpeak intention used a different task")
            if peer_role is None or action_peer_role != peer_role:
                raise RuntimeError("Collaborative AgentSpeak intention used an unexpected peer")
            state["investigation_intention"] = "investigate_with_peer"

        source = agentspeak.StringSource(
            "specialist_runtime.asl",
            self._build_specialist_program(
                task_id,
                incident_id,
                role,
                task_type,
                peer_role,
            ),
        )
        environment = agentspeak.runtime.Environment()
        agent = environment.build_agent(source, actions, name=f"{role}_bdi")
        environment.run_agent(agent)

        if state["acceptance_intention"] != "accept_task":
            raise RuntimeError("Specialist AgentSpeak did not commit to accept_task")
        expected_intention = "investigate_with_peer" if peer_role else "investigate_incident"
        if state["investigation_intention"] != expected_intention:
            raise RuntimeError(
                f"Specialist AgentSpeak did not commit to {expected_intention}"
            )

        return BDISpecialistTaskResult(
            task_id=task_id,
            incident_id=incident_id,
            role=role,
            goal="handle_investigation_task",
            acceptance_intention="accept_task",
            investigation_intention=expected_intention,
        )

    def _build_technical_lead_program(
        self,
        incident_id: str,
        anomaly: dict[str, object],
        available_agents: list[str],
    ) -> str:
        if self._technical_lead_plan is None:
            raise RuntimeError("Technical Lead AgentSpeak plan is not configured")
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

        for available_role in available_agents:
            lines.append(f"agent_available({available_role}).")

        lines.append(f"!manage_incident({incident}).")
        lines.append("")
        lines.append(self._technical_lead_plan)
        return "\n".join(lines)

    def _build_specialist_program(
        self,
        task_id: str,
        incident_id: str,
        role: str,
        task_type: str,
        peer_role: str | None,
    ) -> str:
        if self._specialist_plan is None:
            raise RuntimeError("Specialist AgentSpeak plan is not configured")
        task = json.dumps(task_id)
        incident = json.dumps(incident_id)
        lines = [
            f"task({task}).",
            f"task_type({task}, {task_type.lower()}).",
            f"task_state({task}, dispatched).",
            f"incident({incident}).",
            f"root_cause_unknown({incident}).",
            f"assigned_to({task}, {role}).",
            f"self_role({role}).",
        ]
        if peer_role:
            lines.append(f"peer_context_available({task}, {peer_role}).")
        lines.extend(
            [
                f"!handle_investigation_task({task}, {incident}).",
                "",
                self._specialist_plan,
            ]
        )
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
