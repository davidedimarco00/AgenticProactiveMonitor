from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import Any

from spade_llm.context import ContextManager
from spade_llm.providers.base_provider import BaseLLMProvider


LOGGER = logging.getLogger("agentic_system.agents.review")
_ALLOWED_DECISIONS = {"resolve", "operator_action_required", "request_support"}
_ALLOWED_SUPPORT_DOMAINS = {"system", "network", "application", "software"}
_MAX_REVIEW_ATTEMPTS = 3


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
    """Gemma-only critic that reviews a completed specialist ReAct result."""

    SYSTEM_PROMPT = """You are the Technical Lead of an IT monitoring multi-agent team.
A specialist has completed an evidence-gathering ReAct investigation. Critically review
only the supplied evidence and decide what the autonomous workflow should do next.

Choose exactly one decision:
- resolve: evidence is sufficient and no immediate human corrective action is required.
  Use this for transient/non-reproduced anomalies or a diagnosis that is already safe to
  close without executing a change.
- operator_action_required: evidence is sufficient for a diagnosis, but remediation needs
  an operator or a change that this monitoring system must not execute autonomously.
- request_support: evidence is insufficient or another technical domain must investigate
  before a diagnosis can be accepted.

The specialist result may contain `specialists_already_involved`. When requesting support,
prefer a technical domain whose specialist has not already participated in this incident.
The support specialist will receive the current specialist's evidence directly over XMPP
and will correlate it with its own MCP/RAG observations. Do not use request_support merely
to repeat the same investigation with a specialist that has already participated.

Do not invent evidence, commands, measurements, or a root cause that the specialist did
not support. Keep remediation advisory only. Return only one JSON object with fields:
decision, confidence, diagnosis_summary, root_cause, rationale, remediation_summary,
remediation_steps, support_domain, support_reason.

confidence must be 0..1. remediation_steps must be an array of concise strings.
support_domain must be null unless decision=request_support, otherwise one of system,
network, application, software. support_reason must be null unless support is requested."""

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
            response = await self.provider.get_llm_response(
                context,
                tools=None,
                conversation_id=conversation_id,
            )
            text = str(response.get("text") or "").strip()
            try:
                assessment = self._parse_response(text)
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
                            "The previous review response was empty or invalid. Return ONLY "
                            "the required JSON object with every required field present. "
                            "Do not call tools and do not add prose outside the JSON."
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

    @staticmethod
    def _parse_response(raw_text: str) -> TechnicalLeadReviewAssessment:
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
