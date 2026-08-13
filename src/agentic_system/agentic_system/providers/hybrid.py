from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Optional

import httpx
from spade_llm.context import ContextManager
from spade_llm.providers import LLMProvider
from spade_llm.providers.base_provider import BaseLLMProvider
from spade_llm.tools import LLMTool


LOGGER = logging.getLogger("agentic_system.providers")


class _PromptContextView:
    """Read-only prompt view used for the tool-selection model."""

    def __init__(self, prompt: list[dict[str, Any]], metadata: dict[str, Any]) -> None:
        self._prompt = prompt
        self._metadata = metadata

    def get_prompt(self, conversation_id: Optional[str] = None) -> list[dict[str, Any]]:
        return list(self._prompt)

    def get_tracing_metadata(self, conversation_id: Optional[str] = None) -> dict[str, Any]:
        return dict(self._metadata)


class OllamaToolCallingProvider(BaseLLMProvider):
    """Qwen tool selector backed by Ollama's native ``/api/chat`` endpoint.

    Only model I/O is implemented here. SPADE-LLM still owns MCP discovery,
    the tool registry, tool execution, context, memory and the iterative tool loop.
    """

    def __init__(self, *, model: str, base_url: str, timeout: float = 120.0) -> None:
        super().__init__()
        self.ollama_model = model
        self.model = f"ollama_native/{model}"
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.endpoint = f"{self.base_url}/api/chat"

    async def _post_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(self.endpoint, json=payload)
            response.raise_for_status()
            data = response.json()

        if not isinstance(data, dict):
            raise RuntimeError("Ollama tool-calling response is not a JSON object")
        return data

    async def get_llm_response(
        self,
        context: ContextManager,
        tools: Optional[list[LLMTool]] = None,
        conversation_id: Optional[str] = None,
        output_schema: Optional[Any] = None,
    ) -> dict[str, Any]:
        if output_schema is not None:
            raise ValueError("OllamaToolCallingProvider does not produce structured output")

        payload: dict[str, Any] = {
            "model": self.ollama_model,
            "messages": context.get_prompt(conversation_id),
            "stream": False,
            "think": False,
            "options": {"temperature": 0},
        }
        if tools:
            payload["tools"] = [tool.to_openai_tool() for tool in tools]

        data = await self._post_chat(payload)
        message = data.get("message")
        if not isinstance(message, dict):
            raise RuntimeError("Unexpected Ollama /api/chat response shape")

        raw_calls = message.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            raise RuntimeError("Ollama tool_calls field is not a list")

        parsed_calls: list[dict[str, Any]] = []
        for raw_call in raw_calls:
            if not isinstance(raw_call, dict):
                raise RuntimeError("Ollama returned an invalid tool call")

            function = raw_call.get("function") or {}
            if not isinstance(function, dict):
                raise RuntimeError("Ollama returned an invalid tool function")

            name = str(function.get("name") or "").strip()
            if not name:
                raise RuntimeError("Ollama returned a tool call without a function name")

            arguments = function.get("arguments", {})
            if isinstance(arguments, str):
                if arguments.strip():
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError as exc:
                        raise RuntimeError(
                            f"Ollama returned invalid JSON arguments for tool {name}"
                        ) from exc
                else:
                    arguments = {}

            if not isinstance(arguments, dict):
                raise RuntimeError(f"Ollama returned non-object arguments for tool {name}")

            parsed_calls.append(
                {
                    "id": str(raw_call.get("id") or f"call_{uuid.uuid4().hex[:12]}"),
                    "name": name,
                    "arguments": arguments,
                }
            )

        content = message.get("content") or ""
        if not isinstance(content, str):
            content = str(content)

        if tools and not parsed_calls:
            LOGGER.info(
                "Tool model %s returned no native tool call; content=%r",
                self.model,
                content[:300],
            )

        return {
            "text": None if parsed_calls else content,
            "tool_calls": parsed_calls,
            "structured": None,
        }


