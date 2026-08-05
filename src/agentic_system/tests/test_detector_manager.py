import pytest

from src.agentic_system.detectors.manager import DetectorManager


class FakeMetrics:
    async def discover_hosts(self):
        return [{'host_id': 'machine-03', 'machine_role': 'api-gateway'}]


class FakeClient:
    async def request(self, method, path, json=None):
        if method == 'GET':
            return {'hits': {'hits': []}}
        if path.endswith('/_start'):
            return {}
        return {'_id': 'detector-1'}


@pytest.mark.asyncio
async def test_synchronise_creates_missing_detector():
    manager = DetectorManager(FakeClient(), FakeMetrics())
    result = await manager.synchronise(['cpu.usage_active'])
    assert result['auto-machine-03-cpu-usage-active'] == 'detector-1'
