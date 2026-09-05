# Automated Diagnostic Evaluation Harness

This directory contains the thesis evaluation harness for the `evaluation` branch.

The evaluation isolates the **agentic diagnosis** from OpenSearch detector quality:

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

## Simple evaluation metrics

The thesis evaluation intentionally uses simple task-oriented measurements instead of a weighted diagnostic score.

For each scenario the harness reports:

- completed diagnoses / total runs;
- correct fault location / total runs;
- correct fault type / total runs;
- expected evidence points found / expected evidence points;
- mean diagnosis time;
- mean number of ReAct steps;
- mean number of tool calls.

The detailed `scores.json` file for each run also keeps the diagnosed root cause, matched evidence points and the tool sequence for qualitative inspection.

There is **no combined Diagnostic Score**, no Efficiency formula, no Tool Sequence Similarity (TSS), no Argument Consistency (AC), and no weighted correctness formula in the main evaluation.

Detection Rate, Time-to-Detect, anomaly grade, detector confidence and detector score are also intentionally **not evaluation metrics** because the anomaly trigger is synthetic.

The correctness checks are deterministic and use the frozen expectations in `config/ground-truth.json`. No LLM is used as an evaluation judge.

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

## Recommended smoke tests

Before the final thesis measurements, execute one run for each scenario:

```powershell
.\evaluation\scripts\Invoke-Evaluation.ps1 -Scenario cpu-spike -Repetitions 1 -RecoverySeconds 10
.\evaluation\scripts\Invoke-Evaluation.ps1 -Scenario memory-leak -Repetitions 1 -RecoverySeconds 10
.\evaluation\scripts\Invoke-Evaluation.ps1 -Scenario network-latency -Repetitions 1 -RecoverySeconds 10
```

Smoke tests validate the complete workflow but must not be included in the final thesis measurements.

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

The synthetic anomaly contains the same type of single-entity symptom and entity information that starts the production workflow, but it is not treated as proof that the fault exists. Runtime claims must still be supported by live MCP observations.

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

`incident.json` contains the durable incident, timeline and tasks returned by the backend API.

`scores.json` contains the simple result for one run: completion state, location correctness, fault-type correctness, evidence count, diagnosis time, ReAct steps and tool calls.

`summary.csv` is the main thesis-oriented export. For each scenario it reports values such as:

```text
runs
completed_runs
correct_location_runs
correct_fault_runs
evidence_points_matched
evidence_points_expected
mean_diagnosis_time_seconds
mean_react_steps
mean_tool_calls
```

`model-comparison.csv` contains the same simple measurements aggregated by model profile and can be used later if alternative model configurations are evaluated.

## Evaluation integrity

Do not edit `ground-truth.json` after inspecting measured results. If a correctness expectation must be changed for a methodological reason, document the change and rerun the affected campaign.

Do not delete failed, timed-out or inconclusive runs. They are part of the experimental dataset.

The thesis must explicitly state that OpenSearch anomaly detection is part of the implemented architecture but is not experimentally evaluated in this campaign. Synthetic anomaly injection is used to isolate the diagnostic subsystem while the underlying faults and diagnostic evidence remain real.
