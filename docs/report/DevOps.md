# DevOps

The project uses lightweight DevOps practices to keep the thesis prototype reproducible, testable, and easy to publish. The current workflow combines containerised execution, controlled configuration, layered tests, pull-request checks, automatic releases, and automatic documentation deployment.

## 1. Container strategy

The runtime uses two independent Docker Compose projects.

### Agentic infrastructure

```text
agentic-proactive-monitor-infrastructure
```

This project contains the monitoring and agentic services, including OpenSearch, OpenSearch Dashboards, Qdrant, MongoDB, Open WebUI, Prosody/XMPP, MCP Server, the knowledge-base services, the agentic backend, the operator dashboard, and bootstrap containers.

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

The two projects communicate through the shared observability network:

```text
agentic-monitoring-net
```

The monitored services also use a private application network for their internal request path.

## 2. Native Ollama deployment

Ollama is intentionally outside Docker Compose. It runs directly on Windows so local models can use the NVIDIA GPU independently from the Docker Desktop Linux environment.

Containers access it through:

```text
http://host.docker.internal:11434
```

The model names are configuration values, allowing the reasoning and tool models to be changed without modifying the multi-agent architecture.

## 3. Configuration management

Runtime values are defined through environment variables and `.env.example` files.

A typical local preparation step from PowerShell is:

```powershell
cd .\src\infrastructure
Copy-Item .env.example .env -ErrorAction SilentlyContinue
```

The same principle is used for the monitored system. Local credentials and secrets should be replaced before non-local use.

## 4. Local startup

The normal startup sequence is:

1. start Ollama on Windows and ensure the required models are available;
2. start the agentic infrastructure;
3. start the monitored Notes Platform;
4. verify container health, telemetry, knowledge ingestion, and detector bootstrap.

From PowerShell:

```powershell
cd .\src\infrastructure
docker compose up -d --build

cd ..\monitored_system
docker compose up -d --build
```

The infrastructure creates the shared observability network used by the monitored system.

## 5. Health checks and bootstrap services

Long-running services expose Docker health checks where useful.

One-shot bootstrap containers prepare:

- OpenSearch index templates;
- Qdrant collections;
- knowledge-base ingestion;
- XMPP accounts and certificates;
- OpenSearch Dashboards data views;
- OpenSearch Anomaly Detection detectors.

This makes a clean local deployment reproducible without manual recreation of monitoring objects.

## 6. Reproducible fault experiments

Fault scenarios are stored with the monitored workload rather than inside the agentic backend.

A common reset command is:

```powershell
cd .\src\monitored_system
.\infrastructure\scenarios\reset-to-base.ps1
```

Detector experiments include recovery periods because OpenSearch Anomaly Detection uses an adaptive model. Repeating a fault immediately after a previous anomaly would not be equivalent to testing an independent static threshold.

## 7. Monitored-system test runner

The monitored-system test runner is implemented in PowerShell.

Examples:

```powershell
cd .\src\monitored_system
.\infrastructure\tests\run-tests.ps1 -Test preflight
.\infrastructure\tests\run-tests.ps1 -Test cpu-spike
.\infrastructure\tests\run-tests.ps1 -Test memory-leak
.\infrastructure\tests\run-tests.ps1 -Test network-latency
.\infrastructure\tests\run-tests.ps1 -Test all
```

The suite validates infrastructure conditions, telemetry, scenario behaviour, detector results, recovery, and the invariant that all anomaly detectors are `SINGLE_ENTITY`.

Generated local experiment outputs are excluded from version control where appropriate.

## 8. Agentic backend test strategy

The backend test suite is separated by responsibility:

```text
tests/unit/         isolated project logic
tests/integration/  running infrastructure interactions
tests/e2e/          Gherkin acceptance scenarios
```

### Unit tests

Unit tests cover areas such as:

- AgentSpeak BDI execution;
- incident and task state machines;
- anomaly intake and recovery;
- detector-focus metadata;
- ReAct evidence contracts;
- tool schema and target validation;
- peer collaboration;
- Technical Lead triage and review;
- persistence and reporting logic.

They do not require the Docker stack.

### Integration tests

Integration tests exercise real infrastructure boundaries, including XMPP communication, MCP discovery, MongoDB persistence, FastAPI reads, runtime services, and model routing.

The expensive live model-routing test is opt-in and verifies:

```text
Gemma reasoning -> Qwen tool selection -> SPADE-LLM MCP execution
```

### End-to-end tests

End-to-end tests use Gherkin scenarios through `pytest-bdd` and validate externally observable backend and operator behaviour against the running stack.

## 9. Running backend tests locally

From Windows PowerShell:

```powershell
cd .\src\agentic_system
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"

python -m pytest tests\unit
python -m pytest tests\integration -m integration
python -m pytest tests\e2e -m e2e
```

Integration and e2e suites require the necessary Docker services and Ollama models.

To run the live routing integration test:

```powershell
$env:RUN_LIVE_MODEL_ROUTING = "1"
python -m pytest tests\integration\test_live_model_routing.py -v
```

## 10. GitHub Actions backend unit tests

The repository contains an `Agentic Backend Unit Tests` workflow.

For relevant backend changes it:

```text
checkout
  -> Python 3.12
  -> pip install -e ".[test]"
  -> pytest tests/unit -v
```

Hosted CI runs the isolated unit suite, while Docker-, network-, GPU-, and live-model-dependent checks remain local or explicitly opt-in.

## 11. Git workflow

Development is organised through feature branches and pull requests. `main` is the integration baseline, while dedicated branches are used for focused work such as the agentic backend and documentation.

Documentation is maintained on the `docs` branch and merged into `main` when ready for publication.

## 12. Conventional Commits validation

Pull requests targeting `main` run the `Validate Commits` workflow.

Commit messages follow Conventional Commit-style prefixes such as:

```text
feat: add diagnostic capability
fix: correct incident recovery
docs: update backend architecture
```

This keeps change history easier to read and supports automated release notes.

## 13. Automatic releases

Every push to `main` triggers the release workflow.

The strategy is intentionally simple for the thesis repository:

- if no version tag exists, use the version in `package.json`;
- otherwise increment the patch component of the latest semantic version;
- create and publish a GitHub Release;
- generate release notes automatically.

The workflow uses the repository-provided GitHub token with `contents: write` permission.

## 14. Documentation deployment

The documentation website is built with VitePress.

Changes under `docs/**` on `main` trigger the GitHub Pages workflow:

```text
checkout
  -> setup Node.js
  -> install documentation dependencies
  -> build VitePress
  -> upload Pages artifact
  -> deploy GitHub Pages
```

The build output is:

```text
docs/.vitepress/dist
```

and the configured repository base path is:

```text
/AgenticProactiveMonitor/
```

## 15. CI/CD boundary

The project deliberately separates fast hosted checks from environment-dependent thesis experiments.

GitHub Actions currently covers repository-level checks, backend unit tests, releases, and documentation publishing. Full Docker integration, controlled fault injection, `tc/netem` behaviour, and real local-model evaluation depend on the target Windows/Docker/Ollama environment and are therefore executed locally with repeatable commands.
