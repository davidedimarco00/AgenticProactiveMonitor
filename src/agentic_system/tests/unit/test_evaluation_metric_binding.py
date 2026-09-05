from __future__ import annotations

from types import SimpleNamespace

from agentic_system.reasoning.evaluation_react import SpecialistReActExecutor
from agentic_system.reasoning.react_contracts import _EvidenceRequest


def _network_assignment() -> dict:
    request = _EvidenceRequest(
        kind="metric_history",
        target_component="api-gateway",
        related_component="processing-service",
        purpose="Measure detector-aligned transport latency on the affected path.",
        time_scope="recent_history",
        causal_relation="measure_signal",
        causal_link="The path history directly measures the NETLAT-reported signal.",
    )
    return {
        "incident_anchor": {
            "observed_signal": "network_transport_latency",
            "affected_component": "api-gateway",
            "related_component": "processing-service",
        },
        "evidence_request": {
            **request.model_dump(),
            "incident_signal": "network_transport_latency",
            "anchor_component": "api-gateway",
        },
    }


def test_evaluation_adapter_derives_netlat_metric_name() -> None:
    assert (
        SpecialistReActExecutor._metric_name_from_assignment(_network_assignment())
        == "network_transport_latency"
    )


def test_single_metric_tool_binds_netlat_source_target_and_metric() -> None:
    executor = object.__new__(SpecialistReActExecutor)
    tool = SimpleNamespace(
        name="apm_mcp_get_metrics",
        args_schema={
            "type": "object",
            "properties": {
                "host_id": {"type": "string"},
                "metric": {
                    "type": "string",
                    "enum": ["cpu", "memory", "network_transport_latency"],
                },
                "target_host": {
                    "anyOf": [
                        {
                            "type": "string",
                            "enum": [
                                "api-gateway",
                                "processing-service",
                                "data-service",
                            ],
                        },
                        {"type": "null"},
                    ],
                    "default": None,
                },
                "minutes": {"type": "integer", "minimum": 1, "maximum": 120},
            },
            "required": ["host_id", "metric"],
            "additionalProperties": False,
        },
    )
    executor._active_evidence_request = None

    args = executor._deterministic_single_tool_arguments(
        tool=tool,
        assignment=_network_assignment(),
        evidence_needed="Measure detector-aligned transport latency.",
    )

    assert args == {
        "host_id": "api-gateway",
        "target_host": "processing-service",
        "metric": "network_transport_latency",
    }
