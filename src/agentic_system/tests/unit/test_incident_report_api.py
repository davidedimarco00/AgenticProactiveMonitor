from types import SimpleNamespace

from fastapi.testclient import TestClient

from agentic_system.api import create_api_app


class FakeRepository:
    async def get_incident(self, incident_id: str):
        if incident_id != "INC-PDF-001":
            return None
        return {
            "incident_id": incident_id,
            "status": "OPERATOR_ACTION_REQUIRED",
            "severity": "HIGH",
            "entity": "processing-service",
            "created_at": "2026-08-21T16:00:00+00:00",
            "updated_at": "2026-08-21T16:03:00+00:00",
            "anomaly": {
                "detector_name": "CPU-processing-service",
                "anomaly_type": "CPU",
                "grade": 1.0,
                "confidence": 0.95,
            },
            "diagnosis": {
                "summary": "CPU saturation is localized to processing-service.",
                "root_cause": "CPU-bound worker execution.",
                "confidence": 0.9,
                "evidence": ["CPU-heavy worker process observed."],
            },
            "agentic": {"review_confidence": 0.97},
            "remediation": {
                "status": "ADVISORY",
                "summary": "Verify the workload and apply the approved remediation.",
                "steps": [
                    {
                        "title": "Verify processes",
                        "target": "processing-service",
                        "command_type": "verification",
                        "command": "docker exec processing-service ps -eo pid,comm,%cpu --sort=-%cpu",
                        "purpose": "Confirm the CPU-heavy process.",
                        "expected_result": "The offending process is visible.",
                        "what_to_verify": "CPU usage matches the diagnosis.",
                    }
                ],
            },
            "validation": {
                "status": "OPERATOR_ACTION_PENDING",
                "summary": "Operator action is required.",
            },
        }

    async def list_events(self, **kwargs):
        return []


class FakeRuntime:
    def __init__(self) -> None:
        self.started = True
        self.agents = [SimpleNamespace() for _ in range(5)]
        self.team_communication_ok = True
        self.unreachable_specialists = []

    @property
    def running_count(self) -> int:
        return 5

    def snapshot(self):
        return []


def test_pdf_report_endpoint_returns_real_pdf() -> None:
    app = create_api_app(FakeRuntime(), FakeRepository())  # type: ignore[arg-type]
    client = TestClient(app)

    response = client.get("/api/v1/incidents/INC-PDF-001/report")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert "INC-PDF-001-incident-report.pdf" in response.headers["content-disposition"]
    assert response.content.startswith(b"%PDF")
