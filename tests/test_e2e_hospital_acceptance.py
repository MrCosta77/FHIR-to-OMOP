"""End-to-End Acceptance Test for Hospital CSV mapping workflow (7D.4D)."""

import json
from pathlib import Path

import duckdb
import pytest

from src.utils import setup_cdm_schema, setup_audit
from src.mapping import governance
from src.adapters import source_identity, event_binding, ingestion_handoff, hospital_csv, hospital_csv_mapping
from src.etl import apply_stcm
from src.adapters.ingestion_handoff import IngestionReceipt

FIXTURE_CSV = Path(__file__).parent / "fixtures" / "hospital_csv" / "e2e_hospital_6domain.csv"

class _E2EFakeCollection:
    def __init__(self, target_table):
        self.target_table = target_table
        self.metadata = {"distance_metric": "cosine", "index_signature": "e2e-index"}

    def count(self): return 1

    def query(self, query_texts, n_results):
        mapping = {
            "condition_occurrence": 9001,
            "drug_exposure": 9002,
            "measurement": 9003,
            "observation": 9004,
            "procedure_occurrence": 9005,
            "device_exposure": 9006,
        }
        return {
            "ids": [[str(mapping[self.target_table])]],
            "documents": [["E2E synthetic candidate"]],
            "distances": [[0.05]],
        }

class _E2EFakeOllama:
    def list(self):
        return {"models": [{"model": "llama3.2:3b", "digest": "sha256:e2e-model"}]}

    def chat(self, **kwargs):
        prompt = kwargs["messages"][0]["content"]

        concept_id = 0
        for cand in [9001, 9002, 9003, 9004, 9005, 9006]:
            if str(cand) in prompt:
                concept_id = cand
                break

        decision = "ABSTAIN"
        if any(token in prompt for token in ["Fever", "acetaminophen 500", "HBA1C_RATIO", "Current smoker", "Transthoracic echocardiography", "Implantable cardiac pacemaker"]):
            decision = "SELECT"

        return {"message": {"content": json.dumps({
            "decision": decision,
            "selected_concept_id": concept_id if decision == "SELECT" else None,
            "confidence": 0.95,
            "reason": "E2E testing reason.",
            "clinical_signals": ["e2e test signal"],
        })}}

