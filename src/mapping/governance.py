"""Human review and publication controls for semantic mappings."""

from __future__ import annotations

import os
import uuid
from collections import Counter, defaultdict

from src.security.privacy import audit_security_event, authorize_actor


TARGET_GOVERNANCE = {
    "condition_occurrence": ("CMF_SYNTHEA_CONDITION", "SNOMED", "Condition"),
    "drug_exposure": ("CMF_SYNTHEA_DRUG", "RxNorm", "Drug"),
    "measurement": ("CMF_SYNTHEA_MEASUREMENT", "LOINC", "Measurement"),
    "observation": ("CMF_SYNTHEA_OBSERVATION", "SNOMED", "Observation"),
    "procedure_occurrence": ("CMF_SYNTHEA_PROCEDURE", "SNOMED", "Procedure"),
    "device_exposure": ("CMF_SYNTHEA_DEVICE", "SNOMED", "Device"),
}


def current_run_id() -> str | None:
    return os.environ.get("CMF_RUN_ID") or None


def _columns(con, table):
    return {
        row[0]
        for row in con.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'main' AND table_name = ?
            """,
            [table],
        ).fetchall()
    }


def _table_exists(con, table):
    return bool(con.execute("""
        SELECT COUNT(*) FROM information_schema.tables
        WHERE table_schema = 'main' AND table_name = ?
    """, [table]).fetchone()[0])


def _add_column(con, table, name, datatype):
    if name not in _columns(con, table):
        con.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {datatype}')


def ensure_governance_tables(con):
    """Install the review schema and non-destructively upgrade legacy audit data."""
    con.execute("CREATE SEQUENCE IF NOT EXISTS seq_provenance_id START 1")
    con.execute("""
        CREATE TABLE IF NOT EXISTS mapping_provenance (
            provenance_id BIGINT DEFAULT nextval('seq_provenance_id') PRIMARY KEY,
            target_table VARCHAR NOT NULL,
            target_id BIGINT,
            source_value VARCHAR NOT NULL,
            normalized_value VARCHAR,
            assigned_concept_id INTEGER,
            mapping_method VARCHAR,
            score DOUBLE,
            model_name VARCHAR,
            prompt_version VARCHAR,
            vocabulary_version VARCHAR,
            reviewed_by VARCHAR DEFAULT 'Pending_Human_Review',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    for name, datatype in (
        ("run_id", "VARCHAR"),
        ("mapping_decision_id", "VARCHAR"),
    ):
        _add_column(con, "mapping_provenance", name, datatype)

    con.execute("""
        CREATE TABLE IF NOT EXISTS etl_run (
            run_id VARCHAR PRIMARY KEY,
            status VARCHAR NOT NULL,
            started_at TIMESTAMP NOT NULL,
            completed_at TIMESTAMP,
            git_commit VARCHAR,
            input_manifest VARCHAR NOT NULL,
            configuration_manifest VARCHAR NOT NULL,
            step_manifest VARCHAR NOT NULL,
            error_message VARCHAR
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS mapping_decision (
            mapping_decision_id VARCHAR PRIMARY KEY,
            run_id VARCHAR,
            target_table VARCHAR NOT NULL,
            source_value VARCHAR NOT NULL,
            normalized_value VARCHAR,
            assigned_concept_id INTEGER NOT NULL,
            mapping_method VARCHAR NOT NULL,
            score DOUBLE,
            model_name VARCHAR,
            prompt_version VARCHAR,
            vocabulary_version VARCHAR,
            status VARCHAR NOT NULL,
            proposed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TIMESTAMP,
            reviewer VARCHAR,
            review_reason VARCHAR
        )
    """)
    for name, datatype in (
        ("prompt_version", "VARCHAR"),
        ("llm_decision", "VARCHAR"),
        ("llm_confidence", "DOUBLE"),
        ("llm_reason", "VARCHAR"),
        ("clinical_signals", "VARCHAR"),
        ("model_digest", "VARCHAR"),
        ("generation_parameters", "VARCHAR"),
        ("index_signature", "VARCHAR"),
    ):
        _add_column(con, "mapping_decision", name, datatype)
    con.execute("""
        CREATE TABLE IF NOT EXISTS mapping_rejection_policy (
            target_table VARCHAR NOT NULL,
            source_value VARCHAR NOT NULL,
            assigned_concept_id INTEGER NOT NULL,
            reviewer VARCHAR NOT NULL,
            reason VARCHAR,
            rejected_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            PRIMARY KEY (target_table, source_value, assigned_concept_id)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS approved_mapping_set (
            target_table VARCHAR NOT NULL,
            source_value VARCHAR NOT NULL,
            assigned_concept_id INTEGER NOT NULL,
            mapping_decision_id VARCHAR NOT NULL,
            approved_run_id VARCHAR,
            reviewer VARCHAR NOT NULL,
            reason VARCHAR,
            approved_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            PRIMARY KEY (target_table, source_value)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS clinical_mapping_review (
            review_id VARCHAR PRIMARY KEY,
            mapping_decision_id VARCHAR NOT NULL,
            reviewer VARCHAR NOT NULL,
            verdict VARCHAR NOT NULL,
            rationale VARCHAR NOT NULL,
            submitted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (mapping_decision_id, reviewer)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS clinical_mapping_adjudication (
            adjudication_id VARCHAR PRIMARY KEY,
            mapping_decision_id VARCHAR NOT NULL UNIQUE,
            adjudicator VARCHAR NOT NULL,
            final_action VARCHAR NOT NULL,
            rationale VARCHAR NOT NULL,
            reviewer_count INTEGER NOT NULL,
            unanimous BOOLEAN NOT NULL,
            adjudicated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    _migrate_legacy_decisions(con)


def _migrate_legacy_decisions(con):
    """Attach legacy LLM provenance to decisions and unpublish pending STCM rows."""
    legacy = con.execute("""
        SELECT run_id, target_table, source_value, assigned_concept_id,
               ANY_VALUE(normalized_value), ANY_VALUE(mapping_method),
               MAX(score), ANY_VALUE(model_name), ANY_VALUE(vocabulary_version),
               reviewed_by
        FROM mapping_provenance
        WHERE mapping_decision_id IS NULL
          AND mapping_method = 'llm_rag_few_shot'
          AND assigned_concept_id IS NOT NULL
          AND reviewed_by IN (
              'Pending_Human_Review', 'Below_Confidence_Threshold',
              'Approved_by_Human', 'Rejected_by_Human'
          )
        GROUP BY run_id, target_table, source_value, assigned_concept_id, reviewed_by
    """).fetchall()
    statuses = {
        "Pending_Human_Review": "PENDING",
        "Below_Confidence_Threshold": "LOW_CONFIDENCE",
        "Approved_by_Human": "APPROVED",
        "Rejected_by_Human": "REJECTED",
    }
    for (
        run_id, target_table, source_value, concept_id, normalized_value,
        mapping_method, score, model_name, vocabulary_version, reviewed_by,
    ) in legacy:
        decision_id = decision_id_for(
            run_id, target_table, source_value, concept_id
        )
        status = statuses[reviewed_by]
        con.execute("""
            INSERT INTO mapping_decision (
                mapping_decision_id, run_id, target_table, source_value,
                normalized_value, assigned_concept_id, mapping_method, score,
                model_name, prompt_version, vocabulary_version, status,
                reviewer, reviewed_at, review_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'legacy-unversioned', ?, ?,
                      CASE WHEN ? IN ('APPROVED', 'REJECTED')
                           THEN 'Legacy migration' END,
                      CASE WHEN ? IN ('APPROVED', 'REJECTED')
                           THEN now() END,
                      'Migrated from pre-decision provenance')
            ON CONFLICT (mapping_decision_id) DO NOTHING
        """, [
            decision_id, run_id, target_table, source_value, normalized_value,
            int(concept_id), mapping_method, score, model_name,
            vocabulary_version, status, status, status,
        ])
        con.execute("""
            UPDATE mapping_provenance SET mapping_decision_id = ?
            WHERE mapping_decision_id IS NULL
              AND COALESCE(run_id, '') = COALESCE(?, '')
              AND target_table = ? AND source_value = ?
              AND assigned_concept_id = ? AND mapping_method = ?
              AND reviewed_by = ?
        """, [
            decision_id, run_id, target_table, source_value, int(concept_id),
            mapping_method, reviewed_by,
        ])

    if _table_exists(con, "source_to_concept_map"):
        con.execute("""
            DELETE FROM source_to_concept_map stcm
            WHERE stcm.source_vocabulary_id LIKE 'CMF_SYNTHEA%'
              AND EXISTS (
                  SELECT 1 FROM mapping_provenance p
                  WHERE p.source_value = stcm.source_code
                    AND p.assigned_concept_id = stcm.target_concept_id
                    AND p.mapping_method = 'llm_rag_few_shot'
              )
              AND NOT EXISTS (
                  SELECT 1 FROM mapping_provenance p
                  WHERE p.source_value = stcm.source_code
                    AND p.assigned_concept_id = stcm.target_concept_id
                    AND p.mapping_method = 'llm_rag_few_shot'
                    AND p.reviewed_by = 'Approved_by_Human'
              )
        """)


def decision_id_for(run_id, target_table, source_value, concept_id):
    identity = "|".join(
        [run_id or "UNTRACKED", target_table, source_value.strip().lower(), str(concept_id)]
    )
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"cmf:mapping-decision:{identity}"))


def rejection_policy_exists(con, target_table, source_value, concept_id):
    ensure_governance_tables(con)
    return bool(con.execute("""
        SELECT COUNT(*) FROM mapping_rejection_policy
        WHERE target_table = ? AND LOWER(TRIM(source_value)) = LOWER(TRIM(?))
          AND assigned_concept_id = ? AND active
    """, [target_table, source_value, int(concept_id)]).fetchone()[0])


def register_decision(
    con,
    target_table,
    source_value,
    concept_id,
    normalized_value,
    mapping_method,
    score,
    model_name,
    vocabulary_version,
    status,
    run_id=None,
    prompt_version="mapping-prompt-v1",
    llm_decision=None,
    llm_confidence=None,
    llm_reason=None,
    clinical_signals=None,
    model_digest=None,
    generation_parameters=None,
    index_signature=None,
):
    ensure_governance_tables(con)
    run_id = run_id or current_run_id()
    decision_id = decision_id_for(run_id, target_table, source_value, concept_id)
    con.execute("""
        INSERT INTO mapping_decision (
            mapping_decision_id, run_id, target_table, source_value,
            normalized_value, assigned_concept_id, mapping_method, score,
            model_name, prompt_version, vocabulary_version, status,
            llm_decision, llm_confidence, llm_reason, clinical_signals,
            model_digest, generation_parameters, index_signature
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (mapping_decision_id) DO UPDATE SET
            normalized_value = EXCLUDED.normalized_value,
            score = EXCLUDED.score,
            model_name = EXCLUDED.model_name,
            prompt_version = EXCLUDED.prompt_version,
            vocabulary_version = EXCLUDED.vocabulary_version,
            llm_decision = EXCLUDED.llm_decision,
            llm_confidence = EXCLUDED.llm_confidence,
            llm_reason = EXCLUDED.llm_reason,
            clinical_signals = EXCLUDED.clinical_signals,
            model_digest = EXCLUDED.model_digest,
            generation_parameters = EXCLUDED.generation_parameters,
            index_signature = EXCLUDED.index_signature,
            status = CASE
                WHEN mapping_decision.status IN ('APPROVED', 'REJECTED')
                THEN mapping_decision.status ELSE EXCLUDED.status END
    """, [
        decision_id, run_id, target_table, source_value, normalized_value,
        int(concept_id), mapping_method, score, model_name,
        prompt_version, vocabulary_version, status, llm_decision,
        llm_confidence, llm_reason, clinical_signals, model_digest,
        generation_parameters, index_signature,
    ])
    return decision_id


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
    row = con.execute("""
        SELECT status FROM mapping_decision
        WHERE mapping_decision_id = ?
    """, [decision_id]).fetchone()
    if not row:
        raise ValueError(f"Unknown mapping decision: {decision_id}")
    if row[0] not in {"PENDING", "LOW_CONFIDENCE"}:
        raise ValueError(f"Decision is not independently reviewable: {row[0]}")
    existing = con.execute("""
        SELECT COUNT(*) FROM clinical_mapping_review
        WHERE mapping_decision_id = ?
    """, [decision_id]).fetchone()[0]
    if existing >= 2:
        raise ValueError("Two independent reviews already exist; adjudication is required.")
    duplicate = con.execute("""
        SELECT COUNT(*) FROM clinical_mapping_review
        WHERE mapping_decision_id = ? AND LOWER(TRIM(reviewer)) = LOWER(TRIM(?))
    """, [decision_id, reviewer]).fetchone()[0]
    if duplicate:
        raise ValueError("The same reviewer cannot review a decision twice.")
    con.execute("""
        INSERT INTO clinical_mapping_review (
            review_id, mapping_decision_id, reviewer, verdict, rationale
        ) VALUES (?, ?, ?, ?, ?)
    """, [str(uuid.uuid4()), decision_id, reviewer, action, rationale])
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
    columns = [
        "mapping_decision_id", "run_id", "target_table", "source_value",
        "normalized_value", "assigned_concept_id", "mapping_method", "score",
        "model_name", "affected_events",
    ]
    rows = con.execute("""
        SELECT d.mapping_decision_id, d.run_id, d.target_table,
               d.source_value, d.normalized_value, d.assigned_concept_id,
               d.mapping_method, d.score, d.model_name,
               COUNT(DISTINCT p.target_id) AS affected_events
        FROM mapping_decision d
        LEFT JOIN mapping_provenance p
          ON p.mapping_decision_id = d.mapping_decision_id
        WHERE d.status IN ('PENDING', 'LOW_CONFIDENCE')
          AND NOT EXISTS (
              SELECT 1 FROM clinical_mapping_review r
              WHERE r.mapping_decision_id = d.mapping_decision_id
                AND LOWER(TRIM(r.reviewer)) = LOWER(TRIM(?))
          )
          AND (
              SELECT COUNT(*) FROM clinical_mapping_review r
              WHERE r.mapping_decision_id = d.mapping_decision_id
          ) < 2
        GROUP BY ALL
        ORDER BY MIN(d.proposed_at), d.mapping_decision_id
    """, [reviewer]).fetchall()
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
    columns = [
        "mapping_decision_id", "run_id", "target_table", "source_value",
        "normalized_value", "assigned_concept_id", "mapping_method", "score",
        "model_name", "affected_events", "review_count",
    ]
    rows = con.execute("""
        SELECT d.mapping_decision_id, d.run_id, d.target_table,
               d.source_value, d.normalized_value, d.assigned_concept_id,
               d.mapping_method, d.score, d.model_name,
               COUNT(DISTINCT p.target_id) AS affected_events,
               COUNT(DISTINCT r.review_id) AS review_count
        FROM mapping_decision d
        JOIN clinical_mapping_review r
          ON r.mapping_decision_id = d.mapping_decision_id
        LEFT JOIN mapping_provenance p
          ON p.mapping_decision_id = d.mapping_decision_id
        WHERE d.status IN ('PENDING', 'LOW_CONFIDENCE')
          AND NOT EXISTS (
              SELECT 1 FROM clinical_mapping_adjudication a
              WHERE a.mapping_decision_id = d.mapping_decision_id
          )
          AND NOT EXISTS (
              SELECT 1 FROM clinical_mapping_review own
              WHERE own.mapping_decision_id = d.mapping_decision_id
                AND LOWER(TRIM(own.reviewer)) = LOWER(TRIM(?))
          )
        GROUP BY ALL
        HAVING COUNT(DISTINCT r.review_id) >= 2
        ORDER BY MIN(d.proposed_at), d.mapping_decision_id
    """, [adjudicator]).fetchall()
    result = [dict(zip(columns, row, strict=True)) for row in rows]
    audit_security_event(
        con, "CLINICAL_REVIEW_QUEUE_ACCESS", adjudicator, "ALLOWED",
        {"role": "adjudicator", "result_count": len(result)},
    )
    return result


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
    reviews = con.execute("""
        SELECT reviewer, verdict FROM clinical_mapping_review
        WHERE mapping_decision_id = ?
        ORDER BY submitted_at, review_id
    """, [decision_id]).fetchall()
    if len(reviews) != 2:
        raise ValueError("Exactly two independent reviews are required before adjudication.")
    reviewer_keys = {reviewer.strip().casefold() for reviewer, _ in reviews}
    if len(reviewer_keys) != 2:
        raise ValueError("Clinical reviews must come from two distinct reviewers.")
    if adjudicator.casefold() in reviewer_keys:
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
                final_action, rationale, reviewer_count, unanimous
            ) VALUES (?, ?, ?, ?, ?, 2, ?)
        """, [
            str(uuid.uuid4()), decision_id, adjudicator, action, rationale,
            unanimous,
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


def clinical_review_agreement(con):
    """Measure raw agreement and Cohen's kappa for completed review pairs."""
    ensure_governance_tables(con)
    rows = con.execute("""
        SELECT d.mapping_decision_id, d.target_table, r.verdict
        FROM mapping_decision d
        JOIN clinical_mapping_review r
          ON r.mapping_decision_id = d.mapping_decision_id
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
        total_votes = 2 * len(pairs)
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
        SELECT target_table, source_value, assigned_concept_id, run_id
        FROM mapping_decision WHERE mapping_decision_id = ?
    """, [decision_id]).fetchone()
    if not row:
        raise ValueError(f"Unknown mapping decision: {decision_id}")
    target_table, source_value, concept_id, run_id = row
    source_vocabulary, target_vocabulary, expected_domain = TARGET_GOVERNANCE[target_table]
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
        con.execute("""
            UPDATE mapping_decision
            SET status = ?, reviewer = ?, review_reason = ?, reviewed_at = now()
            WHERE mapping_decision_id = ? AND status IN ('PENDING', 'LOW_CONFIDENCE')
        """, [new_status, reviewer, reason, decision_id])
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
            """, [source_value, source_vocabulary])
            con.execute("""
                INSERT INTO source_to_concept_map (
                    source_code, source_concept_id, source_vocabulary_id,
                    source_code_description, target_concept_id,
                    target_vocabulary_id, valid_start_date, valid_end_date,
                    invalid_reason
                ) VALUES (?, 0, ?, ?, ?, ?, CURRENT_DATE, '2099-12-31', NULL)
            """, [
                source_value, source_vocabulary, source_value,
                int(concept_id), target_vocabulary,
            ])
            con.execute("""
                UPDATE mapping_rejection_policy SET active = FALSE
                WHERE target_table = ? AND source_value = ?
                  AND assigned_concept_id = ?
            """, [target_table, source_value, int(concept_id)])
        else:
            con.execute("""
                INSERT INTO mapping_rejection_policy (
                    target_table, source_value, assigned_concept_id,
                    reviewer, reason
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (target_table, source_value, assigned_concept_id)
                DO UPDATE SET reviewer = EXCLUDED.reviewer,
                              reason = EXCLUDED.reason,
                              rejected_at = now(), active = TRUE
            """, [target_table, source_value, int(concept_id), reviewer, reason])
            con.execute("""
                UPDATE approved_mapping_set SET active = FALSE
                WHERE target_table = ? AND source_value = ?
                  AND assigned_concept_id = ?
            """, [target_table, source_value, int(concept_id)])
            con.execute("""
                DELETE FROM source_to_concept_map
                WHERE source_code = ? AND source_vocabulary_id = ?
                  AND target_concept_id = ?
            """, [source_value, source_vocabulary, int(concept_id)])
        if manage_transaction:
            con.execute("COMMIT")
    except Exception:
        if manage_transaction:
            con.execute("ROLLBACK")
        raise
    return new_status
