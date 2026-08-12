---
kb_id: linux.memory.pressure
version: 1
collection: kb-system-engineer-linux
document_type: technical-reference
roles: [system_engineer]
platform: linux
topics: [memory, swap, reclaim, pressure, oom, psi]
source_urls:
  - https://www.kernel.org/doc/html/latest/filesystems/proc.html
  - https://docs.kernel.org/accounting/psi.html
---

# Linux Memory and Pressure

## System memory information

Linux exposes system memory information through `/proc/meminfo`.

Important fields include:

- `MemTotal`: usable physical RAM;
- `MemFree`: currently unused RAM;
- `MemAvailable`: estimate of memory available for starting new applications without swapping;
- `Cached`: filesystem page-cache and related cached memory;
- `Buffers`: temporary block-device buffer memory;
- `SwapTotal` and `SwapFree`: configured and free swap space.

`MemFree` alone is not a complete representation of available memory because Linux intentionally uses free memory for caches that may later be reclaimed.

## Process memory

Process memory can be inspected through `/proc/<pid>/status`, `statm`, `smaps` and `smaps_rollup`.

Common concepts include:

- virtual address space;
- resident set size (RSS);
- anonymous memory;
- file-backed mappings;
- shared mappings;
- swapped anonymous memory.

Different tools can calculate process memory differently. The exact field and scope should be considered when comparing values.

## Swap and reclaim

Linux can reclaim cached pages and, when configured, move eligible anonymous memory to swap. Swap activity and memory pressure are related but are not equivalent concepts.

The presence of used swap does not by itself mean that the system is currently under pressure. Current reclaim, allocation and stall behaviour provide additional context.

## Pressure Stall Information

Linux Pressure Stall Information (PSI) exposes the time workloads are stalled because CPU, memory or I/O resources are contended.

System-level PSI is available under:

```text
/proc/pressure/cpu
/proc/pressure/memory
/proc/pressure/io
```

PSI reports recent averages and cumulative stall time. It describes time lost because work could not progress normally due to resource contention.

## Out-of-memory behaviour

When memory allocations cannot be satisfied and reclaim cannot provide enough memory, Linux may invoke an out-of-memory mechanism. OOM events are kernel/runtime events and should be distinguished from ordinary high memory utilization.

Kernel logs and cgroup memory-event counters can provide additional context when an OOM event actually occurs.

## Container memory

Container memory is commonly accounted through Linux cgroups. Container memory usage and host `/proc/meminfo` describe different scopes.

A container can be constrained by a cgroup memory limit even when the host still has available physical memory. Conversely, host memory pressure can affect multiple containers.

Always identify whether a memory observation refers to the host, a process or a cgroup/container.
