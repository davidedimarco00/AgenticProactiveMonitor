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

For each scenario the main reported measurements are:

- completed diagnoses / total runs;
- manually reviewed correct diagnoses / total runs;
- expected evidence points found / expected evidence points;
- mean diagnosis time;
- mean number of ReAct steps;
- mean number of tool calls.

The harness still writes deterministic keyword-oriented location/fault fields to `scores.json`, but these fields are **not the final correctness judgement used in the thesis**. A diagnosis can mention an expected term while explicitly rejecting that cause. Therefore final diagnostic correctness is reviewed manually against the known injected fault and the final causal conclusion in `incident.json`.

There is **no combined Diagnostic Score**, no Efficiency formula, no Tool Sequence Similarity (TSS), no Argument Consistency (AC), and no weighted correctness formula in the main evaluation.

Detection Rate, Time-to-Detect, anomaly grade, detector confidence and detector score are also intentionally **not evaluation metrics** because the anomaly trigger is synthetic.

## Frozen baseline

The completed baseline campaign uses:

```text
reasoning model: gemma4:e4b
tool model:      qwen3.5:4b
embedding model: ibm/granite-embedding:30m
max ReAct steps: 6
specialist reasoning/finalization temperature: 0.0
```

The baseline contains 15 measured runs: five CPU, five memory and five network-latency scenarios. It is treated as the fixed reference configuration and is not rerun when sensitivity experiments are executed.

The final manual baseline correctness review is:

```text
CPU spike:        5/5 correct diagnoses
Memory leak:      3/5 correct diagnoses
Network latency:  1/5 correct diagnoses
Overall:          9/15 correct diagnoses
```

The objective baseline measurements are:

```text
Completed workflows: 15/15
Evidence collected:  39/45
Mean diagnosis time: 267.55 s
Mean ReAct steps:     5.47
Mean tool calls:      5.07
```

## Configuration sensitivity experiments

Three additional experiments change one diagnostic configuration dimension at a time. Only `memory-leak` and `network-latency` are repeated because the baseline CPU scenario already reached 5/5 correct diagnoses.

| Experiment | Reasoning model | Max ReAct steps | Specialist temperature | Runs |
| --- | --- | ---: | ---: | ---: |
| Baseline | `gemma4:e4b` | 6 | 0.0 | 15 already completed |
| `more-reasoning` | `gemma4:e4b` | 10 | 0.0 | 10 |
| `different-model` | `gemma4:e2b` | 6 | 0.0 | 10 |
| `higher-temperature` | `gemma4:e4b` | 6 | 0.5 | 10 |

The tool model (`qwen3.5:4b`), embedding model (`ibm/granite-embedding:30m`), prompts, MCP tools, Qdrant knowledge base, fault parameters and infrastructure remain unchanged.

### Temperature scope

The temperature experiment intentionally changes only the **specialist diagnostic reasoning and finalization** calls performed through the native Ollama structured-output path. Technical Lead reasoning and Qwen tool selection are left unchanged from the frozen baseline. This avoids introducing multiple simultaneous changes into the temperature comparison.

## Running the sensitivity experiments

Pull the current `evaluation` branch first:

```powershell
git switch evaluation
git pull
```

Because the sensitivity support changes the backend image, use `-PrepareEnvironment` for the first smoke test after pulling.

### 1. More reasoning steps

Smoke test:

```powershell
.\evaluation\scripts\Invoke-ConfigurationExperiment.ps1 `
    -Experiment more-reasoning `
    -Scenario network-latency `
    -Repetitions 1 `
    -RecoverySeconds 10 `
    -PrepareEnvironment
```

Final experiment (5 memory + 5 network runs):

```powershell
.\evaluation\scripts\Invoke-ConfigurationExperiment.ps1 -Experiment more-reasoning
```

### 2. Different reasoning model

Smoke test:

```powershell
.\evaluation\scripts\Invoke-ConfigurationExperiment.ps1 `
    -Experiment different-model `
    -Scenario network-latency `
    -Repetitions 1 `
    -RecoverySeconds 10
```

Final experiment:

```powershell
.\evaluation\scripts\Invoke-ConfigurationExperiment.ps1 -Experiment different-model
```

### 3. Higher specialist reasoning temperature

Smoke test:

```powershell
.\evaluation\scripts\Invoke-ConfigurationExperiment.ps1 `
    -Experiment higher-temperature `
    -Scenario network-latency `
    -Repetitions 1 `
    -RecoverySeconds 10
```

Final experiment:

```powershell
.\evaluation\scripts\Invoke-ConfigurationExperiment.ps1 -Experiment higher-temperature
```

Each final sensitivity campaign creates a separate timestamped directory under `evaluation/results/`. Do not combine smoke-test directories with final measurements.

After a final campaign, manually review the ten final causal conclusions before reporting `correct diagnosis`, using the same scenario-specific criteria used for the frozen baseline.

## Experimental controls

For comparable runs the harness keeps the following conditions fixed unless they are the explicit independent variable of the experiment:

- real fault parameters;
- fault stabilization time before the synthetic trigger;
- prompts and agent coordination logic;
- MCP tools;
- Qdrant knowledge base;
- embedding model;
- hardware environment;
- runtime recovery between runs.

Automatic model fallback is not used. A selected model failure remains visible as an experimental outcome.

The backend is recreated before each run with:

```text
ENABLE_TEST_ANOMALY_INJECTION=1
ENABLE_OPENSEARCH_ANOMALY_WATCHER=0
```

through `src/infrastructure/docker-compose.test.yml`.

## Run lifecycle

Each measured run performs:

```text
reset monitored system
    -> recreate evaluation backend
    -> warm local models
    -> short runtime recovery
    -> start REAL controlled fault
    -> wait scenario-specific stabilization time
    -> inject SYNTHETIC SINGLE_ENTITY anomaly
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

Each run stores `metadata.json`, `trigger.json`, `incident.json` and `scores.json`. `summary.csv` contains the objective scenario aggregates, while final causal correctness is manually reviewed from the persisted diagnosis.

## Evaluation integrity

Do not edit the frozen ground truth or baseline results after inspecting the measured outcomes. Do not delete failed, timed-out or inconclusive runs; they are part of the experimental dataset.

The thesis must explicitly state that OpenSearch anomaly detection is part of the implemented architecture but is not experimentally evaluated in these campaigns. Synthetic anomaly injection is used to isolate the diagnostic subsystem while the underlying faults and diagnostic evidence remain real.
