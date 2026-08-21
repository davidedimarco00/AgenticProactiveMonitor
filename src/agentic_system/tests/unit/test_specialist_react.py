from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolMessage
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


class FakeProvider:
    model = "ollama/qwen-test"
    base_url = "http://127.0.0.1:11434"


class FakeCompiledAgent:
    def __init__(self, states: list[dict[str, Any]]) -> None:
        self.states = list(states)
        self.calls: list[dict[str, Any]] = []

    async def ainvoke(self, state: dict[str, Any], config=None) -> dict[str, Any]:
        self.calls.append({"state": state, "config": config})
        if not self.states:
            raise AssertionError("LangChain agent received more calls than expected")
        return self.states.pop(0)


class FakeFinalizer:
    def __init__(self, outputs: list[Any]) -> None:
        self.outputs = list(outputs)
        self.calls: list[Any] = []

    async def ainvoke(self, messages: Any) -> Any:
        self.calls.append(messages)
        if not self.outputs:
            raise AssertionError("Structured finalizer received more calls than expected")
        output = self.outputs.pop(0)
        if isinstance(output, BaseException):
            raise output
        return output


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


def _messages(*, success: bool = True) -> list[Any]:
    call = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "call-load-1",
                "name": "get_system_load",
                "args": {"service": "processing-service"},
                "type": "tool_call",
            }
        ],
    )
    if success:
        content = json.dumps(
            {
                "success": True,
                "observation": {
                    "service": "processing-service",
                    "cpu_percent": 388.2,
                    "source": "live_mcp_test",
                },
            }
        )
    else:
        content = json.dumps({"success": False, "error": "RuntimeError: MCP unavailable"})
    result = ToolMessage(
        content=content,
        tool_call_id="call-load-1",
        name="get_system_load",
    )
    return [call, result, AIMessage(content="Diagnostic closure ready.")]


def _executor(
    agent: FakeCompiledAgent,
    tool: FakeTool | None = None,
    *,
    max_steps: int = 10,
    finalizer: FakeFinalizer | None = None,
) -> SpecialistReActExecutor:
    return SpecialistReActExecutor(
        provider=FakeProvider(),  # type: ignore[arg-type]
        context=ContextManager(system_prompt="You are a test system specialist."),
        tools=[tool or FakeTool()],  # type: ignore[list-item]
        max_steps=max_steps,
        tool_timeout_seconds=1.0,
        agent=agent,
        finalizer=finalizer or FakeFinalizer([FINAL_RESULT]),
    )


def _investigate(executor: SpecialistReActExecutor):
    return executor.investigate(
        task_id="TASK-REACT-001",
        incident_id="INC-REACT-001",
        agent_role="system_engineer",
        severity="HIGH",
        entity="CPU-processing-service",
        anomaly={"detector_name": "CPU-processing-service", "grade": 1.0},
    )


def test_react_finalizes_evidence_without_agent_structured_response() -> None:
    finalizer = FakeFinalizer([FINAL_RESULT])
    agent = FakeCompiledAgent([{"messages": _messages()}])

    result = asyncio.run(_investigate(_executor(agent, finalizer=finalizer)))

    assert len(finalizer.calls) == 1
    assert result.task_id == "TASK-REACT-001"
    assert result.tools_used == ("get_system_load",)
    assert result.react_steps == 2
    assert result.diagnosis_status == "probable"
    assert result.root_cause == FINAL_RESULT["root_cause"]
    assert result.causal_chain == tuple(FINAL_RESULT["causal_chain"])
    assert result.confidence == 0.82
    assert result.assistance_required is True
    assert result.assistance_domain == "application"
    assert len(result.evidence) == 1
    assert result.evidence[0]["success"] is True
    assert result.evidence[0]["observation"]["cpu_percent"] == 388.2
    assert result.conversation_id == "react:system_engineer:INC-REACT-001:TASK-REACT-001"

    finalizer_prompt = str(finalizer.calls[0])
    assert "Collected tool evidence" in finalizer_prompt
    assert "388.2" in finalizer_prompt


def test_langchain_adapter_executes_existing_spade_mcp_tool() -> None:
    tool = FakeTool()
    agent = FakeCompiledAgent([])
    executor = _executor(agent, tool)

    raw = asyncio.run(
        executor._langchain_tools[0].ainvoke({"service": "processing-service"})
    )
    payload = json.loads(raw)

    assert tool.calls == [{"service": "processing-service"}]
    assert payload["success"] is True
    assert payload["observation"]["cpu_percent"] == 388.2


def test_react_retries_once_if_model_attempts_to_conclude_without_live_evidence() -> None:
    no_evidence = {"messages": [AIMessage(content="I can answer directly.")]}
    agent = FakeCompiledAgent([no_evidence, no_evidence])
    finalizer = FakeFinalizer([FINAL_RESULT])

    with pytest.raises(ReActInvestigationError, match="No operational tool"):
        asyncio.run(
            _investigate(_executor(agent, max_steps=2, finalizer=finalizer))
        )

    assert len(agent.calls) == 2
    assert finalizer.calls == []
    retry_messages = agent.calls[1]["state"]["messages"]
    assert "No live operational evidence" in str(retry_messages[-1]["content"])


def test_react_records_tool_failure_without_killing_investigation_contract() -> None:
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
    agent = FakeCompiledAgent([{"messages": _messages(success=False)}])
    finalizer = FakeFinalizer([low_confidence])

    result = asyncio.run(
        _investigate(_executor(agent, finalizer=finalizer))
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
    agent = FakeCompiledAgent([{"messages": _messages()}])
    finalizer = FakeFinalizer([invalid, invalid])

    with pytest.raises(
        ReActInvestigationError,
        match="probable or inconclusive diagnosis must request an assistance domain",
    ):
        asyncio.run(
            _investigate(_executor(agent, finalizer=finalizer))
        )

    assert len(finalizer.calls) == 2


def test_confirmed_diagnosis_requires_root_cause_and_causal_chain_without_peer_request() -> None:
    agent = FakeCompiledAgent([{"messages": _messages()}])
    finalizer = FakeFinalizer([CONFIRMED_RESULT])

    result = asyncio.run(
        _investigate(_executor(agent, finalizer=finalizer))
    )

    assert result.diagnosis_status == "confirmed"
    assert result.root_cause == CONFIRMED_RESULT["root_cause"]
    assert result.causal_chain == tuple(CONFIRMED_RESULT["causal_chain"])
    assert result.assistance_required is False
    assert result.assistance_domain is None
