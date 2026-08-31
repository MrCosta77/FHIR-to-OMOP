from dataclasses import replace
from pathlib import Path

import duckdb
import pytest

from src.adapters.event_binding import (
    EventBindingError,
    bind_pre_ingestion_decision,
)
from src.adapters.hospital_csv import load_hospital_csv
from src.adapters.source_identity import (
    claim_hospital_csv_identity,
    deactivate_source_system,
    register_source_system,
    resolve_source_identity,
)
from src.etl.apply_stcm import apply_stcm_mappings
from src.mapping.governance import (
    adjudicate_mapping_decision,
    blinded_review_queue,
    bootstrap_identity_administrator,
    ensure_governance_tables,
    register_decision,
    register_governed_actor,
    rejection_policy_exists,
    submit_blinded_review,
)


FIXTURE = Path(__file__).parent / "fixtures" / "hospital_csv" / "golden_hospital.csv"


def _clinical_tables(con):
    definitions = {
        "condition_occurrence": (
            "condition_occurrence_id", "condition_concept_id",
            "condition_source_concept_id", "condition_source_value",
        ),
        "drug_exposure": (
            "drug_exposure_id", "drug_concept_id",
            "drug_source_concept_id", "drug_source_value",
        ),
        "measurement": (
            "measurement_id", "measurement_concept_id",
            "measurement_source_concept_id", "measurement_source_value",
        ),
        "observation": (
            "observation_id", "observation_concept_id",
            "observation_source_concept_id", "observation_source_value",
        ),
        "procedure_occurrence": (
            "procedure_occurrence_id", "procedure_concept_id",
            "procedure_source_concept_id", "procedure_source_value",
        ),
        "device_exposure": (
            "device_exposure_id", "device_concept_id",
            "device_source_concept_id", "device_source_value",
        ),
    }
    for table, columns in definitions.items():
        con.execute(f"""
            CREATE TABLE {table} (
                {columns[0]} BIGINT, {columns[1]} INTEGER,
                {columns[2]} INTEGER, {columns[3]} VARCHAR
            )
        """)


def _database(path):
    with duckdb.connect(str(path)) as con:
        ensure_governance_tables(con)
        bootstrap_identity_administrator(
            con, "Test Identity Administrator",
            "One-time event-binding test identity bootstrap.",
        )
        for name, roles in (
            ("Reviewer One", {"reviewer"}),
            ("Reviewer Two", {"reviewer"}),
            ("Adjudicator", {"adjudicator"}),
        ):
            register_governed_actor(
                con, name, roles, "Test Identity Administrator",
                "Explicit governed event-binding test identity.",
                confirm_distinct=True,
            )
        con.execute(
            "CREATE TABLE vocabulary (vocabulary_id VARCHAR, vocabulary_version VARCHAR)"
        )
        con.execute("INSERT INTO vocabulary VALUES ('None', 'v-test')")
        con.execute("""
            CREATE TABLE concept (
                concept_id INTEGER, vocabulary_id VARCHAR, domain_id VARCHAR,
                standard_concept VARCHAR, invalid_reason VARCHAR,
                valid_start_date VARCHAR, valid_end_date VARCHAR
            )
        """)
        con.execute("""
            INSERT INTO concept VALUES
            (300, 'LOINC', 'Measurement', 'S', NULL, '20200101', '20991231')
        """)
        con.execute("""
            CREATE TABLE source_to_concept_map (
                source_code VARCHAR, source_concept_id INTEGER,
                source_vocabulary_id VARCHAR, source_code_description VARCHAR,
                target_concept_id INTEGER, target_vocabulary_id VARCHAR,
                valid_start_date DATE, valid_end_date DATE, invalid_reason VARCHAR
            )
        """)
        _clinical_tables(con)


