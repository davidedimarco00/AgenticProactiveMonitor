from __future__ import annotations

import asyncio
from typing import Any

import pytest
from spade_llm.context import ContextManager

from agentic_system.reasoning import ReActInvestigationError, SpecialistReActExecutor
from agentic_system.reasoning.react_contracts import _EvidenceRequest


MONITORED_HOSTS = [
    "traffic-generator",
    "api-gateway",
    "processing-service",
    "data-service",
    "worker-service",
]


class RuntimeStatsTool:
    """Mirrors the MCP contract: the target argument is an enumeration."""

    name = "apm_mcp_get_runtime_stats"
    description = "Return runtime resource state for one monitored container."
    parameters = {
        "type": "object",
        "properties": {"host_id": {"type": "string", "enum": list(MONITORED_HOSTS)}},
        "required": ["host_id"],
    }

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        return {"host_id": kwargs.get("host_id"), "cpu_percent": 12.0}


class TcpConnectionTool:
    name = "apm_mcp_test_tcp_connection"
    description = "Test a TCP connection from one monitored service to another."
    parameters = {
        "type": "object",
        "properties": {
            "host_id": {"type": "string", "enum": list(MONITORED_HOSTS)},
            "target_host": {
                "type": "string",
                "enum": ["api-gateway", "processing-service", "data-service"],
            },
        },
        "required": ["host_id", "target_host"],
    }

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        return {"reachable": True}


class FakeProvider:
    model = "ollama/test"
    base_url = "http://127.0.0.1:11434"


class RecordingSelector:
    """Stands in for the bound Qwen selector and records every invocation."""

    def __init__(self) -> None:
        self.invocations: list[Any] = []

    async def ainvoke(self, messages: Any) -> Any:
        self.invocations.append(messages)
        raise AssertionError("the tool-selection model must not be called")


class RecordingSelectorBase:
    def __init__(self) -> None:
        self.bindings: list[tuple[str, ...]] = []

    def bind_tools(self, tools: list[Any]) -> object:
        names = tuple(str(tool.name) for tool in tools)
        self.bindings.append(names)
        return object()


def _executor(tools: list[Any], selector: Any | None = None) -> SpecialistReActExecutor:
    return SpecialistReActExecutor(
        provider=FakeProvider(),  # type: ignore[arg-type]
        tool_provider=FakeProvider(),  # type: ignore[arg-type]
        context=ContextManager(system_prompt="You are a test specialist."),
        tools=tools,  # type: ignore[arg-type]
        tool_timeout_seconds=1.0,
        tool_selector=selector or RecordingSelector(),
        finalizer=object(),
    )


def _anomaly(detector_name: str, measurement: str) -> dict[str, Any]:
    return {
        "detector_name": detector_name,
        "measurement_name": measurement,
        "feature_field": f"{measurement}.response_time",
    }


# ----------------------------------------------------------------------
# Path detector targeting
# ----------------------------------------------------------------------
def test_network_path_detector_anchor_resolves_both_endpoints() -> None:
    anchor = SpecialistReActExecutor._build_incident_anchor(
        agent_role="network_engineer",
        entity="NETLAT-api-gateway-processing-service",
        anomaly=_anomaly(
            "NETLAT-api-gateway-processing-service",
            "network_transport_latency",
        ),
        known_components=tuple(MONITORED_HOSTS),
    )

    assert anchor["observed_signal"] == "network_transport_latency"
    assert anchor["affected_component"] == "api-gateway"
    assert anchor["related_component"] == "processing-service"
    assert "from api-gateway to processing-service" in anchor["primary_diagnostic_question"]


def test_application_path_detector_anchor_resolves_both_endpoints() -> None:
    anchor = SpecialistReActExecutor._build_incident_anchor(
        agent_role="application_engineer",
        entity="APPLAT-traffic-generator-api-gateway",
        anomaly=_anomaly(
            "APPLAT-traffic-generator-api-gateway",
            "application_service_latency",
        ),
        known_components=tuple(MONITORED_HOSTS),
    )

    # Both endpoint names contain hyphens: the split is resolved against the
    # component vocabulary, never by counting separators.
    assert anchor["affected_component"] == "traffic-generator"
    assert anchor["related_component"] == "api-gateway"


def test_resource_detector_anchor_declares_no_related_component() -> None:
    anchor = SpecialistReActExecutor._build_incident_anchor(
        agent_role="system_engineer",
        entity="CPU-processing-service",
        anomaly={"detector_name": "CPU-processing-service"},
        known_components=tuple(MONITORED_HOSTS),
    )

    assert anchor["affected_component"] == "processing-service"
    assert anchor["related_component"] is None


