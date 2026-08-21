"""Compatibility module: specialist behavior is configured by role at runtime."""
from .specialist import SpecialistAgent
NetworkEngineerAgent = SpecialistAgent
__all__ = ["NetworkEngineerAgent"]
