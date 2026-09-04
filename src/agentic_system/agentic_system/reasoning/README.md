# Reasoning package

The specialist diagnostic path has one canonical project-level executor:

```text
AgentSpeak intention
        |
        v
specialist_react.SpecialistReActExecutor
        |
        +-- incident anchor
        +-- initial static RAG grounding
        +-- Gemma reasoning (WHAT evidence is needed)
        +-- structured EvidenceRequest contract
        +-- Qwen action selection (HOW to collect it)
        +-- semantic tool-family/target validation
        +-- MCP/RAG observation
        +-- bounded causal finalization
        |
        v
Technical Lead review
```

## Canonical modules

- `specialist_react.py`: project-level specialist behavior and the public ReAct executor.
- `react_policies.py`: the three behavioral prompt contracts (`REASONING_POLICY`, `TOOL_SELECTION_POLICY`, `FINALIZATION_POLICY`).
- `react_contracts.py`: structured evidence requests, diagnostic output and native Ollama context/finalizer contracts.
- `observation_aware_react.py`: lower-level evidence/audit core retained while the refactor is stabilized.
- `langchain_agent.py`: generic Gemma -> Qwen -> tool execution primitives and result types.

## Gemma -> Qwen contract

Production Gemma reasoning no longer hands Qwen only a free-text `evidence_needed`
string. For every `gather_evidence` decision it emits a structured request with:

- evidence family (`kind`);
- primary and optional related component;
- diagnostic purpose;
- time scope;
- causal relation and explicit causal link to the active hypothesis.

The runtime attaches the deterministic incident signal/anchor and validates that
Qwen selects a tool from the requested evidence family and preserves the target
component before MCP execution. A resource anomaly therefore cannot silently
turn into a network check or another component check. Cross-domain evidence is
still possible, but Gemma must explicitly represent it as a cross-domain causal
hypothesis rather than drifting there implicitly.

This is a semantic guard, not a scenario workflow: there is no encoded sequence
such as `CPU -> get_metrics -> get_processes`. Gemma still decides which evidence
family is useful next and Qwen still decides which compatible bound tool gathers
it.

## Compatibility modules

`structured_reasoning_react.py`, `prompt_engineered_react.py`,
`prompt_engineered_collaboration.py`, `context_robust_react.py`, and
`incident_focus_react.py` are temporary import shims. They no longer add runtime
inheritance layers and are not part of the canonical executor MRO. They can be
removed after downstream imports and regression tests no longer depend on their
historical paths.

## Design rule

Python owns deterministic facts and invariants (detector semantics, schemas,
tool bounds, context size, execution budget, traceability and semantic action
compatibility). The LLM owns causal hypotheses, evidence needs, interpretation
and diagnostic meaning. No scenario-specific tool sequence is encoded in the
runtime.
