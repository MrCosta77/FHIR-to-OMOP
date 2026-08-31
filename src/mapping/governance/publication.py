from __future__ import annotations

import uuid

from src.omop.mapping_targets import TARGETS
from src.security.privacy import (
    audit_security_event,
    authorize_actor,
)
from .schema import ensure_governance_tables
from .core import TARGET_GOVERNANCE
from .identity import resolve_governed_actor

def adjudicate_mapping_decision(
    con, decision_id, action, adjudicator, rationale
):
    """Finalize a proposal only after two reviews by distinct other people."""
    action = action.strip().upper()
    if action not in {"APPROVE", "REJECT"}:
        raise ValueError("action must be APPROVE or REJECT")
    adjudicator = adjudicator.strip()
    if not adjudicator:
        raise ValueError("An adjudicator name is required.")
    adjudicator = authorize_actor(adjudicator, "adjudicator")
    rationale = (rationale or "").strip()
    if not rationale:
        raise ValueError("An adjudication rationale is required.")
    ensure_governance_tables(con)
    actor = resolve_governed_actor(con, adjudicator, "adjudicator")
    adjudicator_actor_id = actor["actor_id"]
    adjudicator = actor["display_name"]
    decision = con.execute("""
        SELECT proposed_by_actor_id FROM mapping_decision
        WHERE mapping_decision_id = ?
    """, [decision_id]).fetchone()
    if not decision:
        raise ValueError(f"Unknown mapping decision: {decision_id}")
    if decision[0] and decision[0] == adjudicator_actor_id:
        raise ValueError(
            "A counterproposal author cannot adjudicate their own candidate."
        )
    reviews = con.execute("""
        SELECT reviewer_actor_id, verdict FROM clinical_mapping_review
        WHERE mapping_decision_id = ? AND COALESCE(active, TRUE)
        ORDER BY submitted_at, review_id
    """, [decision_id]).fetchall()
    if len(reviews) != 2:
        raise ValueError("Exactly two independent reviews are required before adjudication.")
    reviewer_actor_ids = {actor_id for actor_id, _ in reviews}
    if len(reviewer_actor_ids) != 2 or None in reviewer_actor_ids:
        raise ValueError("Clinical reviews must come from two distinct reviewers.")
    if adjudicator_actor_id in reviewer_actor_ids:
        raise ValueError("The adjudicator must be distinct from both reviewers.")
    unanimous = reviews[0][1] == reviews[1][1]

    con.execute("BEGIN TRANSACTION")
    try:
        status = _finalize_mapping_decision(
            con, decision_id, action, adjudicator, rationale,
            manage_transaction=False,
        )
        con.execute("""
            INSERT INTO clinical_mapping_adjudication (
                adjudication_id, mapping_decision_id, adjudicator,
                adjudicator_actor_id, final_action, rationale,
                reviewer_count, unanimous
            ) VALUES (?, ?, ?, ?, ?, ?, 2, ?)
        """, [
            str(uuid.uuid4()), decision_id, adjudicator, adjudicator_actor_id,
            action, rationale, unanimous,
        ])
        audit_security_event(
            con, "CLINICAL_MAPPING_ADJUDICATION", adjudicator, status,
            {
                "mapping_decision_id": decision_id,
                "final_action": action,
                "unanimous": unanimous,
            },
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return status

def _validate_scoped_event_binding(
    con,
    decision_id,
    target_table,
    source_adapter,
    source_record_key,
    source_system,
    source_vocabulary_id,
    source_code,
):
    """Revalidate external identity and event state at adjudication time."""
    binding = con.execute("""
        SELECT target_id FROM source_event_binding
        WHERE mapping_decision_id = ? AND source_adapter = ?
          AND source_record_key = ? AND source_system = ?
          AND source_vocabulary_id = ? AND source_code = ?
          AND target_table = ? AND active
    """, [
        decision_id, source_adapter, source_record_key, source_system,
        source_vocabulary_id, source_code, target_table,
    ]).fetchall()
    if len(binding) != 1:
        raise ValueError("Decision requires exactly one active source-event binding")
    active_registry = con.execute("""
        SELECT COUNT(*) FROM source_identity_registry
        WHERE source_adapter = ? AND source_system = ?
          AND source_vocabulary_id = ? AND active
    """, [
        source_adapter, source_system, source_vocabulary_id,
    ]).fetchone()[0]
    if active_registry != 1:
        raise ValueError("Decision source identity is no longer active")
    config = TARGETS[target_table]
    event = con.execute(f"""
        SELECT {config['concept_column']}, {config['source_concept_column']},
               {config['source_column']}
        FROM {target_table}
        WHERE {config['id_column']} = ?
    """, [int(binding[0][0])]).fetchall()
    if len(event) != 1:
        raise ValueError("Bound OMOP event no longer exists uniquely")
    concept_id, source_concept_id, event_source_code = event[0]
    if int(concept_id or 0) != 0 or source_concept_id not in {None, 0}:
        raise ValueError("Bound OMOP event is no longer an unmapped source event")
    if event_source_code != source_code:
        raise ValueError("Bound OMOP event source code has changed")


def _finalize_mapping_decision(
    con, decision_id, action, reviewer, reason=None, *, manage_transaction=True
):
    """Apply an adjudicated decision and atomically update publication policy."""
    action = action.strip().upper()
    if action not in {"APPROVE", "REJECT"}:
        raise ValueError("action must be APPROVE or REJECT")
    reviewer = reviewer.strip()
    if not reviewer:
        raise ValueError("A reviewer name is required.")
    ensure_governance_tables(con)
    row = con.execute("""
        SELECT target_table, source_value, assigned_concept_id, run_id,
               COALESCE(publication_eligible, TRUE), source_adapter,
               source_record_key, source_system, source_code,
               source_vocabulary_id, status
        FROM mapping_decision WHERE mapping_decision_id = ?
    """, [decision_id]).fetchone()
    if not row:
        raise ValueError(f"Unknown mapping decision: {decision_id}")
    (
        target_table, source_value, concept_id, run_id, publication_eligible,
        source_adapter, source_record_key, source_system, explicit_source_code,
        explicit_source_vocabulary, current_status,
    ) = row
    if current_status not in {"PENDING", "LOW_CONFIDENCE"}:
        raise ValueError(f"Decision is not adjudication-eligible: {current_status}")
    if not publication_eligible:
        raise ValueError(
            "This pre-ingestion proposal is not adjudication-eligible; "
            "bind it to an explicit source vocabulary and ingested OMOP event first."
        )
    default_source_vocabulary, target_vocabulary, expected_domain = (
        TARGET_GOVERNANCE[target_table]
    )
    source_vocabulary = explicit_source_vocabulary or default_source_vocabulary
    source_code = explicit_source_code or source_value
    if source_adapter:
        if not all((
            source_record_key, source_system, explicit_source_code,
            explicit_source_vocabulary,
        )):
            raise ValueError("External decision has incomplete source identity")
        _validate_scoped_event_binding(
            con, decision_id, target_table, source_adapter, source_record_key,
            source_system, source_vocabulary, source_code,
        )
    new_status = "APPROVED" if action == "APPROVE" else "REJECTED"

    if action == "APPROVE":
        valid = con.execute("""
            SELECT vocabulary_id FROM concept
            WHERE concept_id = ? AND domain_id = ? AND standard_concept = 'S'
              AND (invalid_reason IS NULL OR invalid_reason = '')
              AND CURRENT_DATE BETWEEN
                  COALESCE(TRY_CAST(valid_start_date AS DATE),
                           TRY_STRPTIME(CAST(valid_start_date AS VARCHAR), '%Y%m%d')::DATE)
                  AND COALESCE(TRY_CAST(valid_end_date AS DATE),
                               TRY_STRPTIME(CAST(valid_end_date AS VARCHAR), '%Y%m%d')::DATE)
            LIMIT 1
        """, [int(concept_id), expected_domain]).fetchone()
        if not valid:
            raise ValueError(
                f"Concept {concept_id} is not a current Standard {expected_domain} concept."
            )
        target_vocabulary = valid[0]

    if manage_transaction:
        con.execute("BEGIN TRANSACTION")
    try:
        duplicate_ids = [
            duplicate[0]
            for duplicate in con.execute("""
                SELECT mapping_decision_id FROM mapping_decision
                WHERE mapping_decision_id <> ?
                  AND status IN ('PENDING', 'LOW_CONFIDENCE')
                  AND target_table = ?
                  AND COALESCE(source_adapter, '') = COALESCE(?, '')
                  AND COALESCE(source_vocabulary_id, '') = COALESCE(?, '')
                  AND COALESCE(source_code, '') = COALESCE(?, '')
                  AND LOWER(TRIM(source_value)) = LOWER(TRIM(?))
                  AND assigned_concept_id = ?
            """, [
                decision_id, target_table, source_adapter,
                explicit_source_vocabulary, explicit_source_code,
                source_value, int(concept_id),
            ]).fetchall()
        ]
        con.execute("""
            UPDATE mapping_decision
            SET status = ?, reviewer = ?, review_reason = ?, reviewed_at = now()
            WHERE mapping_decision_id = ? AND status IN ('PENDING', 'LOW_CONFIDENCE')
        """, [new_status, reviewer, reason, decision_id])
        if duplicate_ids:
            supersede_reason = (
                f"Superseded by canonical adjudication {decision_id}"
            )
            con.execute("""
                UPDATE mapping_decision
                SET status = 'SUPERSEDED', reviewer = ?, review_reason = ?,
                    reviewed_at = now()
                WHERE mapping_decision_id IN (SELECT UNNEST(?::VARCHAR[]))
                  AND status IN ('PENDING', 'LOW_CONFIDENCE')
            """, [reviewer, supersede_reason, duplicate_ids])
            con.execute("""
                UPDATE mapping_provenance
                SET reviewed_by = 'Superseded_By_Canonical_Decision'
                WHERE mapping_decision_id IN (SELECT UNNEST(?::VARCHAR[]))
                  AND reviewed_by IN (
                      'Pending_Human_Review', 'Below_Confidence_Threshold',
                      'REJECTED_BY_POLICY'
                  )
            """, [duplicate_ids])
        legacy_status = (
            "Approved_by_Human" if action == "APPROVE" else "Rejected_by_Human"
        )
        con.execute("""
            UPDATE mapping_provenance
            SET reviewed_by = ?
            WHERE mapping_decision_id = ?
              AND reviewed_by IN (
                  'Pending_Human_Review', 'Below_Confidence_Threshold',
                  'REJECTED_BY_POLICY'
              )
        """, [legacy_status, decision_id])

        if action == "APPROVE":
            con.execute("""
                INSERT INTO scoped_approved_mapping_set (
                    target_table, source_vocabulary_id, source_code,
                    source_value, assigned_concept_id, mapping_decision_id,
                    approved_run_id, reviewer, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (target_table, source_vocabulary_id, source_code)
                DO UPDATE SET
                    source_value = EXCLUDED.source_value,
                    assigned_concept_id = EXCLUDED.assigned_concept_id,
                    mapping_decision_id = EXCLUDED.mapping_decision_id,
                    approved_run_id = EXCLUDED.approved_run_id,
                    reviewer = EXCLUDED.reviewer,
                    reason = EXCLUDED.reason,
                    approved_at = now(), active = TRUE
            """, [
                target_table, source_vocabulary, source_code, source_value,
                int(concept_id), decision_id, run_id, reviewer, reason,
            ])
            if not source_adapter:
                con.execute("""
                    INSERT INTO approved_mapping_set (
                        target_table, source_value, assigned_concept_id,
                        mapping_decision_id, approved_run_id, reviewer, reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (target_table, source_value) DO UPDATE SET
                        assigned_concept_id = EXCLUDED.assigned_concept_id,
                        mapping_decision_id = EXCLUDED.mapping_decision_id,
                        approved_run_id = EXCLUDED.approved_run_id,
                        reviewer = EXCLUDED.reviewer,
                        reason = EXCLUDED.reason,
                        approved_at = now(), active = TRUE
                """, [
                    target_table, source_value, int(concept_id), decision_id,
                    run_id, reviewer, reason,
                ])
            con.execute("""
                DELETE FROM source_to_concept_map
                WHERE source_code = ? AND source_vocabulary_id = ?
            """, [source_code, source_vocabulary])
            con.execute("""
                INSERT INTO source_to_concept_map (
                    source_code, source_concept_id, source_vocabulary_id,
                    source_code_description, target_concept_id,
                    target_vocabulary_id, valid_start_date, valid_end_date,
                    invalid_reason
                ) VALUES (?, 0, ?, ?, ?, ?, CURRENT_DATE, '2099-12-31', NULL)
            """, [
                source_code, source_vocabulary, source_value,
                int(concept_id), target_vocabulary,
            ])
            con.execute("""
                UPDATE scoped_mapping_rejection_policy SET active = FALSE
                WHERE target_table = ? AND source_vocabulary_id = ?
                  AND source_code = ?
                  AND assigned_concept_id = ?
            """, [
                target_table, source_vocabulary, source_code, int(concept_id),
            ])
            if not source_adapter:
                con.execute("""
                    UPDATE mapping_rejection_policy SET active = FALSE
                    WHERE target_table = ? AND source_value = ?
                      AND assigned_concept_id = ?
                """, [target_table, source_value, int(concept_id)])
        else:
            con.execute("""
                INSERT INTO scoped_mapping_rejection_policy (
                    target_table, source_vocabulary_id, source_code,
                    source_value, assigned_concept_id, reviewer, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (
                    target_table, source_vocabulary_id, source_code,
                    assigned_concept_id
                )
                DO UPDATE SET reviewer = EXCLUDED.reviewer,
                              source_value = EXCLUDED.source_value,
                              reason = EXCLUDED.reason,
                              rejected_at = now(), active = TRUE
            """, [
                target_table, source_vocabulary, source_code, source_value,
                int(concept_id), reviewer, reason,
            ])
            con.execute("""
                UPDATE scoped_approved_mapping_set SET active = FALSE
                WHERE target_table = ? AND source_vocabulary_id = ?
                  AND source_code = ?
                  AND assigned_concept_id = ?
            """, [
                target_table, source_vocabulary, source_code, int(concept_id),
            ])
            if not source_adapter:
                con.execute("""
                    INSERT INTO mapping_rejection_policy (
                        target_table, source_value, assigned_concept_id,
                        reviewer, reason
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT (
                        target_table, source_value, assigned_concept_id
                    ) DO UPDATE SET reviewer = EXCLUDED.reviewer,
                                    reason = EXCLUDED.reason,
                                    rejected_at = now(), active = TRUE
                """, [
                    target_table, source_value, int(concept_id), reviewer, reason,
                ])
                con.execute("""
                    UPDATE approved_mapping_set SET active = FALSE
                    WHERE target_table = ? AND source_value = ?
                      AND assigned_concept_id = ?
                """, [target_table, source_value, int(concept_id)])
            con.execute("""
                DELETE FROM source_to_concept_map
                WHERE source_code = ? AND source_vocabulary_id = ?
                  AND target_concept_id = ?
            """, [source_code, source_vocabulary, int(concept_id)])
        if manage_transaction:
            con.execute("COMMIT")
    except Exception:
        if manage_transaction:
            con.execute("ROLLBACK")
        raise
    return new_status
