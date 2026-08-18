// Technical Lead BDI policy for the first incident-management stage.
// The Technical Lead coordinates; it does not diagnose the incident.

+triage_complete(I)
    : incident(I)
      & incident_status(I, taken_in_charge)
    <- !manage_incident(I).

+!manage_incident(I)
    : probable_domain(I, _Domain)
      & triage_recommendation(I, Recommended)
      & agent_available(Recommended)
    <- !select_primary_investigator(I, Recommended).

+!select_primary_investigator(I, Agent)
    : agent_available(Agent)
    <- commit_primary_investigator(I, Agent).
