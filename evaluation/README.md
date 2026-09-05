# Automated Diagnostic Evaluation Harness

This directory contains the thesis evaluation harness for the `evaluation` branch.

The evaluation now isolates the **agentic diagnosis** from OpenSearch detector quality:

- the controlled fault is real and remains active during the diagnosis;
- the anomaly that starts the incident is injected synthetically through the backend test API;
- the OpenSearch anomaly watcher is disabled for the evaluation backend;
- OpenSearch remains available as a telemetry source for MCP diagnostic tools;
- MongoDB persistence, FIFO admission, incident creation, Technical Lead BDI, XMPP delegation, specialist BDI, ReAct execution, peer collaboration, RAG, MCP evidence collection and Technical Lead review remain real.

This design avoids measuring Random Cut Forest detection latency when the research question concerns diagnostic quality.

## Main scenarios

The baseline campaign uses:

- `cpu-spike` on `processing-service`;
- `memory-leak` on `worker-service`;
- `network-latency` from `api-gateway` to `processing-service`.

The existing fault scripts under `src/monitored_system/infrastructure/scenarios/` are reused. They are not duplicated by the evaluation harness.

## Metrics

The main diagnostic metrics are:

- Location Accuracy (LA);
- Type Accuracy (TA);
- Evidence Coverage;
- Diagnostic Score;
- diagnosis time from synthetic trigger to terminal incident;
- ReAct steps;
- tool calls;
- Tool Sequence Similarity (TSS);
- Argument Consistency (AC);
- Divergence Point;
- Structured Diagnosis Agreement.

Detection Rate, Time-to-Detect, anomaly grade, detector confidence and detector score are intentionally **not evaluation metrics** in this campaign because the anomaly trigger is synthetic.

The scoring rules are deterministic and are defined in `config/ground-truth.json`. No LLM is used as an evaluation judge.

## Experimental controls

For comparable runs the harness keeps the following conditions fixed:

- real fault parameters;
- fault stabilization time before the synthetic trigger;
- prompts and agent coordination logic;
- MCP tools;
- Qdrant knowledge base;
- embedding model;
- reasoning temperature;
- maximum ReAct steps;
- hardware environment;
- runtime recovery between runs.

Automatic model fallback is not used. A selected model failure remains visible as an experimental outcome.

The backend is recreated before each run with:

```text
ENABLE_TEST_ANOMALY_INJECTION=1
ENABLE_OPENSEARCH_ANOMALY_WATCHER=0
```

through the existing `src/infrastructure/docker-compose.test.yml` overlay.

## Baseline model profile

```text
reasoning: gemma4:e4b
tool:      qwen3.5:4b
embedding: ibm/granite-embedding:30m
temperature: 0
```

All configured models must already be installed in Ollama.

## First preparation

Run from Windows PowerShell in the repository root:

```powershell
git switch evaluation
git pull

.\evaluation\scripts\Invoke-Evaluation.ps1 -PrepareEnvironment -PreflightOnly
```

The preflight checks:

- Git branch;
- Docker and Python;
- configured Ollama models;
- OpenSearch availability for telemetry;
- backend health;
- monitored API Gateway health;
- availability of the synthetic anomaly injection route;
- absence of non-terminal incidents.

It does **not** wait for OpenSearch anomaly detectors and does not inject a fault.

## Recommended smoke test

Before the thesis measurements, execute one CPU diagnostic run:

```powershell
.\evaluation\scripts\Invoke-Evaluation.ps1 -Scenario cpu-spike -Repetitions 1 -RecoverySeconds 10
```

The smoke test validates the complete path:

```text
real CPU fault
    -> synthetic SINGLE_ENTITY anomaly trigger
    -> durable incident
    -> Technical Lead BDI
    -> specialist selection and XMPP dispatch
    -> live MCP/RAG evidence
    -> ReAct diagnosis
    -> Technical Lead review
    -> scoring
```

Do not include the smoke-test result in the final thesis measurements.

## Final baseline campaign

The default baseline campaign executes all three scenarios five times:

```powershell
.\evaluation\scripts\Invoke-Evaluation.ps1
```

Equivalent explicit command:

```powershell
.\evaluation\scripts\Invoke-Evaluation.ps1 `
    -Campaign baseline `
    -Scenario all `
    -Repetitions 5 `
    -RecoverySeconds 30
```

A single scenario can be evaluated with:

```powershell
.\evaluation\scripts\Invoke-Evaluation.ps1 `
    -Scenario memory-leak `
    -Repetitions 5
```

Because detector waiting has been removed, the campaign is substantially faster than the previous detector-inclusive version.

## Run lifecycle

Each measured run performs:

```text
reset monitored system
    -> recreate evaluation backend
    -> warm local models
    -> short runtime recovery
    -> start REAL controlled fault
    -> wait scenario-specific stabilization time
    -> inject SYNTHETIC anomaly
    -> wait for incident
    -> keep fault active while agents diagnose
    -> collect final incident and ReAct evidence
    -> stop fault
    -> reset monitored system
    -> score run
```

The synthetic anomaly contains the same kind of single-entity symptom and entity information that starts the production workflow, but it is not treated as evidence that the fault exists. Runtime claims must still be supported by live MCP observations.

## Results

Every campaign creates a timestamped directory under:

```text
evaluation/results/
```

Example:

```text
evaluation/results/baseline-20260905-120000/
    campaign-metadata.json
    baseline/
        cpu-spike/
            run-01/
                metadata.json
                trigger.json
                incident.json
                scores.json
            ...
        memory-leak/
        network-latency/
    summary.json
    summary.csv
    model-comparison.csv
```

`trigger.json` records the synthetic anomaly request and backend acceptance.

`incident.json` contains the durable incident, timeline and tasks returned by the backend API. The specialist completion events preserve structured ReAct outcomes, evidence, tools, arguments, observations and ReAct step counts.

`summary.csv` aggregates the thesis metrics by scenario.

`model-comparison.csv` aggregates diagnostic measurements by model profile. Reproducibility metrics are not mixed across different fault scenarios.

## Evaluation integrity

Do not edit `ground-truth.json` after inspecting measured results. If a scoring rule must be changed for a methodological reason, document the change and rerun the affected campaign.

Do not delete failed, timed-out or inconclusive runs. They are part of the experimental dataset.

The thesis must explicitly state that OpenSearch anomaly detection is part of the implemented architecture but is not experimentally evaluated in this campaign. Synthetic anomaly injection is used to isolate the diagnostic subsystem while the underlying faults and diagnostic evidence remain real.
