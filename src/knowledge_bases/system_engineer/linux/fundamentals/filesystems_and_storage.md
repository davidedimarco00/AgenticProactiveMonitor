---
kb_id: linux.filesystems.storage
version: 1
collection: kb-system-engineer-linux
document_type: technical-reference
roles: [system_engineer]
platform: linux
topics: [filesystem, mounts, disk, storage, io, inodes]
source_urls:
  - https://www.kernel.org/doc/html/latest/filesystems/index.html
  - https://www.gnu.org/software/coreutils/manual/html_node/df-invocation.html
  - https://www.gnu.org/software/coreutils/manual/html_node/du-invocation.html
---

# Linux Filesystems and Storage

## Filesystem view

Linux presents files and directories through a single filesystem hierarchy. Separate filesystems and block devices can be attached to that hierarchy at mount points.

A path therefore does not by itself identify the underlying device. Mount information is needed to understand which filesystem backs a directory.

Useful sources include:

```text
/proc/mounts
/proc/self/mountinfo
```

## Space accounting

Filesystem capacity and directory usage describe different concepts.

`df` reports filesystem-level space usage for mounted filesystems. It is based on filesystem accounting rather than recursively adding file sizes.

`du` walks directory trees and reports space associated with files and directories visible from the selected path.

The two views can differ because they answer different questions. Open-but-deleted files, filesystem metadata, reserved blocks, snapshots or other filesystem-specific mechanisms can also affect apparent usage.

## Inodes

Many Linux filesystems use inode-like metadata objects to represent files. A filesystem can have free data blocks while exhausting the metadata capacity required to create additional files.

Tools such as `df -i` expose inode usage where the filesystem supports that concept.

## Block devices

Block devices are commonly visible under `/dev`. Device and partition relationships can be inspected with tools such as `lsblk`.

A mounted filesystem may be backed by:

- a physical disk or partition;
- a logical volume;
- a loop device;
- a network or virtual filesystem;
- container storage layers managed by the runtime.

## I/O activity

Storage performance is not represented by capacity alone. Relevant concepts include:

- read and write throughput;
- I/O operation rate;
- queueing;
- I/O wait and latency;
- device errors;
- filesystem errors or read-only remounts.

Kernel and telemetry counters must be interpreted according to the device and filesystem scope they represent.

## Container filesystems

Containers typically see a filesystem namespace created by the container runtime. Paths inside a container can map to image layers, writable container layers, bind mounts or named volumes.

Persistent application data should be distinguished from the ephemeral writable container layer. A Docker named volume, for example, can survive recreation of the application container while ordinary writable-layer data may not.

## Read-only observation

Useful non-modifying observations include mounted filesystem list, capacity, inode use, directory size, block-device layout and I/O counters. These observations describe current storage state but do not, by themselves, identify the cause of an application problem.
