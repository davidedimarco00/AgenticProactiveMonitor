from __future__ import annotations

import asyncio
import json

import agentic_system.reasoning.evaluation_react as evaluation_module
from agentic_system.reasoning.evaluation_context_guard import _EvaluationDiagnosticFinalizer


class _FakeResponse:
    def __init__(self, body: dict) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._body


class _FakeAsyncClient:
    last_payload: dict | None = None
    response_body: dict = {}

    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, json: dict):
        type(self).last_payload = json
        return _FakeResponse(type(self).response_body)


def _finalizer() -> _EvaluationDiagnosticFinalizer:
    return _EvaluationDiagnosticFinalizer(
        model="gemma4:e4b",
        base_url="http://ollama:11434",
        timeout_seconds=60.0,
        context_size=8192,
    )


def _large_finalization_messages() -> list[dict[str, str]]:
    assignment = {
        "task_id": "TASK-001",
        "incident_id": "INC-001",
        "agent_role": "network_engineer",
        "severity": "HIGH",
        "entity": "NETLAT-api-gateway-processing-service",
        "anomaly": {
            "detector_name": "NETLAT-api-gateway-processing-service",
            "detector_type": "SINGLE_ENTITY",
            "measurement_name": "network_transport_latency",
            "feature_field": "network_transport_latency.response_time",
            "data_start_time": 100,
            "data_end_time": 200,
            "noise": "x" * 5000,
        },
        "incident_anchor": {
            "detector_name": "NETLAT-api-gateway-processing-service",
            "detector_type": "SINGLE_ENTITY",
            "observed_signal": "network_transport_latency",
            "measurement_name": "network_transport_latency",
            "feature_field": "network_transport_latency.response_time",
            "affected_component": "api-gateway",
            "related_component": "processing-service",
            "primary_diagnostic_question": "What transport mechanism caused the latency?",
            "evidence_priority": ["detector-aligned history", "TCP behaviour"],
        },
        "static_project_grounding": {
            "results": [
                {"text": "architecture fact " + ("a" * 2500), "score": 0.9},
                {"text": "runbook fact " + ("b" * 2500), "score": 0.8},
                {"text": "extra fact " + ("c" * 2500), "score": 0.7},
            ]
        },
    }
    decisions = [
        {
            "action": "gather_evidence",
            "decision_summary": "Inspect detector-aligned transport latency. " + ("d" * 1200),
            "current_hypothesis": "Transport delay affects the service path.",
            "evidence_request": {
                "kind": "metric_history",
                "target_component": "api-gateway",
                "related_component": "processing-service",
                "purpose": "Measure transport latency around the anomaly.",
                "time_scope": "anomaly_window",
                "causal_relation": "measure_signal",
                "causal_link": "The metric directly measures the reported transport latency.",
            },
        }
        for _ in range(10)
    ]
    evidence = [
        {
            "step": 1,
            "tool": "apm_mcp_get_metrics",
            "arguments": {
                "host_id": "api-gateway",
                "target_host": "processing-service",
                "metric": "network_transport_latency",
            },
            "observation": {
                "measurement_name": "network_transport_latency",
                "field": "network_transport_latency.response_time",
                "detector_aligned": True,
                "host_id": "api-gateway",
                "target_host": "processing-service",
                "metrics": [
                    {"timestamp": index, "value": 0.4 + index / 1000}
                    for index in range(40)
                ],
                "verbose_metadata": "m" * 4000,
            },
            "success": True,
        },
        *[
            {
                "step": index,
                "tool": "apm_mcp_get_network_connections",
                "arguments": {"host_id": "api-gateway"},
                "observation": {
                    "connections": [
                        {"remote": f"10.0.0.{item}", "state": "ESTABLISHED", "details": "z" * 500}
                        for item in range(30)
                    ]
                },
                "success": True,
            }
            for index in range(2, 10)
        ],
    ]
    prompt = (
        "Finalization policy. Use only supplied evidence. " + ("p" * 1200)
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


def test_finalizer_compacts_large_prompt_below_context_guard() -> None:
    finalizer = _finalizer()
    prepared, budget = finalizer._prepare_messages(_large_finalization_messages())

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

    user_content = next(message["content"] for message in prepared if message["role"] == "user")
    assert "NETLAT-api-gateway-processing-service" in user_content
    assert "network_transport_latency" in user_content
    assert "apm_mcp_get_metrics" in user_content
    assert "detector_aligned" in user_content
    assert "api-gateway" in user_content
    assert "processing-service" in user_content


def test_small_prompt_preserves_historical_schema_instruction() -> None:
    finalizer = _finalizer()
    normalized = finalizer._normalized_messages(
        [
            {"role": "system", "content": "Finalize only from evidence."},
            {"role": "user", "content": "Small evidence payload."},
        ]
    )

    assert any("JSON Schema:" in message["content"] for message in normalized)


def test_finalizer_uses_fixed_context_and_logs_ollama_counts(monkeypatch) -> None:
    monkeypatch.setattr(evaluation_module.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setenv("AGENT_REASONING_TEMPERATURE", "0.5")
    _FakeAsyncClient.last_payload = None
    _FakeAsyncClient.response_body = {
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 3500,
        "eval_count": 280,
        "message": {
            "role": "assistant",
            "content": json.dumps(
                {
                    "summary": "Transport latency was observed on the affected path.",
                    "diagnosis_status": "probable",
                    "root_cause": "Transport-level path degradation between the two services.",
                    "causal_chain": [
                        "Detector-aligned latency increased.",
                        "The affected source and destination path matches the incident.",
                    ],
                    "confidence": 0.7,
                    "findings": ["Detector-aligned transport latency was collected."],
                    "hypotheses": [],
                    "recommended_next_steps": ["Repeat the path measurement for confirmation."],
                    "assistance_domain": None,
                }
            ),
        },
    }

    output = asyncio.run(_finalizer().ainvoke(_large_finalization_messages()))

    assert output.diagnosis_status == "probable"
    payload = _FakeAsyncClient.last_payload
    assert payload is not None
    assert payload["options"]["num_ctx"] == 8192
    assert payload["options"]["num_predict"] == 2048
    assert payload["options"]["temperature"] == 0.5
    assert payload["format"] == _finalizer().schema
    total_chars = sum(len(message["content"]) for message in payload["messages"])
    assert total_chars <= _finalizer()._max_input_chars()
