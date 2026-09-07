from __future__ import annotations

import asyncio
import json

import agentic_system.reasoning.evaluation_react as evaluation_module
from agentic_system.reasoning.evaluation_react import _EvaluationDiagnosticFinalizer


class _FakeResponse:
    def __init__(self, body: dict) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._body


class _FakeAsyncClient:
    payloads: list[dict] = []
    responses: list[dict] = []

    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, json: dict):
        type(self).payloads.append(json)
        if not type(self).responses:
            raise AssertionError("No fake Ollama response configured")
        return _FakeResponse(type(self).responses.pop(0))


def _finalizer() -> _EvaluationDiagnosticFinalizer:
    return _EvaluationDiagnosticFinalizer(
        model="gemma4:e4b",
        base_url="http://ollama:11434",
        timeout_seconds=60.0,
        context_size=8192,
    )


def _large_cpu_finalization_messages() -> list[dict[str, str]]:
    assignment = {
        "task_id": "TASK-CPU-001",
        "incident_id": "INC-CPU-001",
        "agent_role": "system_engineer",
        "severity": "HIGH",
        "entity": "processing-service",
        "anomaly": {
            "detector_name": "CPU-processing-service",
            "detector_type": "SINGLE_ENTITY",
            "measurement_name": "docker_container_cpu",
            "feature_field": "docker_container_cpu.usage_percent",
            "affected_component": "processing-service",
            "data_start_time": 100,
            "data_end_time": 200,
            "noise": "x" * 6000,
        },
        "incident_anchor": {
            "detector_name": "CPU-processing-service",
            "detector_type": "SINGLE_ENTITY",
            "observed_signal": "cpu_utilization",
            "measurement_name": "docker_container_cpu",
            "feature_field": "docker_container_cpu.usage_percent",
            "affected_component": "processing-service",
            "reported_entity": "processing-service",
            "primary_diagnostic_question": "What caused the CPU pressure?",
        },
        "static_project_grounding": {
            "results": [
                {"text": "architecture fact " + ("a" * 2600), "score": 0.9},
                {"text": "runbook fact " + ("b" * 2600), "score": 0.8},
                {"text": "extra fact " + ("c" * 2600), "score": 0.7},
            ]
        },
    }
    decisions = [
        {
            "action": "gather_evidence",
            "decision_summary": "Inspect processing-service CPU pressure. " + ("d" * 1400),
            "current_hypothesis": "A process workload is consuming abnormal CPU.",
            "evidence_request": {
                "kind": "runtime_resource_state",
                "target_component": "processing-service",
                "related_component": None,
                "purpose": "Confirm current CPU pressure.",
                "time_scope": "current",
                "causal_relation": "test_hypothesis",
                "causal_link": "Runtime CPU pressure supports the process workload hypothesis.",
            },
        }
        for _ in range(10)
    ]
    evidence = [
        {
            "step": 1,
            "tool": "apm_mcp_get_metrics",
            "arguments": {
                "host_id": "processing-service",
                "metric": "docker_container_cpu",
            },
            "observation": {
                "measurement_name": "docker_container_cpu",
                "field": "docker_container_cpu.usage_percent",
                "host_id": "processing-service",
                "metrics": [
                    {"timestamp": index, "value": 350.0 + index}
                    for index in range(40)
                ],
                "verbose_metadata": "m" * 4500,
            },
            "success": True,
        },
        *[
            {
                "step": index,
                "tool": "apm_mcp_get_processes",
                "arguments": {"host_id": "processing-service"},
                "observation": {
                    "processes": [
                        {
                            "pid": 12000 + item,
                            "name": "python3",
                            "cpu_percent": 80 + item,
                            "details": "z" * 600,
                        }
                        for item in range(30)
                    ]
                },
                "success": True,
            }
            for index in range(2, 10)
        ],
    ]
    prompt = (
        "Finalization policy. Use only supplied evidence. " + ("p" * 1400)
        + _EvaluationDiagnosticFinalizer._ASSIGNMENT_MARKER
        + json.dumps(assignment)
        + _EvaluationDiagnosticFinalizer._DECISIONS_MARKER
        + json.dumps(decisions)
        + _EvaluationDiagnosticFinalizer._EVIDENCE_MARKER
        + json.dumps(evidence)
    )
    return [
        {"role": "system", "content": "You are the diagnostic finalizer."},
        {"role": "user", "content": prompt},
    ]


