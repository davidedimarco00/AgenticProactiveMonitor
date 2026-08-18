"""Backward-compatible facade for the hybrid Gemma/Qwen provider."""

from ..ai.providers.hybrid import HybridLLMProvider, OllamaToolCallingProvider

__all__ = ["HybridLLMProvider", "OllamaToolCallingProvider"]
