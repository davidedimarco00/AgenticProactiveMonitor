---
kb_id: linux.processes.procfs
version: 1
collection: kb-system-engineer-linux
document_type: technical-reference
roles: [system_engineer]
platform: linux
topics: [processes, procfs, pid, file-descriptors, process-state]
source_urls:
  - https://www.kernel.org/doc/html/latest/filesystems/proc.html
---

# Linux Processes and procfs

## Process identity

Linux represents a running process with a process identifier, or PID. The `/proc` pseudo-filesystem exposes kernel and process information to user space.

For a process with PID `1234`, process-specific information is available under:

```text
/proc/1234/
```

Useful entries include:

- `cmdline`: command-line arguments;
- `cwd`: symbolic link to the current working directory;
- `environ`: process environment variables;
- `exe`: symbolic link to the executable;
- `fd/`: open file descriptors;
- `maps`: virtual memory mappings;
- `smaps` and `smaps_rollup`: detailed memory mapping/accounting information;
- `stat`: compact process statistics;
- `status`: human-readable process status and memory/accounting fields;
- `wchan`: kernel wait location when available.

## Process state

A process can be running, runnable, sleeping, stopped or in another kernel-defined task state. A process that exists is not necessarily consuming CPU at that moment.

Process state should therefore be treated separately from CPU usage.

## Threads

Linux schedules tasks, and a multi-threaded process can have several threads associated with one process. Process-level CPU usage can therefore represent work performed by multiple threads.

The `Threads` field in `/proc/<pid>/status` reports the current number of threads for the process.

## Open file descriptors

`/proc/<pid>/fd/` contains symbolic links representing file descriptors currently opened by the process. They can point to regular files, sockets, pipes or other kernel objects.

File-descriptor information is useful for understanding what resources a process currently has open. The existence of an open descriptor does not by itself indicate that the resource is healthy or actively being used.

## Process memory views

`/proc/<pid>/status`, `statm`, `smaps` and `smaps_rollup` expose different levels of process memory information.

Important concepts include:

- virtual memory: address space reserved or mapped by the process;
- resident memory: pages currently resident in physical memory;
- shared mappings: pages that may be shared with other processes;
- anonymous mappings: memory not backed by a normal filesystem file;
- swap: anonymous memory that has been moved to swap where applicable.

Different tools may report different memory fields because they use different kernel counters and aggregation methods.

## System-wide procfs information

`/proc` also exposes system information such as:

```text
/proc/cpuinfo
/proc/loadavg
/proc/meminfo
/proc/stat
/proc/uptime
/proc/net/
```

These interfaces provide operating-system context. Container-specific metrics must still be interpreted according to the container/cgroup scope in which they were collected.
