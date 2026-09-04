from __future__ import annotations

import re
from typing import Annotated

import docker
from docker.errors import APIError, NotFound
from mcp.server import MCPServer
from pydantic import Field

from .docker_tools import HostId, MONITORED_HOSTS, _error, _get_monitored_container


PingCount = Annotated[int, Field(ge=1, le=4)]
PingTimeoutSeconds = Annotated[int, Field(ge=1, le=2)]

_PACKET_STATS = re.compile(
    r"(?P<sent>\d+)\s+packets transmitted,\s+"
    r"(?P<received>\d+)\s+(?:packets )?received,.*?"
    r"(?P<loss>[0-9.]+)%\s+packet loss"
)
_RTT_STATS = re.compile(
    r"(?:rtt|round-trip) min/avg/max/(?:mdev|stddev)\s*=\s*"
    r"(?P<minimum>[0-9.]+)/(?P<average>[0-9.]+)/"
    r"(?P<maximum>[0-9.]+)/(?P<spread>[0-9.]+)\s+ms"
)
_RESOLVED_IP = re.compile(r"^PING\s+\S+\s+\((?P<ip>[^)]+)\)", re.MULTILINE)


def _validate_target(target_host: str) -> str:
    if target_host not in MONITORED_HOSTS:
        raise ValueError(
            f"target_host must be one of: {', '.join(sorted(MONITORED_HOSTS))}"
        )
    return target_host


def _parse_ping_output(output: str) -> dict[str, object] | None:
    packet_match = _PACKET_STATS.search(output)
    if packet_match is None:
        return None

    sent = int(packet_match.group("sent"))
    received = int(packet_match.group("received"))
    packet_loss = float(packet_match.group("loss"))

    payload: dict[str, object] = {
        "packets_sent": sent,
        "packets_received": received,
        "packet_loss_percent": packet_loss,
        "reachable": received > 0,
    }

    ip_match = _RESOLVED_IP.search(output)
    if ip_match is not None:
        payload["resolved_ip"] = ip_match.group("ip")

    rtt_match = _RTT_STATS.search(output)
    if rtt_match is not None:
        payload.update(
            {
                "rtt_min_ms": float(rtt_match.group("minimum")),
                "rtt_avg_ms": float(rtt_match.group("average")),
                "rtt_max_ms": float(rtt_match.group("maximum")),
                "rtt_mdev_ms": float(rtt_match.group("spread")),
            }
        )

    return payload


def register_icmp_tools(mcp: MCPServer) -> None:
    @mcp.tool()
    def test_icmp_reachability(
        host_id: HostId,
        target_host: HostId,
        count: PingCount = 3,
        timeout_seconds: PingTimeoutSeconds = 1,
    ) -> dict:
        """Test bounded ICMP reachability between two allow-listed monitored services.

        The agent may choose only monitored service IDs plus a small packet count and timeout.
        MCP executes a fixed ``ping`` command inside the source container; no generic shell or
        arbitrary destination is exposed. A valid ping execution with zero replies is returned as
        ``status=ok`` and ``reachable=false`` because that is monitored-system evidence, not a tool
        failure. ``status=error`` is reserved for failures of the diagnostic mechanism itself.
        """
        _validate_target(target_host)
        deadline_seconds = (count * timeout_seconds) + 1

        try:
            client = docker.from_env()
            container = _get_monitored_container(client, host_id)
            if container.status != "running":
                return _error(
                    host_id,
                    "container is not running",
                    target_host=target_host,
                )

            result = container.exec_run(
                [
                    "ping",
                    "-n",
                    "-q",
                    "-c",
                    str(count),
                    "-W",
                    str(timeout_seconds),
                    "-w",
                    str(deadline_seconds),
                    target_host,
                ],
                stdout=True,
                stderr=True,
                privileged=False,
            )
            output = result.output.decode("utf-8", errors="replace").strip()

            # iputils-ping exit code 0 means replies were received and 1 means the command ran
            # correctly but no reply was received. Both are valid diagnostic observations.
            if result.exit_code not in {0, 1}:
                return _error(
                    host_id,
                    output or "ICMP diagnostic command failed",
                    target_host=target_host,
                    exit_code=result.exit_code,
                )

            parsed = _parse_ping_output(output)
            if parsed is None:
                return _error(
                    host_id,
                    "Unable to parse ICMP diagnostic output",
                    target_host=target_host,
                    exit_code=result.exit_code,
                )

            return {
                "status": "ok",
                "observation_type": "icmp_reachability",
                "host_id": host_id,
                "target_host": target_host,
                "count": count,
                "timeout_seconds": timeout_seconds,
                "exit_code": result.exit_code,
                **parsed,
            }
        except ValueError as exc:
            return _error(host_id, str(exc), target_host=target_host)
        except NotFound:
            return _error(
                host_id,
                "monitored container not found",
                target_host=target_host,
            )
        except APIError as exc:
            return _error(
                host_id,
                f"Docker API error: {exc}",
                target_host=target_host,
            )
