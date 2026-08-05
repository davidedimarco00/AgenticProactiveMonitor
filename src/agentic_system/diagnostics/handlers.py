from __future__ import annotations

import asyncio
from typing import Any

import docker


class DiagnosticHandlers:
    def __init__(self, metrics_repository: Any, logs_repository: Any) -> None:
        self.metrics_repository = metrics_repository
        self.logs_repository = logs_repository
        self.docker = docker.from_env()

    def close(self) -> None:
        self.docker.close()

    def as_mapping(self):
        return {
            "inspect_container": self.inspect_container,
            "get_container_stats": self.get_container_stats,
            "check_health_endpoint": self.check_health_endpoint,
            "query_metrics": self.query_metrics,
            "query_logs": self.query_logs,
        }

    async def inspect_container(self, target: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        def run():
            container = self.docker.containers.get(target)
            return {
                "id": container.id,
                "name": container.name,
                "status": container.status,
                "state": container.attrs.get("State", {}),
                "config": {"image": container.attrs.get("Config", {}).get("Image")},
            }

        return await asyncio.to_thread(run)

    async def get_container_stats(self, target: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        def run():
            stats = self.docker.containers.get(target).stats(stream=False)
            return {
                "cpu_stats": stats.get("cpu_stats", {}),
                "memory_stats": stats.get("memory_stats", {}),
                "pids_stats": stats.get("pids_stats", {}),
            }

        return await asyncio.to_thread(run)

    async def check_health_endpoint(self, target: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self.inspect_container(target, parameters)

    async def query_metrics(self, target: str, parameters: dict[str, Any]) -> dict[str, Any]:
        metric = str(parameters.get("metric", "cpu.usage_active"))
        minutes = int(parameters.get("minutes", 10))
        return {"metric": metric, "samples": await self.metrics_repository.window(target, metric, minutes)}

    async def query_logs(self, target: str, parameters: dict[str, Any]) -> dict[str, Any]:
        minutes = int(parameters.get("minutes", 10))
        return {"logs": await self.logs_repository.window(target, minutes=minutes, limit=300)}
