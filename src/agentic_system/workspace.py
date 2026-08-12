from __future__ import annotations

import asyncio
from copy import deepcopy

from .models import Evidence, Hypothesis, Incident, IncidentStatus


class IncidentWorkspace:
    """Concurrency-safe in-memory blackboard.

    It is intentionally repository-agnostic so that an OpenSearch-backed
    implementation can replace it without changing the agents.
    """

    def __init__(self) -> None:
        self._incidents: dict[str, Incident] = {}
        self._lock = asyncio.Lock()

    async def create(self, incident: Incident) -> Incident:
        async with self._lock:
            self._incidents[incident.incident_id] = deepcopy(incident)
            return deepcopy(incident)

    async def get(self, incident_id: str) -> Incident:
        async with self._lock:
            if incident_id not in self._incidents:
                raise KeyError(f"Unknown incident: {incident_id}")
            return deepcopy(self._incidents[incident_id])

    async def add_evidence(self, evidence: Evidence) -> None:
        async with self._lock:
            incident = self._incidents[evidence.incident_id]
            incident.evidence.append(evidence)

    async def replace_hypotheses(self, incident_id: str, hypotheses: list[Hypothesis]) -> None:
        async with self._lock:
            self._incidents[incident_id].hypotheses = hypotheses

    async def start_round(self, incident_id: str) -> int:
        async with self._lock:
            incident = self._incidents[incident_id]
            incident.investigation_round += 1
            incident.status = IncidentStatus.INVESTIGATING
            return incident.investigation_round

    async def confirm(self, incident_id: str, hypothesis_id: str) -> None:
        async with self._lock:
            incident = self._incidents[incident_id]
            incident.confirmed_hypothesis_id = hypothesis_id
            incident.status = IncidentStatus.DIAGNOSED
            for hypothesis in incident.hypotheses:
                hypothesis.confirmed = hypothesis.hypothesis_id == hypothesis_id

    async def fail(self, incident_id: str) -> None:
        async with self._lock:
            self._incidents[incident_id].status = IncidentStatus.FAILED
