# 3 Domain Driven Design to Microservices

> In following subsections we describe how we created the microservices architecture starting
> from the definition of the domain and subdomains described in previous sections.

## 3.1 System Operations

> In this section are shown the mais systems operations identified in the smart gym
> system. Operations are derived use cases and represent the main commands that guides
> the behaviour of the system.

| Actor      | Related Use Case   | Command                                   | Description                                                            |
| ---------- | ------------------ | ----------------------------------------- | ---------------------------------------------------------------------- |
| Gym Member | Access Gym         | `startGymSession(badgeId)`                | Creates a new GymSession when badge is validated at entrance turnstile |
| Gym Member | Exit Gym           | `endGymSession(badgeId)`                  | Closes active GymSession and updates gym count                         |
| Gym Member | Access Area        | `enterArea(badgeId, areaId)`              | Registers AreaEntered event and increments area count                  |
| Gym Member | Exit Area          | `exitArea(badgeId, areaId)`               | Registers AreaExited event and decrements area count                   |
| Gym Member | Use Machine        | `startMachineSession(machineId, badgeId)` | Sets machine status to Occupied and creates MachineSession             |
| Gym Member | Stop Using Machine | `endMachineSession(machineId)`            | Closes MachineSession and sets machine status to Free                  |
| Admin      | Set Maintenance    | `setMachineMaintenance(machineId)`        | Changes machine state to Maintenance                                   |
| Admin      | Login              | `authenticateAdmin(credentials)`          | Validates admin credentials                                            |

<p align="center"><em>Table X: Main System Operation Description</em></p>

| Query                          | Description                                       |
| ------------------------------ | ------------------------------------------------- |
| `getGymCount()`                | Returns total people currently inside the gym     |
| `getAreaStatus(areaId)`        | Returns area occupancy and capacity               |
| `getMachineStatus(machineId)`  | Returns machine state (Free/Occupied/Maintenance) |
| `getActiveSessions()`          | Returns active gym sessions                       |
| `getMachineHistory(machineId)` | Returns machine usage history                     |
| `getAttendanceReport(date)`    | Returns aggregated attendance statistics          |

<p align="center"><em>Table X: Main System Queries Description</em></p>

## 3.2 Subdomains to Microservices

> To correctly apply the Decomposition by Subdomain pattern suggested by Domain-Driven Design, each identified bounded context has been mapped to a dedicated microservice.
> This choice guarantees clear separation of concerns, independent evolution, service autonomy, and database isolation.
> Each microservice owns its domain model, enforces its invariants, and exposes its behavior through well-defined REST APIs or asynchronous events.
> The mapping between subdomains and microservices is described below.

## 3.2.1 Occupancy Tracking Service (Core)

This microservice implements the Core Domain of the SmartGym Monitor system.
It is responsible for managing the lifecycle of gym sessions and maintaining the global consistency of gym occupancy.

| Responsibility           | Description                                                                        |
| ------------------------ | ---------------------------------------------------------------------------------- |
| Gym Session Management   | Creates and terminates GymSession entities when members enter or exit the gym.     |
| Global Gym Count         | Maintains the real-time number of people inside the gym.                           |
| Invariant Enforcement    | Ensures that a badge cannot have more than one active GymSession at the same time. |
| Domain Event Publication | Emits domain events such as GymSessionStarted and GymSessionEnded.                 |

## 3.2.2 Area Management Service (Supporting)

| Responsibility       | Description                                                              |
| -------------------- | ------------------------------------------------------------------------ |
| Area Configuration   | Defines and updates area metadata (name, capacity).                      |
| Occupancy Tracking   | Maintains the current number of people inside each GymArea.              |
| Capacity Enforcement | Guarantees that the constraint 0 ≤ currentCount ≤ capacity always holds. |
| Event Handling       | Processes AreaEntered and AreaExited events.                             |

## 3.2.3 Machine Management Service (Supporting)

| Responsibility              | Description                                                                        |
| --------------------------- | ---------------------------------------------------------------------------------- |
| Machine Configuration       | Manages machine metadata and association with areas.                               |
| Machine Session Lifecycle   | Creates and closes MachineSession entities.                                        |
| State Transition Validation | Ensures valid transitions between Free, Occupied, and Maintenance states.          |
| Consistency Enforcement     | Guarantees that a machine cannot have more than one active session simultaneously. |

## 3.2.4 Embedded Service (Supporting)

| Responsibility             | Description                                                     |
| -------------------------- | --------------------------------------------------------------- |
| Device Integration         | Manages RFID readers, turnstiles, doors, and proximity sensors. |
| Event Translation          | Converts hardware signals into structured backend events.       |
| Asynchronous Communication | Publishes device events through an MQTT/Event Bus mechanism.    |
| Device Monitoring          | Exposes device health and operational status for observability. |

## 3.2.5 Analytics Service (Generic)

| Responsibility              | Description                                                  |
| --------------------------- | ------------------------------------------------------------ |
| Attendance Statistics       | Computes gym attendance trends and peak hours.               |
| Machine Usage Analysis      | Calculates machine utilization rates and dwell time.         |
| Historical Data Aggregation | Processes domain events to generate analytical reports.      |
| Read Model Management       | Maintains optimized read models for dashboard visualization. |

## 3.2.6 Authentication Service (Generic)

