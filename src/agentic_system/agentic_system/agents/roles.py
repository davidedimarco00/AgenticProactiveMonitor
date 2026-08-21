"""Public role exports without one empty subclass per specialist domain."""

from .specialist import SpecialistAgent
from .technical_lead import TechnicalLeadAgent

SystemEngineerAgent = SpecialistAgent
NetworkEngineerAgent = SpecialistAgent
ApplicationEngineerAgent = SpecialistAgent
SoftwareDeveloperAgent = SpecialistAgent

__all__ = [
    "TechnicalLeadAgent",
    "SystemEngineerAgent",
    "NetworkEngineerAgent",
    "ApplicationEngineerAgent",
    "SoftwareDeveloperAgent",
]
