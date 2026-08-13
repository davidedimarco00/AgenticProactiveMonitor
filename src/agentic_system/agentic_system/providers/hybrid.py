from __future__ import annotations

import logging
from typing import Any, Optional

from spade_llm.context import ContextManager
from spade_llm.providers import LLMProvider
from spade_llm.providers.base_provider import BaseLLMProvider
from spade_llm.tools import LLMTool


LOGGER = logging.getLogger("agentic_system.providers")


class _PromptContextView:
    """Read-only prompt view used for the tool-selection model.

    SPADE-LLM providers only need get_prompt() and get_tracing_metadata() for
    completion requests. Keeping this view separate avoids mutating the real
    conversation context with internal routing instructions.
    """

    def __init__(self, prompt: list[dict[str, Any]], metadata: dict[str, Any]) -> None:
        self._prompt = prompt
        self._metadata = metadata

    def get_prompt(self, conversation_id: Optional[str] = None) -> list[dict[str, Any]]:
        return list(self._prompt)

    def get_tracing_metadata(self, conversation_id: Optional[str] = None) -> dict[str, Any]:
        return dict(self._metadata)


class HybridLLMProvider(BaseLLMProvider):
    """SPADE-LLM provider that assigns one Ollama model to each AI role.

    - reasoning_provider: produces the reasoning/diagnostic response (Gemma)
    - tool_provider: decides tool/function calls from the SPADE-LLM tool list (Qwen)
    - embedding_provider: generates embeddings when SPADE-LLM requests them (Granite)

    Tool discovery, execution, context, memory and MCP remain owned by SPADE-LLM.
    """

    TOOL_SELECTOR_SYSTEM_PROMPT = (
        "You are the tool-calling model of an agent. Use only the tools supplied "
        "by the framework. Based on the conversation and the reasoning draft, "
        "decide whether external evidence or an action is required. If a tool is "
        "needed, select the most appropriate tool and provide valid arguments. "
        "If no tool is needed, return no tool call. Do not replace the reasoning "
        "model's final answer with your own prose."
    )

    def __init__(
        self,
        *,
        reasoning_provider: LLMProvider,
        tool_provider: LLMProvider,
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
            tool_provider=LLMProvider(
                model=f"ollama_chat/{config.tool_model}",
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
                    "Decide now whether a tool call is required. Use the supplied "
                    "tool specifications for any call; otherwise return no tool call."
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
