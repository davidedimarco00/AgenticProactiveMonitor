# Controlled Failure Scenarios

The scenarios in this directory inject reproducible faults into the monitored Notes Platform while normal synthetic user traffic continues to run.

## Available scenarios

### data-service-down

Stops the `data-service` container to simulate complete persistence-layer unavailability.

```powershell
.\infrastructure\scenarios\data-service-down\start.ps1
.\infrastructure\scenarios\data-service-down\stop.ps1
```

### high-latency

Adds a runtime delay to `processing-service` without rebuilding or restarting it.

```powershell
.\infrastructure\scenarios\high-latency\start.ps1
.\infrastructure\scenarios\high-latency\start.ps1 -DelayMs 2500
.\infrastructure\scenarios\high-latency\stop.ps1
```

Each scenario includes a `scenario.yaml` file describing the target, injected root cause, expected symptoms and recovery action. These files provide ground truth for later diagnostic evaluation.
