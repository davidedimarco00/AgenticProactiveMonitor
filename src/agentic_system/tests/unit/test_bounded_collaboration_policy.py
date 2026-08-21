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
        "assistance_required": assistance_required,
        "assistance_domain": assistance_domain,
        "specialists_already_involved": involved,
    }


def test_confirmed_result_cannot_start_unnecessary_support_round() -> None:
    specialist_result = _result(
        "confirmed",
        assistance_required=False,
        assistance_domain=None,
        involved=["network_engineer"],
    )

    with pytest.raises(RuntimeError, match="must terminate autonomous investigation"):
        TechnicalLeadReviewReasoner._validate_decision_against_specialist_result(
            _assessment("request_support", support_domain="system"),
            specialist_result=specialist_result,
        )

    TechnicalLeadReviewReasoner._validate_decision_against_specialist_result(
        _assessment("resolve"),
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

    TechnicalLeadReviewReasoner._validate_decision_against_specialist_result(
        _assessment("request_support", support_domain="system"),
        specialist_result=specialist_result,
    )


def test_second_cross_domain_support_is_rejected_and_operator_review_is_terminal() -> None:
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

    with pytest.raises(RuntimeError, match="support budget exhausted"):
        TechnicalLeadReviewReasoner._validate_decision_against_specialist_result(
            _assessment("request_support", support_domain="application"),
            specialist_result=specialist_result,
        )

    TechnicalLeadReviewReasoner._validate_decision_against_specialist_result(
        _assessment("operator_action_required"),
        specialist_result=specialist_result,
    )