def _identity_and_decision(con, *, record=None, vocabulary="CMF_HOSP_LIS", status="PRE_INGESTION"):
    record = record or load_hospital_csv(FIXTURE)[0]
    claim = claim_hospital_csv_identity(record)
    register_source_system(
        con,
        claim.source_adapter,
        claim.source_system,
        vocabulary,
        actor="Source Administrator",
        reason="Approved test source",
    )
    identity = resolve_source_identity(con, claim)
    decision_id = register_decision(
        con,
        claim.target_table,
        record.source_value,
        300,
        "Glucose measurement",
        "llm_rag_json",
        0.94,
        "test-model",
        "v-test",
        status,
        run_id="RUN-binding",
        llm_decision="SELECT",
        llm_confidence=0.94,
        source_adapter=claim.source_adapter,
        source_record_key=claim.source_record_key,
        publication_eligible=False,
    )
    con.execute("""
        INSERT INTO mapping_provenance (
            target_table, target_id, source_value, normalized_value,
            assigned_concept_id, mapping_method, score, model_name,
            vocabulary_version, reviewed_by, run_id, mapping_decision_id,
            source_adapter, source_record_key, publication_eligible
        ) VALUES (?, ?, ?, 'Glucose measurement', 300, 'llm_rag_json',
                  0.94, 'test-model', 'v-test', ?, 'RUN-binding', ?, ?, ?, FALSE)
    """, [
        claim.target_table,
        -int(claim.source_record_key[:15], 16),
        record.source_value,
        (
            "Pre_Ingestion_Proposal"
            if status == "PRE_INGESTION"
            else "Pre_Ingestion_Low_Confidence"
        ),
        decision_id,
        claim.source_adapter,
        claim.source_record_key,
    ])
    return identity, decision_id


def _bind(con, identity, decision_id, target_id=101):
    return bind_pre_ingestion_decision(
        con,
        identity,
        decision_id,
        target_id,
        actor="Source Administrator",
        reason="Verified ingestion event",
    )


def _approve(con, decision_id):
    submit_blinded_review(con, decision_id, "APPROVE", "Reviewer One", "Verified")
    submit_blinded_review(con, decision_id, "APPROVE", "Reviewer Two", "Verified")
    return adjudicate_mapping_decision(
        con, decision_id, "APPROVE", "Adjudicator", "Final verification"
    )


def test_binding_promotes_exact_event_atomically_to_existing_review_queue(
    tmp_path, monkeypatch
):
    database = tmp_path / "binding.duckdb"
    _database(database)
    with duckdb.connect(str(database)) as con:
        con.execute("INSERT INTO measurement VALUES (101, 0, 0, 'GLU_BLD')")
        identity, decision_id = _identity_and_decision(con)

        binding = _bind(con, identity, decision_id)
        repeated = _bind(con, identity, decision_id)

        assert repeated == binding
        assert binding.decision_status == "PENDING"
        assert [row["mapping_decision_id"] for row in blinded_review_queue(
            con, "Reviewer One"
        )] == [decision_id]
        assert con.execute("""
            SELECT status, source_system, source_vocabulary_id, source_code,
                   publication_eligible FROM mapping_decision
        """).fetchone() == (
            "PENDING", "LIS_LOCAL", "CMF_HOSP_LIS", "GLU_BLD", True,
        )
        assert con.execute("""
            SELECT target_id, reviewed_by, publication_eligible
            FROM mapping_provenance
        """).fetchone() == (101, "Pending_Human_Review", True)
        assert con.execute("SELECT COUNT(*) FROM source_event_binding").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM source_to_concept_map").fetchone()[0] == 0

    atomic_database = tmp_path / "atomic-binding.duckdb"
    _database(atomic_database)
    with duckdb.connect(str(atomic_database)) as con:
        con.execute("INSERT INTO measurement VALUES (101, 0, 0, 'GLU_BLD')")
        identity, decision_id = _identity_and_decision(con)

        def fail_audit(*_args, **_kwargs):
            raise RuntimeError("audit unavailable")

        monkeypatch.setattr(
            "src.adapters.event_binding.audit_security_event", fail_audit
        )
        with pytest.raises(RuntimeError, match="audit unavailable"):
            _bind(con, identity, decision_id)
        assert con.execute("SELECT COUNT(*) FROM source_event_binding").fetchone()[0] == 0
        assert con.execute("""
            SELECT status, publication_eligible FROM mapping_decision
        """).fetchone() == ("PRE_INGESTION", False)


