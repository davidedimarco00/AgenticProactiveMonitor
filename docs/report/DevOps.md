# DevOps

The project uses lightweight DevOps practices to keep the thesis prototype reproducible and easy to validate. The current workflow focuses on containerised execution, controlled configuration, repeatable experiments, pull-request validation, automatic releases, and automatic documentation deployment.

## 1. Container strategy

The project uses two independent Docker Compose projects.

### Agentic infrastructure

```text
agentic-proactive-monitor-infrastructure
```

This project contains the monitoring and support services, including OpenSearch, OpenSearch Dashboards, Qdrant, Open WebUI, Prosody/XMPP, MCP Server, the knowledge-base web interface, bootstrap services, and the operator dashboard.

### Monitored workload

```text
monitored-system
```

This project contains only the application under observation:

- `traffic-generator`;
- `api-gateway`;
- `processing-service`;
- `data-service`;
- `worker-service`.

The two projects are connected only through the shared observability network:

```text
agentic-monitoring-net
```

The monitored services also use their own private application network.

## 2. Native Ollama deployment

Ollama is intentionally not part of Docker Compose. It runs directly on Windows so local language models can use the NVIDIA GPU independently from the Docker Desktop backend.

The infrastructure reaches Ollama through:

```text
http://host.docker.internal:11434
```

This separation also allows the monitored network-latency scenario to use the Docker Linux environment required by `tc/netem` without coupling GPU inference to that runtime.

## 3. Configuration management

Runtime values are kept in environment variables and `.env.example` files.

The repository should contain safe defaults and examples only. Real secrets, tokens, and local credentials must not be committed.

Typical local preparation from PowerShell is:

```powershell
cd .\src\infrastructure
Copy-Item .env.example .env
```

The same principle is used for the standalone monitored system.

## 4. Startup order

The expected local startup sequence is:

1. start native Ollama on Windows;
2. start the agentic infrastructure;
3. start the monitored Notes Platform;
4. verify telemetry and detector bootstrap.

From PowerShell:

```powershell
cd .\src\infrastructure
docker compose up -d --build

cd ..\monitored_system
docker compose up -d --build
```

The infrastructure is started first because it creates the shared observability network used by the monitored system.

## 5. Health checks and bootstrap services

Long-running infrastructure containers use Docker health checks where useful.

One-shot bootstrap services prepare:

- OpenSearch index templates;
- Qdrant collection configuration;
- OpenSearch Dashboards data views;
- OpenSearch Anomaly Detection detectors.

This avoids requiring the developer to manually recreate the monitoring environment after a clean deployment.

## 6. Reproducible fault experiments

Controlled failure scenarios are stored with the monitored workload rather than inside the agentic infrastructure.

Scenario control is implemented with PowerShell scripts so experiments can be repeated from the Windows development environment.

A common reset command is:

```powershell
.\infrastructure\scenarios\reset-to-base.ps1
```

Detector experiments include an explicit recovery window because OpenSearch Anomaly Detection uses an adaptive model. Repeating a synthetic fault immediately after recovery is not considered equivalent to testing a static threshold.

## 7. Automated monitored-system tests

The monitored-system test runner is implemented in PowerShell.

Examples:

```powershell
.\infrastructure\tests\run-tests.ps1 -Test preflight
.\infrastructure\tests\run-tests.ps1 -Test cpu-spike
.\infrastructure\tests\run-tests.ps1 -Test memory-leak
.\infrastructure\tests\run-tests.ps1 -Test network-latency
.\infrastructure\tests\run-tests.ps1 -Test all
```

The test suite validates both infrastructure conditions and experimental behaviour. It also checks that all OpenSearch detectors are `SINGLE_ENTITY`.

Generated experimental outputs are kept outside version control so the repository contains source and test definitions rather than local experiment results.

## 8. Git workflow

Development is organised through feature branches and pull requests.

Relevant development branches have been used to isolate work on:

- infrastructure cleanup;
- monitored system;
- MCP Server;
- operator dashboard;
- agentic backend;
- documentation and release automation.

Pull requests provide a clear integration point before changes reach `main`.

## 9. Conventional Commits validation

Pull requests targeting `main` run the `Validate Commits` GitHub Actions workflow.

The workflow installs the Node.js project dependencies and executes Commitlint against all commits introduced by the pull request.

The project therefore uses Conventional Commit-style messages such as:

```text
feat: add diagnostic tool
fix: correct detector configuration
docs: update architecture documentation
```

## 10. Automatic releases

Every push to `main` triggers the `Release` workflow.

The current release strategy is intentionally simple for the thesis repository:

- if no release tag exists, the workflow uses the version stored in `package.json`;
- otherwise, it increments the patch component of the latest `vMAJOR.MINOR.PATCH` tag;
- GitHub creates and publishes the release;
- release notes are generated automatically.

Example sequence:

```text
v1.0.0
v1.0.1
v1.0.2
...
```

The workflow uses the GitHub-provided token and requires repository `contents: write` permission.

## 11. Documentation deployment

The documentation site is built with VitePress.

Changes under `docs/**` on `main` trigger the GitHub Pages workflow:

```text
checkout
  -> setup Node.js 24
  -> install docs dependencies
  -> build VitePress
  -> upload Pages artifact
  -> deploy GitHub Pages
```

The VitePress build output is:

```text
docs/.vitepress/dist
```

The project site is configured for the repository base path:

```text
/AgenticProactiveMonitor/
```

## 12. Current CI/CD scope

The current GitHub automation focuses on repository quality and publishing. Full integration tests of the Docker-based monitoring environment are not yet executed by hosted GitHub Actions because the thesis experiments depend on a local Docker Desktop environment and, for some scenarios, specific network and GPU behaviour.

For this reason, the current approach is:

- GitHub Actions for commit validation, releases, and documentation publishing;
- local PowerShell tests for monitored-system and anomaly-detection experiments;
- pytest for the MCP Server tool layer.

This separation keeps automated repository checks fast while preserving realistic experimental validation on the target development environment.
