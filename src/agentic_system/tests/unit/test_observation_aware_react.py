import asyncio

from spade_llm.context import ContextManager

from agentic_system.reasoning import SpecialistReActExecutor


class FakeProvider:
    model = "ollama/test-model"
    base_url = "http://127.0.0.1:11434"


class FakeSelector:
    async def ainvoke(self, messages):  # pragma: no cover - not used here
        raise AssertionError("tool selection is not part of this unit test")


class FakeTool:
    def __init__(self, observation, *, name="apm_mcp_get_processes") -> None:
        self.name = name
        self.description = "Return bounded live diagnostic evidence."
        self.parameters = {
            "type": "object",
            "properties": {"host_id": {"type": "string"}},
            "required": ["host_id"],
            "additionalProperties": False,
        }
        self.observation = observation

    async def execute(self, **kwargs):
        return self.observation


def _executor(tool: FakeTool, *, traces=None) -> SpecialistReActExecutor:
    return SpecialistReActExecutor(
        provider=FakeProvider(),  # type: ignore[arg-type]
        tool_provider=FakeProvider(),  # type: ignore[arg-type]
        context=ContextManager(system_prompt="test"),
        tools=[tool],  # type: ignore[list-item]
        tool_selector=FakeSelector(),
        finalizer=object(),
        trace_sink=(traces.append if traces is not None else None),
    )


def test_complete_process_output_is_preserved_while_gemma_receives_structured_projection() -> None:
    processes = [
        {
            "pid": index + 100,
            "cpu_percent": 99.0 - index,
            "memory_percent": 1.0,
            "command": "python3 " + ("x" * 220),
        }
        for index in range(30)
    ]
    tool = FakeTool(
        {
            "status": "ok",
            "host_id": "processing-service",
            "returned_processes": len(processes),
            "processes": processes,
            "tail_marker": "RAW_OUTPUT_END",
        }
    )
    executor = _executor(tool)

    evidence = asyncio.run(
        executor._execute_tool(
            step=1,
            tool_name=tool.name,
            arguments={"host_id": "processing-service"},
        )
    )

    assert evidence.success is True
    assert len(evidence.observation["processes"]) == 30
    assert evidence.observation["tail_marker"] == "RAW_OUTPUT_END"

    process_view = evidence.reasoning_observation["processes"]
    assert process_view["returned_to_reasoning"] == 12
    assert process_view["total_items"] == 30
    assert process_view["omitted_items"] == 18
    assert len(process_view["items"]) == 12

    persisted = evidence.to_dict()
    assert len(persisted["observation"]["processes"]) == 30
    assert persisted["reasoning_observation"]["processes"]["omitted_items"] == 18

    projected = executor._project_evidence_for_reasoning([evidence])
    assert len(projected[0].observation["processes"]["items"]) == 12
    assert "tail_marker" in projected[0].observation


def test_mcp_status_error_is_not_marked_as_successful_observation() -> None:
    tool = FakeTool(
        {
            "status": "error",
            "host_id": "processing-service",
            "error": "process not found",
        },
        name="apm_mcp_inspect_process",
    )
    executor = _executor(tool)

    evidence = asyncio.run(
        executor._execute_tool(
            step=2,
            tool_name=tool.name,
            arguments={"host_id": "processing-service"},
        )
    )

    assert evidence.success is False
    assert evidence.observation["status"] == "error"
    assert evidence.reasoning_observation == {
        "status": "error",
        "error": "process not found",
        "tool": "apm_mcp_inspect_process",
    }


def test_observe_trace_exposes_raw_and_reasoning_views_separately() -> None:
    traces = []
    tool = FakeTool(
        {
            "status": "ok",
            "host_id": "processing-service",
            "processes": [
                {"pid": index, "cpu_percent": 80.0 - index, "command": "python3"}
                for index in range(20)
            ],
        }
    )
    executor = _executor(tool, traces=traces)
    evidence = asyncio.run(
        executor._execute_tool(
            step=1,
            tool_name=tool.name,
            arguments={"host_id": "processing-service"},
        )
    )

    asyncio.run(
        executor._emit_trace(
            action="observe",
            reason="MCP observation returned to reasoning.",
            incident_id="INC-OBS-001",
            task_id="TASK-OBS-001",
            tool=tool.name,
            outcome="ok",
            details={"observation": evidence.observation, "success": evidence.success},
        )
    )

    details = traces[0]["details"]
    assert len(details["raw_observation"]["processes"]) == 20
    assert details["reasoning_observation"]["processes"]["returned_to_reasoning"] == 12
    assert details["reasoning_observation"]["processes"]["omitted_items"] == 8
    assert details["success"] is True
    assert "retained for audit" in details["observation_contract"]
