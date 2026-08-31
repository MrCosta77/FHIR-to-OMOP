from __future__ import annotations

from .core import TARGET_GOVERNANCE, decision_id_for

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
        ("source_adapter", "VARCHAR"),
        ("source_record_key", "VARCHAR"),
        ("source_system", "VARCHAR"),
        ("source_code", "VARCHAR"),
        ("source_vocabulary_id", "VARCHAR"),
        ("publication_eligible", "BOOLEAN DEFAULT TRUE"),
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
        ("source_adapter", "VARCHAR"),
        ("source_record_key", "VARCHAR"),
        ("source_system", "VARCHAR"),
        ("source_code", "VARCHAR"),
        ("source_vocabulary_id", "VARCHAR"),
        ("publication_eligible", "BOOLEAN DEFAULT TRUE"),
        ("proposed_by", "VARCHAR"),
        ("proposal_rationale", "VARCHAR"),
        ("supersedes_decision_id", "VARCHAR"),
        ("proposed_by_actor_id", "VARCHAR"),
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
        CREATE TABLE IF NOT EXISTS scoped_mapping_rejection_policy (
            target_table VARCHAR NOT NULL,
            source_vocabulary_id VARCHAR NOT NULL,
            source_code VARCHAR NOT NULL,
            source_value VARCHAR NOT NULL,
            assigned_concept_id INTEGER NOT NULL,
            reviewer VARCHAR NOT NULL,
            reason VARCHAR,
            rejected_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            PRIMARY KEY (
                target_table, source_vocabulary_id, source_code,
                assigned_concept_id
            )
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS scoped_approved_mapping_set (
            target_table VARCHAR NOT NULL,
            source_vocabulary_id VARCHAR NOT NULL,
            source_code VARCHAR NOT NULL,
            source_value VARCHAR NOT NULL,
            assigned_concept_id INTEGER NOT NULL,
            mapping_decision_id VARCHAR NOT NULL,
            approved_run_id VARCHAR,
            reviewer VARCHAR NOT NULL,
            reason VARCHAR,
            approved_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            PRIMARY KEY (target_table, source_vocabulary_id, source_code)
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
    for name, datatype in (
        ("reviewer_key", "VARCHAR"),
        ("active", "BOOLEAN DEFAULT TRUE"),
        ("invalidated_at", "TIMESTAMP"),
        ("invalidated_by", "VARCHAR"),
        ("invalidation_reason", "VARCHAR"),
        ("reviewer_actor_id", "VARCHAR"),
    ):
        _add_column(con, "clinical_mapping_review", name, datatype)
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
    _add_column(
        con, "clinical_mapping_adjudication", "adjudicator_actor_id", "VARCHAR"
    )
    con.execute("""
        CREATE TABLE IF NOT EXISTS governed_actor (
            actor_id VARCHAR PRIMARY KEY,
            display_name VARCHAR NOT NULL,
            canonical_name VARCHAR NOT NULL UNIQUE,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            registered_by VARCHAR NOT NULL,
            registration_reason VARCHAR NOT NULL,
            registered_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            deactivated_at TIMESTAMP,
            deactivated_by VARCHAR,
            deactivation_reason VARCHAR
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS governed_actor_alias (
            alias_key VARCHAR PRIMARY KEY,
            actor_id VARCHAR NOT NULL,
            alias_name VARCHAR NOT NULL,
            source VARCHAR NOT NULL,
            approved_by VARCHAR NOT NULL,
            approval_reason VARCHAR NOT NULL,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            approved_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS governed_actor_role (
            actor_id VARCHAR NOT NULL,
            role VARCHAR NOT NULL,
            granted_by VARCHAR NOT NULL,
            grant_reason VARCHAR NOT NULL,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            granted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (actor_id, role)
        )
    """)
    _migrate_governed_actors(con)
    _normalize_and_deduplicate_reviews(con)
    con.execute("""
        CREATE TABLE IF NOT EXISTS source_identity_registry (
            source_adapter VARCHAR NOT NULL,
            source_system VARCHAR NOT NULL,
            source_vocabulary_id VARCHAR NOT NULL,
            registered_by VARCHAR NOT NULL,
            registration_reason VARCHAR NOT NULL,
            registered_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            deactivated_by VARCHAR,
            deactivation_reason VARCHAR,
            deactivated_at TIMESTAMP,
            PRIMARY KEY (
                source_adapter, source_system, source_vocabulary_id
            )
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS source_event_binding (
            binding_id VARCHAR PRIMARY KEY,
            source_adapter VARCHAR NOT NULL,
            source_record_key VARCHAR NOT NULL UNIQUE,
            source_system VARCHAR NOT NULL,
            source_vocabulary_id VARCHAR NOT NULL,
            source_code VARCHAR NOT NULL,
            target_table VARCHAR NOT NULL,
            target_id BIGINT NOT NULL,
            mapping_decision_id VARCHAR NOT NULL UNIQUE,
            bound_by VARCHAR NOT NULL,
            binding_reason VARCHAR NOT NULL,
            bound_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            ingestion_run_id VARCHAR,
            input_manifest_sha256 VARCHAR,
            handoff_batch_id VARCHAR,
            UNIQUE (target_table, target_id)
        )
    """)
    for name, datatype in (
        ("ingestion_run_id", "VARCHAR"),
        ("input_manifest_sha256", "VARCHAR"),
        ("handoff_batch_id", "VARCHAR"),
    ):
        _add_column(con, "source_event_binding", name, datatype)
    _copy_legacy_publication_policy(con)
    _migrate_legacy_decisions(con)


def _normalize_and_deduplicate_reviews(con):
    """Keep the earliest vote per person and semantic proposal active."""
    from src.security.privacy import canonical_actor_key

    missing_keys = con.execute("""
        SELECT review_id, reviewer FROM clinical_mapping_review
        WHERE reviewer_key IS NULL OR TRIM(reviewer_key) = ''
    """).fetchall()
    for review_id, reviewer in missing_keys:
        con.execute("""
            UPDATE clinical_mapping_review SET reviewer_key = ?
            WHERE review_id = ?
        """, [canonical_actor_key(reviewer), review_id])

    rows = con.execute("""
        SELECT r.review_id,
               COALESCE(r.reviewer_actor_id, r.reviewer_key), d.target_table,
               COALESCE(d.source_adapter, ''),
               COALESCE(d.source_vocabulary_id, ''),
               COALESCE(d.source_code, ''), LOWER(TRIM(d.source_value)),
               d.assigned_concept_id
        FROM clinical_mapping_review r
        JOIN mapping_decision d USING (mapping_decision_id)
        WHERE COALESCE(r.active, TRUE)
        ORDER BY r.submitted_at, r.review_id
    """).fetchall()
    seen = set()
    redundant = []
    for row in rows:
        semantic_actor = tuple(row[1:])
        if semantic_actor in seen:
            redundant.append(row[0])
        else:
            seen.add(semantic_actor)
    if redundant:
        con.execute("""
            UPDATE clinical_mapping_review
            SET active = FALSE, invalidated_at = now(),
                invalidated_by = 'SYSTEM_IDENTITY_DEDUP',
                invalidation_reason =
                    'Duplicate vote by the same normalized actor on a semantic duplicate'
            WHERE review_id IN (SELECT UNNEST(?::VARCHAR[]))
        """, [redundant])


def _migrate_governed_actors(con):
    """Assign deterministic actor IDs to existing governed actions."""
    import uuid

    from src.security.privacy import canonical_actor_key

    identities = []
    identities.extend(
        (name, "reviewer")
        for (name,) in con.execute("""
            SELECT DISTINCT reviewer FROM clinical_mapping_review
            WHERE reviewer IS NOT NULL AND TRIM(reviewer) <> ''
        """).fetchall()
    )
    identities.extend(
        (name, "adjudicator")
        for (name,) in con.execute("""
            SELECT DISTINCT adjudicator FROM clinical_mapping_adjudication
            WHERE adjudicator IS NOT NULL AND TRIM(adjudicator) <> ''
        """).fetchall()
    )
    identities.extend(
        (name, "reviewer")
        for (name,) in con.execute("""
            SELECT DISTINCT proposed_by FROM mapping_decision
            WHERE proposed_by IS NOT NULL AND TRIM(proposed_by) <> ''
        """).fetchall()
    )
    actor_ids = {}
    for display_name, role in identities:
        key = canonical_actor_key(display_name)
        actor_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"cmf:governed-actor:{key}")
        )
        actor_ids[key] = actor_id
        con.execute("""
            INSERT INTO governed_actor (
                actor_id, display_name, canonical_name, registered_by,
                registration_reason
            ) VALUES (?, ?, ?, 'SYSTEM_LEGACY_MIGRATION',
                      'Migrated from an existing governed action')
            ON CONFLICT DO NOTHING
        """, [actor_id, display_name, key])
        con.execute("""
            INSERT INTO governed_actor_alias (
                alias_key, actor_id, alias_name, source, approved_by,
                approval_reason
            ) VALUES (?, ?, ?, 'legacy_migration', 'SYSTEM_LEGACY_MIGRATION',
                      'Canonical alias migrated from governed history')
            ON CONFLICT DO NOTHING
        """, [key, actor_id, display_name])
        con.execute("""
            INSERT INTO governed_actor_role (
                actor_id, role, granted_by, grant_reason
            ) VALUES (?, ?, 'SYSTEM_LEGACY_MIGRATION',
                      'Role inferred from governed history')
            ON CONFLICT DO NOTHING
        """, [actor_id, role])

    for key, actor_id in actor_ids.items():
        con.execute("""
            UPDATE clinical_mapping_review SET reviewer_actor_id = ?
            WHERE reviewer_key = ? AND reviewer_actor_id IS NULL
        """, [actor_id, key])
        for review_id, reviewer in con.execute("""
            SELECT review_id, reviewer FROM clinical_mapping_review
            WHERE reviewer_actor_id IS NULL
        """).fetchall():
            if canonical_actor_key(reviewer) == key:
                con.execute("""
                    UPDATE clinical_mapping_review SET reviewer_actor_id = ?
                    WHERE review_id = ?
                """, [actor_id, review_id])
        for adjudication_id, adjudicator in con.execute("""
            SELECT adjudication_id, adjudicator
            FROM clinical_mapping_adjudication
            WHERE adjudicator_actor_id IS NULL
        """).fetchall():
            if canonical_actor_key(adjudicator) == key:
                con.execute("""
                    UPDATE clinical_mapping_adjudication
                    SET adjudicator_actor_id = ? WHERE adjudication_id = ?
                """, [actor_id, adjudication_id])
        for decision_id, proposer in con.execute("""
            SELECT mapping_decision_id, proposed_by FROM mapping_decision
            WHERE proposed_by_actor_id IS NULL AND proposed_by IS NOT NULL
        """).fetchall():
            if canonical_actor_key(proposer) == key:
                con.execute("""
                    UPDATE mapping_decision SET proposed_by_actor_id = ?
                    WHERE mapping_decision_id = ?
                """, [actor_id, decision_id])

