from types import SimpleNamespace

from agentic_system.reasoning import SpecialistReActExecutor


def test_cpu_detector_anchor_prioritizes_resource_attribution() -> None:
    anchor = SpecialistReActExecutor._build_incident_anchor(
        agent_role="system_engineer",
        entity="CPU-processing-service",
        anomaly={"detector_name": "CPU-processing-service"},
    )

    assert anchor["observed_signal"] == "container_cpu"
    assert anchor["signal_classification_source"] == "detector_naming_contract"
    assert anchor["affected_component"] == "processing-service"
    assert "CPU anomaly" in anchor["primary_diagnostic_question"]
    priorities = " ".join(anchor["evidence_priority"]).lower()
    assert "container cpu" in priorities
    assert "process" in priorities
    assert "network" not in priorities


def test_exact_detector_metadata_takes_precedence_over_detector_name() -> None:
    anchor = SpecialistReActExecutor._build_incident_anchor(
        agent_role="application_engineer",
        entity="legacy-name",
        anomaly={
            "detector_name": "CPU-misleading-fallback-name",
            "measurement_name": "application_service_latency",
            "feature_field": "application_service_latency.response_time",
        },
    )

    assert anchor["observed_signal"] == "application_service_latency"
    assert anchor["signal_classification_source"] == "detector_metadata"
    assert "HTTP response latency" in anchor["primary_diagnostic_question"]


def test_mcp_health_ping_is_removed_but_real_icmp_tool_remains() -> None:
    tools = [
        SimpleNamespace(name="apm_mcp_ping"),
        SimpleNamespace(name="apm_mcp_test_icmp_reachability"),
        SimpleNamespace(name="apm_mcp_get_runtime_stats"),
    ]

    filtered = SpecialistReActExecutor._filter_specialist_tools(tools)
    names = [tool.name for tool in filtered]

    assert "apm_mcp_ping" not in names
    assert "apm_mcp_test_icmp_reachability" in names
    assert "apm_mcp_get_runtime_stats" in names


def test_focus_policies_forbid_silent_cross_domain_symptom_pivot() -> None:
    reasoning = " ".join(SpecialistReActExecutor.REASONING_POLICY.split())
    selection = " ".join(SpecialistReActExecutor.TOOL_SELECTION_POLICY.split())
    finalization = " ".join(SpecialistReActExecutor.FINALIZATION_POLICY.split())

    assert "reported anomaly signal as INVARIANT" in reasoning
    assert "never replace it with an unrelated symptom" in reasoning
    assert "Off-domain evidence is secondary" in reasoning
    assert "incident_anchor.primary_diagnostic_question" in selection
    assert "generic MCP server health-check named ping" in selection
    assert "must explain incident_anchor.observed_signal" in finalization
