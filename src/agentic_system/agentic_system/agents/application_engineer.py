"""Compatibility module: specialist behavior is configured by role at runtime."""
from .specialist import SpecialistAgent
ApplicationEngineerAgent = SpecialistAgent
__all__ = ["ApplicationEngineerAgent"]
