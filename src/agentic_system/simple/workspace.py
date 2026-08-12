from __future__ import annotations

import asyncio
from copy import deepcopy

from .models import CriticReview, Diagnosis, IncidentContext, IncidentStatus


class Workspace:
    def __init__(self) -> None:
        self._items: dict[str, IncidentContext] = {}
        self._lock = asyncio.Lock()

    async def create(self, incident: IncidentContext) -> None:
        async with self._lock:
            self._items[incident.incident_id] = deepcopy(incident)

    async def get(self, incident_id: str) -> IncidentContext:
        async with self._lock:
            return deepcopy(self._items[incident_id])

    async def begin_round(self, incident_id: str) -> None:
        async with self._lock:
            item = self._items[incident_id]
            item.round += 1
            item.status = IncidentStatus.COLLECTING_EVIDENCE

    async def add_evidence(self, incident_id: str, evidence: dict) -> None:
        async with self._lock:
            self._items[incident_id].evidence.append(deepcopy(evidence))

    async def set_diagnosis(self, incident_id: str, diagnosis: Diagnosis) -> None:
        async with self._lock:
            item = self._items[incident_id]
            item.diagnosis = diagnosis
            item.status = IncidentStatus.REVIEWING

    async def set_review(self, incident_id: str, review: CriticReview) -> None:
        async with self._lock:
            self._items[incident_id].critic_review = review

    async def set_status(self, incident_id: str, status: IncidentStatus) -> None:
        async with self._lock:
            self._items[incident_id].status = status

    async def set_remediation(self, incident_id: str, report: dict) -> None:
        async with self._lock:
            self._items[incident_id].remediation = deepcopy(report)
