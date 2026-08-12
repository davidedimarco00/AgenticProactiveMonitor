import httpx
import pytest

from src.agentic_system.detectors.manager import DetectorManager


class FakeMetrics:
    async def discover_hosts(self):
        return [{"host_id": "processing-service", "machine_role": "processing-service"}]


class EmptyMetrics:
    async def discover_hosts(self):
        return []


class FakeClient:
    def __init__(self):
        self.requests = []

    async def request(self, method, path, json=None):
        self.requests.append((method, path, json))
        if path.endswith("/_search"):
            return {"hits": {"hits": []}}
        if path.endswith("/_start"):
            return {}
        return {"_id": "detector-1"}


class MissingRegistryClient:
    async def request(self, method, path, json=None):
        request = httpx.Request(method, f"http://opensearch:9200{path}")
        response = httpx.Response(
            404,
            request=request,
            json={
                "error": {
                    "type": "index_not_found_exception",
                    "index": ".opendistro-anomaly-detectors",
                }
            },
        )
        raise httpx.HTTPStatusError(
            "missing detector registry",
            request=request,
            response=response,
        )


@pytest.mark.asyncio
async def test_synchronise_creates_missing_detector():
    manager = DetectorManager(FakeClient(), FakeMetrics())
    result = await manager.synchronise(["cpu.usage_active"])
    assert result["auto-processing-service-cpu-usage-active"] == "detector-1"


@pytest.mark.asyncio
async def test_fresh_cluster_detector_index_is_treated_as_empty():
    manager = DetectorManager(MissingRegistryClient(), FakeMetrics())
    assert await manager.existing_by_name() == {}


@pytest.mark.asyncio
async def test_no_metric_hosts_skips_detector_registry_query():
    client = FakeClient()
    manager = DetectorManager(client, EmptyMetrics())
    assert await manager.synchronise(["cpu.usage_active"]) == {}
    assert client.requests == []
