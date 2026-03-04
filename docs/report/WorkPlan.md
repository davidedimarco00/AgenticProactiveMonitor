# Work Plan

This project follows a structured work plan that combines **Domain-Driven Design (DDD)** principles with an incremental development approach.  
The goal is to progressively move from domain understanding to implementation, while keeping the domain model consistent and evolvable throughout the project.

The work is divided into several phases, each producing clear and verifiable outputs.

## Phase 1 – Domain Analysis

The first phase focuses on understanding the gym domain and identifying its main concepts and rules.  
During this phase, the problem domain is analyzed independently from technical implementation details.

The main activities of this phase include:
- identification of the core domain and relevant subdomains,
- definition of the ubiquitous language shared by domain experts and developers,
- identification of the main domain events related to access control, occupancy, and equipment usage.

The output of this phase is a clear domain description and a shared vocabulary that will guide all subsequent design and implementation decisions.

## Phase 2 – Bounded Contexts and Context Map

In the second phase, the domain is divided into **bounded contexts**, each representing a coherent part of the system with a well-defined responsibility and terminology.

The activities of this phase include:
- definition of bounded contexts related to access monitoring, equipment usage, occupancy projection, and analytics,
- identification of relationships between bounded contexts,
- construction of a context map describing how contexts interact and exchange information.

This phase helps reduce complexity and prevents ambiguity in the domain model by clearly separating concerns.

## Phase 3 – Domain Model Design

During this phase, the conceptual domain model is translated into a software-oriented model using DDD building blocks.

The main activities include:
- identification of entities, value objects, and aggregate roots,
- definition of domain invariants and consistency rules,
- modeling of domain events and domain services,
- definition of repositories and domain interfaces.

The result of this phase is a detailed domain model that represents the core business logic of the SmartGym Monitor system.

## Phase 4 – System Architecture and Technology Design

Once the domain model is defined, the overall system architecture is designed.  
This phase focuses on how domain concepts are implemented and how system components interact.

The activities include:
- definition of the backend architecture based on DDD principles,
- design of interfaces between the backend, simulated sensors, and the web dashboard,
- selection of communication mechanisms such as REST APIs and WebSockets,
- definition of data persistence strategies.

The architecture is designed to support modularity, scalability, and future extensions.

## Phase 5 – Implementation and Integration

In this phase, the system is implemented incrementally following the previously defined design.

The main activities include:
- implementation of simulated sensors for access control and equipment usage,
- development of the backend services and domain logic,
- implementation of real-time updates and data storage,
- development of the web dashboard for monitoring and visualization.

Continuous integration tools are used to ensure code quality and system consistency during development.

## Phase 6 – Testing and Validation

The goal of this phase is to verify that the system behaves correctly and satisfies the project objectives.  
Testing activities are organized according to the **Testing Pyramid**, in order to ensure a good balance between reliability, maintainability, and development effort.

At the base of the pyramid, **unit tests** are used to validate the core domain logic.  
These tests focus on entities, value objects, aggregate roots, and domain services, verifying domain invariants such as correct occupancy updates, valid state transitions, and consistency rules.

The middle layer of the pyramid is composed of **integration tests**, which verify the interaction between different system components.  
These tests focus on the integration between the backend, the database, and external inputs such as simulated sensors, ensuring that domain events are correctly processed and persisted.

At the top of the pyramid, a limited number of **end-to-end tests** are used to validate the system from a user perspective.  
These tests verify complete scenarios, such as people entering and exiting gym areas or machines being used, and check that real-time updates are correctly displayed on the dashboard.

By following the Testing Pyramid approach, the project prioritizes fast and reliable tests for domain logic, while still ensuring that the complete system behaves correctly when all components are integrated.
