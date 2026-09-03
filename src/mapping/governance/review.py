from __future__ import annotations

import uuid
from collections import Counter, defaultdict

from src.security.privacy import (
    audit_security_event,
    authorize_actor,
    canonical_actor_key,
)

from .core import TARGET_GOVERNANCE
from .identity import resolve_governed_actor
from .schema import ensure_governance_tables


def submit_blinded_review(con, decision_id, action, reviewer, rationale):
    """Record one independent review without exposing or publishing peer votes."""
    action = action.strip().upper()
    if action not in {"APPROVE", "REJECT"}:
        raise ValueError("action must be APPROVE or REJECT")
    reviewer = reviewer.strip()
    if not reviewer:
        raise ValueError("A reviewer name is required.")
    reviewer = authorize_actor(reviewer, "reviewer")
    rationale = (rationale or "").strip()
    if not rationale:
        raise ValueError("A clinical rationale is required.")
    ensure_governance_tables(con)
    actor = resolve_governed_actor(con, reviewer, "reviewer")
    reviewer_actor_id = actor["actor_id"]
    reviewer = actor["display_name"]
    row = con.execute("""
        SELECT status, COALESCE(publication_eligible, TRUE), proposed_by,
               target_table, source_adapter, source_vocabulary_id,
               source_code, source_value, assigned_concept_id,
               proposed_by_actor_id
        FROM mapping_decision
        WHERE mapping_decision_id = ?
    """, [decision_id]).fetchone()
    if not row:
        raise ValueError(f"Unknown mapping decision: {decision_id}")

    (status, publication_eligible, proposed_by, target_table, source_adapter,
     source_vocabulary_id, source_code, source_value, assigned_concept_id,
     proposed_by_actor_id) = row

    if not publication_eligible:
        raise ValueError(
            "Pre-ingestion proposals are not clinically reviewable until bound "
            "to an explicit source vocabulary and ingested OMOP event."
        )
    if status not in {"PENDING", "LOW_CONFIDENCE"}:
        raise ValueError(f"Decision is not independently reviewable: {status}")
    if proposed_by_actor_id and proposed_by_actor_id == reviewer_actor_id:
        raise ValueError("A counterproposal author cannot review their own candidate.")
    existing = con.execute("""
        SELECT COUNT(*) FROM clinical_mapping_review
        WHERE mapping_decision_id = ? AND COALESCE(active, TRUE)
    """, [decision_id]).fetchone()[0]
    if existing >= 2:
        raise ValueError("Two independent reviews already exist; adjudication is required.")
    duplicate = con.execute("""
        SELECT COUNT(*)
        FROM clinical_mapping_review r
        JOIN mapping_decision prior USING (mapping_decision_id)
        WHERE COALESCE(r.active, TRUE) AND r.reviewer_actor_id = ?
          AND prior.target_table = ?
          AND COALESCE(prior.source_adapter, '') = COALESCE(?, '')
          AND COALESCE(prior.source_vocabulary_id, '') = COALESCE(?, '')
          AND COALESCE(prior.source_code, '') = COALESCE(?, '')
          AND LOWER(TRIM(prior.source_value)) = LOWER(TRIM(?))
          AND prior.assigned_concept_id = ?
    """, [
        reviewer_actor_id, target_table, source_adapter, source_vocabulary_id,
        source_code, source_value, int(assigned_concept_id),
    ]).fetchone()[0]
    if duplicate:
        raise ValueError(
            "The same person cannot review a semantic mapping twice, including "
            "duplicate decisions or identity variants."
        )
    con.execute("""
        INSERT INTO clinical_mapping_review (
            review_id, mapping_decision_id, reviewer, reviewer_key,
            reviewer_actor_id, verdict, rationale, active
        ) VALUES (?, ?, ?, ?, ?, ?, ?, TRUE)
    """, [
        str(uuid.uuid4()), decision_id, reviewer, canonical_actor_key(reviewer),
        reviewer_actor_id, action, rationale,
    ])
    audit_security_event(
        con, "CLINICAL_MAPPING_REVIEW", reviewer, "RECORDED",
        {"mapping_decision_id": decision_id, "verdict": action},
    )
    count = existing + 1
    return {
        "mapping_decision_id": decision_id,
        "review_count": count,
        "ready_for_adjudication": count == 2,
    }

