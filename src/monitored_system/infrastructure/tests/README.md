# Monitored System Test Suite

This directory contains repeatable PowerShell tests for the monitored Notes Platform.

The suite validates the monitored system only. It does not test SPADE/XMPP agents or the agentic infrastructure.

## What is tested

- normal availability of the five monitored containers;
- Notes Platform health through `api-gateway`;
- presence of metrics and logs for every monitored service;
- presence and runtime state of the 13 OpenSearch Anomaly Detection detectors;
- every detector must be `SINGLE_ENTITY`;
- CPU spike on `processing-service` -> `CPU-processing-service` anomaly;
- memory leak on `worker-service` -> `RAM-worker-service` anomaly;
- real network latency on `api-gateway -> processing-service` -> `NETLAT-api-gateway-processing-service` anomaly;
- controlled application latency on `processing-service` -> observable end-to-end HTTP latency;
- `data-service` outage -> HTTP 503 propagated through the Notes Platform.

`high-latency` and `data-service-down` are currently behavioural fault tests. The current detector set contains CPU, RAM, and network-service-latency detectors, so the suite does not falsely require a dedicated anomaly detector result for those two scenarios.

## Usage

Run the commands from `src/monitored_system`.

### Safe preflight only

```powershell
.\infrastructure\tests\run-tests.ps1 -Test preflight
```

This does not inject any fault.

### Individual detector tests

```powershell
.\infrastructure\tests\run-tests.ps1 -Test cpu-spike
.\infrastructure\tests\run-tests.ps1 -Test memory-leak
.\infrastructure\tests\run-tests.ps1 -Test network-latency
```

Each test:

1. finds the detector dynamically by name;
2. verifies that it is `RUNNING` and `SINGLE_ENTITY`;
3. starts the existing controlled scenario;
4. waits for a real-time anomaly with `anomaly_grade > 0`;
5. checks the corresponding telemetry;
6. restores the base scenario automatically, including on failure.

### Behavioural fault tests

```powershell
.\infrastructure\tests\run-tests.ps1 -Test high-latency
.\infrastructure\tests\run-tests.ps1 -Test data-service-down
```

### Full suite

```powershell
.\infrastructure\tests\run-tests.ps1 -Test all
```

The full suite waits five minutes of normal traffic between detector-oriented fault tests. This is configurable:

```powershell
.\infrastructure\tests\run-tests.ps1 -Test all -RecoveryMinutes 5 -DetectorWaitMinutes 7
```

## Results

Every execution creates a JSON report under:

```text
infrastructure/tests/results/
```

The report records PASS/FAIL results and relevant values such as anomaly grade, confidence, maximum CPU/RAM usage, and maximum network response time.

The `results` directory is intentionally ignored by Git so experimental outputs do not become part of the source repository.