def test_approval_publishes_explicit_local_identity_and_maps_only_bound_event(tmp_path):
    database = tmp_path / "approval.duckdb"
    _database(database)
    with duckdb.connect(str(database)) as con:
        con.execute("""
            INSERT INTO measurement VALUES
            (101, 0, 0, 'GLU_BLD'), (102, 0, 0, 'GLU_BLD')
        """)
        identity, decision_id = _identity_and_decision(con)
        _bind(con, identity, decision_id)

        assert _approve(con, decision_id) == "APPROVED"
        assert con.execute("""
            SELECT source_code, source_vocabulary_id, target_concept_id
            FROM source_to_concept_map
        """).fetchone() == ("GLU_BLD", "CMF_HOSP_LIS", 300)
        assert con.execute("""
            SELECT source_vocabulary_id, source_code, source_value,
                   assigned_concept_id
            FROM scoped_approved_mapping_set
        """).fetchone() == (
            "CMF_HOSP_LIS", "GLU_BLD", "glicose sangue", 300,
        )
        assert con.execute("SELECT COUNT(*) FROM approved_mapping_set").fetchone()[0] == 0

    apply_stcm_mappings(database)
    with duckdb.connect(str(database), read_only=True) as con:
        assert con.execute("""
            SELECT measurement_id, measurement_concept_id
            FROM measurement ORDER BY measurement_id
        """).fetchall() == [(101, 300), (102, 0)]


@pytest.mark.parametrize(
    ("event", "message"),
    [
        ((101, 0, 0, "WRONG_CODE"), "does not exactly match"),
        ((101, 300, 0, "GLU_BLD"), "already mapped"),
        ((101, 0, 123, "GLU_BLD"), "already has a source concept"),
    ],
)
def test_binding_rejects_wrong_or_already_mapped_event_and_rolls_back(
    tmp_path, event, message
):
    database = tmp_path / "invalid-event.duckdb"
    _database(database)
    with duckdb.connect(str(database)) as con:
        con.execute("INSERT INTO measurement VALUES (?, ?, ?, ?)", event)
        identity, decision_id = _identity_and_decision(con)

        with pytest.raises(EventBindingError, match=message):
            _bind(con, identity, decision_id)

        assert con.execute("SELECT COUNT(*) FROM source_event_binding").fetchone()[0] == 0
        assert con.execute("""
            SELECT status, publication_eligible FROM mapping_decision
        """).fetchone() == ("PRE_INGESTION", False)


def test_stale_registry_and_abstention_cannot_be_bound(tmp_path):
    database = tmp_path / "stale.duckdb"
    _database(database)
    with duckdb.connect(str(database)) as con:
        con.execute("INSERT INTO measurement VALUES (101, 0, 0, 'GLU_BLD')")
        identity, decision_id = _identity_and_decision(con)
        deactivate_source_system(
            con,
            "hospital-csv-v1",
            "LIS_LOCAL",
            actor="Source Administrator",
            reason="Feed retired",
        )
        with pytest.raises(ValueError, match="not registered|stale"):
            _bind(con, identity, decision_id)

        con.execute("UPDATE mapping_decision SET status = 'ABSTAINED', llm_decision = 'ABSTAIN'")
        register_source_system(
            con,
            "hospital-csv-v1",
            "LIS_LOCAL",
            "CMF_HOSP_LIS",
            actor="Source Administrator",
            reason="Feed restored",
        )
        identity = resolve_source_identity(con, identity.claim)
        with pytest.raises(EventBindingError, match="not a bindable"):
            _bind(con, identity, decision_id)


