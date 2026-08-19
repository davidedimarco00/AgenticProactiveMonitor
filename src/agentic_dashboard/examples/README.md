# Dashboard demo incident

`demo-incident.json` is the existing mock incident used to validate the operator read path without waiting for a real OpenSearch anomaly.

The demo is loaded directly through the backend MongoDB repository. It does **not** add a POST/PATCH operation to the operator API and does not start or steer the agents.

Before loading the demo, rebuild and start MongoDB, the backend and the dashboard from Windows PowerShell:

```powershell
cd .\src\infrastructure
docker compose up -d --build mongodb agentic-backend agentic-system-dashboard
```

Then run:

```powershell
..\agentic_dashboard\examples\load-demo-incident.ps1
```

The script is idempotent: running it again replaces `DEMO-CPU-001` and its mock incident events in MongoDB.

After the script completes, verify:

```text
Swagger:        http://127.0.0.1:8082/docs
Incident API:   http://127.0.0.1:8082/api/v1/incidents/DEMO-CPU-001
Dashboard:      http://127.0.0.1:5050/incidents/DEMO-CPU-001
PDF report:     http://127.0.0.1:5050/incidents/DEMO-CPU-001/report.pdf
```

The loader adapts the older mock format to the current backend model. Raw metric values contained in the historical JSON are not persisted in MongoDB; only the anomaly identity/grade/confidence and concise agentic conclusions are kept. OpenSearch remains the source for raw metrics and logs.
