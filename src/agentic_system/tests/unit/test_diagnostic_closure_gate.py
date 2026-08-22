import asyncio

from agentic_system.reasoning.diagnostic_react import _DiagnosticFinalOutput
from agentic_system.reasoning.langchain_agent import ReActInvestigationError, _ReasoningDecision
from agentic_system.reasoning.observation_aware_react import (
    ObservationAwareEvidence,
    SpecialistReActExecutor,
)


class _Provider:
    model = "ollama/test-model"
    base_url = "http://127.0.0.1:11434"


class _Context:
    def add_message_dict(self, *args, **kwargs):
        return None

    def add_assistant_message(self, *args, **kwargs):
        return None

    def add_tool_result(self, *args, **kwargs):
        return None


class _ScriptedExecutor(SpecialistReActExecutor):
    def __init__(self, decisions, *, fail_first_finalization: bool = False) -> None:
        self.max_steps = len(decisions)
        self.reasoning_provider = _Provider()
        self.tool_provider = _Provider()
        self.provider = self.reasoning_provider
        self.context = _Context()
        self._decisions = list(decisions)
        self._tool_index = 0
        self.finalize_calls = 0
        self.fail_first_finalization = fail_first_finalization
        self.traces = []

    async def _reason(self, *, assignment, evidence, decisions):
        return self._decisions[len(decisions)]

    async def _select_tool(self, *, assignment, evidence_needed, evidence):
        names = ["apm_mcp_get_metrics", "apm_mcp_get_processes", "apm_mcp_inspect_process"]
        name = names[min(self._tool_index, len(names) - 1)]
        self._tool_index += 1
        return name, {"host_id": assignment["entity"]}

    async def _execute_tool(self, *, step, tool_name, arguments):
        return ObservationAwareEvidence(
            step=step,
            tool=tool_name,
            arguments=arguments,
            observation={"status": "ok", "tool": tool_name, "cpu_percent": 390.0},
            reasoning_observation={
                "status": "ok",
                "tool": tool_name,
                "cpu_percent": 390.0,
            },
            success=True,
        )

    async def _finalize(self, *, assignment, evidence, decisions):
        self.finalize_calls += 1
        if self.fail_first_finalization and self.finalize_calls == 1:
            raise ReActInvestigationError(
                "Gemma structured diagnostic finalization failed: Value error, "
                "probable diagnosis requires a root_cause"
            )
        return _DiagnosticFinalOutput(
            summary="CPU saturation is explained by a CPU-bound worker process.",
            diagnosis_status="probable",
            root_cause="A CPU-bound worker process is saturating processing-service.",
            causal_chain=[
                "CPU-bound worker execution",
                "sustained container CPU consumption",
                "OpenSearch CPU anomaly",
            ],
            confidence=0.85,
            findings=["Live CPU evidence shows sustained saturation."],
            hypotheses=[],
            recommended_next_steps=[],
            assistance_required=False,
            assistance_domain=None,
        )

    async def _emit_trace(self, **kwargs):
        self.traces.append(kwargs)

    @staticmethod
    def _observation_summary(observation, *, success):
        return "ok" if success else "error"


def _decision(action, summary, *, hypothesis=None, evidence_needed=None):
    return _ReasoningDecision(
        action=action,
        decision_summary=summary,
        current_hypothesis=hypothesis,
        evidence_needed=evidence_needed,
    )


def _run(executor: _ScriptedExecutor):
    return asyncio.run(
        executor.investigate(
            task_id="TASK-CPU-001",
            incident_id="INC-CPU-001",
            agent_role="system_engineer",
            severity="high",
            entity="processing-service",
            anomaly={"metric": "cpu", "grade": 1.0},
        )
    )


def test_finish_without_concrete_hypothesis_continues_investigation() -> None:
    executor = _ScriptedExecutor(
        [
            _decision(
                "gather_evidence",
                "Confirm the resource anomaly with live evidence.",
                hypothesis="CPU anomaly cause not yet identified",
                evidence_needed="Current CPU/runtime evidence for processing-service.",
            ),
            _decision(
                "finish",
                "The anomaly is visible, but no cause has been identified.",
                hypothesis=None,
            ),
            _decision(
                "gather_evidence",
                "Identify the process responsible for CPU consumption.",
                hypothesis="A CPU-bound process may be causing the saturation.",
                evidence_needed="Process-level CPU consumption inside processing-service.",
            ),
            _decision(
                "finish",
                "The process evidence supports a concrete causal hypothesis.",
                hypothesis="A CPU-bound worker process is saturating processing-service.",
            ),
        ]
    )

    result = _run(executor)

    assert result.diagnosis_status == "probable"
    assert result.root_cause is not None
    assert result.tools_used == ("apm_mcp_get_metrics", "apm_mcp_get_processes")
    assert executor.finalize_calls == 1
    assert any(trace["action"] == "diagnostic_closure_rejected" for trace in executor.traces)


def test_semantic_finalization_error_returns_to_react_instead_of_failing() -> None:
    executor = _ScriptedExecutor(
        [
            _decision(
                "gather_evidence",
                "Confirm the anomaly.",
                hypothesis="A CPU-bound process may be causing the saturation.",
                evidence_needed="Current CPU evidence.",
            ),
            _decision(
                "finish",
                "Attempt evidence-backed closure.",
                hypothesis="A CPU-bound process may be causing the saturation.",
            ),
            _decision(
                "gather_evidence",
                "Collect process-level evidence after rejected closure.",
                hypothesis="A CPU-bound worker process may be causing the saturation.",
                evidence_needed="Process-level CPU consumption and command identity.",
            ),
            _decision(
                "finish",
                "Process-level evidence now supports the causal hypothesis.",
                hypothesis="A CPU-bound worker process is saturating processing-service.",
            ),
        ],
        fail_first_finalization=True,
    )

    result = _run(executor)

    assert result.diagnosis_status == "probable"
    assert result.root_cause == "A CPU-bound worker process is saturating processing-service."
    assert executor.finalize_calls == 2
    rejected = [
        trace for trace in executor.traces if trace["action"] == "diagnostic_closure_rejected"
    ]
    assert rejected
    assert "probable diagnosis requires a root_cause" in rejected[0]["details"]["finalization_error"]
