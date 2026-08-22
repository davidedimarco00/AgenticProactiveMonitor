from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from spade_llm.context import ContextManager
from spade_llm.providers.base_provider import BaseLLMProvider


LOGGER = logging.getLogger("agentic_system.agents.review")
_ALLOWED_DECISIONS = {"resolve", "operator_action_required", "request_support"}
_ALLOWED_SUPPORT_DOMAINS = {"system", "network", "application", "software"}
_SUPPORT_ROLE_BY_DOMAIN = {
    "system": "system_engineer",
    "network": "network_engineer",
    "application": "application_engineer",
    "software": "software_developer",
}
_MAX_REVIEW_ATTEMPTS = 3
_MAX_SUPPORT_ROUNDS = 1

ReviewDecision = Literal["resolve", "operator_action_required", "request_support"]
SupportDomain = Literal["system", "network", "application", "software"]
CommandType = Literal["verification", "remediation"]


class _RemediationStep(BaseModel):
    """One operator-facing advisory action produced by the Technical Lead."""

    title: str
    target: str
    command_type: CommandType
    command: str
    purpose: str
    expected_result: str
    what_to_verify: str

    @field_validator(
        "title",
        "target",
        "command",
        "purpose",
        "expected_result",
        "what_to_verify",
    )
    @classmethod
    def _not_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("remediation step fields cannot be empty")
        return normalized


class _TechnicalLeadReviewOutput(BaseModel):
    """Structured critic decision returned by the Technical Lead model."""

    decision: ReviewDecision
    confidence: float = Field(ge=0.0, le=1.0)
    diagnosis_summary: str
    root_cause: str
    rationale: str
    remediation_summary: str
    remediation_steps: list[_RemediationStep]
    support_domain: SupportDomain | None = None
    support_reason: str | None = None


@dataclass(frozen=True, slots=True)
class TechnicalLeadReviewAssessment:
    decision: str
    confidence: float
    diagnosis_summary: str
    root_cause: str
    rationale: str
    remediation_summary: str
    remediation_steps: tuple[dict[str, str], ...]
    support_domain: str | None = None
    support_reason: str | None = None


@dataclass(frozen=True, slots=True)
class TechnicalLeadReviewDecision:
    incident_id: str
    decision: str
    confidence: float
    diagnosis_summary: str
    root_cause: str
    rationale: str
    remediation_summary: str
    remediation_steps: tuple[dict[str, str], ...]
    support_domain: str | None
    support_reason: str | None
    bdi_goal: str
    bdi_review_intention: str
    bdi_decision_intention: str


