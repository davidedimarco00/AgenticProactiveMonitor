"""Compatibility module: specialist behavior is configured by role at runtime."""
from .specialist import SpecialistAgent
SystemEngineerAgent = SpecialistAgent
__all__ = ["SystemEngineerAgent"]
