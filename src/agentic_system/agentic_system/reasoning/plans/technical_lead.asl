// Technical Lead BDI policy.
// One AgentSpeak policy owns both first-stage coordination and the
// post-investigation critic decision. Diagnostic evidence is still gathered by
// specialists; the Technical Lead triages, coordinates and reviews it.

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

// Post-investigation critic cycle. The specialist result is represented as a
// belief and the Technical Lead commits one explicit workflow decision.
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
