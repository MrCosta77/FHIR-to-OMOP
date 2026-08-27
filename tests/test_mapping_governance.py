import duckdb

from src.mapping.governance import (
    ensure_governance_tables,
    register_decision,
    rejection_policy_exists,
    review_mapping_decision,
)


def _create_stcm(con):
    con.execute("""
        CREATE TABLE source_to_concept_map (
            source_code VARCHAR, source_concept_id INTEGER,
            source_vocabulary_id VARCHAR, source_code_description VARCHAR,
            target_concept_id INTEGER, target_vocabulary_id VARCHAR,
            valid_start_date DATE, valid_end_date DATE, invalid_reason VARCHAR
        )
    """)


def _create_concepts(con):
    con.execute("""
        CREATE TABLE concept (
            concept_id INTEGER, domain_id VARCHAR, standard_concept VARCHAR,
            invalid_reason VARCHAR, valid_start_date VARCHAR,
            valid_end_date VARCHAR
        )
    """)
    con.execute("""
        INSERT INTO concept VALUES
        (300, 'Measurement', 'S', NULL, '20200101', '20991231'),
        (301, 'Condition', 'S', NULL, '20200101', '20991231')
    """)


def _proposal(con, concept_id=300, run_id="RUN-test"):
    decision_id = register_decision(
        con, "measurement", "Legacy", concept_id, "Candidate",
        "llm_rag_few_shot", 0.95, "test-model", "v-test", "PENDING",
        run_id=run_id,
    )
    con.execute("""
        INSERT INTO mapping_provenance (
            target_table, target_id, source_value, normalized_value,
            assigned_concept_id, mapping_method, score, model_name,
            vocabulary_version, reviewed_by, run_id, mapping_decision_id
        ) VALUES (
            'measurement', 1, 'Legacy', 'Candidate', ?,
            'llm_rag_few_shot', 0.95, 'test-model', 'v-test',
            'Pending_Human_Review', ?, ?
        )
    """, [concept_id, run_id, decision_id])
    return decision_id


def test_approval_is_the_only_operation_that_publishes_stcm():
    with duckdb.connect(":memory:") as con:
        ensure_governance_tables(con)
        _create_stcm(con)
        _create_concepts(con)
        decision_id = _proposal(con)

        assert con.execute("SELECT COUNT(*) FROM source_to_concept_map").fetchone()[0] == 0
        review_mapping_decision(
            con, decision_id, "APPROVE", "Mario Costa", "Clinically verified"
        )

        assert con.execute("""
            SELECT source_vocabulary_id, target_concept_id
            FROM source_to_concept_map
        """).fetchall() == [("CMF_SYNTHEA_MEASUREMENT", 300)]
        assert con.execute("""
            SELECT status, reviewer, review_reason FROM mapping_decision
        """).fetchone() == ("APPROVED", "Mario Costa", "Clinically verified")
        assert con.execute("""
            SELECT run_id, reviewed_by FROM mapping_provenance
        """).fetchone() == ("RUN-test", "Approved_by_Human")


def test_rejection_persists_policy_and_never_publishes():
    with duckdb.connect(":memory:") as con:
        ensure_governance_tables(con)
        _create_stcm(con)
        _create_concepts(con)
        decision_id = _proposal(con)

        review_mapping_decision(
            con, decision_id, "REJECT", "Mario Costa", "Wrong clinical meaning"
        )

        assert rejection_policy_exists(con, "measurement", "Legacy", 300)
        assert con.execute("SELECT COUNT(*) FROM source_to_concept_map").fetchone()[0] == 0
        assert con.execute("SELECT status FROM mapping_decision").fetchone()[0] == "REJECTED"


def test_wrong_domain_cannot_be_approved():
    with duckdb.connect(":memory:") as con:
        ensure_governance_tables(con)
        _create_stcm(con)
        _create_concepts(con)
        decision_id = _proposal(con, concept_id=301)

        try:
            review_mapping_decision(con, decision_id, "APPROVE", "Reviewer")
        except ValueError as exc:
            assert "Standard Measurement" in str(exc)
        else:
            raise AssertionError("Cross-domain approval was accepted")


def test_legacy_pending_provenance_is_migrated_and_unpublished():
    with duckdb.connect(":memory:") as con:
        con.execute("CREATE SEQUENCE seq_provenance_id START 1")
        con.execute("""
            CREATE TABLE mapping_provenance (
                provenance_id BIGINT DEFAULT nextval('seq_provenance_id'),
                target_table VARCHAR, target_id BIGINT, source_value VARCHAR,
                normalized_value VARCHAR, assigned_concept_id INTEGER,
                mapping_method VARCHAR, score DOUBLE, model_name VARCHAR,
                vocabulary_version VARCHAR, reviewed_by VARCHAR,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        _create_stcm(con)
        con.execute("""
            INSERT INTO mapping_provenance (
                target_table, target_id, source_value, normalized_value,
                assigned_concept_id, mapping_method, score, model_name,
                vocabulary_version, reviewed_by
            ) VALUES (
                'measurement', 42, 'Legacy', 'Candidate', 300,
                'llm_rag_few_shot', 0.95, 'old-model', 'v-old',
                'Pending_Human_Review'
            )
        """)
        con.execute("""
            INSERT INTO source_to_concept_map VALUES (
                'Legacy', 0, 'CMF_SYNTHEA_MEASUREMENT', 'Legacy',
                300, 'LOINC', '2020-01-01', '2099-12-31', NULL
            )
        """)

        ensure_governance_tables(con)

        assert con.execute("""
            SELECT status, prompt_version FROM mapping_decision
        """).fetchone() == ("PENDING", "legacy-unversioned")
        assert con.execute("""
            SELECT mapping_decision_id IS NOT NULL FROM mapping_provenance
        """).fetchone()[0]
        assert con.execute("SELECT COUNT(*) FROM source_to_concept_map").fetchone()[0] == 0
