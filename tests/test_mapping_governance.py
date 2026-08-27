import duckdb

from src.mapping.governance import (
    adjudicate_mapping_decision,
    blinded_adjudication_queue,
    blinded_review_queue,
    clinical_review_agreement,
    ensure_governance_tables,
    register_decision,
    rejection_policy_exists,
    review_mapping_decision,
    submit_blinded_review,
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
            concept_id INTEGER, vocabulary_id VARCHAR, domain_id VARCHAR,
            standard_concept VARCHAR,
            invalid_reason VARCHAR, valid_start_date VARCHAR,
            valid_end_date VARCHAR
        )
    """)
    con.execute("""
        INSERT INTO concept VALUES
        (300, 'LOINC', 'Measurement', 'S', NULL, '20200101', '20991231'),
        (301, 'SNOMED', 'Condition', 'S', NULL, '20200101', '20991231'),
        (302, 'SNOMED', 'Device', 'S', NULL, '20200101', '20991231'),
        (303, 'LOINC', 'Observation', 'S', NULL, '20200101', '20991231')
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


def _adjudicate(
    con, decision_id, final_action, *, first="APPROVE", second="APPROVE"
):
    submit_blinded_review(
        con, decision_id, first, "Reviewer One", "Independent rationale one"
    )
    submit_blinded_review(
        con, decision_id, second, "Reviewer Two", "Independent rationale two"
    )
    return adjudicate_mapping_decision(
        con, decision_id, final_action, "Clinical Adjudicator", "Final rationale"
    )


def test_adjudication_is_the_only_operation_that_publishes_stcm():
    with duckdb.connect(":memory:") as con:
        ensure_governance_tables(con)
        _create_stcm(con)
        _create_concepts(con)
        decision_id = _proposal(con)

        assert con.execute("SELECT COUNT(*) FROM source_to_concept_map").fetchone()[0] == 0
        submit_blinded_review(
            con, decision_id, "APPROVE", "Reviewer One", "Clinically verified"
        )
        assert con.execute("SELECT COUNT(*) FROM source_to_concept_map").fetchone()[0] == 0
        submit_blinded_review(
            con, decision_id, "APPROVE", "Reviewer Two", "Clinically verified"
        )
        assert con.execute("SELECT COUNT(*) FROM source_to_concept_map").fetchone()[0] == 0
        adjudicate_mapping_decision(
            con, decision_id, "APPROVE", "Clinical Adjudicator", "Clinically verified"
        )

        assert con.execute("""
            SELECT source_vocabulary_id, target_concept_id
            FROM source_to_concept_map
        """).fetchall() == [("CMF_SYNTHEA_MEASUREMENT", 300)]
        assert con.execute("""
            SELECT status, reviewer, review_reason FROM mapping_decision
        """).fetchone() == ("APPROVED", "Clinical Adjudicator", "Clinically verified")
        assert con.execute("""
            SELECT run_id, reviewed_by FROM mapping_provenance
        """).fetchone() == ("RUN-test", "Approved_by_Human")


