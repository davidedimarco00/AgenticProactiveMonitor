from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import Any, Literal

from pydantic import BaseModel, Field
from spade_llm.context import ContextManager
from spade_llm.providers.base_provider import BaseLLMProvider


LOGGER = logging.getLogger("agentic_system.agents.review")
_ALLOWED_DECISIONS = {"resolve", "operator_action_required", "request_support"}
_ALLOWED_SUPPORT_DOMAINS = {"system", "network", "application", "software"}
_MAX_REVIEW_ATTEMPTS = 3

ReviewDecision = Literal["resolve", "operator_action_required", "request_support"]
SupportDomain = Literal["system", "network", "application", "software"]


class _TechnicalLeadReviewOutput(BaseModel):
    """Schema requested from Ollama/LiteLLM for the TL critic response."""

    decision: ReviewDecision
    confidence: float = Field(ge=0.0, le=1.0)
    diagnosis_summary: str
    root_cause: str
    rationale: str
    remediation_summary: str
    remediation_steps: list[str]
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
    remediation_steps: tuple[str, ...]
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
    remediation_steps: tuple[str, ...]
    support_domain: str | None
    support_reason: str | None
    bdi_goal: str
    bdi_review_intention: str
    bdi_decision_intention: str


class TechnicalLeadReviewReasoner:
    """Gemma-only critic that reviews a completed specialist diagnostic result."""

    SYSTEM_PROMPT = """You are the Technical Lead of an IT monitoring multi-agent team.
A specialist has completed an evidence-gathering ReAct investigation. Critically review
only the supplied evidence and decide what the autonomous workflow should do next.

The specialist result now contains an explicit diagnostic closure:
- diagnosis_status: confirmed, probable, or inconclusive
- root_cause: the specialist's evidence-backed causal explanation, or null
- causal_chain: cause -> mechanism -> observed anomaly
- recommended_next_steps: remaining DIAGNOSTIC checks, not remediation

Choose exactly one decision:
- resolve: use only when diagnosis_status=confirmed and the evidence supports a root cause,
  while no immediate human corrective action is required.
- operator_action_required: use only when diagnosis_status=confirmed, the root cause is
  sufficiently supported, and the required corrective change must be performed by a human.
- request_support: use when the diagnosis is probable/inconclusive, when remaining diagnostic
  steps require another technical domain, or when another specialist must validate the causal
  explanation before the diagnosis can be accepted.

Do NOT escalate to the operator merely because more investigation is needed. Investigation
belongs to the agents. If the specialist still recommends actions such as inspect logs,
query metrics, examine connections, validate service behaviour, or collect another domain's
evidence, select request_support rather than presenting those diagnostic actions as remediation.

The specialist result may contain `specialists_already_involved`. When requesting support,
prefer a technical domain whose specialist has not already participated in this incident.
If the specialist explicitly sets assistance_required=true and provides assistance_domain,
treat that as strong evidence that another domain is needed, while still checking whether
that domain has already participated. The support specialist will receive the current
specialist's evidence directly over XMPP and correlate it with its own MCP/RAG observations.

Do not invent evidence, commands, measurements, or a root cause that the specialist did
not support. Keep remediation advisory only. Remediation must describe what should be done
AFTER the diagnosis; do not copy unresolved diagnostic checks into remediation_steps.
Return only one JSON object with fields: decision, confidence, diagnosis_summary,
root_cause, rationale, remediation_summary, remediation_steps, support_domain,
support_reason.

The `decision` field MUST be exactly one of: resolve, operator_action_required,
request_support. confidence must be 0..1. remediation_steps must be an array of concise
strings. support_domain must be null unless decision=request_support, otherwise one of
system, network, application, software. support_reason must be null unless support is requested."""

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
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            },
            conversation_id,
        )

        last_error: RuntimeError | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = await self.provider.get_llm_response(
                    context,
                    tools=None,
                    conversation_id=conversation_id,
                    output_schema=_TechnicalLeadReviewOutput,
                )
            except Exception as structured_error:
                LOGGER.warning(
                    "Technical Lead structured output transport failed; falling back to "
                    "plain JSON generation for this attempt: incident=%s attempt=%d/%d error=%s",
                    incident_id,
                    attempt,
                    self.max_attempts,
                    structured_error,
                )
                response = await self.provider.get_llm_response(
                    context,
                    tools=None,
                    conversation_id=conversation_id,
                    output_schema=None,
                )

            try:
                assessment = self._assessment_from_response(response)
                self._validate_decision_against_specialist_result(
                    assessment,
                    specialist_result=specialist_result,
                )
            except RuntimeError as exc:
                last_error = exc
                if attempt >= self.max_attempts:
                    raise
                LOGGER.warning(
                    "Technical Lead review response rejected; retrying inference: "
                    "incident=%s attempt=%d/%d error=%s",
                    incident_id,
                    attempt,
                    self.max_attempts,
                    exc,
                )
                context.add_message_dict(
                    {
                        "role": "user",
                        "content": (
                            "The previous review decision was invalid for the diagnostic state. "
                            f"Validation error: {exc}. Return ONLY the required JSON object. "
                            "Terminal decisions require diagnosis_status=confirmed. If the "
                            "specialist diagnosis is probable or inconclusive, request support "
                            "from a useful specialist domain rather than assigning diagnostic "
                            "work to the human operator."
                        ),
                    },
                    conversation_id,
                )
                continue

            if attempt > 1:
                LOGGER.warning(
                    "Technical Lead review reasoning recovered after retry: "
                    "incident=%s attempt=%d/%d",
                    incident_id,
                    attempt,
                    self.max_attempts,
                )
            return assessment

        raise last_error or RuntimeError("Technical Lead review reasoning failed")

    @classmethod
    def _assessment_from_response(cls, response: dict[str, Any]) -> TechnicalLeadReviewAssessment:
        structured = response.get("structured")
        if structured is not None:
            if isinstance(structured, BaseModel):
                payload = structured.model_dump()
            elif isinstance(structured, dict):
                payload = dict(structured)
            else:
                raise RuntimeError(
                    "Technical Lead structured review returned an unsupported object"
                )
            return cls._parse_payload(payload)

        text = str(response.get("text") or "").strip()
        return cls._parse_response(text)

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
        remediation_steps = tuple(
            str(item).strip() for item in steps_raw if str(item).strip()
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

        if diagnosis_status not in {"confirmed", "probable", "inconclusive"}:
            raise RuntimeError(
                f"Specialist returned unsupported diagnosis_status: {diagnosis_status!r}"
            )

        if diagnosis_status != "confirmed" and assessment.decision != "request_support":
            raise RuntimeError(
                f"{diagnosis_status} specialist diagnosis requires request_support; "
                f"got {assessment.decision}"
            )

        if assessment.decision in {"resolve", "operator_action_required"}:
            if diagnosis_status != "confirmed":
                raise RuntimeError("Terminal TL decision requires confirmed diagnosis")
            if not root_cause or not isinstance(causal_chain, list) or not causal_chain:
                raise RuntimeError(
                    "Terminal TL decision requires specialist root_cause and causal_chain"
                )
            if assistance_required:
                raise RuntimeError(
                    "Terminal TL decision is inconsistent with specialist assistance request"
                )

        if assistance_required and assessment.decision != "request_support":
            raise RuntimeError(
                "Specialist requested diagnostic assistance, so TL must request support"
            )
