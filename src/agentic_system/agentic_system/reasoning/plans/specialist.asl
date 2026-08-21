// Common BDI policy for all specialist agents.
// Role-specific expertise lives in prompts, beliefs and ReAct execution.
// An authorized peer context becomes an explicit belief and selects a
// collaborative investigation intention instead of a normal isolated one.

+!handle_investigation_task(T, I)
    : task_state(T, dispatched)
      & assigned_to(T, Role)
      & self_role(Role)
      & root_cause_unknown(I)
      & peer_context_available(T, Peer)
    <- !accept_task(T, I, Role);
       !investigate_with_peer(T, I, Peer).

+!handle_investigation_task(T, I)
    : task_state(T, dispatched)
      & assigned_to(T, Role)
      & self_role(Role)
      & root_cause_unknown(I)
      & not peer_context_available(T, _Peer)
    <- !accept_task(T, I, Role);
       !investigate_incident(T, I).

+!accept_task(T, I, Role)
    : not task_accepted(T)
    <- .accept_specialist_task(T, I, Role).

+!investigate_with_peer(T, I, Peer)
    : task_accepted(T)
      & peer_context_available(T, Peer)
    <- .commit_collaborative_investigation(T, I, Peer).

+!investigate_incident(T, I)
    : task_accepted(T)
    <- .commit_specialist_investigation(T, I).
