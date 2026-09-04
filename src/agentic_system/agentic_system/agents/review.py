from __future__ import annotations

from dataclasses import dataclass, replace
import json
import logging
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from spade_llm.context import ContextManager
from spade_llm.providers.base_provider import BaseLLMProvider


LOGGER = logging.getLogger("agentic_system.agents.review")

# The Technical Lead review is terminal. Peer collaboration is initiated
# autonomously by the specialist that needs it, before the result ever reaches
# this review, so "resolve" is the only workflow continuation left. The legacy
# decisions are still parsed - older prompts and the unstructured JSON fallback
# can emit them - but they are folded into "resolve" instead of being rejected,
# which would burn retries on the single shared GPU.
_LEGACY_DECISIONS = {"operator_action_required", "request_support"}
_ALLOWED_DECISIONS = {"resolve"} | _LEGACY_DECISIONS
_MAX_REVIEW_ATTEMPTS = 3

ReviewDecision = Literal["resolve"]
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


@dataclass(frozen=True, slots=True)
class TechnicalLeadReviewAssessment:
    decision: str
    confidence: float
    diagnosis_summary: str
    root_cause: str
    rationale: str
    remediation_summary: str
    remediation_steps: tuple[dict[str, str], ...]


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
    bdi_goal: str
    bdi_review_intention: str
    bdi_decision_intention: str


