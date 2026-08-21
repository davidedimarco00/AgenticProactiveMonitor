from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from langchain_core.messages import AIMessage
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


class FakeReasoningProvider:
    model = "ollama/gemma-test"
    base_url = "http://127.0.0.1:11434"

    def __init__(self, decisions: list[dict[str, Any]]) -> None:
        self.decisions = list(decisions)
        self.calls = 0

    async def get_llm_response(
        self,
        context,
        tools=None,
        conversation_id=None,
        output_schema=None,
    ) -> dict[str, Any]:
        self.calls += 1
        if not self.decisions:
            raise AssertionError("Gemma reasoning received more calls than expected")
        payload = self.decisions.pop(0)
        assert output_schema is not None
        return {"text": None, "structured": output_schema(**payload)}


class FakeToolProvider:
    model = "ollama/qwen-test"
    base_url = "http://127.0.0.1:11434"


class FakeToolSelector:
    def __init__(self, calls: list[dict[str, Any] | None]) -> None:
        self.calls = list(calls)
        self.prompts: list[Any] = []

    async def ainvoke(self, messages: Any) -> AIMessage:
        self.prompts.append(messages)
        if not self.calls:
            raise AssertionError("Qwen selector received more calls than expected")
        call = self.calls.pop(0)
        if call is None:
            return AIMessage(content="No tool selected")
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "call-test",
                    "name": call["name"],
                    "args": dict(call.get("args") or {}),
                    "type": "tool_call",
                }
            ],
        )


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

GATHER = {
    "action": "gather_evidence",
    "decision_summary": "Live CPU evidence is required before deciding whether saturation is real.",
    "current_hypothesis": "A CPU-bound workload may be saturating processing-service.",
    "evidence_needed": "Current processing-service CPU and system load.",
}

FINISH = {
    "action": "finish",
    "decision_summary": "Live CPU evidence is sufficient to close this specialist pass.",
    "current_hypothesis": "A CPU-bound workload is the leading explanation.",
    "evidence_needed": None,
}


def _executor(
    *,
    reasoning: list[dict[str, Any]] | None = None,
    tool: FakeTool | None = None,
    selector_calls: list[dict[str, Any] | None] | None = None,
    max_steps: int = 10,
    finalizer: FakeFinalizer | None = None,
    traces: list[dict[str, Any]] | None = None,
) -> SpecialistReActExecutor:
    trace_list = traces if traces is not None else []
    return SpecialistReActExecutor(
        provider=FakeReasoningProvider(reasoning or [GATHER, FINISH]),  # type: ignore[arg-type]
        tool_provider=FakeToolProvider(),  # type: ignore[arg-type]
        context=ContextManager(system_prompt="You are a test system specialist."),
        tools=[tool or FakeTool()],  # type: ignore[list-item]
        max_steps=max_steps,
        tool_timeout_seconds=1.0,
        tool_selector=FakeToolSelector(
            selector_calls
            or [{"name": (tool or FakeTool()).name, "args": {"service": "processing-service"}}]
        ),
        finalizer=finalizer or FakeFinalizer([FINAL_RESULT]),
        trace_sink=trace_list.append,
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


def test_hybrid_react_uses_gemma_reasoning_qwen_action_and_gemma_finalization() -> None:
    traces: list[dict[str, Any]] = []
    tool = FakeTool()
    finalizer = FakeFinalizer([FINAL_RESULT])
    executor = _executor(tool=tool, finalizer=finalizer, traces=traces)

    result = asyncio.run(_investigate(executor))

    assert tool.calls == [{"service": "processing-service"}]
    assert result.tools_used == ("get_system_load",)
    assert result.react_steps == 2
    assert result.diagnosis_status == "probable"
    assert result.root_cause == FINAL_RESULT["root_cause"]
    assert result.assistance_required is True
    assert result.assistance_domain == "application"
    assert result.evidence[0]["observation"]["cpu_percent"] == 388.2
    assert len(finalizer.calls) == 1
    assert "Gemma operational reasoning summaries" in str(finalizer.calls[0])

    actions = [item["action"] for item in traces]
    assert actions == ["react_started", "reason", "select_tool", "observe", "reason", "diagnosis"]
    assert traces[2]["tool"] == "get_system_load"


def test_langchain_tool_adapter_executes_existing_spade_mcp_tool() -> None:
    tool = FakeTool()
    executor = _executor(tool=tool)

    raw = asyncio.run(
        executor._langchain_tools[0].ainvoke({"service": "processing-service"})
    )
    payload = json.loads(raw)

    assert tool.calls == [{"service": "processing-service"}]
    assert payload["success"] is True
    assert payload["observation"]["cpu_percent"] == 388.2


def test_qwen_selection_failure_is_bounded() -> None:
    executor = _executor(
        reasoning=[GATHER],
        selector_calls=[None, None],
        max_steps=1,
    )

    with pytest.raises(ReActInvestigationError, match="Qwen tool selection failed"):
        asyncio.run(_investigate(executor))


def test_confirmed_diagnosis_stops_without_peer_request() -> None:
    finalizer = FakeFinalizer([CONFIRMED_RESULT])
    result = asyncio.run(_investigate(_executor(finalizer=finalizer)))

    assert result.diagnosis_status == "confirmed"
    assert result.root_cause == CONFIRMED_RESULT["root_cause"]
    assert result.causal_chain == tuple(CONFIRMED_RESULT["causal_chain"])
    assert result.assistance_required is False
    assert result.assistance_domain is None


def test_non_confirmed_finalization_cannot_silently_stop_without_peer_assistance() -> None:
    invalid = {
        **FINAL_RESULT,
        "diagnosis_status": "inconclusive",
        "root_cause": None,
        "causal_chain": [],
        "assistance_required": False,
        "assistance_domain": None,
    }
    finalizer = FakeFinalizer([invalid, invalid])

    with pytest.raises(
        ReActInvestigationError,
        match="probable or inconclusive diagnosis must request an assistance domain",
    ):
        asyncio.run(_investigate(_executor(finalizer=finalizer)))

    assert len(finalizer.calls) == 2


def test_search_knowledge_is_traced_as_rag_retrieval() -> None:
    class KnowledgeTool(FakeTool):
        def __init__(self) -> None:
            super().__init__("apm_mcp_search_knowledge")
            self.description = "Search Qdrant project knowledge."

        async def execute(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(dict(kwargs))
            return {
                "collection": "monitored-system",
                "results": [
                    {
                        "score": 0.91,
                        "filename": "data_service.md",
                        "text": "processing-service depends on data-service",
                    }
                ],
            }

    traces: list[dict[str, Any]] = []
    tool = KnowledgeTool()
    result = asyncio.run(
        _investigate(
            _executor(
                tool=tool,
                selector_calls=[
                    {
                        "name": "apm_mcp_search_knowledge",
                        "args": {"query": "processing-service dependency"},
                    }
                ],
                traces=traces,
            )
        )
    )

    assert result.tools_used == ("apm_mcp_search_knowledge",)
    rag_events = [item for item in traces if item["action"] == "rag_retrieval"]
    assert len(rag_events) == 1
    assert rag_events[0]["details"]["source"] == "Qdrant RAG"
    assert "data_service.md" in rag_events[0]["outcome"]
