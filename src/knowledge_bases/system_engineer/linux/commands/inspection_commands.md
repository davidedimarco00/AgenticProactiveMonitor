---
kb_id: linux.commands.inspection
version: 1
collection: kb-system-engineer-linux
document_type: command-reference
roles: [system_engineer]
platform: linux
topics: [commands, processes, cpu, memory, disk, network, kernel]
source_urls:
  - https://gitlab.com/procps-ng/procps
  - https://git.kernel.org/pub/scm/network/iproute2/iproute2.git/
  - https://www.gnu.org/software/coreutils/manual/coreutils.html
  - https://github.com/util-linux/util-linux
  - https://www.kernel.org/doc/html/latest/filesystems/proc.html
---

# Linux Read-Only Inspection Commands

This document describes common commands used to inspect Linux state. The commands provide observations. Their output must be interpreted together with the system scope and other evidence.

## Process listing

```bash
ps -eo pid,ppid,user,stat,pcpu,pmem,etime,comm,args
```

Useful fields include PID, parent PID, user, process state, CPU percentage, memory percentage, elapsed time, executable name and command arguments.

`ps` is a snapshot. A process can change state immediately after the command returns.

```bash
ps -T -p <PID>
```

Shows threads associated with a selected process where supported by the installed `ps` implementation.

## Interactive or sampled process activity

```bash
top
```

Displays continuously refreshed process and system activity.

For a non-interactive snapshot:

```bash
top -b -n 1
```

`top` percentages depend on system configuration, sampling interval and CPU normalization. They should not automatically be compared with differently scoped container metrics.

## procfs process details

```bash
cat /proc/<PID>/status
cat /proc/<PID>/stat
cat /proc/<PID>/cmdline
ls -l /proc/<PID>/fd
```

These expose process state directly through procfs. Some files require sufficient permissions.

## Memory overview

```bash
free -m
```

Summarizes system memory and swap using kernel memory counters. `available` is generally more useful than `free` alone when estimating memory that can be used without swapping.

```bash
cat /proc/meminfo
```

Provides the underlying detailed system memory counters.

```bash
vmstat 1
```

Produces repeated virtual-memory/system activity samples at approximately one-second intervals. Fields can include runnable/blocked tasks, memory, paging, I/O, interrupts, context switches and CPU time categories.

## CPU and load

```bash
cat /proc/loadavg
cat /proc/stat
cat /proc/uptime
```

These expose Linux load averages, accumulated CPU accounting and uptime information.

```bash
uptime
```

Provides uptime and load-average summary information.

## Filesystem capacity

```bash
df -h
```

Shows filesystem-level capacity and usage in human-readable units.

```bash
df -i
```

Shows inode usage for filesystems that expose inode accounting.

```bash
du -sh <PATH>
```

Reports disk usage reachable from a selected directory tree. It is not the same measurement as filesystem-wide `df` usage.

## Block devices and mounts

```bash
lsblk
findmnt
```

`lsblk` summarizes block-device relationships. `findmnt` displays mounted filesystems and mount relationships.

Read-only kernel views are also available through:

```bash
cat /proc/mounts
cat /proc/self/mountinfo
```

## Network interfaces and routes

```bash
ip addr show
ip link show
ip route show
```

These display interface addresses, link information and routing configuration.

```bash
ip -s link show
```

Adds interface traffic/error counters.

## Socket inspection

```bash
ss -lntp
```

Shows listening TCP sockets and, when permissions allow, associated process information.

```bash
ss -antp
```

Shows TCP sockets including established and other connection states.

```bash
ss -s
```

Shows a summary of socket statistics.

Socket state is transport-level information and does not by itself indicate application-level health.

## Kernel messages

```bash
dmesg
```

Displays messages from the kernel ring buffer when permissions allow it. Output can include device, filesystem, memory, networking and kernel-level events.

A timestamped human-readable view may be available with:

```bash
dmesg -T
```

Timestamp conversion can depend on system clock behaviour and command implementation.

## Pressure Stall Information

```bash
cat /proc/pressure/cpu
cat /proc/pressure/memory
cat /proc/pressure/io
```

When PSI is enabled, these files show recent and cumulative resource-stall information.

## Command safety

The commands in this document are intended for observation and do not intentionally change service or kernel state. Commands that kill processes, change routes, alter filesystems, modify sysctl values or change cgroup limits are outside this reference and should require an explicit action/remediation policy.
