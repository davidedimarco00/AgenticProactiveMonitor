from __future__ import annotations

from collections import Counter
from typing import Any

from ..opensearch.repositories import LogsRepository, MetricsRepository
from ..topology import TopologyRegistry
from .models import CriticReview, Diagnosis, DiagnosticCheck, IncidentContext
from .ollama import OllamaClient
from .prompts import CRITIC_SYSTEM_PROMPT, REASONING_SYSTEM_PROMPT

_ALLOWED_DIAGNOSTIC_ACTIONS = {"query_metrics", "query_logs", "inspect_container"}


class EvidenceService:
    def __init__(
        self,
        metrics: MetricsRepository,
        logs: LogsRepository,
        topology: TopologyRegistry,
        docker_tools: Any | None = None,
    ) -> None:
        self.metrics = metrics
        self.logs = logs
        self.topology = topology
        self.docker_tools = docker_tools

    async def collect(
        self,
        incident: IncidentContext,
        checks: list[DiagnosticCheck] | None = None,
    ) -> dict[str, Any]:
        hosts = self.topology.investigation_scope(incident.host_id)
        report: dict[str, Any] = {
            "round": incident.round,
            "hosts": hosts,
            "metrics": {},
            "logs": {},
            "checks": [],
        }
        for host in hosts:
            metric_rows = await self.metrics.window(host, incident.metric_name)
            values = [
                row.get(incident.metric_name)
                for row in metric_rows
                if isinstance(row.get(incident.metric_name), (int, float))
            ]
            report["metrics"][host] = {
                "samples": len(values),
                "min": min(values) if values else None,
                "max": max(values) if values else None,
                "avg": sum(values) / len(values) if values else None,
                "last": values[-1] if values else None,
                "first_timestamp": metric_rows[0].get("@timestamp") if metric_rows else None,
                "last_timestamp": metric_rows[-1].get("@timestamp") if metric_rows else None,
            }
            log_rows = await self.logs.window(host)
            text = " ".join(str(row).lower() for row in log_rows)
            markers = Counter(
                {
                    word: text.count(word)
                    for word in ("error", "timeout", "retry", "failed", "refused")
                }
            )
            report["logs"][host] = {
                "entries": len(log_rows),
                "markers": dict(markers),
                "samples": log_rows[-10:],
            }
        for check in checks or []:
            report["checks"].append(await self._run_check(check))
        return report

    async def _run_check(self, check: DiagnosticCheck) -> dict[str, Any]:
        if check.action == "query_metrics":
            rows = await self.metrics.window(
                check.target,
                str(check.parameters.get("metric", "cpu.usage_active")),
            )
            return {"check": check.model_dump(), "success": True, "rows": rows[-50:]}
        if check.action == "query_logs":
            rows = await self.logs.window(check.target)
            return {"check": check.model_dump(), "success": True, "rows": rows[-50:]}
        if self.docker_tools and check.action == "inspect_container":
            try:
                output = await self.docker_tools.inspect_container(
                    check.target,
                    check.parameters,
                )
                return {"check": check.model_dump(), "success": True, "output": output}
            except Exception as exc:
                return {
                    "check": check.model_dump(),
                    "success": False,
                    "error": str(exc),
                }
        return {
            "check": check.model_dump(),
            "success": False,
            "error": "unsupported safe check",
        }


class ReasoningService:
    def __init__(self, ollama: OllamaClient) -> None:
        self.ollama = ollama

    async def diagnose(self, incident: IncidentContext) -> Diagnosis:
        diagnosis = await self.ollama.structured(
            response_model=Diagnosis,
            system_prompt=REASONING_SYSTEM_PROMPT,
            payload={
                "incident": {
                    "incident_id": incident.incident_id,
                    "host_id": incident.host_id,
                    "metric_name": incident.metric_name,
                    "anomaly_score": incident.anomaly_score,
                    "started_at": incident.started_at.isoformat(),
                    "investigation_round": incident.round,
                },
                "evidence_history": incident.evidence,
            },
        )
        return _normalise_diagnosis(diagnosis, incident)


class CriticService:
    def __init__(self, ollama: OllamaClient) -> None:
        self.ollama = ollama

    async def review(self, incident: IncidentContext) -> CriticReview:
        if not incident.diagnosis:
            return CriticReview(
                accepted=False,
                confidence=1.0,
                reason="No diagnosis was supplied to the Critic Agent.",
            )
        review = await self.ollama.structured(
            response_model=CriticReview,
            system_prompt=CRITIC_SYSTEM_PROMPT,
            payload={
                "incident": {
                    "incident_id": incident.incident_id,
                    "host_id": incident.host_id,
                    "metric_name": incident.metric_name,
                    "anomaly_score": incident.anomaly_score,
                    "investigation_round": incident.round,
                },
                "evidence_history": incident.evidence,
                "proposed_diagnosis": incident.diagnosis.model_dump(mode="json"),
            },
        )
        review.required_checks = _safe_checks(review.required_checks, incident)
        if review.accepted and review.confidence < 0.65:
            review.accepted = False
            review.reason = (
                "The Critic Agent marked the diagnosis as accepted but supplied insufficient confidence. "
                + review.reason
            )
        return review


class RemediationService:
    allowed_targets = {"processing-service"}
    allowed_actions = {"stop_container"}

    async def execute(self, incident: IncidentContext) -> dict[str, Any]:
        target = (
            incident.diagnosis.preferred_hypothesis.component
            if incident.diagnosis
            else incident.host_id
        )
        action = "stop_container"
        allowed = target in self.allowed_targets and action in self.allowed_actions
        return {
            "action": action,
            "target": target,
            "allowed": allowed,
            "executed": False,
            "message": (
                "Execution intentionally disabled in simplified baseline"
                if allowed
                else "Blocked by policy"
            ),
        }


def _normalise_diagnosis(diagnosis: Diagnosis, incident: IncidentContext) -> Diagnosis:
    allowed_hosts = _allowed_hosts(incident)
    for hypothesis in diagnosis.hypotheses:
        if hypothesis.component not in allowed_hosts:
            hypothesis.component = incident.host_id

    preferred = next(
        (
            item
            for item in diagnosis.hypotheses
            if item.hypothesis_id == diagnosis.preferred_hypothesis.hypothesis_id
        ),
        None,
    )
    if preferred is None:
        preferred = max(diagnosis.hypotheses, key=lambda item: item.confidence)
    diagnosis.preferred_hypothesis = preferred
    diagnosis.required_checks = _safe_checks(diagnosis.required_checks, incident)
    return diagnosis


def _safe_checks(
    checks: list[DiagnosticCheck],
    incident: IncidentContext,
) -> list[DiagnosticCheck]:
    allowed_hosts = _allowed_hosts(incident)
    result: list[DiagnosticCheck] = []
    seen: set[tuple[str, str, str]] = set()
    for check in checks:
        if check.action not in _ALLOWED_DIAGNOSTIC_ACTIONS:
            continue
        if check.target not in allowed_hosts:
            continue
        key = (check.action, check.target, str(sorted(check.parameters.items())))
        if key in seen:
            continue
        seen.add(key)
        result.append(check)
    return result[:3]


def _allowed_hosts(incident: IncidentContext) -> set[str]:
    hosts = {incident.host_id}
    for report in incident.evidence:
        hosts.update(str(host) for host in report.get("hosts", []))
    return hosts
