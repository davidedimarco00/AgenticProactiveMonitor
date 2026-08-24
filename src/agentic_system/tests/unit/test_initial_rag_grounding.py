from agentic_system.reasoning.prompt_engineered_collaboration import SpecialistReActExecutor


def _executor_without_runtime() -> SpecialistReActExecutor:
    executor = object.__new__(SpecialistReActExecutor)
    executor._initial_rag_context_by_task = {}
    return executor


def test_initial_rag_query_is_incident_specific_and_bounded() -> None:
    query = SpecialistReActExecutor._build_initial_rag_query(
        agent_role="network_engineer",
        severity="high",
        entity="api-gateway",
        anomaly={
            "detector_name": "NETLAT-api-gateway-processing-service",
            "anomaly_grade": 1.0,
            "confidence": 0.97,
        },
    )

    assert "network_engineer" in query
    assert "api-gateway" in query
    assert "NETLAT-api-gateway-processing-service" in query
    assert "architecture" in query
    assert "service dependencies" in query
    assert "telemetry semantics" in query
    assert "do not infer current runtime state" in query
    assert len(query) <= 2000


def test_knowledge_tool_name_accepts_namespaced_mcp_tool() -> None:
    executor = _executor_without_runtime()
    executor._tool_names = {
        "apm_mcp_get_metrics",
        "apm_mcp_search_knowledge",
    }

    assert executor._knowledge_tool_name() == "apm_mcp_search_knowledge"


def test_assignment_is_enriched_only_for_matching_incident_and_task() -> None:
    executor = _executor_without_runtime()
    executor._initial_rag_context_by_task[("inc-1", "task-1")] = {
        "source": "Qdrant RAG",
        "runtime_evidence": False,
        "retrieval": {"returned_results": 4},
    }

    grounded = executor._assignment_with_grounding(
        {
            "incident_id": "inc-1",
            "task_id": "task-1",
            "entity": "processing-service",
        }
    )
    wrong_incident = executor._assignment_with_grounding(
        {
            "incident_id": "inc-2",
            "task_id": "task-1",
            "entity": "processing-service",
        }
    )
    wrong_task = executor._assignment_with_grounding(
        {
            "incident_id": "inc-1",
            "task_id": "task-2",
            "entity": "processing-service",
        }
    )

    assert grounded["static_project_grounding"]["source"] == "Qdrant RAG"
    assert grounded["static_project_grounding"]["runtime_evidence"] is False
    assert "static_project_grounding" not in wrong_incident
    assert "static_project_grounding" not in wrong_task


def test_prompt_contract_uses_initial_rag_without_treating_it_as_live_evidence() -> None:
    reasoning = " ".join(SpecialistReActExecutor.REASONING_POLICY.split())
    selection = " ".join(SpecialistReActExecutor.TOOL_SELECTION_POLICY.split())
    finalization = " ".join(SpecialistReActExecutor.FINALIZATION_POLICY.split())

    assert "static_project_grounding" in reasoning
    assert "Read and USE it" in reasoning
    assert "NOT live evidence" in reasoning
    assert "search_knowledge" in reasoning

    assert "static_project_grounding" in selection
    assert "Do not spend a ReAct step" in selection

    assert "static_project_grounding" in finalization
    assert "Do NOT treat static grounding as proof" in finalization
