import pytest

from src.agentic_system.simple.models import (
    CriticReview,
    Diagnosis,
    DiagnosticCheck,
    Hypothesis,
    IncidentContext,
)
from src.agentic_system.simple.services import CriticService, ReasoningService


class FakeOllama:
    async def structured(self, *, response_model, system_prompt, payload):
        if response_model is Diagnosis:
            preferred = Hypothesis(
                hypothesis_id="h1",
                cause="Downstream latency creates retries",
                component="invented-host",
                confidence=0.82,
                supporting_evidence=["timeout markers"],
            )
            return Diagnosis(
                hypotheses=[preferred],
                preferred_hypothesis=preferred,
                required_checks=[
                    DiagnosticCheck(action="shutdown_host", target="machine-03"),
                    DiagnosticCheck(action="query_logs", target="machine-04"),
                ],
                explanation="The dependency may be the root cause.",
                root_cause_summary="Possible downstream latency",
            )
        return CriticReview(
            accepted=True,
            confidence=0.40,
            reason="Weak evidence",
            required_checks=[DiagnosticCheck(action="query_metrics", target="machine-04")],
        )


def incident() -> IncidentContext:
    return IncidentContext(
        detector_id="d1",
        host_id="machine-03",
        metric_name="cpu.usage_active",
        anomaly_score=0.91,
        round=1,
        evidence=[{"hosts": ["machine-03", "machine-04"], "metrics": {}, "logs": {}}],
    )


@pytest.mark.asyncio
async def test_reasoning_normalises_targets_and_filters_unsafe_checks():
    result = await ReasoningService(FakeOllama()).diagnose(incident())
    assert result.preferred_hypothesis.component == "machine-03"
    assert [check.action for check in result.required_checks] == ["query_logs"]
    assert result.required_checks[0].target == "machine-04"


@pytest.mark.asyncio
async def test_critic_cannot_accept_with_low_confidence():
    context = incident()
    context.diagnosis = await ReasoningService(FakeOllama()).diagnose(context)
    review = await CriticService(FakeOllama()).review(context)
    assert review.accepted is False
    assert review.required_checks[0].action == "query_metrics"
