"""Compatibility imports for the consolidated specialist ReAct runtime.

Initial RAG grounding, collaboration output semantics and diagnostic finalization
are now implemented by ``specialist_react.py`` and ``react_contracts.py``.
"""

from .react_contracts import (
    _PromptDiagnosticFinalOutput,
    _PromptGemmaDiagnosticFinalizer,
)
from .specialist_react import SpecialistReActExecutor

__all__ = [
    "SpecialistReActExecutor",
    "_PromptDiagnosticFinalOutput",
    "_PromptGemmaDiagnosticFinalizer",
]
