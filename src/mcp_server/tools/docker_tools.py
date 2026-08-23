from datetime import datetime, timezone
from typing import Annotated, Literal

import docker
from docker.errors import APIError, NotFound
from mcp.server import MCPServer
from pydantic import Field


HostId = Literal[
    "traffic-generator",
    "api-gateway",
    "processing-service",
    "data-service",
    "worker-service",
]
ProcessLimit = Annotated[int, Field(ge=1, le=50)]
ConnectionLimit = Annotated[int, Field(ge=1, le=100)]

MONITORED_HOSTS = {
    "traffic-generator",
    "api-gateway",
    "processing-service",
    "data-service",
    "worker-service",
}


def _get_monitored_container(client, host_id: str):
    """Return only containers belonging to the standalone monitored system."""
    if host_id not in MONITORED_HOSTS:
        raise ValueError(
            f"host_id must be one of: {', '.join(sorted(MONITORED_HOSTS))}"
        )

    container = client.containers.get(host_id)
    container.reload()
    return container


def _error(host_id: str, message: str, **extra) -> dict:
    return {
        "status": "error",
        "host_id": host_id,
        "error": message,
        **extra,
    }


def register_docker_tools(mcp: MCPServer) -> None:
    @mcp.tool()
    def get_processes(host_id: HostId, limit: ProcessLimit = 10) -> dict:
        """
        Retrieve processes currently running on a monitored service.

        Processes are ordered by CPU usage. The tool is read-only and only
        executes a fixed diagnostic command inside an allow-listed container.
        """
        if limit < 1 or limit > 50:
            raise ValueError("limit must be between 1 and 50")

        try:
            client = docker.from_env()
            container = _get_monitored_container(client, host_id)

            if container.status != "running":
                return _error(host_id, "container is not running")

            result = container.exec_run(
                [
                    "ps",
                    "-eo",
                    "pid=,ppid=,comm=,%cpu=,%mem=,stat=,etime=",
                    "--sort=-%cpu",
                ],
                stdout=True,
                stderr=True,
                privileged=False,
            )

            if result.exit_code != 0:
                return _error(
                    host_id,
                    result.output.decode("utf-8", errors="replace"),
                    exit_code=result.exit_code,
                )

            processes = []
            output = result.output.decode("utf-8", errors="replace")

            for line in output.splitlines():
                parts = line.strip().split(maxsplit=6)
                if len(parts) != 7:
                    continue

                pid, ppid, command, cpu, memory, state, elapsed = parts
                if command == "ps":
                    continue

                try:
                    processes.append(
                        {
                            "pid": int(pid),
                            "ppid": int(ppid),
                            "command": command,
                            "cpu_percent": float(cpu),
                            "memory_percent": float(memory),
                            "state": state,
                            "elapsed": elapsed,
                        }
                    )
                except ValueError:
                    continue

                if len(processes) >= limit:
                    break

            return {
                "status": "ok",
                "host_id": host_id,
                "container_status": container.status,
                "returned_processes": len(processes),
                "processes": processes,
            }

        except ValueError as exc:
            return _error(host_id, str(exc))
        except NotFound:
            return _error(host_id, "monitored container not found")
        except APIError as exc:
            return _error(host_id, f"Docker API error: {exc}")

    @mcp.tool()
    def get_runtime_stats(host_id: HostId) -> dict:
        """
        Retrieve current CPU, memory, PID and uptime information for a
        monitored service container. The tool is read-only.
        """
        try:
            client = docker.from_env()
            container = _get_monitored_container(client, host_id)

            if container.status != "running":
                return _error(host_id, "container is not running")

            stats = container.stats(stream=False)

            cpu_stats = stats.get("cpu_stats", {})
            precpu_stats = stats.get("precpu_stats", {})
            cpu_usage = cpu_stats.get("cpu_usage", {})
            precpu_usage = precpu_stats.get("cpu_usage", {})

            cpu_delta = cpu_usage.get("total_usage", 0) - precpu_usage.get(
                "total_usage", 0
            )
            system_delta = cpu_stats.get("system_cpu_usage", 0) - precpu_stats.get(
                "system_cpu_usage", 0
            )

            online_cpus = cpu_stats.get("online_cpus")
            if not online_cpus:
                online_cpus = len(cpu_usage.get("percpu_usage", [])) or 1

            cpu_percent = 0.0
            if system_delta > 0 and cpu_delta >= 0:
                cpu_percent = cpu_delta / system_delta * online_cpus * 100.0

            memory_stats = stats.get("memory_stats", {})
            memory_usage = memory_stats.get("usage", 0)
            memory_limit = memory_stats.get("limit", 0)
            memory_details = memory_stats.get("stats", {})
            cache = memory_details.get(
                "inactive_file",
                memory_details.get("total_inactive_file", 0),
            )
            memory_used = max(memory_usage - cache, 0)
            memory_percent = (
                memory_used / memory_limit * 100.0 if memory_limit > 0 else 0.0
            )

            pids_current = stats.get("pids_stats", {}).get("current", 0)

            started_at = container.attrs.get("State", {}).get("StartedAt")
            uptime_seconds = None
            if started_at:
                started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                uptime_seconds = (
                    datetime.now(timezone.utc) - started
                ).total_seconds()

            return {
                "status": "ok",
                "host_id": host_id,
                "container_status": container.status,
                "cpu": {
                    "usage_percent": round(cpu_percent, 2),
                    "available_cpus": online_cpus,
                },
                "memory": {
                    "used_bytes": memory_used,
                    "limit_bytes": memory_limit,
                    "usage_percent": round(memory_percent, 2),
                },
                "pids": pids_current,
                "uptime_seconds": (
                    round(uptime_seconds, 2)
                    if uptime_seconds is not None
                    else None
                ),
            }

        except ValueError as exc:
            return _error(host_id, str(exc))
        except NotFound:
            return _error(host_id, "monitored container not found")
        except APIError as exc:
            return _error(host_id, f"Docker API error: {exc}")

    @mcp.tool()
    def get_disk_usage(host_id: HostId) -> dict:
        """
        Retrieve filesystem capacity, inode usage and container writable-layer
        size for a monitored service. The tool is read-only.
        """
        try:
            client = docker.from_env()
            container = _get_monitored_container(client, host_id)

            if container.status != "running":
                return _error(host_id, "container is not running")

            disk_result = container.exec_run(
                ["df", "-P", "-B1", "/"],
                stdout=True,
                stderr=True,
                privileged=False,
            )
            inode_result = container.exec_run(
                ["df", "-P", "-i", "/"],
                stdout=True,
                stderr=True,
                privileged=False,
            )

            if disk_result.exit_code != 0:
                return _error(
                    host_id,
                    disk_result.output.decode("utf-8", errors="replace"),
                    exit_code=disk_result.exit_code,
                )
            if inode_result.exit_code != 0:
                return _error(
                    host_id,
                    inode_result.output.decode("utf-8", errors="replace"),
                    exit_code=inode_result.exit_code,
                )

            disk_lines = [
                line.strip()
                for line in disk_result.output.decode(
                    "utf-8", errors="replace"
                ).splitlines()
                if line.strip()
            ]
            inode_lines = [
                line.strip()
                for line in inode_result.output.decode(
                    "utf-8", errors="replace"
                ).splitlines()
                if line.strip()
            ]

            if len(disk_lines) < 2 or len(inode_lines) < 2:
                raise ValueError("unexpected df output")

            disk_parts = disk_lines[-1].split()
            inode_parts = inode_lines[-1].split()
            if len(disk_parts) < 6 or len(inode_parts) < 6:
                raise ValueError("unable to parse filesystem usage")

            container_size_info = client.api.containers(
                all=True,
                size=True,
                filters={"id": container.id},
            )

            writable_layer_bytes = 0
            rootfs_bytes = 0
            if container_size_info:
                writable_layer_bytes = container_size_info[0].get("SizeRw", 0) or 0
                rootfs_bytes = container_size_info[0].get("SizeRootFs", 0) or 0

            return {
                "status": "ok",
                "host_id": host_id,
                "container_status": container.status,
                "filesystem": {
                    "type": disk_parts[0],
                    "mount_point": disk_parts[5],
                    "total_bytes": int(disk_parts[1]),
                    "used_bytes": int(disk_parts[2]),
                    "available_bytes": int(disk_parts[3]),
                    "usage_percent": float(disk_parts[4].rstrip("%")),
                },
                "inodes": {
                    "total": int(inode_parts[1]),
                    "used": int(inode_parts[2]),
                    "available": int(inode_parts[3]),
                    "usage_percent": float(inode_parts[4].rstrip("%")),
                },
                "container_storage": {
                    "writable_layer_bytes": writable_layer_bytes,
                    "rootfs_bytes": rootfs_bytes,
                },
            }

        except ValueError as exc:
            return _error(host_id, str(exc))
        except NotFound:
            return _error(host_id, "monitored container not found")
        except APIError as exc:
            return _error(host_id, f"Docker API error: {exc}")

    @mcp.tool()
    def get_network_connections(host_id: HostId, limit: ConnectionLimit = 50) -> dict:
        """
        Retrieve current TCP and UDP sockets of a monitored service.

        The tool is read-only and executes only the fixed `ss -H -tuna`
        diagnostic command.
        """
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")

        try:
            client = docker.from_env()
            container = _get_monitored_container(client, host_id)

            if container.status != "running":
                return _error(host_id, "container is not running")

            result = container.exec_run(
                ["ss", "-H", "-tuna"],
                stdout=True,
                stderr=True,
                privileged=False,
            )

            if result.exit_code != 0:
                return _error(
                    host_id,
                    result.output.decode("utf-8", errors="replace"),
                    exit_code=result.exit_code,
                )

            connections = []
            output = result.output.decode("utf-8", errors="replace")

            for line in output.splitlines():
                parts = line.strip().split()
                if len(parts) < 6:
                    continue

                protocol, state = parts[0], parts[1]
                try:
                    recv_queue = int(parts[2])
                except ValueError:
                    recv_queue = None
                try:
                    send_queue = int(parts[3])
                except ValueError:
                    send_queue = None

                connections.append(
                    {
                        "protocol": protocol,
                        "state": state,
                        "recv_queue": recv_queue,
                        "send_queue": send_queue,
                        "local_address": parts[4],
                        "peer_address": parts[5],
                    }
                )

                if len(connections) >= limit:
                    break

            state_counts = {}
            for connection in connections:
                state = connection["state"]
                state_counts[state] = state_counts.get(state, 0) + 1

            return {
                "status": "ok",
                "host_id": host_id,
                "container_status": container.status,
                "returned_connections": len(connections),
                "state_counts": state_counts,
                "connections": connections,
            }

        except ValueError as exc:
            return _error(host_id, str(exc))
        except NotFound:
            return _error(host_id, "monitored container not found")
        except APIError as exc:
            return _error(host_id, f"Docker API error: {exc}")
