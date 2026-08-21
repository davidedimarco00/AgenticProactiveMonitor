from agentic_system.reasoning.llm import OllamaToolCallingProvider


def test_ollama_message_translation_converts_multiturn_tool_history() -> None:
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "inspect processing-service"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_metrics_1",
                    "type": "function",
                    "function": {
                        "name": "apm_mcp_get_metrics",
                        "arguments": '{"service":"processing-service"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "content": "{'cpu_percent': 391.0}",
            "tool_call_id": "call_metrics_1",
        },
    ]

    translated = OllamaToolCallingProvider._format_messages(messages)

    assistant = translated[2]
    assert assistant == {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "type": "function",
                "function": {
                    "index": 0,
                    "name": "apm_mcp_get_metrics",
                    "arguments": {"service": "processing-service"},
                },
            }
        ],
    }
    assert translated[3] == {
        "role": "tool",
        "tool_name": "apm_mcp_get_metrics",
        "content": "{'cpu_percent': 391.0}",
    }
    assert "tool_call_id" not in translated[3]


def test_ollama_message_translation_preserves_plain_messages() -> None:
    translated = OllamaToolCallingProvider._format_messages(
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "question", "name": "ignored-name"},
            {"role": "assistant", "content": "answer"},
        ]
    )

    assert translated == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "answer"},
    ]