@pytest.mark.integration
def test_e2e_hospital_acceptance(tmp_path, monkeypatch):
    database = tmp_path / "e2e.duckdb"
    
    # 1. Monkeypatch configuration
    monkeypatch.setattr("src.utils.config.DB_PATH", str(database))
    monkeypatch.setattr(setup_cdm_schema, "DB_PATH", str(database))
    monkeypatch.setattr(setup_audit, "DB_PATH", str(database))
    monkeypatch.setattr(apply_stcm, "DB_PATH", str(database))
    
    monkeypatch.setattr("src.utils.config.MODEL_NAME", "llama3.2:3b")
    monkeypatch.setattr(
        "src.adapters.hospital_csv_mapping.get_versioned_collection",
        lambda con, path, target: _E2EFakeCollection(target),
    )

    # 2. Setup database schema
    setup_cdm_schema.create_omop_skeleton()
    setup_audit.setup_audit_tables()

    with duckdb.connect(str(database)) as con:
        governance.ensure_governance_tables(con)
        
        # Insert vocabulary concepts
        concepts = [
            (9001, "E2E Fever", "Condition", "SNOMED", "Clinical Finding", "S", "C1"),
            (9002, "E2E Paracetamol", "Drug", "RxNorm", "Ingredient", "S", "D1"),
            (9003, "E2E HbA1c", "Measurement", "LOINC", "Clinical Observation", "S", "M1"),
            (9004, "E2E Ex-smoker", "Observation", "LOINC", "Clinical Observation", "S", "O1"),
            (9005, "E2E Echo", "Procedure", "SNOMED", "Procedure", "S", "P1"),
            (9006, "E2E Pacemaker", "Device", "SNOMED", "Device", "S", "V1"),
        ]
        con.executemany(
            """
            INSERT INTO concept (
                concept_id, concept_name, domain_id, vocabulary_id,
                concept_class_id, standard_concept, concept_code,
                valid_start_date, valid_end_date, invalid_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, DATE '2000-01-01', DATE '2099-12-31', NULL)
            """,
            concepts,
        )
        
        # Insert person
        con.execute(
            """
            INSERT INTO person (
                person_id, gender_concept_id, year_of_birth, month_of_birth, day_of_birth,
                race_concept_id, ethnicity_concept_id
            ) VALUES (1, 8507, 1980, 1, 1, 0, 0)
            """
        )
        
        # Insert unmapped OMOP events
        events = [
            ("condition_occurrence", "condition_occurrence_id", 101, "condition_concept_id", "condition_start_date", "condition_source_value", "FEVER"),
            ("drug_exposure", "drug_exposure_id", 201, "drug_concept_id", "drug_exposure_start_date", "drug_source_value", "ACETAMINOPHEN_500"),
            ("measurement", "measurement_id", 301, "measurement_concept_id", "measurement_date", "measurement_source_value", "HBA1C_RATIO"),
            ("observation", "observation_id", 401, "observation_concept_id", "observation_date", "observation_source_value", "CURRENT_SMOKER"),
            ("procedure_occurrence", "procedure_occurrence_id", 501, "procedure_concept_id", "procedure_date", "procedure_source_value", "TTE"),
            ("device_exposure", "device_exposure_id", 601, "device_concept_id", "device_exposure_start_date", "device_source_value", "CARDIAC_PACEMAKER"),
        ]
        
        for table, id_col, id_val, concept_col, date_col, src_val_col, src_val in events:
            if table == "condition_occurrence":
                con.execute(f"INSERT INTO {table} ({id_col}, person_id, {concept_col}, {date_col}, condition_type_concept_id, {src_val_col}) VALUES (?, 1, 0, '2026-06-01', 32020, ?)", (id_val, src_val))
            elif table == "drug_exposure":
                con.execute(f"INSERT INTO {table} ({id_col}, person_id, {concept_col}, {date_col}, drug_exposure_end_date, drug_type_concept_id, {src_val_col}) VALUES (?, 1, 0, '2026-06-01', '2026-06-02', 32020, ?)", (id_val, src_val))
            elif table == "measurement":
                con.execute(f"INSERT INTO {table} ({id_col}, person_id, {concept_col}, {date_col}, measurement_type_concept_id, {src_val_col}) VALUES (?, 1, 0, '2026-06-01', 32020, ?)", (id_val, src_val))
            elif table == "observation":
                con.execute(f"INSERT INTO {table} ({id_col}, person_id, {concept_col}, {date_col}, observation_type_concept_id, {src_val_col}) VALUES (?, 1, 0, '2026-06-01', 32020, ?)", (id_val, src_val))
            elif table == "procedure_occurrence":
                con.execute(f"INSERT INTO {table} ({id_col}, person_id, {concept_col}, {date_col}, procedure_type_concept_id, {src_val_col}) VALUES (?, 1, 0, '2026-06-01', 32020, ?)", (id_val, src_val))
            elif table == "device_exposure":
                con.execute(f"INSERT INTO {table} ({id_col}, person_id, {concept_col}, {date_col}, device_type_concept_id, {src_val_col}) VALUES (?, 1, 0, '2026-06-01', 32020, ?)", (id_val, src_val))

        # Insert fake ETL run
        con.execute(
            """
            INSERT INTO etl_run (
                run_id, status, started_at, input_manifest, configuration_manifest, step_manifest
            ) VALUES ('RUN-hospital-ingestion', 'SUCCESS', CURRENT_TIMESTAMP, 'fake-manifest-123', 'fake-config-123', 'fake-step-123')
            """
        )
        
        source_identity.register_source_system(con, "hospital-csv-v1", "E2E_HOSP", "CMF_E2E_HOSP", actor="admin", reason="E2E test")
        
    # 4. Run CSV Mapping
    result = hospital_csv_mapping.run_hospital_csv_mapping(FIXTURE_CSV, db_path=database, chroma_path=tmp_path, client=_E2EFakeOllama())
    assert result["records"] == 12
    assert result["proposals"] == 6
    assert result["abstentions"] == 6

    # 5. Event Binding & Ingestion Handoff
    with duckdb.connect(str(database)) as con:
        decisions = con.execute("""
            SELECT mapping_decision_id, source_record_key, target_table
            FROM mapping_decision
            WHERE status = 'PRE_INGESTION' AND llm_decision = 'SELECT'
        """).fetchall()
        
        records = hospital_csv.load_hospital_csv(FIXTURE_CSV)
        receipts = []
        for decision_id, key, target_table in decisions:
            rec = next(r for r in records if r.source_record_key == key)
            claim = source_identity.claim_hospital_csv_identity(rec)
            identity = source_identity.resolve_source_identity(con, claim)
            
            target_id = None
            if target_table == "condition_occurrence": target_id = 101
            elif target_table == "drug_exposure": target_id = 201
            elif target_table == "measurement": target_id = 301
            elif target_table == "observation": target_id = 401
            elif target_table == "procedure_occurrence": target_id = 501
            elif target_table == "device_exposure": target_id = 601
            
            event_binding.bind_pre_ingestion_decision(con, identity, decision_id, target_id, actor="binder", reason="E2E bind")
            
            import hashlib
            manifest_digest = hashlib.sha256(b"fake-manifest-123").hexdigest()
            receipt = IngestionReceipt(
                schema_version="cmf-ingestion-receipt-v1",
                ingestion_run_id="RUN-hospital-ingestion",
                input_manifest_sha256=manifest_digest,
                source_adapter=claim.source_adapter,
                source_system=claim.source_system,
                source_code=claim.source_code,
                source_record_key=key,
                target_table=target_table,
                target_id=target_id,
            )
            receipts.append(receipt)
            
        report = ingestion_handoff.process_ingestion_handoff(con, receipts, actor="ingestor", reason="E2E handoff")
        assert len(report.outcomes) == 6

        # 6. Adjudicate
        decisions_in_review = con.execute("SELECT mapping_decision_id FROM mapping_decision WHERE status = 'PENDING'").fetchall()
        for (decision_id,) in decisions_in_review:
            governance.submit_blinded_review(con, decision_id, "APPROVE", "Reviewer One", "Looks good")
            governance.submit_blinded_review(con, decision_id, "APPROVE", "Reviewer Two", "Looks fine")
            governance.adjudicate_mapping_decision(con, decision_id, "APPROVE", "Clinical Adjudicator", "Approved for STCM")

    # 7. Apply STCM Mappings
    apply_stcm.apply_stcm_mappings(db_path=str(database))
    
    # 8. Verify Concept IDs
    with duckdb.connect(str(database), read_only=True) as con:
        c1 = con.execute("SELECT condition_concept_id FROM condition_occurrence WHERE condition_occurrence_id = 101").fetchone()[0]
        assert c1 == 9001
        
        d1 = con.execute("SELECT drug_concept_id FROM drug_exposure WHERE drug_exposure_id = 201").fetchone()[0]
        assert d1 == 9002
        
        m1 = con.execute("SELECT measurement_concept_id FROM measurement WHERE measurement_id = 301").fetchone()[0]
        assert m1 == 9003
        
        o1 = con.execute("SELECT observation_concept_id FROM observation WHERE observation_id = 401").fetchone()[0]
        assert o1 == 9004
        
        p1 = con.execute("SELECT procedure_concept_id FROM procedure_occurrence WHERE procedure_occurrence_id = 501").fetchone()[0]
        assert p1 == 9005
        
        v1 = con.execute("SELECT device_concept_id FROM device_exposure WHERE device_exposure_id = 601").fetchone()[0]
        assert v1 == 9006
        
        stcm_count = con.execute("SELECT COUNT(*) FROM source_to_concept_map").fetchone()[0]
        assert stcm_count == 6

