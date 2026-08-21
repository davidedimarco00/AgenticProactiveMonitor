// Technical Lead BDI policy for incident coordination.
// Stage 1 performs triage and selects the primary investigator.
// Stage 2 reviews the completed specialist investigation and commits the next
// workflow decision. Operational evidence gathering remains delegated to specialists.

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

+!review_investigation_result(I)
    : incident_status(I, under_analysis)
      & specialist_result_received(I)
      & not review_complete(I)
    <- .run_tl_review(I);
       !commit_review_decision(I).

+!commit_review_decision(I)
    : review_complete(I)
      & review_decision(I, Decision)
    <- .commit_tl_review_decision(I, Decision).
