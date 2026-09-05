# Automated Evaluation Harness

This directory contains the thesis evaluation harness for the `evaluation` branch.
It does not replace the existing monitored-system fault scripts. Instead, it orchestrates them, waits for the real OpenSearch anomaly, waits for the agentic diagnosis, stores the raw evidence, and computes the metrics used in Chapter 4.

## Scope

The main campaign evaluates three controlled scenarios:

- `cpu-spike` on `processing-service`;
- `memory-leak` on `worker-service`;
- `network-latency` from `api-gateway` to `processing-service`.

The harness checks the complete current detector set before starting: 5 CPU + 5 RAM + 3 NETLAT + 3 APPLAT detectors. Every detector must be `SINGLE_ENTITY` and `RUNNING`.

The main metrics are:

- Detection Rate and Time-to-Detect (TTD);
- Location Accuracy (LA) and Type Accuracy (TA);
- Evidence Coverage and Diagnostic Score;
- diagnosis time, ReAct steps, and tool calls;
- Tool Sequence Similarity (TSS);
- Argument Consistency (AC);
- Divergence Point;
- Structured Diagnosis Agreement.

The scoring rules are deterministic and are defined in `config/ground-truth.json`. No LLM is used as an evaluation judge.

## Important experimental controls

For comparable runs the harness keeps the following conditions fixed:

- fault parameters;
- OpenSearch detector configuration;
- prompts and agent coordination logic;
- MCP tools;
- Qdrant knowledge base and embedding model;
- reasoning temperature;
- recovery period;
- maximum ReAct steps;
- hardware environment.

The backend is recreated before each measured run so in-memory agent state is reset. Ollama models are warmed before the clean detector recovery window. Automatic fallback is not used: if the selected model fails, the failure remains visible in the experiment.

The controlled fault remains active until the agentic incident reaches a terminal state. This is intentional: the specialist must inspect the actual faulty runtime state rather than a system that has already recovered.

## Requirements

Run the harness from Windows PowerShell. The following commands must be available:

```powershell
git --version
docker --version
docker compose version
python --version
ollama list
```

Ollama must already be running on the Windows host.

The current baseline profile follows the `evaluation` branch configuration:

```text
reasoning: gemma4:e4b
tool:      qwen3.5:4b
embedding: ibm/granite-embedding:30m
```

The preflight stops immediately if one of the configured models is not installed. This is deliberate: the evaluation never substitutes another model silently.

## First preparation

From the repository root:

```powershell
git switch evaluation
git pull
.\evaluation\scripts\Invoke-Evaluation.ps1 -PrepareEnvironment -PreflightOnly
```

`-PrepareEnvironment` starts/recreates the required infrastructure, starts the monitored system in the base scenario, and starts the detector-initialisation service. On a fresh OpenSearch volume, the detector initialisation needs the configured normal baseline before all detectors can become available, so the first preparation can take significantly longer than later runs.

If the complete infrastructure is already running and all detectors already have their baseline, use:

```powershell
.\evaluation\scripts\Invoke-Evaluation.ps1 -PreflightOnly
```

## Recommended smoke test

Before the final campaign, execute one CPU run with a short development recovery window:

```powershell
.\evaluation\scripts\Invoke-Evaluation.ps1 -Scenario cpu-spike -Repetitions 1 -RecoveryMinutes 2
```

This smoke test is for validating the harness only. Do not use its result as a final thesis measurement.

## Final baseline campaign

The default `baseline` campaign executes the three main scenarios five times each with a 10-minute clean recovery period:

```powershell
.\evaluation\scripts\Invoke-Evaluation.ps1
```

Equivalent explicit command:

```powershell
.\evaluation\scripts\Invoke-Evaluation.ps1 -Campaign baseline -Scenario all -Repetitions 5 -RecoveryMinutes 10
```

The complete campaign intentionally takes a long time because detector recovery is part of the experimental control.

You can run one scenario only:

```powershell
.\evaluation\scripts\Invoke-Evaluation.ps1 -Scenario memory-leak -Repetitions 5
```

## Results

Every campaign creates a timestamped directory under:

```text
evaluation/results/
```

Example:

```text
evaluation/results/baseline-20260905-101500/
    campaign-metadata.json
    baseline/
        cpu-spike/
            run-01/
                metadata.json
                detection.json
                incident.json
                scores.json
            ...
        memory-leak/
        network-latency/
    summary.json
    summary.csv
    model-comparison.csv
```

The raw incident file contains the incident timeline and durable tasks returned by the backend API. The `SPECIALIST_INVESTIGATION_COMPLETED` timeline event contains the structured ReAct outcome, including the complete evidence list, tool sequence information, tool arguments, observations, diagnosis status, root cause, causal chain, findings, and ReAct step count.

`summary.csv` is the main table-oriented export for Chapter 4. `model-comparison.csv` aggregates the same measurements by model profile; initially it contains the baseline profile and becomes useful when explicit alternative profiles are added to `config/model-profiles.json`.

## Evaluation integrity

Do not edit `ground-truth.json` after inspecting measured results. If a scoring rule must be changed for a methodological reason, document the change and rerun the affected campaign.

Do not delete failed or missed runs. Detector misses, diagnosis timeouts, task failures, and inconsistent trajectories are experimental outcomes and must remain part of the dataset.
