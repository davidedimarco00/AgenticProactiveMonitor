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
_ALLOWED_REVIEW_DECISIONS = {"resolve", "operator_action_required", "request_support"}


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
class BDIReviewAssessment:
    decision: str
    confidence: float
    diagnosis_summary: str
    root_cause: str
    rationale: str
    remediation_summary: str
    remediation_steps: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BDIReviewResult:
    incident_id: str
    goal: str
    review_intention: str
    decision_intention: str
    decision: str
    confidence: float
    diagnosis_summary: str
    root_cause: str
    rationale: str
    remediation_summary: str
    remediation_steps: tuple[str, ...]


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
ReviewCallback = Callable[[], Awaitable[BDIReviewAssessment]]


class AgentSpeakBDIRuntime:
    """Single AgentSpeak bridge used by both Technical Lead and specialists.

    A Technical Lead instance loads one AgentSpeak policy containing triage,
    specialist selection and post-investigation review plans. A specialist
    instance loads the specialist policy. Python only hosts the interpreter and
    exposes narrow bridge actions; beliefs, goals and intentions remain explicit
    AgentSpeak constructs.
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

    async def review_specialist_result(
        self,
        *,
        incident_id: str,
        review_callback: ReviewCallback,
    ) -> BDIReviewResult:
        """Run the Technical Lead critic goal through the same BDI runtime."""

        if self._technical_lead_plan is None:
            raise RuntimeError("Technical Lead AgentSpeak plan is not configured")
        normalized = incident_id.strip()
        if not normalized:
            raise ValueError("Technical Lead review BDI requires incident_id")

        event_loop = asyncio.get_running_loop()
        async with self._semaphore:
            return await asyncio.to_thread(
                self._run_review,
                normalized,
                event_loop,
                review_callback,
            )

    async def accept_specialist_task(
        self,
        *,
        task_id: str,
        incident_id: str,
        role: str,
        task_type: str,
    ) -> BDISpecialistTaskResult:
        """Deliberate a received specialist task before operational ReAct starts."""

        if self._specialist_plan is None:
            raise RuntimeError("Specialist AgentSpeak plan is not configured")

        normalized_task = task_id.strip()
        normalized_incident = incident_id.strip()
        normalized_role = role.strip().lower()
        normalized_type = task_type.strip().upper()
        if not normalized_task:
            raise ValueError("Specialist BDI requires task_id")
        if not normalized_incident:
            raise ValueError("Specialist BDI requires incident_id")
        if normalized_role not in _ALLOWED_ROLES:
            raise ValueError(f"Unsupported specialist role: {role!r}")
        if normalized_type != "INVESTIGATE_INCIDENT":
            raise ValueError(f"Unsupported specialist task type: {task_type!r}")

        async with self._semaphore:
            return await asyncio.to_thread(
                self._run_specialist,
                normalized_task,
                normalized_incident,
                normalized_role,
                normalized_type,
            )

    async def deliberate_peer_help(
        self,
        *,
        help_id: str,
        incident_id: str,
        role: str,
        peer_role: str,
    ) -> BDISpecialistTaskResult:
        """Deliberate an autonomous peer-help request before the ephemeral ReAct."""

        if self._specialist_plan is None:
            raise RuntimeError("Specialist AgentSpeak plan is not configured")

        normalized_help = help_id.strip()
        normalized_incident = incident_id.strip()
        normalized_role = role.strip().lower()
        normalized_peer = peer_role.strip().lower()
        if not normalized_help:
            raise ValueError("Specialist BDI requires help_id")
        if not normalized_incident:
            raise ValueError("Specialist BDI requires incident_id")
        if normalized_role not in _ALLOWED_ROLES:
            raise ValueError(f"Unsupported specialist role: {role!r}")
        if normalized_peer not in _ALLOWED_ROLES:
            raise ValueError(f"Unsupported peer specialist role: {peer_role!r}")
        if normalized_peer == normalized_role:
            raise ValueError("A specialist cannot help itself")

        async with self._semaphore:
            return await asyncio.to_thread(
                self._run_peer_help,
                normalized_help,
                normalized_incident,
                normalized_role,
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
            self._add_belief(agent, "triage_confidence", incident_id, normalized.confidence)
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

        # The unified Technical Lead policy also contains review plans. Register
        # guarded review procedures so AgentSpeak can parse the whole policy in a
        # triage cycle without reporting unresolved actions.
        @actions.add_procedure(
            ".run_tl_review",
            (agentspeak.runtime.Agent, agentspeak.asl_str),
        )
        def review_not_available_during_triage(
            _agent: agentspeak.runtime.Agent,
            _action_incident_id: str,
        ) -> None:
            raise RuntimeError("Technical Lead review action cannot run during triage")

        @actions.add_procedure(
            ".commit_tl_review_decision",
            (agentspeak.asl_str, agentspeak.asl_str),
        )
        def review_commit_not_available_during_triage(
            _action_incident_id: str,
            _decision: str,
        ) -> None:
            raise RuntimeError("Technical Lead review commit cannot run during triage")

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

    def _run_review(
        self,
        incident_id: str,
        event_loop: asyncio.AbstractEventLoop,
        review_callback: ReviewCallback,
    ) -> BDIReviewResult:
        state: dict[str, object] = {
            "review_intention": None,
            "decision_intention": None,
            "assessment": None,
            "decision": None,
        }
        actions = agentspeak.Actions(agentspeak.stdlib.actions)

        @actions.add_procedure(
            ".run_tl_review",
            (agentspeak.runtime.Agent, agentspeak.asl_str),
        )
        def run_tl_review(agent: agentspeak.runtime.Agent, action_incident_id: str) -> None:
            if action_incident_id != incident_id:
                raise RuntimeError("AgentSpeak review action received a different incident_id")
            state["review_intention"] = "review_specialist_result"
            future = asyncio.run_coroutine_threadsafe(review_callback(), event_loop)
            try:
                assessment = future.result(timeout=self.action_timeout_seconds)
            except FutureTimeoutError:
                future.cancel()
                raise RuntimeError("Technical Lead review reasoning timed out") from None
            if assessment.decision not in _ALLOWED_REVIEW_DECISIONS:
                raise RuntimeError(
                    f"Unsupported Technical Lead review decision: {assessment.decision}"
                )
            state["assessment"] = assessment
            self._add_belief(agent, "review_complete", incident_id)
            self._add_belief(
                agent,
                "review_decision",
                incident_id,
                agentspeak.Literal(assessment.decision),
            )

        @actions.add_procedure(
            ".commit_tl_review_decision",
            (agentspeak.asl_str, agentspeak.asl_str),
        )
        def commit_tl_review_decision(action_incident_id: str, decision: str) -> None:
            if action_incident_id != incident_id:
                raise RuntimeError("AgentSpeak review commit received a different incident_id")
            if decision not in _ALLOWED_REVIEW_DECISIONS:
                raise RuntimeError(f"AgentSpeak committed invalid review decision: {decision}")
            state["decision_intention"] = "commit_review_decision"
            state["decision"] = decision

        # The same policy contains triage plans. Guarded triage procedures keep
        # the complete policy valid during a review-only deliberation cycle.
        @actions.add_procedure(
            ".run_tl_triage",
            (agentspeak.runtime.Agent, agentspeak.asl_str),
        )
        def triage_not_available_during_review(
            _agent: agentspeak.runtime.Agent,
            _action_incident_id: str,
        ) -> None:
            raise RuntimeError("Technical Lead triage action cannot run during review")

        @actions.add_procedure(
            ".commit_primary_investigator",
            (agentspeak.asl_str, agentspeak.asl_str),
        )
        def triage_commit_not_available_during_review(
            _action_incident_id: str,
            _primary_investigator: str,
        ) -> None:
            raise RuntimeError("Technical Lead triage commit cannot run during review")

        source = agentspeak.StringSource(
            "technical_lead_review_runtime.asl",
            self._build_review_program(incident_id),
        )
        environment = agentspeak.runtime.Environment()
        agent = environment.build_agent(source, actions, name="technical_lead_review_bdi")
        environment.run_agent(agent)

        assessment = state["assessment"]
        if not isinstance(assessment, BDIReviewAssessment):
            raise RuntimeError("AgentSpeak did not execute Technical Lead review reasoning")
        if state["review_intention"] != "review_specialist_result":
            raise RuntimeError("AgentSpeak did not commit to review_specialist_result")
        if state["decision_intention"] != "commit_review_decision":
            raise RuntimeError("AgentSpeak did not commit the review decision")
        if state["decision"] != assessment.decision:
            raise RuntimeError("AgentSpeak review decision differs from the critic assessment")

        return BDIReviewResult(
            incident_id=incident_id,
            goal="review_investigation",
            review_intention="review_specialist_result",
            decision_intention="commit_review_decision",
            decision=assessment.decision,
            confidence=assessment.confidence,
            diagnosis_summary=assessment.diagnosis_summary,
            root_cause=assessment.root_cause,
            rationale=assessment.rationale,
            remediation_summary=assessment.remediation_summary,
            remediation_steps=assessment.remediation_steps,
        )

    def _run_specialist(
        self,
        task_id: str,
        incident_id: str,
        role: str,
        task_type: str,
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

        # The specialist policy also contains the peer-help plan. Register a
        # guarded stub so AgentSpeak parses the whole policy in a normal task
        # cycle without reporting an unresolved action.
        @actions.add_procedure(
            ".commit_peer_help_investigation",
            (
                agentspeak.runtime.Agent,
                agentspeak.asl_str,
                agentspeak.asl_str,
                agentspeak.asl_str,
            ),
        )
        def peer_help_not_available_during_task(
            _agent: agentspeak.runtime.Agent,
            _help_id: str,
            _incident_id: str,
            _peer_role: str,
        ) -> None:
            raise RuntimeError("Peer-help intention cannot run during a normal task cycle")

        source = agentspeak.StringSource(
            "specialist_runtime.asl",
            self._build_specialist_program(task_id, incident_id, role, task_type),
        )
        environment = agentspeak.runtime.Environment()
        agent = environment.build_agent(source, actions, name=f"{role}_bdi")
        environment.run_agent(agent)

        if state["acceptance_intention"] != "accept_task":
            raise RuntimeError("Specialist AgentSpeak did not commit to accept_task")
        if state["investigation_intention"] != "investigate_incident":
            raise RuntimeError("Specialist AgentSpeak did not commit to investigate_incident")

        return BDISpecialistTaskResult(
            task_id=task_id,
            incident_id=incident_id,
            role=role,
            goal="handle_investigation_task",
            acceptance_intention="accept_task",
            investigation_intention="investigate_incident",
        )

    def _run_peer_help(
        self,
        help_id: str,
        incident_id: str,
        role: str,
        peer_role: str,
    ) -> BDISpecialistTaskResult:
        state: dict[str, object] = {
            "acceptance_intention": None,
            "investigation_intention": None,
        }
        actions = agentspeak.Actions(agentspeak.stdlib.actions)

        @actions.add_procedure(
            ".commit_peer_help_investigation",
            (agentspeak.runtime.Agent, agentspeak.asl_str, agentspeak.asl_str, agentspeak.asl_str),
        )
        def commit_peer_help_investigation(
            agent: agentspeak.runtime.Agent,
            action_help_id: str,
            action_incident_id: str,
            action_peer_role: str,
        ) -> None:
            if action_help_id != help_id or action_incident_id != incident_id:
                raise RuntimeError("Peer-help AgentSpeak used a different identity")
            if action_peer_role != peer_role:
                raise RuntimeError("Peer-help AgentSpeak used an unexpected requester")
            state["acceptance_intention"] = "accept_peer_help"
            state["investigation_intention"] = "provide_peer_help"
            self._add_belief(agent, "peer_help_committed", help_id)

        # Guarded stubs for the normal-task plan so the whole specialist policy
        # parses during a peer-help cycle.
        @actions.add_procedure(
            ".accept_specialist_task",
            (
                agentspeak.runtime.Agent,
                agentspeak.asl_str,
                agentspeak.asl_str,
                agentspeak.asl_str,
            ),
        )
        def accept_not_available_during_peer_help(
            _agent: agentspeak.runtime.Agent,
            _task_id: str,
            _incident_id: str,
            _role: str,
        ) -> None:
            raise RuntimeError("Task acceptance cannot run during a peer-help cycle")

        @actions.add_procedure(
            ".commit_specialist_investigation",
            (agentspeak.asl_str, agentspeak.asl_str),
        )
        def commit_not_available_during_peer_help(
            _task_id: str,
            _incident_id: str,
        ) -> None:
            raise RuntimeError("Specialist investigation commit cannot run during a peer-help cycle")

        incident = json.dumps(incident_id)
        help_literal = json.dumps(help_id)
        program = "\n".join(
            [
                f"peer_help_requested({help_literal}, {incident}, {peer_role}).",
                f"self_role({role}).",
                f"root_cause_unknown({incident}).",
                f"!provide_peer_help({help_literal}, {incident}).",
                "",
                self._specialist_plan,
            ]
        )
        source = agentspeak.StringSource("specialist_peer_help_runtime.asl", program)
        environment = agentspeak.runtime.Environment()
        agent = environment.build_agent(source, actions, name=f"{role}_peer_help_bdi")
        environment.run_agent(agent)

        if state["investigation_intention"] != "provide_peer_help":
            raise RuntimeError("Specialist AgentSpeak did not commit to provide_peer_help")

        return BDISpecialistTaskResult(
            task_id=help_id,
            incident_id=incident_id,
            role=role,
            goal="provide_peer_help",
            acceptance_intention="accept_peer_help",
            investigation_intention="provide_peer_help",
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

        lines.extend(
            [
                f"!manage_incident({incident}).",
                "",
                self._technical_lead_plan,
            ]
        )
        return "\n".join(lines)

    def _build_review_program(self, incident_id: str) -> str:
        if self._technical_lead_plan is None:
            raise RuntimeError("Technical Lead AgentSpeak plan is not configured")
        incident = json.dumps(incident_id)
        return "\n".join(
            [
                f"incident({incident}).",
                f"incident_status({incident}, under_analysis).",
                f"specialist_result_received({incident}).",
                f"!review_investigation_result({incident}).",
                "",
                self._technical_lead_plan,
            ]
        )

    def _build_specialist_program(
        self,
        task_id: str,
        incident_id: str,
        role: str,
        task_type: str,
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
            f"!handle_investigation_task({task}, {incident}).",
            "",
            self._specialist_plan,
        ]
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


# Backward-compatible public name for callers/tests that used the dedicated
# review runtime. It now resolves to the same unified Technical Lead BDI bridge.
TechnicalLeadReviewBDIRuntime = AgentSpeakBDIRuntime
