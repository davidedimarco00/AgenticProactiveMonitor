from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..opensearch import OpenSearchClient, MetricsRepository


@dataclass(frozen=True)
class DetectorSpec:
    host_id: str
    metric: str
    name: str


class DetectorManager:
    def __init__(self, client: OpenSearchClient, metrics: MetricsRepository, interval_minutes: int = 1) -> None:
        self.client = client
        self.metrics = metrics
        self.interval_minutes = interval_minutes

    async def desired_specs(self, configured_metrics: list[str]) -> list[DetectorSpec]:
        hosts = await self.metrics.discover_hosts()
        specs: list[DetectorSpec] = []
        for host in hosts:
            for metric in configured_metrics:
                safe_metric = re.sub(r'[^a-zA-Z0-9]+', '-', metric).strip('-').lower()
                specs.append(DetectorSpec(host['host_id'], metric, f'auto-{host["host_id"]}-{safe_metric}'))
        return specs

    async def existing_by_name(self) -> dict[str, str]:
        data = await self.client.request('GET', '/_plugins/_anomaly_detection/detectors/_search?size=1000')
        result: dict[str, str] = {}
        for hit in data.get('hits', {}).get('hits', []):
            source = hit.get('_source', {})
            name = source.get('name')
            if name:
                result[name] = hit['_id']
        return result

    def payload(self, spec: DetectorSpec) -> dict[str, Any]:
        return {
            'name': spec.name,
            'description': f'Automatically managed detector for {spec.metric} on {spec.host_id}',
            'time_field': '@timestamp',
            'indices': ['metrics-*'],
            'filter_query': {'bool': {'filter': [{'term': {'host_id.keyword': spec.host_id}}, {'exists': {'field': spec.metric}}]}},
            'detection_interval': {'period': {'interval': self.interval_minutes, 'unit': 'Minutes'}},
            'window_delay': {'period': {'interval': 1, 'unit': 'Minutes'}},
            'shingle_size': 8,
            'schema_version': 0,
            'feature_attributes': [{
                'feature_name': f'avg_{spec.metric}',
                'feature_enabled': True,
                'aggregation_query': {'metric': {'avg': {'field': spec.metric}}},
            }],
        }

    async def synchronise(self, configured_metrics: list[str]) -> dict[str, str]:
        existing = await self.existing_by_name()
        created: dict[str, str] = {}
        for spec in await self.desired_specs(configured_metrics):
            if spec.name in existing:
                created[spec.name] = existing[spec.name]
                continue
            data = await self.client.request('POST', '/_plugins/_anomaly_detection/detectors', json=self.payload(spec))
            detector_id = data.get('_id') or data.get('id')
            if detector_id:
                created[spec.name] = detector_id
                await self.client.request('POST', f'/_plugins/_anomaly_detection/detectors/{detector_id}/_start')
        return created
