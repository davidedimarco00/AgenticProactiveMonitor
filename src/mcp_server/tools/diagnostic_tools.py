from __future__ import annotations

import json
from typing import Annotated, Literal

import docker
from docker.errors import APIError, NotFound
from mcp.server import MCPServer
from pydantic import Field

from .docker_tools import HostId, MONITORED_HOSTS, _error, _get_monitored_container


TargetHostId = Literal[
    "traffic-generator",
    "api-gateway",
    "processing-service",
    "data-service",
    "worker-service",
]

# Only components that actually expose an application service endpoint are
# valid targets for service-level TCP/HTTP checks. These are INTERNAL Docker
# endpoints, not host-published ports. The mapping mirrors the monitored-system
# runtime topology and prevents an LLM from mixing host ports (for example
# api-gateway host port 8080) with container-to-container service ports.
ServiceTargetHostId = Literal[
    "api-gateway",
    "processing-service",
    "data-service",
]

SERVICE_INTERNAL_ENDPOINTS: dict[str, dict[str, object]] = {
    "api-gateway": {"port": 5000, "health_path": "/health"},
    "processing-service": {"port": 8000, "health_path": "/health"},
    "data-service": {"port": 8000, "health_path": "/health"},
}

Pid = Annotated[int, Field(ge=1, le=4_194_304)]
ThreadLimit = Annotated[int, Field(ge=1, le=100)]
ProcessTreeLimit = Annotated[int, Field(ge=1, le=200)]
DiagnosticTimeout = Annotated[float, Field(ge=0.2, le=5.0)]
HttpPath = Annotated[str, Field(min_length=1, max_length=256, pattern=r"^/")]


def _validate_pid(pid: int) -> int:
    if pid < 1 or pid > 4_194_304:
        raise ValueError("pid must be between 1 and 4194304")
    return pid


def _validate_target(target_host: str) -> str:
    if target_host not in MONITORED_HOSTS:
        raise ValueError(
            f"target_host must be one of: {', '.join(sorted(MONITORED_HOSTS))}"
        )
    return target_host


def _service_endpoint(target_host: str) -> dict[str, object]:
    endpoint = SERVICE_INTERNAL_ENDPOINTS.get(target_host)
    if endpoint is None:
        raise ValueError(
            "target_host does not expose a registered monitored application endpoint; "
            f"valid service targets are: {', '.join(sorted(SERVICE_INTERNAL_ENDPOINTS))}"
        )
    return dict(endpoint)


def service_internal_port(target_host: str) -> int:
    """Return the authoritative container-to-container port for a service."""

    return int(_service_endpoint(target_host)["port"])


def _validate_timeout(timeout_seconds: float) -> float:
    if timeout_seconds < 0.2 or timeout_seconds > 5.0:
        raise ValueError("timeout_seconds must be between 0.2 and 5.0")
    return timeout_seconds


def _exec_fixed_python(container, script: str, *arguments: str):
    """Execute one fixed read-only Python diagnostic snippet in a monitored container."""
    last_result = None
    for executable in ("python3", "python"):
        result = container.exec_run(
            [executable, "-c", script, *arguments],
            stdout=True,
            stderr=True,
            privileged=False,
        )
        last_result = result
        if result.exit_code == 0:
            return result
        output = result.output.decode("utf-8", errors="replace").lower()
        if "executable file not found" not in output and "not found" not in output:
            return result
    return last_result


