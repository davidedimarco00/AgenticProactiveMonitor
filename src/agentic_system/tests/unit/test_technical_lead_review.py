import asyncio
import json
from pathlib import Path

import pytest

from agentic_system.agents.review import (
    TechnicalLeadReviewReasoner,
    _TechnicalLeadReviewOutput,
)
from agentic_system.reasoning import BDIReviewAssessment, TechnicalLeadReviewBDIRuntime


def _technical_lead_plan_path() -> Path:
    return (
        Path(__file__).parents[2]
        / "agentic_system"
        / "reasoning"
        / "plans"
        / "technical_lead.asl"
    )


def _valid_review_payload(*, decision: str = "resolve") -> dict:
    support = decision == "request_support"
    return {
        "decision": decision,
        "confidence": 0.91,
        "diagnosis_summary": (
            "The current diagnosis needs another domain before acceptance."
            if support
            else "The reported CPU anomaly has an evidence-backed explanation."
        ),
        "root_cause": "A CPU-bound workload caused the observed CPU saturation.",
        "rationale": (
            "Application evidence is required to validate the remaining uncertainty."
            if support
            else "Current runtime and metric evidence supports the causal explanation."
        ),
        "remediation_summary": (
            "Do not apply corrective changes before additional diagnosis."
            if support
            else "No immediate autonomous corrective action is required."
        ),
        "remediation_steps": [] if support else ["Continue monitoring after diagnosis."],
        "support_domain": "application" if support else None,
        "support_reason": "Application evidence is still required." if support else None,
    }


def _valid_review_json(*, decision: str = "resolve") -> str:
    return json.dumps(_valid_review_payload(decision=decision))


def _incident() -> dict:
    return {
        "incident_id": "INC-REVIEW-RETRY",
        "status": "UNDER_ANALYSIS",
        "severity": "MEDIUM",
        "entity": "processing-service",
        "anomaly": {"detector_id": "cpu-detector"},
        "agentic": {"primary_investigator": "system_engineer"},
    }


def _specialist_result(*, diagnosis_status: str = "confirmed") -> dict:
    confirmed = diagnosis_status == "confirmed"
    return {
        "status": "completed",
        "summary": "Live evidence was correlated against the reported CPU anomaly.",
        "diagnosis_status": diagnosis_status,
        "root_cause": (
            "A CPU-bound workload caused processing-service saturation."
            if diagnosis_status in {"confirmed", "probable"}
            else None
        ),
        "causal_chain": (
            [
                "CPU-bound workload executes on processing-service.",
                "CPU usage rises above the normal range.",
                "The CPU anomaly is observed by the detector.",
            ]
            if diagnosis_status in {"confirmed", "probable"}
            else []
        ),
        "confidence": 0.9 if confirmed else 0.62,
        "findings": ["CPU usage exceeded the expected range during the anomaly."],
        "evidence": [],
        "hypotheses": [] if confirmed else ["The workload may be application-driven."],
        "recommended_next_steps": [] if confirmed else ["Correlate application behaviour."],
        "assistance_required": not confirmed,
        "assistance_domain": None if confirmed else "application",
    }


def test_review_parser_accepts_resolve_decision() -> None:
    result = TechnicalLeadReviewReasoner._parse_response(
        f"```json\n{_valid_review_json()}\n```"
    )

    assert result.decision == "resolve"
    assert result.confidence == 0.91
    assert result.support_domain is None
    assert result.remediation_steps == ("Continue monitoring after diagnosis.",)


def test_review_structured_schema_constrains_decision_and_support_domain() -> None:
    schema = _TechnicalLeadReviewOutput.model_json_schema()
    properties = schema["properties"]

    assert set(properties["decision"]["enum"]) == {
        "resolve",
        "operator_action_required",
        "request_support",
    }
    support_schema = properties["support_domain"]
    enum_values = set()
    for option in support_schema.get("anyOf", []):
        enum_values.update(option.get("enum", []))
    assert enum_values == {"system", "network", "application", "software"}
    assert properties["confidence"]["minimum"] == 0.0
    assert properties["confidence"]["maximum"] == 1.0


