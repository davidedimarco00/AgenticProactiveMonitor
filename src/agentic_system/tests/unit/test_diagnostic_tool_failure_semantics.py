import asyncio

from spade_llm.context import ContextManager

from agentic_system.reasoning import SpecialistReActExecutor


class _FakeProvider:
    model = "ollama/fake-model"
    base_url = "http://127.0.0.1:11434"


class _ErrorStatusTool:
    name = "get_network_connections"
    description = "Return monitored network connections."
    parameters = {
        "type": "object",
        "properties": {
            "host_id": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        "required": ["host_id"],
    }

    async def execute(self, **kwargs):
        return {
            "status": "error",
            "host_id": kwargs.get("host_id"),
            "error": "limit must be between 1 and 100",
        }


def test_tool_reported_error_is_failed_evidence_acquisition() -> None:
    executor = SpecialistReActExecutor(
        provider=_FakeProvider(),  # type: ignore[arg-type]
        tool_provider=_FakeProvider(),  # type: ignore[arg-type]
        context=ContextManager(system_prompt="test"),
        tools=[_ErrorStatusTool()],  # type: ignore[list-item]
        max_steps=1,
        tool_timeout_seconds=1.0,
        tool_selector=object(),
        finalizer=object(),
    )

    evidence = asyncio.run(
        executor._execute_tool(
            step=1,
            tool_name="get_network_connections",
            arguments={"host_id": "processing-service", "limit": 100},
        )
    )

    assert evidence.success is False
    assert evidence.observation["status"] == "error"
    assert "limit must be between" in evidence.observation["error"]
