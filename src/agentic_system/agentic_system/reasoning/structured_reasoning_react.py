"""Compatibility import for the consolidated specialist ReAct executor.

Structured reasoning, bounded saturation recovery and native Ollama handling now
live in ``specialist_react.py``. Keep this module temporarily so existing tests
and external imports do not break during the refactor.
"""

from .specialist_react import SpecialistReActExecutor

__all__ = ["SpecialistReActExecutor"]
