from types import SimpleNamespace

from agentic_system.api import attach_anomaly_inbox_api, create_api_app


class FakeRepository:
    pass


class FakeAnomalyInbox:
    pass


class FakeRuntime:
    def __init__(self) -> None:
        self.started = True
        self.agents = [SimpleNamespace() for _ in range(5)]
        self.team_communication_ok = True
        self.unreachable_specialists: list[str] = []

    @property
    def running_count(self) -> int:
        return 5

    def snapshot(self) -> list[dict[str, str]]:
        return []


def test_public_openapi_contract_is_read_only_for_incidents() -> None:
    app = create_api_app(FakeRuntime(), FakeRepository())  # type: ignore[arg-type]
    paths = app.openapi()["paths"]

    assert "get" in paths["/api/v1/incidents"]
    assert "post" not in paths["/api/v1/incidents"]
    assert "get" in paths["/api/v1/incidents/{incident_id}"]
    assert "patch" not in paths["/api/v1/incidents/{incident_id}"]
    assert "/internal/v1/incidents" not in paths
    assert "/internal/v1/incidents/{incident_id}" not in paths
    assert "/internal/v1/incidents/{incident_id}/events" not in paths


def test_public_openapi_exposes_pdf_report_endpoint() -> None:
    app = create_api_app(FakeRuntime(), FakeRepository())  # type: ignore[arg-type]
    paths = app.openapi()["paths"]

    assert "get" in paths["/api/v1/incidents/{incident_id}/report"]


def test_public_openapi_exposes_readable_inbox_and_waiting_anomaly_dismissal() -> None:
    app = create_api_app(FakeRuntime(), FakeRepository())  # type: ignore[arg-type]
    attach_anomaly_inbox_api(app, FakeAnomalyInbox())  # type: ignore[arg-type]
    paths = app.openapi()["paths"]

    assert "get" in paths["/api/v1/anomalies"]
    assert "post" not in paths["/api/v1/anomalies"]
    assert "put" not in paths["/api/v1/anomalies"]
    assert "delete" in paths["/api/v1/anomalies/{anomaly_key}"]
