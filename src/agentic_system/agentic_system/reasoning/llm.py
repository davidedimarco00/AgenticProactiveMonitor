"""Compatibility layer for the simplified role-based Ollama providers."""
from __future__ import annotations
from typing import Any
from .models import RoleLLMProvider, SharedInferenceGate


class HybridLLMProvider(RoleLLMProvider):
    """Deprecated compatibility name; runtime now assigns models by agent role."""

    @classmethod
    def from_runtime(cls, config: Any) -> "HybridLLMProvider":
        return cls(
            model=config.reasoning_model,
            base_url=config.ollama_url,
            embedding_model=config.embedding_model,
            gate=SharedInferenceGate(int(getattr(config, "max_llm_concurrency", 1))),
        )


OllamaToolCallingProvider = RoleLLMProvider

__all__ = ["HybridLLMProvider", "OllamaToolCallingProvider"]
