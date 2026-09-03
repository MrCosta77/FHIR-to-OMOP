from __future__ import annotations

import hashlib

from src.security.privacy import (
    audit_security_event,
    authorize_actor,
)

from .core import TARGET_GOVERNANCE, decision_id_for
from .identity import resolve_governed_actor
from .schema import ensure_governance_tables


def _semantic_duplicate_ids(con, row: dict):
    return [
        value[0]
        for value in con.execute(
            """
            SELECT mapping_decision_id
            FROM mapping_decision
            WHERE target_table = ?
              AND COALESCE(source_adapter, '') = COALESCE(?, '')
              AND COALESCE(source_vocabulary_id, '') = COALESCE(?, '')
              AND COALESCE(source_code, '') = COALESCE(?, '')
              AND LOWER(TRIM(source_value)) = LOWER(TRIM(?))
              AND assigned_concept_id = ?
            """,
            [
                row["target_table"],
                row["source_adapter"],
                row["source_vocabulary_id"],
                row["source_code"],
                row["source_value"],
                int(row["assigned_concept_id"]),
            ],
        ).fetchall()
    ]


def counterproposal_source_queue(con, proposer):
    """Return finally rejected mappings this actor may correct."""
    proposer = authorize_actor((proposer or "").strip(), "reviewer")
    ensure_governance_tables(con)
    actor = resolve_governed_actor(con, proposer, "reviewer")
    proposer_actor_id = actor["actor_id"]
    proposer = actor["display_name"]
    columns = [
        "mapping_decision_id", "target_table", "source_value",
        "rejected_concept_id", "rejected_candidate", "reviewed_at",
    ]
    rows = con.execute(
        """
        SELECT d.mapping_decision_id, d.target_table, d.source_value,
               d.assigned_concept_id, d.normalized_value, d.reviewed_at
        FROM mapping_decision d
        WHERE d.status = 'REJECTED'
          AND COALESCE(d.publication_eligible, TRUE)
          AND d.source_adapter IS NULL
          AND EXISTS (
              SELECT 1 FROM clinical_mapping_review r
              WHERE r.mapping_decision_id = d.mapping_decision_id
                AND COALESCE(r.active, TRUE)
                AND r.reviewer_actor_id = ?
                AND r.verdict = 'REJECT'
          )
          AND NOT EXISTS (
              SELECT 1 FROM mapping_decision c
              WHERE c.supersedes_decision_id = d.mapping_decision_id
                AND c.mapping_method = 'human_counterproposal'
                AND c.status <> 'SUPERSEDED'
          )
        ORDER BY d.reviewed_at, d.mapping_decision_id
        """,
        [proposer_actor_id],
    ).fetchall()
    return [dict(zip(columns, row)) for row in rows]


