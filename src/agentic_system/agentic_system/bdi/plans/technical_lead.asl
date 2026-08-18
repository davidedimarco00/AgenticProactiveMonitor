// Technical Lead BDI policy for first-stage incident coordination.
// The Technical Lead performs triage and selects the primary investigator.
// It must not diagnose the incident or propose remediation.

+!manage_incident(I)
    : incident_status(I, taken_in_charge)
      & root_cause_unknown(I)
    <- !triage_incident(I);
       !select_primary_investigator(I).

+!triage_incident(I)
    : not triage_complete(I)
    <- .run_tl_triage(I).

+!select_primary_investigator(I)
    : triage_complete(I)
      & probable_domain(I, _Domain)
      & triage_recommendation(I, Agent)
      & agent_available(Agent)
    <- .commit_primary_investigator(I, Agent).
