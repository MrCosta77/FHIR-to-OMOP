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

Each person has a stable `actor_id`; names are display attributes and never the
key used to establish independence. A clinical rationale is mandatory for
every review and adjudication. A reviewer cannot submit twice or adjudicate a
case they reviewed.

## Governed professional identities

`governed_actor` records the immutable person identifier and active state;
`governed_actor_role` grants reviewer, adjudicator or identity-administrator
capability; and `governed_actor_alias` contains explicitly approved name
variants. Accents, case and whitespace normalize deterministically. Shortened
names and spelling errors are accepted only after an administrator attaches
them to the correct actor.

Similarity detection is a warning and blocking control, never proof of
identity. A possible match must be resolved as either an approved alias or an
explicitly confirmed distinct person. Reviews, counterproposals and
adjudications store the relevant `actor_id`; separation-of-duty checks use that
identifier across semantically duplicate decisions.

The first identity administrator is established by a one-time audited
bootstrap, available only while no active `source_admin` exists. Thereafter,
actor registration, role grants and aliases require an active administrator
`actor_id`; the bootstrap cannot be repeated.

## Governed candidate correction

A rejected candidate is never edited in place. After two independent reviews
and final `REJECT` adjudication, a reviewer who voted to reject it may submit a
different Athena `concept_id` with a clinical rationale. The system then:

1. validates that the candidate is current, Standard and in the expected OMOP
   domain;
2. creates a new `human_counterproposal` decision linked to the rejected
   decision while preserving both histories;
3. copies the affected-event provenance without publishing an STCM mapping;
4. excludes the proposer from reviewing or adjudicating their own candidate;
5. requires two new independent reviews and a distinct adjudicator before the
   corrected mapping can be published.

Semantically duplicate pending decisions are retained but marked
`SUPERSEDED` when their canonical decision is adjudicated. This prevents a
hidden duplicate from re-entering the queue while maintaining the audit trail.

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

The portal maintains a governed identity registry but does not itself prove who
is operating the browser. Before any PHI pilot, its `actor_id` resolution must
be bound to institution-managed authentication and role claims rather than a
typed name. Until that control and the privacy policy are active, the workflow
is suitable only for synthetic or approved de-identified data.
