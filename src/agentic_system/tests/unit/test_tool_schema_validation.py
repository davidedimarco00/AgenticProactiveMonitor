import pytest

from agentic_system.reasoning import SpecialistReActExecutor


class ToolWithJsonSchema:
    args_schema = {
        "type": "object",
        "properties": {
            "host_id": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        "required": ["host_id"],
        "additionalProperties": False,
    }


def test_json_schema_validation_rejects_out_of_range_tool_argument() -> None:
    with pytest.raises(ValueError, match="maximum of 100"):
        SpecialistReActExecutor._validate_tool_args(
            ToolWithJsonSchema(),
            {"host_id": "processing-service", "limit": 500},
        )


def test_json_schema_validation_accepts_valid_tool_arguments() -> None:
    args = SpecialistReActExecutor._validate_tool_args(
        ToolWithJsonSchema(),
        {"host_id": "processing-service", "limit": 100},
    )

    assert args == {"host_id": "processing-service", "limit": 100}
