import asyncio
from pathlib import Path

from agentic_system.agents.review import TechnicalLeadReviewReasoner
from agentic_system.reasoning import BDIReviewAssessment, TechnicalLeadReviewBDIRuntime


def _technical_lead_plan_path() -> Path:
    return (
        Path(__file__).parents[2]
        / "agentic_system"
        / "reasoning"
        / "plans"
        / "technical_lead.asl"
    )


def test_review_parser_accepts_resolve_decision() -> None:
    result = TechnicalLeadReviewReasoner._parse_response(
        """```json
        {
          "decision": "resolve",
          "confidence": 0.91,
          "diagnosis_summary": "The reported CPU anomaly is not currently reproduced.",
          "root_cause": "The evidence is consistent with a transient CPU spike.",
          "rationale": "Current runtime and metric evidence is healthy.",
          "remediation_summary": "No immediate corrective action is required.",
          "remediation_steps": ["Continue monitoring the service."],
          "support_domain": null,
          "support_reason": null
        }
        ```"""
    )

    assert result.decision == "resolve"
    assert result.confidence == 0.91
    assert result.support_domain is None
    assert result.remediation_steps == ("Continue monitoring the service.",)


def test_agentspeak_review_commits_critic_decision() -> None:
    runtime = TechnicalLeadReviewBDIRuntime(
        technical_lead_asl=str(_technical_lead_plan_path()),
        action_timeout_seconds=5.0,
    )

    async def fake_review() -> BDIReviewAssessment:
        return BDIReviewAssessment(
            decision="request_support",
            confidence=0.73,
            diagnosis_summary="The current evidence is not sufficient for a final diagnosis.",
            root_cause="Root cause remains unconfirmed.",
            rationale="Application behaviour must be correlated before accepting the hypothesis.",
            remediation_summary="Do not apply corrective changes before additional evidence.",
            remediation_steps=(),
            support_domain="application",
            support_reason="Application logs and service behaviour must be reviewed.",
        )

    result = asyncio.run(
        runtime.review_specialist_result(
            incident_id="INC-REVIEW-001",
            review_callback=fake_review,
        )
    )

    assert result.incident_id == "INC-REVIEW-001"
    assert result.goal == "review_investigation"
    assert result.review_intention == "review_specialist_result"
    assert result.decision_intention == "commit_review_decision"
    assert result.decision == "request_support"
    assert result.support_domain == "application"
