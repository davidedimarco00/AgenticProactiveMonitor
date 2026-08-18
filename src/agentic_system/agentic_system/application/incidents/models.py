from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class IncidentWorkflowResult:
    """Result produced when one anomaly is applied to the incident lifecycle."""

    incident: dict[str, Any]
    created: bool
    correlated: bool