def _valid_cpu_output() -> dict:
    return {
        "summary": "High CPU pressure was attributed to processing-service worker processes.",
        "diagnosis_status": "probable",
        "root_cause": "Abnormal CPU-consuming workload in processing-service python processes.",
        "causal_chain": [
            "CPU utilisation increased on processing-service.",
            "Runtime process evidence identified CPU-consuming python processes.",
        ],
        "confidence": 0.8,
        "findings": ["CPU pressure and process attribution were both observed."],
        "hypotheses": [],
        "recommended_next_steps": ["Inspect the workload executed by the identified processes."],
        "assistance_domain": None,
    }


def test_finalizer_compacts_large_cpu_prompt_below_context_guard() -> None:
    finalizer = _finalizer()
    prepared, budget = finalizer._prepare_messages(_large_cpu_finalization_messages())

    assert budget["compacted"] is True
    assert budget["input_budget_tokens"] == 4096
    assert budget["input_chars"] <= budget["input_char_guard"]
    assert budget["input_char_guard"] == 6144

    normalized = finalizer._normalized_messages(prepared)
    total_chars = sum(len(message["content"]) for message in normalized)
    assert total_chars <= finalizer._max_input_chars()
    assert any(
        "response schema supplied through the structured output format" in message["content"]
        for message in normalized
    )

    user_content = next(
        message["content"] for message in prepared if message["role"] == "user"
    )
    assert "CPU-processing-service" in user_content
    assert "processing-service" in user_content
    assert "docker_container_cpu" in user_content
    assert "apm_mcp_get_metrics" in user_content


def test_small_prompt_preserves_historical_schema_instruction() -> None:
    normalized = _finalizer()._normalized_messages(
        [
            {"role": "system", "content": "Finalize only from evidence."},
            {"role": "user", "content": "Small evidence payload."},
        ]
    )

    assert any("JSON Schema:" in message["content"] for message in normalized)


def test_finalizer_uses_fixed_context_and_structured_format(monkeypatch) -> None:
    monkeypatch.setattr(evaluation_module.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setenv("AGENT_REASONING_TEMPERATURE", "0.5")
    _FakeAsyncClient.payloads = []
    _FakeAsyncClient.responses = [
        {
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 3300,
            "eval_count": 220,
            "message": {
                "role": "assistant",
                "content": json.dumps(_valid_cpu_output()),
            },
        }
    ]

    output = asyncio.run(_finalizer().ainvoke(_large_cpu_finalization_messages()))

    assert output.diagnosis_status == "probable"
    assert len(_FakeAsyncClient.payloads) == 1
    payload = _FakeAsyncClient.payloads[0]
    assert payload["options"]["num_ctx"] == 8192
    assert payload["options"]["num_predict"] == 2048
    assert payload["options"]["temperature"] == 0.5
    assert payload["format"] == _finalizer().schema
    total_chars = sum(len(message["content"]) for message in payload["messages"])
    assert total_chars <= _finalizer()._max_input_chars()


def test_length_truncation_retries_once_with_minimal_context(monkeypatch) -> None:
    monkeypatch.setattr(evaluation_module.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setenv("AGENT_REASONING_TEMPERATURE", "0")
    _FakeAsyncClient.payloads = []
    _FakeAsyncClient.responses = [
        {
            "done": True,
            "done_reason": "length",
            "prompt_eval_count": 5800,
            "eval_count": 95,
            "message": {
                "role": "assistant",
                "content": '{"summary":"truncated before closing"',
            },
        },
        {
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 2400,
            "eval_count": 180,
            "message": {
                "role": "assistant",
                "content": json.dumps(_valid_cpu_output()),
            },
        },
    ]

    output = asyncio.run(_finalizer().ainvoke(_large_cpu_finalization_messages()))

    assert output.root_cause.startswith("Abnormal CPU-consuming workload")
    assert len(_FakeAsyncClient.payloads) == 2
    first_chars = sum(
        len(message["content"]) for message in _FakeAsyncClient.payloads[0]["messages"]
    )
    retry_chars = sum(
        len(message["content"]) for message in _FakeAsyncClient.payloads[1]["messages"]
    )
    assert retry_chars <= first_chars
    assert retry_chars <= _finalizer()._max_input_chars()
