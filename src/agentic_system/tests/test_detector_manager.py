import pytest

from src.agentic_system.detectors.manager import DetectorManager


class FakeMetrics:
    async def discover_hosts(self):
        return [{"host_id": "machine-03", "machine_role": "api-gateway"}]


class FakeClient:
    def __init__(self):
        self.calls = []

    async def request(self, method, path, json=None):
        self.calls.append((method, path, json))
        if path.endswith("/_search"):
            return {"hits": {"hits": []}}
        if path.endswith("/_start"):
            return {}
        return {"_id": "detector-1"}


@pytest.mark.asyncio
async def test_synchronise_creates_missing_detector():
    client = FakeClient()
    manager = DetectorManager(client, FakeMetrics())

    result = await manager.synchronise(["cpu.usage_active"])

    assert result["auto-machine-03-cpu-usage-active"] == "detector-1"
    assert client.calls[0] == (
        "POST",
        "/_plugins/_anomaly_detection/detectors/_search",
        {"size": 1000, "query": {"match_all": {}}},
    )


@pytest.mark.asyncio
async def test_existing_detector_supports_nested_source_shape():
    class ExistingClient(FakeClient):
        async def request(self, method, path, json=None):
            return {
                "hits": {
                    "hits": [
                        {
                            "_id": "existing-1",
                            "_source": {"detector": {"name": "existing-detector"}},
                        }
                    ]
                }
            }

    manager = DetectorManager(ExistingClient(), FakeMetrics())

    assert await manager.existing_by_name() == {
        "existing-detector": "existing-1"
    }