def test_rejection_persists_policy_and_never_publishes():
    with duckdb.connect(":memory:") as con:
        ensure_governance_tables(con)
        _create_stcm(con)
        _create_concepts(con)
        decision_id = _proposal(con)

        _adjudicate(
            con, decision_id, "REJECT", first="APPROVE", second="REJECT"
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
            _adjudicate(con, decision_id, "APPROVE")
        except ValueError as exc:
            assert "Standard Measurement" in str(exc)
        else:
            raise AssertionError("Cross-domain approval was accepted")


def test_device_approval_publishes_only_to_the_device_source_vocabulary():
    with duckdb.connect(":memory:") as con:
        ensure_governance_tables(con)
        _create_stcm(con)
        _create_concepts(con)
        decision_id = register_decision(
            con, "device_exposure", "Legacy implant", 302, "Implant",
            "llm_rag_json", 0.95, "test-model", "v-test", "PENDING",
            run_id="RUN-device", prompt_version="mapping-json-v2",
            llm_decision="SELECT", llm_confidence=0.95,
        )

        _adjudicate(con, decision_id, "APPROVE")

        assert con.execute("""
            SELECT source_vocabulary_id, target_vocabulary_id,
                   target_concept_id
            FROM source_to_concept_map
        """).fetchone() == ("CMF_SYNTHEA_DEVICE", "SNOMED", 302)


def test_observation_approval_preserves_the_concepts_actual_vocabulary():
    with duckdb.connect(":memory:") as con:
        ensure_governance_tables(con)
        _create_stcm(con)
        _create_concepts(con)
        decision_id = register_decision(
            con, "observation", "Legacy observation", 303, "Observation",
            "llm_rag_json", 0.95, "test-model", "v-test", "PENDING",
            run_id="RUN-observation", prompt_version="mapping-json-v2",
            llm_decision="SELECT", llm_confidence=0.95,
        )

        _adjudicate(con, decision_id, "APPROVE")

        assert con.execute("""
            SELECT source_vocabulary_id, target_vocabulary_id,
                   target_concept_id
            FROM source_to_concept_map
        """).fetchone() == ("CMF_SYNTHEA_OBSERVATION", "LOINC", 303)


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


def test_retired_single_review_path_is_blocked():
    with duckdb.connect(":memory:") as con:
        ensure_governance_tables(con)
        decision_id = _proposal(con)
        try:
            review_mapping_decision(
                con, decision_id, "APPROVE", "Only Reviewer", "Not enough"
            )
        except ValueError as exc:
            assert "two blinded reviews" in str(exc)
        else:
            raise AssertionError("Single-review publication was accepted")


def test_review_and_adjudication_queues_remain_blind():
    with duckdb.connect(":memory:") as con:
        ensure_governance_tables(con)
        decision_id = _proposal(con)

        queue = blinded_review_queue(con, "Reviewer One")
        assert [row["mapping_decision_id"] for row in queue] == [decision_id]
        assert "reviewer" not in queue[0]
        assert "verdict" not in queue[0]

        submit_blinded_review(
            con, decision_id, "APPROVE", "Reviewer One", "First rationale"
        )
        assert blinded_review_queue(con, "Reviewer One") == []
        assert len(blinded_review_queue(con, "Reviewer Two")) == 1
        submit_blinded_review(
            con, decision_id, "REJECT", "Reviewer Two", "Second rationale"
        )
        assert blinded_review_queue(con, "Reviewer Three") == []
        assert blinded_adjudication_queue(con, "Reviewer One") == []
        adjudication = blinded_adjudication_queue(con, "Clinical Adjudicator")
        assert [row["mapping_decision_id"] for row in adjudication] == [decision_id]
        assert "reviewer" not in adjudication[0]
        assert "verdict" not in adjudication[0]


def test_reviewers_and_adjudicator_must_be_distinct_and_rationales_are_required():
    with duckdb.connect(":memory:") as con:
        ensure_governance_tables(con)
        decision_id = _proposal(con)
        try:
            submit_blinded_review(con, decision_id, "APPROVE", "Reviewer One", "")
        except ValueError as exc:
            assert "rationale" in str(exc)
        else:
            raise AssertionError("Empty rationale was accepted")
        submit_blinded_review(
            con, decision_id, "APPROVE", "Reviewer One", "First rationale"
        )
        try:
            submit_blinded_review(
                con, decision_id, "APPROVE", " reviewer one ", "Duplicate"
            )
        except ValueError as exc:
            assert "same reviewer" in str(exc)
        else:
            raise AssertionError("Duplicate reviewer was accepted")
        submit_blinded_review(
            con, decision_id, "APPROVE", "Reviewer Two", "Second rationale"
        )
        try:
            adjudicate_mapping_decision(
                con, decision_id, "APPROVE", "Reviewer One", "Self adjudication"
            )
        except ValueError as exc:
            assert "distinct" in str(exc)
        else:
            raise AssertionError("A reviewer adjudicated their own case")


def test_clinical_review_agreement_reports_raw_agreement_and_cohens_kappa():
    with duckdb.connect(":memory:") as con:
        ensure_governance_tables(con)
        patterns = [
            ("APPROVE", "APPROVE"),
            ("APPROVE", "REJECT"),
            ("REJECT", "REJECT"),
            ("REJECT", "APPROVE"),
        ]
        for index, (first, second) in enumerate(patterns):
            decision_id = _proposal(con, run_id=f"RUN-kappa-{index}")
            submit_blinded_review(
                con, decision_id, first, "Reviewer One", "First rationale"
            )
            submit_blinded_review(
                con, decision_id, second, "Reviewer Two", "Second rationale"
            )
        agreement = clinical_review_agreement(con)
        assert agreement["overall"] == {
            "pair_count": 4,
            "raw_agreement": 0.5,
            "cohens_kappa": 0.0,
            "approve_votes": 4,
            "reject_votes": 4,
        }
        assert agreement["by_domain"]["Measurement"]["pair_count"] == 4
