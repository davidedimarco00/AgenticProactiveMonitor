from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from spade_llm.context import ContextManager

from agentic_system.reasoning import ReActInvestigationError, SpecialistReActExecutor


class FakeTool:
    def __init__(self, name: str = "get_system_load") -> None:
        self.name = name
        self.description = "Return live system load for a monitored entity."
        self.parameters = {
            "type": "object",
            "properties": {"service": {"type": "string"}},
        }
        self.calls: list[dict[str, Any]] = []

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        return {
            "service": kwargs.get("service"),
            "cpu_percent": 388.2,
            "source": "live_mcp_test",
        }


class SequenceProvider:
    model = "fake/hybrid"

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def get_llm_response(
        self,
        context,
        tools=None,
        conversation_id=None,
        output_schema=None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "tools": [tool.name for tool in tools] if tools else [],
                "conversation_id": conversation_id,
                "prompt": context.get_prompt(conversation_id),
                "output_schema": output_schema,
            }
        )
        if not self.responses:
            raise AssertionError("Provider received more calls than expected")
        return self.responses.pop(0)


FINAL_RESULT = {
    "summary": "CPU saturation is likely application-driven and needs cross-domain correlation.",
    "diagnosis_status": "probable",
    "root_cause": "A CPU-bound application workload is probably saturating processing-service.",
    "causal_chain": [
        "A CPU-bound workload consumes processing-service compute capacity.",
        "Live system load reports CPU at 388.2 percent.",
        "The resource anomaly is observed on processing-service.",
    ],
    "confidence": 0.82,
    "findings": ["Live system load reports CPU at 388.2 percent for processing-service."],
    "hypotheses": ["A CPU-bound workload may be saturating the processing service."],
    "recommended_next_steps": ["Correlate the same interval with application evidence."],
    "assistance_required": True,
    "assistance_domain": "application",
}

CONFIRMED_RESULT = {
    "summary": "The high CPU anomaly is caused by the observed CPU-bound processing workload.",
    "diagnosis_status": "confirmed",
    "root_cause": "A CPU-bound processing workload saturates processing-service.",
    "causal_chain": [
        "CPU-bound processing workload executes on processing-service.",
        "CPU reaches 388.2 percent in live telemetry.",
        "The CPU detector observes the resulting resource anomaly.",
    ],
    "confidence": 0.94,
    "findings": ["Live system load reports CPU at 388.2 percent for processing-service."],
    "hypotheses": [],
    "recommended_next_steps": [],
    "assistance_required": False,
    "assistance_domain": None,
}


def _executor(provider: SequenceProvider, tool: FakeTool, *, max_steps: int = 10):
    return SpecialistReActExecutor(
        provider=provider,  # type: ignore[arg-type]
        context=ContextManager(system_prompt="You are a test specialist."),
        tools=[tool],  # type: ignore[list-item]
        max_steps=max_steps,
        tool_timeout_seconds=1.0,
    )


def _tool_call(call_id: str = "call-load-1") -> dict[str, Any]:
    return {
        "text": None,
        "tool_calls": [
            {
                "id": call_id,
                "name": "get_system_load",
                "arguments": {"service": "processing-service"},
            }
        ],
        "structured": None,
    }


def _stop(text: str = "Evidence may be sufficient.") -> dict[str, Any]:
    return {"text": text, "tool_calls": [], "structured": None}


def test_react_executes_tool_and_challenges_early_stop_before_final_result() -> None:
    tool = FakeTool()
    provider = SequenceProvider(
        [
            _tool_call(),
            _stop("I think the evidence is sufficient."),
            _stop("No further in-domain evidence is required; peer correlation is needed."),
            {"text": json.dumps(FINAL_RESULT), "tool_calls": [], "structured": None},
        ]
    )

    result = asyncio.run(
        _executor(provider, tool).investigate(
            task_id="TASK-REACT-001",
            incident_id="INC-REACT-001",
            agent_role="system_engineer",
            severity="HIGH",
            entity="CPU-processing-service",
            anomaly={"detector_name": "CPU-processing-service", "grade": 1.0},
        )
    )

    assert tool.calls == [{"service": "processing-service"}]
    assert result.task_id == "TASK-REACT-001"
    assert result.react_steps == 3
    assert result.tools_used == ("get_system_load",)
    assert result.diagnosis_status == "probable"
    assert result.root_cause == FINAL_RESULT["root_cause"]
    assert result.causal_chain == tuple(FINAL_RESULT["causal_chain"])
    assert result.confidence == 0.82
    assert result.assistance_required is True
    assert result.assistance_domain == "application"
    assert len(result.evidence) == 1
    assert result.evidence[0]["success"] is True
    assert result.evidence[0]["observation"]["cpu_percent"] == 388.2
    assert "diagnostic closure check" in provider.calls[2]["prompt"]
    assert provider.calls[-1]["output_schema"] is not None
    assert result.conversation_id == "react:system_engineer:INC-REACT-001:TASK-REACT-001"