class TechnicalLeadReviewReasoner:
    """Gemma critic with bounded collaboration and operator-ready remediation."""

    SYSTEM_PROMPT = """You are the Technical Lead of an IT monitoring multi-agent team.
Review only the supplied specialist evidence and choose the next workflow action.

Decisions:
- resolve: accept the best evidence-backed autonomous diagnostic result when no further specialist
  evidence is requested and no immediate human corrective/manual diagnostic action is required.
  This may be confirmed, probable, or a bounded inconclusive result after the autonomous evidence
  budget is exhausted.
- operator_action_required: use only when the accepted diagnosis/evidence requires a human corrective
  action, or when a necessary diagnostic action cannot be performed by the autonomous tool layer and
  must genuinely be carried out by a human operator.
- request_support: ask exactly one NEW specialist domain only when the current specialist explicitly
  requests cross-domain diagnostic evidence and the support budget is still available.

Collaboration rules:
- A confirmed diagnosis with assistance_required=false is terminal for autonomous diagnosis.
- A probable/inconclusive diagnosis with assistance_required=false must NOT trigger another
  specialist and must NOT be escalated to the operator merely because confidence is below 1.0.
  Prefer resolve for the best-effort result unless a concrete human action is genuinely required.
- Collaboration is bounded to one cross-domain support round. Never walk through all specialists.
- If support_budget_exhausted=true, request_support is forbidden, but budget exhaustion alone does
  not force operator escalation: accept the best available result when no human action is needed.
- When support is allowed, support_domain must be one of eligible_support_domains and should match
  specialist_requested_domain when that request is valid.

Diagnostic review rules:
- Never invent evidence, measurements or a root cause.
- Preserve a specialist probable root cause when its causal hypothesis is supported by the supplied
  evidence; do not downgrade it merely because every causal link is not proven.
- For an inconclusive result accepted as resolve, do not invent a cause. Use a root_cause value such
  as "Unconfirmed after bounded autonomous investigation" and explain what the evidence did establish.

Remediation rules:
- Remediation is advisory only; the agent must never execute it automatically.
- For terminal decisions, provide concrete operator steps grounded in the diagnosis/evidence.
- Commands must be suitable for the thesis Windows operator workstation: prefer PowerShell and
  Docker CLI commands. To inspect or act inside a monitored Linux container, wrap the command with
  `docker exec <container> ...` so it is executable from PowerShell.
- Each remediation step must state target, command_type (verification or remediation), exact command,
  purpose, expected_result, and what_to_verify.
- Verification steps should be read-only. Remediation commands that change service state are only
  recommendations for a human operator and must be clearly described as such.
- Do not recommend a diagnostic check that the specialists have already executed unless it is a
  post-remediation verification.
- For request_support, remediation_steps may be empty because diagnosis is still in progress.

Return only one JSON object with: decision, confidence, diagnosis_summary, root_cause, rationale,
remediation_summary, remediation_steps, support_domain, support_reason. confidence is the confidence
in the workflow/review decision, not diagnostic confidence. support_domain and support_reason must be
null unless decision=request_support."""

    def __init__(
        self,
        provider: BaseLLMProvider,
        *,
        max_attempts: int = _MAX_REVIEW_ATTEMPTS,
    ) -> None:
        if max_attempts <= 0:
            raise ValueError("max_attempts must be greater than zero")
        self.provider = provider
        self.max_attempts = max_attempts

    async def assess(
        self,
        *,
        incident: dict[str, Any],
        specialist_result: dict[str, Any],
    ) -> TechnicalLeadReviewAssessment:
        incident_id = str(incident.get("incident_id") or "").strip()
        if not incident_id:
            raise ValueError("Technical Lead review requires incident_id")

        support_policy = self._support_policy(specialist_result)
        conversation_id = f"technical-lead-review:{incident_id}"
        context = ContextManager(system_prompt=self.SYSTEM_PROMPT)
        context.add_message_dict(
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "incident": {
                            "incident_id": incident_id,
                            "status": incident.get("status"),
                            "severity": incident.get("severity"),
                            "entity": incident.get("entity"),
                            "anomaly": incident.get("anomaly") or {},
                            "triage": incident.get("agentic") or {},
                        },
                        "specialist_result": specialist_result,
                        "support_policy": support_policy,
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            },
            conversation_id,
        )

        last_error: RuntimeError | None = None
        for attempt in range(1, self.max_attempts + 1):
            response = await self._request_review(
                context,
                conversation_id=conversation_id,
                incident_id=incident_id,
                attempt=attempt,
            )
            try:
                assessment = self._assessment_from_response(response)
                self._validate_decision_against_specialist_result(
                    assessment,
                    specialist_result=specialist_result,
                )
                if attempt > 1:
                    LOGGER.warning(
                        "Technical Lead review reasoning recovered after retry: incident=%s attempt=%d/%d",
                        incident_id,
                        attempt,
                        self.max_attempts,
                    )
                return assessment
            except RuntimeError as exc:
                last_error = exc
                if attempt >= self.max_attempts:
                    raise
                LOGGER.warning(
                    "Technical Lead review response rejected; retrying inference: incident=%s attempt=%d/%d error=%s",
                    incident_id,
                    attempt,
                    self.max_attempts,
                    exc,
                )
                context.add_message_dict(
                    {
                        "role": "user",
                        "content": self._retry_instruction(exc, support_policy),
                    },
                    conversation_id,
                )

        raise last_error or RuntimeError("Technical Lead review reasoning failed")

    async def _request_review(
        self,
        context: ContextManager,
        *,
        conversation_id: str,
        incident_id: str,
        attempt: int,
    ) -> dict[str, Any]:
        try:
            return await self.provider.get_llm_response(
                context,
                tools=None,
                conversation_id=conversation_id,
                output_schema=_TechnicalLeadReviewOutput,
            )
        except Exception as structured_error:
            LOGGER.warning(
                "Technical Lead structured output transport failed; falling back to plain JSON generation: "
                "incident=%s attempt=%d/%d error=%s",
                incident_id,
                attempt,
                self.max_attempts,
                structured_error,
            )
            return await self.provider.get_llm_response(
                context,
                tools=None,
                conversation_id=conversation_id,
                output_schema=None,
            )

    @staticmethod
    def _retry_instruction(error: RuntimeError, support_policy: dict[str, Any]) -> str:
        if support_policy["support_budget_exhausted"]:
            next_rule = (
                "The support budget is exhausted. Do not request another specialist. Choose resolve "
                "for the best evidence-backed result when no human action is required; choose "
                "operator_action_required only when a concrete human corrective/manual diagnostic "
                "action is genuinely necessary."
            )
        else:
            next_rule = (
                "Request support only if specialist_result.assistance_required=true. Otherwise do "
                "not add a specialist and do not force operator escalation solely because the "
                "diagnosis is probable or inconclusive. If support is required, use only "
                f"eligible_support_domains={support_policy['eligible_support_domains']} and prefer "
                f"specialist_requested_domain={support_policy['specialist_requested_domain']!r}."
            )
        return (
            "The previous review decision violated the deterministic collaboration/remediation "
            f"contract. Validation error: {error}. {next_rule} Return ONLY the required JSON object."
        )

    @classmethod
    def _assessment_from_response(cls, response: dict[str, Any]) -> TechnicalLeadReviewAssessment:
        structured = response.get("structured")
        if structured is not None:
            if isinstance(structured, BaseModel):
                return cls._parse_payload(structured.model_dump())
            if isinstance(structured, dict):
                return cls._parse_payload(dict(structured))
            raise RuntimeError("Technical Lead structured review returned an unsupported object")
        return cls._parse_response(str(response.get("text") or "").strip())

    @classmethod
    def _parse_response(cls, raw_text: str) -> TechnicalLeadReviewAssessment:
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
                f"Technical Lead review did not return valid JSON: {raw_text[:300]!r}"
            ) from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Technical Lead review response must be a JSON object")
        return cls._parse_payload(payload)

    @staticmethod
    def _normalize_remediation_step(item: Any) -> dict[str, str] | None:
        if isinstance(item, BaseModel):
            item = item.model_dump()
        if isinstance(item, str):
            text = item.strip()
            if not text:
                return None
            return {
                "title": "Operator action",
                "target": "affected monitored component",
                "command_type": "verification",
                "command": text,
                "purpose": text,
                "expected_result": "The command should provide the expected verification outcome.",
                "what_to_verify": "Confirm the observed state matches the incident diagnosis.",
            }
        if not isinstance(item, dict):
            return None
        try:
            return _RemediationStep.model_validate(item).model_dump()
        except Exception as exc:
            raise RuntimeError(f"Invalid structured remediation step: {exc}") from exc

    @staticmethod
    def _parse_payload(payload: dict[str, Any]) -> TechnicalLeadReviewAssessment:
        decision = str(payload.get("decision") or "").strip().lower()
        if decision not in _ALLOWED_DECISIONS:
            raise RuntimeError(f"Technical Lead review returned invalid decision: {decision!r}")
        try:
            confidence = float(payload.get("confidence"))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Technical Lead review confidence is invalid") from exc
        if not 0.0 <= confidence <= 1.0:
            raise RuntimeError("Technical Lead review confidence must be between 0 and 1")

        diagnosis_summary = str(payload.get("diagnosis_summary") or "").strip()
        root_cause = str(payload.get("root_cause") or "").strip()
        rationale = str(payload.get("rationale") or "").strip()
        remediation_summary = str(payload.get("remediation_summary") or "").strip()
        if not diagnosis_summary or not root_cause or not rationale or not remediation_summary:
            raise RuntimeError("Technical Lead review returned incomplete diagnostic fields")

        steps_raw = payload.get("remediation_steps") or []
        if not isinstance(steps_raw, list):
            raise RuntimeError("Technical Lead remediation_steps must be an array")
        normalized_steps = []
        for item in steps_raw:
            normalized = TechnicalLeadReviewReasoner._normalize_remediation_step(item)
            if normalized is not None:
                normalized_steps.append(normalized)
        remediation_steps = tuple(normalized_steps)

        if decision in {"resolve", "operator_action_required"} and not remediation_steps:
            raise RuntimeError(
                "Terminal Technical Lead decision requires at least one structured operator step"
            )

        support_domain_raw = payload.get("support_domain")
        support_domain = (
            str(support_domain_raw).strip().lower()
            if support_domain_raw is not None
            else None
        )
        support_reason_raw = payload.get("support_reason")
        support_reason = (
            str(support_reason_raw).strip() if support_reason_raw is not None else None
        )
        if decision == "request_support":
            if support_domain not in _ALLOWED_SUPPORT_DOMAINS:
                raise RuntimeError("Technical Lead support request requires a valid domain")
            if not support_reason:
                raise RuntimeError("Technical Lead support request requires a reason")
        else:
            support_domain = None
            support_reason = None

        return TechnicalLeadReviewAssessment(
            decision=decision,
            confidence=confidence,
            diagnosis_summary=diagnosis_summary,
            root_cause=root_cause,
            rationale=rationale,
            remediation_summary=remediation_summary,
            remediation_steps=remediation_steps,
            support_domain=support_domain,
            support_reason=support_reason,
        )

    @staticmethod
    def _support_policy(specialist_result: dict[str, Any]) -> dict[str, Any]:
        involved: list[str] = []
        for raw_role in specialist_result.get("specialists_already_involved") or []:
            role = str(raw_role).strip().lower()
            if role and role not in involved:
                involved.append(role)

        support_round = max(len(involved) - 1, 0)
        support_budget_exhausted = support_round >= _MAX_SUPPORT_ROUNDS
        eligible_domains = [] if support_budget_exhausted else [
            domain
            for domain, role in _SUPPORT_ROLE_BY_DOMAIN.items()
            if role not in involved
        ]
        requested_domain = str(
            specialist_result.get("assistance_domain") or ""
        ).strip().lower()
        if requested_domain not in _ALLOWED_SUPPORT_DOMAINS:
            requested_domain = ""

        return {
            "specialists_already_involved": involved,
            "support_round": support_round,
            "max_support_rounds": _MAX_SUPPORT_ROUNDS,
            "support_budget_exhausted": support_budget_exhausted,
            "eligible_support_domains": eligible_domains,
            "specialist_requested_domain": requested_domain or None,
        }

    @staticmethod
    def _validate_decision_against_specialist_result(
        assessment: TechnicalLeadReviewAssessment,
        *,
        specialist_result: dict[str, Any],
    ) -> None:
        diagnosis_status = str(
            specialist_result.get("diagnosis_status") or "inconclusive"
        ).strip().lower()
        root_cause = str(specialist_result.get("root_cause") or "").strip()
        causal_chain = specialist_result.get("causal_chain") or []
        assistance_required = bool(specialist_result.get("assistance_required", False))
        support_policy = TechnicalLeadReviewReasoner._support_policy(specialist_result)
        support_budget_exhausted = bool(support_policy["support_budget_exhausted"])

        if diagnosis_status not in {"confirmed", "probable", "inconclusive"}:
            raise RuntimeError(
                f"Specialist returned unsupported diagnosis_status: {diagnosis_status!r}"
            )

        if diagnosis_status in {"confirmed", "probable"}:
            if not root_cause or not isinstance(causal_chain, list) or not causal_chain:
                raise RuntimeError(
                    f"{diagnosis_status} specialist diagnosis requires root_cause and causal_chain"
                )

        if diagnosis_status == "confirmed":
            if assistance_required:
                raise RuntimeError(
                    "Confirmed specialist diagnosis cannot request diagnostic assistance"
                )
            if assessment.decision == "request_support":
                raise RuntimeError(
                    "Confirmed diagnosis without assistance must terminate autonomous investigation"
                )
            return

        if support_budget_exhausted:
            if assessment.decision == "request_support":
                raise RuntimeError(
                    "Cross-domain support budget exhausted; request_support is forbidden. Accept "
                    "the best available result or escalate only when concrete human action is needed."
                )
            return

        if not assistance_required:
            if assessment.decision == "request_support":
                raise RuntimeError(
                    f"{diagnosis_status} diagnosis does not request peer assistance; do not add a "
                    "specialist. Accept the bounded best-effort result unless concrete human action "
                    "is required."
                )
            return

        if assessment.decision != "request_support":
            raise RuntimeError(
                f"{diagnosis_status} diagnosis explicitly requests cross-domain evidence; authorize "
                "the one bounded support round while budget is available"
            )

        support_domain = str(assessment.support_domain or "").strip().lower()
        eligible_domains = list(support_policy["eligible_support_domains"])
        requested_domain = support_policy["specialist_requested_domain"]
        if support_domain not in eligible_domains:
            raise RuntimeError(
                f"support_domain={support_domain!r} is not eligible; eligible support domains are "
                f"{eligible_domains}"
            )
        if requested_domain and requested_domain in eligible_domains and support_domain != requested_domain:
            raise RuntimeError(
                f"Specialist requested support_domain={requested_domain!r}; TL cannot redirect to "
                f"{support_domain!r} without a new evidence-backed reason"
            )
