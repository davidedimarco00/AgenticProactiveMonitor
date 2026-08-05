from __future__ import annotations

from abc import ABC, abstractmethod
from statistics import mean
from typing import Any

from spade.behaviour import CyclicBehaviour
from spade.template import Template

from ..models import Evidence, EvidenceType
from ..protocol import MessageType, build_message, parse_payload
from .base import WorkspaceAgent


class EvidenceAgent(WorkspaceAgent, ABC):
    coordinator_jid: str

    @abstractmethod
    async def collect_evidence(self, incident_id: str) -> list[Evidence]:
        raise NotImplementedError

    class RequestBehaviour(CyclicBehaviour):
        async def run(self) -> None:
            message = await self.receive(timeout=10)
            if message is None:
                return
            incident_id = str(parse_payload(message)['incident_id'])
            evidence = await self.agent.collect_evidence(incident_id)
            for item in evidence:
                await self.agent.workspace.add_evidence(item)
            await self.send(build_message(self.agent.coordinator_jid, MessageType.EVIDENCE_SUBMITTED, {
                'incident_id': incident_id,
                'source_agent': str(self.agent.jid),
                'evidence': [item.model_dump(mode='json') for item in evidence],
            }))

    async def setup(self) -> None:
        template = Template()
        template.set_metadata('message_type', MessageType.EVIDENCE_REQUEST.value)
        self.add_behaviour(self.RequestBehaviour(), template)


class MetricsAgent(EvidenceAgent):
    metrics_repository: Any

    async def collect_evidence(self, incident_id: str) -> list[Evidence]:
        incident = await self.workspace.get(incident_id)
        samples = await self.metrics_repository.window(incident.host_id, incident.metric_name, minutes=10)
        values = [self._read_path(sample, incident.metric_name) for sample in samples]
        numeric = [float(value) for value in values if isinstance(value, (int, float))]
        summary = f'Collected {len(samples)} samples for {incident.metric_name} on {incident.host_id}'
        details = {'samples': samples[-50:], 'sample_count': len(samples)}
        if numeric:
            details.update({'min': min(numeric), 'max': max(numeric), 'mean': mean(numeric), 'latest': numeric[-1]})
            summary += f'; latest={numeric[-1]:.2f}, mean={mean(numeric):.2f}'
        return [Evidence(incident_id=incident_id, source_agent=str(self.jid), evidence_type=EvidenceType.METRIC,
                         summary=summary, details=details, confidence=0.9 if numeric else 0.4)]

    @staticmethod
    def _read_path(document: dict[str, Any], dotted: str) -> Any:
        value: Any = document
        for part in dotted.split('.'):
            if not isinstance(value, dict) or part not in value:
                return None
            value = value[part]
        return value


class LogsAgent(EvidenceAgent):
    logs_repository: Any

    async def collect_evidence(self, incident_id: str) -> list[Evidence]:
        incident = await self.workspace.get(incident_id)
        logs = await self.logs_repository.window(incident.host_id, minutes=10, limit=300)
        text = ' '.join(str(item).lower() for item in logs)
        keywords = {key: text.count(key) for key in ('error', 'timeout', 'retry', 'failed', 'refused')}
        relevant = sum(keywords.values())
        return [Evidence(
            incident_id=incident_id,
            source_agent=str(self.jid),
            evidence_type=EvidenceType.LOG,
            summary=f'Collected {len(logs)} logs on {incident.host_id}; {relevant} error-related markers found',
            details={'logs': logs[-100:], 'keyword_counts': keywords},
            confidence=0.85 if logs else 0.35,
        )]