class HybridLLMProvider(BaseLLMProvider):
    """Assign Gemma to reasoning, Qwen to tools and Granite to embeddings."""

    TOOL_SELECTOR_SYSTEM_PROMPT = (
        "You are the tool-calling model of an agent. Use only the tools supplied "
        "by the framework. Based on the conversation and the reasoning draft, "
        "decide whether external evidence or an action is required. If a tool is "
        "needed, select the most appropriate tool and provide valid arguments. "
        "When the request explicitly requires a live check, you must call a suitable "
        "tool instead of answering from memory. If no tool is needed, return no tool "
        "call. Do not replace the reasoning model's final answer with your own prose."
    )

    def __init__(
        self,
        *,
        reasoning_provider: BaseLLMProvider,
        tool_provider: BaseLLMProvider,
        embedding_provider: LLMProvider,
    ) -> None:
        super().__init__()
        self.reasoning_provider = reasoning_provider
        self.tool_provider = tool_provider
        self.embedding_provider = embedding_provider
        self.model = reasoning_provider.model
        self.reasoning_model = reasoning_provider.model
        self.tool_model = tool_provider.model
        self.embedding_model = embedding_provider.model
        self.base_url = reasoning_provider.base_url

    @classmethod
    def from_runtime(cls, config: Any) -> "HybridLLMProvider":
        return cls(
            reasoning_provider=LLMProvider(
                model=f"ollama/{config.reasoning_model}",
                base_url=config.ollama_url,
            ),
            tool_provider=OllamaToolCallingProvider(
                model=config.tool_model,
                base_url=config.ollama_url,
            ),
            embedding_provider=LLMProvider(
                model=f"ollama/{config.embedding_model}",
                base_url=config.ollama_url,
            ),
        )

    async def get_llm_response(
        self,
        context: ContextManager,
        tools: Optional[list[LLMTool]] = None,
        conversation_id: Optional[str] = None,
        output_schema: Optional[Any] = None,
    ) -> dict[str, Any]:
        if not tools:
            return await self.reasoning_provider.get_llm_response(
                context,
                tools=None,
                conversation_id=conversation_id,
                output_schema=output_schema,
            )

        reasoning_response = await self.reasoning_provider.get_llm_response(
            context,
            tools=None,
            conversation_id=conversation_id,
            output_schema=None,
        )
        reasoning_text = str(reasoning_response.get("text") or "").strip()

        selector_prompt = [
            {"role": "system", "content": self.TOOL_SELECTOR_SYSTEM_PROMPT},
            *context.get_prompt(conversation_id),
        ]
        if reasoning_text:
            selector_prompt.append(
                {
                    "role": "assistant",
                    "content": f"Reasoning draft from the reasoning model:\n{reasoning_text}",
                }
            )
        selector_prompt.append(
            {
                "role": "user",
                "content": (
                    "Choose the next action now. If external evidence is required, "
                    "return a native tool call using one of the supplied tools."
                ),
            }
        )

        selector_context = _PromptContextView(
            selector_prompt,
            context.get_tracing_metadata(conversation_id),
        )
        tool_response = await self.tool_provider.get_llm_response(
            selector_context,
            tools=tools,
            conversation_id=conversation_id,
            output_schema=None,
        )
        tool_calls = tool_response.get("tool_calls") or []

        if tool_calls:
            LOGGER.info(
                "Tool model %s selected %d tool call(s) after reasoning with %s",
                self.tool_model,
                len(tool_calls),
                self.reasoning_model,
            )
            return {"text": None, "tool_calls": tool_calls, "structured": None}

        if output_schema is not None:
            return await self.reasoning_provider.get_llm_response(
                context,
                tools=None,
                conversation_id=conversation_id,
                output_schema=output_schema,
            )

        LOGGER.info(
            "Tool model %s selected no tool; returning reasoning from %s",
            self.tool_model,
            self.reasoning_model,
        )
        return reasoning_response

    async def get_embeddings(self, texts: list[str]) -> list[list[float]]:
        return await self.embedding_provider.get_embeddings(texts)
