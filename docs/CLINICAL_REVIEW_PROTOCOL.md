# Blinded clinical mapping review protocol

Status: technically implemented; clinical execution pending.

## Purpose

This protocol governs LLM-assisted source-to-concept mappings before they can
enter the active STCM set. Technical benchmark labels and synthetic test votes
must never be represented as clinical validation.

## Roles and separation of duties

1. Reviewer A independently inspects the source term, candidate concept,
   domain, retrieval score and affected-event count.
2. Reviewer B performs the same review without seeing Reviewer A's identity,
   vote or rationale.
3. A distinct adjudicator independently makes the final decision after both
   reviews exist. The adjudication screen also hides reviewer identities, votes
   and rationales until the final action is recorded.
4. Only adjudication can publish to `approved_mapping_set` and
   `source_to_concept_map`, activate a rejection policy or change event-level
   provenance status.

Each person must use a stable professional identity. A clinical rationale is
mandatory for every review and adjudication. A reviewer cannot submit twice or
adjudicate a case they reviewed.

## Blinding and audit data

Independent queues expose only the mapping proposal and affected-event count.
Peer votes and rationales remain in `clinical_mapping_review` and are not
returned by the queue API. Final decisions are stored separately in
`clinical_mapping_adjudication`; mapping publication remains transactional.

The portal reports raw pair agreement and Cohen's kappa overall and by OMOP
domain. Kappa is descriptive: no acceptance threshold is claimed until the
clinical team approves a sample-size and interpretation plan.

## Clinical execution requirements

- At least two qualified reviewers and one qualified adjudicator, all distinct.
- Documented reviewer specialty, training and conflict-of-interest policy.
- A prespecified sampling plan covering every supported domain, abstentions,
  low-confidence proposals and common/rare concepts.
- Resolution of disagreements without revealing earlier identities before the
  adjudicator records an independent decision.
- Signed approval of the final reviewed set before changing its curation status
  from `PROVISIONAL_TECHNICAL` to `CLINICALLY_VALIDATED`.

## Current limitation

The portal records a named identity but does not itself authenticate a person.
Before any PHI pilot it must run behind institution-managed authentication and
role-based access control. Until that control and the privacy policy are active,
the workflow is suitable only for synthetic or approved de-identified data.
