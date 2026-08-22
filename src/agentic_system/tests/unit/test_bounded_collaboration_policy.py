import pytest

from agentic_system.agents.review import (
    TechnicalLeadReviewAssessment,
    TechnicalLeadReviewReasoner,
)


def _assessment(decision: str, *, support_domain: str | None = None) -> TechnicalLeadReviewAssessment:
    return TechnicalLeadReviewAssessment(
        decision=decision,
        confidence=0.9,
        diagnosis_summary="Evidence-backed diagnostic review.",
        root_cause="Observed latency is explained by the investigated service path.",
        rationale="Decision follows the bounded collaboration policy.",
        remediation_summary="No autonomous remediation is performed.",
        remediation_steps=(),
        support_domain=support_domain,
        support_reason="Cross-domain evidence is required." if support_domain else None,
    )


def _result(
    diagnosis_status: str,
    *,
    assistance_required: bool,
    assistance_domain: str | None,
    involved: list[str],
) -> dict:
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
        "assistance_required": assistance_required,
        "assistance_domain": assistance_domain,
        "specialists_already_involved": involved,
    }


def _normalize(
    assessment: TechnicalLeadReviewAssessment,
    specialist_result: dict,
) -> TechnicalLeadReviewAssessment:
    policy = TechnicalLeadReviewReasoner._support_policy(specialist_result)
    return TechnicalLeadReviewReasoner._normalize_diagnosis_first_decision(
        assessment,
        specialist_result=specialist_result,
        support_policy=policy,
    )


def test_confirmed_result_cannot_start_unnecessary_support_round() -> None:
    specialist_result = _result(
        "confirmed",
        assistance_required=False,
        assistance_domain=None,
        involved=["network_engineer"],
    )

    normalized = _normalize(
        _assessment("request_support", support_domain="system"),
        specialist_result,
    )
    assert normalized.decision == "resolve"
    assert normalized.support_domain is None

    TechnicalLeadReviewReasoner._validate_decision_against_specialist_result(
        normalized,
        specialist_result=specialist_result,
    )


def test_first_explicit_cross_domain_support_is_allowed() -> None:
    specialist_result = _result(
        "probable",
        assistance_required=True,
        assistance_domain="system",
        involved=["network_engineer"],
    )

    policy = TechnicalLeadReviewReasoner._support_policy(specialist_result)
    assert policy["support_round"] == 0
    assert policy["support_budget_exhausted"] is False
    assert "system" in policy["eligible_support_domains"]

    normalized = _normalize(
        _assessment("request_support", support_domain="system"),
        specialist_result,
    )
    assert normalized.decision == "request_support"
    assert normalized.support_domain == "system"

    TechnicalLeadReviewReasoner._validate_decision_against_specialist_result(
        normalized,
        specialist_result=specialist_result,
    )


def test_uncertain_result_without_assistance_closes_as_best_effort_diagnosis() -> None:
    specialist_result = _result(
        "inconclusive",
        assistance_required=False,
        assistance_domain=None,
        involved=["system_engineer"],
    )

    for proposed in (
        _assessment("resolve"),
        _assessment("operator_action_required"),
        _assessment("request_support", support_domain="network"),
    ):
        normalized = _normalize(proposed, specialist_result)
        assert normalized.decision == "resolve"
        assert normalized.support_domain is None
        TechnicalLeadReviewReasoner._validate_decision_against_specialist_result(
            normalized,
            specialist_result=specialist_result,
        )


def test_operator_action_required_is_not_valid_autonomous_terminal_decision() -> None:
    specialist_result = _result(
        "probable",
        assistance_required=False,
        assistance_domain=None,
        involved=["system_engineer"],
    )

    with pytest.raises(RuntimeError, match="not a valid autonomous diagnostic terminal"):
        TechnicalLeadReviewReasoner._validate_decision_against_specialist_result(
            _assessment("operator_action_required"),
            specialist_result=specialist_result,
        )

    normalized = _normalize(_assessment("operator_action_required"), specialist_result)
    assert normalized.decision == "resolve"


def test_second_cross_domain_support_is_rejected_but_best_effort_resolve_is_allowed() -> None:
    specialist_result = _result(
        "probable",
        assistance_required=True,
        assistance_domain="application",
        involved=["network_engineer", "system_engineer"],
    )

    policy = TechnicalLeadReviewReasoner._support_policy(specialist_result)
    assert policy["support_round"] == 1
    assert policy["support_budget_exhausted"] is True
    assert policy["eligible_support_domains"] == []

    normalized_support = _normalize(
        _assessment("request_support", support_domain="application"),
        specialist_result,
    )
    assert normalized_support.decision == "resolve"

    normalized_operator = _normalize(
        _assessment("operator_action_required"),
        specialist_result,
    )
    assert normalized_operator.decision == "resolve"

    TechnicalLeadReviewReasoner._validate_decision_against_specialist_result(
        normalized_support,
        specialist_result=specialist_result,
    )
    TechnicalLeadReviewReasoner._validate_decision_against_specialist_result(
        normalized_operator,
        specialist_result=specialist_result,
    )


def test_probable_result_still_requires_concrete_root_cause_and_chain() -> None:
    specialist_result = _result(
        "probable",
        assistance_required=False,
        assistance_domain=None,
        involved=["system_engineer"],
    )
    specialist_result["root_cause"] = None

    with pytest.raises(RuntimeError, match="probable specialist diagnosis requires"):
        TechnicalLeadReviewReasoner._validate_decision_against_specialist_result(
            _assessment("resolve"),
            specialist_result=specialist_result,
        )


def test_review_fallback_preserves_a_diagnosis_instead_of_escalating() -> None:
    specialist_result = _result(
        "inconclusive",
        assistance_required=False,
        assistance_domain=None,
        involved=["system_engineer"],
    )

    fallback = TechnicalLeadReviewReasoner._fallback_assessment(
        specialist_result,
        error=RuntimeError("model kept returning an invalid workflow decision"),
    )

    assert fallback.decision == "resolve"
    assert fallback.root_cause == (
        "The investigated service path is the most likely causal mechanism."
    )
    assert fallback.diagnosis_summary
    assert fallback.support_domain is None
    assert "instead of escalating to OPERATOR_ACTION_REQUIRED" in fallback.rationale
