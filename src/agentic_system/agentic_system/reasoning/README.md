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
        +-- Qwen action selection (HOW to collect it)
        +-- MCP/RAG observation
        +-- bounded causal finalization
        |
        v
Technical Lead review
```

## Canonical modules

- `specialist_react.py`: project-level specialist behavior and the public ReAct executor.
- `react_policies.py`: the three behavioral prompt contracts (`REASONING_POLICY`, `TOOL_SELECTION_POLICY`, `FINALIZATION_POLICY`).
- `react_contracts.py`: structured diagnostic output and native Ollama context/finalizer contracts.
- `observation_aware_react.py`: lower-level evidence/audit core retained while the refactor is stabilized.
- `langchain_agent.py`: generic Gemma -> Qwen -> tool execution primitives and result types.

## Compatibility modules

`structured_reasoning_react.py`, `prompt_engineered_react.py`,
`prompt_engineered_collaboration.py`, `context_robust_react.py`, and
`incident_focus_react.py` are temporary import shims. They no longer add runtime
inheritance layers and are not part of the canonical executor MRO. They can be
removed after downstream imports and regression tests no longer depend on their
historical paths.

## Design rule

Python owns deterministic facts and invariants (detector semantics, schemas,
tool bounds, context size, execution budget, traceability). The LLM owns causal
hypotheses, evidence needs, interpretation and diagnostic meaning. No scenario-
specific tool sequence is encoded in the runtime.
