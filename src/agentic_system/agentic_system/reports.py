"""Backward-compatible facade for report generation infrastructure."""

from .infrastructure.reports import build_incident_report

__all__ = ["build_incident_report"]
