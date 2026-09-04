"""Compatibility import for the consolidated specialist ReAct executor.

Prompt policy is centralized in ``react_policies.py`` and runtime behavior in
``specialist_react.py``. This module remains as a temporary import shim.
"""

from .specialist_react import SpecialistReActExecutor

__all__ = ["SpecialistReActExecutor"]
