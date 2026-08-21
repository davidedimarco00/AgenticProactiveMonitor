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
            }
        )
        if not self.responses:
            raise AssertionError("Provider received more calls than expected")
        return self.responses.pop(0)


FINAL_RESULT = {
    "summary": "The processing service shows a high CPU condition that requires further correlation.",
    "confidence": 0.82,
    "findings": ["Live system load reports CPU at 388.2 percent for processing-service."],
    "hypotheses": ["A CPU-bound workload may be saturating the processing service."],
    "recommended_next_steps": ["Correlate the CPU interval with application logs."],
    "assistance_required": True,
    "assistance_domain": "application",
}


def _executor(provider: SequenceProvider, tool: FakeTool, *, max_steps: int = 6):
    return SpecialistReActExecutor(
        provider=provider,  # type: ignore[arg-type]
        context=ContextManager(system_prompt="You are a test specialist."),
        tools=[tool],  # type: ignore[list-item]
        max_steps=max_steps,
        tool_timeout_seconds=1.0,
    )


def test_react_executes_real_tool_observation_before_structured_result() -> None:
    tool = FakeTool()
    provider = SequenceProvider(
        [
            {
                "text": None,
                "tool_calls": [
                    {
                        "id": "call-load-1",
                        "name": "get_system_load",
                        "arguments": {"service": "processing-service"},
                    }
                ],
                "structured": None,
            },
            {"text": "Evidence is sufficient.", "tool_calls": [], "structured": None},
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
    assert result.react_steps == 2
    assert result.tools_used == ("get_system_load",)
    assert result.confidence == 0.82
    assert result.assistance_required is True
    assert result.assistance_domain == "application"
    assert len(result.evidence) == 1
    assert result.evidence[0]["success"] is True
    assert result.evidence[0]["observation"]["cpu_percent"] == 388.2
    assert result.conversation_id == "react:system_engineer:INC-REACT-001:TASK-REACT-001"


def test_react_repairs_invalid_final_json_once() -> None:
    tool = FakeTool()
    provider = SequenceProvider(
        [
            {
                "text": None,
                "tool_calls": [
                    {
                        "id": "call-load-1",
                        "name": "get_system_load",
                        "arguments": {"service": "processing-service"},
                    }
                ],
                "structured": None,
            },
            {"text": "Enough evidence.", "tool_calls": [], "structured": None},
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
    assert len(provider.calls) == 4


def test_react_retries_empty_finalization_without_repeating_tool_work() -> None:
    tool = FakeTool()
    provider = SequenceProvider(
        [
            {
                "text": None,
                "tool_calls": [
                    {
                        "id": "call-load-once",
                        "name": "get_system_load",
                        "arguments": {"service": "processing-service"},
                    }
                ],
                "structured": None,
            },
            {"text": "Evidence is sufficient.", "tool_calls": [], "structured": None},
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
    assert len(provider.calls) == 4
    assert provider.calls[2]["tools"] == []
    assert provider.calls[3]["tools"] == []


def test_react_refuses_to_conclude_without_any_operational_tool_attempt() -> None:
    tool = FakeTool()
    provider = SequenceProvider(
        [
            {"text": "I can answer directly.", "tool_calls": [], "structured": None},
            {"text": "Still no tool.", "tool_calls": [], "structured": None},
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


def test_react_records_tool_failure_as_observation_instead_of_inventing_success() -> None:
    class FailingTool(FakeTool):
        async def execute(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(dict(kwargs))
            raise RuntimeError("MCP unavailable")

    tool = FailingTool()
    low_confidence = {
        **FINAL_RESULT,
        "summary": "Live evidence collection failed, so no strong conclusion can be made.",
        "confidence": 0.15,
        "findings": [],
        "assistance_required": False,
        "assistance_domain": None,
    }
    provider = SequenceProvider(
        [
            {
                "text": None,
                "tool_calls": [
                    {
                        "id": "call-fail-1",
                        "name": "get_system_load",
                        "arguments": {"service": "processing-service"},
                    }
                ],
                "structured": None,
            },
            {"text": "No more evidence available.", "tool_calls": [], "structured": None},
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
    assert result.confidence == 0.15