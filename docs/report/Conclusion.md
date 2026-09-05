# Conclusion

AgenticProactiveMonitor has reached a complete implementation milestone for the thesis prototype. The repository now contains the monitored workload, observability pipeline, online anomaly detection, controlled diagnostic tools, knowledge retrieval, durable incident persistence, operator dashboard, and the autonomous hybrid multi-agent backend.

The distributed Notes Platform provides a controlled environment with real HTTP traffic, service dependencies, SQLite persistence, metrics, structured logs, and reproducible fault scenarios. OpenSearch Anomaly Detection currently uses **16 SINGLE_ENTITY detectors**: five CPU, five RAM, three network transport-latency, and three application-service-latency detectors.

The diagnostic surface is implemented through a read-only MCP Server. Agents can retrieve detector-aligned metrics, query logs, inspect container runtime and process state, perform bounded network and application checks, and retrieve technical knowledge from Qdrant. The MCP layer validates targets and arguments and does not expose a generic shell.

The agentic backend is now a real five-agent SPADE system rather than a future architectural target. The Technical Lead, System Engineer, Network Engineer, Application Engineer, and Software Developer communicate through Prosody/XMPP. AgentSpeak policies provide explicit BDI goals and intentions, while specialists execute bounded ReAct investigations against real MCP tools.

The reasoning path separates model responsibilities. Gemma performs causal and diagnostic reasoning, Qwen selects compatible tools and arguments, and Python enforces deterministic constraints such as detector semantics, schema validation, target binding, execution limits, and traceability. RAG provides static technical grounding without replacing live runtime evidence.

Collaboration is also dynamic. The Technical Lead selects one primary investigator rather than executing a fixed specialist pipeline. When necessary, the primary specialist can autonomously ask one peer specialist for additional cross-domain evidence. The combined result is then returned to the Technical Lead for a final critic review.

MongoDB provides durable workflow state for the anomaly inbox, incidents, agent activity, and investigation tasks. The anomaly intake is recoverable after restart and currently uses a single-active FIFO processing model. OpenSearch remains dedicated to observability and anomaly detection, while Qdrant stores the technical knowledge base.

The operator dashboard completes the human-facing part of the system. It consumes a read-only FastAPI contract and presents waiting anomalies, incidents, diagnosis, evidence summaries, tools used, remediation, validation, agent activity, system health, and downloadable PDF reports. It does not expose private model chain-of-thought and does not allow the operator to manually steer the autonomous investigation through hidden write endpoints.

The main remaining work is therefore **evaluation rather than core backend implementation**. The controlled scenarios can now be used to measure the complete path from anomaly detection to diagnosis and to compare different reasoning/tool-model configurations where useful. The evaluation can focus on detection and investigation time, localization and root-cause accuracy, evidence quality, tool usage, reasoning steps, and the consistency of the final diagnosis.

Future work can extend the current prototype with broader scalability experiments, additional anomaly-detection strategies, stronger coordination policies, and semantic log anomaly detection. These extensions should remain separate from the current implemented scope so the thesis evaluation is based on the system that is actually available in the repository.
