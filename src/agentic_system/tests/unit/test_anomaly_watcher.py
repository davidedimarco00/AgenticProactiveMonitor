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


def test_queue_acceptance_owns_deduplication_across_repeated_polls() -> None:
    accepted_keys: set[str] = set()
    delivered: list[str] = []

    async def on_anomaly(observation) -> bool:
        key = observation.deduplication_key
        if key in accepted_keys:
            return False
        accepted_keys.add(key)
        delivered.append(observation.result_id)
        return True

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
    assert delivered == ["result-1"]
    assert watcher.delivered_count == 1
    assert watcher.duplicate_count == 1


def test_failed_enqueue_does_not_block_other_results() -> None:
    attempts: dict[str, int] = {}
    delivered: list[str] = []

    async def on_anomaly(observation) -> bool:
        attempts[observation.result_id] = attempts.get(observation.result_id, 0) + 1
        if observation.result_id == "result-failing" and attempts[observation.result_id] == 1:
            raise RuntimeError("temporary queue failure")
        delivered.append(observation.result_id)
        return True

    watcher = OpenSearchAnomalyWatcher(
        opensearch_url="http://opensearch:9200",
        on_anomaly=on_anomaly,
        poll_interval_seconds=5,
        lookback_seconds=300,
    )

    async def fake_fetch_hits() -> list[dict[str, object]]:
        return [
            _anomaly_hit(result_id="result-failing"),
            _anomaly_hit(result_id="result-healthy"),
        ]

    watcher._fetch_hits = fake_fetch_hits  # type: ignore[method-assign]

    assert asyncio.run(watcher.poll_once()) == 1
    assert delivered == ["result-healthy"]
    assert watcher.failed_delivery_count == 1
    assert watcher.last_error == "temporary queue failure"

    # The watcher remains alive and retries producer delivery on a later poll.
    assert asyncio.run(watcher.poll_once()) == 2
    assert delivered == ["result-healthy", "result-failing", "result-healthy"]
    assert attempts["result-failing"] == 2
    assert watcher.delivered_count == 3
    assert watcher.last_error is None
