import asyncio
import json

import pytest

from agentic_system.agents.review import TechnicalLeadReviewReasoner


def _payload(decision: str, **extra: object) -> dict:
    payload = {
        "decision": decision,
        "confidence": 0.9,
        "diagnosis_summary": "Evidence-backed diagnostic review.",
        "root_cause": "Observed latency is explained by the investigated service path.",
        "rationale": "The review closes the incident with the collected evidence.",
        "remediation_summary": "No autonomous remediation is performed.",
        "remediation_steps": [],
    }
    payload.update(extra)
    return payload


def _result(diagnosis_status: str, *, involved: list[str]) -> dict:
    return {
        "diagnosis_status": diagnosis_status,
        "summary": "The specialist completed a bounded evidence-backed diagnosis.",
        "root_cause": (
            "Observed latency is explained by the investigated service path."
            if diagnosis_status in {"confirmed", "probable"}
            else None
        ),
        "causal_chain": (
            ["Service path degraded.", "Latency increased.", "Detector observed anomaly."]
            if diagnosis_status in {"confirmed", "probable"}
            else []
        ),
        "confidence": 0.8,
        "findings": ["Live service evidence was collected."],
        "hypotheses": ["The investigated service path is the most likely causal mechanism."],
        "recommended_next_steps": ["Verify the same path after the workload returns to baseline."],
        "specialists_already_involved": involved,
    }


def test_review_prompt_never_offers_another_specialist() -> None:
    prompt = TechnicalLeadReviewReasoner.SYSTEM_PROMPT

    assert "request_support" not in prompt
    assert "support_domain" not in prompt
    assert "resolve is the only valid decision" in prompt


def test_legacy_support_request_is_folded_into_a_terminal_resolve() -> None:
    assessment = TechnicalLeadReviewReasoner._parse_payload(
        _payload(
            "request_support",
            support_domain="system",
            support_reason="Cross-domain evidence is required.",
        )
    )

    assert assessment.decision == "resolve"
    assert not hasattr(assessment, "support_domain")


def test_legacy_operator_escalation_is_folded_into_a_terminal_resolve() -> None:
    assessment = TechnicalLeadReviewReasoner._parse_payload(
        _payload("operator_action_required")
    )

    assert assessment.decision == "resolve"


def test_unknown_decision_is_still_rejected() -> None:
    with pytest.raises(RuntimeError, match="invalid decision"):
        TechnicalLeadReviewReasoner._parse_payload(_payload("escalate_to_vendor"))


@pytest.mark.parametrize("diagnosis_status", ["confirmed", "probable"])
def test_causal_diagnosis_requires_root_cause_and_chain(diagnosis_status: str) -> None:
    specialist_result = _result(diagnosis_status, involved=["system_engineer"])
    specialist_result["root_cause"] = None

    with pytest.raises(RuntimeError, match=f"{diagnosis_status} specialist diagnosis requires"):
        TechnicalLeadReviewReasoner._validate_specialist_result(specialist_result)


def test_inconclusive_result_is_a_valid_terminal_input() -> None:
    TechnicalLeadReviewReasoner._validate_specialist_result(
        _result("inconclusive", involved=["system_engineer", "network_engineer"])
    )


def test_unsupported_diagnosis_status_is_rejected() -> None:
    specialist_result = _result("confirmed", involved=["system_engineer"])
    specialist_result["diagnosis_status"] = "in_progress"

    with pytest.raises(RuntimeError, match="unsupported diagnosis_status"):
        TechnicalLeadReviewReasoner._validate_specialist_result(specialist_result)


def test_review_fallback_preserves_a_diagnosis_instead_of_escalating() -> None:
    fallback = TechnicalLeadReviewReasoner._fallback_assessment(
        _result("inconclusive", involved=["system_engineer"]),
        error=RuntimeError("model kept returning an invalid workflow decision"),
    )

    assert fallback.decision == "resolve"
    assert fallback.root_cause == (
        "The investigated service path is the most likely causal mechanism."
    )
    assert fallback.diagnosis_summary
    assert "instead of escalating to OPERATOR_ACTION_REQUIRED" in fallback.rationale


def test_terminal_resolve_never_closes_an_incident_without_operator_guidance() -> None:
    """An empty remediation list would leave the operator a diagnosis and nothing to do."""

    class FakeProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def get_llm_response(
            self,
            context,
            tools=None,
            conversation_id=None,
            output_schema=None,
        ):
            self.calls += 1
            return {"text": json.dumps(_payload("resolve")), "structured": None}

    provider = FakeProvider()
    reasoner = TechnicalLeadReviewReasoner(provider)  # type: ignore[arg-type]
    specialist_result = _result("probable", involved=["system_engineer"])

    result = asyncio.run(
        reasoner.assess(
            incident={"incident_id": "INC-TERMINAL-001"},
            specialist_result=specialist_result,
        )
    )

    # The recommendation is promoted deterministically: no extra inference.
    assert provider.calls == 1
    assert result.decision == "resolve"
    assert len(result.remediation_steps) == 1
    step = result.remediation_steps[0]
    assert step["command"] == specialist_result["recommended_next_steps"][0]
    assert step["command_type"] == "verification"


def test_operator_guidance_promotion_does_not_override_the_technical_lead() -> None:
    reviewed_step = {
        "title": "Verify processing-service",
        "target": "processing-service",
        "command_type": "verification",
        "command": "docker exec processing-service ps -eo pid,comm,%cpu",
        "purpose": "Confirm the diagnosed workload is still running.",
        "expected_result": "The CPU-heavy process is listed first.",
        "what_to_verify": "Process identity and CPU usage.",
    }
    assessment = TechnicalLeadReviewReasoner._parse_payload(
        _payload("resolve", remediation_steps=[reviewed_step])
    )

    kept = TechnicalLeadReviewReasoner._ensure_operator_guidance(
        assessment,
        specialist_result=_result("probable", involved=["system_engineer"]),
    )

    assert kept.remediation_steps == (reviewed_step,)
