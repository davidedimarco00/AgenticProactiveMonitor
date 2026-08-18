// Technical Lead BDI policy for the first incident-management stage.
// The Technical Lead coordinates; it does not diagnose the incident.

+incident_status(I, taken_in_charge)
    : incident(I)
    <- !manage_incident(I).

+!manage_incident(I)
    : not triage_complete(I)
    <- !triage_incident(I).

+!triage_incident(I)
    <- request_triage_analysis(I).

+!manage_incident(I)
    : triage_complete(I)
      & probable_domain(I, _Domain)
      & triage_recommendation(I, Recommended)
      & agent_available(Recommended)
    <- !select_primary_investigator(I, Recommended).

+!select_primary_investigator(I, Agent)
    : agent_available(Agent)
    <- commit_primary_investigator(I, Agent).
