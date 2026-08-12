---
kb_id: linux.containers.cgroups-v2
version: 1
collection: kb-system-engineer-linux
document_type: technical-reference
roles: [system_engineer]
platform: linux
topics: [containers, cgroups, cpu, memory, io, pressure]
source_urls:
  - https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html
  - https://docs.kernel.org/accounting/psi.html
---

# Linux cgroup v2 and Container Resource Scope

## cgroup purpose

Linux control groups, or cgroups, organize processes hierarchically and provide resource accounting and control through resource-specific controllers.

Container runtimes use Linux namespaces and cgroups as important building blocks for container isolation and resource management.

A container is therefore not a separate kernel. Processes inside containers execute on the host Linux kernel while operating within configured namespaces and cgroup boundaries.

## cgroup hierarchy

In cgroup v2, processes are organized in a unified hierarchy. Controllers expose files in the cgroup filesystem that represent configuration and runtime accounting.

The exact path of a container's cgroup depends on the container runtime and service manager configuration.

## CPU information

cgroup CPU interfaces provide accounting and control for CPU resources assigned to a cgroup. CPU usage measured for a container is scoped to the tasks contained in that cgroup rather than to all host processes.

CPU quota or CPU-set configuration can constrain the CPU resources available to a workload independently of total host CPU capacity.

## Memory information

The memory controller accounts memory associated with the cgroup. Important cgroup v2 concepts include:

- `memory.current`: memory currently used by the cgroup and descendants;
- `memory.max`: hard memory limit when configured;
- `memory.events`: counters for memory-related events;
- `memory.stat`: detailed memory accounting;
- `memory.pressure`: PSI information scoped to the cgroup when supported.

A container can therefore experience a cgroup memory constraint even if host-wide `/proc/meminfo` still reports available memory.

## I/O information

The I/O controller can account and control block-device I/O for cgroups. Container I/O observations should be associated with the cgroup and underlying device scope represented by the metric.

## Pressure Stall Information

cgroup v2 can expose CPU, memory and I/O pressure information using the same general PSI format as system-wide `/proc/pressure/*` interfaces.

Pressure describes time during which tasks cannot make normal progress because a resource is contended. Pressure and utilization are different measurements.

## Container versus host observations

Host and container metrics answer different questions:

```text
host metric      -> state of the Linux host or host-visible resource
cgroup metric    -> state/accounting of a group of tasks
process metric   -> state/accounting of one process or thread set
```

The scope of an observation must be known before it is used in reasoning.

## Docker context

Docker presents container-level statistics derived from Linux runtime and cgroup information. The exact representation can differ from raw kernel interfaces, but the core distinction remains: container resource metrics are scoped differently from host-wide system metrics.