def test_react_repairs_invalid_final_json_once() -> None:
    tool = FakeTool()
    provider = SequenceProvider(
        [
            _tool_call(),
            _stop(),
            _stop("Peer evidence is required before confirmation."),
            {"text": "not-json", "tool_calls": [], "structured": None},
            {"text": json.dumps(FINAL_RESULT), "tool_calls": [], "structured": None},
        ]
    )

    result = asyncio.run(
        _executor(provider, tool).investigate(
            task_id="TASK-REACT-REPAIR",
            incident_id="INC-REACT-REPAIR",
            agent_role="system_engineer",
            severity="MEDIUM",
            entity="processing-service",
            anomaly={},
        )
    )

    assert result.summary == FINAL_RESULT["summary"]
    assert len(provider.calls) == 5


def test_react_retries_empty_finalization_without_repeating_tool_work() -> None:
    tool = FakeTool()
    provider = SequenceProvider(
        [
            _tool_call("call-load-once"),
            _stop(),
            _stop("Another domain is required."),
            {"text": "", "tool_calls": [], "structured": None},
            {"text": json.dumps(FINAL_RESULT), "tool_calls": [], "structured": None},
        ]
    )

    result = asyncio.run(
        _executor(provider, tool).investigate(
            task_id="TASK-EMPTY-FINAL",
            incident_id="INC-EMPTY-FINAL",
            agent_role="system_engineer",
            severity="MEDIUM",
            entity="processing-service",
            anomaly={},
        )
    )

    assert result.summary == FINAL_RESULT["summary"]
    assert tool.calls == [{"service": "processing-service"}]
    assert len(provider.calls) == 5
    assert provider.calls[3]["tools"] == []
    assert provider.calls[4]["tools"] == []


def test_react_refuses_to_conclude_without_any_operational_tool_attempt() -> None:
    tool = FakeTool()
    provider = SequenceProvider(
        [
            _stop("I can answer directly."),
            _stop("Still no tool."),
        ]
    )

    with pytest.raises(ReActInvestigationError, match="No operational tool"):
        asyncio.run(
            _executor(provider, tool, max_steps=2).investigate(
                task_id="TASK-NO-TOOL",
                incident_id="INC-NO-TOOL",
                agent_role="system_engineer",
                severity="MEDIUM",
                entity="processing-service",
                anomaly={},
            )
        )


def test_react_records_tool_failure_and_requests_diagnostic_assistance() -> None:
    class FailingTool(FakeTool):
        async def execute(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(dict(kwargs))
            raise RuntimeError("MCP unavailable")

    tool = FailingTool()
    low_confidence = {
        "summary": "Live evidence collection failed, so no root cause can be established.",
        "diagnosis_status": "inconclusive",
        "root_cause": None,
        "causal_chain": [],
        "confidence": 0.15,
        "findings": [],
        "hypotheses": [],
        "recommended_next_steps": ["Obtain independent application-domain evidence."],
        "assistance_required": True,
        "assistance_domain": "application",
    }
    provider = SequenceProvider(
        [
            _tool_call("call-fail-1"),
            _stop("No reliable conclusion yet."),
            _stop("Peer evidence is required because the live system tool failed."),
            {"text": json.dumps(low_confidence), "tool_calls": [], "structured": None},
        ]
    )

    result = asyncio.run(
        _executor(provider, tool).investigate(
            task_id="TASK-FAIL-OBS",
            incident_id="INC-FAIL-OBS",
            agent_role="system_engineer",
            severity="MEDIUM",
            entity="processing-service",
            anomaly={},
        )
    )

    assert result.evidence[0]["success"] is False
    assert "MCP unavailable" in result.evidence[0]["observation"]["error"]
    assert result.diagnosis_status == "inconclusive"
    assert result.root_cause is None
    assert result.assistance_required is True
    assert result.confidence == 0.15


def test_non_confirmed_finalization_cannot_silently_stop_without_peer_assistance() -> None:
    invalid = {
        **FINAL_RESULT,
        "diagnosis_status": "inconclusive",
        "root_cause": None,
        "causal_chain": [],
        "assistance_required": False,
        "assistance_domain": None,
    }

    with pytest.raises(
        ReActInvestigationError,
        match="probable or inconclusive diagnosis must request an assistance domain",
    ):
        SpecialistReActExecutor._parse_final_payload(invalid)


def test_confirmed_diagnosis_requires_root_cause_and_causal_chain_without_peer_request() -> None:
    payload = SpecialistReActExecutor._parse_final_payload(CONFIRMED_RESULT)

    assert payload["diagnosis_status"] == "confirmed"
    assert payload["root_cause"] == CONFIRMED_RESULT["root_cause"]
    assert payload["assistance_required"] is False
