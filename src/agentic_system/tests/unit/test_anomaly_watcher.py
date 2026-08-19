from __future__ import annotations

import asyncio

from agentic_system.integrations import (
    OpenSearchAnomalyWatcher,
    anomaly_observation_from_hit,
)


def _anomaly_hit(*, result_id: str = "result-1", grade: float = 1.0) -> dict[str, object]:
    return {
        "_index": ".opensearch-anomaly-results-history-test",
        "_id": result_id,
        "_source": {
            "detector_id": "detector-123",
            "anomaly_grade": grade,
            "confidence": 0.91,
            "anomaly_score": 6.4,
            "data_start_time": 1_700_000_000_000,
            "data_end_time": 1_700_000_060_000,
            "execution_start_time": 1_700_000_120_000,
            "execution_end_time": 1_700_000_121_000,
            "feature_data": [{"feature_name": "CPU_ANOMALY", "data": 391.0}],
        },
    }


def test_anomaly_observation_keeps_only_workflow_metadata() -> None:
    observation = anomaly_observation_from_hit(_anomaly_hit())

    assert observation is not None
    assert observation.result_id == "result-1"
    assert observation.detector_id == "detector-123"
    assert observation.anomaly_grade == 1.0
    assert observation.confidence == 0.91
    assert "feature_data" not in observation.to_dict()


def test_non_anomalous_result_is_ignored() -> None:
    assert anomaly_observation_from_hit(_anomaly_hit(grade=0.0)) is None


def test_same_opensearch_result_is_delivered_only_once_per_runtime() -> None:
    delivered = []

    async def on_anomaly(observation) -> None:
        delivered.append(observation)

    watcher = OpenSearchAnomalyWatcher(
        opensearch_url="http://opensearch:9200",
        on_anomaly=on_anomaly,
        poll_interval_seconds=5,
        lookback_seconds=300,
    )

    async def fake_fetch_hits() -> list[dict[str, object]]:
        return [_anomaly_hit()]

    watcher._fetch_hits = fake_fetch_hits  # type: ignore[method-assign]

    assert asyncio.run(watcher.poll_once()) == 1
    assert asyncio.run(watcher.poll_once()) == 0
    assert len(delivered) == 1
    assert watcher.delivered_count == 1
