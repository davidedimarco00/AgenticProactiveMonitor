from __future__ import annotations

from typing import Any
from urllib.parse import quote

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentic_system.api import attach_anomaly_inbox_api


class FakeInbox:
    def __init__(self) -> None:
        self.record = {
            "anomaly_key": ".opensearch-anomaly-results-history-test:result-1",
            "result_id": "result-1",
            "result_index": ".opensearch-anomaly-results-history-test",
            "detector_id": "detector-123",
            "detector_name": "CPU-processing-service",
            "state": "WAITING",
            "incident_id": None,
        }

    async def get_anomaly(self, anomaly_key: str) -> dict[str, Any] | None:
        if anomaly_key != self.record["anomaly_key"]:
            return None
        return dict(self.record)

    async def dismiss_waiting_anomaly(
        self,
        anomaly_key: str,
        *,
        dismissed_by: str = "operator",
        reason: str = "",
    ) -> dict[str, Any] | None:
        if anomaly_key != self.record["anomaly_key"] or self.record["state"] != "WAITING":
            return None
        self.record.update(
            {
                "state": "DISMISSED",
                "dismissed_by": dismissed_by,
                "dismissal_reason": reason,
            }
        )
        return dict(self.record)

    async def list_anomalies(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return [dict(self.record)]

    async def count_anomalies(self, *, states: list[str] | None = None) -> int:
        if not states:
            return 1
        return 1 if self.record["state"] in states else 0


def test_delete_waiting_anomaly_soft_dismisses_false_positive() -> None:
    inbox = FakeInbox()
    app = FastAPI()
    attach_anomaly_inbox_api(app, inbox)  # type: ignore[arg-type]
    client = TestClient(app)

    encoded = quote(str(inbox.record["anomaly_key"]), safe="")
    response = client.delete(f"/api/v1/anomalies/{encoded}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "dismissed"
    assert payload["anomaly"]["state"] == "DISMISSED"
    assert payload["anomaly"]["dismissed_by"] == "operator"
    assert response.headers["access-control-allow-origin"] == "*"


def test_delete_rejects_anomaly_after_it_is_no_longer_waiting() -> None:
    inbox = FakeInbox()
    inbox.record["state"] = "PROCESSING"
    app = FastAPI()
    attach_anomaly_inbox_api(app, inbox)  # type: ignore[arg-type]
    client = TestClient(app)

    encoded = quote(str(inbox.record["anomaly_key"]), safe="")
    response = client.delete(f"/api/v1/anomalies/{encoded}")

    assert response.status_code == 409
