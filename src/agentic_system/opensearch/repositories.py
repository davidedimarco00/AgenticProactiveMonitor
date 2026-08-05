from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .client import OpenSearchClient


class MetricsRepository:
    def __init__(self, client: OpenSearchClient, index_pattern: str = 'metrics-*') -> None:
        self.client = client
        self.index_pattern = index_pattern

    async def window(self, host_id: str, metric: str, minutes: int = 10) -> list[dict[str, Any]]:
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=minutes)
        body = {
            'size': 500,
            'sort': [{'@timestamp': 'asc'}],
            '_source': ['@timestamp', 'host_id', 'machine_role', metric],
            'query': {'bool': {'filter': [
                {'term': {'host_id.keyword': host_id}},
                {'range': {'@timestamp': {'gte': start.isoformat(), 'lte': end.isoformat()}}},
                {'exists': {'field': metric}},
            ]}},
        }
        data = await self.client.search(self.index_pattern, body)
        return [hit['_source'] for hit in data.get('hits', {}).get('hits', [])]

    async def discover_hosts(self) -> list[dict[str, str]]:
        body = {'size': 0, 'aggs': {'hosts': {'terms': {'field': 'host_id.keyword', 'size': 100}, 'aggs': {'role': {'terms': {'field': 'machine_role.keyword', 'size': 1}}}}}}
        data = await self.client.search(self.index_pattern, body)
        result: list[dict[str, str]] = []
        for bucket in data.get('aggregations', {}).get('hosts', {}).get('buckets', []):
            roles = bucket.get('role', {}).get('buckets', [])
            result.append({'host_id': bucket['key'], 'machine_role': roles[0]['key'] if roles else 'unknown'})
        return result


class LogsRepository:
    def __init__(self, client: OpenSearchClient, index_pattern: str = 'logs-*') -> None:
        self.client = client
        self.index_pattern = index_pattern

    async def window(self, host_id: str, minutes: int = 10, limit: int = 300) -> list[dict[str, Any]]:
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=minutes)
        body = {
            'size': limit,
            'sort': [{'@timestamp': 'asc'}],
            'query': {'bool': {'filter': [
                {'term': {'host_id.keyword': host_id}},
                {'range': {'@timestamp': {'gte': start.isoformat(), 'lte': end.isoformat()}}},
            ]}},
        }
        data = await self.client.search(self.index_pattern, body)
        return [hit['_source'] for hit in data.get('hits', {}).get('hits', [])]
