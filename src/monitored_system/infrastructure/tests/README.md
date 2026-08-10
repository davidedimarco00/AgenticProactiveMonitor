# Monitored System Test Suite

This directory contains repeatable PowerShell tests for the monitored Notes Platform.

The suite validates the monitored system only. It does not test SPADE/XMPP agents or the agentic infrastructure.

## What is tested

- normal availability of the five monitored containers;
- Notes Platform health through `api-gateway`;
- presence of metrics and logs for every monitored service;
- absence of leftover controlled fault markers before testing;
- presence and runtime state of the 13 OpenSearch Anomaly Detection detectors;
- every detector must be `SINGLE_ENTITY`;
- CPU spike on `processing-service` -> `CPU-processing-service` anomaly;
- memory growth on `worker-service` -> `RAM-worker-service` anomaly;
- real network latency on `api-gateway -> processing-service` -> `NETLAT-api-gateway-processing-service` anomaly;
- controlled application latency on `processing-service` -> observable end-to-end HTTP latency;
- `data-service` outage -> HTTP 503 propagated through the Notes Platform.

`high-latency` and `data-service-down` are behavioural fault tests. The current detector set contains CPU, RAM, and network-service-latency detectors, so the suite does not require a dedicated anomaly result for those two scenarios.

## Why detector tests use recovery windows

OpenSearch Anomaly Detection uses an adaptive Random Cut Forest model over an incoming time series. Repeating the same synthetic fault immediately after recovery is therefore not equivalent to testing a static threshold. The detector has already observed the previous pattern and recent anomalous/recovery buckets can still influence its temporal context.

For this reason, each detector-oriented test now:

1. verifies that the detector is `RUNNING` and `SINGLE_ENTITY`;
2. restores the monitored system to the normal base scenario;
3. waits for a configurable clean recovery period before injecting the fault;
4. records the average baseline metric over the last five minutes;
5. injects a clearly separated controlled fault;
6. waits for an OpenSearch result with `anomaly_grade > 0`;
7. independently verifies that the expected metric change actually occurred;
8. records both successful detections and detector misses;
9. restores the base scenario in a `finally` block.

The default clean recovery period is 10 minutes. It can be shortened during development, but the default should be preferred for final experimental runs.

## Default detector experiment profile

The generic scenario scripts remain independently configurable. The test suite uses a stronger profile by default so that a detector experiment is clearly separated from normal traffic:

- CPU: 6 workers on `processing-service`;
- RAM: progressive allocation up to 1024 MB, 64 MB every 10 seconds on `worker-service`;
- network latency: 400 ms delay with 50 ms jitter from `api-gateway` to `processing-service`;
- detector result timeout: 10 minutes;
- clean recovery before each detector test: 10 minutes.

These values can be overridden from PowerShell without editing source code.

## Usage

Run commands from `src/monitored_system`.

### Safe preflight only

```powershell
.\infrastructure\tests\run-tests.ps1 -Test preflight
```

The preflight does not inject a fault. It checks containers, Notes health, telemetry, controlled-fault state, and all 13 `SINGLE_ENTITY` detectors.

### Individual detector tests

```powershell
.\infrastructure\tests\run-tests.ps1 -Test cpu-spike
.\infrastructure\tests\run-tests.ps1 -Test memory-leak
.\infrastructure\tests\run-tests.ps1 -Test network-latency
```

For faster development-only runs, the recovery window can be overridden, for example:

```powershell
.\infrastructure\tests\run-tests.ps1 -Test memory-leak -RecoveryMinutes 5
```

For a custom RAM experiment:

```powershell
.\infrastructure\tests\run-tests.ps1 -Test memory-leak -MemoryTotalMB 1280 -MemoryStepMB 64 -MemoryStepSeconds 10
```

For a custom network experiment:

```powershell
.\infrastructure\tests\run-tests.ps1 -Test network-latency -NetworkDelayMs 500 -NetworkJitterMs 75
```

### Behavioural fault tests

```powershell
.\infrastructure\tests\run-tests.ps1 -Test high-latency
.\infrastructure\tests\run-tests.ps1 -Test data-service-down
```

The application latency is configurable:

```powershell
.\infrastructure\tests\run-tests.ps1 -Test high-latency -ApplicationDelayMs 2000
```

### Full suite

```powershell
.\infrastructure\tests\run-tests.ps1 -Test all
```

The three detector-based tests each perform their own recovery period, so a full experimental suite intentionally takes longer than a development smoke test.

## Results

Every execution creates a JSON report under:

```text
infrastructure/tests/results/
```

Detector experiments are also appended to:

```text
infrastructure/tests/results/detector-confidence-history.csv
```

The experiment history contains:

- scenario and fault parameters;
- whether an anomaly was detected (`detected`);
- detector ID, type, and enable time;
- detector running time at fault injection and at anomaly detection;
- anomaly grade, confidence, and anomaly score;
- detection latency;
- baseline metric value;
- maximum metric value observed during the fault;
- failure reason for detector misses or insufficient fault telemetry.

Recording misses is intentional: keeping only successful anomalies would bias later analysis of detector confidence and detection reliability.

This dataset can later be used to study relationships such as detector running time vs. confidence and to compare detection behaviour across repeated controlled fault injections.

The `results` directory is intentionally ignored by Git so experimental outputs do not become part of the source repository.
