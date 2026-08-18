"""AI provider integration used by SPADE-LLM agents."""

from .providers import HybridLLMProvider, OllamaToolCallingProvider

__all__ = ["HybridLLMProvider", "OllamaToolCallingProvider"]
