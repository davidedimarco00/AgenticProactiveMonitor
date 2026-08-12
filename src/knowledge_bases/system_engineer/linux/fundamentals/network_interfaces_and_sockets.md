---
kb_id: linux.network.interfaces-sockets
version: 1
collection: kb-system-engineer-linux
document_type: technical-reference
roles: [system_engineer]
platform: linux
topics: [network, interfaces, sockets, tcp, udp, routes, ports]
source_urls:
  - https://www.kernel.org/doc/html/latest/networking/index.html
  - https://www.kernel.org/doc/html/latest/filesystems/proc.html
---

# Linux Network Interfaces and Sockets

## Network interfaces

Linux exposes network interfaces as kernel-managed devices. An interface can have addresses, link state, counters and routing relationships.

Common interface information includes:

- interface name;
- operational/link state;
- MAC address where applicable;
- IPv4 and IPv6 addresses;
- received/transmitted bytes and packets;
- errors and dropped packets.

Interface counters describe traffic observed by that interface. They do not identify which application generated the traffic unless combined with additional process/socket information.

## Routing

The routing table determines which next hop or interface Linux selects for a destination according to configured routes and policy.

A host can have multiple interfaces and routes. Reachability to one destination should therefore be considered together with the route selected for that destination.

## Sockets

Applications communicate through sockets. TCP and UDP sockets can be inspected from user space and through `/proc/net` interfaces.

Useful socket attributes include:

- local address and port;
- remote address and port;
- protocol;
- TCP state;
- owning process when permissions and tools allow it.

## Listening sockets

A TCP service normally needs a socket in a listening state before new incoming connections can be accepted.

A listening socket confirms that the kernel has a local endpoint open for that process. It does not guarantee that the application can complete higher-level requests correctly.

## TCP connection states

TCP connections move through protocol states such as `LISTEN`, `SYN-SENT`, `ESTABLISHED`, `FIN-WAIT`, `TIME-WAIT` and others.

Socket state describes the transport connection state. It should be kept distinct from application-level states such as HTTP success, database health or request processing.

## Ports

A port number identifies a transport endpoint together with protocol and address. The same numeric port can be used by different protocols or network namespaces.

Containerized applications may listen on one port inside a container while Docker publishes a different host-side port.

## Network namespaces

Linux network namespaces can provide separate interfaces, routing tables and socket namespaces. Containers commonly use network namespaces, so the network view from inside a container can differ from the host network view.

## Observation boundaries

Network observations should identify their scope: host namespace, container namespace, interface, source/target pair or socket. A connection failure, packet loss observation or closed port is runtime evidence, but the cause still requires reasoning using the wider system context.