def submit_counterproposal(
    con, original_decision_id, candidate_concept_id, proposer, rationale
):
    """Create a traceable human candidate without mutating the rejected record."""
    proposer = authorize_actor((proposer or "").strip(), "reviewer")
    rationale = (rationale or "").strip()
    if not rationale:
        raise ValueError("A clinical rationale for the counterproposal is required.")
    try:
        candidate_concept_id = int(candidate_concept_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("Candidate concept_id must be an integer.") from exc

    ensure_governance_tables(con)
    actor = resolve_governed_actor(con, proposer, "reviewer")
    proposer_actor_id = actor["actor_id"]
    proposer = actor["display_name"]
    cursor = con.execute(
        """
        SELECT mapping_decision_id, target_table, source_value,
               normalized_value, assigned_concept_id, run_id,
               vocabulary_version, COALESCE(publication_eligible, TRUE) as publication_eligible,
               source_adapter, source_record_key, source_system, source_code,
               source_vocabulary_id, status
        FROM mapping_decision WHERE mapping_decision_id = ?
        """,
        [original_decision_id],
    )
    row = cursor.fetchone()
    if not row:
        raise ValueError(f"Unknown mapping decision: {original_decision_id}")
    columns = [desc[0] for desc in cursor.description]
    original = dict(zip(columns, row))

    if original["status"] != "REJECTED":
        raise ValueError(
            "The original candidate must be finally REJECTED before a "
            "counterproposal can be created."
        )
    if not original["publication_eligible"]:
        raise ValueError("Only publication-eligible decisions can be corrected.")
    if original["source_adapter"]:
        raise ValueError(
            "Counterproposals for externally bound events require a new verified "
            "source-event binding and are not yet supported by this workflow."
        )
    if candidate_concept_id == int(original["assigned_concept_id"]):
        raise ValueError("The counterproposal must select a different concept.")

    duplicate_ids = _semantic_duplicate_ids(con, original)
    reviewed_reject = con.execute(
        """
        SELECT COUNT(*) FROM clinical_mapping_review
        WHERE mapping_decision_id IN (SELECT UNNEST(?::VARCHAR[]))
          AND COALESCE(active, TRUE)
          AND reviewer_actor_id = ?
          AND verdict = 'REJECT'
        """,
        [duplicate_ids, proposer_actor_id],
    ).fetchone()[0]
    if not reviewed_reject:
        raise ValueError(
            "The proposer must have independently rejected the original candidate."
        )

    expected_domain = TARGET_GOVERNANCE[original["target_table"]][2]
    candidate = con.execute(
        """
        SELECT concept_name, vocabulary_id
        FROM concept
        WHERE concept_id = ? AND domain_id = ? AND standard_concept = 'S'
          AND (invalid_reason IS NULL OR invalid_reason = '')
          AND CURRENT_DATE BETWEEN COALESCE(TRY_CAST(valid_start_date AS DATE), TRY_STRPTIME(CAST(valid_start_date AS VARCHAR), '%Y%m%d')::DATE) AND COALESCE(TRY_CAST(valid_end_date AS DATE), TRY_STRPTIME(CAST(valid_end_date AS VARCHAR), '%Y%m%d')::DATE)
        LIMIT 1
        """,
        [candidate_concept_id, expected_domain],
    ).fetchone()
    if not candidate:
        raise ValueError(
            f"Concept {candidate_concept_id} is not a current Standard "
            f"{expected_domain} concept."
        )

    semantic_key = "|".join(
        [
            original["target_table"], original["source_vocabulary_id"] or "", original["source_code"] or "",
            original["source_value"].strip().casefold(), str(candidate_concept_id),
        ]
    )
    scope_digest = hashlib.sha256(semantic_key.encode("utf-8")).hexdigest()
    decision_id = decision_id_for(
        "HUMAN-CURATION", original["target_table"], original["source_value"], candidate_concept_id,
        source_record_key=scope_digest,
    )
    existing = con.execute(
        """
        SELECT proposed_by_actor_id FROM mapping_decision
        WHERE mapping_decision_id = ?
        """,
        [decision_id],
    ).fetchone()
    if existing:
        if existing[0] != proposer_actor_id:
            raise ValueError("This counterproposal already exists under another proposer.")
        return {
            "mapping_decision_id": decision_id,
            "candidate_concept_id": candidate_concept_id,
            "candidate_name": candidate[0],
            "created": False,
        }

    con.execute("BEGIN TRANSACTION")
    try:
        con.execute(
            """
            INSERT INTO mapping_decision (
                mapping_decision_id, run_id, target_table, source_value,
                normalized_value, assigned_concept_id, mapping_method, score,
                model_name, prompt_version, vocabulary_version, status,
                source_system, source_code, source_vocabulary_id,
                publication_eligible, proposed_by, proposal_rationale,
                supersedes_decision_id, proposed_by_actor_id
            ) VALUES (?, 'HUMAN-CURATION', ?, ?, ?, ?,
                      'human_counterproposal', NULL, 'human-curation',
                      'human-counterproposal-v1', ?, 'PENDING', ?, ?, ?, TRUE,
                      ?, ?, ?, ?)
            """,
            [
                decision_id, original["target_table"], original["source_value"], candidate[0],
                candidate_concept_id, original["vocabulary_version"], original["source_system"], original["source_code"],
                original["source_vocabulary_id"], proposer, rationale, original_decision_id,
                proposer_actor_id,
            ],
        )
        con.execute(
            """
            INSERT INTO mapping_provenance (
                target_table, target_id, source_value, normalized_value,
                assigned_concept_id, mapping_method, score, model_name,
                prompt_version, vocabulary_version, reviewed_by, run_id,
                mapping_decision_id, source_system, source_code,
                source_vocabulary_id, publication_eligible
            )
            SELECT target_table, target_id, source_value, ?, ?,
                   'human_counterproposal', NULL, 'human-curation',
                   'human-counterproposal-v1', vocabulary_version,
                   'Pending_Human_Review', 'HUMAN-CURATION', ?, source_system,
                   source_code, source_vocabulary_id, TRUE
            FROM (
                SELECT p.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY p.target_table, p.target_id
                           ORDER BY p.created_at, p.provenance_id
                       ) AS event_rank
                FROM mapping_provenance p
                WHERE p.mapping_decision_id IN (
                    SELECT UNNEST(?::VARCHAR[])
                )
            ) p
            WHERE event_rank = 1
            """,
            [candidate[0], candidate_concept_id, decision_id, duplicate_ids],
        )
        audit_security_event(
            con, "CLINICAL_MAPPING_COUNTERPROPOSAL", proposer, "RECORDED",
            {
                "candidate_concept_id": candidate_concept_id,
                "domain": expected_domain,
            },
            run_id="HUMAN-CURATION",
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {
        "mapping_decision_id": decision_id,
        "candidate_concept_id": candidate_concept_id,
        "candidate_name": candidate[0],
        "created": True,
    }
