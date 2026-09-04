// Common BDI policy for all specialist agents.
// Role-specific expertise lives in prompts, beliefs and ReAct execution.
// A specialist handles the durable investigation task delegated by the
// Technical Lead. Autonomous peer collaboration is a separate goal: a peer that
// asks for help commits a distinct provide_peer_help intention.

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

// Autonomous peer collaboration: another specialist could not confirm a root
// cause and contacted this agent directly. No Technical Lead authorization is
// involved; the requester folds the returned evidence into one combined result.
+!provide_peer_help(H, I)
    : peer_help_requested(H, I, Peer)
      & self_role(Role)
      & root_cause_unknown(I)
    <- .commit_peer_help_investigation(H, I, Peer).