def test_adjudication_revalidates_event_and_registry_after_binding(tmp_path):
    database = tmp_path / "revalidation.duckdb"
    _database(database)
    with duckdb.connect(str(database)) as con:
        con.execute("INSERT INTO measurement VALUES (101, 0, 0, 'GLU_BLD')")
        identity, decision_id = _identity_and_decision(con)
        _bind(con, identity, decision_id)
        submit_blinded_review(con, decision_id, "APPROVE", "Reviewer One", "Verified")
        submit_blinded_review(con, decision_id, "APPROVE", "Reviewer Two", "Verified")
        con.execute("UPDATE measurement SET measurement_source_value = 'CHANGED'")

        with pytest.raises(ValueError, match="source code has changed"):
            adjudicate_mapping_decision(
                con, decision_id, "APPROVE", "Adjudicator", "Final verification"
            )
        assert con.execute("SELECT COUNT(*) FROM source_to_concept_map").fetchone()[0] == 0
        con.execute("UPDATE measurement SET measurement_source_value = 'GLU_BLD'")
        deactivate_source_system(
            con,
            "hospital-csv-v1",
            "LIS_LOCAL",
            actor="Source Administrator",
            reason="Feed retired before adjudication",
        )
        with pytest.raises(ValueError, match="no longer active"):
            adjudicate_mapping_decision(
                con, decision_id, "APPROVE", "Adjudicator", "Final verification"
            )


def test_hospital_rejection_is_scoped_and_never_writes_legacy_policy(tmp_path):
    database = tmp_path / "rejection.duckdb"
    _database(database)
    with duckdb.connect(str(database)) as con:
        con.execute("INSERT INTO measurement VALUES (101, 0, 0, 'GLU_BLD')")
        identity, decision_id = _identity_and_decision(con)
        _bind(con, identity, decision_id)
        submit_blinded_review(con, decision_id, "REJECT", "Reviewer One", "Unsafe")
        submit_blinded_review(con, decision_id, "REJECT", "Reviewer Two", "Unsafe")

        assert adjudicate_mapping_decision(
            con, decision_id, "REJECT", "Adjudicator", "Rejected clinically"
        ) == "REJECTED"
        assert rejection_policy_exists(
            con,
            "measurement",
            "glicose sangue",
            300,
            source_vocabulary_id="CMF_HOSP_LIS",
            source_code="GLU_BLD",
        )
        assert not rejection_policy_exists(
            con,
            "measurement",
            "glicose sangue",
            300,
            source_vocabulary_id="CMF_HOSP_LIS2",
            source_code="GLU_BLD",
        )
        assert con.execute("SELECT COUNT(*) FROM mapping_rejection_policy").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM source_to_concept_map").fetchone()[0] == 0

        next_record = replace(
            load_hospital_csv(FIXTURE)[0], record_id="lab-002"
        )
        con.execute("INSERT INTO measurement VALUES (102, 0, 0, 'GLU_BLD')")
        next_identity, next_decision_id = _identity_and_decision(
            con, record=next_record
        )
        with pytest.raises(EventBindingError, match="rejection policy"):
            _bind(con, next_identity, next_decision_id, target_id=102)
        assert con.execute("""
            SELECT status, publication_eligible FROM mapping_decision
            WHERE mapping_decision_id = ?
        """, [next_decision_id]).fetchone() == ("PRE_INGESTION", False)


def test_scoped_policy_allows_same_code_in_different_local_vocabularies(tmp_path):
    database = tmp_path / "scoped.duckdb"
    _database(database)
    with duckdb.connect(str(database)) as con:
        con.execute("""
            INSERT INTO scoped_approved_mapping_set (
                target_table, source_vocabulary_id, source_code, source_value,
                assigned_concept_id, mapping_decision_id, reviewer
            ) VALUES
            ('measurement', 'CMF_HOSP_LIS', 'SHARED', 'display one', 300, 'd1', 'a'),
            ('measurement', 'CMF_HOSP_LIS2', 'SHARED', 'display two', 300, 'd2', 'a')
        """)
        assert con.execute("""
            SELECT COUNT(*) FROM scoped_approved_mapping_set
            WHERE source_code = 'SHARED'
        """).fetchone()[0] == 2
