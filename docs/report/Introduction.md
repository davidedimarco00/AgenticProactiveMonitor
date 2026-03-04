# Introduction

Modern gyms are complex environments composed of different areas, various types of equipment, and a number of users that changes significantly during the day.  
For this reason, monitoring the occupancy of gym areas and machines is important to ensure safety, improve user experience, and support better management decisions.

The **SmartGym Monitor** project aims to design and implement a smart gym monitoring system that allows administrative staff to observe, in real time, the level of occupancy in different gym areas and the usage of gym equipment.  
The system also supports the analysis of historical data in order to identify peak hours, congestion patterns, and equipment utilization trends.

In addition to building a working monitoring system, an important objective of the project is the **collection and storage of data in a structured and reusable format**.  
Data such as access events, area occupancy changes, and machine usage sessions are stored in a way that makes them suitable for future processing by data analysis tools or **Artificial Intelligence (AI) systems**, for example for prediction, optimization, or anomaly detection.

The project follows the **Domain-Driven Design (DDD)** approach, with the goal of modeling the software system around the main concepts of the gym domain.  
Instead of focusing only on technical aspects, the design starts from the domain language and identifies concepts such as gym areas, access events, occupancy, and machine usage, organizing them into clearly defined **bounded contexts**.

From a system point of view, SmartGym Monitor is composed of:
- simulated sensors that generate events related to people entering and exiting gym areas, as well as the usage of gym machines,
- a backend system that processes domain events, updates the current state of the gym, and stores historical data,
- a web dashboard that displays real-time occupancy information and basic analytics for administrative users.

The system is designed to keep **data generation, domain processing, and data visualization separated**, allowing the same data to be reused by different components.  
This design choice makes it possible to extend the system in the future with advanced analytics or AI-based services without changing the core domain logic.

The project also adopts basic **DevOps practices**, such as containerization and continuous integration, to improve reliability, scalability, and ease of deployment.  
All components are designed to be modular and loosely coupled, supporting future extensions of the system.

This document presents the domain analysis and design of the SmartGym Monitor system, focusing on the domain model, the ubiquitous language, the bounded contexts, and their interactions.