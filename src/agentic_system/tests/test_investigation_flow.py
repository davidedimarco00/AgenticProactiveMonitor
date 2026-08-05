from src.agentic_system.models import DiagnosticTest


def test_diagnostic_test_is_structured_and_non_destructive() -> None:
    test = DiagnosticTest(
        incident_id="inc-1",
        hypothesis_id="h-1",
        action="inspect_container",
        target="machine-03",
        rationale="Gather runtime evidence",
    )
    assert test.action == "inspect_container"
    assert test.target == "machine-03"
