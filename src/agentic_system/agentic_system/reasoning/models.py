from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from typing import Any, Optional

from spade_llm.context import ContextManager
from spade_llm.providers import LLMProvider
from spade_llm.providers.base_provider import BaseLLMProvider
from spade_llm.tools import LLMTool


class SharedInferenceGate:
    """Backend-wide Ollama concurrency gate shared by all agent model roles."""

    def __init__(self, max_concurrency: int = 1) -> None:
        if max_concurrency <= 0:
            raise ValueError("max_concurrency must be greater than zero")
        self.max_concurrency = max_concurrency
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._active = 0
        self._waiting = 0
        self._peak_active = 0

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        self._waiting += 1
        try:
            await self._semaphore.acquire()
        finally:
            self._waiting -= 1
        self._active += 1
        self._peak_active = max(self._peak_active, self._active)
        try:
            yield
        finally:
            self._active -= 1
            self._semaphore.release()

    def snapshot(self) -> dict[str, int | str]:
        return {
            "scope": "BACKEND_GLOBAL",
            "max_concurrency": self.max_concurrency,
            "active": self._active,
            "waiting": self._waiting,
            "peak_active": self._peak_active,
        }


class RoleLLMProvider(BaseLLMProvider):
    """Small SPADE-LLM provider that binds one model to one cognitive role.

    Technical Lead agents receive the reasoning model; specialist agents receive
    the tool-capable model. Both share the same embedding model and inference gate.
    LangChain reuses ``inference_slot`` when it executes a specialist ReAct run.
    """

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        embedding_model: str,
        gate: SharedInferenceGate,
    ) -> None:
        super().__init__()
        self._chat = LLMProvider(model=f"ollama/{model}", base_url=base_url)
        self._embedding = LLMProvider(
            model=f"ollama/{embedding_model}",
            base_url=base_url,
        )
        self.model = self._chat.model
        self.base_url = base_url.rstrip("/")
        self.embedding_model = self._embedding.model
        self._gate = gate

    def inference_slot(self) -> AsyncIterator[None]:
        return self._gate.slot()

    def concurrency_snapshot(self) -> dict[str, int | str]:
        return self._gate.snapshot()

    async def get_llm_response(
        self,
        context: ContextManager,
        tools: Optional[list[LLMTool]] = None,
        conversation_id: Optional[str] = None,
        output_schema: Optional[Any] = None,
    ) -> dict[str, Any]:
        async with self._gate.slot():
            return await self._chat.get_llm_response(
                context,
                tools=tools,
                conversation_id=conversation_id,
                output_schema=output_schema,
            )

    async def get_embeddings(self, texts: list[str]) -> list[list[float]]:
        async with self._gate.slot():
            return await self._embedding.get_embeddings(texts)
