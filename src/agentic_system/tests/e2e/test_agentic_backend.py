import pytest
from pytest_bdd import given, scenarios, then

from tests.integration.conftest import wait_for_backend_ready


pytestmark = pytest.mark.e2e
scenarios("features/agentic_backend.feature")


@given("the agentic backend is ready", target_fixture="health")
def backend_is_ready() -> dict:
    return wait_for_backend_ready()


@then("five agents are running")
def five_agents_are_running(health: dict) -> None:
    assert health["agents_running"] == 5
    assert len(health["agents"]) == 5


@then("all agents use SPADE-LLM")
def all_agents_use_spade_llm(health: dict) -> None:
    assert health["framework"] == "SPADE-LLM"
    assert all(agent["llm_agent"] is True for agent in health["agents"])


@then("all agents are connected to XMPP")
def all_agents_are_connected(health: dict) -> None:
    assert all(agent["xmpp_connected"] is True for agent in health["agents"])


@then("the communication probe is successful")
def communication_probe_is_successful(health: dict) -> None:
    assert health["communication_probe"]["status"] == "passed"
    assert health["team_communication_ok"] is True


@then("request and response use the same correlation id")
def communication_is_correlated(health: dict) -> None:
    probe = health["communication_probe"]
    assert probe["request_correlation_id"] == probe["response_correlation_id"]


@then("durable task recovery is exposed")
def durable_task_recovery_is_exposed(health: dict) -> None:
    recovery = health["task_recovery"]
    assert set(recovery) == {"scanned", "retrying", "failed"}
    assert all(isinstance(recovery[key], int) for key in recovery)


@then("incomplete incident recovery is exposed")
def incomplete_incident_recovery_is_exposed(health: dict) -> None:
    recovery = health["incident_recovery"]
    assert set(recovery) == {"scanned", "resumed", "failed"}
    assert all(isinstance(recovery[key], int) for key in recovery)


@then("the anomaly pipeline allows only one active anomaly")
def anomaly_pipeline_is_single_active(health: dict) -> None:
    watcher = health["anomaly_watcher"]
    assert watcher["processing_mode"] == "FIFO_SINGLE_ACTIVE"
    assert watcher["max_concurrent_anomalies"] == 1
