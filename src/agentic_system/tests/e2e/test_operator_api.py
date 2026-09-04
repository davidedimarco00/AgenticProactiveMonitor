from __future__ import annotations

import json
import os
from urllib.request import urlopen

import pytest
from pytest_bdd import given, scenarios, then, when

from tests.integration.conftest import wait_for_backend_ready


pytestmark = pytest.mark.e2e
scenarios("features/operator_api.feature")

API_URL = os.getenv("AGENTIC_API_TEST_URL", "http://127.0.0.1:8082").rstrip("/")


@given("the agentic backend is ready for operator API checks")
def backend_is_ready_for_operator_api() -> None:
    wait_for_backend_ready()


@when("the operator inspects the published API contract", target_fixture="openapi")
def operator_reads_openapi() -> dict:
    with urlopen(f"{API_URL}/openapi.json", timeout=5) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


@then("Swagger documentation is reachable")
def swagger_is_reachable() -> None:
    with urlopen(f"{API_URL}/docs", timeout=5) as response:  # noqa: S310
        assert response.status == 200
        assert "text/html" in response.headers.get("Content-Type", "")


@then("public incident operations are read only")
def incident_api_is_read_only(openapi: dict) -> None:
    paths = openapi["paths"]
    assert "get" in paths["/api/v1/incidents"]
    assert "post" not in paths["/api/v1/incidents"]
    assert "get" in paths["/api/v1/incidents/{incident_id}"]
    assert "patch" not in paths["/api/v1/incidents/{incident_id}"]
    assert not any(path.startswith("/internal/") for path in paths)


@then("the incident PDF report endpoint is published")
def incident_pdf_is_published(openapi: dict) -> None:
    assert "get" in openapi["paths"]["/api/v1/incidents/{incident_id}/report"]
