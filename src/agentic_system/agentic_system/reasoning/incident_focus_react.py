"""Compatibility import for the consolidated specialist ReAct executor.

Incident anchoring is now part of the canonical ``specialist_react.py`` runtime.
"""

from .specialist_react import SpecialistReActExecutor

__all__ = ["SpecialistReActExecutor"]
