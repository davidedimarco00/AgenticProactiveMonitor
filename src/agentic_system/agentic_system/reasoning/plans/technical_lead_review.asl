// Technical Lead BDI policy for the post-investigation critic cycle.
// The specialist result is a belief. The Technical Lead reviews the evidence
// and commits the next workflow decision without gathering new evidence here.

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
