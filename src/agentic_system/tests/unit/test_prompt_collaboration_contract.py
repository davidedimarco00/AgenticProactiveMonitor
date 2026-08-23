from agentic_system.reasoning.prompt_engineered_collaboration import (
    _PromptDiagnosticFinalOutput,
    _PromptGemmaDiagnosticFinalizer,
)


def _base_payload() -> dict:
    return {
        "summary": "Network evidence does not explain the observed service latency.",
        "diagnosis_status": "inconclusive",
        "root_cause": None,
        "causal_chain": [],
        "confidence": 0.5,
        "findings": ["DNS and TCP connectivity are healthy."],
        "hypotheses": ["Application-level processing delay remains plausible."],
        "recommended_next_steps": [
            "Application specialist should inspect service timing and logs for internal delay."
        ],
        "assistance_domain": "application",
    }


def test_assistance_required_is_derived_from_domain() -> None:
    output = _PromptDiagnosticFinalOutput.model_validate(_base_payload())

    assert output.assistance_domain == "application"
    assert output.assistance_required is True
    assert output.model_dump()["assistance_required"] is True


def test_null_domain_derives_no_assistance() -> None:
    payload = _base_payload()
    payload["assistance_domain"] = None

    output = _PromptDiagnosticFinalOutput.model_validate(payload)

    assert output.assistance_required is False


def test_validation_schema_does_not_ask_llm_for_assistance_required() -> None:
    schema = _PromptDiagnosticFinalOutput.model_json_schema(mode="validation")

    assert "assistance_domain" in schema["properties"]
    assert "assistance_required" not in schema["properties"]


def test_finalizer_schema_uses_domain_as_single_source_of_truth() -> None:
    finalizer = _PromptGemmaDiagnosticFinalizer(
        model="test-model",
        base_url="http://127.0.0.1:11434",
        timeout_seconds=1.0,
    )

    assert "assistance_domain" in finalizer.schema["properties"]
    assert "assistance_required" not in finalizer.schema["properties"]