def blinded_review_queue(con, reviewer):
    """Return proposals not yet reviewed by this reviewer, without peer votes."""
    reviewer = (reviewer or "").strip()
    if not reviewer:
        raise ValueError("A reviewer name is required.")
    reviewer = authorize_actor(reviewer, "reviewer")
    ensure_governance_tables(con)
    actor = resolve_governed_actor(con, reviewer, "reviewer")
    reviewer_actor_id = actor["actor_id"]
    reviewer = actor["display_name"]
    columns = [
        "mapping_decision_id", "run_id", "target_table", "source_value",
        "normalized_value", "assigned_concept_id", "mapping_method", "score",
        "model_name", "affected_events",
    ]
    rows = con.execute("""
        WITH review_counts AS (
            SELECT mapping_decision_id, COUNT(DISTINCT review_id) AS review_count
            FROM clinical_mapping_review
            WHERE COALESCE(active, TRUE)
            GROUP BY mapping_decision_id
        ), provenance_counts AS (
            SELECT mapping_decision_id,
                   COUNT(DISTINCT target_id) AS affected_events
            FROM mapping_provenance
            GROUP BY mapping_decision_id
        ), ranked AS (
            SELECT d.mapping_decision_id, d.run_id, d.target_table,
                   d.source_value, d.normalized_value, d.assigned_concept_id,
                   d.mapping_method, d.score, d.model_name, d.proposed_at,
                   d.proposed_by, d.proposed_by_actor_id,
                   d.source_adapter, d.source_vocabulary_id,
                   d.source_code,
                   COALESCE(p.affected_events, 0) AS affected_events,
                   COALESCE(r.review_count, 0) AS review_count,
                   ROW_NUMBER() OVER (
                       PARTITION BY d.target_table,
                                    COALESCE(d.source_adapter, ''),
                                    COALESCE(d.source_vocabulary_id, ''),
                                    COALESCE(d.source_code, ''),
                                    LOWER(TRIM(d.source_value)),
                                    d.assigned_concept_id
                       ORDER BY COALESCE(r.review_count, 0) DESC,
                                d.proposed_at, d.mapping_decision_id
                   ) AS canonical_rank
            FROM mapping_decision d
            LEFT JOIN review_counts r USING (mapping_decision_id)
            LEFT JOIN provenance_counts p USING (mapping_decision_id)
            WHERE d.status IN ('PENDING', 'LOW_CONFIDENCE')
              AND COALESCE(d.publication_eligible, TRUE)
        )
        SELECT mapping_decision_id, run_id, target_table, source_value,
               normalized_value, assigned_concept_id, mapping_method, score,
               model_name, affected_events
        FROM ranked d
        WHERE canonical_rank = 1
          AND review_count < 2
          AND (
              proposed_by IS NULL
              OR proposed_by_actor_id <> ?
          )
          AND NOT EXISTS (
              SELECT 1 FROM clinical_mapping_review own
              JOIN mapping_decision peer
                ON peer.mapping_decision_id = own.mapping_decision_id
              WHERE COALESCE(own.active, TRUE)
                AND own.reviewer_actor_id = ?
                AND peer.target_table = d.target_table
                AND COALESCE(peer.source_adapter, '') =
                    COALESCE(d.source_adapter, '')
                AND COALESCE(peer.source_vocabulary_id, '') =
                    COALESCE(d.source_vocabulary_id, '')
                AND COALESCE(peer.source_code, '') = COALESCE(d.source_code, '')
                AND LOWER(TRIM(peer.source_value)) = LOWER(TRIM(d.source_value))
                AND peer.assigned_concept_id = d.assigned_concept_id
          )
        ORDER BY proposed_at, mapping_decision_id
    """, [reviewer_actor_id, reviewer_actor_id]).fetchall()
    result = [dict(zip(columns, row, strict=True)) for row in rows]
    audit_security_event(
        con, "CLINICAL_REVIEW_QUEUE_ACCESS", reviewer, "ALLOWED",
        {"role": "reviewer", "result_count": len(result)},
    )
    return result

