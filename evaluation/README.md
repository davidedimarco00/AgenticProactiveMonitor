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

The comparison uses three controlled scenarios:

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

## Reference configuration

The reference profile is:

```text
reasoning model: gemma4:e4b
tool model:      qwen3.5:4b
embedding model: ibm/granite-embedding:30m
max ReAct steps: 6
specialist reasoning/finalization temperature: 0.0
```

Five repetitions are used for each scenario.

## Configuration sensitivity experiments

Three additional profiles change one diagnostic configuration dimension at a time.

| Experiment | Reasoning model | Max ReAct steps | Specialist temperature | Planned runs |
| --- | --- | ---: | ---: | ---: |
| Baseline | `gemma4:e4b` | 6 | 0.0 | 15 |
| `more-reasoning` | `gemma4:e4b` | 10 | 0.0 | 15 |
| `different-model` | `gemma4:e2b` | 6 | 0.0 | 15 |
| `higher-temperature` | `gemma4:e4b` | 6 | 0.5 | 15 |

The tool model (`qwen3.5:4b`), embedding model (`ibm/granite-embedding:30m`), prompts, Qdrant knowledge base, fault parameters and infrastructure remain fixed unless a documented tooling correction is being validated.

### Temperature scope

The temperature experiment intentionally changes only the **specialist diagnostic reasoning and finalization** calls performed through the native Ollama structured-output path. Technical Lead reasoning and Qwen tool selection are left unchanged from the reference profile.

## Detector-aligned network evidence correction

The first network-latency campaigns exposed a tooling limitation: CPU and RAM investigations could retrieve the exact historical OpenSearch field used by their SINGLE_ENTITY detector, while NETLAT investigations mainly relied on connectivity and point-in-time TCP checks.

The evaluation branch therefore extends `get_metrics` so that a `metric_history` request for a NETLAT incident can retrieve:

```text
measurement_name: network_transport_latency
field:            network_transport_latency.response_time
source:           metrics-<source-service>-*
path filter:      network_target=<destination-service>
```

This is the same Layer-4 response-time field used by the implemented SINGLE_ENTITY NETLAT detectors. The source and destination are bound from the incident evidence contract; the LLM is not allowed to invent the monitored path.

Earlier network results obtained before this correction are preserved as exploratory records but are **superseded for the final configuration comparison**. They must not be mixed with the corrected network measurements. CPU and RAM results remain usable because their detector-aligned metric path is unchanged.

For the final comparison, rerun exactly five corrected `network-latency` repetitions for every configuration profile.

## Running the experiments

Pull the current `evaluation` branch first:

```powershell
git switch evaluation
git pull
```

Because the MCP and evaluation backend code changed, use environment preparation for the first corrected smoke test.

### Corrected network smoke test

```powershell
.\evaluation\scripts\Invoke-ConfigurationExperiment.ps1 `
    -Experiment more-reasoning `
    -Scenario network-latency `
    -Repetitions 1 `
    -RecoverySeconds 10 `
    -PrepareEnvironment
```

The resulting incident should contain a `get_metrics` observation with:

```text
metric = network_transport_latency
measurement_name = network_transport_latency
field = network_transport_latency.response_time
target_host = processing-service
detector_aligned = true
```

Only after this smoke test passes should the corrected network campaigns be measured.

### Corrected network reruns

Reference profile:

```powershell
.\evaluation\scripts\Invoke-Evaluation.ps1 `
    -Campaign baseline `
    -Scenario network-latency `
    -Repetitions 5
```

More reasoning:

```powershell
.\evaluation\scripts\Invoke-ConfigurationExperiment.ps1 `
    -Experiment more-reasoning `
    -Scenario network-latency `
    -Repetitions 5
```

Different reasoning model:

```powershell
.\evaluation\scripts\Invoke-ConfigurationExperiment.ps1 `
    -Experiment different-model `
    -Scenario network-latency `
    -Repetitions 5
```

Higher specialist temperature:

```powershell
.\evaluation\scripts\Invoke-ConfigurationExperiment.ps1 `
    -Experiment higher-temperature `
    -Scenario network-latency `
    -Repetitions 5
```

If CPU and memory have not yet been measured for `higher-temperature`, run the full campaign instead:

```powershell
.\evaluation\scripts\Invoke-ConfigurationExperiment.ps1 `
    -Experiment higher-temperature `
    -Scenario all `
    -Repetitions 5
```

Each campaign creates a separate timestamped directory under `evaluation/results/`. Do not combine smoke-test directories with final measurements. Preserve all older result directories for traceability.

After each final campaign, manually review the final causal conclusions before reporting `correct diagnosis`, using the same scenario-specific criteria for every profile.

## Experimental controls

For comparable runs the harness keeps the following conditions fixed unless they are the explicit independent variable:

- real fault parameters;
- fault stabilization time before the synthetic trigger;
- prompts and agent coordination logic;
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

The synthetic anomaly contains the same type of SINGLE_ENTITY symptom and entity information that starts the production workflow, but it is not treated as proof that the fault exists. Runtime claims must still be supported by live MCP observations.

## Results

Every campaign creates a timestamped directory under:

```text
evaluation/results/
```

Each run stores `metadata.json`, `trigger.json`, `incident.json` and `scores.json`. `summary.csv` contains the objective scenario aggregates, while final causal correctness is manually reviewed from the persisted diagnosis.

## Evaluation integrity

Do not edit the frozen ground truth or delete failed, timed-out, inconclusive or superseded runs. Superseded network runs remain part of the experiment history but are excluded from the final corrected network comparison for the documented tooling reason above.

The thesis must explicitly state that OpenSearch anomaly detection is part of the implemented architecture but is not experimentally evaluated in these campaigns. Synthetic anomaly injection is used to isolate the diagnostic subsystem while the underlying faults and diagnostic evidence remain real.
