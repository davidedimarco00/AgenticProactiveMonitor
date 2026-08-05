from __future__ import annotations

from collections import Counter
from typing import Any

from ..opensearch.repositories import LogsRepository, MetricsRepository
from ..topology import TopologyRegistry
from .models import CriticReview, Diagnosis, DiagnosticCheck, Hypothesis, IncidentContext


class EvidenceService:
    def __init__(self, metrics: MetricsRepository, logs: LogsRepository, topology: TopologyRegistry, docker_tools: Any | None = None) -> None:
        self.metrics = metrics
        self.logs = logs
        self.topology = topology
        self.docker_tools = docker_tools

    async def collect(self, incident: IncidentContext, checks: list[DiagnosticCheck] | None = None) -> dict[str, Any]:
        hosts = self.topology.investigation_scope(incident.host_id)
        report: dict[str, Any] = {"hosts": hosts, "metrics": {}, "logs": {}, "checks": []}
        for host in hosts:
            metric_rows = await self.metrics.window(host, incident.metric_name)
            values = [row.get(incident.metric_name) for row in metric_rows if isinstance(row.get(incident.metric_name), (int, float))]
            report["metrics"][host] = {
                "samples": len(values),
                "min": min(values) if values else None,
                "max": max(values) if values else None,
                "avg": sum(values) / len(values) if values else None,
                "last": values[-1] if values else None,
            }
            log_rows = await self.logs.window(host)
            text = " ".join(str(row).lower() for row in log_rows)
            markers = Counter({word: text.count(word) for word in ("error", "timeout", "retry", "failed", "refused")})
            report["logs"][host] = {"entries": len(log_rows), "markers": dict(markers), "samples": log_rows[-10:]}
        for check in checks or []:
            report["checks"].append(await self._run_check(check))
        return report

    async def _run_check(self, check: DiagnosticCheck) -> dict[str, Any]:
        if check.action == "query_metrics":
            rows = await self.metrics.window(check.target, str(check.parameters.get("metric", "cpu.usage_active")))
            return {"check": check.model_dump(), "success": True, "rows": rows[-50:]}
        if check.action == "query_logs":
            rows = await self.logs.window(check.target)
            return {"check": check.model_dump(), "success": True, "rows": rows[-50:]}
        if self.docker_tools and check.action == "inspect_container":
            return {"check": check.model_dump(), "success": True, "output": await self.docker_tools.inspect(check.target)}
        return {"check": check.model_dump(), "success": False, "error": "unsupported safe check"}


class ReasoningService:
    """Deterministic baseline. It can later be replaced by Ollama without changing agents."""

    async def diagnose(self, incident: IncidentContext) -> Diagnosis:
        latest = incident.evidence[-1] if incident.evidence else {}
        host_logs = latest.get("logs", {}).get(incident.host_id, {})
        markers = host_logs.get("markers", {})
        hypotheses = [
            Hypothesis(cause="Local resource saturation", component=incident.host_id, confidence=0.55, evidence=["anomaly score"]),
            Hypothesis(cause="Retry storm caused by downstream dependency", component=incident.host_id, confidence=0.78 if markers.get("retry", 0) or markers.get("timeout", 0) else 0.35, evidence=["retry/timeout log markers"]),
        ]
        preferred = max(hypotheses, key=lambda item: item.confidence)
        checks: list[DiagnosticCheck] = []
        if preferred.confidence < 0.85:
            checks.append(DiagnosticCheck(action="query_metrics", target=preferred.component, parameters={"metric": incident.metric_name}))
        return Diagnosis(hypotheses=hypotheses, preferred_hypothesis=preferred, required_checks=checks, explanation="Diagnosis derived from metric summary, log markers and dependency scope.")


class CriticService:
    async def review(self, incident: IncidentContext) -> CriticReview:
        if not incident.diagnosis:
            return CriticReview(accepted=False, reason="Missing diagnosis")
        preferred = incident.diagnosis.preferred_hypothesis
        has_follow_up = any(item.get("checks") for item in incident.evidence)
        accepted = preferred.confidence >= 0.85 or (incident.round >= 2 and has_follow_up)
        return CriticReview(
            accepted=accepted,
            reason="Evidence is sufficient" if accepted else "Additional evidence is required",
            required_checks=[] if accepted else incident.diagnosis.required_checks,
        )


class RemediationService:
    allowed_targets = {"machine-03"}
    allowed_actions = {"stop_container"}

    async def execute(self, incident: IncidentContext) -> dict[str, Any]:
        target = incident.diagnosis.preferred_hypothesis.component if incident.diagnosis else incident.host_id
        action = "stop_container"
        allowed = target in self.allowed_targets and action in self.allowed_actions
        return {
            "action": action,
            "target": target,
            "allowed": allowed,
            "executed": False,
            "message": "Execution intentionally disabled in simplified baseline" if allowed else "Blocked by policy",
        }