class TechnicalLeadReviewReasoner:
    """Gemma critic that closes an incident with a terminal evidence-backed diagnosis."""

    SYSTEM_PROMPT = """You are the Technical Lead of an IT monitoring multi-agent team.
Review only the supplied specialist evidence and close the incident with the best diagnosis.

Terminal review:
- Your review is the final step of the autonomous workflow and resolve is the only valid decision.
- A diagnosis may be confirmed, probable, or bounded inconclusive. All three are acceptable
  terminal outcomes. Human remediation recommendations belong in remediation_steps and do NOT
  change the workflow status away from resolve.
- You never add a specialist to the incident. Specialists collaborate on their own initiative:
  when one needs cross-domain evidence it contacts a peer directly and folds the peer answer into
  the result you receive. A peer_consultation object in the specialist result means that
  collaboration already happened; judge the combined evidence, do not ask for more agents.

Diagnostic review rules:
- Never invent evidence, measurements or a root cause.
- Preserve a specialist probable root cause when its causal hypothesis is supported by supplied live
  evidence; do not demand an ultimate source-code cause when a lower-level process, dependency,
  component or runtime mechanism already explains the anomaly.
- A diagnostic-process failure is NOT a root cause of the monitored incident. Invalid tool arguments,
  schema-validation errors, tool timeouts, unavailable diagnostic endpoints, malformed responses,
  model/tool-routing failures and duplicate-action saturation describe evidence acquisition, not the
  monitored system, unless that diagnostic infrastructure is explicitly the incident entity.
- If the specialist presents only a diagnostic-tool/process failure as root cause, do not preserve it
  as a causal diagnosis. Resolve as bounded inconclusive and use the conservative root_cause label
  "Unconfirmed causal mechanism after bounded autonomous investigation" while explaining the
  evidence gap in diagnosis_summary/rationale.
- Tool failures may reduce confidence or explain missing evidence, but they must not increase causal
  confidence and must not appear as the anomaly-producing mechanism.
- For an inconclusive result, still return a diagnosis summary describing what the autonomous
  investigation established. If no specific root cause was proven, use a conservative non-empty
  root_cause label such as "Unconfirmed causal mechanism after bounded autonomous investigation".

Remediation rules:
- Remediation is advisory only; the agent must never execute it automatically.
- For resolve, remediation_steps may contain concrete operator recommendations when useful, but these
  recommendations do not turn the incident into OPERATOR_ACTION_REQUIRED.
- Commands must be suitable for the thesis Windows operator workstation: prefer PowerShell and
  Docker CLI commands. To inspect or act inside a monitored Linux container, wrap the command with
  `docker exec <container> ...` so it is executable from PowerShell.
- Each remediation step must state target, command_type (verification or remediation), exact command,
  purpose, expected_result, and what_to_verify.
- Verification steps should be read-only. Remediation commands that change service state are only
  recommendations for a human operator and must be clearly described as such.
- Do not recommend a diagnostic check that the specialists have already executed unless it is a
  post-remediation verification.
- An inconclusive diagnosis still closes the incident: give the operator at least the verification
  step that would confirm or exclude the remaining hypothesis.

Return only one JSON object with: decision, confidence, diagnosis_summary, root_cause, rationale,
remediation_summary, remediation_steps. decision must be "resolve". confidence is the confidence in
the workflow/review decision, not diagnostic confidence."""

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

        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = await self._request_review(
                    context,
                    conversation_id=conversation_id,
                    incident_id=incident_id,
                    attempt=attempt,
                )
                assessment = self._assessment_from_response(response)
                self._validate_specialist_result(specialist_result)
                assessment = self._ensure_operator_guidance(
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
            except Exception as exc:
                last_error = exc
                if attempt >= self.max_attempts:
                    fallback = self._ensure_operator_guidance(
                        self._fallback_assessment(specialist_result, error=exc),
                        specialist_result=specialist_result,
                    )
                    LOGGER.error(
                        "Technical Lead review exhausted model retries; using deterministic "
                        "diagnosis-first fallback instead of operator escalation: incident=%s error=%s",
                        incident_id,
                        exc,
                    )
                    return fallback
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
                        "content": self._retry_instruction(RuntimeError(str(exc))),
                    },
                    conversation_id,
                )

        return self._ensure_operator_guidance(
            self._fallback_assessment(
                specialist_result,
                error=last_error or RuntimeError("Technical Lead review reasoning failed"),
            ),
            specialist_result=specialist_result,
        )

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
    def _retry_instruction(error: RuntimeError) -> str:
        return (
            "The previous review violated the deterministic diagnosis contract. Validation error: "
            f"{error}. The review is terminal: set decision to \"resolve\", never request another "
            "specialist, and preserve the best diagnosis supported by the collected evidence. "
            "Return ONLY the required JSON object."
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
        if decision in _LEGACY_DECISIONS:
            # Neither escalation nor a new support round is a state of this
            # workflow anymore: the incident still receives its diagnosis.
            LOGGER.warning(
                "Technical Lead returned legacy decision %r; normalizing to resolve",
                decision,
            )
            decision = "resolve"
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

        return TechnicalLeadReviewAssessment(
            decision=decision,
            confidence=confidence,
            diagnosis_summary=diagnosis_summary,
            root_cause=root_cause,
            rationale=rationale,
            remediation_summary=remediation_summary,
            remediation_steps=remediation_steps,
        )

    @staticmethod
    def _ensure_operator_guidance(
        assessment: TechnicalLeadReviewAssessment,
        *,
        specialist_result: dict[str, Any],
    ) -> TechnicalLeadReviewAssessment:
        """Never close an incident without a single operator-facing action.

        The review is terminal, so an empty remediation list would leave the
        operator with a diagnosis and nothing to do with it. The specialist's
        own recommended next steps are promoted deterministically instead of
        spending another inference on the shared GPU.
        """

        if assessment.remediation_steps:
            return assessment

        promoted: list[dict[str, str]] = []
        for raw in specialist_result.get("recommended_next_steps") or []:
            step = TechnicalLeadReviewReasoner._normalize_remediation_step(str(raw))
            if step is not None:
                promoted.append(step)
        if not promoted:
            return assessment

        LOGGER.warning(
            "Technical Lead review returned no remediation step; promoting %d specialist "
            "recommendation(s) as advisory operator guidance",
            len(promoted),
        )
        return replace(assessment, remediation_steps=tuple(promoted))

    @staticmethod
    def _fallback_assessment(
        specialist_result: dict[str, Any],
        *,
        error: Exception,
    ) -> TechnicalLeadReviewAssessment:
        summary = str(specialist_result.get("summary") or "").strip()
        if not summary:
            summary = "Bounded autonomous specialist investigation completed with retained evidence."

        root_cause = str(specialist_result.get("root_cause") or "").strip()
        if not root_cause:
            for raw in specialist_result.get("hypotheses") or []:
                candidate = str(raw or "").strip()
                if candidate.lower() not in {"", "none", "null", "unknown", "unconfirmed", "n/a"}:
                    root_cause = candidate
                    break
        if not root_cause:
            root_cause = "Unconfirmed causal mechanism after bounded autonomous investigation"

        try:
            confidence = float(specialist_result.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = min(max(confidence, 0.0), 1.0)

        next_steps = [
            str(item).strip()
            for item in specialist_result.get("recommended_next_steps") or []
            if str(item).strip()
        ]
        remediation_summary = (
            "No autonomous remediation was executed. The retained specialist recommendations are "
            "advisory and the diagnosis was preserved despite Technical Lead model-review failure."
        )
        if next_steps:
            remediation_summary += " Recommended verification: " + next_steps[0]

        return TechnicalLeadReviewAssessment(
            decision="resolve",
            confidence=confidence,
            diagnosis_summary=summary,
            root_cause=root_cause,
            rationale=(
                "The Technical Lead model review could not satisfy the structured workflow contract; "
                "the system therefore preserved the specialist's bounded evidence-backed diagnosis "
                f"instead of escalating to OPERATOR_ACTION_REQUIRED. Review error: {error}"
            ),
            remediation_summary=remediation_summary,
            remediation_steps=(),
        )

    @staticmethod
    def _validate_specialist_result(specialist_result: dict[str, Any]) -> None:
        """Reject a specialist payload that cannot support a terminal diagnosis.

        The review no longer arbitrates collaboration, so the only contract left
        to enforce is that a confirmed/probable diagnosis actually carries the
        causal evidence the final incident report will publish.
        """

        diagnosis_status = str(
            specialist_result.get("diagnosis_status") or "inconclusive"
        ).strip().lower()
        if diagnosis_status not in {"confirmed", "probable", "inconclusive"}:
            raise RuntimeError(
                f"Specialist returned unsupported diagnosis_status: {diagnosis_status!r}"
            )

        if diagnosis_status in {"confirmed", "probable"}:
            root_cause = str(specialist_result.get("root_cause") or "").strip()
            causal_chain = specialist_result.get("causal_chain") or []
            if not root_cause or not isinstance(causal_chain, list) or not causal_chain:
                raise RuntimeError(
                    f"{diagnosis_status} specialist diagnosis requires root_cause and causal_chain"
                )
