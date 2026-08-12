---
kb_id: linux.cpu.load
version: 1
collection: kb-system-engineer-linux
document_type: technical-reference
roles: [system_engineer]
platform: linux
topics: [cpu, load, scheduling, accounting, iowait]
source_urls:
  - https://www.kernel.org/doc/html/latest/admin-guide/cpu-load.html
  - https://www.kernel.org/doc/html/latest/filesystems/proc.html
---

# Linux CPU Accounting and Load

## CPU accounting

Linux exposes CPU accounting counters through `/proc/stat`. The first `cpu` line aggregates CPU time across processors, while `cpuN` lines expose per-CPU counters.

Common accounting categories include:

- `user`: execution in user space;
- `nice`: user-space execution with adjusted nice priority;
- `system`: execution in kernel space;
- `idle`: idle CPU time;
- `iowait`: accounting related to periods where tasks wait for I/O while a CPU may be idle;
- `irq` and `softirq`: interrupt processing;
- `steal`: time not available to the guest because a hypervisor scheduled another workload, when applicable.

These are accumulated counters. Tools calculate percentages by comparing values over a time interval.

## CPU percentage

A CPU percentage is meaningful only together with its scope and sampling interval.

On a multi-core system, tools and container metrics can express CPU usage in different ways. Some views normalize to the whole host, while others can report usage relative to one CPU so that a multi-threaded process or container can exceed 100 percent.

The metric definition must therefore be checked before comparing values from different sources.

## Load average

`/proc/loadavg` exposes load averages over approximately 1, 5 and 15 minutes, plus runnable/total task counts and the most recently created PID.

Load average is not the same as CPU utilization. It reflects scheduler/load conditions and can include tasks waiting in states that contribute to Linux load accounting.

A high load value should therefore be interpreted as a workload/scheduling signal rather than as a direct CPU percentage.

## Runnable and blocked work

Useful system context includes:

- runnable tasks that are ready for CPU time;
- blocked tasks waiting for resources or events;
- context switches;
- per-CPU accounting categories;
- the relationship between application throughput and available CPU time.

## Sampling matters

CPU counters and utilization percentages describe activity over a time window. A single instantaneous sample may not represent a sustained condition.

When comparing two observations, use the same metric, scope and sampling method where possible.

## Container context

Linux containers use the host kernel. CPU accounting for a container is commonly associated with the container's cgroup rather than with a separate guest kernel.

Host-level CPU counters and container-level CPU counters therefore answer different questions and should not be treated as interchangeable measurements.