| Responsibility            | Description                               |
| ------------------------- | ----------------------------------------- |
| Admin Authentication      | Validates administrator credentials.      |
| Token Management          | Issues and validates JWT tokens.          |
| Role-Based Access Control | Enforces authorization rules.             |
| Service Authentication    | Secures service-to-service communication. |

## 3.3 System Operation Identification

> In this section we identify the main operations exposed by each microservice of the SmartGym Monitor system.
> Operations are derived from the use cases and represent the core commands that drive the behavior of the distributed system.

| Micro-service                  | Operations                                                                                                   | Collaborators                                                                                 |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------- |
| **Occupancy Tracking Service** | startGymSession(), endGymSession(), getGymCount(), getActiveGymSessions()                                    | Embedded Service, Area Management Service, Authentication Service                             |
| **Area Management Service**    | enterArea(), exitArea(), getAreaStatus(), updateAreaCapacity()                                               | Embedded Service, Occupancy Tracking Service, Authentication Service                          |
| **Machine Management Service** | startMachineSession(), endMachineSession(), setMachineMaintenance(), getMachineStatus(), getMachineHistory() | Embedded Service, Area Management Service, Occupancy Tracking Service, Authentication Service |
| **Embedded Service**           | publishBadgeScanned(), publishAreaAccess(), publishMachineUsage(), getDeviceStatus()                         | none (event producer)                                                                         |
| **Analytics Service**          | generateAttendanceReport(), getAttendanceStats(), getMachineUtilization(), getPeakHours()                    | Occupancy Tracking Service, Area Management Service, Machine Management Service               |
| **Authentication Service**     | login(), logout(), validateToken()                                                                           | none                                                                                          |

## 3.4 API Interface Definition and Identification

> In this section, the interfaces of the SmartGym Monitor system are described.
> Since the system integrates embedded devices and backend microservices, two communication layers are defined:
> Asynchronous communication (MQTT/Event Bus) between embedded devices and backend. Synchronous REST APIs between microservices and the API Gateway.
> Each microservice exposes a set of REST endpoints that implement the identified system operations.

## 3.4.1 Embedded Service

The Embedded Service communicates with physical or simulated devices and publishes structured domain events to backend services.

| Endpoint                          | Type | Description                                                                          |
| --------------------------------- | ---- | ------------------------------------------------------------------------------------ |
| `/embedded-service/badge-scanned` | POST | Receives a badge scan event from an RFID reader and forwards it to backend services. |
| `/embedded-service/area-access`   | POST | Receives an area access event (IN/OUT direction).                                    |
| `/embedded-service/machine-usage` | POST | Receives proximity sensor events related to machine usage.                           |
| `/embedded-service/device-status` | GET  | Returns operational status of all connected devices.                                 |

## 3.4.2 Occupancy Tracking Service

This service manages gym sessions and global occupancy consistency.
| Endpoint | Type | Description |
| ------------------------------------ | ---- | ------------------------------------------------------------- |
| `/occupancy-service/start-session` | POST | Creates a new GymSession when a member enters the gym. |
| `/occupancy-service/end-session` | POST | Terminates the active GymSession of a member. |
| `/occupancy-service/count` | GET | Returns the total number of members currently inside the gym. |
| `/occupancy-service/active-sessions` | GET | Returns all currently active gym sessions. |

## 3.4.3 Area Management Service

This service manages area configuration and area-level occupancy.

| Endpoint                        | Type | Description                                                |
| ------------------------------- | ---- | ---------------------------------------------------------- |
| `/area-service/enter`           | POST | Registers entry of a member into a specific area.          |
| `/area-service/exit`            | POST | Registers exit of a member from a specific area.           |
| `/area-service/{areaId}`        | GET  | Returns occupancy status and capacity of a specific area.  |
| `/area-service/update-capacity` | POST | Updates the maximum capacity of an area (admin operation). |

## 3.4.4 Machine Management Service

This service handles machine lifecycle and machine sessions.

| Endpoint                               | Type | Description                                                     |
| -------------------------------------- | ---- | --------------------------------------------------------------- |
| `/machine-service/start-session`       | POST | Starts a MachineSession and sets machine status to Occupied.    |
| `/machine-service/end-session`         | POST | Ends the active MachineSession and sets machine status to Free. |
| `/machine-service/set-maintenance`     | POST | Sets machine status to Maintenance (admin operation).           |
| `/machine-service/{machineId}`         | GET  | Returns the current state of a machine.                         |
| `/machine-service/history/{machineId}` | GET  | Returns historical usage sessions of a machine.                 |

## 3.4.5 Analytics Service

The Analytics Service provides aggregated and historical data for administrative monitoring.

| Endpoint                                 | Type | Description                                        |
| ---------------------------------------- | ---- | -------------------------------------------------- |
| `/analytics-service/attendance/{date}`   | GET  | Returns attendance statistics for a specific date. |
| `/analytics-service/machine-utilization` | GET  | Returns aggregated machine usage statistics.       |
| `/analytics-service/peak-hours`          | GET  | Returns peak attendance periods.                   |
| `/analytics-service/reports`             | GET  | Retrieves generated analytical reports.            |

## 3.4.6 Authentication Service

The Authentication Service manages identity verification and token-based security.

| Endpoint                 | Type | Description                                               |
| ------------------------ | ---- | --------------------------------------------------------- |
| `/auth-service/login`    | POST | Authenticates an administrator and generates a JWT token. |
| `/auth-service/logout`   | POST | Invalidates the active token.                             |
| `/auth-service/validate` | GET  | Validates a token for service-to-service communication.   |