def test_review_reasoner_retries_empty_response_before_escalating() -> None:
    class FakeProvider:
        def __init__(self) -> None:
            self.calls = 0
            self.output_schemas = []

        async def get_llm_response(
            self,
            context,
            tools=None,
            conversation_id=None,
            output_schema=None,
        ):
            self.calls += 1
            self.output_schemas.append(output_schema)
            if self.calls == 1:
                return {"text": "", "structured": None}
            return {"text": _valid_review_json(), "structured": None}

    provider = FakeProvider()
    reasoner = TechnicalLeadReviewReasoner(provider, max_attempts=3)  # type: ignore[arg-type]

    result = asyncio.run(
        reasoner.assess(
            incident=_incident(),
            specialist_result=_specialist_result(),
        )
    )

    assert provider.calls == 2
    assert all(schema is not None for schema in provider.output_schemas)
    assert result.decision == "resolve"
    assert result.confidence == 0.91


def test_review_reasoner_prefers_structured_provider_output() -> None:
    class FakeProvider:
        async def get_llm_response(
            self,
            context,
            tools=None,
            conversation_id=None,
            output_schema=None,
        ):
            assert output_schema is not None
            return {
                "text": None,
                "structured": output_schema(**_valid_review_payload()),
            }

    reasoner = TechnicalLeadReviewReasoner(FakeProvider())  # type: ignore[arg-type]
    result = asyncio.run(
        reasoner.assess(
            incident=_incident(),
            specialist_result=_specialist_result(),
        )
    )

    assert result.decision == "resolve"
    assert result.confidence == 0.91


def test_review_rejects_terminal_decision_for_probable_diagnosis_and_requests_support() -> None:
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
            decision = "operator_action_required" if self.calls == 1 else "request_support"
            payload = _valid_review_payload(decision=decision)
            if output_schema is not None:
                return {"text": None, "structured": output_schema(**payload)}
            return {"text": json.dumps(payload), "structured": None}

    provider = FakeProvider()
    reasoner = TechnicalLeadReviewReasoner(provider, max_attempts=3)  # type: ignore[arg-type]

    result = asyncio.run(
        reasoner.assess(
            incident=_incident(),
            specialist_result=_specialist_result(diagnosis_status="probable"),
        )
    )

    assert provider.calls == 2
    assert result.decision == "request_support"
    assert result.support_domain == "application"


def test_review_retries_when_support_domain_already_participated() -> None:
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
            payload = _valid_review_payload(decision="request_support")
            if self.calls == 1:
                payload["support_domain"] = "network"
                payload["support_reason"] = "Ask the network specialist to inspect again."
            else:
                payload["support_domain"] = "system"
                payload["support_reason"] = "System evidence can test the remaining hypothesis."
            if output_schema is not None:
                return {"text": None, "structured": output_schema(**payload)}
            return {"text": json.dumps(payload), "structured": None}

    provider = FakeProvider()
    reasoner = TechnicalLeadReviewReasoner(provider, max_attempts=3)  # type: ignore[arg-type]
    specialist_result = _specialist_result(diagnosis_status="probable")
    specialist_result["specialists_already_involved"] = ["network_engineer"]
    specialist_result["assistance_domain"] = "system"

    result = asyncio.run(
        reasoner.assess(
            incident=_incident(),
            specialist_result=specialist_result,
        )
    )

    assert provider.calls == 2
    assert result.decision == "request_support"
    assert result.support_domain == "system"


def test_terminal_review_validation_requires_confirmed_root_cause_and_causal_chain() -> None:
    assessment = TechnicalLeadReviewReasoner._parse_response(_valid_review_json())
    incomplete = _specialist_result()
    incomplete["causal_chain"] = []

    with pytest.raises(RuntimeError, match="root_cause and causal_chain"):
        TechnicalLeadReviewReasoner._validate_decision_against_specialist_result(
            assessment,
            specialist_result=incomplete,
        )


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
