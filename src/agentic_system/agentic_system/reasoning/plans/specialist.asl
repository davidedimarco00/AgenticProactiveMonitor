// Common BDI policy for all specialist agents.
// Role-specific expertise lives in prompts, beliefs and later ReAct execution.
// This plan only accepts a durable task and commits the specialist to investigate it.

+!handle_investigation_task(T, I)
    : task_state(T, dispatched)
      & assigned_to(T, Role)
      & self_role(Role)
      & root_cause_unknown(I)
    <- !accept_task(T, I, Role);
       !investigate_incident(T, I).

+!accept_task(T, I, Role)
    : not task_accepted(T)
    <- .accept_specialist_task(T, I, Role).

+!investigate_incident(T, I)
    : task_accepted(T)
    <- .commit_specialist_investigation(T, I).
