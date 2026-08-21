"""Compatibility module: specialist behavior is configured by role at runtime."""
from .specialist import SpecialistAgent
SoftwareDeveloperAgent = SpecialistAgent
__all__ = ["SoftwareDeveloperAgent"]