def register_diagnostic_tools(mcp: MCPServer) -> None:
    @mcp.tool()
    def get_process_threads(
        host_id: HostId,
        pid: Pid,
        limit: ThreadLimit = 30,
    ) -> dict:
        """Inspect threads of one process, ordered by CPU usage. Read-only."""
        _validate_pid(pid)
        try:
            client = docker.from_env()
            container = _get_monitored_container(client, host_id)
            if container.status != "running":
                return _error(host_id, "container is not running")

            result = container.exec_run(
                [
                    "ps",
                    "-L",
                    "-p",
                    str(pid),
                    "-o",
                    "pid=,tid=,pcpu=,pmem=,stat=,comm=",
                    "--sort=-pcpu",
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
                    pid=pid,
                )

            threads = []
            for line in result.output.decode("utf-8", errors="replace").splitlines():
                parts = line.strip().split(maxsplit=5)
                if len(parts) != 6:
                    continue
                proc_pid, tid, cpu, memory, state, command = parts
                try:
                    threads.append(
                        {
                            "pid": int(proc_pid),
                            "tid": int(tid),
                            "cpu_percent": float(cpu),
                            "memory_percent": float(memory),
                            "state": state,
                            "command": command,
                        }
                    )
                except ValueError:
                    continue
                if len(threads) >= limit:
                    break

            return {
                "status": "ok",
                "host_id": host_id,
                "pid": pid,
                "returned_threads": len(threads),
                "threads": threads,
            }
        except ValueError as exc:
            return _error(host_id, str(exc), pid=pid)
        except NotFound:
            return _error(host_id, "monitored container not found", pid=pid)
        except APIError as exc:
            return _error(host_id, f"Docker API error: {exc}", pid=pid)

    @mcp.tool()
    def inspect_process(host_id: HostId, pid: Pid) -> dict:
        """Read /proc metadata for one PID: status, command line and I/O counters."""
        _validate_pid(pid)
        try:
            client = docker.from_env()
            container = _get_monitored_container(client, host_id)
            if container.status != "running":
                return _error(host_id, "container is not running")

            script = """
import json, pathlib, sys
pid = int(sys.argv[1])
base = pathlib.Path('/proc') / str(pid)
if not base.exists():
    print(json.dumps({'status':'error','error':'process not found','pid':pid}))
    raise SystemExit(0)
def read(name):
    try:
        return (base / name).read_text(errors='replace')
    except Exception:
        return ''
status = {}
for line in read('status').splitlines():
    if ':' in line:
        key, value = line.split(':', 1)
        if key in {'Name','State','PPid','Threads','VmRSS','VmSize','voluntary_ctxt_switches','nonvoluntary_ctxt_switches'}:
            status[key] = value.strip()
cmdline = read('cmdline').replace('\\x00', ' ').strip()
io = {}
for line in read('io').splitlines():
    if ':' in line:
        key, value = line.split(':', 1)
        if key in {'read_bytes','write_bytes','rchar','wchar','syscr','syscw'}:
            io[key] = value.strip()
print(json.dumps({'status':'ok','pid':pid,'process_status':status,'cmdline':cmdline,'io':io}))
""".strip()
            result = _exec_fixed_python(container, script, str(pid))
            if result is None or result.exit_code != 0:
                output = "python diagnostic runtime unavailable"
                if result is not None:
                    output = result.output.decode("utf-8", errors="replace")
                return _error(host_id, output, pid=pid)
            payload = json.loads(result.output.decode("utf-8", errors="replace"))
            payload["host_id"] = host_id
            return payload
        except (ValueError, json.JSONDecodeError) as exc:
            return _error(host_id, str(exc), pid=pid)
        except NotFound:
            return _error(host_id, "monitored container not found", pid=pid)
        except APIError as exc:
            return _error(host_id, f"Docker API error: {exc}", pid=pid)

    @mcp.tool()
    def get_process_tree(
        host_id: HostId,
        limit: ProcessTreeLimit = 100,
    ) -> dict:
        """Retrieve process parent/child relationships with CPU and memory usage. Read-only."""
        try:
            client = docker.from_env()
            container = _get_monitored_container(client, host_id)
            if container.status != "running":
                return _error(host_id, "container is not running")
            result = container.exec_run(
                ["ps", "-eo", "pid=,ppid=,pcpu=,pmem=,stat=,comm=", "--sort=pid"],
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
            for line in result.output.decode("utf-8", errors="replace").splitlines():
                parts = line.strip().split(maxsplit=5)
                if len(parts) != 6:
                    continue
                pid, ppid, cpu, memory, state, command = parts
                if command == "ps":
                    continue
                try:
                    processes.append(
                        {
                            "pid": int(pid),
                            "ppid": int(ppid),
                            "cpu_percent": float(cpu),
                            "memory_percent": float(memory),
                            "state": state,
                            "command": command,
                        }
                    )
                except ValueError:
                    continue
                if len(processes) >= limit:
                    break
            return {
                "status": "ok",
                "host_id": host_id,
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
    def resolve_service_dns(host_id: HostId, target_host: TargetHostId) -> dict:
        """Resolve one allow-listed monitored service from another service container."""
        _validate_target(target_host)
        try:
            client = docker.from_env()
            container = _get_monitored_container(client, host_id)
            if container.status != "running":
                return _error(host_id, "container is not running", target_host=target_host)
            script = """
import json, socket, sys
name = sys.argv[1]
try:
    infos = socket.getaddrinfo(name, None)
    addresses = sorted({item[4][0] for item in infos})
    print(json.dumps({'status':'ok','target_host':name,'addresses':addresses}))
except Exception as exc:
    print(json.dumps({'status':'error','target_host':name,'error':str(exc)}))
""".strip()
            result = _exec_fixed_python(container, script, target_host)
            if result is None or result.exit_code != 0:
                output = "python diagnostic runtime unavailable"
                if result is not None:
                    output = result.output.decode("utf-8", errors="replace")
                return _error(host_id, output, target_host=target_host)
            payload = json.loads(result.output.decode("utf-8", errors="replace"))
            payload["host_id"] = host_id
            return payload
        except (ValueError, json.JSONDecodeError) as exc:
            return _error(host_id, str(exc), target_host=target_host)
        except NotFound:
            return _error(host_id, "monitored container not found", target_host=target_host)
        except APIError as exc:
            return _error(host_id, f"Docker API error: {exc}", target_host=target_host)

    @mcp.tool()
    def test_tcp_connection(
        host_id: HostId,
        target_host: ServiceTargetHostId,
        timeout_seconds: DiagnosticTimeout = 2.0,
    ) -> dict:
        """Perform a bounded TCP test using the target service's authoritative internal port.

        The caller chooses only source and target services. The MCP server resolves the
        container-to-container port from the monitored-system topology so an agent cannot
        mix host-published ports with internal service ports.
        """
        port = service_internal_port(target_host)
        _validate_timeout(timeout_seconds)
        try:
            client = docker.from_env()
            container = _get_monitored_container(client, host_id)
            if container.status != "running":
                return _error(
                    host_id,
                    "container is not running",
                    target_host=target_host,
                    port=port,
                )
            script = """
import json, socket, sys, time
host, port, timeout = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
start = time.perf_counter()
try:
    with socket.create_connection((host, port), timeout=timeout):
        elapsed = (time.perf_counter() - start) * 1000
        print(json.dumps({'status':'ok','target_host':host,'port':port,'connected':True,'connect_ms':round(elapsed,2)}))
except Exception as exc:
    elapsed = (time.perf_counter() - start) * 1000
    print(json.dumps({'status':'error','target_host':host,'port':port,'connected':False,'connect_ms':round(elapsed,2),'error':str(exc)}))
""".strip()
            result = _exec_fixed_python(
                container,
                script,
                target_host,
                str(port),
                str(timeout_seconds),
            )
            if result is None or result.exit_code != 0:
                output = "python diagnostic runtime unavailable"
                if result is not None:
                    output = result.output.decode("utf-8", errors="replace")
                return _error(
                    host_id,
                    output,
                    target_host=target_host,
                    port=port,
                    endpoint_source="authoritative_monitored_system_topology",
                )
            payload = json.loads(result.output.decode("utf-8", errors="replace"))
            payload["host_id"] = host_id
            payload["endpoint_source"] = "authoritative_monitored_system_topology"
            return payload
        except (ValueError, json.JSONDecodeError) as exc:
            return _error(
                host_id,
                str(exc),
                target_host=target_host,
                port=port,
                endpoint_source="authoritative_monitored_system_topology",
            )
        except NotFound:
            return _error(
                host_id,
                "monitored container not found",
                target_host=target_host,
                port=port,
                endpoint_source="authoritative_monitored_system_topology",
            )
        except APIError as exc:
            return _error(
                host_id,
                f"Docker API error: {exc}",
                target_host=target_host,
                port=port,
                endpoint_source="authoritative_monitored_system_topology",
            )

    @mcp.tool()
    def check_http_endpoint(
        host_id: HostId,
        target_host: ServiceTargetHostId,
        path: HttpPath = "/health",
        timeout_seconds: DiagnosticTimeout = 3.0,
    ) -> dict:
        """Issue a read-only HTTP GET using the target service's authoritative internal port.

        The internal port is resolved by MCP from monitored-system topology. The optional
        path remains explicit because specialists may need a specific read-only endpoint;
        when omitted, the common service health endpoint is used.
        """
        port = service_internal_port(target_host)
        _validate_timeout(timeout_seconds)
        if not path.startswith("/") or len(path) > 256 or "\n" in path or "\r" in path:
            raise ValueError("path must be an absolute HTTP path up to 256 characters")
        try:
            client = docker.from_env()
            container = _get_monitored_container(client, host_id)
            if container.status != "running":
                return _error(
                    host_id,
                    "container is not running",
                    target_host=target_host,
                    port=port,
                )
            script = """
import json, sys, time, urllib.error, urllib.request
host, port, path, timeout = sys.argv[1], int(sys.argv[2]), sys.argv[3], float(sys.argv[4])
url = f'http://{host}:{port}{path}'
start = time.perf_counter()
try:
    request = urllib.request.Request(url, method='GET', headers={'User-Agent':'APM-Diagnostic/1.0'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read(512).decode('utf-8', errors='replace')
        elapsed = (time.perf_counter() - start) * 1000
        print(json.dumps({'status':'ok','url':url,'http_status':response.status,'latency_ms':round(elapsed,2),'body_excerpt':body}))
except urllib.error.HTTPError as exc:
    elapsed = (time.perf_counter() - start) * 1000
    body = exc.read(512).decode('utf-8', errors='replace')
    print(json.dumps({'status':'ok','url':url,'http_status':exc.code,'latency_ms':round(elapsed,2),'body_excerpt':body}))
except Exception as exc:
    elapsed = (time.perf_counter() - start) * 1000
    print(json.dumps({'status':'error','url':url,'latency_ms':round(elapsed,2),'error':str(exc)}))
""".strip()
            result = _exec_fixed_python(
                container,
                script,
                target_host,
                str(port),
                path,
                str(timeout_seconds),
            )
            if result is None or result.exit_code != 0:
                output = "python diagnostic runtime unavailable"
                if result is not None:
                    output = result.output.decode("utf-8", errors="replace")
                return _error(
                    host_id,
                    output,
                    target_host=target_host,
                    port=port,
                    endpoint_source="authoritative_monitored_system_topology",
                )
            payload = json.loads(result.output.decode("utf-8", errors="replace"))
            payload["host_id"] = host_id
            payload["target_host"] = target_host
            payload["port"] = port
            payload["endpoint_source"] = "authoritative_monitored_system_topology"
            return payload
        except (ValueError, json.JSONDecodeError) as exc:
            return _error(
                host_id,
                str(exc),
                target_host=target_host,
                port=port,
                endpoint_source="authoritative_monitored_system_topology",
            )
        except NotFound:
            return _error(
                host_id,
                "monitored container not found",
                target_host=target_host,
                port=port,
                endpoint_source="authoritative_monitored_system_topology",
            )
        except APIError as exc:
            return _error(
                host_id,
                f"Docker API error: {exc}",
                target_host=target_host,
                port=port,
                endpoint_source="authoritative_monitored_system_topology",
            )
