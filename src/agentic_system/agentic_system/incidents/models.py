from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class IncidentWorkflowResult:
    """Result produced when one anomaly is applied to the incident lifecycle.

    Mapping-style access is kept for compatibility with callers that previously
    received the incident document directly.
    """

    incident: dict[str, Any]
    created: bool
    correlated: bool

    def __getitem__(self, key: str) -> Any:
        return self.incident[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.incident.get(key, default)