def blinded_adjudication_queue(con, adjudicator):
    """Return two-review cases without exposing reviewer identities or votes."""
    adjudicator = (adjudicator or "").strip()
    if not adjudicator:
        raise ValueError("An adjudicator name is required.")
    adjudicator = authorize_actor(adjudicator, "adjudicator")
    ensure_governance_tables(con)
    actor = resolve_governed_actor(con, adjudicator, "adjudicator")
    adjudicator_actor_id = actor["actor_id"]
    adjudicator = actor["display_name"]
    columns = [
        "mapping_decision_id", "run_id", "target_table", "source_value",
        "normalized_value", "assigned_concept_id", "mapping_method", "score",
        "model_name", "affected_events", "review_count",
    ]
    rows = con.execute("""
        WITH review_counts AS (
            SELECT mapping_decision_id, COUNT(DISTINCT review_id) AS review_count
            FROM clinical_mapping_review
            WHERE COALESCE(active, TRUE)
            GROUP BY mapping_decision_id
        ), provenance_counts AS (
            SELECT mapping_decision_id,
                   COUNT(DISTINCT target_id) AS affected_events
            FROM mapping_provenance
            GROUP BY mapping_decision_id
        ), ranked AS (
            SELECT d.mapping_decision_id, d.run_id, d.target_table,
                   d.source_value, d.normalized_value, d.assigned_concept_id,
                   d.mapping_method, d.score, d.model_name, d.proposed_at,
                   d.proposed_by, d.proposed_by_actor_id,
                   COALESCE(p.affected_events, 0) AS affected_events,
                   COALESCE(r.review_count, 0) AS review_count,
                   ROW_NUMBER() OVER (
                       PARTITION BY d.target_table,
                                    COALESCE(d.source_adapter, ''),
                                    COALESCE(d.source_vocabulary_id, ''),
                                    COALESCE(d.source_code, ''),
                                    LOWER(TRIM(d.source_value)),
                                    d.assigned_concept_id
                       ORDER BY COALESCE(r.review_count, 0) DESC,
                                d.proposed_at, d.mapping_decision_id
                   ) AS canonical_rank
            FROM mapping_decision d
            LEFT JOIN review_counts r USING (mapping_decision_id)
            LEFT JOIN provenance_counts p USING (mapping_decision_id)
            WHERE d.status IN ('PENDING', 'LOW_CONFIDENCE')
              AND COALESCE(d.publication_eligible, TRUE)
        )
        SELECT mapping_decision_id, run_id, target_table, source_value,
               normalized_value, assigned_concept_id, mapping_method, score,
               model_name, affected_events, review_count
        FROM ranked d
        WHERE canonical_rank = 1
          AND review_count >= 2
          AND (
              proposed_by IS NULL
              OR proposed_by_actor_id <> ?
          )
          AND NOT EXISTS (
              SELECT 1 FROM clinical_mapping_adjudication a
              WHERE a.mapping_decision_id = d.mapping_decision_id
          )
          AND NOT EXISTS (
              SELECT 1 FROM clinical_mapping_review own
              WHERE own.mapping_decision_id = d.mapping_decision_id
                AND COALESCE(own.active, TRUE)
                AND own.reviewer_actor_id = ?
          )
        ORDER BY proposed_at, mapping_decision_id
    """, [
        adjudicator_actor_id, adjudicator_actor_id,
    ]).fetchall()
    result = [dict(zip(columns, row, strict=True)) for row in rows]
    audit_security_event(
        con, "CLINICAL_REVIEW_QUEUE_ACCESS", adjudicator, "ALLOWED",
        {"role": "adjudicator", "result_count": len(result)},
    )
    return result

def clinical_review_agreement(con):
    """Measure raw agreement and Cohen's kappa for completed review pairs."""
    ensure_governance_tables(con)
    rows = con.execute("""
        SELECT d.mapping_decision_id, d.target_table, r.verdict
        FROM mapping_decision d
        JOIN clinical_mapping_review r
          ON r.mapping_decision_id = d.mapping_decision_id
        WHERE COALESCE(r.active, TRUE)
        ORDER BY d.mapping_decision_id, r.submitted_at, r.review_id
    """).fetchall()
    grouped = defaultdict(list)
    domains = {}
    for decision_id, target_table, verdict in rows:
        grouped[decision_id].append(verdict)
        domains[decision_id] = TARGET_GOVERNANCE[target_table][2]

    def metrics(pairs):
        if not pairs:
            return {
                "pair_count": 0, "raw_agreement": None,
                "cohens_kappa": None, "approve_votes": 0, "reject_votes": 0,
            }
        votes = Counter(value for pair in pairs for value in pair)
        left_votes = Counter(pair[0] for pair in pairs)
        right_votes = Counter(pair[1] for pair in pairs)
        observed = sum(left == right for left, right in pairs) / len(pairs)
        2 * len(pairs)
        expected = sum(
            (left_votes[verdict] / len(pairs))
            * (right_votes[verdict] / len(pairs))
            for verdict in ("APPROVE", "REJECT")
        )
        kappa = None if expected == 1.0 else (observed - expected) / (1.0 - expected)
        return {
            "pair_count": len(pairs), "raw_agreement": observed,
            "cohens_kappa": kappa, "approve_votes": votes["APPROVE"],
            "reject_votes": votes["REJECT"],
        }

    complete = {
        decision_id: verdicts[:2]
        for decision_id, verdicts in grouped.items() if len(verdicts) >= 2
    }
    by_domain = {}
    for domain in sorted(set(domains.values())):
        pairs = [
            pair for decision_id, pair in complete.items()
            if domains[decision_id] == domain
        ]
        by_domain[domain] = metrics(pairs)
    return {
        "overall": metrics(list(complete.values())),
        "by_domain": by_domain,
    }

def review_mapping_decision(*_args, **_kwargs):
    """Block the retired single-review publication path."""
    raise ValueError(
        "Direct publication is disabled; use two blinded reviews and adjudication."
    )