def test_unknown_endpoint_leaves_the_detector_name_untouched() -> None:
    anchor = SpecialistReActExecutor._build_incident_anchor(
        agent_role="network_engineer",
        entity="NETLAT-api-gateway-retired-service",
        anomaly=_anomaly("NETLAT-api-gateway-retired-service", "network_transport_latency"),
        known_components=tuple(MONITORED_HOSTS),
    )

    assert anchor["affected_component"] == "NETLAT-api-gateway-retired-service"
    assert anchor["related_component"] is None


def test_component_vocabulary_is_discovered_from_tool_schemas() -> None:
    executor = _executor([RuntimeStatsTool(), TcpConnectionTool()])

    vocabulary = executor._component_vocabulary_from_tools()

    assert set(vocabulary) == set(MONITORED_HOSTS)
    # Longest first, so a component that prefixes another cannot shadow it.
    assert list(vocabulary) == sorted(vocabulary, key=lambda item: (-len(item), item))
    assert executor._component_vocabulary_from_tools() is vocabulary


def test_evidence_request_inherits_the_far_endpoint_of_the_path() -> None:
    executor = _executor([TcpConnectionTool()])
    assignment = {
        "incident_anchor": SpecialistReActExecutor._build_incident_anchor(
            agent_role="network_engineer",
            entity="NETLAT-api-gateway-processing-service",
            anomaly=_anomaly(
                "NETLAT-api-gateway-processing-service",
                "network_transport_latency",
            ),
            known_components=tuple(MONITORED_HOSTS),
        )
    }
    normalized = SpecialistReActExecutor._normalize_and_validate_evidence_request(
        _EvidenceRequest(
            kind="network_path",
            target_component="api-gateway",
            purpose="Measure the TCP behaviour of the anomalous service path.",
            time_scope="current",
            causal_relation="measure_signal",
            causal_link="TCP behaviour on this path can explain the reported transport latency.",
        ),
        assignment,
    )

    assert normalized.related_component == "processing-service"

    # The far endpoint reaches the tool argument without a model round trip.
    bound = executor._bind_tool_arguments(
        executor._langchain_tool_by_name["apm_mcp_test_tcp_connection"],
        {},
        {"evidence_request": normalized.model_dump()},
    )
    assert bound == {"host_id": "api-gateway", "target_host": "processing-service"}


# ----------------------------------------------------------------------
# Bounded tool selection
# ----------------------------------------------------------------------
def test_selector_is_bound_only_to_the_compatible_family() -> None:
    executor = _executor([RuntimeStatsTool(), TcpConnectionTool()])
    base = RecordingSelectorBase()
    executor._tool_selector_base = base

    restricted = executor._selector_for_tools(("apm_mcp_get_runtime_stats",))

    assert base.bindings == [("apm_mcp_get_runtime_stats",)]
    # The restricted selector is cached: rebinding on every step would rebuild
    # the tool payload for each inference.
    assert executor._selector_for_tools(("apm_mcp_get_runtime_stats",)) is restricted
    assert base.bindings == [("apm_mcp_get_runtime_stats",)]


def test_selector_falls_back_to_the_full_binding_without_candidates() -> None:
    executor = _executor([RuntimeStatsTool(), TcpConnectionTool()])
    base = RecordingSelectorBase()
    executor._tool_selector_base = base

    assert executor._selector_for_tools(()) is executor._tool_selector
    assert base.bindings == []


def test_unaddressable_target_fails_before_spending_an_inference() -> None:
    selector = RecordingSelector()
    executor = _executor([RuntimeStatsTool()], selector=selector)
    assignment = {
        "evidence_request": {
            "kind": "runtime_resource_state",
            "target_component": "NETLAT-api-gateway-processing-service",
            "purpose": "Inspect the runtime state of the anomalous component.",
        }
    }

    with pytest.raises(ReActInvestigationError, match="not an addressable monitored component"):
        asyncio.run(
            executor._select_tool(
                assignment=assignment,
                evidence_needed="Runtime resource state of the affected component.",
                evidence=[],
            )
        )

    assert selector.invocations == []


def test_addressable_target_is_still_delegated_to_the_selector() -> None:
    executor = _executor([RuntimeStatsTool()])
    assignment = {
        "evidence_request": {
            "kind": "runtime_resource_state",
            "target_component": "processing-service",
            "purpose": "Inspect the runtime state of the anomalous component.",
        }
    }

    name, args = asyncio.run(
        executor._select_tool(
            assignment=assignment,
            evidence_needed="Runtime resource state of the affected component.",
            evidence=[],
        )
    )

    # A single compatible tool with a derivable target never reaches the model.
    assert name == "apm_mcp_get_runtime_stats"
    assert args == {"host_id": "processing-service"}
