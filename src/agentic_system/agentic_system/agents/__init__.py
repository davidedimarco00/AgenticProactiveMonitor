from .base import BaseAgent
from .specialist import SpecialistAgent
from .technical_lead import TechnicalLeadAgent

# Backwards-compatible role names. Runtime identity still comes from each SPADE
# agent's role/JID and prompt; four empty Python subclasses are no longer needed.
SystemEngineerAgent = SpecialistAgent
NetworkEngineerAgent = SpecialistAgent
ApplicationEngineerAgent = SpecialistAgent
SoftwareDeveloperAgent = SpecialistAgent

__all__ = [
    "BaseAgent",
    "SpecialistAgent",
    "TechnicalLeadAgent",
    "SystemEngineerAgent",
    "NetworkEngineerAgent",
    "ApplicationEngineerAgent",
    "SoftwareDeveloperAgent",
]
