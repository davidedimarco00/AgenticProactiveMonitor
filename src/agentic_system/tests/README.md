# Agentic backend tests

The backend test suite is split by responsibility:

- `unit/`: isolated project logic, without Docker services.
- `integration/`: real communication between SPADE agents and the running infrastructure (XMPP, SPADE-LLM and MCP capabilities).
- `e2e/`: Gherkin acceptance scenarios executed with `pytest-bdd`.

Install the backend and test dependencies from PowerShell:

```powershell
cd src\agentic_system
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
```

Run the three levels separately:

```powershell
python -m pytest tests\unit
python -m pytest tests\integration -m integration
python -m pytest tests\e2e -m e2e
```

Integration and end-to-end tests require the Docker infrastructure and the configured Ollama models to be running.