def _copy_legacy_publication_policy(con):
    """Non-destructively seed vocabulary-scoped policy from legacy tables."""
    for target_table, (source_vocabulary_id, _target_vocab, _domain) in (
        TARGET_GOVERNANCE.items()
    ):
        con.execute("""
            INSERT INTO scoped_mapping_rejection_policy (
                target_table, source_vocabulary_id, source_code, source_value,
                assigned_concept_id, reviewer, reason, rejected_at, active
            )
            SELECT target_table, ?, source_value, source_value,
                   assigned_concept_id, reviewer, reason, rejected_at, active
            FROM mapping_rejection_policy
            WHERE target_table = ?
            ON CONFLICT DO NOTHING
        """, [source_vocabulary_id, target_table])
        con.execute("""
            INSERT INTO scoped_approved_mapping_set (
                target_table, source_vocabulary_id, source_code, source_value,
                assigned_concept_id, mapping_decision_id, approved_run_id,
                reviewer, reason, approved_at, active
            )
            SELECT target_table, ?, source_value, source_value,
                   assigned_concept_id, mapping_decision_id, approved_run_id,
                   reviewer, reason, approved_at, active
            FROM approved_mapping_set
            WHERE target_table = ?
            ON CONFLICT DO NOTHING
        """, [source_vocabulary_id, target_table])

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
