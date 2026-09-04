"""Compatibility imports for the consolidated specialist ReAct runtime.

Ollama context configuration and structured-output contracts are centralized in
``react_contracts.py``; the canonical executor is ``specialist_react.py``.
"""

import httpx

from .react_contracts import (
    _ContextAwarePromptGemmaDiagnosticFinalizer,
    _configured_ollama_context,
)
from .specialist_react import SpecialistReActExecutor

__all__ = [
    "SpecialistReActExecutor",
    "_ContextAwarePromptGemmaDiagnosticFinalizer",
    "_configured_ollama_context",
]
