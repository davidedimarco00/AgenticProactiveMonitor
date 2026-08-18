"""Backward-compatible facade for AI providers."""

from ..ai.providers import HybridLLMProvider, OllamaToolCallingProvider

__all__ = ["HybridLLMProvider", "OllamaToolCallingProvider"]
